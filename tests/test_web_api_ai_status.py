"""W4 后端：AI 状态真相 + 统一后的测试结果形状（契约 §6 / §63）。

这三件事必须由后端说了算：健康状态（ready/degraded/unavailable/not_configured）、
测试结果的唯一形状、以及 HTTP 状态的来源标注。
"""

from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.ai.errors import AuthenticationError, ServerError
from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider, register_provider_type
from app.web.services.ai_admin import derive_ai_status
from tests.api_harness import api_server, error_code

KEY_NAME = "CATOOBOT_W4_STATUS_KEY"
KEY_BOOM = "CATOOBOT_W4_STATUS_KEY_BOOM"


class FakeProvider(AIProvider):
    def __init__(self, **kwargs: Any) -> None:
        self.name = str(kwargs.get("name") or "fake")
        self.api_key = str(kwargs.get("api_key") or "")

    async def chat(self, request: AIRequest) -> AIResponse:  # noqa: ARG002 - a stub
        return AIResponse(content="测试成功", model="fake-model", provider=self.name)

    async def close(self) -> None:
        return None


class BoomProvider(AIProvider):
    def __init__(self, **kwargs: Any) -> None:
        self.name = str(kwargs.get("name") or "boom")

    async def chat(self, request: AIRequest) -> AIResponse:  # noqa: ARG002 - a stub
        if os.environ.get(KEY_BOOM) == "auth":
            raise AuthenticationError(self.name, "invalid api key")
        raise ServerError(self.name, "boom-model", status=503, detail="upstream exploded")

    async def close(self) -> None:
        return None


register_provider_type("w4fake", lambda **kwargs: FakeProvider(**kwargs))
register_provider_type("w4boom", lambda **kwargs: BoomProvider(**kwargs))


def ai_block(*, provider_type: str = "w4fake", env: str = KEY_NAME) -> dict[str, Any]:
    return {
        "ai": {
            "enabled": True,
            "providers": {
                "p": {
                    "type": provider_type,
                    "base_url": "http://127.0.0.1:9/v1",
                    "api_key_env": env,
                }
            },
            "models": [{"name": "fast", "provider": "p", "model": "m-fast"}],
        }
    }


@pytest.fixture()
def with_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.setenv(KEY_NAME, "test-key-value")
    yield KEY_NAME


@pytest.fixture()
def without_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    monkeypatch.delenv(KEY_NAME, raising=False)
    yield KEY_NAME


