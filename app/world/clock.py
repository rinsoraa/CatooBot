"""Persistent world time: the clock every world rule reads (spec §12-§16).

The character lives on a configurable timezone, and **every** time-dependent
decision goes through here — business code never writes ``if hour > 18``.

Time is *logical*: the clock can be advanced for simulations, and elapsed time
is computed against the last observed tick so a restart does not "replay" the
downtime as a burst of events (spec §15/§57/§58).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Period boundaries are the single source of truth (spec §14). They mirror the
# behaviour engine's windows so both layers agree on what "evening" means.
PERIOD_BOUNDS: tuple[tuple[str, int, int], ...] = (
    ("late_night", 0, 5),
    ("early_morning", 5, 8),
    ("morning", 8, 12),
    ("noon", 12, 14),
    ("afternoon", 14, 18),
    ("evening", 18, 23),
    ("night", 23, 24),
)

WEEKDAYS = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


@dataclass
class WorldTime:
    """A resolved instant plus everything the world needs to know about it."""

    moment: datetime
    period: str
    date_text: str
    time_text: str
    weekday: str
    is_weekend: bool
    day_key: str            # YYYY-MM-DD in the character timezone
    hour_bucket: str        # YYYY-MM-DDTHH
    hour: int

    def describe(self) -> str:
        weekend = "，周末" if self.is_weekend else ""
        return f"现在是{_period_text(self.period)}（{self.time_text}），{self.weekday}{weekend}。"


def _period_text(period: str) -> str:
    return {
        "late_night": "深夜",
        "early_morning": "清晨",
        "morning": "上午",
        "noon": "中午",
        "afternoon": "下午",
        "evening": "晚上",
        "night": "夜里",
    }.get(period, "现在")


class WorldClock:
    """Timezone-aware, simulation-friendly clock."""

    def __init__(
        self,
        timezone: str = "Asia/Singapore",
        logger: logging.Logger | None = None,
        clock: Callable[[], float] = time.time,
        offset_seconds: float = 0.0,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.World")
        self.timezone_name = timezone or "Asia/Singapore"
        self._clock: Callable[[], float] = clock
        self._tz: Any = None
        self._offset = offset_seconds          # only used by the simulator
        try:
            self._tz = ZoneInfo(self.timezone_name)
        except (ZoneInfoNotFoundError, ValueError):
            self._log.warning(
                "Unknown world timezone '%s' — falling back to UTC+8", self.timezone_name
            )
            from datetime import timezone as _tz

            self._tz = _tz(timedelta(hours=8))

    # ------------------------------------------------------------- reading

    @property
    def offset_seconds(self) -> float:
        return self._offset

    def now(self) -> datetime:
        """Current character-local time (simulator offset included)."""
        stamp = float(self._clock()) + self._offset
        return datetime.fromtimestamp(stamp, tz=self._tz)

    def now_timestamp(self) -> float:
        """Logical-world wall-clock seconds (offset included) for episodes."""
        return float(self._clock()) + self._offset

    def snapshot(self, moment: datetime | None = None) -> WorldTime:
        current = moment or self.now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=self._tz)
        else:
            current = current.astimezone(self._tz)
        return WorldTime(
            moment=current,
            period=period_for(current.hour),
            date_text=current.strftime("%Y-%m-%d"),
            time_text=current.strftime("%H:%M"),
            weekday=WEEKDAYS[current.weekday()],
            is_weekend=current.weekday() >= 5,
            day_key=current.strftime("%Y-%m-%d"),
            hour_bucket=current.strftime("%Y-%m-%dT%H"),
            hour=current.hour,
        )

    def elapsed_since(self, timestamp: float | None) -> float:
        """Seconds since a wall-clock stamp (0 when unknown)."""
        if not timestamp:
            return 0.0
        return max(0.0, float(self._clock()) - float(timestamp))

    def hours_since(self, timestamp: float | None) -> float:
        return self.elapsed_since(timestamp) / 3600.0

    def days_between(self, earlier: float | None, later: float | None = None) -> float:
        if not earlier:
            return 0.0
        reference = float(later) if later else float(self._clock())
        return max(0.0, (reference - float(earlier)) / 86400.0)

    # ------------------------------------------------------ calendar bounds

    def today_key(self, moment: datetime | None = None) -> str:
        return self.snapshot(moment).day_key

    def hour_bucket(self, moment: datetime | None = None) -> str:
        return self.snapshot(moment).hour_bucket

    def start_of_day(self, moment: datetime | None = None) -> float:
        current = moment.astimezone(self._tz) if moment else self.now()
        midnight = current.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight.timestamp()

    def start_of_hour(self, moment: datetime | None = None) -> float:
        current = moment.astimezone(self._tz) if moment else self.now()
        top = current.replace(minute=0, second=0, microsecond=0)
        return top.timestamp()

    # ---------------------------------------------------------- simulation

    def advance(self, seconds: float) -> None:
        """Move the *character* clock forward (simulator / tests only)."""
        self._offset += seconds
        self._log.info("[World] clock advanced by %.1fs", seconds)

    def reset_offset(self) -> None:
        self._offset = 0.0

    @property
    def timezone(self) -> ZoneInfo:
        return self._tz


def period_for(hour: int) -> str:
    """Map an hour to a world period (spec §14)."""
    for name, start, end in PERIOD_BOUNDS:
        if start <= hour < end:
            return name
    return "evening"


def is_sleep_period(hour: int) -> bool:
    return hour >= 23 or hour < 8
