"""`/api/v1/runtime` / `/api/v1/logs` 的 W5 扩展（契约 §3/§7.4）。

覆盖：``runtime.process``、``runtime.onebot``（含 lane 深度，由
``OneBotGateway.stats()`` 只读投影）、``hub.queue_size``、``scheduler.last_report``、
``database.size_bytes`` 与 ``logs/tail`` 的 channel/level/q/limit 过滤。

W2/W3/W4 的既有键必须原样保留（本文件同时是回归面）。
"""

from __future__ import annotations

import logging
import sys
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.integrations.onebot.gateway import ConnectionState, OneBotGateway
from app.main import VERSION
from app.web.api.runtime import RuntimeApiRoutes
from app.web.server import WebServer
from tests.api_harness import ApiClient, free_port
from tests.conftest import BIBLE_PATH
from tests.test_web_realtime import DummyAdapter


class V1Server(WebServer, RuntimeApiRoutes):
    """显式组合（RuntimeApiRoutes 已由 W2 合入 ApiRoutes，这里保持可单测）。"""


class _FakeTransport:
    connected = False

    async def start(self, handler: Any) -> None:
        return None

    async def stop(self) -> None:
        return None


class _FakeRuntime:
    config = None
    _log = logging.getLogger("test.gateway.ext")

    def __init__(self) -> None:
        self.events = SimpleNamespace(publish=lambda *args, **kwargs: None)
        self.persons = SimpleNamespace(core_map=lambda: {})

    async def submit_external(self, event: Any) -> None:
        return None

    async def wakeup(self) -> list[Any]:
        return []

    async def conversation_turn(self, **kwargs: Any) -> Any:
        return object()

    def commit_conversation_response(self, response: Any) -> Any:
        return SimpleNamespace(mode="silent", text="")


def build_gateway() -> OneBotGateway:
    config = SimpleNamespace(
        dedupe_ttl=600.0,
        dedupe_max_size=32,
        self_ids=["7"],
        max_pending_per_lane=5,
        shutdown_timeout=0.5,
    )
    return OneBotGateway(
        _FakeRuntime(),
        transport=_FakeTransport(),
        config=config,
        logger=logging.getLogger("test.gateway.ext"),
    )


PRIVATE_EVENT = {
    "post_type": "message",
    "message_type": "private",
    "user_id": "10001",
    "self_id": "7",
    "message_id": "m1",
    "message": [{"type": "text", "data": {"text": "在吗"}}],
    "time": 1,
}


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


