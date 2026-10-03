"""W6 切换面：v1 默认 + v0.8 回滚 + 旧入口别名（§13/§14/§15/§116）。

三件事必须同时成立：

* 出厂默认 `web.version == "v1"`，四个共享入口（/character、/social、
  /memory、/memory/health）交给 SPA，旧控制台整体搬到 /legacy/*；
* `web.version = "v0.8"` 时旧 UI 回到 `/`、`/login` 与四个入口，但
  `/api/v1/*` 照常工作（API 不依赖 UI 开关）；
* 两种模式共用同一个会话 Cookie，HTML 互不串味（SPA 无旧控制台标记，
  旧页面无 SPA 根节点）。
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config.settings import AppConfig
from app.web.routes.base import SESSION_COOKIE
from app.web.spa import spa_ready
from tests.api_harness import ApiClient, api_server, error_code

#: 真实 .env 里的名字在测试进程里必须无效（配置写路径会触发 load_dotenv）
_ENV_NAMES = ("CATOOBOT_WEB_PASSWORD",)

SPA_PATHS = ("/", "/character", "/social", "/memory", "/memory/health")
LEGACY_ALIASES = (
    "/legacy",
    "/legacy/character",
    "/legacy/social",
    "/legacy/memory",
    "/legacy/memory/health",
)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("app.config.settings.load_dotenv", lambda *args, **kwargs: None)
    for name in _ENV_NAMES:
        os.environ.pop(name, None)
    yield
    for name in _ENV_NAMES:
        os.environ.pop(name, None)


def has_session_cookie(client: ApiClient) -> bool:
    """两种 UI 用的是同一个会话 Cookie 名（AuthService 只有一套）。"""
    return any(cookie.key == SESSION_COOKIE for cookie in client._session.cookie_jar)


class TestShippedDefault:
    def test_default_web_version_is_v1(self) -> None:
        config = AppConfig(web={"enabled": True})
        assert config.web.version == "v1"


class TestV1Mode:
    async def test_login_page_is_the_spa_shell_for_anonymous(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, _server):
            status, html = await client.request("GET", "/login", raw=True)
            assert status == 200, "v1 的 /login 必须无需会话"
            assert 'id="app"' in html, "v1 的 /login 必须返回 SPA 外壳"
            assert "限流 429" not in html, "SPA 外壳不得包含旧控制台标记"

    async def test_shared_entry_points_serve_the_spa_for_an_operator(self, tmp_path: Path) -> None:
        assert spa_ready(), "webui/dist 未构建：SPA 断言无从谈起"
        async with api_server(tmp_path) as (client, _bot, _server):
            status, _ = await client.login()
            assert status == 200 and has_session_cookie(client)
            for path in SPA_PATHS:
                status, html = await client.request("GET", path, raw=True)
                assert status == 200, f"{path} 应可访问（已登录）"
                assert 'id="app"' in html, f"v1 模式下 {path} 应返回 SPA 外壳"
                assert "限流 429" not in html, f"SPA 外壳不得包含旧控制台标记：{path}"

    async def test_the_v08_console_keeps_its_own_aliases(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, _server):
            status, _ = await client.login()
            assert status == 200
            for path in LEGACY_ALIASES:
                status, html = await client.request("GET", path, raw=True)
                assert status == 200, f"{path} 必须一直可用（回滚面）"
                assert 'id="app"' not in html, f"{path} 不得返回 SPA 外壳"
                assert 'class="topbar"' in html, f"{path} 应返回 v0.8 控制台页面"

    async def test_overview_api_still_answers_json(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get("/api/v1/overview", csrf=False)
            assert status == 401 and error_code(payload) == "auth.unauthorized"
            await client.login()
            status, payload = await client.get("/api/v1/overview")
            assert status == 200 and payload["ok"] is True and isinstance(payload["data"], dict)


class TestV08Rollback:
    async def test_rollback_serves_the_legacy_ui_but_keeps_the_v1_api(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, server):
            # 回滚开关：等价于 overrides 里写 web.version=v0.8；分发器按请求读取
            server._config.version = "v0.8"

            status, login_html = await client.request("GET", "/login", raw=True)
            assert status == 200 and 'action="/login"' in login_html
            assert 'id="app"' not in login_html, "v0.8 的登录页不得是 SPA"

            # JSON 登录仍然可用：API 不依赖 UI 开关
            status, _ = await client.login()
            assert status == 200 and has_session_cookie(client)

            status, home = await client.request("GET", "/", raw=True)
            assert status == 200
            assert "限流 429" in home, "v0.8 的 / 应回到旧仪表盘"
            assert 'id="app"' not in home

            for path in ("/character", "/social", "/memory", "/memory/health"):
                status, html = await client.request("GET", path, raw=True)
                assert status == 200
                assert 'id="app"' not in html, f"v0.8 下 {path} 应是旧页面"
                assert 'class="topbar"' in html, f"v0.8 下 {path} 应是旧控制台页面"

            # 同一个会话 Cookie：HTML 与 JSON API 都能用
            status, overview = await client.get("/api/v1/overview")
            assert status == 200 and overview["ok"] is True

    async def test_four_aliases_stay_legacy_in_both_modes(self, tmp_path: Path) -> None:
        """无论 UI 开关在哪一档，/legacy* 始终是 v0.8 控制台。"""
        async with api_server(tmp_path) as (client, _bot, server):
            await client.login()
            for mode in ("v1", "v0.8"):
                server._config.version = mode
                for path in LEGACY_ALIASES:
                    status, html = await client.request("GET", path, raw=True)
                    assert status == 200, f"[{mode}] {path} 别名必须可用"
                    assert 'id="app"' not in html, f"[{mode}] {path} 不得返回 SPA"
