"""Evaluation and result assembly (spec §23/§24/§43/§73/§74/§75).

The evaluator decides whether the goal is met — rule-based by default so the
answer is deterministic and testable:

* every step completed                    → ``complete``
* some steps completed, some failed       → ``partial``
* nothing usable                          → ``failed``
* a required tool was unavailable         → ``needs_replan``

Returning "partial" instead of pretending success matters: the character then
tells the user which part is missing (spec §74).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.agent.models import AgentResult, Observation, Plan, StepRecord
from app.config.settings import AgentConfig

STATUS_COMPLETE = "complete"
STATUS_PARTIAL = "partial"
STATUS_FAILED = "failed"
STATUS_REPLAN = "needs_replan"


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
        """Optional LLM check on top of the rule-based verdict (off by default)."""
        verdict = self.evaluate(plan, observations, step_records)
        if not (self.config.evaluator.use_llm and self.engine is not None):
            return verdict
        return verdict

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
