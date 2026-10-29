"""WebUI tests: authentication (PBKDF2 + sessions), HTTP gate, AdminService."""

from __future__ import annotations

import asyncio
import re
from typing import Any

import aiohttp
import pytest

from app.config.settings import WebConfig
from app.web.auth import AuthService, hash_password, verify_password


class TestPasswordHashing:
    def test_hash_and_verify(self) -> None:
        stored = hash_password("s3cret!")
        assert "s3cret" not in stored
        assert verify_password("s3cret!", stored)
        assert not verify_password("wrong", stored)

    def test_malformed_hash_rejected(self) -> None:
        assert not verify_password("x", "garbage")


def make_auth(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    async def build():
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'web.db'}"))
        await database.connect()
        return AuthService(WebConfig(username="admin", password="pw123"), database), database

    return asyncio.get_event_loop_policy().new_event_loop()


class TestAuthService:
    async def test_bootstrap_login_flow(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'web1.db'}"))
        await database.connect()
        auth = AuthService(WebConfig(username="admin", password="pw123"), database)

        await auth.ensure_bootstrap_user()
        token = await auth.login("admin", "pw123")
        assert token is not None
        assert auth.validate(token)
        auth.logout(token)
        assert not auth.validate(token)

        assert await auth.login("admin", "wrong") is None
        assert await auth.login("ghost", "pw123") is None
        await database.close()

    async def test_bootstrap_uses_env_var(self, tmp_path, monkeypatch) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'web2.db'}"))
        await database.connect()
        monkeypatch.setenv("CATOOBOT_WEB_PASSWORD", "envpw")
        auth = AuthService(WebConfig(username="root"), database)
        await auth.ensure_bootstrap_user()
        assert await auth.login("root", "envpw") is not None
        await database.close()

    async def test_sessions_expire(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'web3.db'}"))
        await database.connect()
        clock = {"now": 1000.0}
        auth = AuthService(
            WebConfig(username="admin", password="pw"), database, clock=lambda: clock["now"]
        )
        await auth.ensure_bootstrap_user()
        token = await auth.login("admin", "pw")
        assert auth.validate(token)
        clock["now"] += 13 * 3600
        assert not auth.validate(token)
        await database.close()


