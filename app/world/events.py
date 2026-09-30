"""LifeEventService: what happened in the character's world, written once.

Every write is idempotent: the event id is derived from
``(type, key, bucket)`` and the bucket defaults to the character's current day,
so a retried tick, a scheduler restart or a duplicate pass can never double-log
the same happening (spec §25/§26).

Writes are also *budgeted* (spec §49): a global hourly/daily ceiling plus
per-type daily caps and cooldowns, so the world cannot spam itself into a soap
opera.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from typing import Any

from app.config.settings import WorldEventsConfig
from app.world.clock import WorldClock
from app.world.models import (
    DEFAULT_IMPORTANCE,
    LIFE_EVENT_TYPES,
    LifeEvent,
    make_event_id,
)

#: Defaults used when config does not name a type explicitly.
_TYPE_DEFAULT_MAX_PER_DAY: dict[str, int] = {
    "ambient": 4,
    "activity": 12,
    "routine": 6,
    "interest": 4,
    "goal": 4,
    "project": 2,
    "relationship": 6,
    "milestone": 2,
}
_TYPE_DEFAULT_COOLDOWN: dict[str, int] = {
    "ambient": 60,       # colour must never repeat within the hour
    "activity": 0,       # activity changes are already smoothed by the routine
    "routine": 0,
    "interest": 90,
    "goal": 0,           # progress is governed by the daily goal budget (§30)
    "project": 240,
    "relationship": 30,
    "milestone": 0,
}


class LifeEventService:
    def __init__(
        self,
        *,
        database: Any,
        clock: WorldClock,
        config: WorldEventsConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._config = config or WorldEventsConfig()
        self._log = logging.getLogger("CatooBot.World") if logger is None else logger
        self.suppressed = 0

    # ---------------------------------------------------------------- write

    async def emit(
        self,
        *,
        type: str,
        summary: str,
        key: str = "",
        bucket: str | None = None,
        detail: dict[str, Any] | None = None,
        importance: float | None = None,
        source: str = "scheduled",
        related_goal: str = "",
        related_topic: str = "",
        session_id: str = "",
        user_id: str = "",
        allow_duplicate: bool = False,
    ) -> LifeEvent | None:
        """Record one event. Returns ``None`` when suppressed by policy."""
        if type not in LIFE_EVENT_TYPES:
            self._log.warning("[World] Unknown life-event type '%s' — recorded as ambient", type)
            type = "ambient"
        if self._db is None:
            return None

        moment = self._clock.snapshot()
        stamp = int(moment.moment.timestamp())
        bucket_key = bucket or moment.day_key
        event_id = make_event_id(type, key or summary[:40], bucket_key)

        if not allow_duplicate:
            duplicate = await self._exists(event_id)
            if duplicate:
                self.suppressed += 1
                self._log.debug("[World] duplicate event skipped (%s)", event_id)
                return None
            blocked = await self._blocked_reason(type, stamp)
            if blocked:
                self.suppressed += 1
                self._log.debug("[World] Event suppressed (%s): %s", blocked, summary[:60])
                return None

        event = LifeEvent(
            event_id=event_id,
            type=type,
            summary=summary[:500],
            detail=detail or {},
            source=source,
            importance=(
                DEFAULT_IMPORTANCE.get(type, 0.2) if importance is None else float(importance)
            ),
            related_goal=related_goal,
            related_topic=related_topic,
            session_id=session_id,
            user_id=user_id,
            created_at=stamp,
        )
        try:
            await self._db.execute(
                """INSERT OR IGNORE INTO world_events
                   (event_id, type, summary, detail, source, importance, related_goal,
                    related_topic, session_id, user_id, surfaced_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                event.to_row(),
            )
        except Exception:  # noqa: BLE001 - the world must survive a write failure
            self._log.exception("[World] Failed to record life event")
            return None
        self._log.info("[World] %s: %s", type, event.summary[:80])
        return event

    async def _exists(self, event_id: str) -> bool:
        row = await self._db.fetchone(
            "SELECT 1 AS ok FROM world_events WHERE event_id = ? LIMIT 1", (event_id,)
        )
        return row is not None

    async def _blocked_reason(self, event_type: str, stamp: int) -> str:
        hour_start = self._clock.start_of_hour()
        day_start = self._clock.start_of_day()
        if await self._count(type=None, since=hour_start) >= self._config.max_per_hour:
            return f"hourly_limit({self._config.max_per_hour})"
        if await self._count(type=None, since=day_start) >= self._config.max_per_day:
            return f"daily_limit({self._config.max_per_day})"
        type_cap = self._config.type_max_per_day.get(
            event_type, _TYPE_DEFAULT_MAX_PER_DAY.get(event_type, 0)
        )
        if type_cap and await self._count(type=event_type, since=day_start) >= type_cap:
            return f"type_daily_limit({event_type}:{type_cap})"
        cooldown = self._config.type_cooldown_minutes.get(
            event_type, _TYPE_DEFAULT_COOLDOWN.get(event_type, 0)
        )
        if cooldown:
            last = await self.last_of_type(event_type)
            if last is not None and self._clock.elapsed_since(last.created_at) < cooldown * 60:
                return f"type_cooldown({event_type}:{cooldown}m)"
        return ""

    async def _count(self, *, type: str | None, since: float) -> int:
        if type:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM world_events WHERE type = ? AND created_at >= ?",
                (type, int(since)),
            )
        else:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM world_events WHERE created_at >= ?", (int(since),)
            )
        return int(row["n"]) if row else 0

    # ---------------------------------------------------------------- reads

    async def recent(
        self,
        limit: int = 10,
        *,
        types: Iterable[str] | None = None,
        since: float | None = None,
        min_importance: float = 0.0,
        exclude_types: Iterable[str] | None = None,
    ) -> list[LifeEvent]:
        sql = "SELECT * FROM world_events"
        clauses: list[str] = []
        params: list[Any] = []
        type_list = list(types) if types else []
        if type_list:
            clauses.append(f"type IN ({', '.join('?' for _ in type_list)})")
            params.extend(type_list)
        excluded = list(exclude_types) if exclude_types else []
        if excluded:
            clauses.append(f"type NOT IN ({', '.join('?' for _ in excluded)})")
            params.extend(excluded)
        if since:
            clauses.append("created_at >= ?")
            params.append(int(since))
        if min_importance:
            clauses.append("importance >= ?")
            params.append(float(min_importance))
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(int(limit))
        rows = await self._db.fetchall(sql, tuple(params))
        return [LifeEvent.from_row(row) for row in rows]

    async def between(self, start: float, end: float, limit: int = 200) -> list[LifeEvent]:
        rows = await self._db.fetchall(
            """SELECT * FROM world_events WHERE created_at BETWEEN ? AND ?
               ORDER BY created_at ASC, id ASC LIMIT ?""",
            (int(start), int(end), int(limit)),
        )
        return [LifeEvent.from_row(row) for row in rows]

    async def last_of_type(self, event_type: str) -> LifeEvent | None:
        row = await self._db.fetchone(
            "SELECT * FROM world_events WHERE type = ? ORDER BY created_at DESC LIMIT 1",
            (event_type,),
        )
        return LifeEvent.from_row(row) if row else None

    async def surfaced_unseen(self, limit: int = 5) -> list[LifeEvent]:
        rows = await self._db.fetchall(
            """SELECT * FROM world_events WHERE surfaced_at IS NULL
               ORDER BY created_at DESC, id DESC LIMIT ?""",
            (int(limit),),
        )
        return [LifeEvent.from_row(row) for row in rows]

    # --------------------------------------------------------------- upkeep

    async def mark_surfaced(self, event_ids: Iterable[str]) -> None:
        ids = [event_id for event_id in event_ids if event_id]
        if not ids:
            return
        stamp = int(self._clock.now().timestamp())
        try:
            await self._db.execute(
                f"""UPDATE world_events SET surfaced_at = ?
                    WHERE event_id IN ({', '.join('?' for _ in ids)})""",
                (stamp, *ids),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[World] Failed to mark events as surfaced")

    async def prune(self, keep_days: int = 60) -> int:
        """Timeline is *not* memory: old colour is disposable (spec §28)."""
        cutoff = int(self._clock.now().timestamp() - keep_days * 86400)
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM world_events WHERE created_at < ?", (cutoff,)
        )
        count = int(row["n"]) if row else 0
        if count:
            await self._db.execute("DELETE FROM world_events WHERE created_at < ?", (cutoff,))
            self._log.info("[World] Pruned %s old life events (keep=%sd)", count, keep_days)
        return count

    async def stats(self) -> dict[str, Any]:
        day_start = self._clock.start_of_day()
        hour_start = self._clock.start_of_hour()
        row = await self._db.fetchone("SELECT COUNT(*) AS n FROM world_events")
        return {
            "total": int(row["n"]) if row else 0,
            "today": await self._count(type=None, since=day_start),
            "this_hour": await self._count(type=None, since=hour_start),
            "limit_per_hour": self._config.max_per_hour,
            "limit_per_day": self._config.max_per_day,
            "suppressed": self.suppressed,
        }
