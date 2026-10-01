"""CatooBot WebUI: aiohttp admin console.

Structure: pages (server-rendered HTML) + JSON API under /api/*, both behind
cookie-session auth. The WebUI talks to core only through AdminService and
never blocks the QQ pipeline — if it fails to start, chat continues.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from aiohttp import web

from app.web import ui
from app.web.auth import AuthService
from app.web.pages import (
    config_page,
    conversation_page,
    correction_page,
    sandbox_page,
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
from app.web.routes.models import ModelRoutes
from app.web.routes.ops import OpsRoutes
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
    OpsRoutes, ModelRoutes, MediaRoutes, IdentityRoutes, SocialRoutes, ToolRoutes, AgentRoutes
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
        app.router.add_get("/memory", self._memory_page)
        app.router.add_get("/memory/search", self._memory_search_page)
        app.router.add_post("/memory/search", self._memory_search_page)
        app.router.add_get("/memory/timeline", self._memory_timeline_page)
        app.router.add_get("/memory/health", self._memory_health_page)
        app.router.add_get("/memory/retrieval-debug", self._memory_debug_page)
        app.router.add_post("/memory/retrieval-debug", self._memory_debug_page)
        app.router.add_get("/memory/embeddings", self._memory_embeddings_page)
        app.router.add_post("/memory/embeddings/{action}", self._memory_embedding_action)
        app.router.add_get("/memory/consolidation", self._memory_consolidation_page)
        app.router.add_post("/memory/consolidation/run", self._memory_consolidation_run)
        app.router.add_get("/memory/detail/{memory_id}", self._memory_detail_page)
        app.router.add_post("/api/memory/{action}/{memory_id}", self._api_memory_action)
        app.router.add_get("/prompts", self._prompts_page)
        app.router.add_post("/prompts", self._prompts_save)
        app.router.add_get("/behavior", self._behavior_legacy_redirect)
        app.router.add_get("/sandbox/chat", self._sandbox_chat_page)
        app.router.add_post("/behavior/settings", self._behavior_settings_save)
        app.router.add_post("/behavior/preview", self._behavior_preview)
        app.router.add_post("/behavior/test-response", self._behavior_test_response)
        app.router.add_post("/behavior/trigger/{action}", self._behavior_trigger)
        app.router.add_get("/topics", self._topics_page)
        app.router.add_post("/api/topics/{action}/{topic_id}", self._api_topic_action)
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
        app.router.add_get("/sandbox", self._sandbox_page)
        app.router.add_get("/sandbox/inspectors", self._sandbox_inspectors_page)
        app.router.add_get("/sandbox/needs", self._sandbox_needs_page)
        app.router.add_get("/sandbox/bible", self._sandbox_bible_page)
        app.router.add_get("/sandbox/trace", self._sandbox_trace_page)
        app.router.add_post("/sandbox/simulate", self._sandbox_simulate)
        app.router.add_post("/api/sandbox/control", self._api_sandbox_control)
        self.register_ops(app)
        self.register_models(app)
        self.register_media(app)
        self.register_identity(app)
        self.register_social(app)
        self.register_tools(app)
        self.register_agent(app)
        app.router.add_get("/memory/correction", self._memory_correction_page)
        app.router.add_post("/memory/correction", self._memory_correction_page)
        app.router.add_post("/memory/correction/apply", self._memory_correction_apply)

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

    @staticmethod
    def _memory_tabs(active: str) -> str:
        tabs = [
            ("/memory", "浏览"),
            ("/memory/search", "搜索"),
            ("/memory/correction", "记忆修正"),
            ("/memory/timeline", "时间线"),
            ("/memory/health", "健康度"),
            ("/memory/retrieval-debug", "检索调试"),
            ("/memory/embeddings", "向量"),
            ("/memory/consolidation", "巩固"),
        ]
        return ui.tabs(tabs, active)

    async def _memory_page(self, request: web.Request) -> web.Response:
        keyword = request.query.get("q", "")
        category = request.query.get("category", "")
        scope_key = request.query.get("scope", "")
        memories = await self._admin.list_memories(
            keyword=keyword, category=category, scope_key=scope_key, limit=200
        )
        options = [
            ("", "全部类型"),
            ("fact", "fact · 客观事实"),
            ("preference", "preference · 喜好"),
            ("profile", "profile · 资料"),
            ("project", "project · 项目"),
            ("interest", "interest · 兴趣"),
            ("habit", "habit · 习惯"),
            ("event", "event · 发生过的事"),
            ("relationship", "relationship · 关系"),
            ("instruction", "instruction · 用户要求"),
        ]
        rows = "".join(
            f"<tr><td>{m['id']}</td>"
            f"<td><a href='/memory?scope={quote(str(m['scope_key']))}'>"
            f"{esc(m['scope_key'])}</a></td>"
            f"<td>{ui.badge(m['category'], 'info')}</td>"
            f"<td>{esc(m['content'])}</td>"
            f"<td>{m['importance']:.2f}</td><td>{m['confidence']:.2f}</td>"
            f"<td>{m['use_count']}</td>"
            f"<td class='muted'>{esc(format_ts(m['created_at']))}</td>"
            f"<td><a class='btn btn-ghost btn-sm' href='/memory/detail/{m['id']}'"
            f"{ui.attr_tip('查看这条记忆的来源、关系与演化历史')}>详情</a>"
            f"<form class='inline' method='post' action='/api/memory/delete/{m['id']}'"
            f" data-confirm='确定删除这条记忆吗？（会同时删除它的向量）'>"
            f"<button class='btn btn-danger btn-sm' type='submit'"
            f"{ui.attr_tip('物理删除：不推荐，通常用「记忆修正」或归档更安全')}>删除</button>"
            f"</form></td></tr>"
            for m in memories
        )
        filters = (
            "<form method='get' action='/memory'><div class='grid'>"
            + ui.field(
                "搜索内容",
                "q",
                keyword,
                tip_text="按关键词过滤（不做语义检索，语义搜索请用「搜索」页）",
            )
            + ui.select(
                "类型", "category", options, category, tip_text="记忆的语义分类，影响检索时的加权"
            )
            + ui.field(
                "Scope",
                "scope",
                scope_key,
                tip_text="按用户/群过滤：user:123456 或 group:654321；留空查看全部",
            )
            + "</div><p><button class='btn btn-secondary' type='submit'"
            + ui.attr_tip("应用过滤条件")
            + ">筛选</button>"
            + ui.link_button(
                "/memory/correction",
                "用一句话修正记忆",
                tip_text="不用删改数据库：写清「改成什么」，她就会当作一直如此",
            )
            + "</p></form>"
        )
        table = ui.table(
            ["ID", "Scope", "类型", "内容", "重要度", "置信度", "用过", "创建时间", ""],
            rows,
            tips=[
                "记忆主键，修正时用它定位",
                "属于哪位用户/哪个群",
                "记忆分类",
                "记忆正文（会进入提示词的就是这行）",
                "越高越优先被想起",
                "模型对这条事实的把握",
                "被检索命中的次数",
                "写入时间",
                "详情 / 删除",
            ],
            empty="没有符合条件的记忆",
            table_id="memories",
        )
        body = (
            self._memory_tabs("/memory")
            + ui.card("筛选", filters, tip_text="这里按元数据过滤；语义相似度搜索在「搜索」标签页")
            + ui.card(
                f"记忆({len(memories)})",
                table,
                tip_text="只列出状态为 active 的记忆；被取代/归档的旧事实在时间线里可查",
            )
        )
        return web.Response(
            text=layout("记忆", "/memory", body, subtitle="她长期记住的事，以及为什么"),
            content_type="text/html",
        )

    async def _api_memory_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        memory_id = int(request.match_info["memory_id"])
        ok = await self._admin.memory_action(action, memory_id)
        return web.json_response({"ok": ok})

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

    async def _sandbox_chat_page(self, request: web.Request) -> web.Response:
        """Chat-surface behaviour, merged into the sandbox area (three parts).

        Her *life* belongs to the sandbox; these settings govern the three
        conversation surfaces: private replies, group participation and
        proactive messaging.
        """
        data = await self._behavior.dashboard()
        cfg = self._bot.config.behavior
        initiative = data["initiative"]
        scheduler = data["scheduler"]

        private_card = ui.card(
            "私聊回复",
            "<p class='hint'>有人私聊她时的节奏：延迟、分段与作息门控。"
            "她“正在做什么”由生活沙盒决定，这里只管回话方式。</p>"
            "<div class='grid'>"
            + ui.switch(
                "reply_enabled",
                cfg.reply.enabled,
                "启用延迟回复",
                tip_text="关掉后总是秒回；开着则按下方区间随机延迟",
            )
            + ui.field(
                "最小延迟（秒）", "min_delay", cfg.reply.min_delay, tip_text="最短等多久再回"
            )
            + ui.field(
                "最大延迟（秒）", "max_delay", cfg.reply.max_delay, tip_text="最长等多久再回"
            )
            + ui.switch(
                "chunking_enabled",
                cfg.chunking.enabled,
                "启用自然分段",
                tip_text="偶尔把一段回复拆成几条，更像真人",
            )
            + ui.field(
                "分段概率",
                "chunk_probability",
                cfg.chunking.chunk_probability,
                tip_text="0~1，越大越常拆",
            )
            + ui.field(
                "最多段数", "max_chunks", cfg.chunking.max_chunks, tip_text="一条回复最多拆成几段"
            )
            + "</div>"
            "<div class='grid'>"
            + ui.switch(
                "sleep_enabled",
                cfg.schedule.sleep_enabled,
                "启用睡眠时段",
                tip_text="这个时段回复更慢更困，也不主动说话",
            )
            + ui.field("入睡", "sleep_start", cfg.schedule.sleep_start, tip_text="HH:MM")
            + ui.field("起床", "sleep_end", cfg.schedule.sleep_end, tip_text="HH:MM")
            + ui.switch(
                "dnd_enabled",
                cfg.schedule.dnd_enabled,
                "启用免打扰",
                tip_text="这个时段不主动、也可以不回应",
            )
            + ui.field("免打扰开始", "dnd_start", cfg.schedule.dnd_start, tip_text="HH:MM")
            + ui.field("免打扰结束", "dnd_end", cfg.schedule.dnd_end, tip_text="HH:MM")
            + ui.switch(
                "dnd_blocks_replies",
                cfg.schedule.dnd_blocks_replies,
                "免打扰也阻止被动回复",
                tip_text="关闭时只是不主动，别人叫她还是回",
            )
            + "</div>",
            tip_text="私聊永远会回（除非免打扰配置成不回）；这里调的是“怎么回”",
        )

        group_card = ui.card(
            "群聊参与",
            "<p class='hint'>@ 她 / 回复她 → 必回；下面只管“没 @ 她”的自主插话。"
            "先由社交认知做结构化判断（话题相关、能补充、时机合适就参与）。</p>"
            "<div class='grid'>"
            + ui.switch(
                "participation_enabled",
                cfg.group.participation_enabled,
                "允许非 @ 插话",
                tip_text="关掉后群里只有 @ / 回复她才会说话",
            )
            + ui.field(
                "参与频率",
                "participation_probability",
                cfg.group.participation_probability,
                tip_text="确定性的额度累积：每条合格消息攒这么多额度，攒满 1 参与一次。"
                "1.0 ≈ 每条都参与（仍受冷却/每日额度约束）；0 = 完全交给社交认知",
            )
            + ui.field(
                "最短消息长度",
                "min_message_length",
                cfg.group.min_message_length,
                tip_text="短于此长度的群消息不参与判断（“哈哈哈”这种）",
            )
            + "</div>"
            "<p class='hint'>冷却、每日上限、观察批次与阈值在 "
            "<a href='/social/policy'>社交策略</a> 页；单个群的参与开关在 "
            "<a href='/groups'>群组</a> 页。</p>",
            tip_text="群聊参与 = 社交认知判断 + 这里的频率/长度控制",
        )

        initiative_card = ui.card(
            "主动回复",
            "<p class='hint'>没人找她时，她会不会主动发消息（会被多重硬限制拦住）。"
            "她在生活里发生的事（做完的事、零工、快递）会作为话题来源。</p>"
            "<div class='grid'>"
            + ui.switch(
                "initiative_enabled",
                cfg.initiative.enabled,
                "启用主动聊天",
                tip_text="总开关；下面所有硬性上限仍然会拦",
            )
            + ui.field(
                "最小间隔（分）",
                "min_interval_minutes",
                cfg.initiative.min_interval_minutes,
                tip_text="两次主动之间至少隔多久",
            )
            + ui.field("每日上限", "daily_limit", cfg.initiative.daily_limit, tip_text="")
            + ui.field("每小时上限", "hourly_limit", cfg.initiative.hourly_limit, tip_text="")
            + ui.field(
                "闲置（小时）",
                "idle_hours",
                cfg.initiative.idle_hours,
                tip_text="对方多久没说话才主动",
            )
            + ui.select(
                "最低关系",
                "min_relationship_stage",
                [
                    ("new", "new"),
                    ("familiar", "familiar"),
                    ("close", "close"),
                    ("very_close", "very_close"),
                ],
                cfg.initiative.min_relationship_stage,
                tip_text="关系熟到这个程度才主动",
            )
            + ui.field(
                "未回复上限",
                "max_unanswered",
                cfg.initiative.max_unanswered,
                tip_text="主动发了没回，达到上限就不再追问",
            )
            + "</div>"
            "<p class='hint'>后台消息额度在沙盒配置 <code>sandbox."
            "max_background_messages_per_day</code>。</p>",
            tip_text="主动消息 ≠ 后台生活：她在生活，但主动找人要过这道门",
        )

        settings_body = (
            "<form method='post' action='/behavior/settings'>"
            + private_card
            + group_card
            + initiative_card
            + "<p><button class='btn btn-primary' type='submit' "
            "data-tip='保存并热加载，无需重启'>保存设置</button></p></form>"
        )

        manual_html = (
            '<form class="inline" method="post" action="/behavior/test-response">'
            '<input name="text" placeholder="测试一句用户消息" style="width:340px;display:inline-block">'
            '<button class="btn btn-secondary btn-sm" type="submit">测试回复（生成不发送）</button></form>'
            '<div style="margin-top:12px" class="toolbar">'
            '<form class="inline" method="post" action="/behavior/trigger/mood_up">'
            '<button class="btn btn-secondary btn-sm" type="submit">心情 +1</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/mood_down">'
            '<button class="btn btn-secondary btn-sm" type="submit">心情 -1</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/reset_state">'
            '<button class="btn btn-danger btn-sm" type="submit">重置状态</button></form>'
            '<form class="inline" method="post" action="/behavior/trigger/test_initiative">'
            '<button class="btn btn-secondary btn-sm" type="submit">测试主动性（评估不发送）</button></form>'
            "</div>"
        )

        simulator_html = (
            "<form method='post' action='/behavior/preview'>"
            + "<div class='grid'>"
            + ui.field("时间", "sim_time", "23:50")
            + ui.field("心情", "mood", "relaxed")
            + ui.field("参考活动", "activity", "reading")
            + ui.select(
                "关系",
                "relationship",
                [
                    ("new", "new"),
                    ("familiar", "familiar"),
                    ("close", "close"),
                    ("very_close", "very_close"),
                ],
                "familiar",
            )
            + ui.field("话题", "topic", "未完成的项目")
            + "</div>"
            + ui.textarea("示例回复文本", "sample_reply", "好呀。\n\n等我看一下再说。", rows=3)
            + "<p><button class='btn btn-primary' type='submit' "
            "data-tip='只预览，不会发送到 QQ'>预览</button></p></form>"
        )

        events = data["recent_events"]
        event_rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(e['created_at']))}</td><td>{esc(e['type'])}</td>"
            f"<td>{esc(e['scope_key'] or '')}</td><td>{esc(e['reason'])}</td>"
            f"<td>{esc((e['detail'] or '')[:80])}</td></tr>"
            for e in events
        )
        events_card = ui.table(
            ["时间", "类型", "Scope", "原因", "详情"],
            event_rows or '<tr><td colspan="5" class="muted">暂无行为记录</td></tr>',
            tips=["发生时间", "事件类型", "所属会话", "触发原因", "详情"],
            empty="暂无行为记录",
            table_id="behavior-events",
            filterable=False,
        )

        stats = ui.stats_grid(
            [
                ("心情", data["state"].get("mood") or "-", "当前心情阶梯"),
                ("时段", data["time"]["period"], "角色时区的当前时段"),
                ("睡觉中", "是" if data["time"]["is_sleeping"] else "否", "由作息门控"),
                ("勿扰", "是" if data["time"]["in_dnd"] else "否", ""),
                ("调度 tick", scheduler["ticks"], "共享调度器执行次数"),
                ("今日主动", initiative.get("sent_today_total", 0), "主动消息今日已发"),
            ]
        )

        body = (
            sandbox_page._tabs("/sandbox/chat")
            + stats
            + settings_body
            + ui.card("手动触发", manual_html, tip_text="只在这里后台执行，不会发到 QQ")
            + ui.card("回复预演", simulator_html, tip_text="预览延迟与分段，不会发送")
            + ui.card("最近行为记录", events_card, tip_text="行为引擎做了什么、为什么")
        )
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                body,
                subtitle="私聊回复 / 群聊参与 / 主动回复（她怎么做人，由沙盒决定）",
            ),
            content_type="text/html",
        )

    async def _behavior_legacy_redirect(self, request: web.Request) -> web.Response:
        raise web.HTTPFound("/sandbox/chat")

    async def _behavior_settings_save(self, request: web.Request) -> web.Response:
        form = await request.post()
        await self._behavior.save_settings(dict(form))
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                '<div class="card">设置已保存并热加载</div>'
                '<meta http-equiv="refresh" content="1;url=/sandbox/chat">',
            ),
            content_type="text/html",
        )

    async def _behavior_preview(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._behavior.preview(dict(form))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>Preview 结果（未发送任何消息）</h3><pre>{esc(rendered)}</pre>'
                '<p><a href="/sandbox/chat">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _behavior_test_response(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._behavior.test_response(str(form.get("text", "你好")))
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>测试回复（未发送）</h3>'
                f"<pre>{esc(rendered)}</pre><p><a href='/sandbox/chat'>返回</a></p></div>",
            ),
            content_type="text/html",
        )

    async def _behavior_trigger(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        await request.post()
        if action == "mood_up":
            result = await self._behavior.test_state_transition(+1)
        elif action == "mood_down":
            result = await self._behavior.test_state_transition(-1)
        elif action == "reset_state":
            result = await self._behavior.reset_state()
        elif action == "test_initiative":
            preview = await self._behavior.preview(
                {"topic": "未完成的项目", "relationship": "familiar"}
            )
            result = preview.get("initiative", preview)
        else:
            result = {"ok": False, "detail": f"unknown action: {action}"}
        rendered = json.dumps(result, ensure_ascii=False, indent=2)
        return web.Response(
            text=layout(
                "沙盒 · 对话行为",
                "/sandbox",
                f'<div class="card"><h3>{esc(action)}</h3>'
                f"<pre>{esc(rendered)}</pre><p><a href='/sandbox/chat'>返回</a></p></div>",
            ),
            content_type="text/html",
        )

    # --------------------------------------------------------------- topics

    async def _topics_page(self, request: web.Request) -> web.Response:
        scope_key = request.query.get("scope", "")
        status = request.query.get("status", "")
        topics = await self._behavior.list_topics(scope_key=scope_key, status=status)
        status_options = "".join(
            f'<option value="{s}" {"selected" if s == status else ""}>{s or "全部"}</option>'
            for s in ("", "active", "waiting", "resolved", "forgotten")
        )
        rows = "".join(
            f"<tr><td>{t['id']}</td><td>{esc(t['scope_key'])}</td><td>{esc(t['title'])}</td>"
            f"<td>{esc(t['status'])}</td><td>{t['importance']:.2f}</td>"
            f"<td class='muted'>{esc(format_ts(t['last_discussed_at']))}</td><td>"
            f"<form class='inline' method='post' action='/api/topics/resolve/{t['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>解决</button></form>"
            f"<form class='inline' method='post' action='/api/topics/forget/{t['id']}'>"
            f"<button class='btn btn-secondary btn-sm'>遗忘</button></form>"
            f"<form class='inline' method='post' action='/api/topics/delete/{t['id']}'>"
            f"<button class='btn btn-danger btn-sm'>删除</button></form></td></tr>"
            for t in topics
        )
        body = (
            '<div class="card"><form method="get" action="/topics">'
            f'<label>Scope (user:123 / group:456)</label><input name="scope" value="{esc(scope_key)}">'
            f'<label>状态</label><select name="status">{status_options}</select>'
            "<p><button class='btn btn-primary'>筛选</button></p></form></div>"
            f'<div class="card"><h3>Topics ({len(topics)})</h3>'
            "<table><tr><th>ID</th><th>Scope</th><th>Title</th><th>Status</th>"
            "<th>Imp</th><th>Last</th><th></th></tr>"
            + (rows or '<tr><td colspan="7" class="muted">暂无话题</td></tr>')
            + "</table></div>"
        )
        return web.Response(
            text=layout("话题", "/topics", body, subtitle="还没聊完的事，以及她的主动话题来源"),
            content_type="text/html",
        )

    async def _api_topic_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        topic_id = int(request.match_info["topic_id"])
        ok = await self._behavior.topic_action(action, topic_id)
        raise web.HTTPFound(f"/topics?updated={1 if ok else 0}")

    # -------------------------------------------------------- memory (v0.5)

    async def _memory_search_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        mode = str(form.get("mode") or request.query.get("mode", "hybrid"))
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()

        results_html = ""
        if query:
            data = await self._memory_admin.search(query, mode=mode, scope_key=scope_key, limit=15)
            rows = "".join(
                f"<tr><td>{esc(r.get('final') if r.get('final') is not None else '-')}</td>"
                f"<td>{esc(r.get('semantic'))}</td><td>{esc(r.get('keyword'))}</td>"
                f"<td>{esc(r.get('layer'))}</td><td>{esc(r.get('category'))}</td>"
                f"<td>{esc(r.get('scope_key'))}</td><td>{esc(r.get('origin'))}</td>"
                f"<td><a href='/memory/detail/{r['id']}'>{r['id']}</a></td>"
                f"<td>{esc(r.get('content'))}</td></tr>"
                for r in data.get("results", [])
            )
            note = (
                ""
                if data.get("semantic_available")
                else (
                    '<p class="muted">语义检索不可用（Embedding 未配置或失败）— 已降级为关键词+元数据排序</p>'
                )
            )
            results_html = (
                f'<div class="card"><h3>结果 ({len(data.get("results", []))}) — mode={esc(mode)}</h3>{note}'
                "<table><tr><th>final</th><th>semantic</th><th>keyword</th><th>layer</th>"
                "<th>type</th><th>scope</th><th>来源</th><th>ID</th><th>内容</th></tr>"
                + (rows or '<tr><td colspan="9" class="muted">无结果</td></tr>')
                + "</table></div>"
            )

        body = (
            self._memory_tabs("/memory/search")
            + f"""
