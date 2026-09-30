"""Initiative engine + gate + topic thread tests (spec §72/§73)."""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.behavior.initiative import InitiativeCandidate, InitiativeEngine
from app.behavior.presence import PresenceResolver
from app.behavior.topics import TopicManager
from app.config.settings import BehaviorInitiativeConfig, BehaviorScheduleConfig

TZ = ZoneInfo("Asia/Singapore")
DAY = datetime(2026, 9, 29, 15, 0, tzinfo=TZ)  # awake, no DND


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FrozenPresence(PresenceResolver):
    def __init__(self, moment: datetime, schedule: BehaviorScheduleConfig | None = None) -> None:
        super().__init__("Asia/Singapore", schedule or BehaviorScheduleConfig())
        self._moment = moment

    def now(self) -> datetime:  # type: ignore[override]
        return self._moment


def make_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'init.db'}"))


def make_engine(
    tmp_path,
    *,
    presence: PresenceResolver | None = None,
    clock: Clock | None = None,
    **config_overrides,
) -> tuple[InitiativeEngine, TopicManager]:
    from app.database.database import Database

    database: Database = make_db(tmp_path)
    topics = TopicManager(database, clock=clock or Clock())
    config = BehaviorInitiativeConfig(**{"enabled": True, **config_overrides})
    engine = InitiativeEngine(
        config,
        presence or FrozenPresence(DAY),
        database=database,
        topics=topics,
        rng=random.Random(1),
        clock=clock or Clock(),
    )
    return engine, topics


async def open_db(engine: InitiativeEngine) -> None:
    await engine._db.connect()  # noqa: SLF001 - test helper


def candidate(
    reason: str = "unfinished_topic", topic: str = "网站的音乐页面"
) -> InitiativeCandidate:
    return InitiativeCandidate(
        scope_key="private:1", user_id="1", reason=reason, topic=topic
    )


class TestGateHardRules:
    async def test_disabled_engine_rejects(self, tmp_path) -> None:
        engine, _ = make_engine(tmp_path, enabled=False)
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "disabled"
        await engine._db.close()  # noqa: SLF001

    async def test_sleeping_blocks_initiative(self, tmp_path) -> None:
        night = FrozenPresence(datetime(2026, 9, 29, 3, 0, tzinfo=TZ))
        engine, _ = make_engine(tmp_path, presence=night)
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "sleeping"
        await engine._db.close()  # noqa: SLF001

    async def test_dnd_blocks_initiative(self, tmp_path) -> None:
        schedule = BehaviorScheduleConfig(dnd_enabled=True, dnd_start="14:00", dnd_end="16:00")
        presence = FrozenPresence(DAY, schedule)
        engine, _ = make_engine(tmp_path, presence=presence)
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "dnd"
        await engine._db.close()  # noqa: SLF001

    async def test_user_disabled_blocks(self, tmp_path) -> None:
        engine, _ = make_engine(tmp_path)
        await open_db(engine)
        result = await engine.evaluate(
            candidate(), relationship_stage="close", user_enabled=False
        )
        assert not result.allowed and result.reason == "user_disabled"
        await engine._db.close()  # noqa: SLF001

    async def test_relationship_stage_required(self, tmp_path) -> None:
        engine, _ = make_engine(tmp_path, min_relationship_stage="familiar")
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="new")
        assert not result.allowed and result.reason == "relationship_too_new"
        await engine._db.close()  # noqa: SLF001

    async def test_cooldown_blocks_second_message(self, tmp_path) -> None:
        clock = Clock()
        engine, _ = make_engine(tmp_path, clock=clock, min_interval_minutes=120, max_unanswered=5)
        await open_db(engine)
        await engine.record_sent("private:1", "上次说的话", reason="unfinished_topic")

        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "cooldown"

        clock.advance(121 * 60)
        result = await engine.evaluate(
            candidate(reason="long_absence", topic=""), relationship_stage="close"
        )
        assert result.reason != "cooldown"
        await engine._db.close()  # noqa: SLF001

    async def test_daily_limit(self, tmp_path) -> None:
        clock = Clock()
        engine, _ = make_engine(
            tmp_path, clock=clock, daily_limit=2, hourly_limit=99,
            min_interval_minutes=1, max_unanswered=5,
        )
        await open_db(engine)
        for _ in range(2):
            await engine.record_sent("private:1", "发过的话", reason="long_absence")
            clock.advance(120)
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "daily_limit"
        await engine._db.close()  # noqa: SLF001

    async def test_hourly_limit(self, tmp_path) -> None:
        clock = Clock()
        engine, _ = make_engine(
            tmp_path, clock=clock, hourly_limit=1, daily_limit=10,
            min_interval_minutes=1, max_unanswered=5,
        )
        await open_db(engine)
        await engine.record_sent("private:1", "发过的话", reason="long_absence")
        clock.advance(120)  # same hour
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "hourly_limit"
        await engine._db.close()  # noqa: SLF001

    async def test_awaiting_reply_blocks_followup(self, tmp_path) -> None:
        """One proactive invitation → wait for the user (spec §58/§59)."""
        clock = Clock()
        engine, _ = make_engine(tmp_path, clock=clock, max_unanswered=1, min_interval_minutes=1)
        await open_db(engine)
        await engine.record_sent("private:1", "在忙吗", reason="long_absence")
        clock.advance(600)
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason == "awaiting_reply"

        await engine.note_user_activity("private:1")  # user replied
        result = await engine.evaluate(candidate(), relationship_stage="close")
        assert result.reason != "awaiting_reply"
        await engine._db.close()  # noqa: SLF001

    async def test_no_reason_is_rejected(self, tmp_path) -> None:
        engine, _ = make_engine(tmp_path)
        await open_db(engine)
        empty = InitiativeCandidate(scope_key="private:1", user_id="1", reason="")
        result = await engine.evaluate(empty, relationship_stage="close")
        assert not result.allowed and result.reason == "no_reason"
        await engine._db.close()  # noqa: SLF001


