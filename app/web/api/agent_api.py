"""`/api/v1/agent*` — Agent 面板、任务浏览、控制与干跑（WebUI v1.0 · W5 §7.4）。

全部委托 :class:`~app.web.services.agent.AgentAdminService`（其背后是真实的
``AgentRuntime``：planner/executor/evaluator/store）。这里不复制任何规划逻辑。

状态推导（诚实口径，绝不猜测）：

* ``bot.agent`` 不存在 → ``unavailable``；
* ``bot.agent.enabled`` 为假（``agent.enabled=false``）→ ``disabled``；
* 否则 → ``ready``。

``simulate`` 是干跑：只做分类 + 规划，不执行工具、不发任何 QQ 消息。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web.api.common import (
    API_PREFIX,
    bad_request,
    conflict,
    json_endpoint,
    not_found,
    ok,
    read_json,
    read_query_int,
)
from app.web.routes.base import WebContext

#: AgentAdminService.control 允许的动作
AGENT_ACTIONS = frozenset({"pause", "resume", "cancel", "retry", "replay"})

#: 仍在进行中的任务状态（AgentRuntime.metrics 的 active 口径）
ACTIVE_STATUSES = ("created", "planning", "ready", "running", "waiting", "replanning")


class AgentApiRoutes(WebContext):
    """`/api/v1/agent`、`/api/v1/agent/tasks*`、`/api/v1/agent/simulate`。"""

    @property
    def _agent_runtime(self) -> Any | None:
        return getattr(self._bot, "agent", None)

    def _status(self) -> str:
        runtime = self._agent_runtime
        if runtime is None:
            return "unavailable"
        return "ready" if bool(getattr(runtime, "enabled", False)) else "disabled"

    def _service(self) -> Any:
        service = getattr(self, "_agent_admin", None)
        if service is None:
            from app.web.services.agent import AgentAdminService

            service = AgentAdminService(self._bot)
            self._agent_admin = service
        return service

    # ------------------------------------------------------------------ panel

    async def _v1_agent(self, request: web.Request) -> web.Response:
        runtime = self._agent_runtime
        if runtime is None:
            return ok(
                {
                    "status": "unavailable",
                    "health": None,
                    "active_tasks": [],
                    "recent_tasks": [],
                    "policy": None,
                    "budget": None,
                    "planner_model": None,
                    "evaluator_model": None,
                },
                request=request,
            )
        service = self._service()
        dashboard = await service.dashboard()
        policy = dashboard.get("policy") or {}
        recent = list(dashboard.get("recent") or [])
        active = [task for task in recent if str(task.get("status", "")) in ACTIVE_STATUSES]
        # recent 快照只有 10 条；进行中的任务单独取全量再过滤
        if len(active) < int((dashboard.get("metrics") or {}).get("active") or 0):
            all_tasks = await service.tasks(limit=500)
            active = [task for task in all_tasks if str(task.get("status", "")) in ACTIVE_STATUSES]
        return ok(
            {
                "status": self._status(),
                "health": dashboard.get("health") or await service.health(),
                "active_tasks": active,
                "recent_tasks": recent,
                "policy": policy,
                "budget": policy.get("budget"),
                "planner_model": policy.get("planner_model"),
                "evaluator_model": policy.get("evaluator_model"),
            },
            request=request,
        )

    # ------------------------------------------------------------------ tasks

    async def _v1_agent_tasks(self, request: web.Request) -> web.Response:
        status = request.query.get("status", "")
        limit = read_query_int(request, "limit", default=100, minimum=1, maximum=500)
        offset = read_query_int(request, "offset", default=0, minimum=0, maximum=1_000_000)
        service = self._service()
        window = min(1000, offset + limit)
        rows = await service.tasks(status=status, limit=window)
        items = rows[offset : offset + limit]
        total: int | None = len(rows) if len(rows) < window else None
        return ok({"items": items, "total": total}, request=request)

    async def _v1_agent_task_detail(self, request: web.Request) -> web.Response:
        task_id = str(request.match_info["task_id"])
        detail = await self._service().task_detail(task_id)
        if not detail.get("task"):
            raise not_found(f"Agent 任务不存在：{task_id}", code="agent.task_unknown")
        return ok(detail, request=request)

    async def _v1_agent_task_action(self, request: web.Request) -> web.Response:
        task_id = str(request.match_info["task_id"])
        action = str(request.match_info["action"])
        if action not in AGENT_ACTIONS:
            raise bad_request(f"未知的 Agent 动作：{action}", code="agent.action_unknown")
        service = self._service()
        detail = await service.task_detail(task_id)
        if not detail.get("task"):
            raise not_found(f"Agent 任务不存在：{task_id}", code="agent.task_unknown")
        result = await service.control(action, task_id)
        if not result.get("ok"):
            raise conflict(
                str(result.get("detail") or f"动作未能应用：{action}"),
                code="agent.action_failed",
            )
        return ok({"task_id": task_id, "action": action, **result}, request=request)

    # --------------------------------------------------------------- simulate

    async def _v1_agent_simulate(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        text = str(body.get("text", "")).strip()
        if not text:
            raise bad_request("缺少 text：需要一段文本才能模拟", field="text")
        # dry run only：只规划，不执行工具、不发消息
        data = await self._service().simulate(text, execute=False)
        return ok(data, request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_agent(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/agent", wrap(self._v1_agent))
        app.router.add_get(f"{API_PREFIX}/agent/tasks", wrap(self._v1_agent_tasks))
        app.router.add_post(f"{API_PREFIX}/agent/simulate", wrap(self._v1_agent_simulate))
        app.router.add_get(
            f"{API_PREFIX}/agent/tasks/{{task_id}}", wrap(self._v1_agent_task_detail)
        )
        app.router.add_post(
            f"{API_PREFIX}/agent/tasks/{{task_id}}/{{action}}",
            wrap(self._v1_agent_task_action),
        )