<div class="card"><h3>记忆搜索</h3><form method="post" action="/memory/search">
<label>查询</label><input name="q" value="{esc(query)}">
<label>模式</label><select name="mode">
<option value="hybrid" {"selected" if mode == "hybrid" else ""}>hybrid（语义+关键词+元数据）</option>
<option value="semantic" {"selected" if mode == "semantic" else ""}>semantic（仅语义）</option>
<option value="keyword" {"selected" if mode == "keyword" else ""}>keyword（仅关键词）</option>
</select>
<label>Scope（留空=全部；user:123 / group:456）</label>
<input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">搜索</button></p></form></div>
{results_html}"""
        )
        return web.Response(
            text=layout(
                "记忆 · 搜索", "/memory", body, subtitle="语义 + 关键词混合检索，可看每项得分"
            ),
            content_type="text/html",
        )

    async def _memory_timeline_page(self, request: web.Request) -> web.Response:
        scope_key = request.query.get("scope", "")
        memories = await self._memory_admin.timeline(scope_key, limit=300)
        rows = "".join(
            f"<tr><td class='muted'>{esc(format_ts(m.get('event_at') or m.get('created_at')))}</td>"
            f"<td>{esc(m['layer'])}</td><td>{esc(m['category'])}</td>"
            f"<td>{esc(m['status'])}</td><td>{esc(m['scope_key'])}</td>"
            f"<td><a href='/memory/detail/{m['id']}'>{m['id']}</a></td>"
            f"<td>{esc(m.get('summary') or m['content'])}</td></tr>"
            for m in memories
        )
        body = (
            self._memory_tabs("/memory/timeline")
            + f"""
