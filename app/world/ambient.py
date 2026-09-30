"""Ambient life events: low-frequency background colour (spec §45-§48).

Ambient events exist so the character feels like she *has* a life between
messages. They are deliberately:

* **rare** — cooldown plus a daily cap, both configurable;
* **state-aware** — templated off what she is actually doing, never random
  noise that contradicts the current activity;
* **fictional and unverifiable** — her room, her games, her playlists. She never
  claims to have done something in the real world that a user could check
  (spec §3/§45): no news, no real people, no places the user could verify.
"""

from __future__ import annotations

import logging
import random
from typing import Any

from app.config.settings import WorldAmbientConfig, WorldConfig
from app.world.clock import WorldClock
from app.world.events import LifeEventService
from app.world.routine import activity_for_label

#: Templates keyed by activity tag. They stay inside the character's own room,
#: library and imagination on purpose.
AMBIENT_TEMPLATES: dict[str, tuple[str, ...]] = {
    "game": (
        "在游戏里折腾了半天，进度只推进了一点点",
        "游戏里卡在同一个地方好几次，有点上头",
        "随手开了个新存档，试试别的玩法",
        "把手柄扔到一边又捡回来了",
    ),
    "creative": (
        "搭到一半的墙看着不太对，又拆了重来",
        "在地图里绕来绕去，最后决定换个位置重建",
        "翻出一张以前存的灵感图，对着改了改",
    ),
    "media": (
        "随便点开一部片子，看着看着走神了",
        "找了个片子当背景音，其实没怎么看进去",
        "看到一半停下来去倒了杯水，回来接着放",
    ),
    "music": (
        "同一首歌循环了好几遍",
        "翻出一个很久没听的歌单，从头听了一遍",
        "被推荐了一首没听过的歌，先存着",
    ),
    "meal": (
        "随便弄了点东西吃，味道一般",
        "吃饭的时候又顺手刷了会儿手机",
        "想着要不要点个外卖，最后还是自己对付了",
    ),
    "phone": (
        "刷手机刷到忘了本来要干嘛",
        "翻了翻聊天记录，又退出来了",
        "收藏了一堆东西，估计之后也不会看",
    ),
    "social": ("和几个朋友在语音里闲扯了一会儿",),
    "outside": ("出去溜达了一小圈，顺便买了点东西",),
    "sleep": ("翻了个身，继续睡",),
    "slow": ("发了会儿呆，什么也没干",),
    "custom": ("自己安静待了会儿",),
}

_DEFAULT_TEMPLATE = "自己待着，什么也没发生"

#: Ambient colour that is *not* allowed: anything the user could verify.
FORBIDDEN_AMBIENT_MARKERS: tuple[str, ...] = (
    "新闻", "热搜", "发布会", "直播", "线下", "见面", "出远门", "旅行", "航班", "地铁",
    "上班", "上学", "公司", "学校", "医院", "警察",
)


def ambient_templates_for(label: str) -> tuple[str, ...]:
    """Pick the template group that matches what she is doing."""
    activity = activity_for_label(label)
    for tag in activity.tags:
        if tag in AMBIENT_TEMPLATES:
            return AMBIENT_TEMPLATES[tag]
    return AMBIENT_TEMPLATES["custom"]


def is_safe_ambient(text: str) -> bool:
    return not any(marker in text for marker in FORBIDDEN_AMBIENT_MARKERS)


class AmbientLife:
    """Decides whether a small piece of background life happens now."""

    def __init__(
        self,
        *,
        events: LifeEventService,
        clock: WorldClock,
        config: WorldAmbientConfig | None = None,
        world_config: WorldConfig | None = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._events = events
        self._clock = clock
        self._config = config or WorldAmbientConfig()
        self._world = world_config
        self._log = logger or logging.getLogger("CatooBot.World")
        self._rng = rng or random.Random()

    @property
    def enabled(self) -> bool:
        if not self._config.enabled:
            return False
        return not (self._world is not None and self._world.rest_mode)

    async def maybe_emit(
        self,
        *,
        activity: str,
        period: str,
        sleeping: bool = False,
        catch_up: bool = False,
    ) -> str:
        """Emit at most one ambient event. Returns the summary ('' = nothing)."""
        if not self.enabled:
            return "skip:disabled"
        if sleeping:
            return "skip:sleeping"
        if period == "late_night":
            return "skip:late_night"

        last = await self._events.last_of_type("ambient")
        if last is not None:
            wait = self._config.min_interval_minutes * 60
            if self._clock.elapsed_since(last.created_at) < wait:
                return "skip:cooldown"
        today = await self._events.recent(
            limit=50, types=["ambient"], since=self._clock.start_of_day()
        )
        if len(today) >= self._config.max_per_day:
            return "skip:daily_cap"

        templates = ambient_templates_for(activity)
        recent_texts = {event.summary for event in today}
        pool = [text for text in templates if text not in recent_texts] or list(templates)
        text = self._rng.choice(pool)
        if not is_safe_ambient(text):
            self._log.warning("[World] Ambient text rejected by reality boundary: %s", text)
            return "skip:boundary"

        summary = f"{activity}的时候，{text}" if activity else text
        bucket = self._clock.hour_bucket()
        event = await self._events.emit(
            type="ambient",
            summary=summary,
            key=f"ambient:{activity}:{text[:16]}",
            bucket=bucket,
            importance=0.2,
            source="ambient",
        )
        if event is None:
            return "skip:suppressed"
        return summary

    def view(self) -> dict[str, Any]:
        return {
            "enabled": self._config.enabled,
            "min_interval_minutes": self._config.min_interval_minutes,
            "max_per_day": self._config.max_per_day,
            "templates": {tag: len(items) for tag, items in AMBIENT_TEMPLATES.items()},
        }
