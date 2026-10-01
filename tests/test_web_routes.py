"""WebUI route inventory (Task 18).

The admin server is being split into per-domain route modules; this snapshot is
what makes that split provably behaviour-preserving — every route the
single-file server served must still be served afterwards, and nothing may
appear or vanish silently. Route *handlers* may move between modules freely:
the table is method + path only.
"""

from __future__ import annotations

import socket

ROUTES = [
    "GET /",
    "GET /agent",
    "GET /agent/metrics",
    "GET /agent/policy",
    "GET /agent/simulator",
    "GET /agent/tasks",
    "GET /agent/tasks/{task_id}",
    "GET /behavior",
    "GET /character",
    "GET /character/export",
    "GET /config",
    "GET /conversation",
    "GET /conversation/continuity",
    "GET /credentials",
    "GET /expressions",
    "GET /groups",
    "GET /login",
    "GET /logs",
    "GET /memory",
    "GET /memory/consolidation",
    "GET /memory/correction",
    "GET /memory/detail/{memory_id}",
    "GET /memory/embeddings",
    "GET /memory/health",
    "GET /memory/retrieval-debug",
    "GET /memory/search",
    "GET /memory/timeline",
    "GET /models",
    "GET /prompts",
    "GET /runtime",
    "GET /sandbox",
    "GET /sandbox/bible",
    "GET /sandbox/chat",
    "GET /sandbox/inspectors",
    "GET /sandbox/needs",
    "GET /sandbox/trace",
    "GET /sessions",
    "GET /social",
    "GET /social/group",
    "GET /social/observations",
    "GET /social/policy",
    "GET /social/simulator",
    "GET /stickers",
    "GET /tools",
    "GET /tools/decision-debug",
    "GET /tools/executions",
    "GET /tools/metrics",
    "GET /tools/permissions",
    "GET /tools/{name}",
    "GET /topics",
    "GET /users",
    "GET /ws/events",
    "POST /agent/simulator",
    "POST /api/agent/control",
    "POST /api/credentials",
    "POST /api/credentials/delete",
    "POST /api/memory/{action}/{memory_id}",
    "POST /api/models/override",
    "POST /api/runtime/{action}",
    "POST /api/sandbox/control",
    "POST /api/sessions/clear",
    "POST /api/stickers/reindex",
    "POST /api/stickers/{action}/{sticker_id}",
    "POST /api/tools/cache/clear",
    "POST /api/tools/config",
    "POST /api/tools/permission",
    "POST /api/tools/permission/clear",
    "POST /api/tools/test",
    "POST /api/tools/toggle",
    "POST /api/topics/{action}/{topic_id}",
    "POST /behavior/preview",
    "POST /behavior/settings",
    "POST /behavior/test-response",
    "POST /behavior/trigger/{action}",
    "POST /character",
    "POST /character/import",
    "POST /character/import/confirm",
    "POST /character/state",
    "POST /config/ai",
    "POST /config/basic",
    "POST /config/onebot",
    "POST /config/raw",
    "POST /config/reset",
    "POST /config/test-provider",
    "POST /config/web",
    "POST /expressions/delete",
    "POST /expressions/toggle",
    "POST /groups/toggle",
    "POST /login",
    "POST /logout",
    "POST /memory/consolidation/run",
    "POST /memory/correction",
    "POST /memory/correction/apply",
    "POST /memory/embeddings/{action}",
    "POST /memory/retrieval-debug",
    "POST /memory/search",
    "POST /prompts",
    "POST /sandbox/simulate",
    "POST /social/analyze",
    "POST /social/policy",
    "POST /social/replay",
    "POST /tools/decision-debug",
    "POST /users",
]


class TestRouteInventory:
    async def test_admin_route_table_matches_the_snapshot(self, tmp_path) -> None:
        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from app.web.server import WebServer
        from tests.test_web_realtime import DummyAdapter

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": "sqlite:///" + str(tmp_path / "routes.db")},
            logging={"log_dir": str(tmp_path / "logs")},
            web={
                "enabled": True,
                "host": "127.0.0.1",
                "port": port,
                "username": "admin",
                "password": "pw123",
            },
        )
        bot = Bot(config, DummyAdapter())
        await bot.database.connect()
        await bot.character.start()
        server = WebServer(config.web, bot)
        await server.start()
        try:
            table = sorted(
                f"{route.method} {route.resource.canonical}"
                for route in server._runner.app.router.routes()
                if route.method not in ("HEAD",)
            )
        finally:
            await server.stop()
            await bot.shutdown()

        assert table == ROUTES, (
            "WebUI 路由表与快照不一致："
            f" 新增: {sorted(set(table) - set(ROUTES))} / 丢失: {sorted(set(ROUTES) - set(table))}"
        )