<div class="card"><form method="get" action="/memory/timeline">
<label>Scope（留空=全部）</label><input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">筛选</button></p></form></div>
<div class="card"><h3>时间线 ({len(memories)})</h3>
<table><tr><th>时间</th><th>层</th><th>类型</th><th>状态</th><th>Scope</th><th>ID</th><th>内容</th></tr>
{rows or '<tr><td colspan="7" class="muted">暂无记忆</td></tr>'}</table></div>"""
        )
        return web.Response(
            text=layout(
                "记忆 · 时间线",
                "/memory",
                body,
                subtitle="她的记忆是怎么长出来的（含被取代的旧事实）",
            ),
            content_type="text/html",
        )

    async def _memory_detail_page(self, request: web.Request) -> web.Response:
        memory_id = int(request.match_info["memory_id"])
        data = await self._memory_admin.detail(memory_id)
        if not data:
            return web.Response(
                text=layout(
                    "记忆",
                    "/memory",
                    self._memory_tabs("/memory") + '<div class="card">记忆不存在</div>',
                ),
                content_type="text/html",
            )
        memory = data["memory"]
        relations = data["relations"]
        previous = data["supersedes"]

        rel_rows = (
            "".join(
                f"<tr><td>{esc(r['relation'])}</td><td>{r['to_id']}</td>"
                f"<td>{esc(r.get('related_status') or '')}</td>"
                f"<td>{esc((r.get('related_content') or '')[:80])}</td></tr>"
                for r in relations
            )
            or '<tr><td colspan="4" class="muted">无关联</td></tr>'
        )
        prev_html = "".join(
            f"<li>#{p['id']}（{esc(p['status'])}）{esc(p['content'])}</li>" for p in previous
        )
        body = (
            self._memory_tabs("/memory")
            + f"""
