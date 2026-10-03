"""`/api/v1` 会话 / CSRF / 配置中心（WebUI v1.0 · W2 契约 §2-§4、§10）。

测试通过真实 HTTP + 真实中间件跑：匿名、无 CSRF、错 CSRF、正确 CSRF、
热更新、需要重启、敏感键、预览不落盘——每一条都是契约里的验收项。
"""

from __future__ import annotations

from typing import Any

from tests.api_harness import api_server, error_code


def keys(payload: dict[str, Any]) -> list[str]:
    return [row["key"] for row in payload["data"]["items"]]


class TestSessionAndCsrf:
    async def test_anonymous_read_is_401_with_the_envelope(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/config/effective")
            assert status == 401
            assert payload["ok"] is False
            assert error_code(payload) == "auth.unauthorized"
            assert payload["meta"]["request_id"]

    async def test_login_returns_csrf_and_meta_works(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.login()
            assert status == 200
            assert payload["data"]["csrf_token"] == client.csrf
            status, meta = await client.get("/api/v1/meta")
            assert status == 200
            assert meta["data"]["api"] == "v1"
            assert meta["data"]["core_behavior_phase"] == "P16"

    async def test_bad_credentials_are_401(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.login(password="wrong-pass")
            assert status == 401
            assert error_code(payload) == "auth.unauthorized"

    async def test_csrf_bootstrap_matches_the_session_token(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/csrf")
            assert status == 200
            assert payload["data"]["csrf_token"] == client.csrf
            assert payload["data"]["header"] == "X-CSRF-Token"

    async def test_write_without_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"bot.name": "X"}}, csrf=False
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"

    async def test_write_with_wrong_csrf_is_403(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config",
                body={"values": {"bot.name": "X"}},
                headers={"X-CSRF-Token": "not-the-token"},
            )
            assert status == 403
            assert error_code(payload) == "auth.csrf"

    async def test_logout_invalidates_the_session(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, _ = await client.delete("/api/v1/session")
            assert status == 200
            status, payload = await client.get("/api/v1/config/effective")
            assert status == 401


class TestConfigSchema:
    async def test_schema_returns_labels_types_and_constraints(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/config/schema")
            assert status == 200
            items = payload["data"]["items"]
            assert len(items) > 100
            sample = next(row for row in items if row["key"] == "runtime.tick_interval_seconds")
            assert sample["label"]
            assert sample["type"] == "float"
            assert sample["constraints"]["min"] > 0
            assert sample["hot_reload"] is False
            assert sample["restart_required"] is True
            assert sample["area"] == "运行"

    async def test_unused_keys_are_hidden_but_queryable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            _status, normal = await client.get("/api/v1/config/schema")
            assert "expression.learn_max_per_hour" not in keys(normal)
            _status, expert = await client.get("/api/v1/config/schema?include=unused")
            row = next(
                row
                for row in expert["data"]["items"]
                if row["key"] == "expression.learn_max_per_hour"
            )
            assert row["usage_status"] == "DEFINED_BUT_UNUSED"
            assert row["hidden"] is True

    async def test_legend_explains_status_and_sources(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            _status, payload = await client.get("/api/v1/config/schema")
            legends = payload["data"]["legends"]
            assert "DEFINED_BUT_UNUSED" in legends["usage_status"]
            assert legends["source"]["models"]

    async def test_secret_fields_never_carry_a_value(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, server):  # type: ignore[no-untyped-def]
            await client.login()
            server._config_admin.set_env_secret(
                "CATOOBOT_ONEBOT_ACCESS_TOKEN", "super-secret-token"
            )
            status, payload = await client.get("/api/v1/config/effective?keys=onebot.access_token")
            assert status == 200
            row = payload["data"]["items"][0]
            assert "value" not in row
            assert row["sensitive"] is True
            assert row["configured"] is True
            assert "super-secret-token" not in str(payload)


class TestEffectiveConfig:
    async def test_default_source_is_reported(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get(
                "/api/v1/config/effective?keys=behavior.reply.min_delay"
            )
            assert status == 200
            row = payload["data"]["items"][0]
            assert row["value"] == 0.8
            assert row["source"] in ("default", "yaml")

    async def test_wildcard_keys_expand_to_real_instances(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        overrides = {
            "ai": {
                "providers": {"mock": {"base_url": "http://127.0.0.1:9/v1", "api_key_env": ""}},
                "models": [{"name": "m1", "provider": "mock", "model": "m1"}],
            }
        }
        async with api_server(tmp_path, config_overrides=overrides) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/config/effective?keys=ai.models[i].name")
            assert status == 200
            rows = payload["data"]["items"]
            assert rows, "the models section must expand into concrete instances"
            assert all("[" in row["key"] for row in rows)
            assert rows[0]["value"] == "m1"

    async def test_override_reports_overrides_as_source(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"behavior.reply.min_delay": 1.5}}
            )
            assert status == 200
            row = next(
                item
                for item in payload["data"]["values"]
                if item["key"] == "behavior.reply.min_delay"
            )
            assert row["source"] == "overrides"
            assert row["value"] == 1.5


class TestHyphenatedKeys:
    async def test_a_hyphenated_provider_name_resolves_in_effective_config(self, tmp_path) -> None:
        """W6：Provider 名允许连字符（W2 校验规则），点号键解析必须同样接受它。"""
        overrides = {
            "ai": {
                "providers": {
                    "my-provider": {"base_url": "http://127.0.0.1:9/v1", "api_key_env": ""}
                },
                "models": [{"name": "m1", "provider": "my-provider", "model": "m1"}],
            }
        }
        async with api_server(tmp_path, config_overrides=overrides) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get(
                "/api/v1/config/effective?keys=ai.providers.my-provider.base_url"
            )
            assert status == 200
            row = payload["data"]["items"][0]
            assert row["key"] == "ai.providers.my-provider.base_url"
            assert row["value"] == "http://127.0.0.1:9/v1"


class TestConfigApply:
    async def test_hot_reload_really_applies(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, _server):  # type: ignore[no-untyped-def]
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"behavior.reply.min_delay": 2.5}}
            )
            assert status == 200
            data = payload["data"]
            assert data["hot_reload"] == ["behavior.reply.min_delay"]
            assert data["restart_required"] == []
            assert data["effective"] == [True]
            assert bot.config.behavior.reply.min_delay == 2.5

    async def test_restart_required_is_not_fake_hot_reload(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, server):  # type: ignore[no-untyped-def]
            await client.login()
            before = bot.runtime_scheduler.interval if bot.runtime_scheduler else None
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"runtime.tick_interval_seconds": 2.0}}
            )
            assert status == 200
            data = payload["data"]
            assert data["restart_required"] == ["runtime.tick_interval_seconds"]
            assert data["effective"] == [False]
            assert data["warnings"][0]["code"] == "config.restart_required"
            # the running scheduler still holds its construction-time interval
            after = bot.runtime_scheduler.interval if bot.runtime_scheduler else None
            assert after == before
            _status, pending = await client.get("/api/v1/config/restart-pending")
            assert pending["data"]["pending"] == ["runtime.tick_interval_seconds"]

    async def test_unknown_key_is_400(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"ai.temperature.fake": 1}}
            )
            assert status == 400
            assert error_code(payload) == "config.unknown_key"

    async def test_sensitive_key_must_use_credentials(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"onebot.access_token": "sk-nope"}}
            )
            assert status == 400
            assert error_code(payload) == "config.readonly_key"
            assert "sk-nope" not in str(payload)

    async def test_invalid_value_is_422(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.patch(
                "/api/v1/config", body={"values": {"behavior.chunking.max_chunks": -3.5}}
            )
            assert status == 422
            assert payload["ok"] is False

    async def test_validate_previews_without_writing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, bot, server):  # type: ignore[no-untyped-def]
            await client.login()
            status, payload = await client.post(
                "/api/v1/config/validate",
                body={
                    "values": {
                        "behavior.reply.max_delay": 9.0,
                        "runtime.tick_interval_seconds": 2.0,
                    }
                },
            )
            assert status == 200
            assert payload["data"]["restart_required"] == ["runtime.tick_interval_seconds"]
            assert bot.config.behavior.reply.max_delay != 9.0
            assert not server._config_admin.overrides_path.exists()

    async def test_reset_needs_confirmation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/config/reset", body={})
            assert status == 409
            assert error_code(payload) == "config.confirm_required"

    async def test_raw_yaml_roundtrip_keeps_the_written_values(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, _payload = await client.put(
                "/api/v1/config/raw",
                body={"yaml": "bot:\n  name: RawName\n", "confirm": "raw"},
            )
            assert status == 200
            _status, payload = await client.get("/api/v1/config/raw")
            assert "RawName" in payload["data"]["yaml"]


class TestOldUiRegression:
    async def test_ssr_pages_still_render(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with api_server(tmp_path) as (client, _bot, _server):
            status, html = await client.get("/", raw=True)
            assert status in (200, 302)
            assert isinstance(html, str)
            status, _ = await client.post("/login", body={"username": "admin", "password": "pw123"})
            assert status in (200, 302)
