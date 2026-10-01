"""Execution engine: dependency-aware step execution (spec §19/§33/§34/§67).

    while not finished:
        pick ready steps (dependencies satisfied)
        run independent ones in parallel (bounded by max_parallel_tools)
        record observations
        stop on budget / timeout / cancellation

Tools are executed **only** through the v0.6 tool runtime, so tool policy,
permissions, rate limits and the tool budget all still apply (spec §30/§31/§61).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from app.agent.errors import BudgetExceededError, TaskCancelledError, TaskTimeoutError
from app.agent.models import (
    AgentBudget,
    Observation,
    Plan,
    StepRecord,
    StepSpec,
    observations_to_context,
)
from app.config.settings import AgentConfig
from app.tools.models import ToolCall, ToolContext
from app.tools.policy import TurnBudget

PLACEHOLDER = "{{"

# Analysis steps (tool=None) get their input from the observations so far.
ANALYSIS_PROMPT = """你在执行一个已完成步骤的分析任务。

用户目标：{goal}
当前步骤：{step}
已知信息：
{observations}

只输出该步骤要得到的结果（一句到三句话），不要复述过程，不要输出 JSON。
"""

ARGUMENT_PROMPT = """为下面的步骤生成工具参数。

工具：{tool}
参数 schema：{schema}
步骤说明：{step}
已知信息：
{observations}