<div class="card"><h3>记忆 #{memory["id"]}</h3>
<table>
<tr><th>内容</th><td>{esc(memory["content"])}</td></tr>
<tr><th>Summary</th><td>{esc(memory.get("summary") or "-")}</td></tr>
<tr><th>Layer / Type</th><td>{esc(memory["layer"])} / {esc(memory["category"])}</td></tr>
<tr><th>Scope</th><td>{esc(memory["scope_key"])}</td></tr>
<tr><th>Status</th><td>{esc(memory["status"])}</td></tr>
<tr><th>Importance / Confidence</th><td>{memory["importance"]:.2f} / {memory["confidence"]:.2f}</td></tr>
<tr><th>来源</th><td>{esc(memory["source"])}</td></tr>
<tr><th>使用次数</th><td>{memory["use_count"]}（最近 {esc(format_ts(memory.get("last_used_at")))}）</td></tr>
<tr><th>创建 / 更新</th><td>{esc(format_ts(memory["created_at"]))} / {esc(format_ts(memory["updated_at"]))}</td></tr>
<tr><th>有效期</th><td>{esc(format_ts(memory.get("valid_from")))} ~ {esc(format_ts(memory.get("valid_until")))}</td></tr>
</table>
<p><form class="inline" method="post" action="/api/memory/activate/{memory["id"]}"><button class="btn btn-secondary btn-sm">激活</button></form>
<form class="inline" method="post" action="/api/memory/archive/{memory["id"]}"><button class="btn btn-secondary btn-sm">归档</button></form>
<form class="inline" method="post" action="/api/memory/reembed/{memory["id"]}"><button class="btn btn-secondary btn-sm">重新向量化</button></form>
<form class="inline" method="post" action="/api/memory/delete/{memory["id"]}"><button class="btn btn-danger btn-sm">删除</button></form></p>
</div>
<div class="card"><h3>替代的历史记忆</h3><ul>{prev_html or '<li class="muted">无</li>'}</ul></div>
<div class="card"><h3>关联关系</h3>
<table><tr><th>关系</th><th>目标 ID</th><th>状态</th><th>内容</th></tr>{rel_rows}</table></div>"""
        )
        return web.Response(
            text=layout("记忆 · 详情", "/memory", body, subtitle="一条记忆的来源、关系与演化"),
            content_type="text/html",
        )

    async def _memory_health_page(self, request: web.Request) -> web.Response:
        health = await self._memory_admin.health()
        embedding = health.get("embedding", {})
        scheduler = health.get("scheduler", {})
        last = health.get("last_consolidation") or {}

        def stat(label: str, value: object) -> str:
            return f'<div class="stat"><span class="muted">{esc(label)}</span><b>{esc(value)}</b></div>'

        cards = "".join(
            stat(label, value)
            for label, value in (
                ("总计", health.get("total", 0)),
                ("Active", health.get("active", 0)),
                ("事件层", health.get("episodic", 0)),
                ("语义层", health.get("semantic", 0)),
                ("已被取代", health.get("superseded", 0)),
                ("已归档", health.get("archived", 0)),
                ("已过期", health.get("expired", 0)),
                ("Avg Importance", health.get("average_importance", 0)),
                ("Avg Confidence", health.get("average_confidence", 0)),
                ("向量化覆盖率", health.get("embedding_coverage", 0)),
                ("已向量化", health.get("embedded", 0)),
                (
                    "Semantic Search",
                    "on" if health.get("retrieval", {}).get("semantic_available") else "off",
                ),
                ("未落盘（outbox）", health.get("outbox", {}).get("pending", 0)),
            )
        )
        body = (
            self._memory_tabs("/memory/health")
            + f"""
