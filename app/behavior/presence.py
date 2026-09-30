"""Presence: the character's sense of time, sleep and do-not-disturb.

Everything here is derived from configuration (timezone + windows), never
hardcoded clocks (spec §19). Sleeping/DND are *fictional* character states —
they never take the bot offline; they only modulate behaviour.
"""

from __future__ import annotations

import logging
from datetime import datetime
from datetime import time as dtime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.behavior.models import TimeContext
from app.config.settings import BehaviorScheduleConfig

# Period -> (start_hour inclusive, end_hour exclusive)
_PERIODS: tuple[tuple[str, int, int], ...] = (
    ("early_morning", 5, 8),
    ("morning", 8, 12),
    ("noon", 12, 14),
    ("afternoon", 14, 18),
    ("evening", 18, 23),
    ("night", 23, 24),
    ("late_night", 0, 5),
)

_WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def _parse_hhmm(value: str) -> dtime | None:
    try:
        hour, _, minute = value.partition(":")
        return dtime(int(hour), int(minute))
    except (TypeError, ValueError):
        return None


def _in_window(now: dtime, start: dtime, end: dtime) -> bool:
    """Window may wrap midnight (e.g. 23:00 -> 08:00)."""
    if start == end:
        return False
    if start < end:
        return start <= now < end
    return now >= start or now < end


class PresenceResolver:
    """Answers 'what time is it for the character, and is she available?'."""

    def __init__(
        self,
        timezone: str = "Asia/Singapore",
        schedule: BehaviorScheduleConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.Behavior")
        self._schedule = schedule or BehaviorScheduleConfig()
        self.timezone_name = timezone
        self._tz: Any = None
        try:
            self._tz = ZoneInfo(timezone)
        except (ZoneInfoNotFoundError, ValueError):
            self._log.warning(
                "Unknown timezone '%s' — falling back to UTC+8 (install tzdata for full support)",
                timezone,
            )
            from datetime import timedelta
            from datetime import timezone as _tz

            self._tz = _tz(timedelta(hours=8), name=timezone or "UTC+8")

    def now(self) -> datetime:
        return datetime.now(self._tz)

    def time_context(self, moment: datetime | None = None) -> TimeContext:
        now = moment.astimezone(self._tz) if moment else self.now()
        period = self._period_for(now.hour)
        return TimeContext(
            timezone=self.timezone_name,
            local_time=now.strftime("%H:%M"),
            date_text=now.strftime("%Y-%m-%d"),
            weekday=_WEEKDAYS[now.weekday()],
            period=period,
            is_weekend=now.weekday() >= 5,
            is_sleeping=self.is_sleeping(now),
            in_dnd=self.in_dnd(now),
        )

    @staticmethod
    def _period_for(hour: int) -> str:
        for name, start, end in _PERIODS:
            if start <= hour < end:
                return name
        return "evening"

    # ------------------------------------------------------------- windows

    def is_sleeping(self, moment: datetime | None = None) -> bool:
        if not self._schedule.sleep_enabled:
            return False
        start = _parse_hhmm(self._schedule.sleep_start)
        end = _parse_hhmm(self._schedule.sleep_end)
        if start is None or end is None:
            return False
        now = moment.astimezone(self._tz) if moment else self.now()
        return _in_window(now.time(), start, end)

    def in_dnd(self, moment: datetime | None = None) -> bool:
        if not self._schedule.dnd_enabled:
            return False
        start = _parse_hhmm(self._schedule.dnd_start)
        end = _parse_hhmm(self._schedule.dnd_end)
        if start is None or end is None:
            return False
        now = moment.astimezone(self._tz) if moment else self.now()
        return _in_window(now.time(), start, end)

    def is_night(self, moment: datetime | None = None) -> bool:
        start = _parse_hhmm(self._schedule.night_start)
        end = _parse_hhmm(self._schedule.night_end)
        if start is None or end is None:
            return False
        now = moment.astimezone(self._tz) if moment else self.now()
        return _in_window(now.time(), start, end)

    # -------------------------------------------------------- availability

    def hard_block_reason(self, *, for_initiative: bool) -> str | None:
        """Hard gates that must never be overridden by probability (spec §56)."""
        ctx = self.time_context()
        if ctx.is_sleeping:
            return "sleeping"
        if ctx.in_dnd and (for_initiative or self._schedule.dnd_blocks_replies):
            return "dnd"
        return None
