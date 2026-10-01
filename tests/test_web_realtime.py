"""Live WebUI push (Task 17): narration + status over one WebSocket.

Three properties matter more than features here, and each has a test:

* **auth** — the upgrade carries the normal session cookie; without it the
  handshake is refused with 401 (never a redirect a WS client cannot follow);
* **whitelist** — only narration lines (redacted) and an admin-service status
  snapshot travel; raw protocol payloads never do;
* **backpressure** — every subscriber owns a bounded queue, so a browser that
  stops reading loses the oldest messages instead of stalling QQ chat.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

import aiohttp
import pytest

from app.adapters import Adapter
from app.config.settings import AppConfig
from app.core.bot import Bot
from app.web.realtime import RealtimeHub, attach_narration_feed, detach_narration_feed

SECRET_TOKEN = "o-UGheGr.qW.awrh"


class DummyAdapter(Adapter):
    @property
    def name(self) -> str:
        return "dummy"

    @property
    def connected(self) -> bool:
        return False

    @property
    def self_id(self) -> int | None:
        return None

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def call_api(self, action, params=None, timeout=None) -> Any:  # type: ignore[no-untyped-def]
        return {}


class TestHub:
    def test_publish_fans_out_and_unsubscribe_stops_it(self) -> None:
        hub = RealtimeHub()
        first, second = hub.subscribe(), hub.subscribe()
        assert hub.publish("narration", {"message": "hi"}) == 2
        assert hub.subscriber_count == 2
        hub.unsubscribe(second)
        assert hub.publish("narration", {"message": "again"}) == 1
        assert first.get_nowait()["data"] == {"message": "hi"}
        assert second.qsize() == 1  # no longer receiving

    def test_publish_never_blocks_and_drops_the_oldest(self) -> None:
        hub = RealtimeHub(queue_size=2)
        queue = hub.subscribe()
        for index in range(5):
            hub.publish("narration", {"n": index})

        assert hub.dropped == 3
        assert hub.stats()["dropped"] == 3
        kept = [queue.get_nowait()["data"]["n"] for _ in range(queue.qsize())]
        assert kept == [3, 4]  # the live tail survives, not the history

    def test_publish_without_subscribers_is_free(self) -> None:
        hub = RealtimeHub()
        assert hub.publish("status", {"x": 1}) == 0
        assert hub.published == 1  # counted, nobody harmed

    def test_narration_feed_redacts_secrets(self, caplog) -> None:
        hub = RealtimeHub()
        feed = attach_narration_feed(hub)
        queue = hub.subscribe()
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                logging.getLogger("CatooBot.Narration").info(
                    "token=%s 已轮换", SECRET_TOKEN, extra={"narrate": True, "channel": "boot"}
                )
        finally:
            detach_narration_feed(feed)

        message = queue.get_nowait()
        assert message["topic"] == "narration"
        assert message["data"]["channel"] == "boot"
        assert SECRET_TOKEN not in message["data"]["message"]  # redacted
        assert "token=***" in message["data"]["message"]

    def test_feed_removal_stops_the_stream(self) -> None:
        hub = RealtimeHub()
        feed = attach_narration_feed(hub)
        detach_narration_feed(feed)
        logging.getLogger("CatooBot.Narration").info("x", extra={"narrate": True})
        assert hub.published == 0


class TestWebSocket:
    async def _serve(self, tmp_path, monkeypatch, *, status_interval: float = 0.05):  # type: ignore[no-untyped-def]
        monkeypatch.setattr("app.web.server.STATUS_INTERVAL", status_interval)
        import socket

        from app.web.server import WebServer

        port = 0
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": f"sqlite:///{tmp_path / 'ws.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            web={
                "enabled": True,
                "host": "127.0.0.1",
                "port": 8500,
                "username": "admin",
                "password": "pw123",
            },
        )
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
            port = int(probe.getsockname()[1])
        config.web.port = port
        bot = Bot(config, DummyAdapter())
        await bot.database.connect()
        await bot.character.start()
        server = WebServer(config.web, bot)
        await server.start()
        return bot, server, f"http://127.0.0.1:{port}"

    async def test_unauthenticated_upgrade_is_refused(self, tmp_path, monkeypatch) -> None:
        bot, server, base = await self._serve(tmp_path, monkeypatch)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                with pytest.raises(aiohttp.WSServerHandshakeError) as excinfo:
                    await session.ws_connect(base + "/ws/events")
                assert excinfo.value.status == 401
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_narration_and_status_reach_a_logged_in_browser(
        self, tmp_path, monkeypatch
    ) -> None:
        bot, server, base = await self._serve(tmp_path, monkeypatch)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                async with session.post(
                    base + "/login", data={"username": "admin", "password": "pw123"}
                ) as resp:
                    assert resp.status == 200

                async with session.ws_connect(base + "/ws/events") as ws:
                    hello = json.loads((await ws.receive(timeout=2)).data)
                    assert hello["topic"] == "hello"
                    assert hello["data"]["subscribers"] == 1

                    # narration published by the hub (the feed does this in production)
                    server._hub.publish("narration", {"channel": "sense", "message": "嗨"})
                    message = json.loads((await asyncio.wait_for(ws.receive(), timeout=2)).data)
                    assert message["topic"] == "narration"
                    assert message["data"]["message"] == "嗨"

                    # …and the periodic status snapshot built by AdminService
                    status = json.loads((await asyncio.wait_for(ws.receive(), timeout=2)).data)
                    assert status["topic"] == "status"
                    assert {"online", "sandbox", "models", "metrics"} <= set(status["data"])
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_logs_page_ships_the_live_feed(self, tmp_path, monkeypatch) -> None:
        bot, server, base = await self._serve(tmp_path, monkeypatch)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await session.post(base + "/login", data={"username": "admin", "password": "pw123"})
                async with session.get(base + "/logs") as resp:
                    assert resp.status == 200
                    body = await resp.text()
            assert 'id="live-feed"' in body
            assert "/ws/events" in body and "WebSocket" in body
            assert "重连" in body  # the reconnect path is on the page, not only in tests
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_disconnect_leaves_no_subscriber(self, tmp_path, monkeypatch) -> None:
        bot, server, base = await self._serve(tmp_path, monkeypatch)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await session.post(base + "/login", data={"username": "admin", "password": "pw123"})
                ws = await session.ws_connect(base + "/ws/events")
                assert server._hub.subscriber_count == 1
                await ws.close()
                for _ in range(50):  # the server may need a beat to notice
                    if server._hub.subscriber_count == 0:
                        break
                    await asyncio.sleep(0.02)
                assert server._hub.subscriber_count == 0
        finally:
            await server.stop()
            await bot.shutdown()
