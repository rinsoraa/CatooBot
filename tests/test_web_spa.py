"""WebUI v1.0 SPA 托管（W3 §40-§43）。

验证四件事：v1 接管 `/` 与 `/login`、旧控制台在 `/legacy` 完好、
`/api/v1` 与 `/ws` 永不被 fallback 吃掉、`web.version` 能一键回滚。
"""

from __future__ import annotations

from pathlib import Path

import aiohttp
import pytest

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.web import spa as spa_module
from app.web.server import WebServer
from tests.api_harness import free_port
from tests.test_web_realtime import DummyAdapter


def _dist_assets() -> list[Path]:
    assets = spa_module.spa_dir() / "assets"
    return sorted(assets.glob("*")) if assets.is_dir() else []


class TestSpaServing:
    async def _serve(self, tmp_path: Path, *, version: str = "v1") -> tuple[Bot, WebServer, str]:
        port = free_port()
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": "sqlite:///" + str(tmp_path / "spa.db")},
            logging={"log_dir": str(tmp_path / "logs"), "level": "WARNING"},
            web={
                "enabled": True,
                "host": "127.0.0.1",
                "port": port,
                "username": "admin",
                "password": "pw123",
                "version": version,
            },
        )
        bot = Bot(config, DummyAdapter())
        await bot.database.connect()
        await bot.character.start()
        server = WebServer(config.web, bot)
        await server.start()
        return bot, server, f"http://127.0.0.1:{port}"

    @staticmethod
    async def _login(session: aiohttp.ClientSession, base: str) -> None:
        async with session.post(
            base + "/login",
            data={"username": "admin", "password": "pw123"},
            allow_redirects=False,
        ) as response:
            assert response.status == 302

    async def test_root_serves_the_v1_shell_for_a_logged_in_operator(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.get(base + "/") as response:
                    assert response.status == 200
                    body = await response.text()
                    assert 'id="app"' in body
                    assert "/assets/" in body
                    # the v1.0 shell is not the v0.8 dashboard
                    assert "限流 429" not in body
                # a deep link a future SPA route will own
                async with session.get(base + "/ai") as response:
                    assert response.status == 200
                    assert 'id="app"' in await response.text()
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_login_page_is_the_v1_shell_and_needs_no_session(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(base + "/login") as response,
            ):
                assert response.status == 200
                body = await response.text()
                assert 'id="app"' in body
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_legacy_still_serves_the_v08_console(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.get(base + "/legacy") as response:
                    assert response.status == 200
                    body = await response.text()
                    assert "限流 429" in body and "NapCat" in body
                # the old pages keep their own URLs untouched
                async with session.get(base + "/character") as response:
                    assert response.status == 200
                    assert "行为规则" in await response.text()
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_api_namespace_is_never_shadowed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                async with session.get(base + "/api/v1/overview") as response:
                    assert response.status == 401  # anonymous: the API answers, the SPA does not
                    payload = await response.json()
                    assert payload["error"]["code"] == "auth.unauthorized"
                await self._login(session, base)
                async with session.get(base + "/api/does-not-exist") as response:
                    assert response.status == 404
                    payload = await response.json()
                    assert payload["error"]["code"] == "request.unknown_endpoint"
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_hashed_assets_are_immutable_cache(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        assets = _dist_assets()
        if not assets:
            pytest.skip("frontend not built in this checkout")
        name = assets[0].name
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.get(f"{base}/assets/{name}") as response:
                    assert response.status == 200
                    assert "immutable" in response.headers.get("Cache-Control", "")
                    assert len(await response.read()) > 0
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_asset_path_traversal_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.get(base + "/assets/..%2F..%2Fapp%2Fmain.py") as response:
                    assert response.status == 404
                    body = await response.text()
                    assert "VERSION =" not in body
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_version_switch_rolls_back_to_v08(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path, version="v0.8")
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                async with session.get(base + "/login") as response:
                    assert response.status == 200
                    assert 'action="/login"' in await response.text()
                await self._login(session, base)
                async with session.get(base + "/") as response:
                    assert response.status == 200
                    assert "限流 429" in await response.text()
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_missing_dist_explains_the_build_step(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(spa_module, "SPA_DIR", tmp_path / "no-dist")
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.get(base + "/") as response:
                    assert response.status == 503
                    body = await response.text()
                    assert "npm run build" in body
                async with session.get(base + "/legacy") as response:
                    assert response.status == 200  # the console is still reachable
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_favicon_is_quiet_when_absent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot, server, base = await self._serve(tmp_path)
        try:
            async with (
                aiohttp.ClientSession() as session,
                session.get(base + "/favicon.ico") as response,
            ):
                assert response.status in (200, 204)
        finally:
            await server.stop()
            await bot.shutdown()


class TestSpaGuards:
    def test_safe_asset_refuses_absolute_and_parent_paths(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(spa_module, "SPA_DIR", tmp_path)
        (tmp_path / "ok.txt").write_text("fine", encoding="utf-8")
        assert spa_module.safe_asset("ok.txt") is not None
        assert spa_module.safe_asset("../secret.txt") is None
        assert spa_module.safe_asset("..\\secret.txt") is None
        assert spa_module.safe_asset("missing.txt") is None

    def test_index_availability_follows_the_dist_directory(self, tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setattr(spa_module, "SPA_DIR", tmp_path)
        assert spa_module.spa_ready() is False
        (tmp_path / "index.html").write_text("<div id='app'></div>", encoding="utf-8")
        assert spa_module.spa_ready() is True
        assert spa_module.index_file() == tmp_path / "index.html"
