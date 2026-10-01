"""Every field on the 对话行为 page (/sandbox/chat) is verified live.

Covers, one test per knob:
    私聊回复: reply.enabled / min_delay / max_delay / chunking(+probability+max_chunks)
              schedule.sleep_* / dnd_* / dnd_blocks_replies
    群聊参与: participation_enabled / participation_probability (credit rate) /
              min_message_length / cooldown (social) / @ bypass
    主动回复: initiative.enabled / min_interval / daily_limit / hourly_limit /
              min_relationship_stage / max_unanswered / long-absence candidates
    保存链路: BehaviorService.save_settings hot-applies every section.
"""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from app.behavior.engine import CharacterBehaviorEngine
from app.behavior.initiative import InitiativeCandidate, InitiativeEngine
from app.behavior.presence import PresenceResolver
from app.character.state import StateManager as CharacterStateManager
from app.config.settings import (
    BehaviorConfig,
    BehaviorGroupConfig,
    BehaviorInitiativeConfig,
    BehaviorReplyTimingConfig,
    BehaviorScheduleConfig,
    DatabaseConfig,
)
from app.database.database import Database
from app.response.splitter import MessageChunker
from app.response.timing import ReplyTiming
from app.social.cognition import SocialCognitionEngine
from tests.conftest import make_bot

TZ = ZoneInfo("Asia/Singapore")
DAY = datetime(2026, 9, 29, 15, 0, tzinfo=TZ)
NIGHT = datetime(2026, 9, 29, 3, 0, tzinfo=TZ)


class FrozenPresence(PresenceResolver):
    """Presence frozen at a moment with the given schedule windows."""

    def __init__(self, moment: datetime = DAY, schedule: BehaviorScheduleConfig | None = None):
        super().__init__(
            "Asia/Singapore",
            schedule or BehaviorScheduleConfig(sleep_enabled=False, dnd_enabled=False),
        )
        self._moment = moment

    def now(self) -> datetime:  # type: ignore[override]
        return self._moment


class FakeClock:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def _state() -> CharacterStateManager:
    return CharacterStateManager(None)


# =========================================================== 私聊回复