class TestRuntimeProcessAndOneBot:
    async def test_process_block_is_honest(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/runtime")
            assert status == 200
            process = payload["data"]["process"]
            assert set(process) == {"uptime_seconds", "started_at", "version", "python"}
            assert process["version"] == VERSION
            assert process["python"] == sys.version.split()[0]
            # Bot.start() 未跑 → 不编造 uptime
            assert bot.started_at is None
            assert process["uptime_seconds"] is None
            assert process["started_at"] is None

    async def test_onebot_block_when_gateway_is_off(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            assert bot.onebot_gateway is None
            status, payload = await client.get("/api/v1/runtime")
            onebot = payload["data"]["onebot"]
            assert set(onebot) == {
                "state",
                "connected",
                "self_id",
                "last_event_at",
                "received",
                "accepted",
                "deduped",
                "dropped",
                "self_ignored",
                "responses",
                "sent",
                "failed",
                "pending_outbound",
                "busy",
                "lanes",
            }
            assert onebot["state"] == "disabled"
            assert onebot["connected"] is False
            assert onebot["lanes"] == []
            assert onebot["received"] is None  # 未启用 → null，不是 0

    async def test_gateway_stats_reports_counters_and_lanes(self) -> None:
        gateway = build_gateway()
        gateway.state = ConnectionState.connected
        try:
            result = await gateway.handle_transport_event(dict(PRIVATE_EVENT))
            assert result["accepted"] is True
            stats = gateway.stats()
            assert stats["state"] == "connected"
            assert stats["connected"] is True
            assert stats["self_id"] == "7"
            assert stats["received"] == 1
            assert stats["accepted"] == 1
            assert stats["last_event_at"]
            assert stats["pending_outbound"] == 0
            assert stats["lanes"]
            for lane in stats["lanes"]:
                assert set(lane) == {"lane", "pending", "busy"}

            # 纯只读：再读一次计数不变
            again = gateway.stats()
            assert again["received"] == stats["received"]
            assert again["accepted"] == stats["accepted"]
        finally:
            await gateway.stop()

    async def test_runtime_onebot_uses_the_live_gateway(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await _login(client)
            gateway = build_gateway()
            gateway.state = ConnectionState.connected
            await gateway.handle_transport_event(dict(PRIVATE_EVENT))
            bot.onebot_gateway = gateway
            try:
                status, payload = await client.get("/api/v1/runtime")
                onebot = payload["data"]["onebot"]
                assert onebot["state"] == "connected"
                assert onebot["connected"] is True
                assert onebot["received"] == 1
                assert onebot["accepted"] == 1
                assert onebot["self_id"] == "7"
                assert onebot["lanes"] and onebot["lanes"][0]["lane"]
            finally:
                bot.onebot_gateway = None
                await gateway.stop()

    async def test_runtime_status_carries_the_same_blocks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/runtime/status")
            assert status == 200
            assert {"scheduler", "watchdog", "database", "hub", "process", "onebot"} == set(
                payload["data"]
            )


class TestRuntimeExistingKeysExtended:
    async def test_hub_keeps_queue_size(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            _status, payload = await client.get("/api/v1/runtime")
            hub = payload["data"]["hub"]
            assert set(hub) == {"subscribers", "published", "dropped", "queue_size"}
            assert isinstance(hub["queue_size"], int)

    async def test_scheduler_gains_last_report(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            _status, runtime = await client.get("/api/v1/runtime")
            assert "last_report" in runtime["data"]["scheduler"]
            _status, overview = await client.get("/api/v1/overview")
            assert "last_report" in overview["data"]["runtime"]["scheduler"]
            assert "process" in overview["data"]["runtime"]
            assert "onebot" in overview["data"]["runtime"]
            assert "queue_size" in overview["data"]["runtime"]["hub"]

    async def test_database_size_bytes_is_file_backed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            _status, payload = await client.get("/api/v1/runtime")
            database = payload["data"]["database"]
            assert database["connected"] is True
            assert isinstance(database["size_bytes"], int)
            assert database["size_bytes"] > 0


class TestLogsTail:
    def _write_log(self, tmp_path: Any) -> str:
        log_dir = tmp_path / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        path = log_dir / "catoobot.log"
        path.write_text(
            "[12:00:00] [INFO] [CatooBot.Narration] [world] 🌍 世界 │ 她醒来了\n"
            "[12:00:01] [INFO] [CatooBot.Narration] [reply] 💬 碎碎念 │ 你在干嘛\n"
            "[12:00:02] [WARNING] [CatooBot.Web] 普通日志行\n",
            encoding="utf-8",
        )
        return str(path)

    async def test_channel_is_parsed_and_filtered(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            self._write_log(tmp_path)
            status, payload = await client.get("/api/v1/logs/tail?channel=world&limit=10")
            assert status == 200
            data = payload["data"]
            assert {"items", "file", "truncated", "parsed", "total"} <= set(data)
            assert data["parsed"] == 3
            assert data["total"] == 3
            assert len(data["items"]) == 1
            item = data["items"][0]
            assert item["channel"] == "world"
            assert item["message"].startswith("🌍 世界")
            assert "[world]" not in item["message"]

    async def test_non_narration_lines_have_null_channel(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            self._write_log(tmp_path)
            status, payload = await client.get("/api/v1/logs/tail?limit=10")
            assert status == 200
            channels = [item["channel"] for item in payload["data"]["items"]]
            assert channels == [None, "reply", "world"]  # 最新在前
            for item in payload["data"]["items"]:
                assert {"ts", "level", "logger", "message", "channel"} <= set(item)

    async def test_level_and_keyword_filters_still_work(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            self._write_log(tmp_path)
            _status, payload = await client.get("/api/v1/logs/tail?level=WARNING")
            assert [item["message"] for item in payload["data"]["items"]] == ["普通日志行"]
            _status, payload = await client.get("/api/v1/logs/tail?q=💬")
            assert len(payload["data"]["items"]) == 1
            assert payload["data"]["items"][0]["channel"] == "reply"

    async def test_limit_over_500_is_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            status, payload = await client.get("/api/v1/logs/tail?limit=501")
            assert status == 400
            assert payload["ok"] is False

    async def test_channel_filter_returns_empty_for_unknown_channel(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await _login(client)
            self._write_log(tmp_path)
            status, payload = await client.get("/api/v1/logs/tail?channel=vision")
            assert status == 200
            assert payload["data"]["items"] == []
