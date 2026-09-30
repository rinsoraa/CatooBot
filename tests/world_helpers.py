"""Shared helpers for the v0.8 persistent-world tests.

Time is always injected: every world test pins the clock so periods, day
boundaries and cooldowns are deterministic.
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.character.state import StateManager
from app.config.settings import DatabaseConfig, WorldConfig
from app.database.database import Database
from app.world.clock import WorldClock
from app.world.runtime import WorldRuntime, build_world

TZ = "Asia/Singapore"


class FakeTime:
    """A movable wall clock (seconds since epoch)."""

    def __init__(self, start: float) -> None:
        self.value = float(start)

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds

    def set_local(self, day: int, hour: int, minute: int = 0) -> None:
        self.value = local_stamp(day=day, hour=hour, minute=minute)


def local_stamp(day: int = 15, hour: int = 14, minute: int = 30, month: int = 6) -> float:
    """2026-06-15 14:30 Asia/Singapore by default (a Monday afternoon)."""
    return datetime(2026, month, day, hour, minute, tzinfo=ZoneInfo(TZ)).timestamp()


def make_clock(start: float | None = None) -> tuple[WorldClock, FakeTime]:
    fake = FakeTime(start if start is not None else local_stamp())
    return WorldClock(timezone=TZ, clock=fake), fake


async def make_world(
    tmp_path,
    *,
    config: WorldConfig | None = None,
    start: float | None = None,
    name: str = "world.db",
    seed: int = 7,
) -> tuple[WorldRuntime, Database, FakeTime]:
    """A complete world runtime over a throwaway SQLite database."""
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    clock, fake = make_clock(start)
    state_manager = StateManager(database, clock=fake)
    world = build_world(
        config or WorldConfig(),
        database=database,
        state_manager=state_manager,
        timezone=TZ,
        clock=clock,
        rng=random.Random(seed),
    )
    await world.state.load()
    return world, database, fake


async def world_events(database: Database, event_type: str = "") -> list[dict[str, Any]]:
    sql = "SELECT * FROM world_events"
    params: tuple[Any, ...] = ()
    if event_type:
        sql += " WHERE type = ?"
        params = (event_type,)
    sql += " ORDER BY id ASC"
    return await database.fetchall(sql, params)


async def table_count(database: Database, table: str) -> int:
    row = await database.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
    return int(row["n"]) if row else 0


async def events_per_day(world, database: Database, event_type: str = "") -> dict[str, int]:
    """Event counts grouped by the character's calendar day."""
    from datetime import datetime

    sql = "SELECT created_at, type FROM world_events"
    params: tuple[Any, ...] = ()
    if event_type:
        sql += " WHERE type = ?"
        params = (event_type,)
    rows = await database.fetchall(sql, params)
    per_day: dict[str, int] = {}
    for row in rows:
        moment = datetime.fromtimestamp(int(row["created_at"]), tz=world.clock.timezone)
        day = world.clock.snapshot(moment).day_key
        per_day[day] = per_day.get(day, 0) + 1
    return per_day