@pytest.mark.asyncio
async def test_webui_http_gate(tmp_path, unused_tcp_port) -> None:
    """Real HTTP flow: unauthenticated request redirects to /login; after
    login the dashboard renders and the cookie grants access."""
    from app.adapters import Adapter
    from app.config.settings import AppConfig
    from app.core.bot import Bot

    class DummyAdapter(Adapter):
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

    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'http.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        web={
            "enabled": True,
            "host": "127.0.0.1",
            "port": unused_tcp_port,
            "username": "admin",
            "password": "pw123",
        },
    )
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    from app.web.server import WebServer

    web_server = WebServer(config.web, bot)
    await web_server.start()
    base = f"http://127.0.0.1:{unused_tcp_port}"
    # NOTE: aiohttp's cookie jar refuses cookies from IP hosts unless unsafe=True
    # (a client-side policy; real browsers accept host-only IP cookies fine).
    jar = aiohttp.CookieJar(unsafe=True)
    try:
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            # unauthenticated → redirected to login
            async with session.get(base + "/", allow_redirects=False) as resp:
                assert resp.status == 302
                assert resp.headers["Location"] == "/login"

            # v1.0 (W3): /login is the SPA shell; the form POST below stays the
            # legacy endpoint both front ends authenticate with.
            async with session.get(base + "/login") as resp:
                assert resp.status == 200
                assert 'id="app"' in await resp.text()

            # wrong password → not logged in
            async with session.post(
                base + "/login",
                data={"username": "admin", "password": "bad"},
                allow_redirects=False,
            ) as resp:
                assert resp.status == 302
            assert len(list(jar)) == 0  # no session cookie issued

            # correct login → session cookie issued
            async with session.post(
                base + "/login",
                data={"username": "admin", "password": "pw123"},
                allow_redirects=False,
            ) as resp:
                assert resp.status == 302
                assert "catoobot_session" in resp.cookies
            assert len(list(jar)) == 1

            # counters reach the dashboard tiles (Task 4): a fresh bot sits at
            # 0, so inject distinctive values and look for them in the HTML
            bot.metrics.inc("ai_requests", 812)
            bot.metrics.inc("memories_extracted", 719)

            # v1.0 (W3): / is the Vue shell …
            async with session.get(base + "/") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert 'id="app"' in body
                assert 'action="/login"' not in body

            # … and the v0.8 dashboard keeps its content at /legacy.
            async with session.get(base + "/legacy") as resp:
                assert resp.status == 200
                body = await resp.text()
                assert "限流 429" in body and "NapCat" in body
                assert "新增记忆" in body and "812" in body and "719" in body

            # every management page renders its own content
            async with session.get(base + "/legacy/character") as resp:
                body = await resp.text()
                assert resp.status == 200
                assert "Identity" in body and "行为规则" in body
                # the data-transfer card (Task 24) is on the page
                assert "导出数据" in body and "上传并预览" in body

            # export is a JSON download, not a page
            async with session.get(base + "/character/export") as resp:
                assert resp.status == 200
                assert resp.headers["Content-Type"].startswith("application/json")
                assert "attachment" in resp.headers["Content-Disposition"]
                exported = await resp.json()
                assert exported["format"] == "catoobot-character-export"

            async with session.get(base + "/legacy/memory") as resp:
                body = await resp.text()
                assert resp.status == 200
                assert "记忆" in body and "记忆修正" in body
                # the source filter (Task 21) is on the page
                assert "name='source'" in body and "她看过的图片" in body
            other_pages = (
                "/users",
                "/groups",
                "/sessions",
                "/models",
                "/prompts",
                "/logs",
                "/runtime",
                "/expressions",
            )
            for path in other_pages:
                async with session.get(base + path) as resp:
                    assert resp.status == 200, path
            # persona is editable through the WebUI form — with the CSRF token
            # the page injects (a real browser submits it back verbatim)
            async with session.get(base + "/legacy/character") as resp:
                page = await resp.text()
            csrf = re.search(r'name="csrf_token" value="([^"]+)"', page)
            assert csrf is not None, "the page must ship a CSRF token"
            async with session.post(
                base + "/legacy/character",
                data={
                    "name": "小夜",
                    "traits": "安静",
                    "likes": "月亮",
                    "rules": "不说教",
                    "emoji": "1",
                    "kaomoji": "0",
                    "csrf_token": csrf.group(1),
                },
            ) as resp:
                assert resp.status == 200
                assert "已保存并热加载" in await resp.text()
            assert bot.personas.persona.identity.name == "小夜"  # hot-applied
    finally:
        await web_server.stop()
        await bot.shutdown()


