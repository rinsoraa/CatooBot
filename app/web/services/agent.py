"""Agent admin service: dashboard, task explorer, detail, controls, simulator.

Everything the operator needs to understand *why* the agent did something —
structured plans, steps, observations and traces. Never hidden reasoning, and
never exposed to QQ (spec v0.7 §54/§82/§152).
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot


class AgentAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Agent")

    @property
    def runtime(self) -> Any:
        return self.bot.agent

    # ------------------------------------------------------------- dashboard

    async def dashboard(self) -> dict[str, Any]:
        metrics = await self.runtime.metrics()
        recent = await self.runtime.list_tasks(limit=10)
        return {
            "metrics": metrics,
            "recent": recent,
            "policy": self.runtime.policy_snapshot(),
            "health": await self.health(),
        }

    async def health(self) -> dict[str, Any]:
        """Component check (spec v0.7 §92)."""
        runtime = self.runtime
        planner_ok = runtime.planner is not None and runtime.engine is not None
        executor_ok = runtime.executor is not None and self.bot.tools is not None
        tools_ok = bool(self.bot.tools and self.bot.tools.registry.all())
        return {
            "planner": "ok" if planner_ok else "unavailable",
            "executor": "ok" if executor_ok else "unavailable",
            "evaluator": "ok" if runtime.evaluator is not None else "unavailable",
            "tool_router": "ok" if tools_ok else "no tools",
            "task_store": "ok" if self.bot.database is not None else "no database",
            "last_housekeeping": runtime.last_housekeeping,
        }

    # ----------------------------------------------------------------- tasks

    async def tasks(self, *, status: str = "", limit: int = 100) -> list[dict[str, Any]]:
        return await self.runtime.list_tasks(status=status, limit=limit)

    async def task_detail(self, task_id: str) -> dict[str, Any]:
        detail = await self.runtime.task_detail(task_id)
        # plans/steps/observations arrive as JSON strings from SQLite — decode
        for plan in detail.get("plans", []):
            plan["criteria"] = _loads(plan.get("criteria"), [])
            plan["steps"] = _loads(plan.get("steps"), [])
        for step in detail.get("steps", []):
            step["depends_on"] = _loads(step.get("depends_on"), [])
        for observation in detail.get("observations", []):
            observation["data"] = _loads(observation.get("data"), {})
        task = detail.get("task") or {}
        task["facts"] = _loads(task.get("facts"), [])
        task["sources"] = _loads(task.get("sources"), [])
        task["unresolved"] = _loads(task.get("unresolved"), [])
        return detail

    # -------------------------------------------------------------- controls

    async def control(self, action: str, task_id: str) -> dict[str, Any]:
        runtime = self.runtime
        if action == "pause":
            ok = await runtime.pause(task_id, reason="webui")
        elif action == "resume":
            ok = await runtime.resume(task_id)
        elif action == "cancel":
            ok = await runtime.cancel(task_id, reason="webui")
        elif action == "retry":
            detail = await runtime.task_detail(task_id)
            goal = detail.get("goal") or {}
            text = str(goal.get("description") or "")
            if not text:
                return {"ok": False, "detail": "task has no goal text to retry"}
            result = await runtime.run(
                text,
                session_id=str(goal.get("session_id") or "webui:retry"),
                user_id=str(goal.get("user_id") or "webui-admin"),
                group_id=goal.get("group_id"),
            )
            return {
                "ok": True,
                "detail": f"retried -> {result.status}",
                "result": result.model_dump(),
            }
        elif action == "replay":
            detail = await runtime.task_detail(task_id)
            goal = detail.get("goal") or {}
            simulation = await runtime.simulate(
                str(goal.get("description") or ""), session_id="webui:replay"
            )
            return {
                "ok": True,
                "detail": "replay（仅重放规划，不发送任何消息）",
                "simulation": simulation,
            }
        else:
            return {"ok": False, "detail": f"unknown action: {action}"}
        return {"ok": bool(ok), "detail": f"{action} {'applied' if ok else 'skipped'}"}

    # ------------------------------------------------------------- simulator

    async def simulate(self, text: str, *, execute: bool = False) -> dict[str, Any]:
        """Plan (and optionally dry-run) without touching the outside world."""
        payload = await self.runtime.simulate(text)
        if execute:
            payload["dry_run_result"] = await self.dry_run(text)
        return payload

    async def dry_run(self, text: str) -> dict[str, Any]:
        """Planner = real, tool executor = mocked (spec v0.7 §89/§141)."""
        payload = await self.runtime.simulate(text)
        plan = payload.get("plan") or {}
        steps = plan.get("steps") or []
        return {
            "steps": [
                {
                    "id": step.get("id"),
                    "description": step.get("description"),
                    "tool": step.get("tool"),
                    "would_call": bool(step.get("tool")),
                    "arguments": step.get("arguments"),
                }
                for step in steps
            ],
            "note": "Dry Run：只展示会调用什么工具，不会真的请求外部服务，也不会发送 QQ 消息。",
        }

    # --------------------------------------------------------------- traces

    async def traces(self, task_id: str, limit: int = 200) -> list[dict[str, Any]]:
        if self.bot.database is None:
            return []
        rows = await self.bot.database.fetchall(
            "SELECT * FROM agent_traces WHERE task_id = ? ORDER BY id ASC LIMIT ?",
            (task_id, limit),
        )
        return [dict(row) for row in rows]


def _loads(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
