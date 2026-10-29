"""W6 安全矩阵：真实路由表上的「未登录 401 / 无 CSRF 403」契约（§38）。

不 mock 中间件，也不逐路由手写用例：测试从 `server._runner.app.router.routes()`
枚举 `/api/v1` 的每一条真实路由，逐条发真实 HTTP。这样新加的路由只要忘了
认证/CSRF 就会被矩阵自动抓住，而不是等下一次审计。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.web.server import WebServer
from tests.api_harness import ApiClient, api_server, error_code

API_PREFIX = "/api/v1"

#: 真实 .env 里的名字在测试进程里必须无效（配置路径可能触发 load_dotenv）
_ENV_NAMES = ("CATOOBOT_WEB_PASSWORD",)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("app.config.settings.load_dotenv", lambda *args, **kwargs: None)
    for name in _ENV_NAMES:
        os.environ.pop(name, None)
    yield
    for name in _ENV_NAMES:
        os.environ.pop(name, None)


LOGIN_METHOD = "POST"
LOGIN_PATH = f"{API_PREFIX}/session"
WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
READ_METHODS = frozenset({"GET"})

#: 路径参数 → 合理值。目标是否存在不影响结论：认证/CSRF 都先于 handler。
PARAM_VALUES: dict[str, str] = {
    "name": "nope",
    "memory_id": "999999",
    "person_id": "999999",
    "sticker_id": "x",
    "role": "chat",
    "task_id": "t",
    "topic_id": "t",
    "commitment_id": "c",
    "action": "pause",
    "revision": "1",
}
#: 这里未列出的参数（{pattern_id}/{domain}/{ref}/{group_id} 等）给中性值
DEFAULT_PARAM = "x"


def concretize(path: str) -> str:
    """把 ``/memories/{memory_id}/{action}`` 变成可请求的具体路径。"""
    for param, value in PARAM_VALUES.items():
        path = path.replace("{" + param + "}", value)
    while "{" in path:  # 未列出的参数：取值不会影响 401/403 的结论
        start = path.index("{")
        end = path.index("}", start)
        path = path[:start] + DEFAULT_PARAM + path[end + 1 :]
    return path


#: 服务间 Bearer 通道（Minecraft Bridge 回调，契约 §7.5）：不走会话 Cookie，
#: 也就不适用浏览器的 CSRF 模型——它由 Authorization: Bearer 共享密钥把守，
#: 且在会话/CSRF 检查之前短路（app/web/server.py 的 _auth_middleware）。
BEARER_CHANNEL_PATHS = frozenset({f"{API_PREFIX}/minecraft/events"})


def v1_routes(server: WebServer) -> list[tuple[str, str]]:
    """(method, canonical_path) —— 真实路由表，排除登录与 Bearer 豁免路径。"""
    assert server._runner is not None
    rows = {
        (route.method, route.resource.canonical)
        for route in server._runner.app.router.routes()
        if route.method in WRITE_METHODS | READ_METHODS
        and route.resource.canonical.startswith(API_PREFIX)
    }
    rows.discard((LOGIN_METHOD, LOGIN_PATH))
    rows.discard(("POST", f"{API_PREFIX}/minecraft/events"))
    return sorted(rows, key=lambda item: (item[1], item[0]))


def state_fingerprint(bot: Any) -> str:
    """配置 + 沙盒修订号，用来证明被挡下的写请求没有触碰状态。"""
    sandbox = getattr(bot, "sandbox", None)
    revisions = ""
    if sandbox is not None:
        revisions = (
            f"{getattr(sandbox, 'world_revision', '')}:{getattr(sandbox, 'cognitive_revision', '')}"
        )
    payload = f"{bot.config.model_dump_json()}|{revisions}"
    return hashlib.sha256(payload.encode()).hexdigest()


async def _login_ok(client: ApiClient) -> None:
    status, payload = await client.login()
    assert status == 200, f"测试自身的登录必须成功：{status} {payload}"
    assert client.csrf, "登录后必须拿到 CSRF token（写接口测试需要）"


class TestAnonymousMatrix:
    """未登录访客：任何 /api/v1 路由都只能是 401 JSON，不能是 302/HTML/5xx。"""

    async def test_every_v1_route_requires_a_session(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, server):
            routes = v1_routes(server)
            assert len(routes) >= 80, f"路由枚举似乎不完整：只拿到 {len(routes)} 条"
            problems: list[str] = []
            for method, path in routes:
                target = concretize(path)
                status, payload = await client.request(
                    method, target, body={} if method in WRITE_METHODS else None, csrf=False
                )
                if (
                    status != 401
                    or payload.get("ok") is not False
                    or error_code(payload) != "auth.unauthorized"
                ):
                    problems.append(f"{method} {target} -> {status} {error_code(payload)}")
            assert not problems, (
                "未登录访问必须 401 auth.unauthorized（绝不 302/HTML）：\n" + "\n".join(problems)
            )

    async def test_no_v1_route_answers_5xx_to_an_anonymous_request(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, _bot, server):
            problems: list[str] = []
            for method, path in v1_routes(server):
                target = concretize(path)
                status, _payload = await client.request(
                    method, target, body={} if method in WRITE_METHODS else None, csrf=False
                )
                if status >= 500:
                    problems.append(f"{method} {target} -> {status}")
            assert not problems, "匿名请求出现 5xx：错误映射先于 handler 失效了吗？\n" + "\n".join(
                problems
            )

    async def test_login_is_the_only_exempt_post(self, tmp_path: Path) -> None:
        """豁免是方法 + 路径级的：GET /session 仍要登录，POST 空体到达 handler。"""
        async with api_server(tmp_path) as (client, _bot, _server):
            status, payload = await client.get(f"{API_PREFIX}/session", csrf=False)
            assert status == 401 and error_code(payload) == "auth.unauthorized"
            # 空体（无 JSON）到达 handler：400 说明中间件只放行了登录 POST
            status, payload = await client.post(LOGIN_PATH, csrf=False)
            assert status == 400 and error_code(payload) == "request.empty"
            # 对照组：正确凭据可以在没有会话时换取会话（登录豁免真的生效）
            status, payload = await client.login()
            assert status == 200 and client.csrf and payload["ok"] is True


class TestCsrfMatrix:
    """已登录但缺/错 CSRF token 的写请求：403 且处理器不得运行。"""

    async def test_every_v1_write_requires_csrf_and_never_runs(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, bot, server):
            await _login_ok(client)
            writes = [(m, p) for m, p in v1_routes(server) if m in WRITE_METHODS]
            assert len(writes) >= 30, f"写路由枚举似乎不完整：只拿到 {len(writes)} 条"
            before = state_fingerprint(bot)
            problems: list[str] = []
            for method, path in writes:
                target = concretize(path)
                status, payload = await client.request(method, target, body={}, csrf=False)
                if status != 403 or error_code(payload) != "auth.csrf":
                    problems.append(f"{method} {target} -> {status} {error_code(payload)}")
            assert not problems, "缺少 CSRF 头的写请求必须 403 auth.csrf：\n" + "\n".join(problems)
            assert state_fingerprint(bot) == before, (
                "无 CSRF 的写请求被挡下后，配置/沙盒状态必须原封不动（handler 不得运行）"
            )

    async def test_session_writes_are_not_csrf_exempt(self, tmp_path: Path) -> None:
        """与登录同路径的 DELETE（登出）/PATCH（偏好）仍是普通写操作。"""
        async with api_server(tmp_path) as (client, bot, _server):
            await _login_ok(client)
            before = state_fingerprint(bot)
            status, payload = await client.delete(f"{API_PREFIX}/session", csrf=False)
            assert status == 403 and error_code(payload) == "auth.csrf"
            status, payload = await client.patch(
                f"{API_PREFIX}/session", body={"theme": "light"}, csrf=False
            )
            assert status == 403 and error_code(payload) == "auth.csrf"
            assert state_fingerprint(bot) == before, "被挡下的登出/偏好请求不得改变状态"
            # 带正确 token 的登出仍然生效（豁免修复没有误伤真实功能）
            status, payload = await client.delete(f"{API_PREFIX}/session")
            assert status == 200 and payload["data"]["logged_out"] is True

    async def test_wrong_csrf_token_is_rejected_like_a_missing_one(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, bot, _server):
            await _login_ok(client)
            before = state_fingerprint(bot)
            status, payload = await client.patch(
                f"{API_PREFIX}/config",
                body={"values": {"bot.name": "csrf-probe"}},
                csrf=False,
                headers={"X-CSRF-Token": "not-the-token"},
            )
            assert status == 403 and error_code(payload) == "auth.csrf"
            assert state_fingerprint(bot) == before

    async def test_the_real_token_is_accepted(self, tmp_path: Path) -> None:
        """对照组：带正确 token 的写请求必须放行，证明 403 不是"永远拒绝"。"""
        async with api_server(tmp_path) as (client, _bot, _server):
            await _login_ok(client)
            status, payload = await client.patch(
                f"{API_PREFIX}/config", body={"values": {"ai.enabled": False}}
            )
            assert status == 200 and payload["ok"] is True


class TestBearerServiceChannel:
    """Minecraft Bridge 回调（Bearer 通道，Phase 1）：token 门独立于会话/CSRF。

    从 CSRF 矩阵排除（v1_routes 里的 discard）不等于没人管：这里证明
    通道被 Bearer 共享密钥把守，且错误/缺失 token 一律 401，不受登录态影响。
    """

    async def test_bearer_gate_is_independent_of_session_and_csrf(self, tmp_path: Path) -> None:
        from app.config.settings import MinecraftConfig
        from app.integrations.minecraft.service import MinecraftService

        async with api_server(tmp_path) as (client, bot, _server):
            service = MinecraftService(
                bot,
                MinecraftConfig(
                    enabled=True,
                    auto_start_runtime=False,
                    external_callback_token="matrix-token",
                ),
            )
            bot.minecraft = service
            try:
                path = next(iter(BEARER_CHANNEL_PATHS))
                # 未登录 + 无 token / 错 token → 401（不是 302/403/5xx）
                status, payload = await client.post(path, body={}, csrf=False)
                assert status == 401 and error_code(payload) == "auth.unauthorized"
                status, payload = await client.post(
                    path, body={}, headers={"Authorization": "Bearer wrong"}, csrf=False
                )
                assert status == 401 and error_code(payload) == "auth.unauthorized"
                # 已登录但错 token：仍是 401 —— Bearer 门优先于浏览器的 CSRF 模型
                await _login_ok(client)
                status, payload = await client.post(
                    path, body={}, headers={"Authorization": "Bearer wrong"}
                )
                assert status == 401 and error_code(payload) == "auth.unauthorized"
                # 对照组：正确 token 免会话/免 CSRF 直达 handler（载荷合法 → 200）
                status, payload = await client.post(
                    path,
                    body={"event": "minecraft.disconnected", "session_id": None, "timestamp": 1.0},
                    headers={"Authorization": "Bearer matrix-token"},
                    csrf=False,
                )
                assert status == 200 and payload["data"]["accepted"] is True
            finally:
                await service._cleanup()
