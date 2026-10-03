"""`/api/v1` 运行时与日志（WebUI v1.0 · W2 契约 §3）。

只读投影全部来自 :class:`~app.web.services.read_model.RuntimeReadService`；
两个动作走既有 Core 入口，不复制业务逻辑：

* ``POST /runtime/tick`` —— ``RuntimeScheduler.tick_once()``（受 ``_world_lock``
  保护，无副作用外溢；调度器没在跑 → 503 ``world.not_running``）；
* ``POST /runtime/actions/{name}`` —— 既有 ``AdminService.runtime_action``。

``/logs/tail`` 与 ``/logs/channels`` 只做服务端过滤与字段解析（AdminService /
narrator.CHANNELS），前端不再自己过滤文本。
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.web.api.common import (
    API_PREFIX,
    conflict,
    json_endpoint,
    not_found,
    ok,
    read_query_int,
    unavailable,
)
from app.web.routes.base import WebContext
from app.web.services.read_model import RuntimeReadService

#: ``POST /runtime/actions/{name}`` 允许的既有动作（AdminService.runtime_action）
RUNTIME_ACTIONS = frozenset(
    {"reload_persona", "reload_plugins", "reload_models", "restore_model_overrides"}
)


class RuntimeApiRoutes(WebContext):
    """`/api/v1/overview`、`/runtime/*`、`/logs/*`。"""

    _read_model_service: RuntimeReadService | None = None

    def _read(self) -> RuntimeReadService:
        if self._read_model_service is None:
            self._read_model_service = RuntimeReadService(
                self._bot,
                hub=getattr(self, "_hub", None),
                admin=getattr(self, "_admin", None),
            )
        return self._read_model_service

    # ----------------------------------------------------------------- reads

    async def _v1_overview(self, request: web.Request) -> web.Response:
        return ok(await self._read().overview(), request=request)

    async def _v1_runtime(self, request: web.Request) -> web.Response:
        return ok(await self._read().runtime(), request=request)

    async def _v1_runtime_status(self, request: web.Request) -> web.Response:
        return ok(await self._read().runtime(), request=request)

    async def _v1_runtime_scheduler(self, request: web.Request) -> web.Response:
        data = await self._read().runtime()
        return ok(data["scheduler"], request=request)

    # --------------------------------------------------------------- actions

    async def _v1_runtime_tick(self, request: web.Request) -> web.Response:
        """手动跑一次世界 tick（契约 §3；受调度器与 ``_world_lock`` 约束）。"""
        scheduler = getattr(self._bot, "runtime_scheduler", None)
        if scheduler is None or not bool(getattr(scheduler, "running", False)):
            raise unavailable(
                "世界调度器未运行：沙盒未启用或 runtime 已关闭", code="world.not_running"
            )
        sandbox = getattr(self._bot, "sandbox", None)
        lock = getattr(sandbox, "_world_lock", None) if sandbox is not None else None
        if lock is not None and lock.locked():
            raise conflict("世界正在处理一次 tick 或外部事件，请稍后再试", code="world.busy")
        report = await scheduler.tick_once()
        return ok(
            {
                "ran": True,
                "minutes": report.get("minutes"),
                "report": report,
                "ticks": int(scheduler.ticks),
            },
            request=request,
        )

    async def _v1_runtime_action(self, request: web.Request) -> web.Response:
        name = str(request.match_info["name"])
        if name not in RUNTIME_ACTIONS:
            raise not_found(f"未知的运行时动作：{name}", code="world.action_unknown")
        result: dict[str, Any] = await self._admin.runtime_action(name)
        if not result.get("ok"):
            raise not_found(
                str(result.get("detail") or f"动作执行失败：{name}"), code="world.action_unknown"
            )
        return ok(
            {"done": True, "action": name, "detail": str(result.get("detail", ""))},
            request=request,
        )

    # ------------------------------------------------------------------ logs

    async def _v1_logs_tail(self, request: web.Request) -> web.Response:
        # 契约 §3 的 limit=400 默认；W5 起上限收紧到 500，并支持 channel 过滤
        limit = read_query_int(request, "limit", default=400, minimum=1, maximum=500)
        level = request.query.get("level", "")
        keyword = request.query.get("q", "") or request.query.get("keyword", "")
        channel = request.query.get("channel", "")
        data = await self._read().logs_tail(
            level=level, keyword=keyword, lines=limit, channel=channel
        )
        return ok(data, request=request)

    async def _v1_logs_channels(self, request: web.Request) -> web.Response:
        return ok(self._read().logs_channels(), request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_runtime(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/overview", wrap(self._v1_overview))
        app.router.add_get(f"{API_PREFIX}/runtime", wrap(self._v1_runtime))
        app.router.add_get(f"{API_PREFIX}/runtime/status", wrap(self._v1_runtime_status))
        app.router.add_get(f"{API_PREFIX}/runtime/scheduler", wrap(self._v1_runtime_scheduler))
        app.router.add_post(f"{API_PREFIX}/runtime/tick", wrap(self._v1_runtime_tick))
        app.router.add_post(f"{API_PREFIX}/runtime/actions/{{name}}", wrap(self._v1_runtime_action))
        app.router.add_get(f"{API_PREFIX}/logs/tail", wrap(self._v1_logs_tail))
        app.router.add_get(f"{API_PREFIX}/logs/channels", wrap(self._v1_logs_channels))