class TestPrivateReplies:
    def test_reply_disabled_means_instant(self) -> None:
        timing = ReplyTiming(BehaviorReplyTimingConfig(enabled=False), FrozenPresence())
        assert timing.compute(reply_text="你好呀", state=_state().state) == 0.0

    def test_min_and_max_delay_bound_the_delay(self) -> None:
        cfg = BehaviorReplyTimingConfig(enabled=True, min_delay=3.0, max_delay=3.0, jitter=0.0)
        timing = ReplyTiming(cfg, FrozenPresence())
        assert timing.compute(reply_text="随便一句话", state=_state().state) == 3.0

    def test_sleeping_makes_replies_slower(self) -> None:
        cfg = BehaviorReplyTimingConfig(enabled=True, min_delay=0.1, max_delay=60.0, jitter=0.0)
        schedule = BehaviorScheduleConfig(
            sleep_enabled=True, sleep_start="00:30", sleep_end="08:00"
        )
        day = ReplyTiming(cfg, FrozenPresence(DAY, schedule), rng=random.Random(1))
        night = ReplyTiming(cfg, FrozenPresence(NIGHT, schedule), rng=random.Random(1))
        slow = night.compute(reply_text="你好呀", state=_state().state)
        fast = day.compute(reply_text="你好呀", state=_state().state)
        assert night._presence.time_context().is_sleeping is True  # noqa: SLF001
        assert slow > fast

    def test_sleep_window_flags_presence(self) -> None:
        schedule = BehaviorScheduleConfig(
            sleep_enabled=True, sleep_start="00:30", sleep_end="08:00"
        )
        assert FrozenPresence(NIGHT, schedule).time_context().is_sleeping is True
        assert FrozenPresence(DAY, schedule).time_context().is_sleeping is False

    async def test_hot_applied_schedule_reaches_the_gate(self, tmp_path) -> None:
        """WebUI 改作息必须立刻生效。

        PresenceResolver captures the schedule at construction and everything
        (replies, timing, initiative) shares that one instance — so a WebUI save
        that only replaced ``bot.config`` left the gate on the old window until
        a restart. The window below covers the whole day, which makes the
        assertion independent of when the suite runs.
        """
        from app.web.services.behavior import BehaviorService

        bot = make_bot(tmp_path)
        await bot.database.connect()
        try:
            service = BehaviorService(bot)
            await service.apply_overrides(
                {
                    "schedule": {
                        "sleep_enabled": True,
                        "sleep_start": "00:00",
                        "sleep_end": "23:59",
                    }
                }
            )
            assert bot.presence.is_sleeping() is True
            assert bot.presence.hard_block_reason(for_initiative=False) == "sleeping"

            await service.apply_overrides({"schedule": {"sleep_enabled": False}})
            assert bot.presence.is_sleeping() is False
            assert bot.presence.hard_block_reason(for_initiative=False) is None
        finally:
            await bot.shutdown()

    def test_chunking_off_is_single_message(self) -> None:
        from app.config.settings import BehaviorChunkingConfig

        chunker = MessageChunker(BehaviorChunkingConfig(enabled=False))
        assert chunker.plan("第一句话。第二句话。第三句话。") == ["第一句话。第二句话。第三句话。"]

    def test_chunk_probability_one_splits_and_max_chunks_caps(self) -> None:
        from app.config.settings import BehaviorChunkingConfig

        chunker = MessageChunker(
            BehaviorChunkingConfig(chunk_probability=1.0, max_chunks=2),
            rng=random.Random(3),
        )
        chunks = chunker.plan("甲甲甲甲。乙乙乙乙。丙丙丙丙。丁丁丁丁。")
        assert 2 <= len(chunks) <= 2

    async def test_consider_private_respects_dnd_switch(self, tmp_path) -> None:
        schedule = BehaviorScheduleConfig(
            dnd_enabled=True, dnd_start="14:00", dnd_end="16:00", dnd_blocks_replies=True
        )
        config = BehaviorConfig()
        engine = CharacterBehaviorEngine(config, FrozenPresence(DAY, schedule), _state())
        event = _fake_private_event("在吗")
        blocked = await engine.consider_private(event, "在吗")
        assert blocked.respond is False and blocked.reason == "dnd"

        schedule2 = BehaviorScheduleConfig(
            dnd_enabled=True, dnd_start="14:00", dnd_end="16:00", dnd_blocks_replies=False
        )
        engine2 = CharacterBehaviorEngine(
            BehaviorConfig(), FrozenPresence(DAY, schedule2), _state()
        )
        allowed = await engine2.consider_private(event, "在吗")
        assert allowed.respond is True


def _fake_private_event(text: str):  # type: ignore[no-untyped-def]
    from app.message.message import Message
    from app.message.segment import TextSegment

    class _Sender:
        display_name = "测试"

    class _Event:
        def __init__(self) -> None:
            self.message = Message([TextSegment(type="text", data={"text": text})])
            self.self_id = 10001
            self.user_id = 42
            self.group_id = None
            self.is_group = False
            self.sender = _Sender()

    return _Event()


# =========================================================== 群聊参与


async def _social_engine(tmp_path, **group_overrides):  # type: ignore[no-untyped-def]
    bot = make_bot(tmp_path)
    bot.presence = FrozenPresence()
    group_cfg = BehaviorGroupConfig(**{"participation_enabled": True, **group_overrides})
    bot.config = bot.config.model_copy(
        update={"behavior": bot.config.behavior.model_copy(update={"group": group_cfg})}
    )
    clock = FakeClock()
    engine = SocialCognitionEngine(config=bot.config.social, bot=bot, database=None, clock=clock)
    bot.social = engine
    return bot, engine, clock


async def _decide(engine, text: str, *, mid: str = "1", mentioned: bool = False):  # type: ignore[no-untyped-def]
    return await engine.decide(
        group_id="g1",
        message_id=mid,
        user_id="u1",
        nickname="某人",
        text=text,
        mentioned=mentioned,
        reply_to_bot=False,
        group_enabled=True,
    )


