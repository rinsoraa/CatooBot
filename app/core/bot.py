"""CatooBot core: assembles config, adapter, AI, character, memory, web.

The Bot is protocol-agnostic: it holds an :class:`Adapter` and never imports
OneBot/NapCat code directly. Since v0.3 the QQ surface is natural-language
only — the command registry stays as internal infrastructure but nothing
mounts QQ user commands.

本文件同时引用 v0.8 / v1.1 / v2.0 §n（装配层混版）；裸 §N 才指 v2.0。"""

from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING, Any

from app.adapters import Adapter
from app.adapters.onebot_v11.api import BotApi
from app.agent.runtime import AgentRuntime
from app.ai.engine import AIEngine
from app.ai.usage import UsageRecorder
from app.behavior.engine import CharacterBehaviorEngine
from app.behavior.models import ScheduledJob
from app.behavior.presence import PresenceResolver
from app.behavior.scheduler import BehaviorScheduler
from app.character.persona_manager import PersonaManager
from app.character.runtime import CharacterRuntime
from app.commands.registry import CommandRegistry
from app.commands.router import CommandRouter
from app.config.settings import PROJECT_ROOT, AppConfig
from app.config.watcher import ConfigFileWatcher
from app.core.event_bus import EventBus
from app.core.lifecycle import Lifecycle
from app.core.metrics import Metrics
from app.core.router import CoreRouter
from app.core.watchdog import EventLoopWatchdog
from app.database.database import Database
from app.expression import ExpressionLearner, ExpressionStore
from app.media.runtime import MediaRuntime
from app.memory.consolidation import ConsolidationScheduler, MemoryConsolidator
from app.memory.embedding import EmbeddingService
from app.memory.extraction import MemoryExtractor
from app.memory.manager import MemoryManager
from app.memory.outbox import Outbox, outbox_path_for
from app.message.event import Event
from app.permissions.manager import PermissionManager
from app.plugins.loader import PluginLoader
from app.response.delivery import MessageDelivery
from app.response.planner import CharacterResponsePlanner
from app.response.timing import ReplyTiming
from app.social.cognition import SocialCognitionEngine
from app.social.feedback import ReplyFeedbackSettler, ReplyFeedbackStore
from app.tools.runtime import ToolRuntime
from app.utils import console
from app.utils.logger import get_logger
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.web.server import WebServer


