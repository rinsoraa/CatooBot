"""`/api/v1/ai/*` 与 `/api/v1/credentials/*`（WebUI v1.0 · W2 契约 §5-§6、§10-§11）。

测试用真实 HTTP + 真实中间件跑：Provider/模型/角色的写入真的落在
`bot.config` 与 `overrides.yaml`，凭据写入真的落在一个临时 `.env`
（绝不碰项目真实 `.env`），路由器用注册的假 Provider 验证"新配置真的生效"。
"""

from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider, register_provider_type
from app.config import env_store
from app.config.settings import read_overrides
from app.web.api.ai import AiApiRoutes
from app.web.api.credentials import CredentialApiRoutes
from app.web.server import WebServer
from app.web.services.ai_admin import ROLE_ORDER
from tests.api_harness import ApiClient, api_server, error_code

TEST_KEY = "CATOOBOT_TEST_KEY_A"
TEST_KEY_BOOM = "CATOOBOT_TEST_KEY_BOOM"
ONEBOT_TOKEN = "CATOOBOT_ONEBOT_ACCESS_TOKEN"
#: written into os.environ by tests / the API; always cleaned up
_ENV_NAMES = (TEST_KEY, TEST_KEY_BOOM, ONEBOT_TOKEN)


class FakeProvider(AIProvider):
    """Offline provider: returns "pong", or fails when the key says ``sk-boom``."""

    def __init__(self, **kwargs: Any) -> None:
        self.name = str(kwargs.get("name", "fake"))
        self.base_url = str(kwargs.get("base_url", ""))
        self._api_key = str(kwargs.get("api_key", ""))
        self.closed = False

    async def chat(self, request: AIRequest) -> AIResponse:
        if self._api_key == "sk-boom":
            raise RuntimeError("synthetic provider failure")
        return AIResponse(content="pong", model=request.model or "", provider=self.name)

    async def close(self) -> None:
        self.closed = True


register_provider_type("fake_test", FakeProvider)


#: 集成后 WebServer 自带这两个 mixin；别名保留，测试仍走真实中间件。
AiTestServer = WebServer


@asynccontextmanager
async def ai_server(
    tmp_path: Path, *, config_overrides: dict[str, Any] | None = None
) -> AsyncIterator[tuple[ApiClient, Any, Any]]:
    with mock.patch("tests.api_harness.WebServer", AiTestServer):
        async with api_server(tmp_path, config_overrides=config_overrides) as bundle:
            yield bundle


def fake_ai_block(
    *,
    env_name: str = TEST_KEY,
    models: list[dict[str, Any]] | None = None,
    provider: str = "fakep",
) -> dict[str, Any]:
    return {
        "ai": {
            "providers": {
                provider: {
                    "type": "fake_test",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key_env": env_name,
                }
            },
            "models": models
            if models is not None
            else [{"name": "fast", "provider": provider, "model": "fake-model"}],
        }
    }


def two_models(provider: str = "fakep") -> list[dict[str, Any]]:
    return [
        {"name": "m1", "provider": provider, "model": "id1"},
        {"name": "m2", "provider": provider, "model": "id2"},
    ]


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # load_config() calls load_dotenv(): the project's real .env (with the
    # operator's CATOOBOT_WEB_PASSWORD / API keys) must never leak into tests.
    monkeypatch.setattr("app.config.settings.load_dotenv", lambda *args, **kwargs: None)
    # 默认就把凭据指向临时文件；真实 .env / data/secrets.json 绝不参与测试。
    monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr("app.tools.credentials.SECRETS_FILE", tmp_path / "secrets.json")
    for name in _ENV_NAMES:
        os.environ.pop(name, None)
    yield
    for name in _ENV_NAMES:
        os.environ.pop(name, None)


class TestRouteRegistration:
    def test_mixins_expose_registration_hooks(self) -> None:
        assert hasattr(AiApiRoutes, "_register_v1_ai")
        assert hasattr(CredentialApiRoutes, "_register_v1_credentials")
        assert issubclass(AiTestServer, WebServer)


