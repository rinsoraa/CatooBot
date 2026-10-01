"""CatooBot WebUI: aiohttp admin console.

Structure: pages (server-rendered HTML) + JSON API under /api/*, both behind
cookie-session auth. The WebUI talks to core only through AdminService and
never blocks the QQ pipeline — if it fails to start, chat continues.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from aiohttp import web

from app.web import ui
from app.web.auth import AuthService
from app.web.pages import (
    config_page,
    conversation_page,
)
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
from app.web.routes.identity import IdentityRoutes
from app.web.routes.media import MediaRoutes
from app.web.routes.memory import MemoryRoutes
from app.web.routes.models import ModelRoutes
from app.web.routes.ops import OpsRoutes
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
from app.web.services.media import StickerAdminService
from app.web.services.memory import MemoryAdminService
from app.web.services.memory_correction import MemoryCorrectionService
from app.web.services.social import SocialAdminService
from app.web.services.tools import ToolAdminService

if TYPE_CHECKING:
    from app.config.settings import WebConfig
    from app.core.bot import Bot


log = logging.getLogger("CatooBot.Web")


class WebServer(
    OpsRoutes,
    ModelRoutes,
    MediaRoutes,
    IdentityRoutes,
    SocialRoutes,
    ToolRoutes,
    AgentRoutes,
    SandboxRoutes,
    MemoryRoutes,
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
        app.router.add_get("/login", self._login_page)
        app.router.add_post("/login", self._login_submit)
        app.router.add_post("/logout", self._logout)
        app.router.add_get("/", self._dashboard)
        app.router.add_get("/prompts", self._prompts_page)
        app.router.add_post("/prompts", self._prompts_save)
        # v0.9 UI: in-browser configuration + prompt-driven memory correction
        app.router.add_get("/config", self._config_page)
        app.router.add_post("/config/basic", self._config_basic_save)
        app.router.add_post("/config/ai", self._config_ai_save)
        app.router.add_post("/config/onebot", self._config_onebot_save)
        app.router.add_post("/config/web", self._config_web_save)
        app.router.add_post("/config/raw", self._config_raw_save)
        app.router.add_post("/config/reset", self._config_reset)
        app.router.add_post("/config/test-provider", self._config_test_provider)
        app.router.add_get("/conversation", self._conversation_page)
        app.router.add_get("/conversation/continuity", self._conversation_continuity_page)
        self.register_ops(app)
        self.register_models(app)
        self.register_media(app)
        self.register_identity(app)
        self.register_social(app)
        self.register_sandbox(app)
        self.register_memory(app)
        self.register_tools(app)
        self.register_agent(app)

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
        if request.path in ("/login",) or (request.path == "/api/login"):
            return await handler(request)
        token = request.cookies.get(SESSION_COOKIE)
        if not self._auth.validate(token):
            if request.path.startswith("/ws/"):
                raise web.HTTPUnauthorized()  # an upgrade cannot follow a redirect
            raise web.HTTPFound("/login")
        set_csrf_token(token)
        if request.method in MUTATING_METHODS and request.path not in CSRF_EXEMPT_PATHS:
            supplied = request.headers.get(CSRF_HEADER, "")
            if not supplied and request.content_type.startswith(
                "application/x-www-form-urlencoded"
            ):
                supplied = str((await request.post()).get(CSRF_FIELD, ""))
            if supplied != csrf_token(token):
                self._bot.metrics.inc("csrf_rejected")
                log.warning("[Web.Security] CSRF 校验失败：%s %s", request.method, request.path)
                raise web.HTTPForbidden(text="CSRF token 无效或缺失，请刷新页面重试")
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

    # ------------------------------------------------------------- dashboard

    async def _dashboard(self, request: web.Request) -> web.Response:
        data = await self._admin.dashboard()
        metrics = data["metrics"]
        sandbox = getattr(self._bot, "sandbox", None)
        character = getattr(self._bot, "character", None)

        online = bool(data["online"])
        stats = [
            ("NapCat 连接", "在线" if online else "离线", ""),
            ("机器人 QQ", data["self_id"] or "-", ""),
            (
                "当前模型",
                data["current_model"] or "-",
                "最近一次成功调用使用的模型；限流时会自动切到备用模型",
            ),
            ("会话数", data["sessions"], ""),
            ("用户数", data["users"], ""),
            ("群数", data["groups"], ""),
            ("记忆条数", data["memories"], "含已被取代、仍可审计的旧事实"),
            ("收到消息", metrics["messages_received"], ""),
            ("AI 请求", metrics["ai_requests"], "每一次模型调用（含故障转移时的重试）"),
            ("限流 429", metrics["rate_limited"], "被服务商限流时自动故障转移到备用模型"),
            ("AI 失败", metrics["ai_errors"], "所有模型都没能答出来的请求数"),
            ("新增记忆", metrics["memories_extracted"], "从对话里抽取并落库的记忆条数"),
            ("运行时长", f"{metrics['uptime_seconds'] // 60} 分钟", ""),
        ]
        cards = ui.stats_grid(stats)

        state_bits: list[tuple[str, Any, str]] = []
        if character is not None:
            state = await self._admin.character_state()
            state_bits = [
                ("角色", state.get("mood") or "-", "她当前的心情阶梯（down→cheerful）"),
                (
                    "精力",
                    f"{float(state.get('energy') or 0):.0%}",
                    "0~100%，随活动与睡眠变化，太低时回复会更短",
                ),
                ("正在做", state.get("activity") or "发呆", "来自生活沙盒：她此刻在做的事"),
            ]
        if sandbox is not None and sandbox.enabled:
            try:
                context = sandbox.context()
                state_bits.append(("地点", context.get("location", "-"), "她此刻所在的空间"))
                state_bits.append(
                    ("模式", " + ".join(context.get("modes", [])) or "-", "可叠加的人格模式")
                )
                state_bits.append(("小喵", context.get("pet", "-"), "她的猫此刻的状态"))
            except Exception:  # noqa: BLE001 - dashboard must never fail
                pass
        character_card = (
            ui.card(
                "角色此刻",
                ui.stats_grid(state_bits)
                + "<p class='hint'>想看更细的生活与时间线，去 <a href='/sandbox'>沙盒</a> 页面；"
                "控制台里也会实时播报（logging.narrate）。</p>",
                tip_text="她的状态是叙事状态（演出来的），不是真实心理测量",
            )
            if state_bits
            else ""
        )

        model_rows = "".join(
            f"<tr><td>{esc(m['name'])}</td><td>{esc(m['provider'])}</td><td>{esc(m['model'])}</td>"
            f"<td>{ui.badge('启用', 'success') if m['enabled'] else ui.badge('停用', 'default')}</td>"
            f"<td>{ui.badge('冷却中', 'warning') if m['in_cooldown'] else '—'}</td>"
            f"<td>{m['failure_count']}</td>"
            f"<td class='muted'>{esc(m['last_error'] or '')}</td></tr>"
            for m in data["models"]
        )
        model_card = ui.card(
            "模型与故障转移",
            ui.table(
                ["名称", "Provider", "模型 ID", "状态", "冷却", "失败", "最近错误"],
                model_rows,
                tips=[
                    "配置里的模型别名",
                    "使用哪个服务商",
                    "服务商真实的模型 ID",
                    "是否参与调度",
                    "被限流/报错后会短暂冷却",
                    "本次运行累计失败次数",
                    "最后一次失败原因（已脱敏）",
                ],
                empty="还没有配置模型，去「配置 → AI 模型」添加",
                table_id="dash-models",
            )
            + "<p class='hint'>列表顺序即优先级；在 <a href='/models'>模型</a> 页可即时启停与调整顺序。</p>",
            tip_text="主模型被限流时会自动切到下一个可用模型",
        )

        quick = ui.card(
            "常见操作",
            "<div class='toolbar'>"
            + ui.link_button(
                "/config", "改配置（Provider/模型/权限）", tip_text="不用再手工编辑 YAML"
            )
            + ui.link_button(
                "/memory/correction", "修正一条记忆", tip_text="用一句人话让她记住新的说法"
            )
            + ui.link_button(
                "/sandbox",
                "看看她现在在干嘛",
                tip_text="生活沙盒：她在哪个房间、正在做什么、小喵在干嘛",
            )
            + ui.link_button(
                "/sandbox/chat",
                "调回复节奏与主动性",
                tip_text="私聊回复、群聊参与、主动回复（已并入沙盒页）",
            )
            + ui.link_button("/tools", "管理工具", tip_text="启用/限流/权限与执行记录")
            + ui.link_button("/runtime", "运行时与插件", tip_text="重载插件、健康检查")
            + "</div>",
            tip_text="常用的入口都放在这里，省得在左侧找",
        )

        body = cards + quick + character_card + model_card
        return web.Response(
            text=layout(
                "仪表盘",
                "/",
                body,
                subtitle=f"{self._bot.name} · {'在线' if online else '等待 NapCat 连接'}",
            ),
            content_type="text/html",
        )

    # ------------------------------------------------------------- character

    # ---------------------------------------------------------------- memory

    # ----------------------------------------------------------- users/groups

    # -------------------------------------------------------------- sessions

    # ---------------------------------------------------------------- models

    # --------------------------------------------------------------- prompts

    async def _prompts_page(self, request: web.Request) -> web.Response:
        prompts = await self._admin.get_prompts()
        body = f"""<div class="card"><h3>Prompts</h3><form method="post" action="/prompts">