class TestGateProbability:
    async def test_zero_probability_never_sends(self, tmp_path) -> None:
        engine, _ = make_engine(
            tmp_path, base_probability=0.0, topic_bonus=0.0, min_relationship_stage="new"
        )
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="new")
        assert not result.allowed and result.reason == "low_probability"
        await engine._db.close()  # noqa: SLF001

    async def test_full_probability_sends(self, tmp_path) -> None:
        engine, _ = make_engine(
            tmp_path,
            base_probability=1.0,
            topic_bonus=0.0,
            relationship_bonus=0.0,
            min_relationship_stage="new",
        )
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="new")
        assert result.allowed and result.reason == "unfinished_topic"
        await engine._db.close()  # noqa: SLF001

    async def test_topic_raises_probability(self, tmp_path) -> None:
        engine, _ = make_engine(
            tmp_path, base_probability=0.3, topic_bonus=0.5, min_relationship_stage="new"
        )
        await open_db(engine)
        result = await engine.evaluate(candidate(), relationship_stage="new")
        assert result.probability == pytest.approx(0.8)
        await engine._db.close()  # noqa: SLF001


class TestDuplicateDetection:
    async def test_similar_message_is_rejected(self, tmp_path) -> None:
        """Recent repeat wording must not be sent again (spec §57/§73)."""
        clock = Clock()
        engine, _ = make_engine(
            tmp_path, clock=clock, min_interval_minutes=1, max_unanswered=5,
            hourly_limit=99, daily_limit=99,
        )
        await open_db(engine)
        await engine.record_sent("private:1", "最近忙不忙呀", reason="long_absence")

        clock.advance(120)
        similar = InitiativeCandidate(
            scope_key="private:1", user_id="1", reason="long_absence", topic="最近忙不忙呀"
        )
        result = await engine.evaluate(similar, relationship_stage="close")
        assert not result.allowed and result.reason == "duplicate"

        clock.advance(120)
        different = InitiativeCandidate(
            scope_key="private:1",
            user_id="1",
            reason="unfinished_topic",
            topic="你那个音乐页面后来弄完了吗",
        )
        result = await engine.evaluate(different, relationship_stage="close")
        assert result.reason != "duplicate"
        await engine._db.close()  # noqa: SLF001


