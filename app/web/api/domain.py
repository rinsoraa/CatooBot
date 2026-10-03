"""`/api/v1` 领域只读与动作：角色 / 世界 / 记忆 / 社交（WebUI v1.0 · W2 §7.1/§7.3、W5 §7）。

三条规则贯穿本模块：

* 只读投影走 :class:`~app.web.services.read_model.RuntimeReadService` /
  :class:`~app.web.services.world_read.WorldReadService`（纯读，
  不动 ``world_revision``/``cognitive_revision``）；
* 动作一律委托既有 Service（``AdminService`` / ``MemoryAdminService`` /
  ``BehaviorService`` / ``sandbox.lifecycle_manager``），不复制业务逻辑；
* 危险动作（reset / reinitialize / 删除记忆）必须带 ``{"confirm": "<动作名>"}``，
  否则 409 ``world.confirm_required`` / ``memory.confirm_required``。

W5 起 ``DomainApiRoutes`` 同时承载 ``SocialApiRoutes``（``app/web/api/social_api.py``），
使 ``register_v1`` 的 ``_register_v1_social`` 钩子无需改动组合文件即可生效。
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
from app.web.api.social_api import SocialApiRoutes
from app.web.routes.base import WebContext
from app.web.services.read_model import RuntimeReadService
from app.web.services.world_read import WorldReadService

#: 世界控制允许的动作（沿用 routes/sandbox.py:451-474）
WORLD_CONTROLS = frozenset({"pause", "resume", "reset", "reinitialize"})

#: reset / reinitialize 必须显式确认
WORLD_CONFIRM = {"reset": "reset", "reinitialize": "reinitialize"}

#: 记忆动作（MemoryAdminService.action；W5 增加 edit，delete 需 confirm）
MEMORY_ACTIONS = frozenset({"activate", "archive", "reembed", "delete", "edit"})

#: 未闭环话题动作（BehaviorService.topic_action）
TOPIC_ACTIONS = frozenset({"resolve", "forget", "delete"})

#: PATCH /character/state 允许的字段（CharacterStateStore.update 的公开参数）
CHARACTER_STATE_FIELDS = frozenset(
    {
        "mood",
        "energy",
        "activity",
        "current_focus",
        "location",
        "social_state",
        "schedule_state",
        "current_goal",
        "current_project",
        "reason",
    }
)

#: 记忆检索模式（MemoryAdminService.search）
MEMORY_MODES = frozenset({"keyword", "semantic", "hybrid"})


class DomainApiRoutes(SocialApiRoutes, WebContext):
    """`/api/v1/character*`、`/api/v1/world*`、`/api/v1/memories*`、`/api/v1/social*`。"""

    _read_model_service: RuntimeReadService | None = None
    _world_read_service: WorldReadService | None = None

    def _read(self) -> RuntimeReadService:
        if self._read_model_service is None:
            self._read_model_service = RuntimeReadService(
                self._bot,
                hub=getattr(self, "_hub", None),
                admin=getattr(self, "_admin", None),
                memory_admin=getattr(self, "_memory_admin", None),
            )
        return self._read_model_service

    def _world(self) -> WorldReadService:
        if self._world_read_service is None:
            self._world_read_service = WorldReadService(
                self._bot,
                admin=getattr(self, "_admin", None),
                hub=getattr(self, "_hub", None),
            )
        return self._world_read_service

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

    async def _v1_character_state_patch(self, request: web.Request) -> web.Response:
        """更新角色状态字段（mood/energy/activity/…；未知字段 422）。"""
        body = await read_json(request)
        if not body:
            raise bad_request("请求体不能为空", code="request.empty")
        unknown = sorted(set(body) - CHARACTER_STATE_FIELDS)
        if unknown:
            raise unprocessable(f"不支持的状态字段：{unknown[0]}", field=unknown[0])
        changes: dict[str, Any] = {}
        for key, value in body.items():
            if key == "energy":
                if isinstance(value, bool) or not isinstance(value, (int, float)):
                    raise unprocessable("energy 必须是 0~1 的数字", field="energy")
                changes[key] = float(value)
            elif key == "reason":
                if not isinstance(value, str):
                    raise unprocessable("reason 必须是字符串", field="reason")
                changes[key] = value
            else:
                if not isinstance(value, str):
                    raise unprocessable(f"{key} 必须是字符串", field=key)
                changes[key] = value
        state = await self._admin.set_state(**changes)
        return ok(state, request=request)

    # ----------------------------------------------------------------- world

    async def _v1_world(self, request: web.Request) -> web.Response:
        return ok(await self._world().world(), request=request)

    async def _v1_world_trace(self, request: web.Request) -> web.Response:
        limit = read_query_int(request, "limit", default=120, minimum=1, maximum=500)
        return ok(await self._read().world_trace(limit=limit), request=request)

    async def _v1_world_timeline(self, request: web.Request) -> web.Response:
        limit = read_query_int(request, "limit", default=120, minimum=1, maximum=500)
        return ok(await self._world().timeline(limit=limit), request=request)

    async def _v1_world_topics(self, request: web.Request) -> web.Response:
        scope_key = request.query.get("scope_key", "") or ""
        status = request.query.get("status", "") or ""
        items = await self._behavior.list_topics(scope_key=scope_key, status=status)
        return ok({"items": items, "count": len(items)}, request=request)

    async def _v1_world_topic_action(self, request: web.Request) -> web.Response:
        action = str(request.match_info["action"])
        if action not in TOPIC_ACTIONS:
            raise bad_request(f"未知的话题动作：{action}", code="topic.action_unknown")
        try:
            topic_id = int(request.match_info["topic_id"])
        except (KeyError, ValueError) as exc:
            raise bad_request("话题 ID 必须是整数", field="topic_id") from exc
        topics = await self._behavior.list_topics()
        if not any(int(item.get("id") or -1) == topic_id for item in topics):
            raise not_found(f"话题不存在：{topic_id}", code="topic.not_found")
        done = await self._behavior.topic_action(action, topic_id)
        if not done:
            raise conflict(f"话题动作未生效：{action}", code="topic.action_failed")
        return ok({"done": True, "action": action, "topic_id": topic_id}, request=request)

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
        """浏览（无 q）/ 检索（有 q），两者都返回分页字段（W5 §7.3）。"""
        query = request.query.get("q", "") or request.query.get("query", "")
        mode = request.query.get("mode", "hybrid") or "hybrid"
        if mode not in MEMORY_MODES:
            raise bad_request(f"未知的检索模式：{mode}", code="memory.mode_unknown")
        scope_key = request.query.get("scope_key", "") or ""
        person = request.query.get("person", "") or ""
        if person and not scope_key:
            scope_key = f"user:{person}"
        category = request.query.get("category", "") or ""
        layer = request.query.get("layer", "") or ""
        status_raw = request.query.get("status")
        status = "active" if status_raw is None else status_raw
        limit = read_query_int(request, "limit", default=20, minimum=1, maximum=200)
        offset = read_query_int(request, "offset", default=0, minimum=0, maximum=100_000)

        if not query:
            page = await self._memory_admin.list_page(
                scope_key=scope_key,
                category=category,
                layer=layer,
                status=status,
                limit=limit,
                offset=offset,
            )
            return ok(
                {
                    **page,
                    "count": len(page["items"]),
                    "mode": "browse",
                    "semantic_available": None,
                    "error": None,
                },
                request=request,
            )
        if mode == "keyword":
            page = await self._memory_admin.list_page(
                keyword=query,
                scope_key=scope_key,
                category=category,
                layer=layer,
                status=status,
                limit=limit,
                offset=offset,
            )
            return ok(
                {
                    **page,
                    "count": len(page["items"]),
                    "mode": mode,
                    "semantic_available": False,
                    "error": None,
                },
                request=request,
            )
        # semantic / hybrid 沿用既有检索语义（W2 行为），分页在结果窗口上做
        fetched = await self._memory_admin.search(
            query, mode=mode, scope_key=scope_key, limit=offset + limit + 1
        )
        results = list(fetched.get("results") or [])
        window = results[offset : offset + limit]
        more = len(results) > offset + limit
        return ok(
            {
                "items": window,
                "next_cursor": str(offset + limit) if more else None,
                "total": None,
                "count": len(window),
                "mode": fetched.get("mode", mode),
                "semantic_available": fetched.get("semantic_available"),
                "error": fetched.get("error"),
            },
            request=request,
        )

    async def _v1_memories_timeline(self, request: web.Request) -> web.Response:
        # 必须先于 /memories/{memory_id} 注册，否则 "timeline" 会被当成 id
        limit = read_query_int(request, "limit", default=200, minimum=1, maximum=500)
        scope_key = request.query.get("scope_key", "") or ""
        items = await self._memory_admin.timeline(scope_key, limit)
        return ok(
            {"items": items, "count": len(items), "scope_key": scope_key or None},
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
        if action == "delete" and body.get("confirm") != "delete":
            raise conflict(
                "删除记忆不可恢复，请带 confirm=delete 再试",
                code="memory.confirm_required",
            )
        if action == "edit":
            content = str(body.get("content", "")).strip()
            if not content:
                raise bad_request("编辑记忆必须提供 content", code="memory.content_required")
            body = {**body, "content": content}
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
        app.router.add_patch(f"{API_PREFIX}/character/state", wrap(self._v1_character_state_patch))
        app.router.add_get(f"{API_PREFIX}/world", wrap(self._v1_world))
        app.router.add_get(f"{API_PREFIX}/world/trace", wrap(self._v1_world_trace))
        app.router.add_get(f"{API_PREFIX}/world/timeline", wrap(self._v1_world_timeline))
        app.router.add_get(f"{API_PREFIX}/world/topics", wrap(self._v1_world_topics))
        app.router.add_post(
            f"{API_PREFIX}/world/topics/{{topic_id}}/{{action}}",
            wrap(self._v1_world_topic_action),
        )
        app.router.add_post(f"{API_PREFIX}/world/control/{{action}}", wrap(self._v1_world_control))
        app.router.add_get(f"{API_PREFIX}/memories", wrap(self._v1_memories))
        app.router.add_get(f"{API_PREFIX}/memories/timeline", wrap(self._v1_memories_timeline))
        app.router.add_get(f"{API_PREFIX}/memories/health", wrap(self._v1_memories_health))
        app.router.add_get(f"{API_PREFIX}/memories/{{memory_id}}", wrap(self._v1_memory_detail))
        app.router.add_post(
            f"{API_PREFIX}/memories/{{memory_id}}/{{action}}", wrap(self._v1_memory_action)
        )
