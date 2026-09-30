"""AgentRuntime: orchestration, persistence and task lifecycle (v0.7).

    classify → goal → plan → execute → evaluate → (replan) → result

Design rules enforced here:

* every loop is bounded by :class:`AgentBudget` (steps / tool calls / replans / time);
* only one active task per session by default (spec §95);
* task state changes go through the state machine and are persisted (spec §71),
  so a crashed run is found as ``paused`` on the next start (spec §51/§131);
* cancellation / pause / resume come from natural language (spec §36/§37);
* the agent never writes the user-visible reply — it returns an
  :class:`AgentResult` that the Character Runtime renders (spec §42/§108).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.agent.errors import (
    AgentError,
    BudgetExceededError,
    PlanningError,
    PlanValidationError,
    TaskCancelledError,
    TaskTimeoutError,
)
from app.agent.evaluator import STATUS_REPLAN, Evaluator
from app.agent.executor import ExecutionEngine
from app.agent.goal import CONTROL_CANCEL, CONTROL_PAUSE, CONTROL_RESUME, GoalParser, TaskClassifier
from app.agent.models import (
    AgentBudget,
    AgentResult,
    AgentStateMachine,
    Goal,
    Observation,
    Plan,
    StepRecord,
)
from app.agent.planner import Planner
from app.config.settings import AgentConfig
from app.tools.models import ToolContext

if TYPE_CHECKING:  # pragma: no cover
    from app.database.database import Database

DEFAULT_HOUSEKEEPING_SECONDS = 300.0


def _narrate_plan(plan: Any) -> None:
    """Show the structured plan (never hidden reasoning — it is a JSON plan)."""
    try:
        from app.utils.narrator import narrate

        steps = getattr(plan, "steps", []) or []
        narrate().task(
            f"拆成 {len(steps)} 步：{getattr(plan, 'summary', '') or '——'}",
            detail=f"v{getattr(plan, 'version', 1)}",
        )
        for index, step in enumerate(steps, start=1):
            tool = getattr(step, "tool", "") or ""
            depends = getattr(step, "depends_on", []) or []
            detail = "/".join(
                part
                for part in (
                    f"tool={tool}" if tool else "",
                    f"依赖 {','.join(str(d) for d in depends)}" if depends else "",
                )
                if part
            )
            narrate().note("task", f"{index}. {getattr(step, 'description', '')}", detail=detail)
    except Exception:  # noqa: BLE001
        return


def _narrate_result(result: Any, budget: Any) -> None:
    try:
        from app.utils.narrator import narrate

        status = getattr(result, "status", "")
        label = {
            "completed": "办完了",
            "partial": "只办到一半",
            "failed": "没办成",
            "cancelled": "被打断了",
            "expired": "超时停下了",
        }.get(status, status or "结束")
        facts = len(getattr(result, "facts", []) or [])
        unresolved = len(getattr(result, "unresolved", []) or [])
        detail = (
            f"{budget.elapsed():.1f}s · {budget.steps_used} 步 · "
            f"{budget.tool_calls_used} 次工具 · 结论 {facts} 条"
        )
        if unresolved:
            detail += f" · 未拿到 {unresolved} 项"
        narrate().task(f"任务{label}", detail=detail)
    except Exception:  # noqa: BLE001
        return


class AgentRuntime:
    def __init__(
        self,
        config: AgentConfig,
        engine: Any,
        tools: Any,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.engine = engine
        self.tools = tools
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Agent")
        self._clock = clock

        self.classifier = TaskClassifier(config, self._log)
        self.goal_parser = GoalParser(self._log)
        self.planner = Planner(config, engine, tools.registry, self._log)
        self.executor = ExecutionEngine(config, engine, tools, self._log)
        self.evaluator = Evaluator(config, engine, self._log)

        self._tasks: dict[str, dict[str, Any]] = {}       # task_id -> task row
        self._active_by_session: dict[str, str] = {}      # session -> task_id
        self._cancels: dict[str, asyncio.Event] = {}
        self._last_housekeeping: float = 0.0

    # ------------------------------------------------------------------ meta

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    def can_handle(self, kind: str) -> bool:
        return self.classifier.may_handle(kind)

    # --------------------------------------------------------------- entries

    async def handle_control(self, text: str, session_id: str) -> str:
        """Apply a natural-language control intent. Returns the intent or ""."""
        control = self.classifier.detect_control(text)
        if not control:
            return ""
        task_id = self._active_by_session.get(session_id)
        if control == CONTROL_CANCEL:
            if task_id:
                await self.cancel(task_id, reason="user_request")
            return control
        if control == CONTROL_PAUSE:
            if task_id:
                await self.pause(task_id, reason="user_request")
            return control
        if control == CONTROL_RESUME:
            paused = await self.find_paused(session_id)
            if paused:
                await self.resume(paused["task_id"])
                return control + ":" + str(paused["task_id"])
        return control

    async def active_task(self, session_id: str) -> dict[str, Any] | None:
        task_id = self._active_by_session.get(session_id)
        if task_id and task_id in self._tasks:
            return self._tasks[task_id]
        row = await self.find_active(session_id)
        return row

    async def run(
        self,
        text: str,
        *,
        session_id: str,
        user_id: str = "",
        group_id: str | None = None,
        context: ToolContext | None = None,
        classification: Any = None,
        constraints: dict[str, Any] | None = None,
    ) -> AgentResult:
        """Plan and execute one multi-step task; never raises."""
        from app.agent.goal import Classification

        classification = classification or Classification(kind="multi_step", reason="direct")
        goal = self.goal_parser.parse(
            text,
            session_id=session_id,
            user_id=user_id,
            group_id=group_id,
            classification=classification,
            constraints=constraints,
        )
        task_id = uuid.uuid4().hex[:12]
        budget = AgentBudget(
            max_steps=self.config.budget.max_steps,
            max_tool_calls=self.config.budget.max_tool_calls,
            max_replans=self.config.budget.max_replans,
            max_execution_seconds=self.config.budget.max_execution_seconds,
            max_parallel_tools=self.config.budget.max_parallel_tools,
        )
        cancel_event = asyncio.Event()
        self._cancels[task_id] = cancel_event
        started = self._clock()

        await self._store_task(
            task_id,
            goal,
            status="created",
            classification=classification.kind,
            budget=budget,
        )
        self._active_by_session[session_id] = task_id
        await self._trace(task_id, "INFO", "task_created", f"class={classification.kind}")
        self._log.info("[Agent] task %s started (%s): %.60s", task_id, classification.kind, text)

        tool_context = context or ToolContext(
            user_id=user_id,
            group_id=group_id,
            session_id=session_id,
        )

        plan: Plan | None = None
        observations: list[Observation] = []
        records: list[StepRecord] = []
        verdict = "failed"
        cancelled = False
        expired = False
        error_type = ""

        try:
            verdict, plan, observations, records, stop_reason = await self._planning_loop(
                task_id, goal, tool_context, budget, cancel_event
            )
            if stop_reason == "cancelled":
                cancelled = True
                await self._trace(task_id, "INFO", "task_cancelled", "user request")
            elif stop_reason in ("timeout", "budget", "replan_limit"):
                error_type = (
                    "task_timeout" if stop_reason == "timeout" else
                    "replan_limit" if stop_reason == "replan_limit" else "budget_exceeded"
                )
                await self._trace(
                    task_id,
                    "WARNING",
                    error_type,
                    f"stopped early ({stop_reason}) after {budget.steps_used} step(s)",
                )
        except (PlanningError, PlanValidationError) as exc:
            error_type = exc.error_type
            verdict = "failed"
            await self._trace(task_id, "WARNING", exc.error_type, str(exc)[:200])
        except AgentError as exc:
            error_type = exc.error_type
            await self._trace(task_id, "ERROR", exc.error_type, str(exc)[:200])
        except Exception:  # noqa: BLE001 - the agent must never take the bot down
            error_type = "agent_internal_error"
            self._log.exception("[Agent] task %s crashed", task_id)
            await self._trace(task_id, "ERROR", "internal_error", "see logs")

        result = Evaluator.build_result(
            task_id=task_id,
            goal_id=goal.goal_id,
            verdict=verdict,
            plan=plan or Plan(plan_id="-", summary=""),
            observations=observations,
            step_records=records,
            cancelled=cancelled,
            expired=expired,
            error_type=error_type,
        )
        final_status = {
            "completed": "completed",
            "partial": "failed",
            "failed": "failed",
            "cancelled": "cancelled",
            "expired": "expired",
        }.get(result.status, "failed")

        await self._finish_task(
            task_id,
            status=final_status,
            result=result,
            budget=budget,
            duration_ms=(self._clock() - started) * 1000,
            error_type=error_type,
        )
        self._active_by_session.pop(session_id, None)
        self._cancels.pop(task_id, None)
        self._log.info(
            "[Agent] task %s finished: %s (%.1fs, %d step(s), %d tool call(s), %d replan(s))",
            task_id,
            result.status,
            budget.elapsed(),
            budget.steps_used,
            budget.tool_calls_used,
            budget.replans_used,
        )
        _narrate_result(result, budget)
        return result

    # ------------------------------------------------------- planning loop

    async def _planning_loop(
        self,
        task_id: str,
        goal: Goal,
        context: ToolContext,
        budget: AgentBudget,
        cancel_event: asyncio.Event,
    ) -> tuple[str, Plan, list[Observation], list[StepRecord], str]:
        """Plan and execute; always returns the work done so far plus a stop reason."""
        await self._set_status(task_id, "planning")
        allowed = self.tools.registry.names() if self.tools else []
        plan = await self.planner.plan(goal, allowed_tools=allowed)
        plan.task_id = task_id
        await self._store_plan(task_id, plan)
        await self._set_status(task_id, "ready")
        _narrate_plan(plan)
        await self._trace(
            task_id, "DEBUG", "plan_created", f"v{plan.version}: {plan.summary}"
        )

        observations: list[Observation] = []
        records: list[StepRecord] = []

        while True:
            await self._set_status(task_id, "running")
            try:
                new_observations, new_records = await self.executor.run_plan(
                    plan,
                    goal_description=goal.description,
                    context=context,
                    budget=budget,
                    cancelled=cancel_event,
                )
            except TaskCancelledError:
                return "cancelled", plan, observations, records, "cancelled"
            except TaskTimeoutError:
                return self._degraded_verdict(observations), plan, observations, records, "timeout"
            except BudgetExceededError:
                return self._degraded_verdict(observations), plan, observations, records, "budget"
            observations.extend(new_observations)
            records.extend(new_records)
            await self._store_observations(task_id, plan, new_observations, new_records)

            verdict = await self.evaluator.evaluate_async(plan, observations, records)
            await self._trace(
                task_id,
                "DEBUG",
                "evaluated",
                f"v{plan.version} -> {verdict}",
            )
            if verdict != STATUS_REPLAN:
                return verdict, plan, observations, records, ""

            if budget.replans_used >= budget.max_replans:
                await self._trace(
                    task_id, "WARNING", "replan_limit", f"after {budget.replans_used} replan(s)"
                )
                return (
                    self._degraded_verdict(observations),
                    plan,
                    observations,
                    records,
                    "replan_limit",
                )

            reason = self._replan_reason(plan, records)
            budget.replans_used += 1
            await self._set_status(task_id, "replanning")
            await self._trace(task_id, "DEBUG", "replan", reason)
            plan = await self.planner.replan(
                goal,
                plan,
                observations,
                reason,
                version=plan.version + 1,
                allowed_tools=self.tools.registry.names() if self.tools else [],
            )
            plan.task_id = task_id
            await self._store_plan(task_id, plan)
            await self._set_status(task_id, "ready")
            records = []
            self._log.info("[Agent] task %s replanned to v%d", task_id, plan.version)

    @staticmethod
    def _degraded_verdict(observations: list[Observation]) -> str:
        """Whatever succeeded is still worth telling the user about (spec §74)."""
        return "partial" if any(observation.success for observation in observations) else "failed"

    @staticmethod
    def _replan_reason(plan: Plan, records: list[StepRecord]) -> str:
        failed = [record for record in records if record.status in ("failed", "blocked")]
        if not failed:
            return "评估认为信息不足"
        first = failed[0]
        return (
            f"{first.step_id}（{first.description or '步骤'}）未能完成："
            f"{first.observation_summary or first.error_type}"
        )

    # ------------------------------------------------------------- controls

    async def cancel(self, task_id: str, *, reason: str = "user_request") -> bool:
        event = self._cancels.get(task_id)
        if event is not None:
            event.set()
        task = self._tasks.get(task_id)
        if task and AgentStateMachine.is_terminal(task["status"]):
            return False
        try:
            await self._set_status(task_id, "cancelled", error_type=reason)
        except Exception:  # noqa: BLE001 - cancellation must always succeed
            self._log.debug("[Agent] cancel state update skipped for %s", task_id)
        await self._trace(task_id, "INFO", "cancelled", reason)
        return True

    async def pause(self, task_id: str, *, reason: str = "user_request") -> bool:
        task = self._tasks.get(task_id) or await self.get(task_id)
        if task is None or AgentStateMachine.is_terminal(task["status"]):
            return False
        await self._set_status(
            task_id,
            "paused",
            allowed_from=("running", "waiting", "ready", "planning", "replanning"),
        )
        await self._trace(task_id, "INFO", "paused", reason)
        return True

    async def resume(self, task_id: str) -> bool:
        task = self._tasks.get(task_id) or await self.get(task_id)
        if task is None or task["status"] != "paused":
            return False
        await self._set_status(task_id, "running")
        await self._trace(task_id, "INFO", "resumed", "manual")
        return True

    # ---------------------------------------------------------- persistence

    async def _set_status(
        self,
        task_id: str,
        status: str,
        *,
        error_type: str = "",
        allowed_from: tuple[str, ...] | None = None,
    ) -> None:
        task = self._tasks.setdefault(
            task_id, {"task_id": task_id, "status": "created", "session_id": ""}
        )
        current = task.get("status", "created")
        if allowed_from is None:
            AgentStateMachine.ensure(current, status)
        elif current not in allowed_from and current != status:
            raise AgentError(f"cannot move {current} -> {status}")
        task["status"] = status
        task["updated_at"] = int(self._clock())
        if self._db is None:
            return
        try:
            await self._db.execute(
                "UPDATE agent_tasks SET status = ?, updated_at = ?, error_type = ?"
                " WHERE task_id = ?",
                (status, int(self._clock()), error_type or task.get("error_type", ""), task_id),
            )
        except Exception:  # noqa: BLE001 - persistence must not stop the task
            self._log.debug("Failed to persist agent task status", exc_info=True)

    async def _store_task(
        self,
        task_id: str,
        goal: Goal,
        *,
        status: str,
        classification: str,
        budget: AgentBudget,
    ) -> None:
        now = int(self._clock())
        self._tasks[task_id] = {
            "task_id": task_id,
            "goal_id": goal.goal_id,
            "session_id": goal.session_id,
            "user_id": goal.user_id,
            "group_id": goal.group_id,
            "classification": classification,
            "status": status,
            "created_at": now,
            "updated_at": now,
            "budget": budget.snapshot(),
        }
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO agent_goals
                       (goal_id, session_id, user_id, group_id, description, type,
                        priority, constraints, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    goal.goal_id,
                    goal.session_id,
                    goal.user_id,
                    goal.group_id,
                    goal.description,
                    goal.type,
                    goal.priority,
                    json.dumps(goal.constraints, ensure_ascii=False),
                    "active",
                    now,
                ),
            )
            await self._db.execute(
                """INSERT INTO agent_tasks
                       (task_id, goal_id, session_id, user_id, group_id, classification,
                        status, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    task_id,
                    goal.goal_id,
                    goal.session_id,
                    goal.user_id,
                    goal.group_id,
                    classification,
                    status,
                    now,
                    now,
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Agent] failed to persist task %s", task_id)

    async def _store_plan(self, task_id: str, plan: Plan) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO agent_plans
                       (plan_id, task_id, version, status, reason, summary,
                        criteria, steps, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    plan.plan_id,
                    task_id,
                    plan.version,
                    plan.status,
                    plan.reason[:200],
                    plan.summary[:500],
                    json.dumps(plan.criteria, ensure_ascii=False),
                    json.dumps([step.model_dump() for step in plan.steps], ensure_ascii=False),
                    int(self._clock()),
                ),
            )
            # keep only the newest plan marked active
            await self._db.execute(
                "UPDATE agent_plans SET status = 'superseded'"
                " WHERE task_id = ? AND plan_id != ? AND status = 'active'",
                (task_id, plan.plan_id),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to persist plan for %s", task_id, exc_info=True)

    async def _store_observations(
        self,
        task_id: str,
        plan: Plan,
        observations: list[Observation],
        records: list[StepRecord],
    ) -> None:
        if self._db is None:
            return
        by_id = {observation.step_id: observation for observation in observations}
        try:
            for record in records:
                observation = by_id.get(record.step_id)
                await self._db.execute(
                    """INSERT INTO agent_steps
                           (task_id, plan_id, step_id, description, tool_name, depends_on,
                            status, duration_ms, observation_summary, error_type, finished_at)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        task_id,
                        plan.plan_id,
                        record.step_id,
                        record.description[:300],
                        record.tool_name,
                        json.dumps(record.depends_on, ensure_ascii=False),
                        record.status,
                        record.duration_ms,
                        record.observation_summary[:500],
                        record.error_type,
                        int(self._clock()),
                    ),
                )
                if observation is not None:
                    await self._db.execute(
                        """INSERT INTO agent_observations
                               (task_id, step_id, success, summary, data, source, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            task_id,
                            observation.step_id,
                            1 if observation.success else 0,
                            observation.summary[:1000],
                            json.dumps(observation.data, ensure_ascii=False)[:2000],
                            observation.source,
                            observation.created_at or int(self._clock()),
                        ),
                    )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to persist steps for %s", task_id, exc_info=True)

    async def _finish_task(
        self,
        task_id: str,
        *,
        status: str,
        result: AgentResult,
        budget: AgentBudget,
        duration_ms: float,
        error_type: str,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is not None:
            task["status"] = status
            task["updated_at"] = int(self._clock())
            task["budget"] = budget.snapshot()
        if self._db is None:
            return
        try:
            await self._db.execute(
                """UPDATE agent_tasks SET
                       status = ?, updated_at = ?, finished_at = ?, duration_ms = ?,
                       step_count = ?, completed_steps = ?, tool_calls = ?, replans = ?,
                       result_status = ?, result_summary = ?, facts = ?, sources = ?,
                       unresolved = ?, error_type = ?
                   WHERE task_id = ?""",
                (
                    status,
                    int(self._clock()),
                    int(self._clock()),
                    round(duration_ms, 2),
                    budget.steps_used,
                    len(result.facts),
                    budget.tool_calls_used,
                    budget.replans_used,
                    result.status,
                    result.summary[:500],
                    json.dumps(result.facts[:10], ensure_ascii=False),
                    json.dumps(result.sources[:5], ensure_ascii=False),
                    json.dumps(result.unresolved[:5], ensure_ascii=False),
                    error_type,
                    task_id,
                ),
            )
            await self._db.execute(
                "UPDATE agent_goals SET status = ?, completed_at = ? WHERE goal_id = ?",
                (
                    "completed" if status == "completed" else status,
                    int(self._clock()),
                    result.goal_id,
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to finalise task %s", task_id, exc_info=True)

    async def _trace(self, task_id: str, level: str, event: str, detail: str = "") -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO agent_traces (task_id, level, event, detail, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (task_id, level, event, detail[:500], int(self._clock())),
            )
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to write agent trace", exc_info=True)

    # ---------------------------------------------------------- housekeeping

    async def housekeeping(self) -> dict[str, int]:
        """Timeout + stale cleanup; called by the shared BehaviorScheduler (spec §104)."""
        self._last_housekeeping = self._clock()
        summary = {"timed_out": 0, "paused": 0, "stale": 0}
        if self._db is None:
            return summary

        now = int(self._clock())
        timeout_limit = now - int(self.config.budget.max_execution_seconds * 2)
        try:
            rows = await self._db.fetchall(
                "SELECT task_id, status FROM agent_tasks WHERE status IN"
                " ('running','planning','ready','replanning')"
            )
            for row in rows:
                task_id = row["task_id"]
                if task_id in self._cancels or task_id in self._active_by_session.values():
                    continue  # in-flight in this process
                await self._db.execute(
                    "UPDATE agent_tasks SET status = 'expired', updated_at = ?,"
                    " error_type = 'stale_after_restart' WHERE task_id = ?",
                    (now, task_id),
                )
                cached = self._tasks.get(task_id)
                if cached is not None:
                    cached["status"] = "expired"
                    cached["updated_at"] = now
                summary["stale"] += 1
                await self._trace(task_id, "WARNING", "stale_task_expired", "no live runner")
            paused = await self._db.fetchall(
                "SELECT task_id FROM agent_tasks WHERE status = 'paused' AND updated_at < ?",
                (timeout_limit,),
            )
            summary["paused"] = len(paused)
        except Exception:  # noqa: BLE001 - housekeeping must never raise
            self._log.debug("Agent housekeeping failed", exc_info=True)
        return summary

    async def mark_running_tasks_paused(self) -> int:
        """On startup, an interrupted task becomes ``paused`` (spec §51/§131)."""
        if self._db is None:
            return 0
        try:
            rows = await self._db.fetchall(
                "SELECT task_id FROM agent_tasks WHERE status IN"
                " ('running','planning','ready','replanning','waiting')"
            )
            for row in rows:
                await self._db.execute(
                    "UPDATE agent_tasks SET status = 'paused', updated_at = ? WHERE task_id = ?",
                    (int(self._clock()), row["task_id"]),
                )
                await self._trace(row["task_id"], "INFO", "paused_after_restart", "")
            if rows:
                self._log.info("[Agent] marked %d interrupted task(s) as paused", len(rows))
            return len(rows)
        except Exception:  # noqa: BLE001
            self._log.debug("Failed to pause stale tasks", exc_info=True)
            return 0

    # ----------------------------------------------------------------- query

    async def get(self, task_id: str) -> dict[str, Any] | None:
        if task_id in self._tasks:
            return self._tasks[task_id]
        if self._db is None:
            return None
        row = await self._db.fetchone("SELECT * FROM agent_tasks WHERE task_id = ?", (task_id,))
        return dict(row) if row else None

    async def find_active(self, session_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = await self._db.fetchone(
            "SELECT * FROM agent_tasks WHERE session_id = ? AND status IN"
            " ('created','planning','ready','running','waiting','replanning')"
            " ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )
        return dict(row) if row else None

    async def find_paused(self, session_id: str) -> dict[str, Any] | None:
        if self._db is None:
            return None
        row = await self._db.fetchone(
            "SELECT * FROM agent_tasks WHERE session_id = ? AND status = 'paused'"
            " ORDER BY updated_at DESC LIMIT 1",
            (session_id,),
        )
        return dict(row) if row else None

    async def list_tasks(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        if status:
            rows = await self._db.fetchall(
                "SELECT * FROM agent_tasks WHERE status = ? ORDER BY updated_at DESC LIMIT ?",
                (status, limit),
            )
        else:
            rows = await self._db.fetchall(
                "SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT ?", (limit,)
            )
        return [dict(row) for row in rows]

    async def task_detail(self, task_id: str) -> dict[str, Any]:
        """Everything the WebUI shows for one task (spec §54), no hidden reasoning."""
        empty: dict[str, Any] = {"task": None, "goal": None, "plans": [], "steps": [],
                                 "observations": [], "traces": []}
        if self._db is None:
            return empty
        task = await self.get(task_id)
        if task is None:
            return empty
        goal = await self._db.fetchone(
            "SELECT * FROM agent_goals WHERE goal_id = ?", (task.get("goal_id"),)
        )
        plans = await self._db.fetchall(
            "SELECT * FROM agent_plans WHERE task_id = ? ORDER BY version ASC", (task_id,)
        )
        steps = await self._db.fetchall(
            "SELECT * FROM agent_steps WHERE task_id = ? ORDER BY id ASC", (task_id,)
        )
        observations = await self._db.fetchall(
            "SELECT * FROM agent_observations WHERE task_id = ? ORDER BY id ASC LIMIT 200",
            (task_id,),
        )
        traces = await self._db.fetchall(
            "SELECT * FROM agent_traces WHERE task_id = ? ORDER BY id ASC LIMIT 200", (task_id,)
        )
        return {
            "task": task,
            "goal": dict(goal) if goal else None,
            "plans": [dict(row) for row in plans],
            "steps": [dict(row) for row in steps],
            "observations": [dict(row) for row in observations],
            "traces": [dict(row) for row in traces],
        }

    async def metrics(self) -> dict[str, Any]:
        """Dashboard numbers (spec §91/§139)."""
        if self._db is None:
            return {}
        rows = await self._db.fetchall(
            "SELECT * FROM agent_tasks ORDER BY updated_at DESC LIMIT 500"
        )
        total = len(rows)
        if not total:
            return {
                "total": 0, "active": 0, "completed": 0, "failed": 0, "cancelled": 0,
                "paused": 0, "success_rate": 0.0, "partial_rate": 0.0, "failure_rate": 0.0,
                "avg_steps": 0.0, "avg_tool_calls": 0.0, "avg_duration_ms": 0.0,
                "replan_rate": 0.0, "timeout_rate": 0.0,
            }
        completed = sum(1 for row in rows if row["status"] == "completed")
        partial = sum(1 for row in rows if row["result_status"] == "partial")
        failed = sum(1 for row in rows if row["status"] == "failed")
        cancelled = sum(1 for row in rows if row["status"] == "cancelled")
        paused = sum(1 for row in rows if row["status"] == "paused")
        expired = sum(1 for row in rows if row["status"] == "expired")
        active = sum(
            1
            for row in rows
            if row["status"] in ("created", "planning", "ready", "running", "waiting", "replanning")
        )
        return {
            "total": total,
            "active": active,
            "completed": completed,
            "failed": failed,
            "cancelled": cancelled,
            "paused": paused,
            "expired": expired,
            "success_rate": round(completed / total, 3),
            "partial_rate": round(partial / total, 3),
            "failure_rate": round(failed / total, 3),
            "avg_steps": round(sum(row["step_count"] for row in rows) / total, 2),
            "avg_tool_calls": round(sum(row["tool_calls"] for row in rows) / total, 2),
            "avg_duration_ms": round(sum(row["duration_ms"] for row in rows) / total, 1),
            "replan_rate": round(sum(1 for row in rows if row["replans"]) / total, 3),
            "timeout_rate": round(expired / total, 3),
        }

    def policy_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.config.enabled,
            "autonomy": self.config.autonomy,
            "mode": self.config.mode.model_dump(),
            "budget": self.config.budget.model_dump(),
            "planner_model": self.config.planner.model or "(model router)",
            "evaluator_model": self.config.evaluator.model or "(model router)",
            "evaluator_use_llm": self.config.evaluator.use_llm,
            "background_enabled": bool(self.config.background.get("enabled", False)),
            "active_tasks": len(self._active_by_session),
        }

    async def simulate(
        self,
        text: str,
        *,
        session_id: str = "webui:simulation",
        user_id: str = "webui-admin",
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Plan without executing — the WebUI simulator (spec §88/§140/§141)."""
        allowed = self.tools.registry.names() if self.tools else []
        candidates = self.tools.registry.candidates(text) if self.tools else []
        classification = self.classifier.classify(
            text, tool_candidates=len(candidates)
        )
        payload: dict[str, Any] = {
            "classification": classification.kind,
            "reason": classification.reason,
            "control": classification.control,
            "agent_eligible": self.can_handle(classification.kind),
            "candidates": [
                {"name": tool.metadata.name, "score": score} for tool, score in candidates
            ],
            "dry_run": dry_run,
        }
        if classification.kind != "multi_step":
            payload["note"] = "该消息不需要多步 Agent（走普通聊天或单工具路径）"
            return payload

        goal = self.goal_parser.parse(
            text, session_id=session_id, user_id=user_id, classification=classification
        )
        try:
            plan = await self.planner.plan(goal, allowed_tools=allowed)
        except AgentError as exc:
            payload["error"] = f"{exc.error_type}: {exc}"
            return payload
        payload["goal"] = goal.description
        payload["plan"] = {
            "version": plan.version,
            "summary": plan.summary,
            "criteria": plan.criteria,
            "steps": [step.model_dump() for step in plan.steps],
        }
        payload["estimated_tool_calls"] = sum(1 for step in plan.steps if step.tool)
        payload["estimated_steps"] = len(plan.steps)
        return payload

    @property
    def last_housekeeping(self) -> float:
        return self._last_housekeeping

