"""`/api/v1/tools*`（WebUI v1.0 · W5 契约 §7.4）。

真实 HTTP + 真实中间件（会话 Cookie / CSRF）；工具服务是既有 ``ToolAdminService``，
测试只通过 HTTP 面证明：形状、404、确认门（409）、热更新与测试告警位。

``WebServer`` 目前由父任务把 W5 mixin 合入 ``ApiRoutes``；在合入前测试用一个
显式组合的子类挂载 ``ToolsApiRoutes``，两种状态下行为一致。
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.web.api.tools_api import ToolsApiRoutes
from app.web.server import WebServer
from tests.api_harness import ApiClient, error_code, free_port
from tests.conftest import BIBLE_PATH
from tests.test_web_realtime import DummyAdapter


class V1Server(WebServer, ToolsApiRoutes):
    """W5 路由组合面（父任务合入 ApiRoutes 后本子类仍兼容）。"""


@asynccontextmanager
async def v1_server(tmp_path, *, config_overrides: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    port = free_port()
    sections: dict[str, Any] = {
        "bot": {"name": "TestBot"},
        "database": {"url": "sqlite:///" + str(tmp_path / "api.db")},
        "logging": {"log_dir": str(tmp_path / "logs"), "level": "WARNING"},
        "web": {
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "username": "admin",
            "password": "pw123",
        },
        "sandbox": {"enabled": True, "bible_path": str(BIBLE_PATH)},
    }
    sections.update(config_overrides or {})
    config = AppConfig(**sections)
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    await bot.tools.start()
    server = V1Server(config.web, bot)
    server._config_admin.overrides_path = tmp_path / "overrides.yaml"
    await server.start()
    client = ApiClient(f"http://127.0.0.1:{port}")
    try:
        yield client, bot, server
    finally:
        await client.close()
        await server.stop()
        await bot.shutdown()


async def _login(client: ApiClient) -> None:
    status, payload = await client.login()
    assert status == 200, payload


class TestAuthAndList:
    async def test_anonymous_tools_is_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/tools")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"

    async def test_missing_csrf_on_patch_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch(
                "/api/v1/tools/calculator", body={"enabled": False}, csrf=False
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"

    async def test_tools_list_shape_and_rows(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools")
            assert status == 200
            data = payload["data"]
            assert set(data) == {"items", "policy", "stats"}
            names = {row["name"] for row in data["items"]}
            assert {"calculator", "time"} <= names
            assert {"allowed_risk_levels", "rate_limit"} <= set(data["policy"])
            assert {"enabled", "total", "enabled_count", "calls", "failure"} <= set(data["stats"])
            calculator = next(row for row in data["items"] if row["name"] == "calculator")
            assert set(calculator) == {
                "name",
                "display_name",
                "description",
                "category",
                "risk_level",
                "enabled",
                "requires_credentials",
                "has_credential",
                "timeout",
                "cache_ttl_seconds",
                "calls",
                "failures",
                "last_used_at",
            }
            assert calculator["has_credential"] is None  # 无需凭据 → null，不编造
            weather = next(row for row in data["items"] if row["name"] == "weather")
            # 内置工具都没有声明 requires_credentials（凭据走 settings.api_key_env）
            assert weather["requires_credentials"] == []
            assert weather["has_credential"] is None


class TestToolDetail:
    async def test_detail_carries_docs_schemas_and_metrics(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/calculator")
            assert status == 200
            data = payload["data"]
            assert {
                "input_schema",
                "output_schema",
                "when_to_use",
                "when_not_to_use",
                "limitations",
                "settings",
                "metrics",
                "recent_executions",
                "permissions_summary",
            } <= set(data)
            assert data["input_schema"].get("properties")
            assert data["enabled"] is True
            assert data["permissions_summary"] == {"count": 0, "rules": []}

    async def test_detail_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/nope")
            assert status == 404
            assert error_code(payload) == "tools.unknown"


class TestToolPatch:
    async def test_patch_unknown_tool_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch("/api/v1/tools/nope", body={"enabled": False})
            assert status == 404
            assert error_code(payload) == "tools.unknown"

    async def test_patch_unknown_field_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch("/api/v1/tools/calculator", body={"foo": 1})
            assert status == 400
            assert error_code(payload) == "tools.unknown_field"

    async def test_patch_bad_enabled_type_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch(
                "/api/v1/tools/calculator", body={"enabled": "yes"}
            )
            assert status == 400
            assert payload["error"]["field"] == "enabled"

    async def test_patch_enabled_hot_applies_without_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch(
                "/api/v1/tools/calculator", body={"enabled": False}
            )
            assert status == 200
            assert payload["data"]["enabled"] is False
            assert payload["data"]["restart_required"] is False
            assert payload["data"]["applied"] == ["enabled"]
            _status, again = await client.get("/api/v1/tools/calculator")
            assert again["data"]["enabled"] is False

    async def test_patch_timeout_and_settings_are_applied(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch(
                "/api/v1/tools/weather",
                body={"timeout": 4.5, "cache_ttl_seconds": 60, "settings": {"max_results": 3}},
            )
            assert status == 200
            data = payload["data"]
            assert data["timeout"] == 4.5
            assert data["cache_ttl_seconds"] == 60
            assert data["settings"]["max_results"] == 3
            assert set(data["applied"]) == {"timeout", "cache_ttl_seconds", "settings"}
            assert data["restart_required"] is False

    async def test_patch_timeout_bad_type_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch(
                "/api/v1/tools/calculator", body={"timeout": "soon"}
            )
            assert status == 400
            assert payload["error"]["field"] == "timeout"

    async def test_patch_empty_body_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.patch("/api/v1/tools/calculator", body={})
            assert status == 400
            assert error_code(payload) == "tools.nothing_to_update"
            status, payload = await client.patch(
                "/api/v1/tools/calculator", body={"settings": None}
            )
            assert status == 400
            assert error_code(payload) == "tools.nothing_to_update"


class TestToolTestGate:
    async def test_test_without_confirm_is_409(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/calculator/test", body={"arguments": {"expression": "1+1"}}
            )
            assert status == 409
            assert error_code(payload) == "tools.confirm_required"

    async def test_test_with_wrong_confirm_is_409(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/calculator/test",
                body={"confirm": "time", "arguments": {"expression": "1+1"}},
            )
            assert status == 409
            assert error_code(payload) == "tools.confirm_required"

    async def test_test_unknown_tool_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/nope/test", body={"confirm": "nope", "arguments": {}}
            )
            assert status == 404
            assert error_code(payload) == "tools.unknown"

    async def test_test_bad_arguments_type_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/calculator/test",
                body={"confirm": "calculator", "arguments": ["1+1"]},
            )
            assert status == 400
            assert payload["error"]["field"] == "arguments"

    async def test_test_runs_and_warns_about_external_calls(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/calculator/test",
                body={"confirm": "calculator", "arguments": {"expression": "6*7"}},
            )
            assert status == 200
            data = payload["data"]
            assert data["ok"] is True
            assert data["result"]["data"]["result"] == 42
            assert data["may_have_called_external"] is True
            assert isinstance(data["duration_ms"], (int, float))
            assert data["error"] is None

    async def test_failed_test_returns_error_not_ok(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/calculator/test",
                body={"confirm": "calculator", "arguments": {"action": "evaluate"}},
            )
            assert status == 200
            data = payload["data"]
            assert data["ok"] is False
            assert data["result"] is None
            assert data["error"]


class TestExecutionsAndMetrics:
    async def test_executions_include_the_test_run(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            await client.post(
                "/api/v1/tools/calculator/test",
                body={"confirm": "calculator", "arguments": {"expression": "2+2"}},
            )
            status, payload = await client.get("/api/v1/tools/executions?name=calculator")
            assert status == 200
            assert set(payload["data"]) == {"items", "total"}
            assert payload["data"]["total"] >= 1
            row = payload["data"]["items"][0]
            assert row["tool_name"] == "calculator"
            assert "arguments_preview" not in row  # 永不回显原始参数

    async def test_executions_limit_is_validated(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/executions?limit=9999")
            assert status == 400

    async def test_metrics_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/metrics")
            assert status == 200
            data = payload["data"]
            assert {"calls", "success", "failure", "by_tool", "avg_latency_ms"} <= set(data)


class TestPermissions:
    async def test_permission_crud_roundtrip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.put(
                "/api/v1/tools/permissions",
                body={"scope": "user", "scope_id": "10001", "tool": "calculator", "allowed": False},
            )
            assert status == 200
            assert payload["data"]["saved"] is True

            _status, listing = await client.get("/api/v1/tools/permissions")
            rows = listing["data"]["items"]
            assert any(
                row["scope"] == "user"
                and row["ref"] == "10001"
                and row["tool_name"] == "calculator"
                for row in rows
            )

            status, payload = await client.delete(
                "/api/v1/tools/permissions?scope=user&scope_id=10001&tool=calculator"
            )
            assert status == 200
            assert payload["data"]["cleared"] is True
            _status, listing = await client.get("/api/v1/tools/permissions")
            assert listing["data"]["total"] == 0

    async def test_permission_bad_scope_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.put(
                "/api/v1/tools/permissions",
                body={"scope": "galaxy", "scope_id": "1", "tool": "calculator", "allowed": True},
            )
            assert status == 400
            assert error_code(payload) == "tools.scope_unknown"

    async def test_permission_unknown_tool_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.put(
                "/api/v1/tools/permissions",
                body={"scope": "group", "scope_id": "1", "tool": "nope", "allowed": True},
            )
            assert status == 404
            assert error_code(payload) == "tools.unknown"

    async def test_permission_delete_needs_params(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.delete("/api/v1/tools/permissions?scope=user")
            assert status == 400


class TestCacheAndDecisionDebug:
    async def test_cache_clear_needs_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post("/api/v1/tools/cache/clear", body={})
            assert status == 409
            assert error_code(payload) == "tools.confirm_required"

    async def test_cache_clear_with_confirm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.post(
                "/api/v1/tools/cache/clear", body={"confirm": "clear"}
            )
            assert status == 200
            assert isinstance(payload["data"]["cleared"], int)

    async def test_decision_debug_reads_only(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/decision-debug?text=帮我算一下 12*34")
            assert status == 200
            data = payload["data"]
            assert {"query", "candidates", "rejected", "selected"} <= set(data)
            assert data["query"] == "帮我算一下 12*34"

    async def test_decision_debug_without_text_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/tools/decision-debug")
            assert status == 400
            assert payload["error"]["field"] == "text"
