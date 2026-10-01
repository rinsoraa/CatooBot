"""WebUI security hardening (Task 18): login throttling + CSRF.

Both close real gaps from the audit — an unlimited password oracle and
state-changing POSTs any site could trigger — and both are deliberately
boring: no storage, no dependency, injectable clock.
"""

from __future__ import annotations

import re

import aiohttp
import pytest

from app.web.security import (
    LoginThrottle,
    csrf_token,
    inject_csrf,
    set_csrf_token,
)


class TestLoginThrottle:
    def _throttle(self, *, clock=None, metrics=None):  # type: ignore[no-untyped-def]
        now = clock or [1000.0]
        return LoginThrottle(
            max_attempts=5, window_seconds=300.0, clock=lambda: now[0], metrics=metrics
        ), now

    def test_allows_until_the_limit_then_blocks(self) -> None:
        throttle, _now = self._throttle()
        key = "1.2.3.4|admin"
        for _ in range(5):
            assert throttle.allowed(key) is True
            throttle.record_failure(key)
        assert throttle.allowed(key) is False
        assert 0 < throttle.retry_after(key) <= 300.0

    def test_window_expiry_frees_the_key(self) -> None:
        throttle, now = self._throttle()
        key = "1.2.3.4|admin"
        for _ in range(5):
            throttle.record_failure(key)
        assert throttle.allowed(key) is False
        now[0] += 301
        assert throttle.allowed(key) is True

    def test_keys_are_independent_and_reset_clears(self) -> None:
        throttle, _now = self._throttle()
        for _ in range(5):
            throttle.record_failure("a|admin")
        assert throttle.allowed("b|admin") is True  # another client is unaffected
        throttle.reset("a|admin")
        assert throttle.allowed("a|admin") is True

    def test_metric_counts_once_the_limit_is_reached(self) -> None:
        class FakeMetrics:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def inc(self, key: str, amount: int = 1) -> None:
                self.keys.append(key)

        metrics = FakeMetrics()
        throttle, _now = self._throttle(metrics=metrics)
        for _ in range(5):
            throttle.record_failure("k")
        assert metrics.keys == ["login_throttled"]
        assert throttle.stats()["max_attempts"] == 5


class TestCsrfTokens:
    def test_token_is_stable_per_session_and_different_across_sessions(self) -> None:
        assert csrf_token("session-a") == csrf_token("session-a")
        assert csrf_token("session-a") != csrf_token("session-b")
        assert csrf_token(None) == ""

    def test_injection_targets_post_forms_only(self) -> None:
        set_csrf_token("session-a")
        try:
            html = (
                '<form method="get" action="/x"><input></form>'
                '<form method="post" action="/y"><input></form>'
                '<form action="/z" method="POST"><input></form>'
            )
            injected = inject_csrf(html)
        finally:
            set_csrf_token(None)
        assert "/x" in injected and 'name="csrf_token"' not in injected.split('action="/y"')[0]
        assert injected.count('name="csrf_token"') == 2  # both POST forms, in any order
        assert csrf_token("session-a") in injected

    def test_injection_is_a_noop_without_a_session(self) -> None:
        set_csrf_token(None)
        html = '<form method="post" action="/y"></form>'
        assert inject_csrf(html) == html

    def test_injection_handles_single_quoted_and_unquoted_method(self) -> None:
        """P0：method='post'（单引号）曾不被注入器识别，提交必 403。"""
        set_csrf_token("session-a")
        try:
            html = (
                "<form method='post' action='/a'><input></form>"
                "<form method=post action='/b'><input></form>"
                "<form method='POST' action='/c'><input></form>"
            )
            injected = inject_csrf(html)
        finally:
            set_csrf_token(None)
        assert injected.count('name="csrf_token"') == 3