class TestCandidates:
    async def test_topic_candidate_generated(self, tmp_path) -> None:
        engine, topics = make_engine(tmp_path)
        await open_db(engine)
        await topics.create("private:1", "个人网站的音乐页面", importance=0.8)
        candidates = await engine.build_candidates(
            scope_key="private:1", user_id="1", last_seen=None, relationship_stage="familiar"
        )
        assert any(c.reason == "unfinished_topic" and "音乐页面" in c.topic for c in candidates)
        await engine._db.close()  # noqa: SLF001

    async def test_long_absence_candidate(self, tmp_path) -> None:
        clock = Clock()
        engine, _ = make_engine(tmp_path, clock=clock, idle_hours=6)
        await open_db(engine)
        candidates = await engine.build_candidates(
            scope_key="private:1",
            user_id="1",
            last_seen=int(clock.now) - 7 * 3600,
            relationship_stage="familiar",
        )
        assert any(c.reason == "long_absence" for c in candidates)
        await engine._db.close()  # noqa: SLF001

    async def test_no_candidate_without_reason(self, tmp_path) -> None:
        clock = Clock()
        engine, _ = make_engine(tmp_path, clock=clock, idle_hours=6)
        await open_db(engine)
        candidates = await engine.build_candidates(
            scope_key="private:1",
            user_id="1",
            last_seen=int(clock.now) - 60,
            relationship_stage="familiar",
        )
        assert candidates == []  # nothing to say → stay quiet (spec §60)
        await engine._db.close()  # noqa: SLF001


class TestPersistence:
    async def test_limits_survive_restart(self, tmp_path) -> None:
        """Counters live in SQLite, so a restart cannot reset the budget (§49)."""

        database = make_db(tmp_path)
        await database.connect()
        clock = Clock()
        config = BehaviorInitiativeConfig(
            enabled=True, min_interval_minutes=1, daily_limit=1, max_unanswered=5
        )
        first = InitiativeEngine(
            config, FrozenPresence(DAY), database=database, rng=random.Random(1), clock=clock
        )
        await first.record_sent("private:1", "第一次主动", reason="long_absence")
        await database.close()

        database2 = make_db(tmp_path)
        await database2.connect()
        second = InitiativeEngine(
            config, FrozenPresence(DAY), database=database2, rng=random.Random(1), clock=clock
        )
        result = await second.evaluate(candidate(), relationship_stage="close")
        assert not result.allowed and result.reason in {"daily_limit", "cooldown", "awaiting_reply"}
        await database2.close()

    async def test_events_recorded(self, tmp_path) -> None:
        engine, _ = make_engine(tmp_path)
        await open_db(engine)
        await engine.record_sent("private:1", "你好呀", reason="long_absence", user_id="1")
        events = await engine.recent_events(limit=10)
        assert any(e["type"] == "initiative_sent" for e in events)
        await engine._db.close()  # noqa: SLF001


class TestTopics:
    async def test_create_and_get_active(self, tmp_path) -> None:
        from app.database.database import Database

        database: Database = make_db(tmp_path)
        await database.connect()
        manager = TopicManager(database)
        await manager.create("user:1", "个人网站的音乐页面")
        active = await manager.get_active_topics("user:1")
        assert [t.title for t in active] == ["个人网站的音乐页面"]
        await database.close()

    async def test_scopes_are_isolated(self, tmp_path) -> None:
        from app.database.database import Database

        database: Database = make_db(tmp_path)
        await database.connect()
        manager = TopicManager(database)
        await manager.create("user:1", "甲的网站项目")
        await manager.create("user:2", "乙的论文")
        assert len(await manager.get_active_topics("user:1")) == 1
        assert len(await manager.get_active_topics("user:2")) == 1
        assert await manager.get_active_topics("user:3") == []
        await database.close()

    async def test_resolve_and_forget(self, tmp_path) -> None:
        from app.database.database import Database

        database: Database = make_db(tmp_path)
        await database.connect()
        manager = TopicManager(database)
        topic = await manager.create("user:1", "个人网站的音乐页面")
        assert topic is not None
        await manager.resolve(topic.id)
        assert await manager.get_active_topics("user:1") == []
        assert (await manager.get(topic.id)).status == "resolved"

        topic2 = await manager.create("user:1", "在做的游戏")
        await manager.forget(topic2.id)
        assert await manager.get_active_topics("user:1") == []
        await database.close()

    async def test_update_touches_timestamp(self, tmp_path) -> None:
        from app.database.database import Database

        database: Database = make_db(tmp_path)
        await database.connect()
        clock = Clock()
        manager = TopicManager(database, clock=clock)
        topic = await manager.create("user:1", "音乐页面")
        clock.advance(3600)
        updated = await manager.touch(topic.id)
        assert updated.last_discussed_at == int(clock.now)
        await database.close()

    async def test_similar_topic_does_not_duplicate(self, tmp_path) -> None:
        from app.database.database import Database

        database: Database = make_db(tmp_path)
        await database.connect()
        manager = TopicManager(database)
        first = await manager.create("user:1", "个人网站的音乐页面")
        again = await manager.create("user:1", "个人网站的音乐页面")
        assert again.id == first.id
        assert await manager.count("user:1") == 1
        await database.close()