<div class="grid">{cards}</div>
<div class="card"><h3>Embedding</h3>
<table>
<tr><th>Provider / Model</th><td>{esc(embedding.get("provider") or "-")} / {esc(embedding.get("model") or "-")}</td></tr>
<tr><th>Dimensions</th><td>{esc(embedding.get("dimensions") or "-")}</td></tr>
<tr><th>Cache hits / misses</th><td>{esc(embedding.get("hits", 0))} / {esc(embedding.get("misses", 0))}</td></tr>
<tr><th>Failures</th><td>{esc(embedding.get("failures", 0))}</td></tr>
<tr><th>Last error</th><td class="muted">{esc(embedding.get("last_error") or "-")}</td></tr>
</table></div>
<div class="card"><h3>Consolidation</h3>
<table>
<tr><th>Schedule</th><td>{esc(scheduler.get("schedule") or "-")}（running={esc(scheduler.get("running"))}, runs={esc(scheduler.get("runs", 0))}）</td></tr>
<tr><th>Next run in</th><td>{esc(scheduler.get("next_run_in"))}</td></tr>
<tr><th>Last report</th><td>{esc(last.get("summary") or "-")}</td></tr>
</table></div>
<div class="card"><h3>Policy / Retention</h3>
<pre>{esc(json.dumps({"policy": health.get("policy"), "retention": health.get("retention"), "retrieval": health.get("retrieval")}, ensure_ascii=False, indent=2))}</pre></div>"""
        )
        return web.Response(
            text=layout("记忆 · 健康度", "/memory", body, subtitle="向量化覆盖、配额与降级情况"),
            content_type="text/html",
        )

    async def _memory_debug_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        query = str(form.get("q") or request.query.get("q", "")).strip()
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()
        result_html = ""
        if query:
            data = await self._memory_admin.retrieval_debug(query, scope_key)
            rows = "".join(
                f"<tr><td>{r['id']}</td><td>{r['final']}</td><td>{r['semantic']}</td>"
                f"<td>{r['keyword']}</td><td>{r['importance']}</td><td>{r['confidence']}</td>"
                f"<td>{r['recency']}</td><td>{r['relationship']}</td><td>{r['topic_bonus']}</td>"
                f"<td>{r['temporal']}</td><td>{esc(r['origin'])}</td>"
                f"<td>{esc(r['content'])}</td></tr>"
                for r in data.get("results", [])
            )
            result_html = f"""
