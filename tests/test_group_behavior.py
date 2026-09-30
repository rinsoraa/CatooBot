"""Group participation gate + scheduler tests (spec §36/§74)."""

from __future__ import annotations

import asyncio
import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.behavior.engine import CharacterBehaviorEngine
from app.behavior.presence import PresenceResolver
from app.behavior.scheduler import BehaviorScheduler
from app.behavior.topics import TopicManager
from app.character.relationship import RelationshipManager
from app.character.state import StateManager
from app.config.settings import (
    BehaviorActivityConfig,
    BehaviorConfig,
    BehaviorGroupConfig,
    BehaviorInitiativeConfig,
    BehaviorScheduleConfig,
    DatabaseConfig,
)
from app.database.database import Database
from tests.conftest import make_bot

TZ = ZoneInfo("Asia/Singapore")
DAY = datetime(2026, 9, 29, 15, 0, tzinfo=TZ)


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FrozenPresence(PresenceResolver):
    def __init__(self, moment: datetime = DAY, schedule=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__("Asia/Singapore", schedule or BehaviorScheduleConfig())
        self._moment = moment

    def now(self) -> datetime:  # type: ignore[override]
        return self._moment


class FakeEvent:
    """Minimal stand-in for a group MessageEvent."""

    def __init__(self, text: str = "今天吃什么", at_self_id: int | None = None,
                 at_others: bool = False) -> None:
        from app.message.message import Message
        from app.message.segment import AtSegment, TextSegment

        segments = []
        if at_self_id:
            segments.append(AtSegment(type="at", data={"qq": str(at_self_id)}))
        if at_others:
            segments.append(AtSegment(type="at", data={"qq": "999999"}))
        segments.append(TextSegment(type="text", data={"text": text}))
        self.message = Message(segments)
        self.self_id = 10001
        self.group_id = 555
        self.user_id = 42
        self.is_group = True
        self.sender = None


async def make_engine(tmp_path, group_overrides: dict | None = None):
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'grp.db'}"))
    await database.connect()
    states = StateManager(database)
    relationships = RelationshipManager(database)
    config = BehaviorConfig(
        group=BehaviorGroupConfig(**{"participation_enabled": True, **(group_overrides or {})}),
        activity=BehaviorActivityConfig(enabled=False),
        initiative=BehaviorInitiativeConfig(enabled=False),
    )
    engine = CharacterBehaviorEngine(
        config,
        FrozenPresence(),
        states,
        relationships=relationships,
        database=database,
        rng=random.Random(1),
    )
    return engine, database


class TestGroupGate:
    async def test_mention_always_responds(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_enabled": False})
        event = FakeEvent("你好呀", at_self_id=10001)
        decision = await engine.consider_group(event, "你好呀", mentioned=True)
        assert decision.respond and decision.reason == "mentioned"
        await database.close()

    async def test_non_mention_ignored_by_default(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_enabled": False})
        event = FakeEvent("今天吃什么")
        decision = await engine.consider_group(event, "今天吃什么", mentioned=False)
        assert not decision.respond and decision.reason == "participation_disabled"
        await database.close()

    async def test_disabled_group_never_participates(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path)
        event = FakeEvent("这个游戏挺好玩")
        decision = await engine.consider_group(
            event, "这个游戏挺好玩", mentioned=False, group_enabled=False
        )
        assert not decision.respond and decision.reason == "group_disabled"
        await database.close()

    async def test_zero_probability_stays_silent(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 0.0})
        event = FakeEvent("今天吃什么")
        for _ in range(5):
            decision = await engine.consider_group(event, "今天吃什么", mentioned=False)
            assert not decision.respond
        await database.close()

    async def test_high_probability_participates(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 1.0})
        event = FakeEvent("大家在聊什么游戏")
        decision = await engine.consider_group(event, "大家在聊什么游戏", mentioned=False)
        assert decision.respond and decision.reason == "participation"
        await database.close()

    async def test_group_cooldown_limits_frequency(self, tmp_path) -> None:
        clock = Clock()
        engine, database = await make_engine(
            tmp_path, {"participation_probability": 1.0, "cooldown_seconds": 300}
        )
        engine._clock = clock  # noqa: SLF001
        engine.initiative._clock = clock  # noqa: SLF001

        event = FakeEvent("聊聊游戏吧")
        first = await engine.consider_group(event, "聊聊游戏吧", mentioned=False)
        assert first.respond
        await engine.note_group_participation(555)

        second = await engine.consider_group(event, "聊聊游戏吧", mentioned=False)
        assert not second.respond and second.reason == "cooldown"

        clock.advance(301)
        third = await engine.consider_group(event, "另一个话题在这里", mentioned=False)
        assert third.respond
        await database.close()

    async def test_message_addressed_to_someone_else_ignored(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 1.0})
        event = FakeEvent("你觉得呢", at_others=True)
        decision = await engine.consider_group(event, "你觉得呢", mentioned=False)
        assert not decision.respond and decision.reason == "addressed_to_someone_else"
        await database.close()

    async def test_too_short_message_ignored(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 1.0})
        decision = await engine.consider_group(FakeEvent("嗯"), "嗯", mentioned=False)
        assert not decision.respond and decision.reason == "too_short"
        await database.close()

    async def test_sleeping_blocks_group_participation(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 1.0})
        engine.presence = FrozenPresence(datetime(2026, 9, 29, 3, 0, tzinfo=TZ))
        decision = await engine.consider_group(FakeEvent("半夜聊天"), "半夜聊天", mentioned=False)
        assert not decision.respond and decision.reason == "sleeping"
        await database.close()

    async def test_topic_relevance_raises_probability(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path, {"participation_probability": 0.0})
        topics = TopicManager(database)
        await topics.create("group:555", "Minecraft 服务器的建筑计划", importance=0.8)
        engine.topics = topics
        event = FakeEvent("Minecraft 服务器什么时候开")
        decision = await engine.consider_group(
            event, "Minecraft 服务器什么时候开", mentioned=False
        )
        assert decision.detail["related"] is True
        assert decision.probability > 0.0
        await database.close()