只输出 JSON 对象（参数），不要任何解释：
"""


class ExecutionEngine:
    def __init__(
        self,
        config: AgentConfig,
        engine: Any,
        tools: Any,
        logger: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.engine = engine
        self.tools = tools
        self._log = logger or logging.getLogger("CatooBot.Agent.Executor")

    # ------------------------------------------------------------------ api

    async def run_plan(
        self,
        plan: Plan,
        *,
        goal_description: str,
        context: ToolContext,
        budget: AgentBudget,
        cancelled: Any = None,
    ) -> tuple[list[Observation], list[StepRecord]]:
        """Execute every step that can run; returns observations + step records."""
        observations: list[Observation] = []
        records: list[StepRecord] = []
        remaining: dict[str, StepSpec] = {step.id: step for step in plan.steps}
        completed: set[str] = set()
        failed: set[str] = set()

        self._log.info(
            "[Agent] executing plan v%d with %d step(s): %s",
            plan.version,
            len(plan.steps),
            ", ".join(step.id for step in plan.steps),
        )

        while remaining:
            self._guard(budget, cancelled)
            ready = [
                step
                for step in remaining.values()
                if all(dep in completed for dep in step.depends_on)
                and not any(dep in failed for dep in step.depends_on)
            ]
            if not ready:
                # Nothing runnable: everything left is blocked by failures.
                for step in list(remaining.values()):
                    records.append(self._record(step, "blocked", error_type="dependency_failed"))
                    remaining.pop(step.id, None)
                break

            batch = ready[: max(1, budget.max_parallel_tools)]
            for step in batch:
                remaining.pop(step.id, None)

            outcomes = await asyncio.gather(
                *(
                    self._execute_step(step, goal_description, observations, context, budget)
                    for step in batch
                )
            )
            for step, observation, record in outcomes:
                observations.append(observation)
                records.append(record)
                if observation.success:
                    completed.add(step.id)
                else:
                    failed.add(step.id)

        return observations, records

    # -------------------------------------------------------------- internals

    def _guard(self, budget: AgentBudget, cancelled: Any) -> None:
        if cancelled is not None and getattr(cancelled, "is_set", lambda: False)():
            raise TaskCancelledError("task cancelled by user")
        if budget.expired():
            raise TaskTimeoutError(f"task exceeded {budget.max_execution_seconds:.0f}s")
        if budget.steps_left <= 0:
            raise BudgetExceededError(f"step budget exhausted ({budget.max_steps})")

    async def _execute_step(
        self,
        step: StepSpec,
        goal_description: str,
        observations: list[Observation],
        context: ToolContext,
        budget: AgentBudget,
    ) -> tuple[StepSpec, Observation, StepRecord]:
        started = time.perf_counter()
        budget.steps_used += 1

        try:
            if step.tool:
                observation = await self._run_tool_step(step, observations, context, budget)
            else:
                observation = await self._run_analysis_step(step, goal_description, observations)
        except Exception as exc:  # noqa: BLE001 - a step failure is data, not a crash
            self._log.warning("[Agent] step %s failed: %s", step.id, exc)
            observation = Observation(
                step_id=step.id,
                success=False,
                summary=f"步骤未能完成：{exc}",
                source="agent",
                created_at=int(time.time()),
            )

        record = self._record(
            step,
            "completed" if observation.success else "failed",
            duration_ms=(time.perf_counter() - started) * 1000,
            observation_summary=observation.summary[:200],
            error_type="" if observation.success else "step_failed",
        )
        step.status = record.status
        return step, observation, record

    async def _run_tool_step(
        self,
        step: StepSpec,
        observations: list[Observation],
        context: ToolContext,
        budget: AgentBudget,
    ) -> Observation:
        assert step.tool
        if budget.tool_calls_left <= 0:
            raise BudgetExceededError(f"tool call budget exhausted ({budget.max_tool_calls})")

        arguments = dict(step.arguments or {})
        if step.depends_on or not arguments or self._needs_resolution(arguments):
            arguments = await self._resolve_arguments(step, observations, arguments)
            self._log.debug("[Agent] %s resolved arguments: %s", step.id, arguments)

        call = ToolCall(
            name=step.tool,
            arguments=arguments,
            reason=f"agent:{step.id}",
            argument_sources={"source": "plan" if not step.depends_on else "observation"},
        )
        budget.tool_calls_used += 1
        # Tool policy / permissions / rate limits / tool budget still apply.
        result = await self.tools.executor.execute(
            call,
            context,
            TurnBudget(
                max_calls=max(1, budget.tool_calls_left + 1),
                max_execution_time=self.config.budget.max_execution_seconds,
            ),
        )
        summary = (result.summary or result.error or "").strip()
        if not result.success:
            summary = f"工具 '{step.tool}' 未能返回结果：{result.error or result.error_type}"
        return Observation(
            step_id=step.id,
            success=result.success,
            summary=summary[:1500],
            data={
                "tool": step.tool,
                "error_type": result.error_type,
                "cache_hit": result.cache_hit,
                "arguments": arguments,
            },
            source=str(result.metadata.get("source") or step.tool),
            created_at=int(time.time()),
        )

    async def _run_analysis_step(
        self,
        step: StepSpec,
        goal_description: str,
        observations: list[Observation],
    ) -> Observation:
        """A no-tool step: the model reasons over the observations (spec §22)."""
        context_block = observations_to_context(
            observations, limit=self.config.max_observations_in_context
        )
        if not context_block:
            raise BudgetExceededError("no observations available for analysis step")
        prompt = ANALYSIS_PROMPT.format(
            goal=goal_description, step=step.description or step.id, observations=context_block
        )
        from app.ai.models import AIRequest, ChatMessage

        response = await self.engine.chat(
            AIRequest(messages=[ChatMessage.user(prompt)], temperature=0.3)
        )
        summary = (response.content or "").strip()
        if not summary:
            raise BudgetExceededError("analysis step produced no text")
        return Observation(
            step_id=step.id,
            success=True,
            summary=summary[:1500],
            data={"analysis": True},
            source="agent-analysis",
            created_at=int(time.time()),
        )

    # ------------------------------------------------- argument resolution

    @staticmethod
    def _needs_resolution(arguments: dict[str, Any]) -> bool:
        return any(isinstance(value, str) and PLACEHOLDER in value for value in arguments.values())

    async def _resolve_arguments(
        self,
        step: StepSpec,
        observations: list[Observation],
        arguments: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill arguments from earlier observations, bounded and validated."""
        tool = self.tools.registry.maybe_get(step.tool or "")
        schema = tool.metadata.input_schema if tool else {}
        context_block = observations_to_context(
            observations, limit=self.config.max_observations_in_context
        )
        prompt = (
            ARGUMENT_PROMPT.format(
                tool=step.tool,
                schema=json.dumps(schema, ensure_ascii=False),
                step=step.description or step.id,
                observations=context_block or "（无）",
            )
            + f"\n已有参数（可覆盖）：{json.dumps(arguments, ensure_ascii=False)}\n"
        )

        from app.ai.models import AIRequest, ChatMessage

        try:
            response = await self.engine.chat(
                AIRequest(messages=[ChatMessage.user(prompt)], temperature=0.1)
            )
        except Exception:  # noqa: BLE001 - fall back to the planned arguments
            self._log.debug("[Agent] argument resolution failed; using plan arguments")
            return {k: v for k, v in arguments.items() if not self._is_placeholder(v)}

        text = (response.content or "").strip()
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            return {k: v for k, v in arguments.items() if not self._is_placeholder(v)}
        try:
            resolved = json.loads(text[start : end + 1])
        except (TypeError, ValueError):
            return {k: v for k, v in arguments.items() if not self._is_placeholder(v)}
        if not isinstance(resolved, dict):
            return {k: v for k, v in arguments.items() if not self._is_placeholder(v)}
        # Keep planned values that were not placeholders.
        merged = {**resolved, **{k: v for k, v in arguments.items() if not self._is_placeholder(v)}}
        return merged

    @staticmethod
    def _is_placeholder(value: Any) -> bool:
        return isinstance(value, str) and PLACEHOLDER in value

    @staticmethod
    def _record(
        step: StepSpec,
        status: str,
        *,
        duration_ms: float = 0.0,
        observation_summary: str = "",
        error_type: str = "",
    ) -> StepRecord:
        return StepRecord(
            step_id=step.id,
            description=step.description,
            tool_name=step.tool or "",
            depends_on=list(step.depends_on),
            status=status,
            duration_ms=round(duration_ms, 2),
            observation_summary=observation_summary,
            error_type=error_type,
        )
