"""WorldTimeline: the ordered record of the character's life.

Deliberately **not** Memory: a timeline entry answers "what was she doing at
14:30" and is allowed to be forgotten after a while, while Memory holds what
matters for conversations (spec §27/§28). Timeline reads never re-rank, never
extract and never touch the memory pipeline.
"""

from __future__ import annotations

import logging
from typing import Any

from app.world.clock import WorldClock
from app.world.events import LifeEventService
from app.world.models import LifeEvent

_TYPE_LABEL = {
    "activity": "活动",
    "goal": "目标",
    "project": "项目",
    "interest": "兴趣",
    "routine": "作息",
    "relationship": "关系",
    "ambient": "日常",
    "milestone": "里程碑",
}


class WorldTimeline:
    def __init__(
        self,
        *,
        events: LifeEventService,
        clock: WorldClock,
        logger: logging.Logger | None = None,
    ) -> None:
        self._events = events
        self._clock = clock
        self._log = logger or logging.getLogger("CatooBot.World")

    # ---------------------------------------------------------------- views

    async def day(self, day_key: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """All events of one character-day, oldest first."""
        key = day_key or self._clock.today_key()
        start, end = self._day_bounds(key)
        rows = await self._events.between(start, end, limit=limit)
        return [self._row(item) for item in rows]

    async def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._events.recent(limit=limit)
        return [self._row(item) for item in rows]

    async def prompt_lines(self, limit: int = 4, *, hours: float = 48.0) -> list[str]:
        """Short "what happened lately" lines for the chat prompt (spec §41)."""
        since = self._clock.now().timestamp() - hours * 3600
        rows = await self._events.recent(limit=limit, since=since, min_importance=0.2)
        lines: list[str] = []
        for event in rows:
            when = event.created_text
            lines.append(f"{when} {event.summary}")
        return lines

    async def range(
        self, *, days: int = 7, limit: int = 500
    ) -> dict[str, list[dict[str, Any]]]:
        """Newest-first-per-day buckets for the timeline page (spec §53)."""
        from datetime import datetime

        days = max(1, min(60, days))
        now_stamp = self._clock.now().timestamp()
        result: dict[str, list[dict[str, Any]]] = {}
        for offset in range(days - 1, -1, -1):
            stamp = now_stamp - offset * 86400
            day_key = datetime.fromtimestamp(stamp, tz=self._clock.timezone).strftime("%Y-%m-%d")
            start, end = self._day_bounds(day_key)
            rows = await self._events.between(start, end, limit=limit)
            result[day_key] = [self._row(item) for item in rows]
        return result

    # --------------------------------------------------------------- replay

    async def replay(
        self,
        *,
        days: float = 1.0,
        limit: int = 300,
    ) -> list[dict[str, Any]]:
        """A read-only retelling of a past window (spec §52).

        Replay never mutates state, never sends messages and never writes
        events — it exists so a human can audit what the world did.
        """
        end = self._clock.now().timestamp()
        start = end - max(0.01, days) * 86400
        rows = await self._events.between(start, end, limit=limit)
        story: list[dict[str, Any]] = []
        previous: str = ""
        for event in rows:
            entry = self._row(event)
            entry["changed"] = bool(previous and previous != event.summary)
            previous = event.summary
            story.append(entry)
        self._log.info("[World] Replayed %.1f day(s): %s events", days, len(story))
        return story

    # ------------------------------------------------------------ analytics

    async def summary(self, *, days: float = 1.0) -> dict[str, Any]:
        end = self._clock.now().timestamp()
        start = end - max(0.01, days) * 86400
        rows = await self._events.between(start, end, limit=2000)
        counts: dict[str, int] = {}
        for event in rows:
            counts[event.type] = counts.get(event.type, 0) + 1
        return {
            "window_days": days,
            "total": len(rows),
            "by_type": counts,
            "first": rows[0].created_text if rows else "",
            "last": rows[-1].created_text if rows else "",
        }

    # --------------------------------------------------------------- helper

    def _row(self, event: LifeEvent) -> dict[str, Any]:
        return {
            "event_id": event.event_id,
            "type": event.type,
            "type_label": _TYPE_LABEL.get(event.type, event.type),
            "summary": event.summary,
            "importance": round(event.importance, 2),
            "source": event.source,
            "goal": event.related_goal,
            "session_id": event.session_id,
            "surfaced_at": event.surfaced_at,
            "created_at": event.created_at,
            "created_text": event.created_text,
            "detail": event.detail,
        }

    def _day_bounds(self, day_key: str) -> tuple[float, float]:
        from datetime import datetime, timedelta

        day = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=self._clock.timezone)
        start = day.timestamp()
        return start, (day + timedelta(days=1)).timestamp() - 1
