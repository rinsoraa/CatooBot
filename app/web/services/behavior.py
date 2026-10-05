"""Behaviour admin service: dashboard data, live settings, tests, preview.

Settings are stored as a JSON override in the ``settings`` table and applied
to the running engine (hot reload, spec v0.8 §53/§90). Manual triggers and the
simulator only ever run here in the WebUI — never exposed to QQ (spec v0.8 §54/§55).
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

from app.behavior.presence import PresenceResolver
from app.character.relationship import Relationship
from app.character.turn import TurnOrigin
from app.config.settings import BehaviorConfig

if TYPE_CHECKING:
    from app.core.bot import Bot

SETTINGS_KEY = "behavior_overrides"


class BehaviorService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Behavior")

    @property
    def behavior(self):
        return self.bot.behavior

    # ------------------------------------------------------------ dashboard

    async def dashboard(self) -> dict[str, Any]:
        snapshot = await self.behavior.snapshot()
        scheduler = await self.bot.scheduler.snapshot()
        recent = await self.behavior.initiative.recent_events(limit=15)
        topics = await self.behavior.topics.all_topics(limit=15)
        snapshot["scheduler"] = scheduler
        snapshot["recent_events"] = recent
        snapshot["topics"] = [t.model_dump() for t in topics]
        return snapshot

    async def recent_replies(self, limit: int = 10) -> list[dict[str, Any]]:
        return await self.behavior.initiative.recent_events(limit=limit, event_type="reply_sent")

    # ------------------------------------------------------------- settings

    async def load_overrides(self) -> dict[str, Any]:
        data = await self.bot.database.get_setting_json(SETTINGS_KEY) if self.bot.database else None
        return data if isinstance(data, dict) else {}

    async def apply_overrides(self, overrides: dict[str, Any]) -> BehaviorConfig:
        """Merge overrides onto the configured behaviour and apply in place."""
        merged = self.bot.config.behavior.model_dump()
        for section, values in overrides.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section].update(values)
            else:
                merged[section] = values
        new_config = BehaviorConfig.model_validate(merged)
        self._apply(new_config)
        return new_config

    def _apply(self, config: BehaviorConfig) -> None:
        """Hot-apply a behaviour config to the live engine components."""
        self.bot.config.behavior = config
        engine = self.bot.behavior
        engine.config = config
        engine.initiative.config = config.initiative
        engine.initiative.presence = engine.presence
        # The sleep/DND/night windows live in PresenceResolver, which captures
        # the schedule at construction — without this push, a WebUI change to
        # 作息 only takes effect after a restart (the gate keeps the old window).
        self.bot.presence.apply_schedule(config.schedule)
        self.bot.reply_timing._config = config.reply  # noqa: SLF001
        self.bot.response_planner._chunk_cfg = config.chunking  # noqa: SLF001
        self.bot.response_planner._chunker._config = config.chunking  # noqa: SLF001
        self._log.info("[Behavior] settings applied (hot reload)")

    async def save_settings(self, form: dict[str, Any]) -> dict[str, Any]:
        """Persist + apply a settings form from the WebUI.

        The override map is keyed by BehaviorConfig *section* (reply, chunking,
        ...) so :meth:`apply_overrides` merges it onto the live config.
        """
        overrides = await self.load_overrides()

        def as_bool(value: Any, default: bool = False) -> bool:
            return str(value) == "1" if value is not None else default

        def as_float(name: str, default: float) -> float:
            try:
                return float(form.get(name, default))
            except (TypeError, ValueError):
                return default

        def as_int(name: str, default: int) -> int:
            try:
                return int(float(form.get(name, default)))
            except (TypeError, ValueError):
                return default

        payload: dict[str, Any] = {
            "reply": {
                "enabled": as_bool(form.get("reply_enabled")),
                "min_delay": as_float("min_delay", 0.8),
                "max_delay": as_float("max_delay", 8.0),
            },
            "chunking": {
                "enabled": as_bool(form.get("chunking_enabled")),
                "chunk_probability": as_float("chunk_probability", 0.35),
                "max_chunks": as_int("max_chunks", 3),
            },
            "schedule": {
                "sleep_enabled": as_bool(form.get("sleep_enabled")),
                "sleep_start": str(form.get("sleep_start", "00:30")),
                "sleep_end": str(form.get("sleep_end", "08:00")),
                "dnd_enabled": as_bool(form.get("dnd_enabled")),
                "dnd_start": str(form.get("dnd_start", "23:00")),
                "dnd_end": str(form.get("dnd_end", "08:00")),
                "dnd_blocks_replies": as_bool(form.get("dnd_blocks_replies")),
            },
            "group": {
                "participation_enabled": as_bool(form.get("participation_enabled")),
                "participation_probability": as_float("participation_probability", 0.04),
                "min_message_length": as_int("min_message_length", 3),
                "cooldown_seconds": as_int("group_cooldown", 300),
            },
            "initiative": {
                "enabled": as_bool(form.get("initiative_enabled")),
                "min_interval_minutes": as_int("min_interval_minutes", 120),
                "daily_limit": as_int("daily_limit", 3),
                "hourly_limit": as_int("hourly_limit", 1),
                "idle_hours": as_float("idle_hours", 6.0),
                "min_relationship_stage": str(form.get("min_relationship_stage", "familiar")),
                "max_unanswered": as_int("max_unanswered", 1),
            },
        }
        # 核心好友块：表单**没带的字段保持不动**（v0.8 表单总是带上它自己的字段，
        # 而 v1 设置页/手工编辑只写它实际涉及的那些键）
        core: dict[str, Any] = {}
        if "core_initiative_enabled" in form:
            core["enabled"] = as_bool(form.get("core_initiative_enabled"))
        for int_key, int_default in (
            ("min_interval_minutes", 60),
            ("daily_limit", 6),
            ("hourly_limit", 2),
            ("max_unanswered", 2),
        ):
            if f"core_{int_key}" in form:
                core[int_key] = as_int(f"core_{int_key}", int_default)
        for float_key, float_default in (("idle_hours", 3.0), ("base_probability", 0.5)):
            if f"core_{float_key}" in form:
                core[float_key] = as_float(f"core_{float_key}", float_default)
        if core:
            payload["initiative"]["core_friend"] = core
        for section, values in payload.items():
            if isinstance(values, dict) and isinstance(overrides.get(section), dict):
                # Merge, never replace: a form that does not expose every key of a
                # section must not silently drop overrides written elsewhere (the
                # v1 settings page writes into this same override map).
                merged = dict(overrides[section])
                merged.update(values)
                overrides[section] = merged
            else:
                overrides[section] = values
        if self.bot.database:
            await self.bot.database.set_setting_json(SETTINGS_KEY, overrides, int(time.time()))
        await self.apply_overrides(overrides)
        # Presence windows are owned by the resolver instance; rebuild it.
        self.bot.presence = PresenceResolver(
            self.bot.config.character.timezone, self.bot.config.behavior.schedule
        )
        self.bot.behavior.presence = self.bot.presence
        self.bot.reply_timing._presence = self.bot.presence  # noqa: SLF001
        return payload

    async def restore_overrides(self) -> None:
        overrides = await self.load_overrides()
        if not overrides:
            return
        try:
            await self.apply_overrides(overrides)
            self._log.info("Restored behaviour overrides")
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to restore behaviour overrides")

    # ------------------------------------------------------- manual triggers

    async def test_response(self, text: str, *, user_id: str = "0") -> dict[str, Any]:
        """Dry run of the reply path — generates but never sends to QQ."""
        plan_info = await self._simulate(text, user_id=user_id)
        return plan_info

    async def _simulate(self, text: str, *, user_id: str) -> dict[str, Any]:

        behavior = self.behavior
        relationship = Relationship(user_id=str(user_id), stage="familiar", interaction_count=5)
        state = await behavior.current_state()
        time_ctx = behavior.time_context()

        try:
            # Phase 3E.1：这是**预览**（管理台代打一句话看她会怎么回）——
            # 按 BACKGROUND 处理：预览绝不改动游戏世界，LOW Minecraft 动作会被意图门拒绝。
            reply = await self.bot.character.respond(
                f"private:{user_id}",
                user_id,
                text,
                turn_origin=TurnOrigin.BACKGROUND,
                time_context=time_ctx,
                record_interaction=False,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced to the admin
            return {"ok": False, "error": str(exc)}

        plan = self.bot.response_planner.plan_reply(
            reply,
            state=state,
            relationship=relationship,
            time_context=time_ctx,
        )
        return {
            "ok": True,
            "reply": reply,
            "delay": round(plan.delay, 2),
            "chunks": plan.chunks,
            "state": state.model_dump(),
            "time": time_ctx.describe(),
        }

    async def preview(self, form: dict[str, Any]) -> dict[str, Any]:
        """Behaviour simulator (spec v0.8 §55/§86) — never sends a QQ message."""
        behavior = self.behavior
        rng = random.Random(7)

        # Temporary overrides so the admin can probe other states/times.
        presence = behavior.presence
        simulated_time = str(form.get("sim_time", "")).strip()
        if simulated_time:
            presence = self._presence_at(simulated_time)

        state = await behavior.current_state()
        mood = str(form.get("mood", "")).strip()
        activity = str(form.get("activity", "")).strip()
        if mood:
            state = state.model_copy(update={"mood": mood})
        if activity:
            state = state.model_copy(update={"activity": activity})

        stage = str(form.get("relationship", "familiar")).strip() or "familiar"
        relationship = Relationship(user_id="preview", stage=stage, interaction_count=10)
        topic = str(form.get("topic", "")).strip()

        time_ctx = presence.time_context()
        delay = self.bot.reply_timing.compute(
            reply_text=str(form.get("sample_reply", "好呀，等我看一下")),
            state=state,
            relationship=relationship,
            time_context=time_ctx,
        )
        chunks = self.bot.response_planner._chunker.plan(  # noqa: SLF001 - preview only
            str(form.get("sample_reply", "好呀。\n\n等我看一下。"))
        )

        block = presence.hard_block_reason(for_initiative=True)
        initiative_ok = bool(self.bot.config.behavior.initiative.enabled) and not block
        reason = topic or "long_absence"
        probability = self.bot.config.behavior.initiative.base_probability
        if topic:
            probability = min(1.0, probability + self.bot.config.behavior.initiative.topic_bonus)
        if stage in ("close", "very_close"):
            bonus = self.bot.config.behavior.initiative.relationship_bonus
            probability = min(1.0, probability + bonus)

        return {
            "ok": True,
            "time": time_ctx.describe(),
            "period": time_ctx.period,
            "sleeping": time_ctx.is_sleeping,
            "dnd": time_ctx.in_dnd,
            "state": state.model_dump(),
            "relationship": stage,
            "delay": round(delay, 2),
            "chunks": chunks,
            "initiative": {
                "would_consider": initiative_ok,
                "blocked_by": block or "",
                "reason": reason,
                "probability": round(probability, 2),
                "simulated_roll": round(rng.random(), 2),
            },
        }

    def _presence_at(self, hhmm: str) -> PresenceResolver:
        """A PresenceResolver whose 'now' is the simulated time of day."""
        resolver = PresenceResolver(
            self.bot.config.character.timezone, self.bot.config.behavior.schedule
        )
        from datetime import datetime

        try:
            hour, _, minute = hhmm.partition(":")
            simulated = resolver.now().replace(
                hour=int(hour), minute=int(minute), second=0, microsecond=0
            )
        except (TypeError, ValueError):
            return resolver

        class _Frozen(PresenceResolver):
            def now(self) -> datetime:
                return simulated

        frozen = _Frozen(self.bot.config.character.timezone, self.bot.config.behavior.schedule)
        return frozen

    # ------------------------------------------------------- state controls

    async def test_state_transition(self, direction: int) -> dict[str, Any]:
        state = await self.bot.character.states.nudge_mood(direction, source="webui_manual")
        return {"ok": True, "state": state.model_dump()}

    async def reset_state(self) -> dict[str, Any]:
        state = await self.bot.character.states.reset()
        return {"ok": True, "state": state.model_dump()}

    # --------------------------------------------------------------- topics

    async def list_topics(self, scope_key: str = "", status: str = "") -> list[dict[str, Any]]:
        topics = await self.behavior.topics.all_topics(scope_key=scope_key, status=status)
        return [t.model_dump() for t in topics]

    async def topic_action(self, action: str, topic_id: int) -> bool:
        manager = self.behavior.topics
        if action == "resolve":
            return await manager.resolve(topic_id)
        if action == "forget":
            return await manager.forget(topic_id)
        if action == "delete":
            return await manager.delete(topic_id)
        return False