class TestAiProviders:
    async def test_create_provider_and_list_it(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.put(
                "/api/v1/ai/providers/httpai",
                body={
                    "type": "openai_compatible",
                    "base_url": "http://localhost:7864/v1",
                    "api_key_env": TEST_KEY,
                },
            )
            assert status == 200
            item = payload["data"]
            assert item["name"] == "httpai"
            assert item["type"] == "openai_compatible"
            assert item["has_key"] is False
            assert item["restart_required"] is False
            status, listing = await client.get("/api/v1/ai/providers")
            assert status == 200
            assert [row["name"] for row in listing["data"]["items"]] == ["httpai"]

    async def test_provider_upsert_updates_in_place(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            await client.put(
                "/api/v1/ai/providers/p",
                body={"type": "fake_test", "base_url": "http://127.0.0.1:9/v1", "api_key_env": ""},
            )
            status, payload = await client.put(
                "/api/v1/ai/providers/p",
                body={
                    "type": "fake_test",
                    "base_url": "http://localhost:9999/v1",
                    "api_key_env": TEST_KEY,
                },
            )
            assert status == 200
            assert payload["data"]["base_url"] == "http://localhost:9999/v1"
            _status, listing = await client.get("/api/v1/ai/providers")
            assert len(listing["data"]["items"]) == 1

    async def test_provider_invalid_name_type_and_url(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            base = {"type": "fake_test", "base_url": "http://127.0.0.1:9/v1", "api_key_env": ""}
            status, payload = await client.put("/api/v1/ai/providers/bad.name", body=base)
            assert status == 400 and error_code(payload) == "ai.invalid_name"
            status, payload = await client.put(
                "/api/v1/ai/providers/ok", body={**base, "type": "nope"}
            )
            assert status == 400 and error_code(payload) == "ai.provider_unknown"
            status, payload = await client.put(
                "/api/v1/ai/providers/ok", body={**base, "base_url": "ftp://host/v1"}
            )
            assert status == 400 and error_code(payload) == "config.invalid_value"

    async def test_delete_unknown_provider_is_404(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.delete("/api/v1/ai/providers/ghost")
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.delete(
                "/api/v1/ai/providers/ghost", body={"confirm": "ghost"}
            )
            assert status == 404 and error_code(payload) == "ai.provider_unknown"

    async def test_delete_base_defined_provider_is_conflict(self, tmp_path: Path) -> None:
        overrides = {"ai": {"providers": {"basep": {"base_url": "http://x.test/v1"}}, "models": []}}
        async with ai_server(tmp_path, config_overrides=overrides) as (client, _bot, _server):
            await client.login()
            status, payload = await client.delete(
                "/api/v1/ai/providers/basep", body={"confirm": "basep"}
            )
            assert status == 409
            assert error_code(payload) == "config.base_defined"
            assert "config.yaml" in payload["error"]["message"]

    async def test_delete_used_provider_needs_force(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            await client.put(
                "/api/v1/ai/providers/inuse",
                body={"type": "fake_test", "base_url": "http://127.0.0.1:9/v1", "api_key_env": ""},
            )
            await client.put(
                "/api/v1/ai/models/inuse-model", body={"provider": "inuse", "model": "m"}
            )
            status, payload = await client.delete("/api/v1/ai/providers/inuse")
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.delete(
                "/api/v1/ai/providers/inuse?force=1", body={"confirm": "inuse"}
            )
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.delete(
                "/api/v1/ai/providers/inuse", body={"confirm": "inuse"}
            )
            assert status == 409 and error_code(payload) == "ai.provider_in_use"
            status, payload = await client.delete(
                "/api/v1/ai/providers/inuse?force=1", body={"confirm": "force"}
            )
            assert status == 200 and payload["data"]["models_removed"] == 1
            _status, models = await client.get("/api/v1/ai/models")
            assert all(row["name"] != "inuse-model" for row in models["data"]["items"])


class TestAiModels:
    async def test_create_model_and_read_back(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path, config_overrides=fake_ai_block(models=[])) as (
            client,
            _bot,
            _server,
        ):
            await client.login()
            status, payload = await client.put(
                "/api/v1/ai/models/fast", body={"provider": "fakep", "model": "fake-model"}
            )
            assert status == 200
            assert payload["data"]["enabled"] is True
            _status, listing = await client.get("/api/v1/ai/models")
            rows = listing["data"]["items"]
            assert [row["name"] for row in rows] == ["fast"]
            assert rows[0]["order"] == 0
            assert rows[0]["roles"] == ["chat"]  # order[0] 是默认聊天模型
            assert rows[0]["usage"]["calls"] == 0

    async def test_create_model_unknown_provider_is_400(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.put(
                "/api/v1/ai/models/fast", body={"provider": "ghost", "model": "m"}
            )
            assert status == 400 and error_code(payload) == "ai.provider_unknown"

    async def test_update_model_toggles_enabled(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, bot, _s):
            await client.login()
            status, _payload = await client.put("/api/v1/ai/models/fast", body={"enabled": False})
            assert status == 200
            assert bot.config.ai.models[0].enabled is False
            _status, listing = await client.get("/api/v1/ai/models")
            assert listing["data"]["items"][0]["enabled"] is False

    async def test_unknown_model_is_404(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, _bot, _s):
            await client.login()
            status, payload = await client.delete("/api/v1/ai/models/ghost")
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.delete(
                "/api/v1/ai/models/ghost", body={"confirm": "ghost"}
            )
            assert status == 404 and error_code(payload) == "ai.model_unknown"
            status, payload = await client.post("/api/v1/ai/models/ghost/test", body={})
            assert status == 404 and error_code(payload) == "ai.model_unknown"

    async def test_reorder_models_persists(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(
            models=two_models() + [{"name": "m3", "provider": "fakep", "model": "id3"}]
        )
        async with ai_server(tmp_path, config_overrides=overrides) as (client, bot, server):
            await client.login()
            status, payload = await client.put(
                "/api/v1/ai/models/order", body={"order": ["m3", "m1", "m2"]}
            )
            assert status == 200 and payload["data"]["order"] == ["m3", "m1", "m2"]
            assert [m.name for m in bot.config.ai.models] == ["m3", "m1", "m2"]
            stored = read_overrides(server._config_admin.overrides_path)
            assert [m["name"] for m in stored["ai"]["models"]] == ["m3", "m1", "m2"]
            _status, listing = await client.get("/api/v1/ai/models")
            assert [row["order"] for row in listing["data"]["items"]] == [0, 1, 2]

    async def test_reorder_rejects_non_permutation(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, _bot, _s):
            await client.login()
            status, payload = await client.put("/api/v1/ai/models/order", body={"order": ["m1"]})
            assert status == 400 and error_code(payload) == "ai.invalid_order"

    async def test_delete_model_pinned_to_role_needs_force(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, bot, _server):
            await client.login()
            await client.put("/api/v1/ai/roles/vision", body={"model": "m1"})
            status, payload = await client.delete("/api/v1/ai/models/m1")
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.delete("/api/v1/ai/models/m1", body={"confirm": "m1"})
            assert status == 409 and error_code(payload) == "ai.model_in_use"
            assert "vision" in payload["error"]["detail"]["roles"]
            status, payload = await client.delete(
                "/api/v1/ai/models/m1?force=1", body={"confirm": "m1"}
            )
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, _payload = await client.delete(
                "/api/v1/ai/models/m1?force=1", body={"confirm": "force"}
            )
            assert status == 200
            assert [m.name for m in bot.config.ai.models] == ["m2"]

    async def test_model_test_success(self, tmp_path: Path) -> None:
        os.environ[TEST_KEY] = "sk-ok-123456"
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, bot, _s):
            await client.login()
            status, _ = await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
            assert status == 200 and "fast" in bot.ai.router.states
            status, payload = await client.post("/api/v1/ai/models/fast/test", body={})
            assert status == 200
            data = payload["data"]
            assert data["ok"] is True and data["model"] == "fast"
            assert data["error_type"] == "" and "pong" in data["response"]
            assert data["http_status"] == 200 and data["http_status_source"] == "upstream"
            assert "sk-ok-123456" not in json.dumps(payload)

    async def test_model_test_reports_failure(self, tmp_path: Path) -> None:
        os.environ[TEST_KEY_BOOM] = "sk-boom"
        block = fake_ai_block(env_name=TEST_KEY_BOOM)
        async with ai_server(tmp_path, config_overrides=block) as (client, _bot, _s):
            await client.login()
            await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
            status, payload = await client.post("/api/v1/ai/models/fast/test", body={})
            assert status == 200
            assert payload["data"]["ok"] is False
            assert payload["data"]["error_type"] == "RuntimeError"
            assert "sk-boom" not in json.dumps(payload)


class TestAiRoles:
    async def test_roles_lists_all_nine(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, _bot, _s):
            await client.login()
            status, payload = await client.get("/api/v1/ai/roles")
            assert status == 200
            rows = payload["data"]["items"]
            assert [row["role"] for row in rows] == list(ROLE_ORDER)
            chat = rows[0]
            assert chat["key"] == "ai.models[0].name" and chat["model"] == "m1"
            vision = next(row for row in rows if row["role"] == "vision")
            assert vision["key"] == "media.vision_model" and vision["model"] == ""

    async def test_set_vision_role_and_clear(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, bot, _s):
            await client.login()
            status, payload = await client.put("/api/v1/ai/roles/vision", body={"model": "m1"})
            assert status == 200
            assert payload["data"]["restart_required"] is True  # media.* 是重启类
            assert bot.config.media.vision_model == "m1"
            status, payload = await client.put("/api/v1/ai/roles/vision", body={"model": ""})
            assert status == 200 and payload["data"]["model"] == ""
            assert bot.config.media.vision_model == ""

    async def test_set_extraction_role_is_hot(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, bot, _s):
            await client.login()
            status, payload = await client.put("/api/v1/ai/roles/extraction", body={"model": "m2"})
            assert status == 200
            assert payload["data"]["restart_required"] is False
            assert bot.config.memory.extraction.model == "m2"

    async def test_set_chat_role_reorders_router(self, tmp_path: Path) -> None:
        os.environ[TEST_KEY] = "sk-ok-123456"
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, bot, _s):
            await client.login()
            await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
            assert list(bot.ai.router.states) == ["m1", "m2"]
            status, payload = await client.put("/api/v1/ai/roles/chat", body={"model": "m2"})
            assert status == 200 and payload["data"]["restart_required"] is False
            assert [m.name for m in bot.config.ai.models] == ["m2", "m1"]
            assert list(bot.ai.router.states) == ["m2", "m1"]

    async def test_unknown_role_and_unknown_model(self, tmp_path: Path) -> None:
        overrides = fake_ai_block(models=two_models())
        async with ai_server(tmp_path, config_overrides=overrides) as (client, _bot, _s):
            await client.login()
            status, payload = await client.put("/api/v1/ai/roles/nope", body={"model": "m1"})
            assert status == 404 and error_code(payload) == "ai.role_unknown"
            status, payload = await client.put("/api/v1/ai/roles/vision", body={"model": "ghost"})
            assert status == 404 and error_code(payload) == "ai.model_unknown"


class TestAiUsage:
    async def test_usage_aggregation_by_model_and_provider(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, bot, _server):
            assert bot.ai_usage is not None
            await bot.ai_usage.record(
                provider="p", model="m1", latency_ms=100.0, ok=True, usage={"total_tokens": 10}
            )
            await bot.ai_usage.record(
                provider="p", model="m1", latency_ms=50.0, ok=False, error_type="RateLimitError"
            )
            await bot.ai_usage.record(
                provider="p", model="m2", latency_ms=70.0, ok=False, error_type="ServerError"
            )
            await client.login()
            status, payload = await client.get("/api/v1/ai/usage?days=7&group_by=model")
            assert status == 200
            rows = {row["key"]: row for row in payload["data"]["items"]}
            assert rows["m1"]["calls"] == 2 and rows["m1"]["failures"] == 1
            assert rows["m1"]["rate_limited"] == 1 and rows["m1"]["tokens"] == 10
            assert rows["m1"]["max_latency_ms"] == 100.0
            assert rows["m2"]["server_errors"] == 1
            _status, by_provider = await client.get("/api/v1/ai/usage?group_by=provider")
            assert by_provider["data"]["items"][0]["calls"] == 3

    async def test_usage_invalid_group_by_is_400(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/ai/usage?group_by=secret")
            assert status == 400 and error_code(payload) == "ai.invalid_value"

    async def test_usage_appears_in_the_model_list(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, bot, _s):
            assert bot.ai_usage is not None
            await bot.ai_usage.record(
                provider="fakep",
                model="fake-model",
                latency_ms=12.0,
                ok=True,
                usage={"total_tokens": 7},
            )
            await client.login()
            _status, listing = await client.get("/api/v1/ai/models")
            usage = listing["data"]["items"][0]["usage"]
            assert usage["calls"] == 1 and usage["tokens"] == 7

    async def test_router_reset_requires_confirm(self, tmp_path: Path) -> None:
        os.environ[TEST_KEY] = "sk-ok-123456"
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, bot, _s):
            await client.login()
            await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
            bot.ai.router.states["fast"].cooldown_until = 10**12
            status, payload = await client.post("/api/v1/ai/router/reset", body={})
            assert status == 409 and error_code(payload) == "ai.confirm_required"
            status, payload = await client.post(
                "/api/v1/ai/router/reset", body={"confirm": "reset"}
            )
            assert status == 200 and payload["data"]["reset"] is True
            assert bot.ai.router.states["fast"].cooldown_until == 0.0


class TestProviderLifecycle:
    async def test_provider_created_via_api_reaches_the_router(self, tmp_path: Path) -> None:
        os.environ[TEST_KEY] = "sk-ok-123456"
        async with ai_server(tmp_path) as (client, bot, _server):
            await client.login()
            status, _ = await client.put(
                "/api/v1/ai/providers/fakep",
                body={
                    "type": "fake_test",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key_env": TEST_KEY,
                },
            )
            assert status == 200
            status, _ = await client.put(
                "/api/v1/ai/models/fast", body={"provider": "fakep", "model": "fake-model"}
            )
            assert status == 200
            status, _ = await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
            assert status == 200
            assert list(bot.ai.router.states) == ["fast"]
            assert bot.ai.enabled is True


class TestCredentials:
    async def test_list_masks_the_written_key(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, _b, _s):
            await client.login()
            secret = "sk-abcdef123456789"
            status, payload = await client.put(
                f"/api/v1/credentials/ai/{TEST_KEY}", body={"value": secret}
            )
            assert status == 200
            assert payload["data"]["configured"] is True
            assert payload["data"]["restart_required"] is False
            assert secret not in json.dumps(payload)
            _status, listing = await client.get("/api/v1/credentials")
            row = next(r for r in listing["data"]["items"] if r["ref"] == TEST_KEY)
            assert row["domain"] == "ai" and row["configured"] is True
            assert row["masked"] and row["masked"] != secret
            assert secret not in json.dumps(listing)

    async def test_env_write_is_atomic_and_keeps_other_lines(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        env_file.write_text("OTHER_VAR=keepme\n# keep this comment\n", encoding="utf-8")
        monkeypatch.setattr(env_store, "env_path", lambda: env_file)
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, _ = await client.put(
                f"/api/v1/credentials/ai/{TEST_KEY}", body={"value": "sk-abcdef123456789"}
            )
            assert status == 200
            text = env_file.read_text(encoding="utf-8")
            assert "OTHER_VAR=keepme" in text and "# keep this comment" in text
            assert f"{TEST_KEY}=sk-abcdef123456789" in text
            assert os.environ[TEST_KEY] == "sk-abcdef123456789"
            assert list(tmp_path.glob(".env.*.tmp")) == []  # atomic replace left nothing behind

    async def test_key_never_lands_in_overrides_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        async with ai_server(tmp_path) as (client, _bot, server):
            await client.login()
            await client.put(
                "/api/v1/ai/providers/localref",
                body={
                    "type": "fake_test",
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key_env": TEST_KEY,
                },
            )
            secret = "sk-abcdef123456789"
            await client.put(f"/api/v1/credentials/ai/{TEST_KEY}", body={"value": secret})
            overrides_text = server._config_admin.overrides_path.read_text(encoding="utf-8")
            assert secret not in overrides_text
            assert TEST_KEY in overrides_text  # the name is fine; the value is not

    async def test_empty_value_deletes(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            await client.put(f"/api/v1/credentials/ai/{TEST_KEY}", body={"value": "sk-delete-me"})
            status, payload = await client.put(
                f"/api/v1/credentials/ai/{TEST_KEY}", body={"value": ""}
            )
            assert status == 200
            assert payload["data"]["deleted"] is True
            assert payload["data"]["configured"] is False
            assert TEST_KEY not in os.environ

    async def test_invalid_domain_and_ref(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.put("/api/v1/credentials/nope/X", body={"value": "v"})
            assert status == 400 and error_code(payload) == "credential.invalid_domain"
            status, payload = await client.put("/api/v1/credentials/ai/1bad", body={"value": "v"})
            assert status == 400 and error_code(payload) == "credential.invalid_name"

    async def test_delete_in_use_requires_force(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        os.environ[TEST_KEY] = "sk-delete-me"
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, _bot, _s):
            await client.login()
            status, payload = await client.delete(f"/api/v1/credentials/ai/{TEST_KEY}")
            assert status == 409 and error_code(payload) == "credential.in_use"
            status, _payload = await client.delete(f"/api/v1/credentials/ai/{TEST_KEY}?force=1")
            assert status == 200
            assert TEST_KEY not in os.environ

    async def test_onebot_write_marks_restart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            token = "onebot-token-123456"
            status, payload = await client.put(
                f"/api/v1/credentials/onebot/{ONEBOT_TOKEN}", body={"value": token}
            )
            assert status == 200 and payload["data"]["restart_required"] is True
            assert token not in json.dumps(payload)
            assert f"{ONEBOT_TOKEN}={token}" in (tmp_path / ".env").read_text(encoding="utf-8")

    async def test_web_password_changes_and_invalidates_sessions(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.put(
                "/api/v1/credentials/web/admin", body={"value": "newpass123"}
            )
            assert status == 200 and payload["data"]["restart_required"] is False
            assert "newpass123" not in json.dumps(payload)
            status, _ = await client.get("/api/v1/session")
            assert status == 401  # 改密后所有会话失效
            status, _ = await client.login(password="newpass123")
            assert status == 200

    async def test_web_password_delete_is_forbidden(self, tmp_path: Path) -> None:
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.delete("/api/v1/credentials/web/admin")
            assert status == 409 and error_code(payload) == "credential.delete_forbidden"

    async def test_tool_credential_roundtrip_in_secrets_json(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import app.tools.credentials as credentials_module

        secrets_path = tmp_path / "secrets.json"
        monkeypatch.setattr(credentials_module, "SECRETS_FILE", secrets_path)
        async with ai_server(tmp_path) as (client, _bot, _server):
            await client.login()
            secret = "weather-secret-123"
            status, payload = await client.put(
                "/api/v1/credentials/tool/weather_api_key", body={"value": secret}
            )
            assert status == 200 and payload["data"]["configured"] is True
            assert secret not in json.dumps(payload)
            assert secrets_path.exists()
            _status, listing = await client.get("/api/v1/credentials")
            row = next(
                r
                for r in listing["data"]["items"]
                if r["domain"] == "tool" and r["ref"] == "weather_api_key"
            )
            assert row["configured"] is True and row["masked"] != secret
            assert secret not in json.dumps(listing)

    async def test_test_endpoint_uses_value_without_persisting(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env_file = tmp_path / ".env"
        monkeypatch.setattr(env_store, "env_path", lambda: env_file)
        async with ai_server(tmp_path, config_overrides=fake_ai_block()) as (client, _bot, _s):
            await client.login()
            secret = "sk-oneoff-123456"
            status, payload = await client.post(
                "/api/v1/credentials/test",
                body={"provider": "fakep", "model": "fake-model", "value": secret},
            )
            assert status == 200
            assert payload["data"]["ok"] is True and payload["data"]["reply"] == "pong"
            assert secret not in json.dumps(payload)
            assert not env_file.exists()  # 现测不落盘
            status, payload = await client.post(
                "/api/v1/credentials/test",
                body={"provider": "fakep", "model": "fake-model", "value": "sk-boom"},
            )
            assert status == 200 and payload["data"]["ok"] is False
            assert payload["data"]["error_type"] == "RuntimeError"
            status, payload = await client.post(
                "/api/v1/credentials/test", body={"provider": "fakep", "model": "fake-model"}
            )
            assert status == 400 and error_code(payload) == "ai.no_key"
            status, payload = await client.post(
                "/api/v1/credentials/test", body={"provider": "ghost", "value": "sk-x"}
            )
            assert status == 404 and error_code(payload) == "ai.provider_unknown"