class TestPrivateGate:
    async def test_private_always_answers(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path)
        decision = await engine.consider_private(FakeEvent("你好"), "你好")
        assert decision.respond and decision.reason == "private_direct"
        await database.close()

    async def test_dnd_can_block_private_replies_when_configured(self, tmp_path) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'dnd.db'}"))
        await database.connect()
        schedule = BehaviorScheduleConfig(
            dnd_enabled=True, dnd_start="14:00", dnd_end="16:00", dnd_blocks_replies=True
        )
        config = BehaviorConfig(activity=BehaviorActivityConfig(enabled=False))
        engine = CharacterBehaviorEngine(
            config,
            FrozenPresence(DAY, schedule),
            StateManager(database),
            database=database,
        )
        decision = await engine.consider_private(FakeEvent("在吗"), "在吗")
        assert not decision.respond and decision.reason == "dnd"
        await database.close()


class TestMoodObservation:
    async def test_negative_message_lowers_mood(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path)
        await engine.states.load()
        await engine.observe_conversation("今天好累啊", "那早点休息吧")
        assert engine.states.state.mood == "quiet"  # one step down from neutral
        await database.close()

    async def test_positive_message_raises_mood(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path)
        await engine.states.load()
        await engine.observe_conversation("哈哈这个太好笑了", "是吧")
        assert engine.states.state.mood == "happy"
        await database.close()

    async def test_plain_message_leaves_mood_alone(self, tmp_path) -> None:
        engine, database = await make_engine(tmp_path)
        await engine.states.load()
        await engine.observe_conversation("今天几号", "29 号")
        assert engine.states.state.mood == "neutral"
        await database.close()


