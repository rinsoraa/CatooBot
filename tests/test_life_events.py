"""Life-event tests (v0.8 §24-§26/§49): idempotency and burst protection.

Spec coverage: an event id is derived from (type, key, bucket) so retried
ticks cannot double-log; global and per-type limits plus cooldowns keep the
world from spamming itself; the timeline keeps nothing that is never used.
"""

from __future__ import annotations

from app.config.settings import DatabaseConfig, WorldEventsConfig
from app.database.database import Database
from app.world.clock import WorldClock
from app.world.events import LifeEventService
from tests.world_helpers import FakeTime, local_stamp


async def make_service(tmp_path, config: WorldEventsConfig | None = None, name="events.db"):
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    fake = FakeTime(local_stamp())
    clock = WorldClock(timezone="Asia/Singapore", clock=fake)
    service = LifeEventService(database=database, clock=clock, config=config)
    return service, database, fake


class TestEmit:
    async def test_emit_records_event(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        event = await service.emit(type="activity", summary="在房间打游戏", key="a1")
        assert event is not None and event.event_id
        rows = await database.fetchall("SELECT * FROM world_events")
        assert len(rows) == 1
        await database.close()

    async def test_same_bucket_is_idempotent(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        first = await service.emit(type="activity", summary="在房间打游戏", key="a1")
        second = await service.emit(type="activity", summary="在房间打游戏", key="a1")
        assert first is not None and second is None
        assert await _count(database) == 1
        assert service.suppressed == 1
        await database.close()

    async def test_new_day_allows_the_same_happening(self, tmp_path) -> None:
        service, database, fake = await make_service(tmp_path)
        await service.emit(type="routine", summary="新的一天", key="day")
        fake.advance(86400)
        again = await service.emit(type="routine", summary="新的一天", key="day")
        assert again is not None
        assert await _count(database) == 2
        await database.close()

    async def test_idempotency_survives_restart(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path, name="restart.db")
        await service.emit(type="activity", summary="在房间打游戏", key="a1")
        restarted = LifeEventService(
            database=database, clock=service._clock, config=WorldEventsConfig()  # noqa: SLF001
        )
        assert await restarted.emit(type="activity", summary="在房间打游戏", key="a1") is None
        await database.close()

    async def test_unknown_type_falls_back_to_ambient(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        event = await service.emit(type="nonsense", summary="???")
        assert event is not None and event.type == "ambient"
        await database.close()

    async def test_importance_default_per_type(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        milestone = await service.emit(type="milestone", summary="完成了一件大事")
        ambient = await service.emit(type="ambient", summary="小事一件")
        assert milestone.importance > ambient.importance
        await database.close()


class TestBurstProtection:
    async def test_hourly_limit(self, tmp_path) -> None:
        service, database, _fake = await make_service(
            tmp_path, WorldEventsConfig(max_per_hour=1, max_per_day=50)
        )
        await service.emit(type="ambient", summary="第一件", key="one")
        blocked = await service.emit(type="ambient", summary="第二件", key="two")
        assert blocked is None
        await database.close()

    async def test_daily_limit(self, tmp_path) -> None:
        service, database, fake = await make_service(
            tmp_path, WorldEventsConfig(max_per_hour=100, max_per_day=2)
        )
        await service.emit(type="activity", summary="一", key="1")
        fake.advance(3600)
        await service.emit(type="activity", summary="二", key="2")
        fake.advance(3600)
        assert await service.emit(type="activity", summary="三", key="3") is None
        await database.close()

    async def test_per_type_daily_cap(self, tmp_path) -> None:
        service, database, _fake = await make_service(
            tmp_path,
            WorldEventsConfig(max_per_hour=50, max_per_day=50, type_max_per_day={"ambient": 1}),
        )
        await service.emit(type="ambient", summary="一", key="1")
        assert await service.emit(type="ambient", summary="二", key="2") is None
        # other types are unaffected
        assert await service.emit(type="activity", summary="换个类型的活动", key="3") is not None
        await database.close()

    async def test_per_type_cooldown(self, tmp_path) -> None:
        service, database, fake = await make_service(
            tmp_path,
            WorldEventsConfig(
                max_per_hour=50,
                max_per_day=50,
                type_max_per_day={"ambient": 5},
                type_cooldown_minutes={"ambient": 60},
            ),
        )
        await service.emit(type="ambient", summary="一", key="1")
        fake.advance(30 * 60)
        assert await service.emit(type="ambient", summary="二", key="2") is None
        fake.advance(31 * 60)
        assert await service.emit(type="ambient", summary="三", key="3") is not None
        await database.close()

    async def test_allow_duplicate_bypasses_limits(self, tmp_path) -> None:
        """Explicit overrides exist for manual/WebUI writes only."""
        service, database, _fake = await make_service(
            tmp_path, WorldEventsConfig(max_per_hour=1, max_per_day=1)
        )
        await service.emit(type="goal", summary="一", key="1")
        forced = await service.emit(
            type="goal", summary="二", key="2", allow_duplicate=True
        )
        assert forced is not None
        await database.close()


class TestReads:
    async def test_recent_filters(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        await service.emit(type="goal", summary="目标事件", key="g")
        await service.emit(type="ambient", summary="小事", key="a")
        goals = await service.recent(limit=10, types=["goal"])
        assert [e.type for e in goals] == ["goal"]
        others = await service.recent(limit=10, exclude_types=["goal"])
        assert [e.type for e in others] == ["ambient"]
        await database.close()

    async def test_recent_respects_importance(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        await service.emit(type="ambient", summary="小事", key="a", importance=0.1)
        await service.emit(type="milestone", summary="大事", key="m", importance=0.9)
        strong = await service.recent(limit=10, min_importance=0.5)
        assert [e.type for e in strong] == ["milestone"]
        await database.close()

    async def test_between_orders_oldest_first(self, tmp_path) -> None:
        service, database, fake = await make_service(tmp_path)
        await service.emit(type="activity", summary="第一件", key="1")
        fake.advance(60)
        await service.emit(type="activity", summary="第二件", key="2")
        rows = await service.between(local_stamp() - 10, fake.value + 10)
        assert [e.summary for e in rows] == ["第一件", "第二件"]
        await database.close()

    async def test_surfaced_tracking(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        event = await service.emit(type="milestone", summary="完成了一件大事")
        unseen = await service.surfaced_unseen()
        assert [e.event_id for e in unseen] == [event.event_id]
        await service.mark_surfaced([event.event_id])
        assert await service.surfaced_unseen() == []
        await database.close()

    async def test_stats(self, tmp_path) -> None:
        service, database, _fake = await make_service(tmp_path)
        await service.emit(type="activity", summary="一", key="1")
        stats = await service.stats()
        assert stats["total"] == 1 and stats["today"] == 1
        assert stats["limit_per_hour"] >= 1
        await database.close()

    async def test_prune_removes_old_colour(self, tmp_path) -> None:
        service, database, fake = await make_service(tmp_path)
        await service.emit(type="ambient", summary="很久以前", key="old")
        fake.advance(10 * 86400)
        await service.emit(type="ambient", summary="最近", key="new")
        removed = await service.prune(keep_days=5)
        assert removed == 1
        assert await _count(database) == 1
        await database.close()


async def _count(database: Database) -> int:
    row = await database.fetchone("SELECT COUNT(*) AS n FROM world_events")
    return int(row["n"]) if row else 0
