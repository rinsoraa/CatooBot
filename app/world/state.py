"""WorldStateService: the single writer of world state (spec §18/§19).

Rules enforced here, once, for everyone:

* every change names a **reason** (spec §20) — no anonymous mutation;
* activity changes are **smooth**: a minimum dwell time must pass before the
  character wanders off to something else (spec §22);
* state changes are *atomic*: one ``model_copy`` + one persist, so no reader
  ever observes a half-updated character;
* the Agent may **read** state, never write it — writes go through this service
  (spec §18), so agent completions become *events* the world then reacts to.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from app.character.state import CharacterState, StateManager
from app.world.clock import WorldClock
from app.world.models import STATE_CHANGE_REASONS, WorldActivity
from app.world.routine import RoutineService

EventSink = Callable[[dict[str, Any]], Awaitable[None]]

#: state-change reason -> how a person would phrase it
REASON_TEXT = {
    "time_passage": "时间过去了",
    "activity": "换了个事情做",
    "scheduled_event": "日程到点了",
    "user_interaction": "有人来找她说话",
    "task_completion": "帮人办成了一件事",
    "task_failure": "有件事没办成",
    "topic_progress": "话题有进展",
    "relationship_event": "关系上有变化",
    "ambient_event": "生活里的小事",
    "manual": "手动调整",
    "recovery": "重启后恢复",
    "mood_nudge": "心情被刚才的事影响到了",
}

#: How much energy a full hour of a normal activity costs/gains.
_ENERGY_PER_HOUR = 0.06


class WorldStateService:
    def __init__(
        self,
        *,
        state_manager: StateManager,
        clock: WorldClock,
        routine: RoutineService | None = None,
        config: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._states = state_manager
        self._clock = clock
        self._routine = routine
        self._config = config
        self._log = logger or logging.getLogger("CatooBot.World")
        self._sink: EventSink | None = None

    # ------------------------------------------------------------- plumbing

    def set_event_sink(self, sink: EventSink | None) -> None:
        """Late-bound life-event emitter (avoids a circular import)."""
        self._sink = sink

    @property
    def state(self) -> CharacterState:
        return self._states.state

    async def load(self) -> CharacterState:
        return await self._states.load()

    # ---------------------------------------------------------- transitions

    def can_change_activity(self, activity: str, *, force: bool = False) -> bool:
        """Smooth transitions: no hopping between activities (spec §22)."""
        if force or self._routine is None:
            return True
        current = self._states.state
        if not current.activity or current.activity == activity:
            return True
        dwell = max(0, int(self._routine.transition_minutes)) * 60
        if dwell <= 0:
            return True
        since = current.last_activity_change or current.activity_since
        if not since:
            return True
        return self._clock.elapsed_since(since) >= dwell

    async def change(
        self,
        reason: str,
        *,
        activity: str | None = None,
        location: str | None = None,
        social_state: str | None = None,
        schedule_state: str | None = None,
        mood: str | None = None,
        energy: float | None = None,
        current_focus: str | None = None,
        current_goal: str | None = None,
        current_project: str | None = None,
        force: bool = False,
        emit: bool = False,
        summary: str = "",
        importance: float | None = None,
        event_type: str = "activity",
        related_goal: str = "",
        extra: dict[str, Any] | None = None,
        current_activity_episode_id: str | None = None,
        activity_started_at: int | None = None,
        activity_planned_end_at: int | None = None,
        activity_status: str | None = None,
        interaction_overlay: str | None = None,
    ) -> CharacterState:
        """Apply one coherent state change; returns the new state.

        ``activity`` is skipped (not applied) when the minimum dwell time has
        not passed, unless ``force`` is set — callers keep their other fields.
        """
        if not reason:
            raise ValueError("world state changes require a reason")
        if reason not in STATE_CHANGE_REASONS:
            self._log.warning("[World] unusual state-change reason: %s", reason)

        episode_fields: dict[str, Any] = {}
        if current_activity_episode_id is not None:
            episode_fields["current_activity_episode_id"] = current_activity_episode_id
        if activity_started_at is not None:
            episode_fields["activity_started_at"] = activity_started_at
        if activity_planned_end_at is not None:
            episode_fields["activity_planned_end_at"] = activity_planned_end_at
        if activity_status is not None:
            episode_fields["activity_status"] = activity_status
        if interaction_overlay is not None:
            episode_fields["interaction_overlay"] = interaction_overlay
        merged_extra = {**(extra or {}), **episode_fields} if episode_fields else extra

        before = self._states.state
        applied = True
        if activity is not None and not self.can_change_activity(activity, force=force):
            applied = False
            activity = None

        state = await self._states.update(
            activity=activity,
            location=location,
            social_state=social_state,
            schedule_state=schedule_state,
            mood=mood,
            energy=energy,
            current_focus=current_focus,
            current_goal=current_goal,
            current_project=current_project,
            reason=reason,
            extra=merged_extra,
        )
        if not applied:
            self._log.debug("[World] activity change deferred (dwell not elapsed)")
        self._narrate_change(before, state, reason)
        if emit and self._sink is not None:
            text = summary or self._describe_change(
                state, activity=activity, schedule_state=schedule_state, reason=reason
            )
            await self._sink(
                {
                    "type": event_type,
                    "summary": text,
                    "importance": importance,
                    "related_goal": related_goal,
                    "source": "scheduled",
                    "detail": {"reason": reason, "activity": state.activity},
                }
            )
        return state

    async def set_activity(
        self,
        activity: WorldActivity,
        *,
        reason: str = "activity",
        force: bool = False,
        emit: bool = True,
        summary: str = "",
    ) -> CharacterState:
        """Move into an activity, carrying its location/social/energy hints."""
        energy = self._states.state.energy + activity.energy_delta
        text = summary or f"在{activity.label}"
        if activity.location:
            text = f"在{activity.location}{activity.label}"
        return await self.change(
            reason,
            activity=activity.label,
            location=activity.location or None,
            social_state=activity.social or None,
            energy=max(0.05, min(1.0, energy)),
            force=force,
            emit=emit,
            summary=text,
            importance=0.25,
            event_type="activity",
        )

    async def apply_time_passage(self, period: str, *, schedule_state: str | None = None) -> None:
        """Slow drift that happens simply because time moved (spec §20)."""
        state = self._states.state
        delta = 0.0
        if state.activity and "sleep" in self._activity_tags(state.activity):
            delta = _ENERGY_PER_HOUR * 1.5
        elif state.activity:
            delta = -_ENERGY_PER_HOUR
        if delta:
            energy = max(0.05, min(1.0, state.energy + delta))
        else:
            energy = state.energy
        changes: dict[str, Any] = {"reason": "time_passage"}
        if energy != state.energy:
            changes["energy"] = energy
        if schedule_state and schedule_state != state.schedule_state:
            changes["schedule_state"] = schedule_state
        if len(changes) > 1:
            await self._states.update(**changes)

    def _activity_tags(self, label: str) -> list[str]:
        if self._routine is None:
            return []
        from app.world.routine import activity_for_label

        return activity_for_label(label).tags

    async def nudge_mood(self, direction: int, *, source: str = "") -> CharacterState:
        """Move one mood-ladder step — ladder rules live in ``StateManager``."""
        before = self._states.state
        after = await self._states.nudge_mood(direction, source=source)
        if after.mood != before.mood:
            self._narrate_change(before, after, "mood_nudge")
            self._log.info(
                "[World] mood %s -> %s (source=%s)", before.mood, after.mood, source or "-"
            )
        return after

    def _narrate_change(self, before: CharacterState, after: CharacterState, reason: str) -> None:
        """心理历程: only meaningful shifts (mood / schedule / energy), never noise."""
        try:
            from app.utils.narrator import narrate

            mood_moved = before.mood != after.mood
            schedule_moved = before.schedule_state != after.schedule_state
            energy_moved = abs(before.energy - after.energy) >= 0.1
            if not (mood_moved or schedule_moved or energy_moved):
                return
            bits: list[str] = []
            if mood_moved:
                bits.append(f"心情 {before.mood} → {after.mood}")
            if schedule_moved:
                bits.append(f"状态 {before.schedule_state} → {after.schedule_state}")
            if energy_moved and not mood_moved:
                bits.append(f"精力 {after.energy:.0%}")
            narrate().say(
                "mind",
                " · ".join(bits) + f" · 正在 {after.activity or '发呆'}",
                detail=REASON_TEXT.get(reason, reason),
            )
        except Exception:  # noqa: BLE001 - narration is cosmetic
            return

    # ------------------------------------------------------------ read side


    def describe(self, *, state: CharacterState | None = None) -> str:
        """A short natural-language line for the prompt (spec §41)."""
        current = state or self._states.state
        bits: list[str] = []
        if current.schedule_state == "sleeping":
            bits.append("你正在睡觉/刚睡醒")
        if current.activity:
            place = f"在{current.location}" if current.location else ""
            bits.append(f"你{place}正在{current.activity}")
        if current.current_focus:
            bits.append(f"最近你在忙：{current.current_focus}")
        if current.energy <= 0.3:
            bits.append("你有点累")
        elif current.energy >= 0.85:
            bits.append("你精神不错")
        return "；".join(bits)

    def view(self) -> dict[str, Any]:
        state = self._states.state
        return {
            "mood": state.mood,
            "energy": round(state.energy, 3),
            "activity": state.activity,
            "activity_since": state.activity_since or None,
            "location": state.location,
            "social_state": state.social_state,
            "schedule_state": state.schedule_state,
            "current_focus": state.current_focus,
            "current_goal": state.current_goal,
            "current_project": state.current_project,
            "last_activity_change": state.last_activity_change or None,
            "last_change_reason": state.last_change_reason,
            "updated_at": state.updated_at or None,
        }

    def snapshot(self) -> dict[str, Any]:
        return self._states.state.model_dump()

    async def restore(self, data: dict[str, Any]) -> None:
        """Recover state from a snapshot (reason=recovery, spec §31)."""
        try:
            state = CharacterState.model_validate(data)
        except Exception:  # noqa: BLE001 - a bad snapshot must not kill boot
            self._log.exception("[World] Snapshot state unreadable, keeping current")
            return
        self._states._state = state  # noqa: SLF001 - snapshot restore is the one owner path
        await self._states._persist()  # noqa: SLF001
        self._log.info(
            "[World] State restored from snapshot (activity=%s)", state.activity or "-"
        )

    # --------------------------------------------------------------- helper

    def _describe_change(
        self,
        state: CharacterState,
        *,
        activity: str | None,
        schedule_state: str | None,
        reason: str,
    ) -> str:
        if activity:
            place = f"在{state.location}" if state.location else ""
            return f"{place}开始{activity}"
        if schedule_state:
            return {
                "sleeping": "去睡觉了",
                "resting": "躺下休息",
                "awake": "醒着",
                "busy": "开始忙自己的事",
            }.get(schedule_state, f"状态变成{schedule_state}")
        return f"状态变化（{reason}）"

    def now_stamp(self) -> int:
        return int(time.time())
