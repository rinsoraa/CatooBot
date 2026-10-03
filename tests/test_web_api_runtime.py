"""`/api/v1` 运行时 / 日志 / WS 新 topic（WebUI v1.0 · W2 契约 §3、§8、§10）。

``WebServer`` 已挂载 ``RuntimeApiRoutes`` / ``DomainApiRoutes``（W2 集成），
测试直接用它：走**真实中间件**
（会话 Cookie + CSRF）与真实 HTTP，既证明路由面，也不绕过契约里的状态码。

另外覆盖 W2 的三处小改动：``AdminService.live_status`` 的 v1 附加块、
``OneBotGateway.last_event_at``、``NarrationFeed`` 的 ``log`` 别名与
``_ws_status_loop`` 的 ``world`` / ``scheduler`` topic。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import aiohttp
from aiohttp import web

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.integrations.onebot.gateway import ConnectionState, OneBotGateway
from app.web.api.domain import DomainApiRoutes
from app.web.api.runtime import RuntimeApiRoutes
from app.web.realtime import RealtimeHub, attach_narration_feed, detach_narration_feed
from app.web.server import WebServer
from tests.api_harness import ApiClient, error_code, free_port
from tests.test_web_realtime import DummyAdapter

SECRET_TOKEN = "o-UGheGr.qW.awrh"


#: 集成后 WebServer 自带这两个 mixin；别名保留，测试仍走真实中间件。
V1WebServer = WebServer


@asynccontextmanager
async def v1_server(tmp_path, *, config_overrides: dict[str, Any] | None = None):  # type: ignore[no-untyped-def]
    """真实 WebServer（带 v1 路由），overrides 文件与数据库按测试隔离。"""
    port = free_port()
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": "sqlite:///" + str(tmp_path / "api.db")},
        logging={"log_dir": str(tmp_path / "logs"), "level": "WARNING"},
        web={
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "username": "admin",
            "password": "pw123",
        },
        **(config_overrides or {}),
    )
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    server = V1WebServer(config.web, bot)
    server._config_admin.overrides_path = tmp_path / "overrides.yaml"
    await server.start()
    client = ApiClient(f"http://127.0.0.1:{port}")
    try:
        yield client, bot, server
    finally:
        await client.close()
        await server.stop()
        await bot.shutdown()


@web.middleware
async def _passthrough(request: web.Request, handler: Any) -> web.StreamResponse:
    return await handler(request)


class TestRouteSurface:
    def test_route_hooks_exist(self) -> None:
        assert hasattr(RuntimeApiRoutes, "_register_v1_runtime")
        assert hasattr(DomainApiRoutes, "_register_v1_domain")

    async def test_stub_application_proves_the_http_surface(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """裸 Application + passthrough 中间件：只证明本模块注册的路由可服务。"""
        async with v1_server(tmp_path) as (_client, _bot, server):
            app = web.Application(middlewares=[_passthrough])
            server._register_v1_runtime(app)
            server._register_v1_domain(app)
            port = free_port()
            runner = web.AppRunner(app, access_log=None)
            await runner.setup()
            await web.TCPSite(runner, "127.0.0.1", port).start()
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"http://127.0.0.1:{port}/api/v1/overview"
                    async with session.get(url) as response:
                        assert response.status == 200
                        body = await response.json()
                        assert body["ok"] is True
                        assert set(body["data"]) == {"qq", "ai", "world", "runtime", "counts"}
            finally:
                await runner.cleanup()

    async def test_anonymous_read_is_401_with_the_envelope(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/overview")
            assert status == 401
            assert payload["ok"] is False
            assert error_code(payload) == "auth.unauthorized"
            assert payload["meta"]["request_id"]


class TestOverviewAndRuntime:
    async def test_overview_carries_the_contract_blocks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/overview")
            assert status == 200
            data = payload["data"]
            assert set(data) == {"qq", "ai", "world", "runtime", "counts"}
            assert set(data["qq"]) == {
                "online",
                "self_id",
                "messages_received",
                "users",
                "groups",
                "sessions",
                "last_event_at",
            }
            assert set(data["ai"]) == {
                "enabled",
                "current_model",
                "models_ok",
                "models_total",
                "requests",
                "errors",
                "rate_limited",
            }
            assert set(data["world"]) == {
                "phase",
                "location",
                "action",
                "modes",
                "needs",
                "world_revision",
                "cognitive_revision",
                "session",
                "interrupted",
            }
            assert set(data["counts"]) == {
                "memories",
                "experiences",
                "goals_open",
                "commitments_open",
            }
            runtime = data["runtime"]
            assert set(runtime["scheduler"]) == {
                "running",
                "interval_seconds",
                "ticks",
                "catchups",
                "last_tick_at",
            }
            assert set(runtime["watchdog"]) == {"last_lag_ms", "max_lag_ms", "lag_events"}
            assert isinstance(runtime["database"]["connected"], bool)
            assert set(runtime["hub"]) == {"subscribers", "published", "dropped"}
            assert isinstance(data["qq"]["messages_received"], int)

    async def test_runtime_status_and_scheduler_shapes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/runtime/status")
            assert status == 200
            assert set(payload["data"]) == {"scheduler", "watchdog", "database", "hub"}
            status, payload = await client.get("/api/v1/runtime/scheduler")
            assert status == 200
            assert set(payload["data"]) == {
                "running",
                "interval_seconds",
                "ticks",
                "catchups",
                "last_tick_at",
                "last_report",
            }
            status, payload = await client.get("/api/v1/runtime")
            assert status == 200
            assert {"scheduler", "watchdog", "database", "hub"} <= set(payload["data"])

    async def test_runtime_reads_do_not_move_the_revisions(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await client.login()
            sandbox = bot.sandbox
            before = (sandbox.world_revision, sandbox.cognitive_revision)
            for path in (
                "/api/v1/overview",
                "/api/v1/runtime",
                "/api/v1/runtime/status",
                "/api/v1/runtime/scheduler",
                "/api/v1/logs/tail?limit=10",
                "/api/v1/logs/channels",
                "/api/v1/world",
                "/api/v1/world/trace?limit=5",
            ):
                status, _payload = await client.get(path)
                assert status == 200, path
            assert (sandbox.world_revision, sandbox.cognitive_revision) == before

    async def test_tick_advances_ticks_and_returns_a_report(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        overrides = {"runtime": {"tick_interval_seconds": 60.0}}
        async with v1_server(tmp_path, config_overrides=overrides) as (client, bot, _server):
            await client.login()
            scheduler = bot.runtime_scheduler
            assert scheduler is not None
            await scheduler.start()
            try:
                before = scheduler.ticks
                status, payload = await client.post("/api/v1/runtime/tick")
                assert status == 200
                data = payload["data"]
                assert data["ran"] is True
                assert isinstance(data["report"], dict)
                assert data["ticks"] == before + 1
                assert scheduler.ticks == before + 1
            finally:
                await scheduler.stop()

    async def test_tick_without_a_running_scheduler_is_503(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, bot, _server):
            await client.login()
            assert bot.runtime_scheduler is not None
            assert bot.runtime_scheduler.running is False
            status, payload = await client.post("/api/v1/runtime/tick")
            assert status == 503
            assert error_code(payload) == "world.not_running"

    async def test_runtime_action_unknown_is_404(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/runtime/actions/nope")
            assert status == 404
            assert error_code(payload) == "world.action_unknown"

    async def test_runtime_action_reload_persona_leaves_a_detail(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.post("/api/v1/runtime/actions/reload_persona")
            assert status == 200
            assert payload["data"]["done"] is True
            assert payload["data"]["action"] == "reload_persona"
            assert "persona=" in payload["data"]["detail"]

    async def test_logs_tail_shape_and_truncated_flag(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/logs/tail?limit=3&level=INFO")
            assert status == 200
            data = payload["data"]
            assert {"items", "file", "truncated"} <= set(data)
            assert isinstance(data["items"], list)
            assert data["file"].endswith("catoobot.log")
            for item in data["items"]:
                assert {"ts", "level", "logger", "message"} <= set(item)

    async def test_logs_channels_quote_the_narrator_table(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (client, _bot, _server):
            await client.login()
            status, payload = await client.get("/api/v1/logs/channels")
            assert status == 200
            channels = {row["key"]: row for row in payload["data"]["channels"]}
            assert channels["world"]["icon"] == "🌍"
            assert channels["world"]["label"] == "世界"
            assert channels["reply"]["label"] == "碎碎念"
            assert len(channels) >= 15


class TestRealtimeAdditions:
    async def test_live_status_keeps_old_keys_and_adds_v1_blocks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        async with v1_server(tmp_path) as (_client, _bot, server):
            data = await server._admin.live_status()
            assert {"online", "sandbox", "models", "metrics", "watchdog", "plugins"} <= set(data)
            assert set(data["qq"]) == {"online", "self_id", "last_event_at"}
            assert set(data["world"]) == {
                "phase",
                "location",
                "action",
                "world_revision",
                "cognitive_revision",
            }
            assert set(data["runtime"]) == {"scheduler", "database"}
            assert set(data["runtime"]["scheduler"]) == {
                "running",
                "interval_seconds",
                "ticks",
                "catchups",
                "last_tick_at",
            }
            assert isinstance(data["runtime"]["database"]["connected"], bool)

    def test_narration_feed_publishes_the_log_alias(self, caplog) -> None:  # type: ignore[no-untyped-def]
        hub = RealtimeHub()
        queue = hub.subscribe()
        feed = attach_narration_feed(hub)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                logging.getLogger("CatooBot.Narration").info(
                    "token=%s 已轮换", SECRET_TOKEN, extra={"narrate": True, "channel": "boot"}
                )
        finally:
            detach_narration_feed(feed)
        first = queue.get_nowait()
        second = queue.get_nowait()
        assert first["topic"] == "narration"
        assert second["topic"] == "log"
        assert second["data"] == first["data"]
        assert SECRET_TOKEN not in second["data"]["message"]

    async def test_gateway_records_last_event_at_on_accept(self) -> None:
        class _Transport:
            connected = False

            async def start(self, handler: Any) -> None:
                return None

            async def stop(self) -> None:
                return None

        class _Events:
            def publish(self, *args: Any, **kwargs: Any) -> None:
                return None

        class _Persons:
            def core_map(self) -> dict[str, str]:
                return {}

        class _Runtime:
            config = None
            events = _Events()
            persons = _Persons()
            _log = logging.getLogger("test.gateway")

            async def submit_external(self, event: Any) -> None:
                return None

            async def wakeup(self) -> list[Any]:
                return []

            async def conversation_turn(self, **kwargs: Any) -> Any:
                return object()

            def commit_conversation_response(self, response: Any) -> Any:
                return SimpleNamespace(mode="silent", text="")

        config = SimpleNamespace(
            dedupe_ttl=600.0,
            dedupe_max_size=32,
            self_ids=["7"],
            max_pending_per_lane=5,
            shutdown_timeout=0.5,
        )
        gateway = OneBotGateway(
            _Runtime(), transport=_Transport(), config=config, logger=logging.getLogger("test")
        )
        gateway.state = ConnectionState.connected
        try:
            result = await gateway.handle_transport_event(
                {
                    "post_type": "message",
                    "message_type": "private",
                    "user_id": "10001",
                    "self_id": "7",
                    "message_id": "m1",
                    "message": [{"type": "text", "data": {"text": "在吗"}}],
                    "time": 1,
                }
            )
            assert result["accepted"] is True
            assert gateway.last_event_at > 0.0
            assert gateway.accepted == 1
        finally:
            await gateway.stop()

    async def test_websocket_adds_world_and_scheduler_topics(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr("app.web.routes.ops.STATUS_INTERVAL", 0.05)
        async with v1_server(tmp_path) as (client, _bot, _server):
            jar = aiohttp.CookieJar(unsafe=True)
            session = aiohttp.ClientSession(cookie_jar=jar)
            try:
                async with session.post(
                    client.base + "/login",
                    data={"username": "admin", "password": "pw123"},
                ) as response:
                    assert response.status == 200
                async with session.ws_connect(client.base + "/ws/events") as ws:
                    hello = json.loads((await ws.receive(timeout=2)).data)
                    assert hello["topic"] == "hello"
                    topics: set[str] = set()
                    deadline = time.monotonic() + 3.0
                    while time.monotonic() < deadline:
                        if {"status", "world", "scheduler"} <= topics:
                            break
                        message = json.loads((await asyncio.wait_for(ws.receive(), timeout=2)).data)
                        topics.add(message["topic"])
                        if message["topic"] == "status":
                            assert "qq" in message["data"]  # v1 扩展后的轻量版
                    assert {"status", "world", "scheduler"} <= topics
            finally:
                await session.close()