<div class="card"><h3>管线</h3>
<pre>{esc(json.dumps({k: v for k, v in data.items() if k != "results"}, ensure_ascii=False, indent=2))}</pre></div>
<div class="card"><h3>打分明细</h3>
<table><tr><th>ID</th><th>final</th><th>semantic</th><th>keyword</th><th>importance</th>
<th>confidence</th><th>recency</th><th>relationship</th><th>topic</th><th>temporal</th>
<th>来源</th><th>内容</th></tr>
{rows or '<tr><td colspan="12" class="muted">无结果</td></tr>'}</table></div>"""
        body = (
            self._memory_tabs("/memory/retrieval-debug")
            + f"""
<div class="card"><h3>检索调试器（仅后台）</h3><form method="post" action="/memory/retrieval-debug">
<label>查询</label><input name="q" value="{esc(query)}">
<label>Scope（留空=全部）</label><input name="scope" value="{esc(scope_key)}">
<p><button class="btn btn-primary">运行</button></p></form></div>
{result_html}"""
        )
        return web.Response(
            text=layout(
                "记忆 · 检索调试", "/memory", body, subtitle="为什么这条记忆会被想起（打分明细）"
            ),
            content_type="text/html",
        )

    async def _memory_embeddings_page(self, request: web.Request) -> web.Response:
        status = await self._memory_admin.embedding_status()
        body = (
            self._memory_tabs("/memory/embeddings")
            + f"""