class Bot:
    """The runtime object every subsystem and plugin gets a reference to."""

    def __init__(self, config: AppConfig, adapter: Adapter) -> None:
        self.config = config
        self.adapter: Adapter = adapter
        self.log = get_logger("CatooBot")
        self._clock = time.time

        self.event_bus = EventBus()
        self.commands = CommandRegistry()
        self.permissions = PermissionManager(config.permissions)
        self.database = Database(config.database)
        self.metrics = Metrics()
        # Task 15: per-call usage rows (tokens, latency, outcome) for the model page.
        # Task 16: single process, single loop — stalls freeze QQ and WebUI alike.
        self.watchdog = (
            EventLoopWatchdog(
                interval_seconds=config.logging.watchdog_interval_seconds,
                threshold_ms=config.logging.watchdog_threshold_ms,
                metrics=self.metrics,
            )
            if config.logging.watchdog_enabled
            else None
        )
        # Task 23: hand-edits to config.yaml are hot-reloaded (no restart).
        self.config_watcher = (
            ConfigFileWatcher(
                paths=[PROJECT_ROOT / "config" / "config.yaml"],
                interval_seconds=config.logging.watch_config_interval_seconds,
                on_change=self._reload_config_from_disk,
            )
            if config.logging.watch_config_enabled
            else None
        )
        self.ai_usage = (
            UsageRecorder(self.database, metrics=self.metrics) if config.ai.usage.enabled else None
        )
        self.ai = AIEngine(
            config.ai,
            self.database,
            router_event_listener=self._on_router_event,
            usage=self.ai_usage,
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
        # Task 14: writes the database refuses land here instead of vanishing.
        # The file lives beside the database it protects, so a test database
        # never writes into the operator's live data.
        outbox_file = outbox_path_for(config.database.url)
        self.outbox = Outbox(outbox_file, metrics=self.metrics) if outbox_file is not None else None
        self.memory = (
            MemoryManager(
                config.memory, self.database, embeddings=self.embeddings, outbox=self.outbox
            )
            if config.memory.enabled
            else None
        )
        # Task 20: reply-outcome observations (settled by the scheduler later).
        self.reply_feedback = ReplyFeedbackStore(
            self.database, outbox=self.outbox, metrics=self.metrics
        )
        if self.outbox is not None:
            self.outbox.register("reply_outcome", self.reply_feedback.replay_entry)
        self.consolidator = (
            MemoryConsolidator(config.memory, self.memory, engine=self.ai) if self.memory else None
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
        # Task 22: expression / 口癖 learning (opt-in; the plugin feeds it).
        self.expression_store = ExpressionStore(
            self.database, config.expression, clock=self._clock, metrics=self.metrics
        )
        self.expression_learner = ExpressionLearner(
            self.expression_store, config.expression, clock=self._clock, metrics=self.metrics
        )
        self.extractor = (
            MemoryExtractor(config.memory, self.ai, self.memory, metrics=self.metrics)
            if self.memory is not None
            else None
        )
        self.character.extractor = self.extractor
        self.character.expression_store = self.expression_store
        self.character.expression_config = config.expression
        self.character.metrics = self.metrics
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

        # v0.6 tool runtime: registry + policy + executor (tools are opt-in).
        self.tools = ToolRuntime(config.tools, self.database)
        self.character.tools = self.tools
        # v0.7 agent runtime: multi-step goals on top of the tool runtime.
        self.agent = AgentRuntime(config.agent, self.ai, self.tools, self.database)
        self.character.agent = self.agent

        # v0.9 social cognition: *when* the character joins a group conversation.
        # Reuses behavior/topic/memory/state/world — no second engine, no dice.
        self.social = SocialCognitionEngine(config=config.social, bot=self, database=self.database)

        # v1.1 media + sticker runtime: image understanding, sticker library,
        # acquisition (background) and expression. Ordinary images never become
        # stickers — the boundary lives in app/media/normalizer.
        self.media = MediaRuntime(config=config.media, engine=self.ai, database=self.database)

        # v1.2 character continuity + conversation turn runtime. The runtime
        # only buffers/classifies/decides; the chat plugin binds the actual
        # respond/deliver callbacks (adapter pattern, minimal intrusion).
        from app.continuity import ContinuityStore
        from app.continuity.manager import ContinuityManager
        from app.conversation.decision import ConversationDecisionEngine
        from app.conversation.runtime import ConversationTurnRuntime

        self.continuity: ContinuityManager | None = None
        if config.continuity.enabled:
            self.continuity = ContinuityManager(
                ContinuityStore(self.database, config.continuity),
                clock=self._clock,
            )
        self.conversation = ConversationTurnRuntime(
            config.conversation,
            ConversationDecisionEngine(config.conversation),
            clock=self._clock,
        )
        # v2.0 Character Life Sandbox: when enabled it IS her world — the
        # legacy WorldRuntime does not start at all (v2.0 §17-§19).
        self.sandbox = None
        self.lifecycle_manager = None
        if config.sandbox.enabled:
            try:
                from app.sandbox import (
                    BibleCompiler,
                    CharacterLifecycleManager,
                    SandboxRuntime,
                    SandboxStore,
                )
                from app.sandbox.ai import SandboxAIDecider

                bible = BibleCompiler(PROJECT_ROOT / config.sandbox.bible_path).compile()
                store = SandboxStore(self.database, clock=self._clock)
                self.sandbox = SandboxRuntime(
                    config.sandbox,
                    store,
                    bible=bible,
                    bot=self,
                    clock=self._clock,
                    ai_decider=SandboxAIDecider(self.ai),
                )
                self.lifecycle_manager = CharacterLifecycleManager(self.database, clock=self._clock)
                self.character.sandbox = self.sandbox
                # The sandbox owns her life; v1.2 continuity receives its events.
                self.sandbox.narrate_ticks = config.logging.narrate_world_ticks
                self.sandbox.continuity = self.continuity
                self.sandbox.state_sync = self._sync_sandbox_state
                # The sandbox is the truth for "is she asleep" (v2.0 §76/§77);
                # the clock window is only the sandbox-off fallback.
                self.presence.set_sleep_state_provider(self._sandbox_asleep)
            except Exception:  # noqa: BLE001 - sandbox failure must not stop startup
                self.log.exception("Sandbox initialization failed; continuing without it")
                self.sandbox = None

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
        """Model-router events feed the dashboard counters.

        ``request`` fires per provider attempt (including the one transient
        retry) and ``failed`` once per request that could not be answered by
        any model, so 请求/失败 ratio stays meaningful.
        """
        if event == "request":
            self.metrics.inc("ai_requests")
        elif event == "rate_limited":
            self.metrics.inc("rate_limited")
        elif event == "empty_finish_length":
            self.metrics.inc("ai_empty_finish_length")
        elif event == "failed":
            self.metrics.inc("ai_errors")

    # ------------------------------------------------------------ lifecycle

    async def _sync_sandbox_state(self, activity: str, location: str, energy: float) -> None:
        """The sandbox is the only writer of her life: mirror it onto state."""
        await self.character.states.update(
            activity=activity, location=location, energy=energy, reason="sandbox"
        )

    def _sandbox_asleep(self) -> bool | None:
        """Presence's source of truth for sleep: sandbox state, or None (off)."""
        sandbox = getattr(self, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            return None
        return bool(sandbox.is_asleep())

    async def _reload_config_from_disk(self) -> None:
        """Hot-reload a hand-edited config.yaml (Task 23 config watching).

        Runs the full load → models expansion → model-reference validation, then
        pushes the result through the config admin service. A broken edit is
        reported and the previous config stays live.
        """
        from app.config.settings import load_config
        from app.web.services.config_admin import ConfigAdminService

        try:
            new_config = load_config()
        except SystemExit as exc:
            self.log.error("[Config.Watch] 配置改动未生效（语法错误）：%s", exc)
            return
        except Exception:  # noqa: BLE001
            self.log.exception("[Config.Watch] 配置重载失败")
            return
        notes = await ConfigAdminService(self).apply(new_config)
        if notes:
            self.log.info("[Config.Watch] 配置已热重载；%s", "；".join(notes))
        else:
            self.log.info("[Config.Watch] 配置已热重载")

    async def _boot_sandbox(self) -> None:
        """Start (or reset-and-seed) the character life sandbox (v2.0 §120)."""
        assert self.sandbox is not None
        sandbox = self.sandbox
        stored_version = await sandbox.store.state_get("bible_version")
        first_v2_boot = not stored_version
        changed = bool(stored_version) and stored_version != sandbox.bible.version
        # v2.0 §3/§120: v2.0's first boot (or a changed bible) wipes the old
        # character's data once — archived to data/character_reset_backup first.
        if (changed or first_v2_boot) and self.config.sandbox.reset_on_bible_change:
            assert self.lifecycle_manager is not None
            self.log.warning(
                "[Sandbox] %s; resetting character data",
                (
                    f"bible changed ({stored_version} → {sandbox.bible.version})"
                    if changed
                    else "first v2.0 boot"
                ),
            )
            report = await self.lifecycle_manager.reset_character(confirm=True)
            await sandbox.store.state_set("bible_version", sandbox.bible.version)
            self.log.info(
                "[Sandbox] character reset: %d tables cleared (backup=%s)",
                len(report.get("removed", {})),
                report.get("backup", "-"),
            )
            if self.continuity is not None:
                await self.continuity.reload()
        await sandbox.start()
        # v2.0 §2: the bible is the canonical source for who she is — the WebUI
        # /character page and the chat prompt both read this persona.
        if self.config.sandbox.sync_persona_from_bible:
            try:
                from app.character.persona import Persona
                from app.sandbox.persona import build_persona_payload

                synced_version = await sandbox.store.state_get("persona_synced_version")
                needs_sync = (
                    not self.personas.persona.is_configured()
                    or synced_version != sandbox.bible.version
                )
                if needs_sync:
                    payload = build_persona_payload(sandbox.bible)
                    await self.personas.save(Persona.model_validate(payload))
                    await self.character.personas.load()
                    await sandbox.store.state_set("persona_synced_version", sandbox.bible.version)
                    self.log.info("[Sandbox] persona synced from bible %s", sandbox.bible.version)
            except Exception:  # noqa: BLE001 - persona sync must not stop boot
                self.log.exception("[Sandbox] persona sync from bible failed")
        narrate().world(
            f"她已经在过自己的日子了（{sandbox.status_line()}）",
            detail=f"沙盒就绪 · 模式 {'+'.join(sandbox.modes.ids()) or 'home'}",
        )

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

        # v2.0: bible change → one clean character reset, then seed the sandbox.
        if self.sandbox is not None:
            try:
                await self._boot_sandbox()
            except Exception:  # noqa: BLE001
                self.log.exception("Sandbox boot failed; chat continues without it")
                self.sandbox = None

        # v1.2 continuity state loads from DB (with TTL decay) before chatting.
        if self.continuity is not None:
            try:
                await self.continuity.start()
                story.boot_step("角色延续状态已就绪", detail="continuity loaded")
            except Exception:  # noqa: BLE001
                self.log.exception("Continuity failed to start (chat continues without it)")
                self.continuity = None

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
        shared_loop = self.behavior.enabled or self.sandbox is not None
        if self.consolidation_scheduler is not None and self.consolidation_scheduler.enabled:
            if shared_loop:
                # Rides the shared scheduler instead of owning a second loop (v0.8 §34).
                self.consolidation_scheduler.external_driver = True
                self.scheduler.register_job(
                    ScheduledJob(
                        name="memory_consolidation",
                        handler=self.consolidation_scheduler.tick,
                        interval_seconds=float(self.consolidation_scheduler.interval_seconds),
                        run_immediately=False,
                        misfire_policy="skip",
                    )
                )
            else:
                await self.consolidation_scheduler.start()

        # Task 14: replay writes the database refused (queued in the outbox).
        # Rides the shared scheduler when it runs, otherwise gets one attempt
        # right after startup — a leftover queue means a past outage.
        if self.memory is not None and self.memory.outbox is not None:
            if shared_loop:
                self.scheduler.register_job(
                    ScheduledJob(
                        name="memory_outbox",
                        handler=self.memory.replay_outbox,
                        interval_seconds=120.0,
                        run_immediately=True,
                        misfire_policy="skip",
                    )
                )
            else:
                asyncio.create_task(self.memory.replay_outbox())

        # Task 20: settle reply outcomes once their observation window passed.
        if shared_loop and self.reply_feedback is not None:
            social_engine = self.social

            async def _apply_outcome(group_key: str, score: float) -> None:
                """Fold a settled turn into the soft state (never the hard gate)."""
                if social_engine is None:
                    return
                group_id = group_key.split(":", 1)[-1]
                social_engine.attention.note_reply_outcome(group_id, score)
                social_engine.engagement.note(group_id, score)

            settler = ReplyFeedbackSettler(
                self.reply_feedback,
                getattr(self.social, "monitor", None),
                metrics=self.metrics,
                on_settled=_apply_outcome,
            )

            async def _settle_and_persist() -> None:
                await settler.settle()
                if social_engine is not None:
                    await self.reply_feedback.save_engagement(social_engine.engagement.snapshot())

            if social_engine is not None:
                snapshot = await self.reply_feedback.load_engagement()
                if snapshot is not None:
                    social_engine.engagement.load(snapshot)

            self.scheduler.register_job(
                ScheduledJob(
                    name="reply_feedback",
                    handler=_settle_and_persist,
                    interval_seconds=30.0,
                    run_immediately=True,
                    misfire_policy="skip",
                )
            )
            self.scheduler.register_job(
                ScheduledJob(
                    name="reply_feedback_prune",
                    handler=lambda: self.reply_feedback.prune(
                        self.config.social.feedback_retention_days
                    ),
                    interval_seconds=86400.0,
                    run_immediately=False,
                    misfire_policy="skip",
                )
            )

        # Task 15: model usage rows are pruned daily on the same loop.
        if self.ai_usage is not None and shared_loop:
            retention_days = self.config.ai.usage.retention_days
            self.scheduler.register_job(
                ScheduledJob(
                    name="ai_usage_prune",
                    handler=lambda: self.ai_usage.prune(retention_days),
                    interval_seconds=86400.0,
                    run_immediately=False,
                    misfire_policy="skip",
                )
            )

        if self.config.tools.enabled:
            await self.tools.start()
            self.log.info(
                "[Tool] runtime ready: %d tool(s) enabled (%s), decision=%s, budget=%d/turn",
                sum(1 for t in self.tools.registry.all() if self.tools.is_enabled(t.metadata.name)),
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

        await self._start_sandbox_jobs()

        # v1.1: background sticker indexer (never blocks QQ from coming up, v1.1 §16).
        if self.media.enabled and self.config.media.indexer_enabled:
            asyncio.create_task(self._run_sticker_indexer())

        # Task 12: derived memory indices (FTS keyword tokens + vector blobs)
        # backfill rows written before them — in the background, so the first
        # message never pays for it.
        if self.memory is not None:
            asyncio.create_task(self.memory.warm_indices())

        # One scheduler for the whole process: behaviour, world, memory upkeep.
        if self.behavior.enabled or self.sandbox is not None:
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
        if self.watchdog is not None:
            self.watchdog.start()
            story.boot_step(
                "事件循环看门狗已启动",
                detail=f"超过 {self.config.logging.watchdog_threshold_ms} ms 的卡顿会告警",
            )
        if self.config_watcher is not None:
            self.config_watcher.start()
            story.boot_step(
                "配置监听已启动",
                detail="手改 config.yaml 后自动热重载（语法错误会提示并保留旧配置）",
            )
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
                "  ·  " + ", ".join(state.spec.name for state in self.ai.router.states.values())
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
        if self.sandbox is not None and self.sandbox.enabled:
            context = self.sandbox.context()
            lines.append(
                f"沙盒       {context.get('location', '-')}"
                f"  ·  {context.get('action') or '闲着'}"
                f"  ·  {'+'.join(context.get('modes', [])) or '-'}"
            )
        jobs = ", ".join(job.name for job in self.scheduler.jobs())
        lines.append(f"后台任务   {jobs or '无'}")
        story.blank()
        story.panel("CatooBot 已就绪 · developer Rinsora", lines, accent="bright_green")
        story.blank()
        story.say(
            "world",
            console.paint("她已经开始过自己的日子了", "bright_cyan"),
            detail="QQ 里直接说话即可，管理看 WebUI /sandbox",
        )

    async def _run_sticker_indexer(self) -> None:
        """Background scan of the sticker import directory (v1.1 §15-§17)."""
        try:
            stats = await self.media.indexer.scan()
            self.log.info("[Media.Indexer] scan done: %s", stats)
        except Exception:  # noqa: BLE001 - indexing must never kill the bot
            self.log.exception("[Media.Indexer] startup scan failed")

    async def _start_sandbox_jobs(self) -> None:
        """Sandbox tick on the shared scheduler (v2.0 §67) — no second loop."""
        sandbox = self.sandbox
        if sandbox is None or not sandbox.enabled:
            return
        self.scheduler.register_job(
            ScheduledJob(
                name="sandbox_tick",
                handler=sandbox.tick,
                interval_seconds=float(self.config.sandbox.tick_seconds),
                run_immediately=False,  # start() already settled the gap
                misfire_policy="skip",
            )
        )
        self.log.info(
            "[Sandbox] active (tick=%ss, phase=%s, modes=%s, location=%s)",
            self.config.sandbox.tick_seconds,
            sandbox.phase.value,
            "+".join(sandbox.modes.ids()) or "-",
            sandbox.character.location,
        )

    async def shutdown(self) -> None:
        """Graceful stop: schedulers, plugins, web, adapter, database."""
        if self.watchdog is not None:
            await self.watchdog.stop()
        if self.config_watcher is not None:
            await self.config_watcher.stop()
        if self.consolidation_scheduler is not None:
            await self.consolidation_scheduler.stop()
        await self.scheduler.stop()
        if self.sandbox is not None:
            try:
                await self.sandbox.shutdown()
            except Exception:  # noqa: BLE001
                self.log.exception("[Sandbox] shutdown persist failed (ignored)")
        await self.conversation.shutdown()
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
        if self.sandbox is not None and self.sandbox.enabled:
            try:
                farewell = f"下线前她正在{self.sandbox.status_line()}"
            except Exception:  # noqa: BLE001 - cosmetic only
                farewell = ""
        narrate().quiet("世界已存档，CatooBot 退出了", detail=farewell)
        self.log.info("CatooBot stopped.")
