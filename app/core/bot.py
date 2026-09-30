"""CatooBot core: assembles config, adapter, AI, character, memory, web.

The Bot is protocol-agnostic: it holds an :class:`Adapter` and never imports
OneBot/NapCat code directly. Since v0.3 the QQ surface is natural-language
only — the command registry stays as internal infrastructure but nothing
mounts QQ user commands.
"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.adapters import Adapter
from app.adapters.onebot_v11.api import BotApi
from app.agent.runtime import AgentRuntime
from app.ai.engine import AIEngine
from app.behavior.engine import CharacterBehaviorEngine
from app.behavior.presence import PresenceResolver
from app.behavior.scheduler import BehaviorScheduler
from app.character.persona_manager import PersonaManager
from app.character.runtime import CharacterRuntime
from app.commands.registry import CommandRegistry
from app.commands.router import CommandRouter
from app.config.settings import AppConfig
from app.core.event_bus import EventBus
from app.core.lifecycle import Lifecycle
from app.core.metrics import Metrics
from app.core.router import CoreRouter
from app.database.database import Database
from app.media.runtime import MediaRuntime
from app.memory.consolidation import ConsolidationScheduler, MemoryConsolidator
from app.memory.embedding import EmbeddingService
from app.memory.extraction import MemoryExtractor
from app.memory.manager import MemoryManager
from app.message.event import Event
from app.permissions.manager import PermissionManager
from app.plugins.loader import PluginLoader
from app.response.delivery import MessageDelivery
from app.response.planner import CharacterResponsePlanner
from app.response.timing import ReplyTiming
from app.social.cognition import SocialCognitionEngine
from app.tools.runtime import ToolRuntime
from app.utils import console
from app.utils.logger import get_logger
from app.utils.narrator import narrate
from app.world.models import ScheduledJob
from app.world.runtime import WorldRuntime, build_world

if TYPE_CHECKING:
    from app.web.server import WebServer


class Bot:
    """The runtime object every subsystem and plugin gets a reference to."""

    def __init__(self, config: AppConfig, adapter: Adapter) -> None:
        self.config = config
        self.adapter: Adapter = adapter
        self.log = get_logger("CatooBot")

        self.event_bus = EventBus()
        self.commands = CommandRegistry()
        self.permissions = PermissionManager(config.permissions)
        self.database = Database(config.database)
        self.metrics = Metrics()
        self.ai = AIEngine(
            config.ai,
            self.database,
            router_event_listener=self._on_router_event,
        )
        # v0.5: semantic memory (embeddings) is opt-in and independent from chat models.
        self.embeddings = (
            EmbeddingService.from_config(
                config.memory.semantic.embedding,
                self.database,
                ai_providers=config.ai.providers,
            )
            if config.memory.enabled and config.memory.semantic.enabled
            else None
        )
        self.memory = (
            MemoryManager(config.memory, self.database, embeddings=self.embeddings)
            if config.memory.enabled
            else None
        )
        self.consolidator = (
            MemoryConsolidator(config.memory, self.memory) if self.memory else None
        )
        self.consolidation_scheduler = (
            ConsolidationScheduler(self.consolidator, config.memory.consolidation.schedule)
            if self.consolidator is not None
            else None
        )
        self.personas = PersonaManager(config.character, self.database)
        self.character = CharacterRuntime(
            persona_manager=self.personas,
            engine=self.ai,
            memory_manager=self.memory,
            database=self.database,
        )
        self.extractor = (
            MemoryExtractor(config.memory, self.ai, self.memory)
            if self.memory is not None
            else None
        )
        self.character.extractor = self.extractor
        self.relationships = self.character.relationships

        # v0.4 behaviour layer: presence → activity/initiative → response plan.
        self.presence = PresenceResolver(config.character.timezone, config.behavior.schedule)
        self.behavior = CharacterBehaviorEngine(
            config.behavior,
            self.presence,
            self.character.states,
            relationships=self.relationships,
            database=self.database,
            memory=self.memory,
        )
        self.behavior.topics = self.behavior.topics
        self.reply_timing = ReplyTiming(config.behavior.reply, self.presence)
        self.response_planner = CharacterResponsePlanner(
            self.reply_timing, config.behavior.chunking
        )
        self.response_delivery = MessageDelivery(self)
        self.scheduler = BehaviorScheduler(self, self.behavior)

        # v0.8 persistent world: background life that continues between messages.
        # It rides the ONE scheduler above — no second loop in the process.
        self.world: WorldRuntime | None = None
        if config.world.enabled:
            self.world = build_world(
                config.world,
                database=self.database,
                state_manager=self.character.states,
                timezone=config.character.timezone,
                presence=self.presence,
            )
            self.character.world = self.world
            self.behavior.world = self.world  # world owns activity while enabled
            self.world.narrate_ticks = config.logging.narrate_world_ticks

        # v0.6 tool runtime: registry + policy + executor (tools are opt-in).
        self.tools = ToolRuntime(config.tools, self.database)
        self.character.tools = self.tools
        # v0.7 agent runtime: multi-step goals on top of the tool runtime.
        self.agent = AgentRuntime(config.agent, self.ai, self.tools, self.database)
        self.character.agent = self.agent

        # v0.9 social cognition: *when* the character joins a group conversation.
        # Reuses behavior/topic/memory/state/world — no second engine, no dice.
        self.social = SocialCognitionEngine(
            config=config.social, bot=self, database=self.database
        )

        # v1.1 media + sticker runtime: image understanding, sticker library,
        # acquisition (background) and expression. Ordinary images never become
        # stickers — the boundary lives in app/media/normalizer.
        self.media = MediaRuntime(
            config=config.media, engine=self.ai, database=self.database
        )

        self.lifecycle = Lifecycle(self)
        self.router = CommandRouter(self, self.commands, prefix=config.bot.command_prefix)
        self.core_router = CoreRouter(self)
        self.plugins = PluginLoader(self)
        self.api = BotApi(self)
        # Single AuthService instance: the WebServer logs users in with it and the
        # config page changes passwords through it.
        from app.web.auth import AuthService

        self.web_auth = AuthService(config.web, self.database)
        self.web: WebServer | None = None  # attached in start() when enabled
        self.started_at: float | None = None

    # ------------------------------------------------------------- identity

    @property
    def name(self) -> str:
        return self.config.bot.name

    @property
    def self_id(self) -> int | None:
        return self.adapter.self_id

    @property
    def is_connected(self) -> bool:
        return self.adapter.connected

    # ------------------------------------------------------------------ API

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Raw OneBot API passthrough (plugins should prefer ``bot.api``)."""
        return await self.adapter.call_api(action, params, timeout)

    async def handle_event(self, event: Event) -> None:
        """Adapter sink: every parsed protocol event enters the bus here."""
        await self.event_bus.emit(event)

    def _on_router_event(self, event: str, model_name: str | None) -> None:
        if event == "rate_limited":
            self.metrics.inc("rate_limited")

    # ------------------------------------------------------------ lifecycle

    async def start(self) -> None:
        """Load DB + plugins, wire the event bus, start adapter and WebUI."""
        self.started_at = time.time()

        story = narrate()
        try:
            await self.database.connect()
            story.boot_step("数据库已连接", detail=str(self.config.database.sqlite_path))
        except Exception:  # noqa: BLE001 - DB trouble must not stop startup
            self.log.exception("Database unavailable, continuing without persistence")
            story.boot_step("数据库不可用（继续运行，但不会落盘）", ok=False)

        # QQ surface: message recording + character chat. No command dispatch.
        self.event_bus.on("message", self.core_router.on_message)

        await self.plugins.load_all()
        loaded = list(self.plugins.loaded)
        story.boot_step(
            "插件已加载", detail=", ".join(loaded) if loaded else "无（内置功能已就绪）"
        )

        try:
            await self.character.start()
            persona = self.character.personas.persona
            story.boot_step(
                "角色已就绪",
                detail=f"{persona.identity.name or persona.name}"
                + (
                    "（人设已配置）"
                    if persona.is_configured()
                    else "（人设待配置: WebUI /character）"
                ),
            )
        except Exception:  # noqa: BLE001
            self.log.exception("Character runtime failed to start (chat continues without persona)")
            story.boot_step("角色运行时启动失败（降级为普通聊天）", ok=False)

        # Memory wiring: topic-aware retrieval + background consolidation.
        self.character.topics = self.behavior.topics
        if self.memory is not None and self.embeddings is not None:
            semantic = self.embeddings.available
            self.log.info(
                "[Memory.Embedding] mode=%s",
                "semantic+keyword" if semantic else "keyword only (fallback)",
            )
            story.boot_step(
                "长期记忆已就绪",
                detail="语义+关键词混合检索" if semantic else "关键词检索（Embedding 未启用/降级）",
            )
        shared_loop = self.behavior.enabled or (self.world is not None and self.world.enabled)
        if self.consolidation_scheduler is not None and self.consolidation_scheduler.enabled:
            if shared_loop:
                # Rides the shared scheduler instead of owning a second loop (§34).
                self.consolidation_scheduler.external_driver = True
                self.scheduler.register_job(
                    ScheduledJob(
                        name="memory_consolidation",
                        handler=self.consolidation_scheduler.tick,
                        interval_seconds=float(
                            self.consolidation_scheduler.interval_seconds
                        ),
                        run_immediately=False,
                        misfire_policy="skip",
                    )
                )
            else:
                await self.consolidation_scheduler.start()

        if self.config.tools.enabled:
            await self.tools.start()
            self.log.info(
                "[Tool] runtime ready: %d tool(s) enabled (%s),"
                " decision=%s, budget=%d/turn",
                sum(
                    1
                    for t in self.tools.registry.all()
                    if self.tools.is_enabled(t.metadata.name)
                ),
                ", ".join(self.tools.registry.names()),
                self.config.tools.decision_mode,
                self.config.tools.max_calls_per_turn,
            )
            story.boot_step(
                "工具运行时已就绪",
                detail=", ".join(self.tools.registry.names()) or "无可用工具",
            )

        if self.agent.enabled:
            paused = await self.agent.mark_running_tasks_paused()
            self.log.info(
                "[Agent] runtime ready (autonomy=%s, budget=%d steps/%d calls/%ds%s)",
                self.config.agent.autonomy,
                self.config.agent.budget.max_steps,
                self.config.agent.budget.max_tool_calls,
                int(self.config.agent.budget.max_execution_seconds),
                f", {paused} interrupted task(s) paused" if paused else "",
            )
            story.boot_step(
                "Agent 已就绪",
                detail=f"autonomy={self.config.agent.autonomy}"
                + (f", {paused} 个未完成任务已暂停" if paused else ""),
            )

        if self.behavior.enabled:
            state = await self.behavior.current_state()
            self.log.info(
                "[Behavior] active (tz=%s, activity=%s, initiative=%s, group=%s)",
                self.presence.timezone_name,
                state.activity or "-",
                "on" if self.config.behavior.initiative.enabled else "off",
                "on" if self.config.behavior.group.participation_enabled else "off",
            )
            story.boot_step(
                "行为引擎已就绪",
                detail=(
                    f"时区 {self.presence.timezone_name}"
                    f", 主动聊天{'开' if self.config.behavior.initiative.enabled else '关'}"
                    f", 群聊参与"
                    f"{'开' if self.config.behavior.group.participation_enabled else '关'}"
                ),
            )

        await self._start_world()

        # v1.1: background sticker indexer (never blocks QQ from coming up, §16).
        if self.media.enabled and self.config.media.indexer_enabled:
            asyncio.create_task(self._run_sticker_indexer())

        # One scheduler for the whole process: behaviour, world, memory upkeep.
        if self.behavior.enabled or (self.world is not None and self.world.enabled):
            await self.scheduler.start()
            story.boot_step(
                "后台调度器已启动",
                detail=", ".join(job.name for job in self.scheduler.jobs()) or "无任务",
            )

        if self.ai.enabled:
            self.log.info(
                "AI engine active with %d model(s): %s",
                self.ai.router.model_count,
                ", ".join(s.spec.name for s in self.ai.router.states.values()),
            )
            try:
                from app.web.services.admin import AdminService

                await AdminService(self).restore_model_overrides()
            except Exception:  # noqa: BLE001
                self.log.exception("Failed to restore model overrides")

        try:
            from app.web.services.behavior import BehaviorService

            await BehaviorService(self).restore_overrides()
        except Exception:  # noqa: BLE001
            self.log.exception("Failed to restore behaviour overrides")

        await self.adapter.start()
        story.boot_step("OneBot 适配器已监听", detail=self.config.onebot.url)
        self.lifecycle.mark_ready()

        if self.config.web.enabled:
            try:
                from app.web.server import WebServer

                self.web = WebServer(self.config.web, self)
                await self.web.start()
                story.boot_step(
                    "WebUI 已启动",
                    detail=f"http://{self.config.web.host}:{self.config.web.port}",
                )
            except Exception:  # noqa: BLE001 - WebUI failure must not kill the bot
                self.log.exception("WebUI failed to start; QQ chat continues unaffected")
                self.web = None
                story.boot_step("WebUI 启动失败（QQ 聊天不受影响）", ok=False)
        else:
            self.log.info("WebUI disabled (set web.enabled: true to manage via browser)")
            story.boot_step("WebUI 未启用", detail="web.enabled: true 可在浏览器里管理", ok=True)

    async def print_ready_panel(self) -> None:
        """The 'all set' box: where QQ connects, where to manage, how she is."""
        story = narrate()
        if not story.enabled:
            return
        lines: list[str] = []
        lines.append(f"QQ 接入    {self.config.onebot.url}")
        lines.append(
            f"模型       {self.ai.router.model_count} 个"
            + (
                "  ·  "
                + ", ".join(
                    state.spec.name for state in self.ai.router.states.values()
                )
                if self.ai.router.model_count
                else "（AI 未启用）"
            )
        )
        if self.web is not None:
            lines.append(
                f"管理后台   http://{self.config.web.host}:{self.config.web.port}"
                f"  ·  账号 {self.config.web.username}"
            )
        else:
            lines.append("管理后台   未启用（web.enabled: true 可打开）")
        persona = self.character.personas.persona if self.character is not None else None
        if persona is not None:
            lines.append(
                f"角色       {persona.identity.name or persona.name}"
                f"  ·  {persona.identity.occupation or '——'}"
            )
        if self.world is not None and self.world.enabled:
            moment = self.world.clock.snapshot()
            state = self.world.state.state
            where = f"（{state.location}）" if state.location else ""
            lines.append(
                f"世界       {moment.time_text} {moment.period}"
                f"  ·  {state.activity or '发呆'}{where}  ·  心情 {state.mood}"
            )
            jobs = ", ".join(job.name for job in self.scheduler.jobs())
            lines.append(f"后台任务   {jobs or '无'}")
        story.blank()
        story.panel("CatooBot 已就绪 · developer Rinsora", lines, accent="bright_green")
        story.blank()
        story.say(
            "world",
            console.paint("她已经开始过自己的日子了", "bright_cyan"),
            detail="QQ 里直接说话即可，管理看 WebUI /world",
        )

    async def _run_sticker_indexer(self) -> None:
        """Background scan of the sticker import directory (§15-§17)."""
        try:
            stats = await self.media.indexer.scan()
            self.log.info(
                "[Media.Indexer] scan done: %s", stats
            )
        except Exception:  # noqa: BLE001 - indexing must never kill the bot
            self.log.exception("[Media.Indexer] startup scan failed")

    async def _start_world(self) -> None:
        """Recover the persistent world and put its jobs on the shared scheduler."""
        world = self.world
        if world is None or not world.enabled:
            return
        try:
            await world.state.load()
            report = await world.restore()
        except Exception:  # noqa: BLE001 - the world must never block startup
            self.log.exception("[World] Start-up recovery failed; continuing")
            return

        self.scheduler.register_job(
            ScheduledJob(
                name="world_tick",
                handler=world.tick,
                interval_seconds=float(self.config.world.tick_seconds),
                run_immediately=True,   # settle the world as soon as the bot is up
                misfire_policy=self.config.world.missed_event_policy,
            )
        )
        self.scheduler.register_job(
            ScheduledJob(
                name="world_prune",
                handler=lambda: world.events.prune(keep_days=60),
                interval_seconds=86400.0,
                run_immediately=False,
                misfire_policy="skip",
                max_runs_per_day=1,
            )
        )
        self.log.info(
            "[World] active (tz=%s, period=%s, activity=%s, tick=%ss, recovery=%s)",
            world.clock.timezone_name,
            world.clock.snapshot().period,
            world.state.state.activity or "-",
            self.config.world.tick_seconds,
            (
                f"{report['downtime_seconds'] / 60:.0f}min/{report['policy']}"
                if report.get("downtime_seconds")
                else "fresh"
            ),
        )

    async def shutdown(self) -> None:
        """Graceful stop: schedulers, plugins, web, adapter, database."""
        if self.world is not None and self.world.enabled:
            try:
                await self.world.save_snapshot()
            except Exception:  # noqa: BLE001 - a failed snapshot must not block exit
                self.log.exception("[World] Final snapshot failed (ignored)")
        if self.consolidation_scheduler is not None:
            await self.consolidation_scheduler.stop()
        await self.scheduler.stop()
        await self.plugins.unload_all()
        if self.web is not None:
            try:
                await self.web.stop()
            except Exception:  # noqa: BLE001
                self.log.exception("WebUI stop raised (ignored)")
        if self.extractor is not None:
            await self.extractor.wait_idle()
        if self.embeddings is not None:
            await self.embeddings.close()
        await self.tools.close()
        await self.ai.close()
        try:
            await self.adapter.stop()
        except Exception:  # noqa: BLE001
            self.log.exception("Adapter stop raised (ignored during shutdown)")
        await self.database.close()
        self.lifecycle.mark_stopped()
        farewell = ""
        if self.world is not None and self.world.enabled:
            try:
                farewell = f"下线前她正在{self.world.status_line()}"
            except Exception:  # noqa: BLE001 - cosmetic only
                farewell = ""
        narrate().quiet("世界已存档，CatooBot 退出了", detail=farewell)
        self.log.info("CatooBot stopped.")