class TestGroupParticipation:
    async def test_switch_off_means_no_organic_talk(self, tmp_path) -> None:
        _bot, engine, _clock = await _social_engine(tmp_path, participation_enabled=False)
        decision = await _decide(engine, "今天天气不错啊")
        assert decision.decision == "ignore"
        assert decision.reason_code == "participation_disabled"

    async def test_rate_one_joins_every_eligible_message(self, tmp_path) -> None:
        """频率 1.0：第一条合格消息就参与（额度一次攒满）。"""
        _bot, engine, _clock = await _social_engine(tmp_path, participation_probability=1.0)
        decision = await _decide(engine, "你们说这个游戏好玩吗")
        assert decision.decision == "reply"
        assert decision.reason_code == "participation_rate"

    async def test_rate_zero_pure_cognition(self, tmp_path) -> None:
        _bot, engine, _clock = await _social_engine(tmp_path, participation_probability=0.0)
        decision = await _decide(engine, "你们说这个游戏好玩吗")
        assert decision.decision == "observe"

    async def test_rate_half_accumulates_deterministically(self, tmp_path) -> None:
        """0.5：第一条攒额度不参与，第二条攒满参与——确定性累积，不是掷骰。"""
        _bot, engine, _clock = await _social_engine(tmp_path, participation_probability=0.5)
        first = await _decide(engine, "第一句话在这里", mid="1")
        second = await _decide(engine, "第二句话还是这里", mid="2")
        assert first.decision in ("observe", "ignore")
        assert second.decision == "reply"
        assert second.reason_code == "participation_rate"

    async def test_cooldown_blocks_right_after_a_reply(self, tmp_path) -> None:
        _bot, engine, clock = await _social_engine(tmp_path, participation_probability=1.0)
        assert (await _decide(engine, "第一条合格消息", mid="1")).decision == "reply"
        engine.policy.record_send("g1")  # the plugin does this after delivery
        blocked = await _decide(engine, "紧接着的第二条", mid="2")
        assert blocked.decision == "ignore" and blocked.reason_code == "cooldown"

    async def test_min_message_length_gate(self, tmp_path) -> None:
        _bot, engine, _clock = await _social_engine(
            tmp_path, participation_probability=1.0, min_message_length=5
        )
        decision = await _decide(engine, "哈哈")
        assert decision.decision == "ignore" and decision.reason_code == "too_short"

    async def test_mention_bypasses_the_switch(self, tmp_path) -> None:
        """@ 她必回，与参与开关/频率无关。"""
        _bot, engine, _clock = await _social_engine(
            tmp_path, participation_enabled=False, participation_probability=0.0
        )
        decision = await _decide(engine, "在吗", mentioned=True)
        assert decision.decision == "reply"
        assert decision.reason_code == "direct_mention"

    async def test_batch_path_falls_back_to_rate(self, tmp_path) -> None:
        """攒够一批（5 条）后仍由结构化判断；频率 0 时不抢话。"""
        _bot, engine, _clock = await _social_engine(tmp_path, participation_probability=0.0)
        for index in range(5):
            decision = await _decide(engine, f"第{index}条普通聊天内容", mid=str(index))
        assert decision.decision in ("observe", "ignore", "defer")


# =========================================================== 主动回复


async def _initiative(tmp_path, **overrides):  # type: ignore[no-untyped-def]
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'init.db'}"))
    await database.connect()
    cfg = BehaviorInitiativeConfig(**{"enabled": True, "base_probability": 1.0, **overrides})
    engine = InitiativeEngine(
        cfg, FrozenPresence(), database=database, rng=random.Random(1), clock=FakeClock()
    )
    return engine, database


def _candidate() -> InitiativeCandidate:
    return InitiativeCandidate(scope_key="private:7", user_id="7", reason="long_absence", topic="")


