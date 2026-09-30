"""ActivityManager: the character's fictional 'what am I doing right now'.

Activities are chosen from the configured pool for the current time period
(never hardcoded, spec §17) and re-rolled on an interval, so the character
shows continuity ("在打游戏") instead of a state that flickers per message.
This is character-world fiction — it must never fabricate verifiable
real-world actions (spec §63).
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

from app.behavior.presence import PresenceResolver
from app.config.settings import BehaviorActivityConfig

if TYPE_CHECKING:
    from app.character.state import CharacterState, StateManager

# Generic fallback pool per period; overridable through config / persona.
DEFAULT_PERIOD_ACTIVITIES: dict[str, list[str]] = {
    "early_morning": ["idle", "resting"],
    "morning": ["idle", "reading", "studying", "working"],
    "noon": ["eating", "idle"],
    "afternoon": ["gaming", "reading", "studying", "working"],
    "evening": ["gaming", "reading", "chatting"],
    "night": ["gaming", "reading", "chatting"],
    "late_night": ["resting", "gaming"],
}


class ActivityManager:
    def __init__(
        self,
        config: BehaviorActivityConfig,
        presence: PresenceResolver,
        states: StateManager,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._presence = presence
        self._states = states
        self._log = logger or logging.getLogger("CatooBot.Activity")
        self._rng = rng or random.Random()
        self._clock = clock
        self._last_roll: float = 0.0

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ------------------------------------------------------------- selection

    def candidates_for(self, period: str) -> list[str]:
        """Activity pool for a period: character pool > period override > default."""
        if self._config.pool:
            base = list(self._config.pool)
            override = self._config.period_preferences.get(period)
            if override:
                # Period preference narrows the character pool, never widens it.
                narrowed = [a for a in override if a in base]
                base = narrowed or base
            return base
        override = self._config.period_preferences.get(period)
        if override:
            return list(override)
        return list(DEFAULT_PERIOD_ACTIVITIES.get(period, [self._config.idle_activity]))

    def pick(self, period: str | None = None) -> str:
        period = period or self._presence.time_context().period
        candidates = self.candidates_for(period)
        return self._rng.choice(candidates) if candidates else self._config.idle_activity

    async def roll(self, *, force: bool = False) -> CharacterState:
        """Advance the activity when the roll interval has passed."""
        if not self._config.enabled:
            return await self._states.load()
        now = self._clock()
        interval = self._config.roll_interval_minutes * 60
        state = await self._states.load()

        if not force:
            if state.activity and now - self._last_roll < interval:
                return state
            if state.activity and state.activity_since and now - state.activity_since < interval:
                return state

        # Sleeping overrides everything: the character is resting, not gaming.
        if self._presence.is_sleeping():
            activity = "sleeping"
        else:
            activity = self.pick()

        self._last_roll = now
        updated = await self._states.set_activity(activity)
        if not state.current_focus:
            # A light "recently thinking about" hook; refreshed occasionally.
            focus = self._rng.choice([*self.candidates_for("afternoon"), "chatting"])
            if focus != activity:
                updated = await self._states.update(current_focus=focus)
        return updated

    def snapshot(self) -> dict[str, Any]:
        ctx = self._presence.time_context()
        return {
            "enabled": self._config.enabled,
            "period": ctx.period,
            "period_generated": ctx.describe(),
            "candidates": self.candidates_for(ctx.period),
            "roll_interval_minutes": self._config.roll_interval_minutes,
            "sleeping": ctx.is_sleeping,
        }
