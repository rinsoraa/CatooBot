"""`/api/v1/tools*` — 工具注册表、配置、测试、权限（WebUI v1.0 · W5 契约 §7.4）。

路由只做三件事：校验入参、调 :class:`~app.web.services.tools.ToolAdminService`、
按契约拼装响应。工具是否真的能执行、权限如何生效，全部留在既有 ToolRuntime。

两个确认门（写操作会碰真实外部服务/缓存）：

* ``POST /tools/{name}/test`` 必须带 ``{"confirm": "<name>"}``，否则
  409 ``tools.confirm_required`` —— 测试可能真的发起一次外部请求；
* ``POST /tools/cache/clear`` 必须带 ``{"confirm": "clear"}``。

``restart_required`` 一律来自 ToolRuntime 的事实：enable/disable、timeout、
cache_ttl、settings 都是热应用（``ToolRuntime.update_tool_settings``），
所以成功响应是 ``false``；若将来某个字段无法热应用，服务层会先返回该标记。
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
)
from app.web.routes.base import WebContext
from app.web.services.tools import ToolAdminService

#: PATCH /tools/{name} 允许的字段（全部可热应用）
TOOL_PATCH_FIELDS = frozenset({"enabled", "settings", "timeout", "cache_ttl_seconds"})

#: 权限规则允许的范围（tool_permissions.scope）
PERMISSION_SCOPES = frozenset({"user", "group"})

#: 缓存清理确认值
CACHE_CLEAR_CONFIRM = "clear"


class ToolsApiRoutes(WebContext):
    """`/api/v1/tools*`（列表/详情/配置/测试/执行记录/权限/决策调试）。"""

    # ---------------------------------------------------------------- helpers

    @property
    def _tools(self) -> ToolAdminService:
        service = getattr(self, "_tool_admin", None)
        if service is None:
            service = ToolAdminService(self._bot)
            self._tool_admin = service
        return service

    def _tool_has_credential(self, requires: list[str]) -> bool | None:
        """`has_credential`：无凭据要求时 null（不适用），否则全部就绪才为 true。"""
        if not requires:
            return None
        credentials = self._tools.runtime.credentials
        return all(credentials.has_secret(name) for name in requires)

    def _tool_row(
        self,
        row: dict[str, Any],
        metrics_by_tool: dict[str, Any],
        last_used: dict[str, float],
    ) -> dict[str, Any]:
        name = str(row.get("name", ""))
        stats = metrics_by_tool.get(name) or {}
        requires = [str(item) for item in (row.get("requires_credentials") or [])]
        return {
            "name": name,
            "display_name": row.get("display_name") or name,
            "description": row.get("description") or "",
            "category": row.get("category") or "",
            "risk_level": row.get("risk_level") or "",
            "enabled": bool(row.get("enabled")),
            "requires_credentials": requires,
            "has_credential": self._tool_has_credential(requires),
            "timeout": row.get("timeout"),
            "cache_ttl_seconds": row.get("cache_ttl_seconds"),
            "calls": int(stats.get("calls") or 0),
            "failures": int(stats.get("failure") or 0),
            "last_used_at": last_used.get(name),
        }

    async def _detail(self, name: str) -> dict[str, Any]:
        detail = await self._tools.tool_detail(name)
        if not detail:
            raise not_found(f"工具不存在：{name}", code="tools.unknown")
        metadata = dict(detail.get("metadata") or {})
        requires = [str(item) for item in (metadata.get("requires_credentials") or [])]
        rules = [row for row in await self._tools.permissions() if row.get("tool_name") == name]
        return {
            "name": metadata.get("name", name),
            "display_name": metadata.get("display_name") or name,
            "description": metadata.get("description") or "",
            "version": metadata.get("version") or "",
            "category": metadata.get("category") or "",
            "tags": list(metadata.get("tags") or []),
            "keywords": list(metadata.get("keywords") or []),
            "risk_level": metadata.get("risk_level") or "",
            "enabled": bool(metadata.get("enabled")),
            "requires_credentials": requires,
            "has_credential": self._tool_has_credential(requires),
            "timeout": metadata.get("timeout"),
            "cache_ttl_seconds": metadata.get("cache_ttl_seconds"),
            "input_schema": metadata.get("input_schema") or {},
            "output_schema": metadata.get("output_schema") or {},
            "when_to_use": metadata.get("when_to_use") or "",
            "when_not_to_use": metadata.get("when_not_to_use") or "",
            "limitations": metadata.get("limitations") or "",
            "settings": detail.get("settings") or {},
            "credentials": detail.get("credentials") or {},
            "metrics": detail.get("metrics") or {},
            "recent_executions": detail.get("executions") or [],
            "permissions_summary": {"count": len(rules), "rules": rules},
        }

    # ------------------------------------------------------------------ reads

    async def _v1_tools_list(self, request: web.Request) -> web.Response:
        service = self._tools
        dashboard = await service.dashboard()
        metrics = dashboard.get("metrics") or {}
        by_tool = metrics.get("by_tool") or {}
        last_used = await service.last_used()
        items = [self._tool_row(row, by_tool, last_used) for row in (dashboard.get("tools") or [])]
        policy = dashboard.get("policy")
        stats = {
            "enabled": dashboard.get("enabled"),
            "decision_mode": dashboard.get("decision_mode"),
            "total": dashboard.get("total"),
            "enabled_count": dashboard.get("enabled_count"),
            "disabled_count": dashboard.get("disabled_count"),
            "max_calls_per_turn": dashboard.get("max_calls_per_turn"),
            "calls": metrics.get("calls"),
            "success": metrics.get("success"),
            "failure": metrics.get("failure"),
            "cache_hits": metrics.get("cache_hits"),
        }
        return ok({"items": items, "policy": policy, "stats": stats}, request=request)

    async def _v1_tool_detail(self, request: web.Request) -> web.Response:
        name = str(request.match_info["name"])
        return ok(await self._detail(name), request=request)

    async def _v1_tool_executions(self, request: web.Request) -> web.Response:
        limit = read_query_int(request, "limit", default=100, minimum=1, maximum=500)
        name = request.query.get("name", "")
        rows = await self._tools.executions(limit=limit, tool_name=name)
        return ok({"items": rows, "total": len(rows)}, request=request)

    async def _v1_tool_metrics(self, request: web.Request) -> web.Response:
        return ok(await self._tools.metrics(), request=request)

    async def _v1_tool_permissions(self, request: web.Request) -> web.Response:
        rows = await self._tools.permissions()
        return ok({"items": rows, "total": len(rows)}, request=request)

    async def _v1_tool_decision_debug(self, request: web.Request) -> web.Response:
        text = request.query.get("text", "") or request.query.get("q", "")
        if not text.strip():
            raise bad_request("缺少 text 参数：需要一段文本才能预览工具选择", field="text")
        mode = request.query.get("mode", "candidates") or "candidates"
        data = await self._tools.decision_debug(text, mode=mode)
        return ok(data, request=request)

    # ----------------------------------------------------------------- writes

    async def _v1_tool_patch(self, request: web.Request) -> web.Response:
        name = str(request.match_info["name"])
        body = await read_json(request)
        unknown = sorted(set(body) - TOOL_PATCH_FIELDS)
        if unknown:
            raise bad_request(f"不支持的字段：{', '.join(unknown)}", code="tools.unknown_field")
        if not body:
            raise bad_request("没有要更新的字段", code="tools.nothing_to_update")
        if self._tools.runtime.registry.maybe_get(name) is None:
            raise not_found(f"工具不存在：{name}", code="tools.unknown")

        applied: list[str] = []
        if "enabled" in body:
            enabled = body["enabled"]
            if not isinstance(enabled, bool):
                raise bad_request("enabled 必须是布尔值", field="enabled")
            if not await self._tools.set_enabled(name, enabled):
                raise not_found(f"工具不存在：{name}", code="tools.unknown")
            applied.append("enabled")

        settings = body.get("settings")
        if settings is not None and not isinstance(settings, dict):
            raise bad_request("settings 必须是对象", field="settings")
        timeout = _optional_float(body, "timeout")
        cache_ttl = _optional_float(body, "cache_ttl_seconds")
        if settings is not None or timeout is not None or cache_ttl is not None:
            result = await self._tools.save_settings(
                name, settings=settings, timeout=timeout, cache_ttl_seconds=cache_ttl
            )
            if not result.get("ok"):
                raise not_found(f"工具不存在：{name}", code="tools.unknown")
            for key, value in (
                ("settings", settings),
                ("timeout", timeout),
                ("cache_ttl_seconds", cache_ttl),
            ):
                if value is not None:
                    applied.append(key)
        if not applied:
            raise bad_request("没有要更新的字段", code="tools.nothing_to_update")

        data = await self._detail(name)
        data["applied"] = applied
        # ToolRuntime 对以上字段全部热应用（v0.6 §73），所以这里是事实值 false
        data["restart_required"] = False
        return ok(data, request=request)

    async def _v1_tool_test(self, request: web.Request) -> web.Response:
        name = str(request.match_info["name"])
        body = await read_json(request, required=False)
        if str(body.get("confirm", "")) != name:
            raise conflict(
                f"工具测试可能真的发起外部请求，请带 confirm={name} 再试",
                code="tools.confirm_required",
            )
        if self._tools.runtime.registry.maybe_get(name) is None:
            raise not_found(f"工具不存在：{name}", code="tools.unknown")
        arguments = body.get("arguments") or {}
        if not isinstance(arguments, dict):
            raise bad_request("arguments 必须是 JSON 对象", field="arguments")
        result = await self._tools.test_tool(name, arguments)
        succeeded = bool(result.get("ok"))
        candidate = result.get("result")
        raw: dict[str, Any] = candidate if isinstance(candidate, dict) else {}
        return ok(
            {
                "ok": succeeded,
                "result": raw if succeeded else None,
                "error": None if succeeded else (raw.get("error") or "工具执行失败"),
                "duration_ms": result.get("latency_ms"),
                # admin 测试直接执行工具，可能真的访问外部服务（不会发 QQ 消息）
                "may_have_called_external": True,
                "note": result.get("note"),
            },
            request=request,
        )

    async def _v1_tool_permissions_put(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        scope = str(body.get("scope", ""))
        scope_id = str(body.get("scope_id", "") or body.get("ref", ""))
        tool = str(body.get("tool", ""))
        allowed = body.get("allowed")
        if scope not in PERMISSION_SCOPES:
            raise bad_request("scope 必须是 user 或 group", code="tools.scope_unknown")
        if not scope_id:
            raise bad_request("scope_id 不能为空", field="scope_id")
        if not tool:
            raise bad_request("tool 不能为空", field="tool")
        if not isinstance(allowed, bool):
            raise bad_request("allowed 必须是布尔值", field="allowed")
        if self._tools.runtime.registry.maybe_get(tool) is None:
            raise not_found(f"工具不存在：{tool}", code="tools.unknown")
        saved = await self._tools.set_permission(scope, scope_id, tool, allowed)
        if not saved:
            raise unavailable("工具权限存储不可用", code="tools.store_unavailable")
        return ok(
            {
                "saved": True,
                "scope": scope,
                "scope_id": scope_id,
                "tool": tool,
                "allowed": allowed,
            },
            request=request,
        )

    async def _v1_tool_permissions_delete(self, request: web.Request) -> web.Response:
        scope = request.query.get("scope", "")
        scope_id = request.query.get("scope_id", "") or request.query.get("ref", "")
        tool = request.query.get("tool", "")
        if scope not in PERMISSION_SCOPES:
            raise bad_request("scope 必须是 user 或 group", code="tools.scope_unknown")
        if not scope_id or not tool:
            raise bad_request("scope_id 与 tool 不能为空")
        cleared = await self._tools.clear_permission(scope, scope_id, tool)
        if not cleared:
            raise unavailable("工具权限存储不可用", code="tools.store_unavailable")
        return ok(
            {"cleared": True, "scope": scope, "scope_id": scope_id, "tool": tool},
            request=request,
        )

    async def _v1_tool_cache_clear(self, request: web.Request) -> web.Response:
        body = await read_json(request, required=False)
        if str(body.get("confirm", "")) != CACHE_CLEAR_CONFIRM:
            raise conflict(
                f"清空工具缓存需要 confirm={CACHE_CLEAR_CONFIRM}",
                code="tools.confirm_required",
            )
        name = str(body.get("name", "") or request.query.get("name", ""))
        if name and self._tools.runtime.registry.maybe_get(name) is None:
            raise not_found(f"工具不存在：{name}", code="tools.unknown")
        cleared = await self._tools.clear_cache(name)
        return ok({"cleared": cleared, "tool": name or None}, request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_tools(self, app: web.Application) -> None:
        wrap = json_endpoint
        # 静态路径必须先注册，否则会落在 /tools/{name} 上
        app.router.add_get(f"{API_PREFIX}/tools", wrap(self._v1_tools_list))
        app.router.add_get(f"{API_PREFIX}/tools/executions", wrap(self._v1_tool_executions))
        app.router.add_get(f"{API_PREFIX}/tools/metrics", wrap(self._v1_tool_metrics))
        app.router.add_get(f"{API_PREFIX}/tools/permissions", wrap(self._v1_tool_permissions))
        app.router.add_put(f"{API_PREFIX}/tools/permissions", wrap(self._v1_tool_permissions_put))
        app.router.add_delete(
            f"{API_PREFIX}/tools/permissions", wrap(self._v1_tool_permissions_delete)
        )
        app.router.add_post(f"{API_PREFIX}/tools/cache/clear", wrap(self._v1_tool_cache_clear))
        app.router.add_get(f"{API_PREFIX}/tools/decision-debug", wrap(self._v1_tool_decision_debug))
        app.router.add_get(f"{API_PREFIX}/tools/{{name}}", wrap(self._v1_tool_detail))
        app.router.add_patch(f"{API_PREFIX}/tools/{{name}}", wrap(self._v1_tool_patch))
        app.router.add_post(f"{API_PREFIX}/tools/{{name}}/test", wrap(self._v1_tool_test))


def _optional_float(body: dict[str, Any], key: str) -> float | None:
    if key not in body or body[key] is None:
        return None
    try:
        return float(body[key])
    except (TypeError, ValueError) as exc:
        raise bad_request(f"{key} 必须是数字", field=key) from exc
