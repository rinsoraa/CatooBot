"""WebUI world-page tests (v0.8 §50-§56): the only management surface.

Spec coverage: every /world page renders, routine settings persist and apply
hot, goals can be created/advanced from the browser, and the control endpoint
pauses/resumes the world. Nothing here ever touches QQ.
"""

from __future__ import annotations

import aiohttp
import pytest

from app.config.settings import AppConfig
from app.core.bot import Bot


class DummyAdapter:
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

    async def call_api(self, action, params=None, timeout=None):  # type: ignore[no-untyped-def]
        return {}


async def make_web_bot(tmp_path, port: int, **world_overrides) -> Bot:
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'world_web.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        web={
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "username": "admin",
            "password": "pw123",
        },
        world=world_overrides or {"enabled": True},
        sandbox={"enabled": False},
    )
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    if bot.world is not None:
        await bot.world.state.load()
    return bot


@pytest.fixture
async def world_web(tmp_path):
    import socket

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    bot = await make_web_bot(tmp_path, port)
    from app.web.server import WebServer

    server = WebServer(bot.config.web, bot)
    await server.start()
    jar = aiohttp.CookieJar(unsafe=True)
    session = aiohttp.ClientSession(cookie_jar=jar)
    await session.post(
        f"http://127.0.0.1:{port}/login", data={"username": "admin", "password": "pw123"}
    )
    try:
        yield bot, session, f"http://127.0.0.1:{port}"
    finally:
        await session.close()
        await server.stop()
        await bot.database.close()


class TestPages:
    async def test_world_overview(self, world_web) -> None:
        bot, session, base = world_web
        await bot.world.state.change("manual", activity="打游戏", location="房间")
        async with session.get(f"{base}/world") as response:
            assert response.status == 200
            body = await response.text()
        assert "打游戏" in body
        assert "世界控制" in body
        assert "后台生活 ≠ 后台发消息" in body

    async def test_world_pages_all_render(self, world_web) -> None:
        _bot, session, base = world_web
        for path in (
            "/world",
            "/world/timeline",
            "/world/routine",
            "/world/goals",
            "/world/events",
            "/world/simulator",
            "/world/replay",
            "/world/health",
        ):
            async with session.get(f"{base}{path}") as response:
                assert response.status == 200, path
                body = await response.text()
                assert "CatooBot" in body            # themed shell rendered
                assert "data-mode=" in body and "sidebar" in body

    async def test_simulator_dry_run(self, world_web) -> None:
        _bot, session, base = world_web
        async with session.post(
            f"{base}/world/simulator",
            data={"days": "1", "step_minutes": "60", "seed": "5"},
        ) as response:
            body = await response.text()
            assert response.status == 200
        assert "预演结果" in body
        assert "不会发送任何消息" in body


class TestRoutineSettings:
    async def test_save_routine_applies_hot(self, world_web) -> None:
        bot, session, base = world_web
        data = {
            "routine_enabled": "1",
            "transition_minutes": "5",
            "tick_seconds": "45",
            "period_afternoon": "听歌、发呆",
            "period_evening": "打游戏",
            "ambient_enabled": "1",
            "ambient_min_interval": "30",
            "ambient_max_per_day": "2",
            "events_max_per_hour": "8",
            "events_max_per_day": "30",
            "goals_enabled": "1",
            "auto_advance": "1",
            "advance_interval_hours": "6",
            "max_progress_per_day": "2",
            "max_active": "4",
            "messaging_enabled": "1",
            "background_messages_per_day": "2",
            "pending_ttl_minutes": "60",
            "missed_event_policy": "catch_up",
        }
        async with session.post(f"{base}/world/routine", data=data) as response:
            assert response.status == 200
            body = await response.text()
        assert "设置已保存并生效" in body
        assert bot.world.routine.labels_for("afternoon") == ["听歌", "发呆"]
        assert bot.world.config.tick_seconds == 45
        assert bot.world.config.routine.transition_minutes == 5
        assert bot.world.config.messaging.max_background_messages_per_day == 2
        assert bot.world.config.events.max_per_day == 30
        assert bot.world.ambient._config.max_per_day == 2  # noqa: SLF001 - hot swap check
        assert bot.config.world.missed_event_policy == "catch_up"

    async def test_overrides_survive_a_restart(self, world_web, tmp_path) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/world/routine",
            data={"routine_enabled": "1", "period_evening": "看电影", "transition_minutes": "9"},
        ) as response:
            assert response.status == 200

        from app.web.services.world import WorldAdminService

        service = WorldAdminService(bot)
        overrides = await service.load_overrides()
        assert overrides["routine"]["periods"]["evening"] == ["看电影"]
        # a fresh bot over the same database restores them
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        other = Database(DatabaseConfig(url=bot.config.database.url))
        await other.connect()
        stored = await other.get_setting_json("world_overrides")
        assert stored["routine"]["transition_minutes"] == 9
        await other.close()


