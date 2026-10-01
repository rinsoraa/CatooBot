"""Evaluation and result assembly (spec §23/§24/§43/§73/§74/§75).

The evaluator decides whether the goal is met — rule-based by default so the
answer is deterministic and testable:

* every step completed                    → ``complete``
* some steps completed, some failed       → ``partial``
* nothing usable                          → ``failed``
* a required tool was unavailable         → ``needs_replan``

Returning "partial" instead of pretending success matters: the character then
tells the user which part is missing (spec §74).

With ``agent.evaluator.use_llm`` the model *verifies completion claims*: rules
see statuses, not meaning — every step can return 200 while the answer still
misses the point. The policy is deliberately one-way (a false "done" is the
costly mistake):

* only a rule-based ``complete`` is re-checked (a failed step is a hard fact,
  and spending a model call there cannot flip it into success);
* the LLM may escalate ``complete`` → ``needs_replan`` when it is confident
  the goal is unmet, and can never do the reverse.

Any trouble (no engine, timeout, unparsable JSON) degrades to the rule verdict
with a WARNING — never silently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from app.agent.models import AgentResult, Observation, Plan, StepRecord
from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.config.settings import AgentConfig

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_REPLAN = "needs_replan"

#: below this the model's "not really done" is treated as a hunch, not a fact
_MIN_CONFIDENCE = 0.6

_FENCE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)

EVALUATION_PROMPT = """你在验收一个任务是否真的完成。只输出 JSON，不要解释：
{{"goal_met": true/false, "confidence": 0.0-1.0, "reason": "一句话理由"}}

判断看"实际拿到了什么"，不是"步骤有没有报错"：
结果答非所问、缺少目标要求的关键内容、或只是空壳，都算 goal_met=false。
拿不准时降低 confidence，不要勉强判 true。

任务目标：{goal}
完成标准：{criteria}
执行过程：
{steps}

