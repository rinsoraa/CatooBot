"""Daily routine: what the character plausibly does in each time period.

The routine is **configuration, never hardcoded** (spec §21): the WebUI writes
``world.routine.periods`` and everything here reads it. The catalogue below is
only a *default* used when no routine has been configured yet.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from app.config.settings import WorldRoutineConfig
from app.world.clock import WorldClock
from app.world.models import WorldActivity

#: Built-in activity definitions, keyed by the label the WebUI shows.
ACTIVITY_CATALOGUE: dict[str, WorldActivity] = {
    "睡觉": WorldActivity("sleeping", "睡觉", location="卧室", energy_delta=0.06,
                          social="quiet", tags=["sleep"]),
    "赖床": WorldActivity("lazing", "赖床", location="卧室", energy_delta=0.03,
                          social="quiet", tags=["sleep", "slow"]),
    "吃早饭": WorldActivity("breakfast", "吃早饭", location="厨房", energy_delta=0.02,
                            social="alone", tags=["meal"]),
    "吃午饭": WorldActivity("lunch", "吃午饭", location="厨房", energy_delta=0.02,
                            social="alone", tags=["meal"]),
    "吃晚饭": WorldActivity("dinner", "吃晚饭", location="厨房", energy_delta=0.02,
                            social="alone", tags=["meal"]),
    "出门买东西": WorldActivity("errand", "出门买东西", location="便利店", energy_delta=-0.03,
                                social="alone", tags=["outside"]),
    "打游戏": WorldActivity("gaming", "打游戏", location="房间", energy_delta=-0.03,
                            social="alone", tags=["game", "focus"]),
    "建房子": WorldActivity("building", "在 Minecraft 里建房子", location="房间",
                            energy_delta=-0.03, social="alone", tags=["game", "focus", "creative"]),
    "看剧": WorldActivity("watching", "看剧", location="客厅", energy_delta=-0.02,
                          social="alone", tags=["media", "slow"]),
    "看电影": WorldActivity("movie", "看电影", location="客厅", energy_delta=-0.02,
                            social="alone", tags=["media", "slow"]),
    "听歌": WorldActivity("music", "听歌", location="房间", energy_delta=0.02,
                          social="alone", tags=["music", "slow"]),
    "刷手机": WorldActivity("scrolling", "刷手机", location="沙发", energy_delta=-0.02,
                            social="quiet", tags=["phone", "slow"]),
    "和朋友连麦": WorldActivity("voice_chat", "和朋友连麦", location="房间", energy_delta=-0.03,
                                social="with_friends", tags=["social", "game"]),
    "在群里聊天": WorldActivity("group_chat", "在群里聊天", location="房间", energy_delta=-0.02,
                                social="chatting", tags=["social", "phone"]),
    "发呆": WorldActivity("idle", "发呆", location="房间", energy_delta=0.01,
                          social="quiet", tags=["slow", "ambient"]),
}

#: period -> candidate labels. A starting point the user can rewrite in WebUI.
DEFAULT_ROUTINE: dict[str, list[str]] = {
    "late_night": ["睡觉"],
    "early_morning": ["睡觉", "赖床"],
    "morning": ["赖床", "吃早饭", "刷手机", "听歌"],
    "noon": ["吃午饭", "看剧", "刷手机"],
    "afternoon": ["打游戏", "建房子", "看剧", "听歌"],
    "evening": ["打游戏", "和朋友连麦", "看电影", "吃晚饭", "在群里聊天"],
    "night": ["刷手机", "听歌", "发呆", "看剧"],
}

#: Periods in which the character is *not* available for normal chat.
REST_PERIODS: tuple[str, ...] = ("late_night",)


def activity_for_label(label: str) -> WorldActivity:
    """Resolve a (possibly user-written) label to an activity definition."""
    known = ACTIVITY_CATALOGUE.get(label)
    if known is not None:
        return known
    return WorldActivity(
        key=f"custom_{abs(hash(label)) % 100000}",
        label=label,
        location="",
        social="alone",
        weight=0.8,
        tags=["custom"],
    )


class RoutineService:
    """Reads the configured routine and answers 'what fits this period?'."""

    def __init__(
        self,
        routine: WorldRoutineConfig | None = None,
        clock: WorldClock | None = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.World")
        self._routine = routine or WorldRoutineConfig()
        self._clock = clock
        self._rng = rng or random.Random()

    # --------------------------------------------------------------- config

    def reconfigure(self, routine: WorldRoutineConfig) -> None:
        self._routine = routine

    @property
    def enabled(self) -> bool:
        return self._routine.enabled

    @property
    def transition_minutes(self) -> int:
        return self._routine.transition_minutes

    def periods(self) -> dict[str, list[str]]:
        configured = {
            period: [label for label in labels if str(label).strip()]
            for period, labels in (self._routine.periods or {}).items()
            if labels
        }
        return configured or dict(DEFAULT_ROUTINE)

    def labels_for(self, period: str) -> list[str]:
        return self.periods().get(period, [])

    def activities_for(self, period: str) -> list[WorldActivity]:
        return [activity_for_label(label) for label in self.labels_for(period)]

    # --------------------------------------------------------------- lookup

    def pick(self, period: str, *, avoid: str = "", reason: str = "") -> WorldActivity | None:
        """Choose an activity for ``period``, preferring a change of scene."""
        candidates = self.activities_for(period)
        if not candidates:
            return None
        if avoid:
            fresh = [item for item in candidates if item.label != avoid]
            if fresh:
                candidates = fresh
            # a single-activity period legitimately repeats (e.g. sleep)
        weights = [max(0.05, item.weight) for item in candidates]
        chosen = self._rng.choices(candidates, weights=weights, k=1)[0]
        self._log.debug(
            "[Routine] period=%s -> %s (reason=%s)", period, chosen.label, reason or "-"
        )
        return chosen

    def default_activity(self, period: str) -> WorldActivity:
        """Deterministic fallback (used by dry runs and recovery)."""
        candidates = self.activities_for(period)
        if candidates:
            return candidates[0]
        return activity_for_label("发呆")

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self._routine.enabled,
            "transition_minutes": self._routine.transition_minutes,
            "periods": self.periods(),
            "catalogue": sorted(ACTIVITY_CATALOGUE.keys()),
            "defaults": dict(DEFAULT_ROUTINE),
        }