<div class="card"><h3>向量状态</h3>
<pre>{esc(json.dumps(status, ensure_ascii=False, indent=2))}</pre>
<form class="inline" method="post" action="/memory/embeddings/rebuild"><button class="btn btn-secondary btn-sm">重建向量</button></form>
<form class="inline" method="post" action="/memory/embeddings/retry"><button class="btn btn-secondary btn-sm">重试失败项</button></form>
<form class="inline" method="post" action="/memory/embeddings/clear-cache"><button class="btn btn-danger btn-sm">清空缓存</button></form>
<p class="muted">重建/补齐在后台执行，不会阻塞 QQ 聊天。</p></div>"""
        )
        return web.Response(
            text=layout("记忆 · 向量", "/memory", body, subtitle="Embedding 服务状态、缓存与重建"),
            content_type="text/html",
        )

    async def _memory_embedding_action(self, request: web.Request) -> web.Response:
        action = request.match_info["action"]
        if action == "rebuild":
            result = await self._memory_admin.rebuild_embeddings(limit=500)
        elif action == "retry":
            result = await self._memory_admin.retry_failed()
        elif action == "clear-cache":
            result = await self._memory_admin.clear_embedding_cache()
        else:
            result = {"error": f"unknown action: {action}"}
        return web.Response(
            text=layout(
                "记忆 · 向量",
                "/memory",
                self._memory_tabs("/memory/embeddings")
                + f'<div class="card"><pre>{esc(json.dumps(result, ensure_ascii=False))}</pre>'
                '<p><a href="/memory/embeddings">返回</a></p></div>',
            ),
            content_type="text/html",
        )

    async def _memory_consolidation_page(self, request: web.Request) -> web.Response:
        status = await self._memory_admin.consolidation_status()
        last = status.get("last_report") or {}
        body = (
            self._memory_tabs("/memory/consolidation")
            + f"""