class TestGoalsPage:
    async def test_create_and_advance_goal(self, world_web) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/world/goals",
            data={
                "action": "create",
                "name": "把房子盖完",
                "description": "在 Minecraft 里建房子",
                "milestones": "打地基\n搭墙",
                "next_action": "打地基",
                "priority": "2",
            },
        ) as response:
            body = await response.text()
            assert response.status == 200
        assert "created" in body
        goals = await bot.world.goals.all(status="active")
        assert [g.name for g in goals] == ["把房子盖完"]

        async with session.post(
            f"{base}/world/goals",
            data={"action": "advance", "goal_id": goals[0].goal_id, "amount": "0.2"},
        ) as response:
            body = await response.text()
        assert "advanced" in body
        reloaded = await bot.world.goals.get(goals[0].goal_id)
        assert reloaded.progress_percent == 20

    async def test_goal_limit_error_is_shown(self, world_web) -> None:
        bot, session, base = world_web
        bot.world.goals._config.max_active = 1  # noqa: SLF001 - force the limit
        await bot.world.goals.create("唯一的目标")
        async with session.post(
            f"{base}/world/goals", data={"action": "create", "name": "第二个目标"}
        ) as response:
            body = await response.text()
        assert "error" in body

    async def test_project_creation(self, world_web) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/world/goals",
            data={"action": "project", "name": "我的 Minecraft 世界"},
        ) as response:
            body = await response.text()
        assert "project created" in body
        assert [p.name for p in await bot.world.goals.projects()] == ["我的 Minecraft 世界"]


class TestControlEndpoint:
    async def test_pause_and_resume(self, world_web) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/api/world/control", data={"action": "pause"}
        ) as response:
            body = await response.text()
        assert "paused" in body and bot.world.paused is True
        async with session.post(
            f"{base}/api/world/control", data={"action": "resume"}
        ) as response:
            body = await response.text()
        assert "resumed" in body and bot.world.paused is False

    async def test_rest_mode_toggle(self, world_web) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/api/world/control", data={"action": "rest_on"}
        ) as response:
            body = await response.text()
        assert "rest mode on" in body and bot.world.rest_mode is True

    async def test_manual_tick_and_snapshot(self, world_web) -> None:
        bot, session, base = world_web
        async with session.post(
            f"{base}/api/world/control", data={"action": "tick"}
        ) as response:
            body = await response.text()
        assert "one world tick" in body and bot.world.ticks >= 1
        async with session.post(
            f"{base}/api/world/control", data={"action": "snapshot"}
        ) as response:
            body = await response.text()
        assert "snapshot saved" in body
        row = await bot.database.fetchone("SELECT COUNT(*) AS n FROM world_snapshots")
        assert row["n"] >= 1

    async def test_clear_pending(self, world_web) -> None:
        bot, session, base = world_web
        await bot.world.messaging_queue.add(
            scope_key="private:1", user_id="1", text="待发的问候"
        )
        async with session.post(
            f"{base}/api/world/control", data={"action": "clear_pending"}
        ) as response:
            body = await response.text()
        assert "cleared 1" in body
        assert await bot.world.messaging_queue.items() == []


class TestDisabledWorld:
    async def test_pages_render_without_world(self, tmp_path) -> None:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        bot = await make_web_bot(tmp_path, port, enabled=False)
        from app.web.server import WebServer

        server = WebServer(bot.config.web, bot)
        await server.start()
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await session.post(
                    f"http://127.0.0.1:{port}/login",
                    data={"username": "admin", "password": "pw123"},
                )
                async with session.get(f"http://127.0.0.1:{port}/world") as response:
                    body = await response.text()
                    assert response.status == 200
                assert "未启用" in body
        finally:
            await server.stop()
            await bot.database.close()

    async def test_control_on_disabled_world_reports_it(self, tmp_path) -> None:
        import socket

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        bot = await make_web_bot(tmp_path, port, enabled=False)
        from app.web.services.world import WorldAdminService

        service = WorldAdminService(bot)
        assert await service.control("pause") == "world disabled"
        assert (await service.dashboard())["enabled"] is False
        await bot.database.close()