只输出 JSON。"""


def _as_float(value: Any) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


def _parse_json(content: str) -> dict[str, Any] | None:
    """Tolerant JSON object from the model reply; ``None`` = no usable opinion."""
    text = (content or "").strip()
    candidates = [match.group("body").strip() for match in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            payload = json.loads(candidate)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict):
            return payload
    return None


class Evaluator:
    def __init__(
        self,
        config: AgentConfig,
        engine: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self._log = logger or logging.getLogger("CatooBot.Agent.Evaluator")

    # ------------------------------------------------------------ evaluation

    def evaluate(
        self,
        plan: Plan,
        observations: list[Observation],
        step_records: list[StepRecord],
    ) -> str:
        """Rule-based verdict; also reports *why* (spec §81 DEBUG trace)."""
        if not step_records:
            return STATUS_FAILED

        succeeded = {record.step_id for record in step_records if record.status == "completed"}
        failed = [record for record in step_records if record.status in ("failed", "blocked")]

        if not failed:
            return STATUS_COMPLETE if len(succeeded) == len(plan.steps) else STATUS_PARTIAL

        # A failed step is worth another approach (another provider, another
        # ordering). The runtime enforces max_replans, so asking for a replan
        # here can never loop forever (spec §26/§127).
        return STATUS_REPLAN

    async def evaluate_async(
        self,
        plan: Plan,
        observations: list[Observation],
        step_records: list[StepRecord],
    ) -> str:
        """Rule verdict, optionally verified by the model (see module docstring)."""
        verdict = self.evaluate(plan, observations, step_records)
        if verdict != STATUS_COMPLETE:
            # A failed/partial plan is a hard fact — a model opinion may not
            # turn it into success, so the call would only cost tokens.
            return verdict
        if not (self.config.evaluator.use_llm and self.engine is not None):
            return verdict

        opinion = await self._ask(plan, observations, step_records)
        if opinion is None:
            self._log.warning(
                "[Agent.Evaluator] no usable evaluation from the model — keeping rule verdict"
            )
            return verdict
        goal_met = bool(opinion.get("goal_met", True))
        confidence = _as_float(opinion.get("confidence"))
        reason = str(opinion.get("reason", "") or "").strip()
        if not goal_met and confidence >= _MIN_CONFIDENCE:
            self._log.warning(
                "[Agent.Evaluator] completion disputed (confidence=%.2f): %s",
                confidence,
                reason or "(no reason given)",
            )
            return STATUS_REPLAN
        self._log.info("[Agent.Evaluator] completion verified by model: %s", reason)
        return verdict

    async def _ask(
        self,
        plan: Plan,
        observations: list[Observation],
        step_records: list[StepRecord],
    ) -> dict[str, Any] | None:
        """One structured call; ``None`` means "no opinion, keep the rules"."""
        summaries = {o.step_id: o.summary for o in observations if o.summary}
        lines: list[str] = []
        for record in step_records:
            expected = plan.step_by_id(record.step_id)
            expectation = (expected.expected or expected.description) if expected else ""
            lines.append(
                "- {step}｜{description}｜期望：{expected}｜状态：{status}｜结果：{result}".format(
                    step=record.step_id,
                    description=record.description or (expected.description if expected else ""),
                    expected=expectation or "（未声明）",
                    status=record.status,
                    result=summaries.get(record.step_id) or "（无结果）",
                )
            )
        prompt = EVALUATION_PROMPT.format(
            goal=plan.summary or "（未给目标描述）",
            criteria="；".join(plan.criteria) if plan.criteria else "（未声明）",
            steps="\n".join(lines) or "（没有步骤）",
        )
        request = AIRequest(
            messages=[ChatMessage.user(prompt)],
            temperature=0.1,
            model=self.config.evaluator.model or None,
        )
        try:
            response = await asyncio.wait_for(
                self.engine.chat(request), timeout=self.config.evaluator.timeout
            )
        except AIError as exc:
            self._log.warning("[Agent.Evaluator] model unavailable (%s) — keeping rules", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - timeout / cancellation / provider bug
            self._log.warning("[Agent.Evaluator] evaluation failed (%s) — keeping rules", exc)
            return None
        return _parse_json(response.content)

    # ---------------------------------------------------------------- result

    @staticmethod
    def build_result(
        *,
        task_id: str,
        goal_id: str,
        verdict: str,
        plan: Plan,
        observations: list[Observation],
        step_records: list[StepRecord],
        cancelled: bool = False,
        expired: bool = False,
        error_type: str = "",
    ) -> AgentResult:
        """Assemble the facts a character reply can be grounded in (spec §43)."""
        facts: list[str] = []
        sources: list[str] = []
        for observation in observations:
            if observation.success and observation.summary:
                facts.append(observation.summary.strip())
            if observation.source and observation.source not in sources:
                sources.append(observation.source)

        unresolved = [
            record.observation_summary or f"步骤 {record.step_id} 未完成"
            for record in step_records
            if record.status in ("failed", "blocked")
        ]

        if cancelled:
            status = "cancelled"
        elif expired:
            status = "expired"
        elif verdict == STATUS_COMPLETE:
            status = "completed"
        elif verdict == STATUS_FAILED:
            status = "failed"
        else:
            status = "partial"

        success_ratio = (
            sum(1 for record in step_records if record.status == "completed") / len(step_records)
            if step_records
            else 0.0
        )
        confidence = round(min(0.95, 0.35 + 0.5 * success_ratio), 2)

        summary_bits = [plan.summary or ""]
        if status == "completed":
            summary_bits.append("任务已完成。")
        elif status == "partial":
            summary_bits.append("任务只完成了一部分。")
        elif status == "cancelled":
            summary_bits.append("用户要求停止，任务已取消。")
        elif status == "expired":
            summary_bits.append("任务超时结束。")
        else:
            summary_bits.append("任务未能完成。")

        return AgentResult(
            task_id=task_id,
            goal_id=goal_id,
            status=status,
            summary=" ".join(bit for bit in summary_bits if bit).strip(),
            facts=facts[:10],
            sources=sources[:5],
            unresolved=unresolved[:5],
            confidence=confidence,
            error_type=error_type,
            completed_at=int(time.time()),
        )
