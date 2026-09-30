"""Planner: turn a goal into a validated, minimal step plan (spec §13/§63-§66).

The planner asks the model for **structured steps only** — never a chain of
thought (spec §12/§64). Whatever comes back is validated before a single tool
runs:

    schema → tool existence → dependencies → budget → policy

One repair attempt is allowed when validation fails; after that the task fails
rather than improvising (spec §66).
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from typing import Any

from app.agent.errors import PlanningError, PlanValidationError
from app.agent.models import Goal, Observation, Plan, StepSpec, observations_to_context
from app.ai.errors import AIError
from app.ai.models import AIRequest, ChatMessage
from app.config.settings import AgentConfig

FENCE = re.compile(r"```(?:json)?\s*(?P<body>.*?)```", re.DOTALL)

PLANNER_PROMPT = """你是一个任务规划器。把用户的目标拆成**必要且最少**的步骤。

可用工具：
{tools}

规则：
- 能一步完成就不要拆成多步；不要为了显得复杂而增加步骤。
- 只规划用户明确要求的事，不要擅自扩大目标（不要顺手加搜索/推荐/订票等）。
- 需要外部事实时使用工具；比较、总结、下结论这类步骤把 tool 设为 null。
- 有先后依赖的步骤用 depends_on 声明；互相独立的步骤不要互相依赖。
- 工具参数尽量给全。地点/时间等如果用户没说，就不要编造。
- 如果目标不明确或无法完成，把 "unclear" 设为 true 并给出 reason，不要硬编计划。

只输出 JSON（不要任何解释文字）：
{{
  "goal": "一句话概括目标",
  "summary": "计划概要（一句话）",
  "criteria": ["完成条件1", "完成条件2"],
  "steps": [
    {{"id": "step_1", "description": "做什么", "tool": "工具名或null",
      "arguments": {{}}, "depends_on": [], "expected": "这一步要得到什么"}}
  ]
}}

用户目标：{goal}
"""

REPLAN_PROMPT = """之前给用户目标的计划执行不下去了，请给出新的计划。

用户目标：{goal}
原计划：{plan}
已完成步骤与观察：
{observations}
失败/阻塞原因：{reason}

规则：
- 保持同样的目标，只调整做法；能复用已完成的结果就不要重复执行。
- 步骤要更少或同样少；数据源不可用时换一条可行路径。
- 如果确实无路可走，把 "unclear" 设为 true 并说明原因。

