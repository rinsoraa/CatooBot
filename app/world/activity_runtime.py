"""Activity Runtime (v1.0): the lifecycle owner of the current Activity Episode.

This is the piece that turns "the world re-picks an activity every tick" into
"the world keeps an episode going until it genuinely ends". The WorldRuntime
tick only advances time; *this* object decides continue/extend/transition — and
only when the current episode reaches its planned end (spec §15/§17-§20).

``CharacterState.activity`` is kept as a derived snapshot of the episode, so
there is exactly one source of truth (spec §8). A chat with a user becomes an
``interaction_overlay``, never a new primary episode (§43-§45).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.world.episode import (
    ActivityEpisode,
    ActivityProfile,
    new_episode_id,
)
from app.world.planner import ActivityContinuationEvaluator, ActivityPlanner


class ActivityRuntime:
    def __init__(
        self,
        *,
        planner: ActivityPlanner,
        state: Any,
        events: Any,
        database: Any = None,
        transition_window_minutes: int = 5,
        logger: logging.Logger | None = None,
        now: Any = None,
    ) -> None:
        self._planner = planner
        self._state = state
        self._events = events
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.World")
        self._clock = now or time.time
        self._transition_window = max(0, transition_window_minutes) * 60
        self._evaluator = ActivityContinuationEvaluator()

        self.episode: ActivityEpisode | None = None
        self.overlay: str = ""
        self._last_energy_at: float | None = None
        self._last_finished_key: str = ""

    # ------------------------------------------------------------------ tick

    async def tick(
        self,
        *,
        period: str,
        sleeping: bool,
        energy: float,
        focus: str,
        current_goal: str,
    ) -> None:
        """Advance the episode lifecycle. Never re-picks a fresh activity
        mid-episode (spec §15)."""
        now = self._now()
        await self._apply_energy(energy, now)

        if self.episode is None or not self.episode.is_active(now):
            await self._start(period, sleeping, energy, current_goal)
            return

        # Hard interrupt: she fell asleep mid-episode.
        if sleeping and "sleep" not in self.episode.tags:
            await self._finish(reason="sleep_transition")
            await self._start(
                period, sleeping, energy, current_goal,
                avoid_key=self.episode.activity_key,
            )
            return

        # Still before the planned end: keep going, decide nothing.
        if now < self.episode.planned_end_at - self._transition_window:
            return

        # Transition window reached: one structured decision.
        profile = self._planner.profile(self.episode.activity_key)
        decision = self._evaluator.decide(
            episode=self.episode,
            profile=profile,
            energy=energy,
            focus=focus,
            sleeping=sleeping,
            now=now,
        )
        if decision["decision"] == "extend":
            self.episode.planned_end_at = now + decision["extension_minutes"] * 60
            self.episode.status = "extended"
            self.episode.extension_count += 1
            self.episode.updated_at = now
            await self._persist(self.episode)
            await self._sync_state()
            await self._emit_lifecycle("extended", f"延长了 {self.episode.activity_label}")
        else:
            await self._finish(reason=decision["reason_code"])
            await self._start(
                period, sleeping, energy, current_goal,
                avoid_key=self._last_finished_key,
            )

    # ------------------------------------------------------------ lifecycle

    async def _start(
        self,
        period: str,
        sleeping: bool,
        energy: float,
        current_goal: str,
        *,
        quiet: bool = False,
        avoid_key: str = "",
    ) -> None:
        current_key = avoid_key or (self.episode.activity_key if self.episode is not None else "")
        activity = self._planner.plan_next(
            period=period,
            sleeping=sleeping,
            energy=energy,
            current_key=current_key,
            current_goal=current_goal,
        )
        profile = self._planner.profile(activity.key)
        now = self._now()
        episode = ActivityEpisode(
            id=new_episode_id(),
            activity_key=activity.key,
            activity_label=activity.label,
            activity_detail=activity.label,
            location=activity.location,
            social_state=activity.social,
            tags=list(activity.tags),
            started_at=now,
            planned_end_at=now + profile.typical_minutes * 60,
            status="active",
            source="routine",
            created_at=now,
            updated_at=now,
        )
        self.episode = episode
        await self._persist(episode)
        await self._sync_state()
        if not quiet:
            await self._emit_lifecycle(
                "started",
                f"{'在' + activity.location if activity.location else ''}{activity.label}",
            )
        self._log.info(
            "[World.Activity] episode=%s started=%s planned_end=%s",
            episode.id,
            activity.key,
            self._fmt(episode.planned_end_at),
        )

    async def _finish(self, *, reason: str) -> None:
        episode = self.episode
        if episode is None:
            return
        now = self._now()
        episode.status = "completed" if reason == "natural_completion" else "interrupted"
        episode.ended_at = now
        episode.transition_reason = reason
        episode.updated_at = now
        await self._persist(episode)
        await self._emit_lifecycle(
            "completed" if reason == "natural_completion" else "interrupted",
            f"结束 {episode.activity_label}（{reason}）",
            reason=reason,
        )
        self._log.info(
            "[World.Activity] episode=%s %s reason=%s elapsed=%sm",
            episode.id,
            episode.status,
            reason,
            episode.elapsed_minutes(now),
        )
        self._last_finished_key = episode.activity_key
        self.episode = None

    # ------------------------------------------------------------ overlay

    async def set_overlay(self, overlay: str) -> None:
        self.overlay = overlay
        await self._state.change(
            "user_interaction", interaction_overlay=overlay, emit=False
        )

    async def clear_overlay(self) -> None:
        if not self.overlay:
            return
        self.overlay = ""
        await self._state.change(
            "user_interaction", interaction_overlay="", emit=False
        )

    # ------------------------------------------------------------ recovery

    async def recover(self) -> None:
        """Reconcile the world after a restart — never fabricates history.

        Priority (spec §104/§105): DB active episode > restored CharacterState
        > a fresh start. Recovery never emits timeline noise for the resume.
        """
        episode = await self._load_active()
        if episode is not None:
            now = self._now()
            if now >= episode.planned_end_at:
                # It ended during downtime: expire it and move on once.
                episode.ended_at = episode.planned_end_at
                episode.status = "expired"
                episode.transition_reason = "recovery"
                await self._persist(episode)
                self.episode = None
                self._log.info("[World.Recovery] expired episode %s reconciled", episode.id)
            else:
                self.episode = episode
                await self._sync_state()
                self._log.info("[World.Recovery] episode %s resumed", episode.id)
            return

        # No DB episode: rebuild from the restored state so the snapshot's
        # activity survives (never re-picked from the routine).
        state = self._state.state
        if state.activity:
            from app.world.routine import activity_for_label

            activity = activity_for_label(state.activity)
            profile = self._planner.profile(activity.key)
            now = self._now()
            started = float(state.activity_started_at or state.activity_since or now)
            planned = float(
                state.activity_planned_end_at or (started + profile.typical_minutes * 60)
            )
            self.episode = ActivityEpisode(
                id=state.current_activity_episode_id or new_episode_id(),
                activity_key=activity.key,
                activity_label=activity.label,
                activity_detail=activity.label,
                location=state.location or activity.location,
                social_state=state.social_state or activity.social,
                tags=list(activity.tags),
                started_at=started,
                planned_end_at=planned,
                status="active",
                source="recovery",
                created_at=now,
                updated_at=now,
            )
            await self._sync_state()
            self._log.info("[World.Recovery] episode rebuilt from snapshot (%s)", activity.key)
            return

        # Truly nothing: start one episode, silently (first boot).
        await self._start("afternoon", False, 0.8, "", quiet=True)

    # ------------------------------------------------------------ internals

    async def _apply_energy(self, energy: float, now: float) -> None:
        if self.episode is None:
            self._last_energy_at = now
            return
        profile = self._planner.profile(self.episode.activity_key)
        if self._last_energy_at is None:
            self._last_energy_at = now
            return
        hours = min(1.0, (now - self._last_energy_at) / 3600.0)
        self._last_energy_at = now
        delta = profile.energy_delta_per_hour * hours
        if abs(delta) < 0.005:
            return
        await self._state.change(
            "time_passage", energy=max(0.05, min(1.0, energy + delta)), emit=False
        )

    async def _sync_state(self) -> None:
        episode = self.episode
        if episode is None:
            return
        await self._state.change(
            "activity",
            activity=episode.activity_label,
            location=episode.location,
            social_state=episode.social_state,
            force=True,
            emit=False,
            current_activity_episode_id=episode.id,
            activity_started_at=int(episode.started_at),
            activity_planned_end_at=int(episode.planned_end_at),
            activity_status=episode.status,
            interaction_overlay=self.overlay,
        )

    async def _emit_lifecycle(self, lifecycle: str, summary: str, *, reason: str = "") -> None:
        if self._events is None or self.episode is None:
            return
        try:
            await self._events.emit(
                type="activity",
                summary=summary,
                key=f"episode:{self.episode.id}:{lifecycle}",
                detail={"lifecycle": lifecycle, "episode_id": self.episode.id, "reason": reason},
                importance=0.3,
                source="scheduled",
            )
        except Exception:  # noqa: BLE001 - timeline is best-effort
            self._log.exception("[World] failed to emit activity lifecycle event")

    async def _persist(self, episode: ActivityEpisode) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO activity_episodes
                   (id, activity_key, activity_label, activity_detail, location,
                    social_state, tags, started_at, planned_end_at, ended_at, status,
                    source, transition_reason, parent_episode_id, extension_count,
                    ambient_count, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status=excluded.status, planned_end_at=excluded.planned_end_at,
                       ended_at=excluded.ended_at, transition_reason=excluded.transition_reason,
                       extension_count=excluded.extension_count,
                       ambient_count=excluded.ambient_count,
                       updated_at=excluded.updated_at""",
                (
                    episode.id,
                    episode.activity_key,
                    episode.activity_label,
                    episode.activity_detail,
                    episode.location,
                    episode.social_state,
                    json.dumps(episode.tags, ensure_ascii=False),
                    episode.started_at,
                    episode.planned_end_at,
                    episode.ended_at,
                    episode.status,
                    episode.source,
                    episode.transition_reason,
                    episode.parent_episode_id,
                    episode.extension_count,
                    episode.ambient_count,
                    episode.created_at,
                    episode.updated_at,
                ),
            )
        except Exception:  # noqa: BLE001 - persistence must not break the world
            self._log.exception("[World] failed to persist activity episode")

    async def _load_active(self) -> ActivityEpisode | None:
        if self._db is None:
            return None
        try:
            row = await self._db.fetchone(
                """SELECT * FROM activity_episodes
                   WHERE status IN ('active', 'extended')
                   ORDER BY started_at DESC LIMIT 1"""
            )
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        tags = []
        try:
            tags = json.loads(row.get("tags") or "[]")
        except (TypeError, ValueError):
            tags = []
        return ActivityEpisode(
            id=row["id"],
            activity_key=row["activity_key"],
            activity_label=row["activity_label"],
            activity_detail=row.get("activity_detail") or "",
            location=row.get("location") or "",
            social_state=row.get("social_state") or "alone",
            tags=tags,
            started_at=float(row["started_at"] or 0.0),
            planned_end_at=float(row["planned_end_at"] or 0.0),
            ended_at=row.get("ended_at"),
            status=row.get("status") or "active",
            source=row.get("source") or "routine",
            transition_reason=row.get("transition_reason") or "",
            parent_episode_id=row.get("parent_episode_id") or "",
            extension_count=int(row.get("extension_count") or 0),
            ambient_count=int(row.get("ambient_count") or 0),
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
        )

    # ---------------------------------------------------------------- views

    def current_profile(self) -> ActivityProfile | None:
        if self.episode is None:
            return None
        return self._planner.profile(self.episode.activity_key)

    def world_context(self) -> dict[str, Any]:
        """The rich activity context v1.0 exposes to Social Cognition (§86)."""
        if self.episode is None:
            return {
                "primary_activity": None,
                "secondary_context": [],
                "interaction_overlay": self.overlay or None,
                "location": "",
                "focus": "",
                "energy": 0.0,
            }
        now = self._now()
        return {
            "primary_activity": self.episode.activity_key,
            "activity_detail": self.episode.activity_detail,
            "activity_label": self.episode.activity_label,
            "elapsed_minutes": self.episode.elapsed_minutes(now),
            "planned_remaining_minutes": self.episode.remaining_minutes(now),
            "location": self.episode.location,
            "social_state": self.episode.social_state,
            "tags": list(self.episode.tags),
            "interaction_overlay": self.overlay or None,
            "status": self.episode.status,
        }

    def episode_dict(self) -> dict[str, Any] | None:
        return self.episode.as_dict() if self.episode is not None else None

    def _now(self) -> float:
        return float(self._clock())

    @staticmethod
    def _fmt(stamp: float) -> str:
        return time.strftime("%H:%M", time.localtime(stamp))
