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

import contextlib
import json
import tempfile
from pathlib import Path
from typing import Any

from aiohttp import web

from app.activity.model import ActivitySource, TransitionReason
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

#: 向量运维（沿用旧版 /memory/embeddings/{action} 语义；清缓存需 confirm=clear-cache）
EMBEDDING_ACTIONS = frozenset({"rebuild", "retry", "clear-cache"})
EMBEDDING_CONFIRM = {"clear-cache": "clear-cache"}

#: 行为调试动作（沿用旧版 /behavior/trigger/{action}）
BEHAVIOR_TRIGGERS = frozenset({"mood_up", "mood_down", "reset_state", "test_initiative"})

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
        # Phase 6A §十四：``activity`` 是**当前 Episode 的派生快照**，不能绕过 Episode 直接写
        # （否则就会出现"Episode 说 A、角色状态说 B"的分裂）。有活动能力时把它翻译成一次
        # 显式换活动（source=USER / reason=MANUAL），由 Episode 生命周期来落状态；没有活动能力
        # （关掉/装配失败）时才退回直接写 —— 那是明确的降级路径。
        requested_activity = changes.pop("activity", None)
        runtime = getattr(self._bot, "activity", None)
        if requested_activity is not None and runtime is not None:
            await runtime.switch_to(
                activity_name=str(requested_activity),
                source=ActivitySource.USER,
                reason=TransitionReason.MANUAL,
            )
        elif requested_activity is not None:
            changes["activity"] = requested_activity
        state = await self._admin.set_state(**changes)
        return ok(state, request=request)

    # ----------------------------------------------------------------- world

    async def _v1_world(self, request: web.Request) -> web.Response:
        return ok(await self._world().world(), request=request)

    async def _v1_world_activity(self, request: web.Request) -> web.Response:
        """Phase 6A：她此刻的**活动**（Activity Episode）只读投影。

        读端点恒 200：没有活动能力（未装配/配置关掉）时如实返回 ``enabled: false``
        —— 那是功能状态，不是故障。**只读**：这里没有 start / cancel / extend
        （§三十七：WebUI 不得修改 Episode），也没有任何能碰世界的动作。
        """
        runtime = getattr(self._bot, "activity", None)
        if runtime is None:
            return ok(
                {
                    "enabled": False,
                    "character_id": "",
                    "degraded": "",
                    "current": None,
                    "recent": [],
                },
                request=request,
            )
        try:
            data = await runtime.status()
        except Exception as exc:  # noqa: BLE001 - 只读失败不变成 5xx（如实降级）
            return ok(
                {
                    "enabled": False,
                    "character_id": "",
                    "degraded": type(exc).__name__,
                    "current": None,
                    "recent": [],
                },
                request=request,
            )
        limit = read_query_int(request, "limit", default=10, minimum=1, maximum=10)
        data["recent"] = list(data.get("recent") or [])[:limit]
        return ok(data, request=request)

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

    # ------------------------------------------------- 记忆运维（v0.8 迁移）

    async def _v1_memories_embeddings(self, request: web.Request) -> web.Response:
        """向量状态（旧版 /memory/embeddings 的只读部分）。"""
        return ok(await self._memory_admin.embedding_status(), request=request)

    async def _v1_memories_embedding_action(self, request: web.Request) -> web.Response:
        action = str(request.match_info["action"])
        if action not in EMBEDDING_ACTIONS:
            raise bad_request(f"未知的向量动作：{action}", code="memory.embedding_action_unknown")
        confirm = EMBEDDING_CONFIRM.get(action)
        if confirm:
            body = await read_json(request, required=False)
            if body.get("confirm") != confirm:
                raise conflict(
                    f"该动作需要 confirm={confirm}：{action}",
                    code="memory.confirm_required",
                )
        if action == "rebuild":
            result = await self._memory_admin.rebuild_embeddings(limit=500)
        elif action == "retry":
            result = await self._memory_admin.retry_failed()
        else:
            result = await self._memory_admin.clear_embedding_cache()
        return ok({"action": action, "result": result}, request=request)

    async def _v1_memories_consolidation(self, request: web.Request) -> web.Response:
        """整理状态（旧版 /memory/consolidation 的只读部分）。"""
        return ok(await self._memory_admin.consolidation_status(), request=request)

    async def _v1_memories_consolidation_run(self, request: web.Request) -> web.Response:
        """手动跑一次记忆整理（旧版 /memory/consolidation/run 的等价物）。"""
        body = await read_json(request, required=False)
        scope_key = str(body.get("scope", "") or "").strip()
        result = await self._memory_admin.run_consolidation(scope_key)
        return ok({"scope": scope_key, "result": result}, request=request)

    async def _v1_memories_retrieval_debug(self, request: web.Request) -> web.Response:
        """检索打分链路（旧版 /memory/retrieval-debug 的等价物，只读）。"""
        query = str(request.query.get("q", "") or "").strip()
        if not query:
            raise bad_request("检索调试需要 q=查询词", field="q", code="memory.query_required")
        scope_key = str(request.query.get("scope", "") or "").strip()
        return ok(await self._memory_admin.retrieval_debug(query, scope_key), request=request)

    async def _v1_session_clear(self, request: web.Request) -> web.Response:
        """清空某个会话的上下文（旧版 /api/sessions/clear 的等价物）。"""
        session_id = str(request.match_info["session_id"])
        body = await read_json(request, required=False)
        if body.get("confirm") != "clear":
            raise conflict(
                "清空会话上下文会让她忘掉这段对话，请带 confirm=clear 再试",
                code="session.confirm_required",
            )
        await self._admin.clear_session(session_id)
        return ok({"cleared": True, "session_id": session_id}, request=request)

    # ------------------------------------------------- 行为调试（v0.8 迁移）

    async def _v1_behavior_test_response(self, request: web.Request) -> web.Response:
        """试跑一次回复链路：生成但不发送（旧版 /behavior/test-response）。"""
        body = await read_json(request, required=False)
        text = str(body.get("text", "") or "").strip()
        if not text:
            raise bad_request("测试回复需要 text", field="text", code="behavior.text_required")
        return ok(await self._behavior.test_response(text), request=request)

    async def _v1_behavior_preview(self, request: web.Request) -> web.Response:
        """行为模拟器：延迟/分条/主动判定，永不发送（旧版 /behavior/preview）。"""
        body = await read_json(request, required=False)
        allowed = {"sim_time", "mood", "activity", "relationship", "topic", "sample_reply"}
        return ok(
            await self._behavior.preview({k: v for k, v in body.items() if k in allowed}),
            request=request,
        )

    async def _v1_behavior_trigger(self, request: web.Request) -> web.Response:
        """手动触发状态变化（旧版 /behavior/trigger/{action}）。"""
        action = str(request.match_info["action"])
        if action not in BEHAVIOR_TRIGGERS:
            raise bad_request(f"未知的行为动作：{action}", code="behavior.trigger_unknown")
        if action == "mood_up":
            result = await self._behavior.test_state_transition(+1)
        elif action == "mood_down":
            result = await self._behavior.test_state_transition(-1)
        elif action == "reset_state":
            result = await self._behavior.reset_state()
        else:
            preview = await self._behavior.preview(
                {"topic": "未完成的项目", "relationship": "familiar"}
            )
            result = preview.get("initiative", preview)
        return ok({"action": action, "result": result}, request=request)

    # ------------------------------------------------- 提示词（v0.8 迁移）

    async def _v1_prompts(self, request: web.Request) -> web.Response:
        """当前提示词：人设系统提示 + 记忆提取提示（旧版 /prompts）。"""
        return ok(await self._admin.get_prompts(), request=request)

    async def _v1_prompts_patch(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        data = {
            key: str(value)
            for key, value in body.items()
            if key in ("persona_system_prompt", "memory_extraction_prompt")
        }
        if not data:
            raise bad_request("没有可写的提示词字段", code="prompts.empty")
        await self._admin.save_prompts(data)
        return ok(await self._admin.get_prompts(), request=request)

    # ------------------------------------------------- 角色导入导出（v0.8 迁移）

    async def _v1_character_export(self, request: web.Request) -> web.Response:
        """导出角色域（人设/记忆/关系…）为一份 JSON 文档。"""
        return ok(await self._admin.export_character(), request=request)

    async def _v1_character_import(self, request: web.Request) -> web.Response:
        """导入角色文档：默认只预览，带 confirm=import 才真正写入（旧版两步式）。"""
        body = await read_json(request)
        document = body.get("document")
        if not isinstance(document, dict):
            raise bad_request(
                "导入需要 document 对象", field="document", code="character.document_required"
            )
        with tempfile.NamedTemporaryFile(
            "w", suffix=".json", delete=False, encoding="utf-8"
        ) as handle:
            json.dump(document, handle, ensure_ascii=False)
            path = handle.name
        try:
            preview = await self._admin.import_character_preview(path)
            if body.get("confirm") != "import":
                return ok({"preview": preview, "applied": False}, request=request)
            applied = await self._admin.import_character_confirm(path)
            return ok({"preview": preview, "applied": True, "result": applied}, request=request)
        finally:
            with contextlib.suppress(OSError):
                Path(path).unlink()

    # ----------------------------------------------------------- registration

    def _register_v1_domain(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/character", wrap(self._v1_character))
        app.router.add_patch(f"{API_PREFIX}/character", wrap(self._v1_character_patch))
        app.router.add_get(f"{API_PREFIX}/character/state", wrap(self._v1_character_state))
        app.router.add_patch(f"{API_PREFIX}/character/state", wrap(self._v1_character_state_patch))
        app.router.add_get(f"{API_PREFIX}/world", wrap(self._v1_world))
        # Phase 6A：世界活动（Episode）—— **只读**，最近 ≤10 条（§三十六/§三十七）
        app.router.add_get(f"{API_PREFIX}/world/activity", wrap(self._v1_world_activity))
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
        # 静态路径必须先于 /memories/{memory_id} 注册（否则会被当成 ID）
        app.router.add_get(f"{API_PREFIX}/memories/embeddings", wrap(self._v1_memories_embeddings))
        app.router.add_post(
            f"{API_PREFIX}/memories/embeddings/{{action}}",
            wrap(self._v1_memories_embedding_action),
        )
        app.router.add_get(
            f"{API_PREFIX}/memories/consolidation", wrap(self._v1_memories_consolidation)
        )
        app.router.add_post(
            f"{API_PREFIX}/memories/consolidation/run",
            wrap(self._v1_memories_consolidation_run),
        )
        app.router.add_get(
            f"{API_PREFIX}/memories/retrieval-debug", wrap(self._v1_memories_retrieval_debug)
        )
        app.router.add_post(
            f"{API_PREFIX}/sessions/{{session_id}}/clear", wrap(self._v1_session_clear)
        )
        # 行为调试 / 提示词 / 角色导入导出（v0.8 能力迁移）
        app.router.add_post(
            f"{API_PREFIX}/behavior/test-response", wrap(self._v1_behavior_test_response)
        )
        app.router.add_post(f"{API_PREFIX}/behavior/preview", wrap(self._v1_behavior_preview))
        app.router.add_post(
            f"{API_PREFIX}/behavior/triggers/{{action}}", wrap(self._v1_behavior_trigger)
        )
        app.router.add_get(f"{API_PREFIX}/prompts", wrap(self._v1_prompts))
        app.router.add_patch(f"{API_PREFIX}/prompts", wrap(self._v1_prompts_patch))
        app.router.add_get(f"{API_PREFIX}/character/export", wrap(self._v1_character_export))
        app.router.add_post(f"{API_PREFIX}/character/import", wrap(self._v1_character_import))
        app.router.add_get(f"{API_PREFIX}/memories/{{memory_id}}", wrap(self._v1_memory_detail))
        app.router.add_post(
            f"{API_PREFIX}/memories/{{memory_id}}/{{action}}", wrap(self._v1_memory_action)
        )