class TestScheduler:
    async def test_tick_runs_and_counts(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        await bot.database.connect()
        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
        await scheduler.tick()
        assert scheduler.ticks == 1
        await bot.shutdown()

    async def test_start_and_stop(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        await bot.database.connect()
        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=0.05)
        await scheduler.start()
        assert scheduler.running
        await asyncio.sleep(0.12)
        assert scheduler.ticks >= 1
        await scheduler.stop()
        assert not scheduler.running
        await bot.shutdown()

    async def test_failing_step_does_not_stop_loop(self, tmp_path) -> None:
        """One broken behaviour must not kill the scheduler (spec §74)."""
        bot = make_bot(tmp_path)
        await bot.database.connect()
        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)

        async def boom() -> None:
            raise RuntimeError("activity exploded")

        bot.behavior.tick = boom  # type: ignore[assignment]
        await scheduler.tick()
        assert scheduler.errors == 1
        assert scheduler.ticks == 1  # loop still alive

        await scheduler.tick()
        assert scheduler.errors == 2
        await bot.shutdown()

    async def test_database_failure_is_survivable(self, tmp_path) -> None:
        """DB trouble degrades silently: components swallow it, chat keeps going.

        The scheduler must not raise, and must keep ticking afterwards.
        """
        bot = make_bot(tmp_path)
        await bot.database.connect()
        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
        await bot.database.close()  # pull the rug out

        await scheduler.tick()  # must not raise
        assert scheduler.ticks == 1
        assert scheduler.running is False  # never started, still fine

        await scheduler.tick()
        assert scheduler.ticks == 2  # loop logic still healthy
        await bot.shutdown()

    async def test_initiative_pass_skipped_when_disabled(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        await bot.database.connect()
        assert not bot.behavior.initiative.enabled
        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
        await scheduler.tick()
        assert scheduler.errors == 0
        await bot.shutdown()


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
class TestInitiativeEndToEnd:
    async def test_proactive_message_sent_once_and_gated_afterwards(self, tmp_path) -> None:
        """Scheduler → initiative → delivery, with the user not replying."""
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, AppConfig
        from tests.ai_mocks import MockAIProvider

        provider = MockAIProvider(behaviors={"A": ["你那个音乐页面后来弄好了吗"]})
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": f"sqlite:///{tmp_path / 'e2e.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            behavior={
                "reply": {"enabled": False},
                "activity": {"enabled": False},
                # tests must not depend on the wall clock: no sleep window
                "schedule": {"sleep_enabled": False, "dnd_enabled": False},
                "initiative": {
                    "enabled": True,
                    "min_interval_minutes": 60,
                    "daily_limit": 5,
                    "hourly_limit": 5,
                    "idle_hours": 1,
                    "min_relationship_stage": "new",
                    "base_probability": 1.0,
                    "topic_bonus": 0.0,
                    "relationship_bonus": 0.0,
                    "max_unanswered": 1,
                },
            },
        )
        bot = make_bot(tmp_path)
        bot.config = config
        bot.ai = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            bot.database,
            providers={"mock": provider},
        )
        bot.character.engine = bot.ai
        # Mirror production wiring (BehaviorService.save_settings): swapping the
        # behaviour config also rebuilds the presence resolver, otherwise the old
        # sleep window would still apply.
        from app.behavior.presence import PresenceResolver

        bot.behavior.config = config.behavior
        bot.behavior.activity._config = config.behavior.activity  # noqa: SLF001
        bot.behavior.initiative.config = config.behavior.initiative
        bot.presence = PresenceResolver(config.character.timezone, config.behavior.schedule)
        bot.behavior.presence = bot.presence
        bot.behavior.initiative.presence = bot.presence
        await bot.database.connect()
        await bot.character.start()

        # a known user with an unfinished topic and a stale last_seen
        rel = await bot.relationships.record_interaction(777)
        await bot.database.execute(
            "UPDATE relationships SET last_seen = ? WHERE user_id = ?", (1, "777")
        )
        await bot.behavior.topics.create("private:777", "个人网站的音乐页面", importance=0.9)

        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
        await scheduler.tick()

        texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
        assert texts == ["你那个音乐页面后来搞好了吗"] or len(texts) == 1
        assert rel.interaction_count == 1  # proactive turns do not inflate it

        # second pass: awaiting-reply gate keeps her quiet
        await scheduler.tick()
        assert len(bot.adapter.sent_texts()) == 1  # type: ignore[attr-defined]
        await bot.shutdown()

    async def test_no_candidate_means_no_message(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, AppConfig
        from tests.ai_mocks import MockAIProvider

        provider = MockAIProvider()
        config = AppConfig(
            bot={"name": "TestBot"},
            database={"url": f"sqlite:///{tmp_path / 'e2e2.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            behavior={
                "reply": {"enabled": False},
                "schedule": {"sleep_enabled": False, "dnd_enabled": False},
                "initiative": {"enabled": True, "idle_hours": 100, "base_probability": 1.0},
            },
        )
        bot = make_bot(tmp_path)
        bot.config = config
        bot.ai = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            bot.database,
            providers={"mock": provider},
        )
        bot.character.engine = bot.ai
        bot.behavior.initiative.config = config.behavior.initiative
        await bot.database.connect()
        await bot.relationships.record_interaction(888)  # recent, no topics

        scheduler = BehaviorScheduler(bot, bot.behavior, tick_seconds=1)
        await scheduler.tick()
        assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        assert provider.calls == []
        await bot.shutdown()
