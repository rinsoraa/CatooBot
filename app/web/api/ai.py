"""`/api/v1/ai/*`：Provider、模型、角色、用量（WebUI v1.0 · W2 契约 §6）。

路由只做参数整形，业务事实全在 :class:`~app.web.services.ai_admin.AIAdminService`；
每个处理器都由 :func:`~app.web.api.common.json_endpoint` 包装，异常自动变成契约信封。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from aiohttp import web

from app.web.api.common import (
    API_PREFIX,
    bad_request,
    conflict,
    json_endpoint,
    ok,
    read_json,
    read_query_int,
)
from app.web.routes.base import WebContext

if TYPE_CHECKING:
    from app.web.services.ai_admin import AIAdminService


class AiApiRoutes(WebContext):
    """AI 区的 v1 端点（`GET/PUT/DELETE /api/v1/ai/...`）。"""

    _ai_admin: AIAdminService | None = None

    def _ai(self) -> AIAdminService:
        admin = getattr(self, "_ai_admin", None)
        if admin is None:
            from app.web.services.ai_admin import AIAdminService

            admin = AIAdminService(self._bot, self._config_admin)
            self._ai_admin = admin
        return admin

    def _register_v1_ai(self, app: web.Application) -> None:
        # imported here on purpose: ai_admin needs api.common, and a module-level
        # import would close a cycle through the api package initializer
        from app.web.services.ai_admin import AIAdminService

        self._ai_admin = AIAdminService(self._bot, self._config_admin)
        wrap = json_endpoint
        prefix = f"{API_PREFIX}/ai"
        app.router.add_get(f"{prefix}/status", wrap(self._v1_ai_status))
        app.router.add_get(f"{prefix}/providers", wrap(self._v1_ai_providers))
        app.router.add_put(f"{prefix}/providers/{{name}}", wrap(self._v1_ai_provider_put))
        app.router.add_delete(f"{prefix}/providers/{{name}}", wrap(self._v1_ai_provider_delete))
        app.router.add_get(f"{prefix}/models", wrap(self._v1_ai_models))
        app.router.add_put(f"{prefix}/models/order", wrap(self._v1_ai_model_order))
        app.router.add_put(f"{prefix}/models/{{name}}", wrap(self._v1_ai_model_put))
        app.router.add_delete(f"{prefix}/models/{{name}}", wrap(self._v1_ai_model_delete))
        app.router.add_post(f"{prefix}/models/{{name}}/test", wrap(self._v1_ai_model_test))
        app.router.add_get(f"{prefix}/roles", wrap(self._v1_ai_roles))
        app.router.add_put(f"{prefix}/roles/{{role}}", wrap(self._v1_ai_role_put))
        app.router.add_get(f"{prefix}/usage", wrap(self._v1_ai_usage))
        app.router.add_post(f"{prefix}/router/reset", wrap(self._v1_ai_router_reset))

    async def _v1_ai_status(self, request: web.Request) -> web.Response:
        return ok(await self._ai().status(), request=request)

    # ------------------------------------------------------------------ provider

    async def _v1_ai_providers(self, request: web.Request) -> web.Response:
        return ok({"items": self._ai().providers()}, request=request)

    async def _v1_ai_provider_put(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        item = await self._ai().upsert_provider(
            request.match_info["name"],
            type=str(body.get("type", "openai_compatible")),
            base_url=str(body.get("base_url", "")),
            api_key_env=str(body.get("api_key_env", "")),
        )
        return ok(item, request=request)

    async def _v1_ai_provider_delete(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        force = _force(request)
        body = await read_json(request, required=False)
        expected = "force" if force else name
        if body.get("confirm") != expected:
            if force:
                message = (
                    f"删除 Provider「{name}」不可恢复：该 Provider 及其下全部模型与相关绑定"
                    "都将从配置中移除；请带 confirm=force"
                )
            else:
                message = (
                    f"删除 Provider「{name}」不可恢复：该 Provider 及其 API Key 引用"
                    f"将从配置中移除；请带 confirm={name}"
                )
            raise conflict(message, code="ai.confirm_required")
        result = await self._ai().delete_provider(name, force=force)
        return ok(result, request=request)

    # --------------------------------------------------------------------- model

    async def _v1_ai_models(self, request: web.Request) -> web.Response:
        return ok({"items": await self._ai().models()}, request=request)

    async def _v1_ai_model_put(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        body = await read_json(request)
        admin = self._ai()
        exists = any(model.name == name for model in self._bot.config.ai.models)
        if exists:
            changes: dict[str, Any] = {}
            if "provider" in body:
                changes["provider"] = str(body["provider"])
            if "model" in body:
                changes["model"] = str(body["model"])
            if "enabled" in body:
                changes["enabled"] = bool(body["enabled"])
            item = await admin.update_model(name, **changes)
        else:
            item = await admin.create_model(
                name,
                provider=str(body.get("provider", "")),
                model=str(body.get("model", "")),
                enabled=bool(body.get("enabled", True)),
            )
        return ok(item, request=request)

    async def _v1_ai_model_delete(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        force = _force(request)
        body = await read_json(request, required=False)
        expected = "force" if force else name
        if body.get("confirm") != expected:
            if force:
                message = (
                    f"删除模型「{name}」不可恢复：该模型及其承担的全部用途绑定"
                    "都将从配置中移除；请带 confirm=force"
                )
            else:
                message = (
                    f"删除模型「{name}」不可恢复：该模型将从配置与失败转移顺序中移除；"
                    f"请带 confirm={name}"
                )
            raise conflict(message, code="ai.confirm_required")
        result = await self._ai().delete_model(name, force=force)
        return ok(result, request=request)

    async def _v1_ai_model_order(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        order = body.get("order")
        if not isinstance(order, list) or not all(isinstance(name, str) for name in order):
            raise bad_request("order 必须是模型别名字符串数组", field="order")
        return ok(
            {"order": await self._ai().reorder([str(name) for name in order])}, request=request
        )

    async def _v1_ai_model_test(self, request: web.Request) -> web.Response:
        body = await read_json(request, required=False)
        prompt = str(body.get("prompt") or "ping")
        result = await self._ai().test_model(request.match_info["name"], prompt=prompt)
        return ok(result, request=request)

    # --------------------------------------------------------------------- roles

    async def _v1_ai_roles(self, request: web.Request) -> web.Response:
        return ok({"items": self._ai().roles()}, request=request)

    async def _v1_ai_role_put(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        if "model" not in body:
            raise bad_request("缺少 model 字段（空字符串表示解绑）", field="model")
        result = await self._ai().set_role(request.match_info["role"], str(body["model"]))
        return ok(result, request=request)

    # --------------------------------------------------------------- usage/reset

    async def _v1_ai_usage(self, request: web.Request) -> web.Response:
        days = read_query_int(request, "days", default=7, minimum=1, maximum=90)
        group_by = request.query.get("group_by", "model")
        items = await self._ai().usage(days=days, group_by=group_by)
        return ok({"items": items, "days": days, "group_by": group_by}, request=request)

    async def _v1_ai_router_reset(self, request: web.Request) -> web.Response:
        body = await read_json(request, required=False)
        if body.get("confirm") != "reset":
            raise conflict(
                "重置路由器会清空内存中的冷却与失败计数，请带 confirm=reset",
                code="ai.confirm_required",
            )
        return ok(await self._ai().reset_router(), request=request)


def _force(request: web.Request) -> bool:
    return request.query.get("force", "").lower() in ("1", "true", "yes")