<label>Persona System Prompt（角色自由补充段）</label>
<textarea name="persona_system_prompt" rows="8">{esc(prompts["persona_system_prompt"])}</textarea>
<p class="hint">角色的身份/性格/规则来自人物档案（<code>config/character_bible.md</code>）并同步在
<a href="/character">角色</a>页；这里只放自由补充段，长期设定请改档案。</p>
<label>Memory Extraction Prompt（留空使用内置默认）</label>
<textarea name="memory_extraction_prompt" rows="8">{esc(prompts["memory_extraction_prompt"])}</textarea>
<p><button class="btn btn-primary">保存并热加载</button></p></form></div>"""
        return web.Response(
            text=layout(
                "提示词",
                "/prompts",
                body,
                subtitle="系统提示词覆盖项（写在这里的内容会优先于默认值）",
            ),
            content_type="text/html",
        )

    async def _prompts_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._admin.save_prompts(
            {
                "persona_system_prompt": str(form.get("persona_system_prompt", "")),
                "memory_extraction_prompt": str(form.get("memory_extraction_prompt", "")),
            }
        )
        return web.Response(
            text=layout(
                "提示词",
                "/prompts",
                '<div class="card">已保存</div>'
                '<meta http-equiv="refresh" content="1;url=/prompts">',
            ),
            content_type="text/html",
        )

    # ------------------------------------------------------------------ logs

    # --------------------------------------------------------------- topics

    # -------------------------------------------------------- memory (v0.5)

    # ---------------------------------------------------------- tools (v0.6)

    # ------------------------------------------------------ tool api routes

    # ---------------------------------------------------------- agent (v0.7)

    # --------------------------------------------------- config editor (v0.9)

    async def _config_page(self, request: web.Request) -> web.Response:
        result = request.query.get("result", "")
        kind = "warn" if result.startswith("!") else "ok"
        message = result.lstrip("!")
        report = self._config_admin.last_report or None
        body = config_page.render(
            self._config_admin,
            tab=request.query.get("tab", "basic"),
            msg=message,
            kind=kind,
            engine_report=report,
        )
        return web.Response(
            text=layout("配置", "/config", body, subtitle="在浏览器里改配置，保存即生效"),
            content_type="text/html",
        )

    async def _config_save(
        self, request: web.Request, payload: dict[str, Any], tab: str
    ) -> web.Response:
        try:
            outcome = await self._config_admin.save(payload)
        except Exception as exc:  # noqa: BLE001 - validation errors are user-facing
            raise web.HTTPFound(f"/config?tab={tab}&result={quote('!' + str(exc)[:300])}") from exc
        notes = outcome.get("notes") or []
        message = "已保存并生效" if not notes else "已保存 · " + "；".join(notes)
        raise web.HTTPFound(f"/config?tab={tab}&result={quote(message)}")

    async def _config_basic_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, self._config_admin.basic_form(form), "basic")

    async def _config_ai_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, {"ai": self._config_admin.ai_form(form)}, "ai")

    async def _config_onebot_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        return await self._config_save(request, self._config_admin.onebot_form(form), "onebot")

    async def _config_web_save(self, request: web.Request) -> web.Response:
        form = dict(await request.post())
        try:
            payload = await self._config_admin.web_form(form)
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(f"/config?tab=web&result={quote('!' + str(exc)[:300])}") from exc
        return await self._config_save(request, payload, "web")

    async def _config_raw_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        try:
            await self._config_admin.save_raw(str(form.get("raw", "")))
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(f"/config?tab=raw&result={quote('!' + str(exc)[:300])}") from exc
        raise web.HTTPFound(f"/config?tab=raw&result={quote('覆盖配置已保存')}")

    async def _config_reset(self, request: web.Request) -> web.Response:
        await self._config_admin.reset()
        raise web.HTTPFound(f"/config?result={quote('已清空覆盖，恢复 config.yaml 原样')}")

    async def _config_test_provider(self, request: web.Request) -> web.Response:
        form = await request.post()
        name = str(form.get("name", ""))
        message = await self._config_admin.test_provider(name)
        prefix = "" if message.startswith("✅") else "!"
        raise web.HTTPFound(f"/config?tab=ai&result={quote(prefix + message)}")

    # ------------------------------------------------ memory correction (v0.9)

    # ---------------------------------------------------- social cognition (v0.9)

    async def _conversation_page(self, request: web.Request) -> web.Response:
        runtime = self._bot.conversation
        sessions = runtime.session_snapshot()
        stale_total = 0
        turns: list[dict[str, Any]] = []
        try:
            row = await self._bot.database.fetchone(
                "SELECT COUNT(*) AS n FROM conversation_turns WHERE status = 'stale'"
            )
            stale_total = int(row["n"]) if row else 0
            rows = await self._bot.database.fetchall(
                "SELECT * FROM conversation_turns ORDER BY started_at DESC LIMIT 30"
            )
            turns = [dict(row) for row in rows]
        except Exception:  # noqa: BLE001 - table may not exist pre-migration
            self._bot.log.debug("[Web] conversation tables unavailable", exc_info=True)
        data = {
            "enabled": runtime.enabled,
            "sessions": sessions,
            "session_count": len(sessions),
            "buffered": sum(s["buffered_messages"] for s in sessions),
            "generating": sum(1 for s in sessions if s["active_generation"]),
            "stale_total": stale_total,
        }
        body = conversation_page._tabs("/conversation") + conversation_page.dashboard(data, turns)
        return web.Response(
            text=layout(
                "对话轮次",
                "/conversation",
                body,
                subtitle="连续消息合并为一个 Turn；改口让旧回复作废（v1.2）",
            ),
            content_type="text/html",
        )

    async def _conversation_continuity_page(self, request: web.Request) -> web.Response:
        continuity = self._bot.continuity
        data: dict[str, Any] = {"enabled": continuity is not None}
        if continuity is not None:
            state = continuity.state.model_dump(mode="json")
            loops = await continuity.store.open_loops(include_closed=True)
            profiles: list[dict[str, Any]] = []
            try:
                rows = await self._bot.database.fetchall(
                    "SELECT * FROM interaction_profiles ORDER BY updated_at DESC LIMIT 20"
                )
                import json as _json

                for row in rows:
                    profiles.append(
                        {
                            "user_id": row["user_id"],
                            "patterns": _json.loads(row["patterns"] or "{}"),
                            "updated_at": row["updated_at"],
                        }
                    )
            except Exception:  # noqa: BLE001
                self._bot.log.debug("[Web] interaction profiles unavailable", exc_info=True)
            shared: list[dict[str, Any]] = []
            try:
                rows = await self._bot.database.fetchall(
                    "SELECT * FROM shared_experiences ORDER BY updated_at DESC LIMIT 20"
                )
                import json as _json

                shared = [
                    {
                        "summary": row["summary"],
                        "type": row["type"],
                        "keywords": _json.loads(row["keywords"] or "[]"),
                        "times_referenced": row["times_referenced"],
                        "confidence": row["confidence"],
                    }
                    for row in rows
                ]
            except Exception:  # noqa: BLE001
                pass
            data.update(
                {
                    "state": state,
                    "open_loops": [
                        {
                            "summary": loop.summary,
                            "type": loop.type.value,
                            "status": loop.status.value,
                            "progress": loop.progress,
                            "source": loop.source,
                            "confidence": loop.confidence,
                        }
                        for loop in loops[:12]
                    ],
                    "profiles": profiles,
                    "shared_experiences": shared,
                }
            )
        body = conversation_page._tabs("/conversation/continuity")
        body += conversation_page.continuity_page(data)
        return web.Response(
            text=layout(
                "对话 · 延续状态",
                "/conversation",
                body,
                subtitle="她最近关注什么、有什么没做完、和谁的共同经历（v1.2）",
            ),
            content_type="text/html",
        )

    # ------------------------------------------------------------------ sandbox

    # ------------------------------------------------------ stickers (v1.1)
