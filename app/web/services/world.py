"""World admin service: dashboard, timeline, routine, goals, events, previews.

Everything here is WebUI-only — the QQ surface stays natural-language (§5).
Settings are stored as a JSON override in the ``settings`` table and applied to
the live runtime, exactly like the v0.4 behaviour overrides.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from app.config.settings import WorldConfig
from app.world.clock import PERIOD_BOUNDS

if TYPE_CHECKING:
    from app.core.bot import Bot

SETTINGS_KEY = "world_overrides"


class WorldAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.World")

    @property
    def world(self):
        return getattr(self.bot, "world", None)

    @property
    def available(self) -> bool:
        world = self.world
        return world is not None and bool(getattr(world, "enabled", False))

    # ------------------------------------------------------------ dashboard

    async def dashboard(self) -> dict[str, Any]:
        world = self.world
        if not self.available:
            return {"enabled": False}
        view = await world.view()
        view["enabled"] = True
        view["scheduler"] = await self.bot.scheduler.snapshot()
        view["jobs"] = [job.as_dict() for job in self.bot.scheduler.jobs()]
        view["pending"] = (
            await world.messaging_queue.view() if world.messaging_queue else {}
        )
        view["snapshots"] = await self.snapshot_count()
        return view

    async def health(self) -> dict[str, Any]:
        world = self.world
        if not self.available:
            return {"enabled": False}
        health = await world.health()
        health["jobs"] = [job.as_dict() for job in self.bot.scheduler.jobs()]
        return health

    async def summary(self) -> str:
        """One-paragraph snapshot of what the character is doing *right now*.

        Rule-driven (no LLM): always contains 时间 / 地点 / 人物 / 事件, plus the
        surrounding context — what she is working on, who she is with, and what
        is happening around her.
        """
        world = self.world
        if not self.available:
            return ""
        moment = world.clock.snapshot()
        state = world.state.state
        period_text = {
            "late_night": "深夜", "early_morning": "清晨", "morning": "上午",
            "noon": "中午", "afternoon": "下午", "evening": "晚上", "night": "夜里",
        }.get(moment.period, moment.period)
        character = self.bot.character.personas.persona.identity.name or "她"
        where = state.location or "自己的小房间"
        doing = state.activity or "发呆"

        sentences = [
            f"现在是{moment.weekday}{moment.time_text}（{period_text}），"
            f"{character}正在{where}{doing}。"
        ]

        schedule_note = {
            "sleeping": "她已经睡着了。",
            "resting": "她正躺着休息。",
            "busy": "她正忙着，不怎么想被打扰。",
        }.get(state.schedule_state, "")
        if schedule_note:
            sentences.append(schedule_note)

        goal = await world.goals.current_goal()
        if goal is not None:
            upcoming = goal.next_open_milestone()
            sentences.append(
                f"她手头有一件事：{goal.name}（进度 {goal.progress_percent}%）"
                + (f"，下一步是{upcoming.name}。" if upcoming is not None else "。")
            )

        social = {
            "chatting": "她正在跟人聊天。",
            "with_friends": "她正和朋友们待在一起。",
            "quiet": "她一个人安安静静地待着。",
        }.get(state.social_state, "")
        if social:
            sentences.append(social)

        topics: list[str] = []
        try:
            manager = getattr(self.bot.behavior, "topics", None)
            if manager is not None:
                for topic in await manager.all_topics(limit=5):
                    if getattr(topic, "status", "active") == "active":
                        topics.append(topic.title)
        except Exception:  # noqa: BLE001 - topics are optional context
            topics = []
        if topics:
            sentences.append(f"最近在聊的话题：{'、'.join(topics[:3])}。")

        return "".join(sentences)

    async def timeline(self, days: int = 7) -> dict[str, list[dict[str, Any]]]:
        if not self.available:
            return {}
        return await self.world.timeline.range(days=days)

    async def events(
        self, *, limit: int = 60, event_type: str = ""
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        rows = await self.world.events.recent(
            limit=limit, types=[event_type] if event_type else None
        )
        return [self._event_row(event) for event in rows]

    def _event_row(self, event) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "type": event.type,
            "summary": event.summary,
            "importance": round(event.importance, 2),
            "source": event.source,
            "goal": event.related_goal,
            "session_id": event.session_id,
            "created_text": event.created_text,
            "created_at": event.created_at,
            "surfaced_at": event.surfaced_at,
            "detail": event.detail,
        }

    async def snapshot_count(self) -> int:
        if not self.available or self.bot.database is None:
            return 0
        row = await self.bot.database.fetchone("SELECT COUNT(*) AS n FROM world_snapshots")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------ activities

    def current_episode(self) -> dict[str, Any] | None:
        if not self.available:
            return None
        runtime = getattr(self.world, "activity", None)
        if runtime is None:
            return None
        return runtime.episode_dict()

    def activity_context(self) -> dict[str, Any]:
        if not self.available:
            return {}
        runtime = getattr(self.world, "activity", None)
        return runtime.world_context() if runtime is not None else {}

    async def episodes(self, *, days: int = 1, limit: int = 200) -> list[dict[str, Any]]:
        if not self.available or self.bot.database is None:
            return []
        since = self.world.clock.now().timestamp() - max(1, days) * 86400
        rows = await self.bot.database.fetchall(
            """SELECT * FROM activity_episodes WHERE started_at >= ?
               ORDER BY started_at ASC LIMIT ?""",
            (int(since), int(limit)),
        )
        import json

        episodes = []
        for row in rows:
            tags = []
            try:
                tags = json.loads(row.get("tags") or "[]")
            except (TypeError, ValueError):
                tags = []
            episodes.append(
                {
                    "id": row["id"],
                    "activity_key": row["activity_key"],
                    "activity_label": row["activity_label"],
                    "location": row.get("location") or "",
                    "started_at": row["started_at"],
                    "planned_end_at": row["planned_end_at"],
                    "ended_at": row.get("ended_at"),
                    "status": row.get("status") or "active",
                    "source": row.get("source") or "routine",
                    "transition_reason": row.get("transition_reason") or "",
                    "extension_count": int(row.get("extension_count") or 0),
                    "ambient_count": int(row.get("ambient_count") or 0),
                    "tags": tags,
                }
            )
        return episodes

    # --------------------------------------------------------------- replay

    async def replay(self, days: float = 1.0) -> list[dict[str, Any]]:
        if not self.available:
            return []
        return await self.world.timeline.replay(days=days)

    async def simulate(
        self, *, days: float = 1.0, step_minutes: int = 30, seed: int | None = None
    ) -> dict[str, Any]:
        if not self.available:
            return {"error": "world disabled"}
        return await self.world.simulate(days=days, step_minutes=step_minutes, seed=seed)

    # -------------------------------------------------------------- routine

    def routine_map(self) -> dict[str, list[str]]:
        if not self.available:
            return {}
        return self.world.routine.periods()

    def period_names(self) -> tuple[str, ...]:
        return tuple(name for name, _start, _end in PERIOD_BOUNDS)

    async def catalogue(self) -> list[str]:
        if not self.available:
            return []
        return list(self.world.routine.as_dict()["catalogue"])

    # ------------------------------------------------------------- settings

    async def load_overrides(self) -> dict[str, Any]:
        data = (
            await self.bot.database.get_setting_json(SETTINGS_KEY)
            if self.bot.database
            else None
        )
        return data if isinstance(data, dict) else {}

    async def apply_overrides(self, overrides: dict[str, Any]) -> WorldConfig:
        merged = self.bot.config.world.model_dump()
        for section, values in overrides.items():
            if isinstance(values, dict) and isinstance(merged.get(section), dict):
                merged[section].update(values)
            else:
                merged[section] = values
        config = WorldConfig.model_validate(merged)
        self._apply(config)
        return config

    def _apply(self, config: WorldConfig) -> None:
        """Hot-apply world settings to the live runtime."""
        self.bot.config.world = config
        world = self.world
        if world is None:
            return
        world.config = config
        world.routine.reconfigure(config.routine)
        world.ambient._config = config.ambient          # noqa: SLF001 - hot swap
        world.ambient._world = config                   # noqa: SLF001
        world.events._config = config.events            # noqa: SLF001
        world.goals._config = config.goals              # noqa: SLF001
        if getattr(world, "activity", None) is not None:
            world.activity._transition_window = config.activity.transition_window_minutes * 60  # noqa: SLF001
        if world.messaging_queue is not None:
            world.messaging_queue._config = config.messaging  # noqa: SLF001
        self._log.info("[World] settings applied (hot reload)")

    async def restore_overrides(self) -> None:
        overrides = await self.load_overrides()
        if overrides:
            try:
                await self.apply_overrides(overrides)
            except Exception:  # noqa: BLE001 - bad stored values must not block boot
                self._log.exception("[World] Stored overrides are invalid; ignoring")

    async def save_settings(
        self, form: dict[str, Any], *, periods: dict[str, list[str]]
    ) -> dict[str, Any]:
        """Persist + apply the routine/settings form from the WebUI."""

        def as_bool(value: Any, default: bool = False) -> bool:
            return str(value) == "1" if value is not None else default

        def as_int(name: str, default: int) -> int:
            try:
                return int(float(form.get(name, default)))
            except (TypeError, ValueError):
                return default

        def as_float(name: str, default: float) -> float:
            try:
                return float(form.get(name, default))
            except (TypeError, ValueError):
                return default

        overrides: dict[str, Any] = {
            "routine": {
                "enabled": as_bool(form.get("routine_enabled"), True),
                "transition_minutes": as_int("transition_minutes", 20),
                "periods": periods,
            },
            "activity": {
                "transition_window_minutes": as_int("transition_window_minutes", 5),
                "planning_horizon_minutes": as_int("planning_horizon_minutes", 240),
                "max_transitions_per_hour": as_int("max_transitions_per_hour", 6),
            },
            "goals": {
                "enabled": as_bool(form.get("goals_enabled"), True),
                "auto_advance": as_bool(form.get("auto_advance"), False),
                "max_active": as_int("max_active", 5),
                "max_progress_events_per_day": as_int("max_progress_per_day", 3),
                "advance_interval_hours": as_float("advance_interval_hours", 12.0),
            },
            "ambient": {
                "enabled": as_bool(form.get("ambient_enabled"), True),
                "min_interval_minutes": as_int("ambient_min_interval", 60),
                "max_per_day": as_int("ambient_max_per_day", 4),
            },
            "events": {
                "max_per_hour": as_int("events_max_per_hour", 10),
                "max_per_day": as_int("events_max_per_day", 40),
            },
            "messaging": {
                "enabled": as_bool(form.get("messaging_enabled"), True),
                "max_background_messages_per_day": as_int("background_messages_per_day", 3),
                "pending_ttl_minutes": as_int("pending_ttl_minutes", 120),
            },
            "tick_seconds": as_int("tick_seconds", 60),
            "missed_event_policy": str(form.get("missed_event_policy", "skip")),
        }
        if form.get("rest_mode") is not None and self.available:
            self.world.set_rest_mode(as_bool(form.get("rest_mode")))
        await self.bot.database.set_setting_json(SETTINGS_KEY, overrides, int(time.time()))
        await self.apply_overrides(overrides)
        return overrides

    # -------------------------------------------------------------- control

    async def control(self, action: str, form: dict[str, Any] | None = None) -> str:
        world = self.world
        if not self.available:
            return "world disabled"
        form = form or {}
        if action == "pause":
            world.pause("webui")
            return "world paused"
        if action == "resume":
            world.resume()
            return "world resumed"
        if action == "rest_on":
            world.set_rest_mode(True)
            return "rest mode on"
        if action == "rest_off":
            world.set_rest_mode(False)
            return "rest mode off"
        if action == "tick":
            await world.tick()
            return "one world tick executed"
        if action == "snapshot":
            await world.save_snapshot()
            return "snapshot saved"
        if action == "recover":
            report = await world.restore()
            return f"recovered: {report}"
        if action == "clear_pending":
            queue = world.messaging_queue
            if queue is None:
                return "no pending queue"
            removed = await queue.clear()
            return f"cleared {removed} pending initiative(s)"
        return f"unknown action: {action}"

    # ---------------------------------------------------------------- goals

    async def goals(self) -> dict[str, Any]:
        if not self.available:
            return {"enabled": False, "goals": [], "projects": []}
        return await self.world.goals.view()

    async def goal_action(self, action: str, form: dict[str, Any]) -> str:
        if not self.available:
            return "world disabled"
        goals = self.world.goals
        try:
            if action == "create":
                milestones = [
                    item.strip()
                    for item in str(form.get("milestones", "")).replace(",", "\n").split("\n")
                    if item.strip()
                ]
                goal = await goals.create(
                    str(form.get("name", "")).strip(),
                    description=str(form.get("description", "")).strip(),
                    milestones=milestones,
                    next_action=str(form.get("next_action", "")).strip(),
                    priority=int(float(form.get("priority", 3) or 3)),
                    source="manual",
                )
                return f"created: {goal.name}"
            if action == "advance":
                goal = await goals.advance(
                    str(form.get("goal_id", "")),
                    amount=float(form.get("amount", 0.08) or 0.08),
                    note=str(form.get("note", "")),
                    reason="manual",
                )
                return f"advanced: {goal.name} -> {goal.progress_percent}%"
            if action == "status":
                goal = await goals.set_status(
                    str(form.get("goal_id", "")), str(form.get("status", "paused"))
                )
                return f"{goal.name} -> {goal.status}"
            if action == "current":
                await goals.set_current(str(form.get("goal_id", "")))
                return "current goal updated"
            if action == "milestone":
                goal = await goals.add_milestone(
                    str(form.get("goal_id", "")), str(form.get("name", ""))
                )
                return f"milestone added to {goal.name}"
            if action == "project":
                project = await goals.create_project(
                    str(form.get("name", "")).strip(),
                    description=str(form.get("description", "")).strip(),
                )
                return f"project created: {project.name}"
        except Exception as exc:  # noqa: BLE001 - surfaced in the WebUI banner
            return f"error: {exc}"
        return f"unknown action: {action}"