@pytest.mark.asyncio
async def test_memory_health_page_banners_extraction_failure(tmp_path, unused_tcp_port) -> None:
    """Task 25: a stuck extraction must be visible on /memory/health, not only
    in the log — the red banner renders exactly when health_note() has a note."""
    from app.adapters import Adapter
    from app.config.settings import AppConfig
    from app.core.bot import Bot

    class DummyAdapter(Adapter):
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

    class StubExtractor:
        def __init__(self, note: str | None) -> None:
            self.note = note

        def health_note(self) -> str | None:
            return self.note

        async def wait_idle(self) -> None:
            return None

    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'health.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        web={
            "enabled": True,
            "host": "127.0.0.1",
            "port": unused_tcp_port,
            "username": "admin",
            "password": "pw123",
        },
    )
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    from app.web.server import WebServer

    web_server = WebServer(config.web, bot)
    await web_server.start()
    base = f"http://127.0.0.1:{unused_tcp_port}"
    jar = aiohttp.CookieJar(unsafe=True)
    try:
        async with aiohttp.ClientSession(cookie_jar=jar) as session:
            async with session.post(
                base + "/login",
                data={"username": "admin", "password": "pw123"},
                allow_redirects=False,
            ) as resp:
                assert resp.status == 302

            bot.extractor = StubExtractor("最近连续 5 次记忆抽取零入库——见日志 [Memory.Extract]")
            async with session.get(base + "/legacy/memory/health") as resp:
                body = await resp.text()
                assert resp.status == 200
                assert "连续 5 次记忆抽取零入库" in body
                assert "<div class='flash flash-error'>" in body
                assert "抽取模型" in body

            bot.extractor = StubExtractor(None)
            async with session.get(base + "/legacy/memory/health") as resp:
                body = await resp.text()
                assert resp.status == 200
                assert "<div class='flash flash-error'>" not in body

            # Task 25 ⑤: AI enabled with nothing usable is a startup-level
            # problem, so the dashboard must say it too (this test config has
            # AI off, hence no banner until it is switched on).
            async with session.get(base + "/legacy") as resp:
                assert "<div class='flash flash-error'>" not in await resp.text()
            bot.config.ai.enabled = True
            bot.ai.enabled = False
            async with session.get(base + "/legacy") as resp:
                body = await resp.text()
                assert resp.status == 200
                assert "AI 已启用，但没有任何可用模型" in body
                assert "<div class='flash flash-error'>" in body
            bot.ai.enabled = True
            async with session.get(base + "/legacy") as resp:
                assert "<div class='flash flash-error'>" not in await resp.text()
    finally:
        await web_server.stop()
        await bot.shutdown()


class TestAdminService:
    async def test_persona_save_and_hot_apply(self, tmp_path) -> None:
        from app.adapters import Adapter
        from app.config.settings import AppConfig
        from app.core.bot import Bot

        class DummyAdapter(Adapter):
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

        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": f"sqlite:///{tmp_path / 'admin.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
        )
        bot = Bot(config, DummyAdapter())
        await bot.database.connect()
        from app.web.services.admin import AdminService

        admin = AdminService(bot)
        await admin.save_persona({"identity": {"name": "小夜"}, "system_prompt": "测试。"})
        persona = await admin.get_persona()
        assert persona.identity.name == "小夜"
        assert bot.personas.persona.identity.name == "小夜"  # hot-applied

        # invalid data rejected, previous persona kept
        with pytest.raises(ValueError):
            await admin.save_persona({"identity": None})
        assert bot.personas.persona.identity.name == "小夜"

        # model overrides hot-apply
        assert not bot.ai.enabled  # no models in this config
        assert await admin.model_overrides() == {}
        await bot.shutdown()

    async def test_group_toggle_reflects_in_list(self, tmp_path) -> None:
        """The per-group participation switch must actually change what the
        Groups page shows (regression: it read ``groups`` instead of
        ``group_profiles``, so the ✓/✗ never moved)."""
        from app.adapters import Adapter
        from app.config.settings import AppConfig
        from app.core.bot import Bot

        class DummyAdapter(Adapter):
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

        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": f"sqlite:///{tmp_path / 'groups.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
        )
        bot = Bot(config, DummyAdapter())
        await bot.database.connect()
        await bot.database.upsert_group("456", "测试群", 1700000000)

        from app.web.services.admin import AdminService

        admin = AdminService(bot)
        groups = await admin.list_groups()
        assert groups[0]["group_id"] == "456"
        assert groups[0]["participation_enabled"] == 1  # default on

        # simulate the toggle: flip it to off
        await bot.database.execute(
            """INSERT INTO group_profiles (group_id, participation_enabled)
               VALUES ('456', 0)
               ON CONFLICT(group_id) DO UPDATE SET
                   participation_enabled=excluded.participation_enabled"""
        )
        groups = await admin.list_groups()
        assert groups[0]["participation_enabled"] == 0
        await bot.shutdown()