class TestHttpGate:
    async def _serve(self, tmp_path):  # type: ignore[no-untyped-def]
        import socket

        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from app.web.server import WebServer
        from tests.test_web_realtime import DummyAdapter  # noqa: I001 - test-local import

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": "sqlite:///" + str(tmp_path / "sec.db")},
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
        return bot, server, f"http://127.0.0.1:{port}"

    async def _login(self, session, base: str) -> str:  # type: ignore[no-untyped-def]
        await session.post(base + "/login", data={"username": "admin", "password": "pw123"})
        async with session.get(base + "/character") as resp:
            page = await resp.text()
        match = re.search(r'name="csrf_token" value="([^"]+)"', page)
        assert match is not None
        return match.group(1)

    async def test_sixth_failed_login_is_throttled(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                statuses = []
                for _ in range(6):
                    async with session.post(
                        base + "/login",
                        data={"username": "admin", "password": "wrong"},
                        allow_redirects=False,
                    ) as resp:
                        statuses.append(resp.status)
                assert statuses[:5] == [302] * 5  # normal rejects
                assert statuses[5] == 429  # …then the oracle closes
                assert bot.metrics.get("login_throttled") == 1
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_a_good_login_clears_the_counter(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                for _ in range(3):
                    await session.post(
                        base + "/login", data={"username": "admin", "password": "wrong"}
                    )
                async with session.post(
                    base + "/login",
                    data={"username": "admin", "password": "pw123"},
                    allow_redirects=False,
                ) as resp:
                    assert resp.status == 302
                # the counter is gone: five more failures would be needed to trip it
                async with session.post(
                    base + "/login",
                    data={"username": "admin", "password": "wrong"},
                    allow_redirects=False,
                ) as resp:
                    assert resp.status == 302
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_post_without_a_token_is_rejected(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                async with session.post(
                    base + "/character", data={"name": "小夜", "csrf_token": "bogus"}
                ) as resp:
                    assert resp.status == 403
                assert bot.metrics.get("csrf_rejected") >= 1
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_post_with_the_page_token_is_accepted(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                token = await self._login(session, base)
                async with session.post(
                    base + "/character",
                    data={
                        "name": "小夜",
                        "traits": "安静",
                        "likes": "月亮",
                        "rules": "不说教",
                        "emoji": "1",
                        "kaomoji": "0",
                        "csrf_token": token,
                    },
                ) as resp:
                    assert resp.status == 200
                    assert "已保存并热加载" in await resp.text()
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_json_clients_may_use_the_header(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                token = await self._login(session, base)
                headers = {"X-CSRF-Token": token, "Content-Type": "application/json"}
                async with session.post(
                    base + "/api/runtime/reload_plugins", headers=headers, data="{}"
                ) as resp:
                    assert resp.status == 200
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_login_page_itself_needs_no_token(self, tmp_path) -> None:
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with (
                aiohttp.ClientSession(cookie_jar=jar) as session,
                session.post(
                    base + "/login",
                    data={"username": "admin", "password": "pw123"},
                    allow_redirects=False,
                ) as resp,
            ):
                assert resp.status == 302
        finally:
            await server.stop()
            await bot.shutdown()

    async def test_every_post_form_has_a_csrf_field(self, tmp_path) -> None:
        """P0 回归：遍历所有返回 HTML 的页面处理器，每个 POST 表单都要有
        csrf_token 隐藏字段——单引号 method='post' 曾绕过注入器，提交必 403。"""
        bot, server, base = await self._serve(tmp_path)
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await self._login(session, base)
                from tests.test_web_routes import ROUTES

                checked = 0
                missing: list[str] = []
                for entry in ROUTES:
                    if not entry.startswith("GET "):
                        continue
                    path = entry[4:]
                    if "{" in path or path.startswith("/ws/") or path == "/login":
                        continue
                    async with session.get(base + path) as resp:
                        if resp.status != 200:
                            continue
                        if "text/html" not in (resp.headers.get("Content-Type") or ""):
                            continue
                        body = await resp.text()
                    for match in re.finditer(r"<form\b[^>]*>", body, re.IGNORECASE):
                        if not re.search(r'method\s*=\s*["\']?post', match.group(0), re.IGNORECASE):
                            continue
                        end = body.find("</form>", match.end())
                        form = body[match.start() : end if end != -1 else match.end()]
                        checked += 1
                        if "csrf_token" not in form:
                            missing.append(path)
                assert not missing, f"这些页面的 POST 表单缺 csrf_token：{missing}"
                assert checked >= 10, f"只覆盖到 {checked} 个 POST 表单，测试强度不足"
        finally:
            await server.stop()
            await bot.shutdown()


def test_throttle_stats_shape() -> None:
    throttle = LoginThrottle()
    stats = throttle.stats()
    assert stats == {"tracked_keys": 0, "max_attempts": 5, "window_seconds": 300.0}
    with pytest.raises(ValueError):
        LoginThrottle(max_attempts="five")  # type: ignore[arg-type]
