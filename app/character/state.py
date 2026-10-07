"""Character narrative state: mood / energy / activity / current_focus.

This is the character's *storytelling* state, not psychology. Since v0.4 the
mood follows a **ladder with inertia** (spec v0.3 §14): it moves at most one step
per change, changes need a source, and it drifts back toward neutral over
time — so "happy → sad" inside one message is impossible.

本文件同时引用 v0.3 / v0.8 / v1.0 §n（状态字段随版本扩展）。"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.database.database import Database

NEUTRAL_MOOD = "neutral"
MOOD_LEVELS: tuple[str, ...] = ("down", "quiet", "neutral", "happy", "cheerful")
DEFAULT_DECAY_SECONDS = 4 * 3600  # one ladder step back toward neutral
MOOD_CHANGE_COOLDOWN_SECONDS = 600  # mood cannot move more often than this


def mood_index(mood: str) -> int | None:
    try:
        return MOOD_LEVELS.index(mood)
    except ValueError:
        return None


def step_mood(mood: str, direction: int) -> str:
    """Move one ladder step (custom labels are preserved unchanged)."""
    index = mood_index(mood)
    if index is None:
        return mood
    target = max(0, min(len(MOOD_LEVELS) - 1, index + direction))
    return MOOD_LEVELS[target]


def step_toward_neutral(mood: str) -> str:
    index = mood_index(mood)
    neutral = MOOD_LEVELS.index(NEUTRAL_MOOD)
    if index is None or index == neutral:
        return mood
    return MOOD_LEVELS[index + (1 if index < neutral else -1)]


class CharacterState(BaseModel):
    mood: str = NEUTRAL_MOOD
    mood_updated_at: int = 0
    energy: float = 0.8
    activity: str = ""
    activity_since: int = 0
    current_focus: str = ""
    updated_at: int = 0
    # --- v0.8 world fields: EXTEND this one state, never a parallel copy (v0.8 §8/§9)
    location: str = ""  # fictional place: 房间 / 客厅 / 便利店
    social_state: str = "alone"  # alone / chatting / with_friends / quiet
    schedule_state: str = "awake"  # awake / resting / sleeping / busy
    current_goal: str = ""  # goal_id the character is pursuing
    current_project: str = ""  # project_id she is slowly building
    last_activity_change: int = 0
    last_change_reason: str = ""  # why the world moved last (spec v0.8 §20)
    # --- v1.0 episode fields: activity is a derived snapshot of the episode (v1.0 §7/§8)
    current_activity_episode_id: str = ""
    activity_started_at: int = 0
    activity_planned_end_at: int = 0
    activity_status: str = ""
    #: Phase 6A §十四：活动由什么机制创建（USER/TASK/ROUTINE/WORLD_EVENT/RECOVERY/SYSTEM）。
    #: 与上面几个字段一样，**只**由 ActivityEpisode 的投影写入；任何模块都不许绕过
    #: Episode 直接写 activity（否则就会出现"Episode 说 A、角色状态说 B"的分裂）。
    activity_source: str = ""
    interaction_overlay: str = ""  # chatting / assisting_user (never the primary activity)

    def decayed(self, now: int) -> CharacterState:
        """A copy with mood drifting one step toward neutral when stale."""
        if mood_index(self.mood) is None or self.mood == NEUTRAL_MOOD:
            return self.model_copy(update={"updated_at": now})
        if self.mood_updated_at == 0:
            return self.model_copy(update={"updated_at": now})
        if now - self.mood_updated_at < DEFAULT_DECAY_SECONDS:
            return self
        return self.model_copy(
            update={
                "mood": step_toward_neutral(self.mood),
                "mood_updated_at": now,
                "updated_at": now,
            }
        )

    def to_json(self) -> str:
        import json

        return json.dumps(self.model_dump(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> CharacterState:
        import json

        return cls.model_validate(json.loads(raw))


class StateManager:
    """Owns the single character state row; persists as JSON, decays lazily."""

    def __init__(
        self,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Character")
        self._clock = clock
        self._state = CharacterState()
        self._loaded = False

    @property
    def state(self) -> CharacterState:
        return self._state.decayed(int(self._clock()))

    async def load(self) -> CharacterState:
        if self._db is not None and not self._loaded:
            try:
                row = await self._db.fetchone("SELECT data FROM character_states WHERE id = 1")
                if row is not None:
                    self._state = CharacterState.from_json(row["data"])
            except Exception:  # noqa: BLE001
                self._log.exception("Failed to load character state, using defaults")
            self._loaded = True
        return self.state

    async def update(
        self,
        *,
        mood: str | None = None,
        energy: float | None = None,
        activity: str | None = None,
        current_focus: str | None = None,
        location: str | None = None,
        social_state: str | None = None,
        schedule_state: str | None = None,
        current_goal: str | None = None,
        current_project: str | None = None,
        # Phase 6A §十四：这几个是**当前 ActivityEpisode 的投影**，只由 ActivityProjection 写。
        # 它们在这里有名字，是为了让投影走同一个 update 通道（原子落盘），而不是为了让
        # 别的模块绕过 Episode 直接改 activity。
        current_activity_episode_id: str | None = None,
        activity_started_at: int | None = None,
        activity_planned_end_at: int | None = None,
        activity_status: str | None = None,
        activity_source: str | None = None,
        activity_since: int | None = None,
        reason: str = "",
        extra: dict[str, Any] | None = None,
    ) -> CharacterState:
        now = int(self._clock())
        changes: dict[str, Any] = {"updated_at": now}
        if reason:
            changes["last_change_reason"] = reason
        if mood is not None:
            changes["mood"] = mood
            changes["mood_updated_at"] = now
        if energy is not None:
            changes["energy"] = max(0.0, min(1.0, energy))
        if activity is not None and activity != self._state.activity:
            changes["activity"] = activity
            changes["activity_since"] = now
            changes["last_activity_change"] = now
            self._log.debug(
                "[Activity] character activity changed %s -> %s (reason=%s)",
                self._state.activity or "-",
                activity,
                reason or "unknown",
            )
        elif activity is not None:
            changes["activity"] = activity
        if current_focus is not None:
            changes["current_focus"] = current_focus
        if location is not None:
            changes["location"] = location
        if social_state is not None:
            changes["social_state"] = social_state
        if schedule_state is not None:
            changes["schedule_state"] = schedule_state
        if current_goal is not None:
            changes["current_goal"] = current_goal
        if current_project is not None:
            changes["current_project"] = current_project
        if current_activity_episode_id is not None:
            changes["current_activity_episode_id"] = current_activity_episode_id
        if activity_started_at is not None:
            changes["activity_started_at"] = int(activity_started_at)
        if activity_planned_end_at is not None:
            changes["activity_planned_end_at"] = int(activity_planned_end_at)
        if activity_status is not None:
            changes["activity_status"] = activity_status
        if activity_source is not None:
            changes["activity_source"] = activity_source
        if activity_since is not None:
            changes["activity_since"] = int(activity_since)
        if extra:
            changes.update(extra)
        self._state = self._state.model_copy(update=changes)
        await self._persist()
        return self.state

    async def nudge_mood(self, direction: int, *, source: str = "") -> CharacterState:
        """Move one mood step, respecting the change cooldown (inertia)."""
        now = int(self._clock())
        if (
            self._state.mood_updated_at
            and now - self._state.mood_updated_at < MOOD_CHANGE_COOLDOWN_SECONDS
        ):
            return self.state
        current = self._state.decayed(now)
        target = step_mood(current.mood, direction)
        if target == current.mood:
            return current
        self._log.info(
            "[Behavior] mood %s -> %s (source=%s)", current.mood, target, source or "unknown"
        )
        return await self.update(mood=target)

    async def set_activity(self, activity: str) -> CharacterState:
        return await self.update(activity=activity)

    async def reset(self) -> CharacterState:
        self._state = CharacterState()
        await self._persist()
        return self.state

    async def _persist(self) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO character_states (id, data, updated_at) VALUES (1, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       data=excluded.data, updated_at=excluded.updated_at""",
                (self._state.to_json(), int(self._clock())),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to persist character state")