class TestProactiveReplies:
    async def test_switch_off_blocks_everything(self, tmp_path) -> None:
        engine, database = await _initiative(tmp_path, enabled=False)
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.allowed is False and gate.reason == "disabled"
        await database.close()

    async def test_probability_one_and_gates_pass(self, tmp_path) -> None:
        engine, database = await _initiative(tmp_path)
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.allowed is True, gate.reason
        await database.close()

    async def test_min_interval_between_proactive_messages(self, tmp_path) -> None:
        engine, database = await _initiative(tmp_path, min_interval_minutes=120)
        await engine.record_sent("private:7", "在吗", reason="long_absence")
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.reason in ("cooldown", "awaiting_reply")
        await database.close()

    async def test_daily_limit(self, tmp_path) -> None:
        engine, database = await _initiative(
            tmp_path, daily_limit=1, hourly_limit=0, min_interval_minutes=1, max_unanswered=9
        )
        await engine.record_sent("private:7", "第一句", reason="long_absence")
        engine._clock.advance(120)  # noqa: SLF001 - past min_interval, still same day
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.reason == "daily_limit"
        await database.close()

    async def test_hourly_limit(self, tmp_path) -> None:
        engine, database = await _initiative(
            tmp_path, hourly_limit=1, daily_limit=0, min_interval_minutes=1, max_unanswered=9
        )
        await engine.record_sent("private:7", "第一句", reason="long_absence")
        engine._clock.advance(120)  # noqa: SLF001 - past min_interval, still same hour
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.reason == "hourly_limit"
        await database.close()

    async def test_relationship_stage_gate(self, tmp_path) -> None:
        engine, database = await _initiative(tmp_path, min_relationship_stage="close")
        gate = await engine.evaluate(_candidate(), relationship_stage="new")
        assert gate.reason == "relationship_too_new"
        await database.close()

    async def test_max_unanswered_gate(self, tmp_path) -> None:
        engine, database = await _initiative(tmp_path, max_unanswered=1, min_interval_minutes=1)
        await engine.record_sent("private:7", "第一句", reason="long_absence")
        gate = await engine.evaluate(_candidate(), relationship_stage="close")
        assert gate.reason == "awaiting_reply"
        await database.close()

    async def test_idle_hours_drive_long_absence_candidate(self, tmp_path) -> None:
        """idle_hours：对方多久没说话，才会出现“好久没聊”的主动候选。"""
        engine, database = await _initiative(tmp_path, idle_hours=6.0)
        clock: FakeClock = engine._clock  # type: ignore[assignment]
        recent = await engine.build_candidates(
            scope_key="private:7",
            user_id="7",
            relationship_stage="close",
            last_seen=int(clock() - 2 * 3600),
        )
        stale = await engine.build_candidates(
            scope_key="private:7",
            user_id="7",
            relationship_stage="close",
            last_seen=int(clock() - 10 * 3600),
        )
        assert not any(c.reason == "long_absence" for c in recent)
        assert any(c.reason == "long_absence" for c in stale)
        await database.close()


# =========================================================== 保存链路


class TestSettingsSaveChain:
    async def test_save_hot_applies_every_section(self, tmp_path) -> None:
        from app.web.services.behavior import BehaviorService

        bot = make_bot(tmp_path)
        await bot.database.connect()
        service = BehaviorService(bot)

        form = {
            "reply_enabled": "1",
            "min_delay": "1.5",
            "max_delay": "5.5",
            "chunking_enabled": "1",
            "chunk_probability": "0.9",
            "max_chunks": "2",
            "sleep_enabled": "1",
            "sleep_start": "01:00",
            "sleep_end": "07:30",
            "dnd_enabled": "0",
            "dnd_start": "23:00",
            "dnd_end": "08:00",
            "dnd_blocks_replies": "0",
            "participation_enabled": "1",
            "participation_probability": "0.8",
            "min_message_length": "4",
            "initiative_enabled": "1",
            "min_interval_minutes": "90",
            "daily_limit": "2",
            "hourly_limit": "1",
            "idle_hours": "5",
            "min_relationship_stage": "close",
            "max_unanswered": "3",
        }
        await service.save_settings(form)

        # live components
        assert bot.reply_timing._config.min_delay == 1.5  # noqa: SLF001
        assert bot.reply_timing._config.max_delay == 5.5  # noqa: SLF001
        assert bot.response_planner._chunk_cfg.chunk_probability == 0.9  # noqa: SLF001
        assert bot.response_planner._chunk_cfg.max_chunks == 2  # noqa: SLF001
        assert bot.presence._schedule.sleep_start == "01:00"  # noqa: SLF001
        assert bot.behavior.config.group.participation_probability == 0.8
        assert bot.behavior.config.group.min_message_length == 4
        assert bot.behavior.initiative.config.daily_limit == 2
        assert bot.behavior.initiative.config.min_relationship_stage == "close"

        # persisted as overrides (DB), reload keeps them
        overrides = await service.load_overrides()
        assert overrides["group"]["participation_probability"] == 0.8
        assert overrides["reply"]["min_delay"] == 1.5
        await bot.shutdown()


