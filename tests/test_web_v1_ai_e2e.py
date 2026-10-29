"""W6 AI 端到端：WebUI 配置 → 真实路由器 → 故障转移/冷却（§67/§68/§69）。

全程走真实 HTTP + 真实中间件 + 真实 `ModelRouter`，Provider 只在进程内注册
一个假类型（绝不联网、绝不使用真 Key）：

* 建 Provider → 存凭据（临时 .env + 环境变量）→ 建模型 → chat 角色；
* `GET /ai/status` 说 ready，`fallback_chain` 是配置顺序；
* 主模型 429、备用成功时，**路由器自己在一次调用里**完成转移
  （一旦是 WebUI 在重试，provider 调用序列/次数就对不上）；
* 主模型进入冷却（剩余秒数为正），全链冷却时 `status == degraded`；
* `POST /ai/router/reset` 清空冷却。
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest

from app.ai.errors import RateLimitError
from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider, register_provider_type
from app.config import env_store
from tests.api_harness import ApiClient, api_server

KEY_ENV = "CATOOBOT_W6_AI_E2E_KEY"
KEY_VALUE = "sk-w6-e2e-123456"
PRIMARY = "m-primary"
BACKUP = "m-backup"
PRIMARY_ID = "id-primary"
BACKUP_ID = "id-backup"
FAKE_TYPE = "w6_fake_e2e"

#: 真实 .env / 环境里绝不允许参与的名字
_ENV_NAMES = (KEY_ENV, "CATOOBOT_WEB_PASSWORD")


class W6FakeProvider(AIProvider):
    """两个模型都返回 pong；测试可把任一模型打成 HTTP 429（RateLimitError）。"""

    #: 类级调用日志：凭据落盘会重建 Provider 实例，类级才能看到全过程
    calls: ClassVar[list[str]] = []
    fail_primary: ClassVar[bool] = False
    fail_backup: ClassVar[bool] = False

    def __init__(self, **kwargs: Any) -> None:
        self.name = str(kwargs.get("name", FAKE_TYPE))
        self.base_url = str(kwargs.get("base_url", ""))
        self.api_key = str(kwargs.get("api_key", ""))

    async def chat(self, request: AIRequest) -> AIResponse:
        model_id = str(request.model or "")
        W6FakeProvider.calls.append(model_id)
        failing = (model_id == PRIMARY_ID and W6FakeProvider.fail_primary) or (
            model_id == BACKUP_ID and W6FakeProvider.fail_backup
        )
        if failing:
            raise RateLimitError(self.name, model_id, "synthetic 429")
        return AIResponse(content=f"pong-from-{model_id}", model=model_id, provider=self.name)

    async def close(self) -> None:
        return None


register_provider_type(FAKE_TYPE, W6FakeProvider)


@pytest.fixture(autouse=True)
def _isolate_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    # load_config() 会调用 load_dotenv()：项目真实 .env（运营者密码/Key）绝不进测试
    monkeypatch.setattr("app.config.settings.load_dotenv", lambda *args, **kwargs: None)
    monkeypatch.setattr(env_store, "env_path", lambda: tmp_path / ".env")
    monkeypatch.setattr("app.tools.credentials.SECRETS_FILE", tmp_path / "secrets.json")
    for name in _ENV_NAMES:
        os.environ.pop(name, None)
    W6FakeProvider.calls = []
    W6FakeProvider.fail_primary = False
    W6FakeProvider.fail_backup = False
    yield
    W6FakeProvider.calls = []
    W6FakeProvider.fail_primary = False
    W6FakeProvider.fail_backup = False
    for name in _ENV_NAMES:
        os.environ.pop(name, None)


async def provision(client: ApiClient) -> None:
    """按产品路径建好：Provider → 凭据 → 两个模型 → chat 角色 → 启用。"""
    status, payload = await client.put(
        "/api/v1/ai/providers/fakep",
        body={
            "type": FAKE_TYPE,
            "base_url": "http://127.0.0.1:9/v1",
            "api_key_env": KEY_ENV,
        },
    )
    assert status == 200, payload
    status, payload = await client.put(
        f"/api/v1/credentials/ai/{KEY_ENV}", body={"value": KEY_VALUE}
    )
    assert status == 200 and payload["data"]["configured"] is True
    assert KEY_VALUE not in json.dumps(payload), "凭据明文绝不回显"
    for name, model_id in ((PRIMARY, PRIMARY_ID), (BACKUP, BACKUP_ID)):
        status, payload = await client.put(
            f"/api/v1/ai/models/{name}", body={"provider": "fakep", "model": model_id}
        )
        assert status == 200, payload
    status, payload = await client.put("/api/v1/ai/roles/chat", body={"model": PRIMARY})
    assert status == 200 and payload["data"]["model"] == PRIMARY
    status, payload = await client.patch("/api/v1/config", body={"values": {"ai.enabled": True}})
    assert status == 200, payload


async def trigger_fallback(
    client: ApiClient, bot: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[dict[str, Any], int]:
    """把主模型打成 429，然后只发一次 HTTP；返回 (测试结果, router.chat 次数)。"""
    monkeypatch.setattr(W6FakeProvider, "fail_primary", True)
    router = bot.ai.router
    original = router.chat
    calls = 0

    async def counting(request: AIRequest) -> AIResponse:
        nonlocal calls
        calls += 1
        return await original(request)

    monkeypatch.setattr(router, "chat", counting)
    status, payload = await client.post(f"/api/v1/ai/models/{PRIMARY}/test", body={})
    assert status == 200, payload
    return payload["data"], calls


class TestAiEndToEnd:
    async def test_provisioned_chain_is_ready_and_answers(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            await provision(client)

            status, payload = await client.get("/api/v1/ai/status")
            data = payload["data"]
            assert status == 200 and data["status"] == "ready"
            assert data["chat_model"] == PRIMARY
            assert data["fallback_chain"] == [PRIMARY, BACKUP]
            assert data["providers"]["with_key"] == 1 and data["providers"]["missing_key"] == []

            status, payload = await client.post(f"/api/v1/ai/models/{PRIMARY}/test", body={})
            assert status == 200 and payload["data"]["ok"] is True
            assert payload["data"]["provider_model"] == PRIMARY_ID
            assert W6FakeProvider.calls == [PRIMARY_ID]

    async def test_fallback_happens_inside_one_router_call(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            await provision(client)

            data, router_calls = await trigger_fallback(client, bot, monkeypatch)
            assert data["ok"] is True, "备用模型必须把请求接住"
            assert data["requested_model"] == PRIMARY and data["model"] == PRIMARY
            assert data["provider_model"] == BACKUP_ID, "实际作答者应为备用模型"
            assert data["provider"] == "fakep"
            # 关键证据：一次 HTTP = 一次 router.chat，重试发生在路由器内部
            assert router_calls == 1, "WebUI 不得自行重试（转移必须发生在路由器里）"
            assert W6FakeProvider.calls == [PRIMARY_ID, BACKUP_ID]

            status, listing = await client.get("/api/v1/ai/models")
            rows = {row["name"]: row for row in listing["data"]["items"]}
            primary = rows[PRIMARY]
            assert primary["in_cooldown"] is True, "429 的主模型必须进入冷却"
            assert 0 < primary["cooldown_remaining_seconds"] <= 30.0
            assert primary["failure_count"] == 1 and primary["last_error"]
            assert primary["usage"]["rate_limited"] == 1, "429 必须记进真实用量表"
            assert rows[BACKUP]["in_cooldown"] is False

            status, payload = await client.get("/api/v1/ai/status")
            assert payload["data"]["errors"]["rate_limited"] >= 1
            # 备用仍可用 = 服务没断（ready）；全链冷却才是 degraded（见下一用例）
            assert payload["data"]["status"] == "ready"

    async def test_degraded_then_reset_clears_cooldowns(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with api_server(tmp_path) as (client, bot, _server):
            await client.login()
            await provision(client)
            await trigger_fallback(client, bot, monkeypatch)

            # 让备用也 429：它的测试调用是 pinned 的，没有别的候选
            W6FakeProvider.fail_backup = True
            status, payload = await client.post(f"/api/v1/ai/models/{BACKUP}/test", body={})
            assert status == 200 and payload["data"]["ok"] is False

            status, payload = await client.get("/api/v1/ai/status")
            data = payload["data"]
            assert data["status"] == "degraded", "全链都不可用时必须报 degraded"
            assert data["models"]["usable"] == 0 and data["models"]["cooldown"] == 2
            assert {row["name"] for row in data["cooldown_models"]} == {PRIMARY, BACKUP}
            assert all(row["remaining_seconds"] > 0 for row in data["cooldown_models"])

            status, listing = await client.get("/api/v1/ai/models")
            rows = {row["name"]: row for row in listing["data"]["items"]}
            assert rows[PRIMARY]["in_cooldown"] is True
            assert rows[PRIMARY]["cooldown_remaining_seconds"] > 0

            status, payload = await client.post(
                "/api/v1/ai/router/reset", body={"confirm": "reset"}
            )
            assert status == 200 and payload["data"]["reset"] is True

            status, listing = await client.get("/api/v1/ai/models")
            assert all(not row["in_cooldown"] for row in listing["data"]["items"])
            status, payload = await client.get("/api/v1/ai/status")
            assert payload["data"]["status"] == "ready"
