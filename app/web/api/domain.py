"""`/api/v1` 领域只读与动作：角色 / 世界 / 记忆（WebUI v1.0 · W2 契约 §7.1/§7.3）。

三条规则贯穿本模块：

* 只读投影走 :class:`~app.web.services.read_model.RuntimeReadService`（纯读，
  不动 ``world_revision``/``cognitive_revision``）；
* 动作一律委托既有 Service（``AdminService`` / ``MemoryAdminService`` /
  ``sandbox.lifecycle_manager``），不复制业务逻辑；
* 危险动作（reset / reinitialize）必须带 ``{"confirm": "<动作名>"}``，
  否则 409 ``world.confirm_required``（沿用 ``routes/sandbox.py`` 的既有约定）。
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
    unavailable,
    unprocessable,
)
from app.web.routes.base import WebContext
from app.web.services.read_model import RuntimeReadService

#: 世界控制允许的动作（沿用 routes/sandbox.py:451-474）
WORLD_CONTROLS = frozenset({"pause", "resume", "reset", "reinitialize"})

#: reset / reinitialize 必须显式确认
WORLD_CONFIRM = {"reset": "reset", "reinitialize": "reinitialize"}

#: 记忆动作（MemoryAdminService.action）
MEMORY_ACTIONS = frozenset({"activate", "archive", "reembed", "delete"})


class DomainApiRoutes(WebContext):
    """`/api/v1/character*`、`/api/v1/world*`、`/api/v1/memories*`。"""

    _read_model_service: RuntimeReadService | None = None

    def _read(self) -> RuntimeReadService:
        if self._read_model_service is None:
            self._read_model_service = RuntimeReadService(
                self._bot,
                hub=getattr(self, "_hub", None),
                admin=getattr(self, "_admin", None),
                memory_admin=getattr(self, "_memory_admin", None),
            )
        return self._read_model_service

    # ------------------------------------------------------------- character

    async def _v1_character(self, request: web.Request) -> web.Response:
        return ok(await self._read().character(), request=request)

    async def _v1_character_patch(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        if not body:
            raise bad_request("请求体不能为空", code="request.empty")
        try:
            await self._admin.save_persona(body)
        except ValueError as exc:
            raise unprocessable(f"人设校验失败：{exc}", detail=str(exc)) from exc
        return ok(await self._read().character(), request=request)

    async def _v1_character_state(self, request: web.Request) -> web.Response:
        return ok(await self._admin.character_state(), request=request)

    # ----------------------------------------------------------------- world

    async def _v1_world(self, request: web.Request) -> web.Response:
        return ok(await self._read().world(), request=request)

    async def _v1_world_trace(self, request: web.Request) -> web.Response:
        limit = read_query_int(request, "limit", default=120, minimum=1, maximum=500)
        return ok(await self._read().world_trace(limit=limit), request=request)

    async def _v1_world_control(self, request: web.Request) -> web.Response:
        """pause / resume / reset / reinitialize（reset 类动作需 confirm）。"""
        action = str(request.match_info["action"])
        if action not in WORLD_CONTROLS:
            raise not_found(f"未知的世界控制动作：{action}", code="world.action_unknown")
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", True):
            raise unavailable("沙盒未启用，无法控制世界", code="world.not_running")
        if action in WORLD_CONFIRM:
            body = await read_json(request, required=False)
            if body.get("confirm") != action:
                raise conflict(
                    f"{action} 会清空角色数据，请带 confirm={action} 再试",
                    code="world.confirm_required",
                )
        if action == "pause":
            sandbox.phase = sandbox.phase.__class__.paused
        elif action == "resume":
            sandbox.phase = sandbox.phase.__class__.running
        elif action == "reset":
            lifecycle = getattr(self._bot, "lifecycle_manager", None)
            if lifecycle is None:
                raise unavailable("角色生命周期管理器不可用，无法重置", code="world.not_running")
            result: dict[str, Any] = await lifecycle.reset_character(confirm=True)
            if not result.get("ok"):
                raise conflict(f"重置失败：{result.get('reason') or '未知原因'}", code="world.busy")
            return ok({"done": True, "action": action, "detail": result}, request=request)
        else:  # reinitialize
            await sandbox.reinitialize()
        return ok({"done": True, "action": action, "phase": sandbox.phase.value}, request=request)

    # -------------------------------------------------------------- memories

    async def _v1_memories(self, request: web.Request) -> web.Response:
        query = request.query.get("q", "") or request.query.get("query", "")
        mode = request.query.get("mode", "hybrid") or "hybrid"
        limit = read_query_int(request, "limit", default=20, minimum=1, maximum=200)
        data = await self._memory_admin.search(query, mode=mode, limit=limit)
        items = list(data.get("results") or [])
        return ok(
            {
                "items": items,
                "next_cursor": None,
                "total": None,
                "count": len(items),
                "mode": data.get("mode", mode),
                "semantic_available": data.get("semantic_available"),
                "error": data.get("error"),
            },
            request=request,
        )

    async def _v1_memories_health(self, request: web.Request) -> web.Response:
        # 必须先于 /memories/{memory_id} 注册，否则 "health" 会被当成 id
        return ok(await self._memory_admin.health(), request=request)

    async def _v1_memory_detail(self, request: web.Request) -> web.Response:
        memory_id = self._memory_id(request)
        data = await self._memory_admin.detail(memory_id)
        if not data:
            raise not_found(f"记忆不存在：{memory_id}", code="memory.not_found")
        return ok(data, request=request)

    async def _v1_memory_action(self, request: web.Request) -> web.Response:
        action = str(request.match_info["action"])
        if action not in MEMORY_ACTIONS:
            raise bad_request(f"未知的记忆动作：{action}", code="memory.action_unknown")
        memory_id = self._memory_id(request)
        body = await read_json(request, required=False)
        done = await self._memory_admin.action(action, memory_id, body)
        if not done:
            raise not_found(f"记忆不存在或动作未生效：{memory_id}", code="memory.not_found")
        return ok({"done": True, "action": action, "memory_id": memory_id}, request=request)

    @staticmethod
    def _memory_id(request: web.Request) -> int:
        try:
            return int(request.match_info["memory_id"])
        except (KeyError, ValueError) as exc:
            raise bad_request("记忆 ID 必须是整数", field="memory_id") from exc

    # ----------------------------------------------------------- registration

    def _register_v1_domain(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/character", wrap(self._v1_character))
        app.router.add_patch(f"{API_PREFIX}/character", wrap(self._v1_character_patch))
        app.router.add_get(f"{API_PREFIX}/character/state", wrap(self._v1_character_state))
        app.router.add_get(f"{API_PREFIX}/world", wrap(self._v1_world))
        app.router.add_get(f"{API_PREFIX}/world/trace", wrap(self._v1_world_trace))
        app.router.add_post(f"{API_PREFIX}/world/control/{{action}}", wrap(self._v1_world_control))
        app.router.add_get(f"{API_PREFIX}/memories", wrap(self._v1_memories))
        app.router.add_get(f"{API_PREFIX}/memories/health", wrap(self._v1_memories_health))
        app.router.add_get(f"{API_PREFIX}/memories/{{memory_id}}", wrap(self._v1_memory_detail))
        app.router.add_post(
            f"{API_PREFIX}/memories/{{memory_id}}/{{action}}", wrap(self._v1_memory_action)
        )
