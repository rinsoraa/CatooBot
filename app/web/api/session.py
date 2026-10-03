"""`/api/v1` 会话、CSRF 与元信息（WebUI v1.0 · W2）。

SPA 需要的引导：一个能读到 CSRF token 的端点（旧 SSR 把 token 注入表单，
脚本读不到），以及 JSON 版登录/登出。认证本身仍走既有会话 Cookie 与
``AuthService``——没有第二套会话机制。
"""

from __future__ import annotations

import platform
import sys
import time

from aiohttp import web

from app.web import ui
from app.web.api.common import (
    ok,
    rate_limited,
    read_json,
    unauthorized,
    unprocessable,
)
from app.web.routes.base import SESSION_COOKIE, WebContext
from app.web.security import CSRF_FIELD, csrf_token

PUBLIC_LOGIN_PATH = "/api/v1/session"


class SessionRoutes(WebContext):
    """`/api/v1/session`, `/api/v1/csrf`, `/api/v1/meta`."""

    async def _v1_session_get(self, request: web.Request) -> web.Response:
        token = request.cookies.get(SESSION_COOKIE)
        if not self._auth.validate(token):
            raise unauthorized()
        return ok(
            {
                "user": {"name": str(getattr(self._config, "username", "admin"))},
                "csrf_token": csrf_token(token),
                "permissions": {"admin": True},
                "lang": ui.preferences_from(request.cookies).get("lang", "zh"),
                "theme": ui.preferences_from(request.cookies).get("theme", "dark"),
            },
            request=request,
        )

    async def _v1_csrf(self, request: web.Request) -> web.Response:
        """Bootstrap the write token for a JSON client (same value as the forms)."""
        token = request.cookies.get(SESSION_COOKIE)
        if not self._auth.validate(token):
            raise unauthorized()
        return ok(
            {"csrf_token": csrf_token(token), "header": "X-CSRF-Token", "field": CSRF_FIELD},
            request=request,
        )

    async def _v1_session_post(self, request: web.Request) -> web.Response:
        """JSON login. Same throttle + AuthService as the SSR form."""
        body = await read_json(request)
        username = str(body.get("username", ""))
        password = str(body.get("password", ""))
        key = f"{request.remote or 'unknown'}|{username}"
        if not self._throttle.allowed(key):
            wait = self._throttle.retry_after(key)
            raise rate_limited(f"尝试过于频繁，请 {int(wait) + 1} 秒后再试")
        token = await self._auth.login(username, password)
        if token is None:
            self._throttle.record_failure(key)
            raise unauthorized("用户名或密码不正确")
        self._throttle.reset(key)
        response = ok(
            {
                "user": {"name": username},
                "csrf_token": csrf_token(token),
                "permissions": {"admin": True},
            },
            request=request,
        )
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax")
        return response

    async def _v1_session_delete(self, request: web.Request) -> web.Response:
        self._auth.logout(request.cookies.get(SESSION_COOKIE))
        response = ok({"logged_out": True}, request=request)
        response.del_cookie(SESSION_COOKIE)
        return response

    async def _v1_session_patch(self, request: web.Request) -> web.Response:
        """Display preferences only (mode/theme/nav/lang) — same cookies as SSR."""
        from app.web.i18n import normalize_lang

        body = await read_json(request)
        response = ok({"updated": sorted(body.keys())}, request=request)
        cookies = {
            "lang": (ui.COOKIE_LANG, normalize_lang),
            "theme": (ui.COOKIE_THEME, ui.normalize_theme),
            "mode": (ui.COOKIE_MODE, ui.normalize_mode),
        }
        for name, (cookie, normalizer) in cookies.items():
            if name in body:
                response.set_cookie(cookie, normalizer(str(body[name])), samesite="Lax")
        if "nav_collapsed" in body:
            response.set_cookie(
                ui.COOKIE_NAV, "collapsed" if body["nav_collapsed"] else "open", samesite="Lax"
            )
        return response

    async def _v1_session_password(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        old = str(body.get("old_password", ""))
        new = str(body.get("new_password", ""))
        username = str(getattr(self._config, "username", "admin"))
        if len(new) < 6:
            raise unprocessable("新密码至少 6 位", field="new_password")
        if await self._auth.login(username, old) is None:
            raise unprocessable("原密码不正确", field="old_password")
        changed = await self._auth.set_password(username, new)
        if not changed:
            raise unprocessable("密码更新失败：用户不存在")
        response = ok({"changed": True}, request=request)
        response.del_cookie(SESSION_COOKIE)  # every session is gone, including this one
        return response

    async def _v1_meta(self, request: web.Request) -> web.Response:
        from app.main import VERSION

        try:
            import importlib.metadata as metadata

            webui_version = metadata.version("catoobot")
        except Exception:  # noqa: BLE001 - the distribution name is optional
            webui_version = ui.VERSION
        started = getattr(self._bot, "started_at", None) or 0.0
        return ok(
            {
                "api": "v1",
                "app_version": VERSION,
                "webui_version": webui_version,
                "core_behavior_phase": "P16",
                "python": sys.version.split()[0],
                "platform": platform.platform(),
                "started_at": started,
                "uptime_seconds": round(time.time() - started, 3) if started else 0.0,
            },
            request=request,
        )
