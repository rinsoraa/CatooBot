"""CatooBot WebUI: aiohttp admin console.

Structure: pages (server-rendered HTML) + JSON API under /api/*, both behind
cookie-session auth. The WebUI talks to core only through AdminService and
never blocks the QQ pipeline — if it fails to start, chat continues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from aiohttp import web

from app.web import ui
from app.web.api import ApiRoutes
from app.web.api.common import API_PREFIX, fail, forbidden, unauthorized
from app.web.auth import AuthService
from app.web.realtime import (
    NarrationFeed,
    RealtimeHub,
    attach_narration_feed,
    detach_narration_feed,
)
from app.web.routes.agent import AgentRoutes
from app.web.routes.base import (  # noqa: F401 - re-exported for compatibility
    SESSION_COOKIE,
    _as_float,
    _as_int,
    esc,
    format_ts,
    layout,
)
from app.web.routes.config import ConfigRoutes
from app.web.routes.dashboard import DashboardRoutes
from app.web.routes.expressions import ExpressionsRoutes
from app.web.routes.identity import IdentityRoutes
from app.web.routes.media import MediaRoutes
from app.web.routes.memory import MemoryRoutes
from app.web.routes.models import ModelRoutes
from app.web.routes.ops import OpsRoutes
from app.web.routes.prompts import PromptRoutes
from app.web.routes.sandbox import SandboxRoutes
from app.web.routes.social import SocialRoutes
from app.web.routes.tools import ToolRoutes
from app.web.security import (
    CSRF_EXEMPT_PATHS,
    CSRF_FIELD,
    CSRF_HEADER,
    MUTATING_METHODS,
    LoginThrottle,
    csrf_token,
    set_csrf_token,
)
from app.web.services.admin import AdminService
from app.web.services.agent import AgentAdminService
from app.web.services.behavior import BehaviorService
from app.web.services.config_admin import ConfigAdminService
from app.web.services.expressions import ExpressionAdminService
from app.web.services.media import StickerAdminService
from app.web.services.memory import MemoryAdminService
from app.web.services.memory_correction import MemoryCorrectionService
from app.web.services.social import SocialAdminService
from app.web.services.tools import ToolAdminService
from app.web.spa import SpaRoutes

if TYPE_CHECKING:
    from app.config.settings import WebConfig
    from app.core.bot import Bot


log = logging.getLogger("CatooBot.Web")


class WebServer(
    ApiRoutes,
    SpaRoutes,
    OpsRoutes,
    ModelRoutes,
    MediaRoutes,
    IdentityRoutes,
    ExpressionsRoutes,
    SocialRoutes,
    ToolRoutes,
    AgentRoutes,
    SandboxRoutes,
    MemoryRoutes,
    ConfigRoutes,
    PromptRoutes,
    DashboardRoutes,
):
    def __init__(self, config: WebConfig, bot: Bot) -> None:
        self._config = config
        self._bot = bot
        self._admin = AdminService(bot)
        self._behavior = BehaviorService(bot)
        self._memory_admin = MemoryAdminService(bot)
        self._config_admin = ConfigAdminService(bot)
        self._correction = MemoryCorrectionService(bot)
        self._tool_admin = ToolAdminService(bot)
        self._agent_admin = AgentAdminService(bot)
        self._social_admin = SocialAdminService(bot)
        self._expression_admin = ExpressionAdminService(bot)
        self._sticker_admin = StickerAdminService(bot)
        self._auth = getattr(bot, "web_auth", None) or AuthService(config, bot.database)
        self._throttle = LoginThrottle(metrics=getattr(bot, "metrics", None))
        self._runner: web.AppRunner | None = None
        self._hub = RealtimeHub(metrics=getattr(bot, "metrics", None))
        self._narration_feed: NarrationFeed | None = None

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        await self._auth.ensure_bootstrap_user()
        # Task 17: stream narration lines to logged-in browsers.
        self._narration_feed = attach_narration_feed(self._hub)
        app = web.Application(middlewares=[self._auth_middleware])
        # `/` and `/login` are dispatch points now (SPA in v1, SSR in v0.8);
        # the form POST stays the legacy login endpoint for both.
        app.router.add_post("/login", self._login_submit)
        app.router.add_post("/logout", self._logout)
        # v0.9 UI: in-browser configuration + prompt-driven memory correction
        self.register_ops(app)
        self.register_models(app)
        self.register_media(app)
        self.register_identity(app)
        self.register_social(app)
        self.register_expressions(app)
        self.register_sandbox(app)
        self.register_memory(app)
        self.register_config(app)
        self.register_prompts(app)
        self.register_tools(app)
        self.register_agent(app)
        # WebUI v1.0 (W2): JSON API under /api/v1, alongside the SSR pages.
        self.register_v1(app)
        # WebUI v1.0 (W3): SPA hosting + /legacy + the catch-all. Registered
        # last on purpose so it never shadows a real route.
        self.register_spa(app)

        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._config.host, self._config.port)
        await site.start()
        log.info("WebUI listening on http://%s:%d", self._config.host, self._config.port)

    async def stop(self) -> None:
        if self._narration_feed is not None:
            detach_narration_feed(self._narration_feed)
            self._narration_feed = None
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None
            log.info("WebUI stopped")

    # ------------------------------------------------------------------ auth

    @web.middleware
    async def _auth_middleware(self, request: web.Request, handler: Any) -> Any:
        ui.set_preferences(ui.preferences_from(request.cookies))
        path = request.path
        # JSON login lives at the same path as the session bootstrap, so the
        # exemption is method-scoped; the SSR login keeps its two forms.
        if (
            path == "/login"
            or path == "/api/login"
            or (path == f"{API_PREFIX}/session" and request.method == "POST")
        ):
            return await handler(request)
        token = request.cookies.get(SESSION_COOKIE)
        if not self._auth.validate(token):
            if path.startswith("/ws/"):
                raise web.HTTPUnauthorized()  # an upgrade cannot follow a redirect
            if path.startswith(API_PREFIX):
                return fail(unauthorized(), request=request)
            raise web.HTTPFound("/login")
        set_csrf_token(token)
        if request.method in MUTATING_METHODS and path not in CSRF_EXEMPT_PATHS | {
            f"{API_PREFIX}/session"
        }:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not supplied and request.content_type.startswith(
                ("application/x-www-form-urlencoded", "multipart/form-data")
            ):
                supplied = str((await request.post()).get(CSRF_FIELD, ""))
            if supplied != csrf_token(token):
                self._bot.metrics.inc("csrf_rejected")
                log.warning("[Web.Security] CSRF 校验失败：%s %s", request.method, request.path)
                if path.startswith(API_PREFIX):
                    return fail(
                        forbidden("CSRF 校验失败：token 无效或缺失", code="auth.csrf"),
                        request=request,
                    )
                raise web.HTTPForbidden(
                    text="CSRF 校验失败：token 无效或缺失（表单未注入安全令牌字段）"
                )
        return await handler(request)

    async def _login_page(self, request: web.Request) -> web.Response:
        ui.set_preferences(ui.preferences_from(request.cookies))
        body = (
            '<div style="max-width:400px;margin:8vh auto">'
            '<div class="card">'
            f'<div class="card-head"><h3>🍥 CatooBot 管理后台{ui.tip("只有运营者使用的后台；QQ 端没有任何管理指令")}</h3></div>'
            '<form method="post" action="/login">'
            f'<label class="field"><span class="field-label">用户名{ui.tip("默认 admin，可在账号设置里修改")}</span>'
            '<input name="username" autofocus autocomplete="username"></label>'
            f'<label class="field"><span class="field-label">密码{ui.tip("首次登录用的是配置里的密码，之后可在 WebUI 修改")}</span>'
            '<input name="password" type="password" autocomplete="current-password"></label>'
            '<p><button class="btn btn-primary" type="submit">登录</button></p>'
            "</form>"
            f'<p class="hint">开发者 {ui.AUTHOR} · {ui.VERSION}</p>'
            "</div></div>"
        )
        return web.Response(text=ui.page("登录", "", body, minimal=True), content_type="text/html")

    async def _login_submit(self, request: web.Request) -> web.Response:
        form = await request.post()
        username = str(form.get("username", ""))
        key = f"{request.remote or 'unknown'}|{username}"
        if not self._throttle.allowed(key):
            wait = self._throttle.retry_after(key)
            log.warning("[Web.Security] 登录被限速：%s（%.0f 秒后重试）", key, wait)
            raise web.HTTPTooManyRequests(text=f"尝试过于频繁，请 {int(wait) + 1} 秒后再试")
        token = await self._auth.login(username, str(form.get("password", "")))
        if token is None:
            self._throttle.record_failure(key)
            raise web.HTTPFound("/login")
        self._throttle.reset(key)
        response = web.HTTPFound("/")
        response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="Lax")
        return response

    async def _logout(self, request: web.Request) -> web.Response:
        self._auth.logout(request.cookies.get(SESSION_COOKIE))
        response = web.HTTPFound("/login")
        response.del_cookie(SESSION_COOKIE)
        return response