<div class="card"><h3>巩固状态</h3>
<pre>{esc(json.dumps(status, ensure_ascii=False, indent=2))}</pre></div>
<div class="card"><h3>手动执行</h3><form method="post" action="/memory/consolidation/run">
<label>Scope（留空=全部；user:123 / group:456）</label><input name="scope" value="">
<p><button class="btn btn-primary">运行巩固</button></p></form>
<p class="muted">去重合并、事件压缩、过期归档、配额清理；后台执行，不阻塞聊天。</p></div>
<div class="card"><h3>上次结果</h3><pre>{esc(last.get("summary") or "尚未运行")}</pre></div>"""
        )
        return web.Response(
            text=layout("记忆 · 巩固", "/memory", body, subtitle="后台整理记忆的时间与结果"),
            content_type="text/html",
        )

    async def _memory_consolidation_run(self, request: web.Request) -> web.Response:
        form = await request.post()
        result = await self._memory_admin.run_consolidation(str(form.get("scope", "")).strip())
        return web.Response(
            text=layout(
                "记忆 · 巩固",
                "/memory",
                self._memory_tabs("/memory/consolidation")
                + f'<div class="card"><pre>{esc(json.dumps(result, ensure_ascii=False, indent=2))}</pre>'
                '<p><a href="/memory/consolidation">返回</a></p></div>',
            ),
            content_type="text/html",
        )

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

    async def _memory_correction_page(self, request: web.Request) -> web.Response:
        form = await request.post() if request.method == "POST" else {}
        scope_key = str(form.get("scope") or request.query.get("scope", "")).strip()
        instruction = str(form.get("instruction") or request.query.get("instruction", "")).strip()
        service = self._correction
        targets = await service.targets()
        memories: list[dict[str, Any]] = []
        plan: dict[str, Any] | None = None
        message = str(form.get("result") or request.query.get("result", ""))
        kind = "warn" if message.startswith("!") else "ok"
        message = message.lstrip("!")
        if not message and request.method == "POST":
            message, kind = "把指令写得更具体一些，例如：把用户喜欢吃的东西改成草莓", "error"
        if scope_key:
            memories = await service.memories(scope_key)
        if request.method == "POST" and scope_key and instruction:
            try:
                plan = await service.plan(scope_key, instruction)
                message = "已生成方案，确认后才会写入"
                kind = "info"
            except Exception as exc:  # noqa: BLE001 - shown to the operator
                message, kind = str(exc), "error"
        body = correction_page.render(
            service,
            targets=targets,
            scope_key=scope_key,
            memories=memories,
            instruction=instruction,
            plan=plan,
            msg=message,
            kind=kind,
        )
        return web.Response(
            text=layout(
                "记忆修正",
                "/memory",
                self._memory_tabs("/memory/correction") + body,
                subtitle="用一句人话纠正她记住的事",
            ),
            content_type="text/html",
        )

    async def _memory_correction_apply(self, request: web.Request) -> web.Response:
        form = await request.post()
        raw = str(form.get("plan", "{}"))
        try:
            plan = json.loads(raw)
            result = await self._correction.apply(plan)
        except Exception as exc:  # noqa: BLE001
            raise web.HTTPFound(
                f"/memory/correction?result={quote('!修正失败：' + str(exc)[:200])}"
            ) from exc
        if result.get("changed"):
            message = f"已修正：{str(plan.get('before') or '')[:40]} → {str(result.get('content') or '')[:40]}"
        else:
            message = "没有需要修改的内容"
        scope_key = str(plan.get("scope_key") or "")
        raise web.HTTPFound(f"/memory/correction?scope={quote(scope_key)}&result={quote(message)}")

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

    def _sandbox_data(self) -> dict[str, Any]:
        sandbox = getattr(self._bot, "sandbox", None)
        data: dict[str, Any] = {"enabled": sandbox is not None}
        if sandbox is None:
            return data
        data.update(
            {
                "phase": sandbox.phase.value,
                "context": sandbox.context(),
                "spaces": [s.model_dump(mode="json") for s in sandbox.spaces.all()],
                "objects": [o.model_dump(mode="json") for o in sandbox.objects.all()],
                "inventories": {key: inv.items for key, inv in sandbox.inventories.all().items()},
                "needs": {
                    key: need.model_dump(mode="json") for key, need in sandbox.needs.all().items()
                },
                "action": (
                    sandbox.current_action.model_dump(mode="json")
                    if sandbox.current_action
                    else None
                ),
                "action_defs": [
                    d.model_dump(mode="json") for d in sandbox.actions.definitions.values()
                ],
                "pet": sandbox.pet_system.snapshot(),
                "social_spaces": [
                    s.model_dump(mode="json") for s in sandbox.social_spaces.values()
                ],
                "bible": sandbox.bible.model_dump(mode="json"),
            }
        )
        return data

    async def _sandbox_page(self, request: web.Request) -> web.Response:
        data = self._sandbox_data()
        sandbox = self._bot.sandbox
        if sandbox is not None:
            data["recent_events"] = await sandbox.store.recent_events(limit=12)
        body = sandbox_page._tabs("/sandbox") + sandbox_page.dashboard(data)
        return web.Response(
            text=layout("生活沙盒", "/sandbox", body, subtitle="她本来就在过自己的日子（v2.0）"),
            content_type="text/html",
        )

    async def _sandbox_inspectors_page(self, request: web.Request) -> web.Response:
        body = sandbox_page._tabs("/sandbox/inspectors") + sandbox_page.inspectors(
            self._sandbox_data()
        )
        return web.Response(
            text=layout("沙盒 · 实体与空间", "/sandbox", body, subtitle="小喵、公寓、冰箱、库存"),
            content_type="text/html",
        )

    async def _sandbox_needs_page(self, request: web.Request) -> web.Response:
        body = sandbox_page._tabs("/sandbox/needs") + sandbox_page.needs_page(self._sandbox_data())
        return web.Response(
            text=layout("沙盒 · 需求与动作", "/sandbox", body, subtitle="她为什么做这件事"),
            content_type="text/html",
        )

    async def _sandbox_bible_page(self, request: web.Request) -> web.Response:
        msg = request.query.get("msg", "")
        body = sandbox_page._tabs("/sandbox/bible") + sandbox_page.bible_page(self._sandbox_data())
        if msg:
            body = f"<div class='card'><p class='muted'>{ui.esc(msg)}</p></div>" + body
        return web.Response(
            text=layout("沙盒 · 人物档案", "/sandbox", body, subtitle="Canonical Source 与覆盖率"),
            content_type="text/html",
        )

    async def _sandbox_trace_page(self, request: web.Request) -> web.Response:
        data = self._sandbox_data()
        sandbox = self._bot.sandbox
        if sandbox is not None:
            data["replay"] = await sandbox.replay(limit=120)
        body = sandbox_page._tabs("/sandbox/trace") + sandbox_page.trace_page(data)
        return web.Response(
            text=layout("沙盒 · 回放", "/sandbox", body, subtitle="结构化轨迹（不含思维链）"),
            content_type="text/html",
        )

    async def _sandbox_simulate(self, request: web.Request) -> web.Response:
        form = await request.post()
        try:
            hours = min(168.0, max(1.0, float(str(form.get("hours", "24")))))
        except ValueError:
            hours = 24.0
        sandbox = getattr(self._bot, "sandbox", None)
        if sandbox is None:
            raise web.HTTPFound("/sandbox/trace?msg=沙盒未启用")
        backup = sandbox._backup_state()  # noqa: SLF001 - simulator is admin-gated
        before = sandbox.current_action
        try:
            sim = await sandbox.simulate(hours=hours)
        finally:
            sandbox._restore_state(backup)  # noqa: SLF001 - dry run never sticks
            sandbox.current_action = before
        self._bot.log.info("[Web] sandbox dry-run %.0fh: %s", hours, sim)
        raise web.HTTPFound(
            "/sandbox/trace?msg=" + quote(f"干跑 {hours:.0f}h 完成（未发送任何 QQ）")
        )

    async def _api_sandbox_control(self, request: web.Request) -> web.Response:
        import json as _json

        payload = _json.loads(await request.text() or "{}")
        action = str(payload.get("action", ""))
        sandbox = getattr(self._bot, "sandbox", None)
        lifecycle = getattr(self._bot, "lifecycle_manager", None)
        result: dict[str, Any] = {"ok": False}
        if sandbox is None:
            result["reason"] = "sandbox_disabled"
        elif action == "pause":
            sandbox.phase = sandbox.phase.__class__.paused
            result = {"ok": True, "phase": sandbox.phase.value}
        elif action == "resume":
            sandbox.phase = sandbox.phase.__class__.running
            result = {"ok": True, "phase": sandbox.phase.value}
        elif action == "reset" and lifecycle is not None:
            result = await lifecycle.reset_character(confirm=bool(payload.get("confirm")))
        elif action == "reinitialize":
            await sandbox.reinitialize()
            result = {"ok": True}
        elif action == "reset_report" and lifecycle is not None:
            result = {"ok": True, "report": await lifecycle.reset_report()}
        return web.json_response(result)

    # ------------------------------------------------------ stickers (v1.1)