# =========================================================== 页面渲染


class TestChatPageRendersEveryField:
    async def test_all_inputs_present(self, tmp_path) -> None:
        import socket

        import aiohttp

        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from app.web.server import WebServer
        from tests.conftest import FakeAdapter

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        config = AppConfig(
            database={"url": f"sqlite:///{tmp_path / 'page.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            web={
                "enabled": True,
                "host": "127.0.0.1",
                "port": port,
                "username": "admin",
                "password": "pw123",
            },
        )
        bot = Bot(config, FakeAdapter())
        await bot.database.connect()
        server = WebServer(bot.config.web, bot)
        await server.start()
        jar = aiohttp.CookieJar(unsafe=True)
        try:
            async with aiohttp.ClientSession(cookie_jar=jar) as session:
                await session.post(
                    f"http://127.0.0.1:{port}/login",
                    data={"username": "admin", "password": "pw123"},
                )
                resp = await session.get(f"http://127.0.0.1:{port}/sandbox/chat")
                body = await resp.text()
        finally:
            await server.stop()
            await bot.shutdown()

        for name in (
            "reply_enabled",
            "min_delay",
            "max_delay",
            "chunking_enabled",
            "chunk_probability",
            "max_chunks",
            "sleep_enabled",
            "sleep_start",
            "sleep_end",
            "dnd_enabled",
            "dnd_start",
            "dnd_end",
            "dnd_blocks_replies",
            "participation_enabled",
            "participation_probability",
            "min_message_length",
            "initiative_enabled",
            "min_interval_minutes",
            "daily_limit",
            "hourly_limit",
            "idle_hours",
            "min_relationship_stage",
            "max_unanswered",
        ):
            assert f"name='{name}'" in body or f'name="{name}"' in body, f"missing input: {name}"


# ============================================== 端到端：群消息 → 参与频率


class TestGroupParticipationEndToEnd:
    async def _bot(self, tmp_path, probability: float):  # type: ignore[no-untyped-def]
        from app.web.services.behavior import BehaviorService
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["在呢在呢"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        # Apply through the WebUI path, and switch the sleep window off: the
        # real default (00:30-08:00) would silently no-op this test at night —
        # the preset here is what the operator would do (作息 → 关闭睡觉门控).
        await BehaviorService(bot).apply_overrides(
            {
                "group": {
                    "participation_enabled": True,
                    "participation_probability": probability,
                },
                "schedule": {"sleep_enabled": False, "dnd_enabled": False},
            }
        )
        return bot

    async def test_rate_one_replies_to_a_plain_group_message(self, tmp_path) -> None:
        """用户实测场景：群聊参与开启 + 频率 1.0 → 普通群消息也会回复。"""
        from tests.conftest import group_event

        bot = await self._bot(tmp_path, 1.0)
        try:
            await bot.event_bus.emit(group_event("你们说这个游戏好玩吗", user_id=888))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == ["在呢在呢"]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_rate_zero_stays_silent(self, tmp_path) -> None:
        from tests.conftest import group_event

        bot = await self._bot(tmp_path, 0.0)
        try:
            await bot.event_bus.emit(group_event("你们说这个游戏好玩吗", user_id=888))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()