只输出 JSON（格式与之前相同）：
"""


class Planner:
    def __init__(
        self,
        config: AgentConfig,
        engine: Any,
        registry: Any,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.registry = registry
        self._log = logger or logging.getLogger("CatooBot.Agent.Planner")

    # ------------------------------------------------------------- planning

    async def plan(self, goal: Goal, *, allowed_tools: list[str] | None = None) -> Plan:
        tools_block = self._tools_block(goal.description, allowed_tools)
        prompt = PLANNER_PROMPT.format(tools=tools_block, goal=goal.description)
        payload = await self._ask(prompt, timeout=self.config.planner.timeout)
        plan = self._build_plan(payload, goal, version=1, reason="initial")
        problems = self.validate(plan, allowed_tools=allowed_tools)
        if problems:
            self._log.warning("[Agent] plan v1 rejected: %s", problems)
            plan = await self._repair(plan, problems, goal, allowed_tools, version=1)
        return plan

    async def replan(
        self,
        goal: Goal,
        previous: Plan,
        observations: list[Observation],
        reason: str,
        *,
        version: int,
        allowed_tools: list[str] | None = None,
    ) -> Plan:
        """Versioned replan — the old plan is kept for the WebUI (spec §28)."""
        prompt = REPLAN_PROMPT.format(
            goal=goal.description,
            plan=previous.to_json(),
            observations=observations_to_context(
                observations, limit=self.config.max_observations_in_context
            )
            or "（尚无完成的步骤）",
            reason=reason,
        )
        payload = await self._ask(prompt, timeout=self.config.planner.timeout)
        plan = self._build_plan(payload, goal, version=version, reason=reason)
        problems = self.validate(plan, allowed_tools=allowed_tools)
        if problems:
            self._log.warning("[Agent] replan v%d rejected: %s", version, problems)
            plan = await self._repair(plan, problems, goal, allowed_tools, version=version)
        return plan

    # ------------------------------------------------------------ validation

    def validate(self, plan: Plan, *, allowed_tools: list[str] | None = None) -> list[str]:
        """Schema → tools → dependencies → budget (spec §65)."""
        problems: list[str] = []
        if not plan.steps:
            problems.append("plan has no steps")
            return problems
        if len(plan.steps) > self.config.budget.max_steps:
            problems.append(
                f"plan has {len(plan.steps)} steps, budget is {self.config.budget.max_steps}"
            )

        seen: set[str] = set()
        for step in plan.steps:
            if not step.id:
                problems.append("a step is missing an id")
                continue
            if step.id in seen:
                problems.append(f"duplicate step id: {step.id}")
            seen.add(step.id)
            if not step.description:
                problems.append(f"{step.id} is missing a description")
            if step.tool:
                if self.registry.maybe_get(step.tool) is None:
                    problems.append(f"{step.id} uses unknown tool '{step.tool}'")
                elif not self.registry.is_enabled(step.tool):
                    problems.append(f"{step.id} uses disabled tool '{step.tool}'")
                elif allowed_tools is not None and step.tool not in allowed_tools:
                    problems.append(f"{step.id} uses tool '{step.tool}' outside the allowed set")
            if step.tool is None and step.depends_on:
                continue  # analysis steps may depend on anything

        for step in plan.steps:
            for dependency in step.depends_on:
                if dependency not in seen:
                    problems.append(f"{step.id} depends on unknown step '{dependency}'")
                elif dependency == step.id:
                    problems.append(f"{step.id} depends on itself")
        if self._has_cycle(plan):
            problems.append("step dependencies contain a cycle")

        tool_steps = sum(1 for step in plan.steps if step.tool)
        if tool_steps > self.config.budget.max_tool_calls:
            problems.append(
                f"plan needs {tool_steps} tool calls, budget is {self.config.budget.max_tool_calls}"
            )
        return problems

    @staticmethod
    def _has_cycle(plan: Plan) -> bool:
        graph = {step.id: set(step.depends_on) for step in plan.steps}
        temporary: set[str] = set()
        permanent: set[str] = set()

        def visit(node: str) -> bool:
            if node in permanent:
                return False
            if node in temporary:
                return True
            temporary.add(node)
            for neighbour in graph.get(node, ()):
                if visit(neighbour):
                    return True
            temporary.discard(node)
            permanent.add(node)
            return False

        return any(visit(node) for node in list(graph))

    # ---------------------------------------------------------------- repair

    async def _repair(
        self,
        plan: Plan,
        problems: list[str],
        goal: Goal,
        allowed_tools: list[str] | None,
        *,
        version: int,
    ) -> Plan:
        """One repair attempt; still invalid → the task fails (spec §66)."""
        self._log.info("[Agent] attempting plan repair (%d problem(s))", len(problems))
        prompt = (
            PLANNER_PROMPT.format(
                tools=self._tools_block(goal.description, allowed_tools), goal=goal.description
            )
            + "\n上一次的计划有以下问题，请修正后重新输出 JSON：\n- "
            + "\n- ".join(problems[:6])
        )
        try:
            payload = await self._ask(prompt, timeout=self.config.planner.timeout)
            repaired = self._build_plan(payload, goal, version=version, reason="repaired")
        except PlanningError:
            raise
        remaining = self.validate(repaired, allowed_tools=allowed_tools)
        if remaining:
            raise PlanValidationError(
                "计划无法通过校验：" + "；".join(remaining[:4])
            )
        return repaired

    # -------------------------------------------------------------- helpers

    def _tools_block(self, query: str, allowed_tools: list[str] | None) -> str:
        candidates = self.registry.candidates(
            query, limit=max(self.config.budget.max_steps, 5), allowed=allowed_tools
        )
        if not candidates:
            candidates = [
                (tool, 1.0)
                for tool in self.registry.all(include_disabled=False)
                if allowed_tools is None or tool.metadata.name in allowed_tools
            ]
        if not candidates:
            return "（当前没有可用工具）"
        lines = []
        for tool, _score in candidates:
            payload = tool.metadata.to_prompt_dict()
            lines.append(
                "- {name}：{description}｜参数：{schema}".format(
                    name=payload["name"],
                    description=payload["description"],
                    schema=json.dumps(payload["parameters"], ensure_ascii=False),
                )
            )
        return "\n".join(lines)

    async def _ask(self, prompt: str, *, timeout: float) -> dict[str, Any]:
        request = AIRequest(
            messages=[ChatMessage.user(prompt)],
            temperature=0.2,
            model=self.config.planner.model or None,
        )
        try:
            import asyncio

            response = await asyncio.wait_for(self.engine.chat(request), timeout=timeout)
        except AIError as exc:
            raise PlanningError(f"planner model failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 - timeout / cancellation
            raise PlanningError(f"planner model unavailable: {exc}") from exc
        return self._parse_json(response.content)

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        text = (content or "").strip()
        candidates = [match.group("body").strip() for match in FENCE.finditer(text)]
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
        raise PlanningError("planner did not return usable JSON")

    def _build_plan(
        self, payload: dict[str, Any], goal: Goal, *, version: int, reason: str
    ) -> Plan:
        if payload.get("unclear"):
            raise PlanningError(str(payload.get("reason") or "目标不明确"))

        steps: list[StepSpec] = []
        raw_steps = payload.get("steps")
        if isinstance(raw_steps, list):
            for index, item in enumerate(raw_steps, start=1):
                if not isinstance(item, dict):
                    continue
                tool = item.get("tool")
                if isinstance(tool, str) and tool.strip().lower() in ("", "null", "none"):
                    tool = None
                arguments = item.get("arguments")
                if not isinstance(arguments, dict):
                    arguments = {}
                depends_on = [str(dep) for dep in (item.get("depends_on") or []) if dep]
                steps.append(
                    StepSpec(
                        id=str(item.get("id") or f"step_{index}"),
                        description=str(item.get("description") or ""),
                        tool=tool if isinstance(tool, str) else None,
                        arguments=arguments,
                        depends_on=depends_on,
                        expected=str(item.get("expected") or ""),
                    )
                )

        criteria = [str(item) for item in (payload.get("criteria") or []) if item]
        return Plan(
            plan_id=uuid.uuid4().hex[:12],
            task_id="",
            version=version,
            summary=str(payload.get("summary") or payload.get("goal") or goal.description),
            criteria=criteria,
            steps=steps,
            created_at=int(time.time()),
            reason=reason,
            status="active",
        )