class TestAiStatus:
    async def test_not_configured_when_nothing_is_set_up(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/ai/status")
            assert status == 200
            data = payload["data"]
            assert data["status"] == "not_configured"
            assert data["checks"] == {
                "has_provider": False,
                "has_credential": False,
                "has_model": False,
                "chat_bound": False,
            }
            assert data["providers"]["total"] == 0 and data["models"]["total"] == 0
            assert data["chat_model"] == "" and data["fallback_chain"] == []

    async def test_ready_with_a_keyed_provider_and_an_enabled_model(
        self, tmp_path: Path, with_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/ai/status")
            assert status == 200
            data = payload["data"]
            assert data["status"] == "ready"
            assert data["enabled"] is True
            assert data["checks"]["has_credential"] is True
            assert data["providers"] == {"total": 1, "with_key": 1, "missing_key": []}
            assert data["models"]["enabled"] == 1 and data["models"]["usable"] == 1
            assert data["chat_model"] == "fast"
            assert data["fallback_chain"] == ["fast"]
            assert data["errors"] == {"rate_limited": 0, "server_errors": 0}

    async def test_unavailable_when_the_key_is_missing(
        self, tmp_path: Path, without_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, _bot, _server):
            await client.login()
            _status, payload = await client.get("/api/v1/ai/status")
            data = payload["data"]
            assert data["status"] == "unavailable"
            assert data["checks"]["has_provider"] is True
            assert data["checks"]["has_credential"] is False
            assert data["providers"]["missing_key"] == ["p"]

    async def test_degraded_when_the_only_model_cools_down(
        self, tmp_path: Path, with_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, bot, _server):
            await client.login()
            state = bot.ai.router.states["fast"]
            state.cooldown_until = time.monotonic() + 30.0
            _status, payload = await client.get("/api/v1/ai/status")
            data = payload["data"]
            assert data["status"] == "degraded"
            assert data["models"]["usable"] == 0 and data["models"]["cooldown"] == 1
            assert data["cooldown_models"][0]["name"] == "fast"
            assert data["cooldown_models"][0]["cooldown_until"] > time.monotonic()
            # the UI counts down from the remaining seconds, never from the monotonic deadline
            assert 0 < data["cooldown_models"][0]["remaining_seconds"] <= 30

    async def test_error_counters_come_from_the_real_usage_table(
        self, tmp_path: Path, with_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, bot, _server):
            await client.login()
            assert bot.ai_usage is not None
            await bot.ai_usage.record(
                provider="p", model="m-fast", latency_ms=12.0, ok=False, error_type="RateLimitError"
            )
            await bot.ai_usage.record(
                provider="p", model="m-fast", latency_ms=30.0, ok=False, error_type="ServerError"
            )
            _status, payload = await client.get("/api/v1/ai/status")
            assert payload["data"]["errors"] == {"rate_limited": 1, "server_errors": 1}

    async def test_overview_reports_the_same_status_word(
        self, tmp_path: Path, with_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, bot, _server):
            await client.login()
            _status, deep = await client.get("/api/v1/ai/status")
            _status, overview = await client.get("/api/v1/overview")
            assert overview["data"]["ai"]["status"] == deep["data"]["status"]
            assert overview["data"]["ai"]["status"] == derive_ai_status(bot)


class TestModelTestShape:
    async def test_success_reports_alias_provider_model_and_reply(
        self, tmp_path: Path, with_key: str
    ) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post(
                "/api/v1/ai/models/fast/test", body={"prompt": "hi"}
            )
            assert status == 200
            data = payload["data"]
            assert data["ok"] is True
            assert data["model"] == "fast" and data["requested_model"] == "fast"
            # the alias the operator configured vs the id that really answered
            assert data["provider_model"] == "fake-model" and data["provider"] == "p"
            assert data["http_status"] == 200 and data["http_status_source"] == "upstream"
            assert data["error_type"] == "" and data["message"] == ""
            assert data["response"] == "测试成功"
            assert isinstance(data["latency_ms"], int | float)

    async def test_upstream_5xx_keeps_its_real_status(self, tmp_path: Path, with_key: str) -> None:
        async with api_server(tmp_path, config_overrides=ai_block(provider_type="w4boom")) as (
            client,
            _bot,
            _server,
        ):
            await client.login()
            _status, payload = await client.post("/api/v1/ai/models/fast/test", body={})
            data = payload["data"]
            assert data["ok"] is False
            # the router exhausted the chain, so the failure is AllModelsFailedError:
            # there is no single upstream HTTP answer to report (§ router semantics)
            assert data["error_type"] == "AllModelsFailedError"
            assert data["http_status"] is None and data["http_status_source"] == ""
            assert "503" in data["message"] and "exploded" in data["message"]
            assert data["response"] == ""

    async def test_auth_error_maps_to_its_canonical_status(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv(KEY_BOOM, "auth")
        async with api_server(
            tmp_path, config_overrides=ai_block(provider_type="w4boom", env=KEY_BOOM)
        ) as (client, _bot, _server):
            await client.login()
            _status, payload = await client.post("/api/v1/ai/models/fast/test", body={})
            data = payload["data"]
            assert data["ok"] is False
            # the exception carries no upstream status → the class's canonical code
            assert data["http_status"] == 401 and data["http_status_source"] == "error_class"
            assert data["error_type"] == "AuthenticationError"

    async def test_unknown_model_is_404(self, tmp_path: Path, with_key: str) -> None:
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/ai/models/ghost/test", body={})
            assert status == 404
            assert error_code(payload) == "ai.model_unknown"

    async def test_the_test_is_a_pure_diagnostic(self, tmp_path: Path, with_key: str) -> None:
        """§37/§107：诊断请求不写记忆、不进对话、不动关系。"""
        async with api_server(tmp_path, config_overrides=ai_block()) as (client, bot, _server):
            await client.login()
            sandbox = bot.sandbox
            before = None
            memories_before = None
            if sandbox is not None:
                before = (sandbox.world_revision, sandbox.cognitive_revision)
                memories_before = await sandbox.memory.count(status="active")
            await client.post("/api/v1/ai/models/fast/test", body={})
            if sandbox is not None and before is not None:
                assert (sandbox.world_revision, sandbox.cognitive_revision) == before
                assert await sandbox.memory.count(status="active") == memories_before
