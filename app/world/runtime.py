"""WorldRuntime: the persistent world's single heartbeat (spec §10-§16).

One object owns the world's tick: it settles the schedule, moves the character
between activities smoothly, lets small ambient things happen, advances goals a
little, and snapshots itself now and then. Everything it writes is bounded and
idempotent, and every state change carries a reason.

Crash isolation (spec §57/§70): a world failure is caught here and logged — QQ
chat must keep working even if the world is broken.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timedelta
from typing import Any

from app.config.settings import WorldConfig
from app.utils.narrator import narrate
from app.world.ambient import AmbientLife
from app.world.clock import WorldClock
from app.world.events import LifeEventService
from app.world.goals import PersistentGoalService
from app.world.messaging import PendingInitiativeQueue
from app.world.models import WorldSnapshot
from app.world.routine import REST_PERIODS, RoutineService
from app.world.state import WorldStateService
from app.world.timeline import WorldTimeline

HEARTBEAT_KEY = "world.last_seen"
DAY_KEY = "world.day_key"
DOWNTIME_ALERT_SECONDS = 900      # 15+ minutes down is worth reporting
MAX_SIMULATION_STEPS = 4000


class WorldRuntime:
    def __init__(
        self,
        *,
        config: WorldConfig,
        clock: WorldClock,
        state: WorldStateService,
        routine: RoutineService,
        events: LifeEventService,
        timeline: WorldTimeline,
        goals: PersistentGoalService,
        ambient: AmbientLife,
        database: Any = None,
        presence: Any = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self.config = config
        self.clock = clock
        self.state = state
        self.routine = routine
        self.events = events
        self.timeline = timeline
        self.goals = goals
        self.ambient = ambient
        self._db = database
        self._presence = presence
        self._log = logger or logging.getLogger("CatooBot.World")
        self._rng = rng or random.Random()

        # v1.0: activity is an Episode lifecycle, not a per-tick roulette.
        from app.world.activity_runtime import ActivityRuntime
        from app.world.planner import ActivityPlanner

        self.planner = ActivityPlanner(
            routine=routine,
            profiles_overrides=config.activity.profiles,
            logger=self._log,
        )
        self.activity = ActivityRuntime(
            planner=self.planner,
            state=state,
            events=events,
            database=database,
            transition_window_minutes=config.activity.transition_window_minutes,
            logger=self._log,
            now=self.clock.now_timestamp,
        )
        self.activity_runtime = self.activity  # alias for clarity

        self.ticks = 0
        self.errors = 0
        self._paused = False
        self._pause_reason = ""
        self._last_tick = 0.0
        self._last_persist = 0.0
        self._last_snapshot = 0.0
        self._day_key = ""
        self._last_period = ""
        self._started_at = 0.0
        self._last_recovery: dict[str, Any] = {}
        self.messaging_queue: PendingInitiativeQueue | None = None
        # Set from logging.narrate_world_ticks: print a status line every tick.
        self.narrate_ticks = False
        # v1.2 §100: optional ContinuityManager — world micro events feed her
        # recent events. Assigned by the bot when continuity is enabled.
        self.continuity: Any = None

    # -------------------------------------------------------------- toggles

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    @property
    def paused(self) -> bool:
        return self._paused

    @property
    def rest_mode(self) -> bool:
        return bool(self.config.rest_mode)

    def pause(self, reason: str = "manual") -> None:
        self._paused = True
        self._pause_reason = reason
        self._log.info("[World] Paused (%s)", reason)

    def resume(self) -> None:
        if self._paused:
            self._log.info("[World] Resumed (was paused: %s)", self._pause_reason)
        self._paused = False
        self._pause_reason = ""

    def set_rest_mode(self, enabled: bool) -> None:
        self.config.rest_mode = bool(enabled)
        self._log.info("[World] Rest mode %s", "on" if enabled else "off")

    # ---------------------------------------------------------------- tick

    async def tick(self) -> None:
        """One world pass. Never raises (spec §70)."""
        if not self.enabled:
            return
        if self._paused:
            return
        self.ticks += 1
        now = self.clock.now()
        moment = self.clock.snapshot(now)
        try:
            await self.state.load()
        except Exception:  # noqa: BLE001
            self.errors += 1
            self._log.exception("[World] Failed to load world state")

        for name, step in (
            ("day_change", self._day_pass(moment)),
            ("schedule", self._schedule_pass(moment)),
            ("activity", self._activity_pass(moment)),
            ("ambient", self._ambient_pass(moment)),
            ("goals", self._goal_pass()),
            ("snapshot", self._snapshot_pass(now)),
        ):
            try:
                await step
            except Exception:  # noqa: BLE001 - one broken step must not stop the world
                self.errors += 1
                self._log.exception("[World] %s step failed", name)
        self._last_tick = now.timestamp()
        if self.narrate_ticks:
            narrate().world(
                "世界心跳",
                detail=(
                    f"{moment.time_text} {moment.period} · "
                    f"{self.state.state.activity or '发呆'} · 心情 {self.state.state.mood}"
                ),
            )

    # ------------------------------------------------------------- day pass

    async def _day_pass(self, moment: Any) -> None:
        if not self._day_key:
            self._day_key = moment.day_key
            return
        if moment.day_key == self._day_key:
            return
        previous = self._day_key
        self._day_key = moment.day_key
        self._log.info("[World] New day: %s -> %s", previous, moment.day_key)
        narrate().world(
            "新的一天开始了",
            detail=f"{previous} → {moment.day_key} · {moment.weekday}",
        )
        await self.events.emit(
            type="routine",
            summary="新的一天开始了",
            key="day_start",
            bucket=moment.day_key,
            importance=0.3,
            source="scheduled",
        )

    # -------------------------------------------------------- schedule pass

    def is_sleeping(self, moment: datetime | None = None) -> bool:
        if self._presence is not None:
            try:
                return bool(self._presence.is_sleeping(moment))
            except Exception:  # noqa: BLE001 - presence is advisory
                self._log.debug("[World] presence.is_sleeping failed", exc_info=True)
        return self.clock.snapshot(moment).period in REST_PERIODS

    def _schedule_state_for(self, period: str, sleeping: bool) -> str:
        if sleeping:
            return "sleeping"
        if period in REST_PERIODS:
            return "resting"
        return "awake"

    async def _schedule_pass(self, moment: Any) -> None:
        sleeping = self.is_sleeping(moment.moment)
        target = self._schedule_state_for(moment.period, sleeping)
        current = self.state.state
        if current.schedule_state != target:
            summary = {
                "sleeping": "去睡了",
                "resting": "躺着休息",
                "awake": "醒了",
                "busy": "开始忙自己的事",
            }.get(target, "状态变化")
            await self.state.change(
                "time_passage",
                schedule_state=target,
                emit=True,
                summary=summary,
                importance=0.3,
                event_type="routine",
            )
            narrate().world(
                {"sleeping": "去睡了", "resting": "躺下休息", "awake": "醒了", "busy": "忙起来了"}
                .get(target, f"状态 → {target}"),
                detail=f"{moment.time_text} {moment.period}",
            )

    # -------------------------------------------------------- activity pass

    async def _activity_pass(self, moment: Any) -> None:
        """Advance the Activity Episode lifecycle (v1.0 §15/§17).

        This is the *only* place activity changes happen, and only when the
        current episode reaches its planned end — the tick never re-rolls a
        fresh activity mid-episode, and there is no random keep (§21).
        """
        self._last_period = moment.period
        state = self.state.state
        sleeping = self.is_sleeping(moment.moment)
        goal = await self.goals.current_goal()
        await self.activity.tick(
            period=moment.period,
            sleeping=sleeping,
            energy=state.energy,
            focus=state.current_focus,
            current_goal=goal.name if goal is not None else "",
        )

    async def _ambient_pass(self, moment: Any) -> None:
        """Ambient events are bound to the current episode (§54-§58): at most
        ``ambient_max_per_episode`` per activity, never a steady drip while a
        stuck activity lingers."""
        episode = self.activity.episode
        profile = self.activity.current_profile()
        sleeping = self.is_sleeping(moment.moment) or self.activity_is_sleep(
            self.state.state.activity
        )
        if episode is None or profile is None or not profile.ambient_eligible:
            return
        if episode.ambient_count >= profile.ambient_max_per_episode:
            return
        outcome = await self.ambient.maybe_emit(
            activity=episode.activity_label,
            period=moment.period,
            sleeping=sleeping,
        )
        if outcome and not outcome.startswith("skip:"):
            episode.ambient_count += 1
            await self.activity._persist(episode)  # noqa: SLF001 - episode owner updates its counter
            self._log.debug("[World] ambient (episode=%s): %s", episode.id, outcome)
            narrate().world(outcome, detail="（她生活里的小事，不一定要说出口）")
            # v1.2 §98-§100: ambient events feed character continuity (recent
            # events) — the hook is optional and never blocks the world tick.
            continuity = getattr(self, "continuity", None)
            if continuity is not None:
                try:
                    await continuity.note_world_micro_event(
                        outcome, activity=episode.activity_key
                    )
                except Exception:  # noqa: BLE001 - cosmetic only
                    self._log.debug("[World] continuity micro event failed", exc_info=True)

    def activity_is_sleep(self, activity: str) -> bool:
        if not activity:
            return False
        from app.world.routine import activity_for_label

        return "sleep" in activity_for_label(activity).tags

    async def _goal_pass(self) -> None:
        if not self.goals.enabled:
            return
        goal = await self.goals.auto_advance_due()
        if goal is not None:
            self._log.info("[World] Auto-advanced goal %s", goal.name)
            narrate().world(
                f"顺手推进了「{goal.name}」",
                detail=f"进度 {goal.progress_percent}%"
                + (f" · 下一步 {goal.next_action}" if goal.next_action else ""),
            )

    # -------------------------------------------------------- snapshot pass

    async def _snapshot_pass(self, now: datetime) -> None:
        stamp = now.timestamp()
        if self._db is None:
            return
        interval = max(1, self.config.snapshot.interval_minutes) * 60
        if self._last_snapshot and (stamp - self._last_snapshot) < interval:
            return
        await self.save_snapshot()
        if (
            not self._last_persist
            or (stamp - self._last_persist) >= self.config.state_persist_seconds
        ):
            self._last_persist = stamp
            await self._persist_heartbeat(stamp)

    # ------------------------------------------------------------ snapshots

    async def save_snapshot(self) -> None:
        if self._db is None:
            return
        now = self.clock.now()
        active_goals = await self.goals.all(status="active", limit=20)
        recent = await self.events.recent(limit=15)
        projects = await self.goals.projects()
        snapshot = WorldSnapshot(
            snapshot_at=int(now.timestamp()),
            world_time=now.strftime("%Y-%m-%d %H:%M"),
            state=self.state.snapshot(),
            goals=[goal.transient_snapshot() for goal in active_goals],
            projects=[project.model_dump() for project in projects],
            recent_events=[event.model_dump() for event in recent],
            counters={"ticks": self.ticks, "errors": self.errors, "day": self._day_key},
        )
        try:
            await self._db.execute(
                "INSERT INTO world_snapshots (snapshot_at, data) VALUES (?, ?)",
                (snapshot.snapshot_at, snapshot.to_json()),
            )
            keep = max(1, self.config.snapshot.keep)
            await self._db.execute(
                """DELETE FROM world_snapshots WHERE id NOT IN
                   (SELECT id FROM world_snapshots ORDER BY snapshot_at DESC LIMIT ?)""",
                (keep,),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[World] Snapshot failed")
            return
        self._last_snapshot = now.timestamp()
        self._log.debug("[World] Snapshot saved (goals=%s)", len(active_goals))

    async def restore(self) -> dict[str, Any]:
        """Recover world state after a restart (spec §31-§34).

        Latest snapshot first, then *settle forward* — missed events are never
        replayed one by one; the world simply continues from now.
        """
        report: dict[str, Any] = {"restored": False, "downtime_seconds": 0.0, "policy": ""}
        if self._db is None or not self.enabled:
            return report
        last_seen = 0.0
        try:
            row = await self._db.fetchone(
                "SELECT data FROM world_snapshots ORDER BY snapshot_at DESC LIMIT 1"
            )
            if row is not None:
                snapshot = WorldSnapshot.from_json(row["data"])
                await self.state.restore(snapshot.state)
                self._last_snapshot = float(snapshot.snapshot_at)
                self._day_key = str(snapshot.counters.get("day") or "")
                report["restored"] = True
                report["snapshot_at"] = snapshot.snapshot_at
            heartbeat = await self._db.get_state(HEARTBEAT_KEY)
            last_seen = float(heartbeat) if heartbeat else 0.0
        except Exception:  # noqa: BLE001 - recovery must never block startup
            self._log.exception("[World] Recovery failed, continuing with current state")
            return report

        now = self.clock.now().timestamp()
        if not last_seen and not self._last_snapshot:
            # Nothing to recover from: this is a first boot, not downtime.
            await self.activity.recover()
            self._last_recovery = report
            await self._persist_heartbeat(now)
            return report
        downtime = max(0.0, now - max(last_seen, self._last_snapshot))
        report["downtime_seconds"] = downtime
        report["policy"] = self.config.missed_event_policy
        # v1.0 §68/§69: reconcile the activity episode — continue or expire it,
        # never fabricate a chain of missed activities.
        await self.activity.recover()
        if downtime >= DOWNTIME_ALERT_SECONDS:
            self._log.info(
                "[World] Recovered after %.0f minutes of downtime (policy=%s)",
                downtime / 60.0,
                self.config.missed_event_policy,
            )
            narrate().world(
                f"她睡了 {downtime / 3600:.1f} 小时后又接上了",
                detail="错过的世界事件："
                + ("跳过" if self.config.missed_event_policy == "skip" else "合并成一条"),
            )
            if self.config.missed_event_policy == "catch_up":
                await self._catch_up(downtime)
            else:
                self._log.info("[World] Missed events skipped by policy")
        self._last_recovery = report
        await self._persist_heartbeat(now)
        return report

    async def _catch_up(self, downtime: float) -> None:
        """One consolidated event — never a replay of everything missed."""
        hours = downtime / 3600.0
        if hours < 1:
            text = "中间走开了一小会儿"
        elif hours < 24:
            text = f"中间有几个小时没在（大约 {hours:.0f} 小时）"
        else:
            text = f"中间隔了大约 {hours / 24:.0f} 天没怎么活动"
        await self.events.emit(
            type="routine",
            summary=text,
            key="catch_up",
            bucket=self.clock.today_key(),
            importance=0.25,
            source="system",
            allow_duplicate=True,
        )

    async def _persist_heartbeat(self, stamp: float) -> None:
        if self._db is None:
            return
        try:
            await self._db.set_state(HEARTBEAT_KEY, str(int(stamp)), int(stamp))
            await self._db.set_state(
                DAY_KEY, self._day_key or self.clock.today_key(), int(stamp)
            )
        except Exception:  # noqa: BLE001
            self._log.debug("[World] Heartbeat write failed", exc_info=True)

    # ------------------------------------------------------ user / agent in

    async def note_user_interaction(self, *, user_id: str = "", session_id: str = "") -> None:
        """Someone talked to her (v1.0 §43-§45): chat is an *overlay*, never a
        new primary activity — she is still "看剧" while chatting."""
        if not self.enabled or self._paused:
            return
        try:
            await self.activity.set_overlay("chatting")
            await self.events.emit(
                type="relationship",
                summary="有人来找我聊天",
                key=f"chat:{session_id or user_id}",
                bucket=self.clock.hour_bucket(),
                importance=0.2,
                source="user",
                session_id=session_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001 - chat must not break because of the world
            self._log.exception("[World] Failed to note user interaction")

    async def note_agent_result(
        self, *, task_type: str, status: str, summary: str, session_id: str = "", user_id: str = ""
    ) -> None:
        """An agent task finished — the world *reacts*, the agent never writes state."""
        if not self.enabled or self._paused:
            return
        success = status in ("completed", "success", "done", "partial")
        try:
            await self.state.change(
                "task_completion" if success else "task_failure",
                emit=False,
            )
            await self.state.nudge_mood(1 if success else -1, source=f"agent:{task_type}")
            narrate().mind(
                mood=self.state.state.mood,
                energy=self.state.state.energy,
                detail=f"因为{'帮人办成了事' if success else '有件事没办成'}",
            )
            await self.events.emit(
                type="goal" if success else "ambient",
                summary=summary[:120] or ("帮你把事情办完了" if success else "有件事没办成"),
                key=f"agent:{task_type}:{session_id}",
                bucket=self.clock.hour_bucket(),
                importance=0.5 if success else 0.35,
                source="agent",
                session_id=session_id,
                user_id=user_id,
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[World] Failed to note agent result")

    # ------------------------------------------------------------ messaging

    async def background_messages_today(self) -> int:
        if self._db is None:
            return 0
        row = await self._db.fetchone(
            """SELECT COUNT(*) AS n FROM behavior_events
               WHERE type = 'initiative_sent' AND created_at >= ?""",
            (int(self.clock.start_of_day()),),
        )
        return int(row["n"]) if row else 0

    async def background_budget_left(self) -> int:
        cap = max(0, int(self.config.messaging.max_background_messages_per_day))
        if not self.config.messaging.enabled:
            return 0
        return max(0, cap - await self.background_messages_today())

    # ----------------------------------------------------------- simulation

    async def simulate(
        self,
        *,
        days: float = 1.0,
        step_minutes: int = 30,
        seed: int | None = None,
    ) -> dict[str, Any]:
        """Dry run: what *would* the world do? Writes nothing, sends nothing.

        v1.0: simulation follows the Episode lifecycle — activities change only
        at planned_end, so advancing 1h yields 0~1 transitions, not 60 events
        (spec §70/§71).
        """
        rng = random.Random(seed if seed is not None else 20260929)
        step = max(5, int(step_minutes)) * 60
        start = self.clock.now()
        end = start + timedelta(days=max(0.01, days))
        cursor = start
        lines: list[dict[str, Any]] = []
        counts: dict[str, int] = {}
        last_ambient: datetime | None = None
        last_goal = start
        steps = 0
        truncated = False
        goal = await self.goals.current_goal()

        episode_key = ""
        episode_label = ""
        episode_planned_end: datetime | None = None
        energy = self.state.state.energy

        while cursor < end:
            steps += 1
            if steps > MAX_SIMULATION_STEPS:
                truncated = True
                break
            moment = self.clock.snapshot(cursor)
            sleeping = self.is_sleeping(cursor)

            if episode_planned_end is None or cursor >= episode_planned_end:
                activity = self.planner.plan_next(
                    period=moment.period,
                    sleeping=sleeping,
                    energy=energy,
                    current_key=episode_key,
                    current_goal=goal.name if goal is not None else "",
                )
                profile = self.planner.profile(activity.key)
                episode_key = activity.key
                episode_label = activity.label
                episode_planned_end = cursor + timedelta(minutes=profile.typical_minutes)
                lines.append(
                    {
                        "at": cursor.strftime("%Y-%m-%d %H:%M"),
                        "kind": "activity",
                        "text": episode_label,
                        "period": moment.period,
                    }
                )
                counts["activity"] = counts.get("activity", 0) + 1
                # bounded energy drift from the profile
                energy = max(0.05, min(1.0, energy + profile.energy_delta_per_hour * step / 3600.0))

            if (
                self.ambient.enabled
                and not sleeping
                and not self.activity_is_sleep(episode_label)
                and moment.period != "late_night"
                and (last_ambient is None or (cursor - last_ambient) >= timedelta(
                    minutes=self.config.ambient.min_interval_minutes * 3
                ))
                and rng.random() < 0.4
                and counts.get("ambient", 0)
                < self.config.ambient.max_per_day * max(1, int(days + 0.99))
            ):
                from app.world.ambient import ambient_templates_for

                last_ambient = cursor
                lines.append(
                    {
                        "at": cursor.strftime("%Y-%m-%d %H:%M"),
                        "kind": "ambient",
                        "text": rng.choice(ambient_templates_for(episode_label)),
                        "period": moment.period,
                    }
                )
                counts["ambient"] = counts.get("ambient", 0) + 1

            if (
                goal is not None
                and self.config.goals.enabled
                and not sleeping
                and (cursor - last_goal)
                >= timedelta(hours=max(1.0, self.config.goals.advance_interval_hours))
                and counts.get("goal", 0)
                < self.config.goals.max_progress_events_per_day * max(1, int(days + 0.99))
            ):
                last_goal = cursor
                lines.append(
                    {
                        "at": cursor.strftime("%Y-%m-%d %H:%M"),
                        "kind": "goal",
                        "text": f"推进「{goal.name}」",
                        "period": moment.period,
                    }
                )
                counts["goal"] = counts.get("goal", 0) + 1

            cursor += timedelta(seconds=step)

        return {
            "days": days,
            "step_minutes": step_minutes,
            "steps": steps,
            "truncated": truncated,
            "counts": counts,
            "events": lines[:500],
            "goal": goal.name if goal is not None else "",
            "notes": [
                "这是预演：不会写入事件、不会改变状态、不会发送任何消息。",
                "现实边界：预演内容全部发生在角色的虚拟生活里。",
            ],
        }

    # --------------------------------------------------------------- status

    def status_line(self) -> str:
        """Short phrase for the console: ``睡觉（卧室）· 心情 neutral``."""
        state = self.state.state
        doing = state.activity or "发呆"
        where = f"（{state.location}）" if state.location else ""
        return f"{doing}{where} · 心情 {state.mood}"

    async def health(self) -> dict[str, Any]:
        stats = await self.events.stats() if self.enabled else {}
        budget = await self.background_budget_left() if self.enabled else 0
        return {
            "enabled": self.enabled,
            "paused": self._paused,
            "pause_reason": self._pause_reason,
            "rest_mode": self.rest_mode,
            "ticks": self.ticks,
            "errors": self.errors,
            "last_tick": self._last_tick or None,
            "last_tick_ago": round(self.clock.elapsed_since(self._last_tick), 1)
            if self._last_tick
            else None,
            "day": self._day_key,
            "timezone": self.clock.timezone_name,
            "event_stats": stats,
            "background_messages_left": budget,
            "recovery": self._last_recovery,
        }

    async def view(self) -> dict[str, Any]:
        moment = self.clock.snapshot()
        return {
            "health": await self.health(),
            "time": {
                "now": moment.moment.strftime("%Y-%m-%d %H:%M"),
                "period": moment.period,
                "weekday": moment.weekday,
                "is_weekend": moment.is_weekend,
                "describe": moment.describe(),
                "sleeping": self.is_sleeping(moment.moment),
            },
            "state": self.state.view(),
            "episode": self.activity.episode_dict(),
            "activity_context": self.activity.world_context(),
            "routine": self.routine.as_dict(),
            "ambient": self.ambient.view(),
            "goals": await self.goals.view(),
            "recent_events": await self.timeline.recent(limit=12),
            "mind": await self.mind(),
        }

    # ------------------------------------------------------------ mind view

    async def mind(self) -> dict[str, Any]:
        """What the character currently believes the world news is (spec §59)."""
        lines = await self.timeline.prompt_lines(limit=3, hours=48)
        goal_line = await self.goals.prompt_line()
        context = self.activity.world_context()
        return {
            "activity": context.get("activity_label") or self.state.state.activity,
            "activity_detail": context.get("activity_detail", ""),
            "elapsed_minutes": context.get("elapsed_minutes", 0),
            "remaining_minutes": context.get("planned_remaining_minutes", 0),
            "location": self.state.state.location,
            "schedule_state": self.state.state.schedule_state,
            "interaction_overlay": context.get("interaction_overlay") or "",
            "goal": goal_line,
            "events": lines,
        }

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "paused": self._paused,
            "rest_mode": self.rest_mode,
            "timezone": self.clock.timezone_name,
            "tick_seconds": self.config.tick_seconds,
        }



def en_label(label: str) -> str:
    """Show the previous activity the way a person would say it."""
    return label or "发呆"


def build_world(
    config: WorldConfig,
    *,
    database: Any,
    state_manager: Any,
    timezone: str = "Asia/Singapore",
    presence: Any = None,
    logger: logging.Logger | None = None,
    clock: WorldClock | None = None,
    rng: random.Random | None = None,
) -> WorldRuntime:
    """Wire a complete world from configuration (used by main and tests)."""
    log = logger or logging.getLogger("CatooBot.World")
    world_clock = clock or WorldClock(timezone=config.timezone or timezone, logger=log)
    routine = RoutineService(routine=config.routine, clock=world_clock, logger=log, rng=rng)
    world_state = WorldStateService(
        state_manager=state_manager, clock=world_clock, routine=routine,
        config=config, logger=log,
    )
    events = LifeEventService(
        database=database, clock=world_clock, config=config.events, logger=log
    )
    timeline = WorldTimeline(events=events, clock=world_clock, logger=log)
    goals = PersistentGoalService(
        database=database, clock=world_clock, config=config.goals,
        state=world_state, events=events, logger=log,
    )
    ambient = AmbientLife(
        events=events, clock=world_clock, config=config.ambient,
        world_config=config, logger=log, rng=rng,
    )
    runtime = WorldRuntime(
        config=config,
        clock=world_clock,
        state=world_state,
        routine=routine,
        events=events,
        timeline=timeline,
        goals=goals,
        ambient=ambient,
        database=database,
        presence=presence,
        logger=log,
        rng=rng,
    )
    runtime.messaging_queue = PendingInitiativeQueue(
        database=database, clock=world_clock, config=config.messaging, logger=log
    )

    async def _sink(payload: dict[str, Any]) -> None:
        await events.emit(
            type=payload.get("type", "activity"),
            summary=payload.get("summary", ""),
            key=f"{payload.get('type', 'activity')}:{payload.get('summary', '')[:24]}",
            detail=payload.get("detail") or {},
            importance=payload.get("importance"),
            source=payload.get("source", "scheduled"),
            related_goal=payload.get("related_goal", ""),
        )

    world_state.set_event_sink(_sink)
    return runtime
