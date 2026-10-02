"""Social Cognition Engine tests (v0.9).

Core acceptance (spec v0.9 §126-§142): no-@ follow-ups are recognised structurally
(not by ``random()``), the 5-message observer triggers once per batch, hard
rules outrank soft judgment, the observer and the reply model stay separate,
and the decision path never rolls a die.
"""

from __future__ import annotations

import logging

from app.ai.models import AIResponse
from app.config.settings import SocialConfig
from app.social.attention import SocialAttention
from app.social.continuation import ContinuationDetector
from app.social.models import (
    ConversationThread,
    GroupMessage,
    ParticipationDecision,
    RelevanceScores,
)
from app.social.monitor import GroupConversationMonitor
from app.social.observer import GroupObserver
from app.social.policy import ParticipationPolicy
from app.social.relevance import RelevanceEvaluator
from app.social.thread import ThreadManager


class FakeTime:
    def __init__(self, start: float = 1_000_000.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


def msg(
    group: str, mid: str, user: str, content: str, *, bot: bool = False, at: float | None = None
) -> GroupMessage:
    return GroupMessage(
        message_id=mid,
        group_id=group,
        user_id="bot" if bot else user,
        nickname="" if bot else f"用户{user}",
        timestamp=at if at is not None else 0.0,
        content=content,
        is_bot_message=bot,
    )


class TestMonitor:
    def test_external_only_counted(self) -> None:
        monitor = GroupConversationMonitor(max_messages=30)
        monitor.record("g", "1", "a", "A", "你好", timestamp=1.0)
        monitor.record_bot("g", "2", "在的", timestamp=2.0)
        monitor.record("g", "3", "b", "B", "吃了吗", timestamp=3.0)
        assert monitor.unobserved_count("g") == 2  # bot msg not counted
        assert [m.message_id for m in monitor.unobserved("g")] == ["1", "3"]

    def test_buffer_is_bounded(self) -> None:
        monitor = GroupConversationMonitor(max_messages=3)
        for i in range(5):
            monitor.record("g", str(i), "u", "U", f"m{i}", timestamp=float(i))
        assert len(monitor.recent("g")) == 3
        assert monitor.recent("g")[-1].content == "m4"

    def test_mark_observed_advances_pointer(self) -> None:
        monitor = GroupConversationMonitor(max_messages=30)
        for i in range(4):
            monitor.record("g", str(i), "u", "U", f"m{i}", timestamp=float(i))
        monitor.mark_observed("g")
        monitor.record("g", "4", "u", "U", "m4", timestamp=4.0)
        assert [m.message_id for m in monitor.unobserved("g")] == ["4"]


class TestThreadManager:
    def test_thread_expires_after_window(self) -> None:
        clock = FakeTime()
        manager = ThreadManager(config=SocialConfig().continuation, clock=clock)
        manager.open("g", topic="看剧", bot_message="我在看剧", bot_message_id="1")
        assert manager.get("g") is not None
        clock.advance(11 * 60)  # 11 minutes > 10 minute window
        assert manager.get("g") is None

    def test_thread_reopens_and_tracks_participants(self) -> None:
        clock = FakeTime()
        manager = ThreadManager(config=SocialConfig().continuation, clock=clock)
        manager.open("g", participants=["a"])
        manager.note_user_message("g", user_id="b", message="好看吗", message_id="2")
        thread = manager.get("g")
        assert thread is not None and "a" in thread.participants and "b" in thread.participants
        assert thread.last_user_message == "好看吗"


class TestContinuationDetector:
    async def test_no_at_follow_up_is_recognized(self) -> None:
        clock = FakeTime()
        detector = ContinuationDetector(follow_up_threshold=0.75, window_minutes=10, clock=clock)
        thread = ConversationThread(
            thread_id="t",
            group_id="g",
            topic="看剧",
            last_bot_message="我在看剧",
            last_bot_message_id="1",
            participants={"a"},
            created_at=clock(),
            updated_at=clock(),
            expires_at=clock() + 600,
        )
        decision = await detector.detect(
            message=msg("g", "2", "a", "看的什么剧？", at=clock()), thread=thread
        )
        assert decision.is_follow_up is True
        assert decision.reason_code in ("direct_follow_up", "topic_continuation")

    async def test_topic_switch_is_not_a_follow_up(self) -> None:
        clock = FakeTime()
        detector = ContinuationDetector(follow_up_threshold=0.75, window_minutes=10, clock=clock)
        thread = ConversationThread(
            thread_id="t",
            group_id="g",
            topic="看剧",
            last_bot_message="我在看剧",
            last_bot_message_id="1",
            participants={"a"},
            created_at=clock(),
            updated_at=clock(),
            expires_at=clock() + 600,
        )
        decision = await detector.detect(
            message=msg("g", "2", "b", "明天几点开会？", at=clock()), thread=thread
        )
        assert decision.is_follow_up is False
        assert decision.reason_code == "unrelated"

    async def test_no_thread_is_unrelated(self) -> None:
        detector = ContinuationDetector(follow_up_threshold=0.75)
        decision = await detector.detect(message=msg("g", "1", "a", "什么剧？"), thread=None)
        assert decision.is_follow_up is False


class TestRelevance:
    async def test_generic_filler_gets_low_contribution(self) -> None:
        evaluator = RelevanceEvaluator()
        scores = await evaluator.evaluate(
            messages=[msg("g", "1", "a", "哈哈哈哈")],
            topic="电视剧",
            interests=[],
            activity_text="",
            focus="",
        )
        assert scores.contribution_value <= 0.3

    async def test_scores_are_bounded(self) -> None:
        evaluator = RelevanceEvaluator()
        scores = await evaluator.evaluate(
            messages=[msg("g", "1", "a", "最近有人玩 Minecraft 吗")],
            topic="Minecraft",
            interests=["Minecraft"],
            activity_text="正在打游戏",
            focus="",
        )
        for value in (
            scores.topic_relevance,
            scores.character_relevance,
            scores.conversation_relevance,
            scores.social_fit,
            scores.contribution_value,
        ):
            assert 0.0 <= value <= 1.0
        assert scores.character_relevance > 0.0


class TestPolicy:
    def _policy(self, clock: FakeTime, **overrides) -> ParticipationPolicy:
        config = SocialConfig(**overrides)
        return ParticipationPolicy(config=config, clock=clock)

    def test_cooldown_blocks_second_reply(self) -> None:
        clock = FakeTime()
        policy = self._policy(clock)
        policy.record_send("g")
        assert (
            policy.block_reason(
                "g",
                group_enabled=True,
                text="你好",
                min_length=1,
                addressed_other=False,
                hard_block=None,
            )
            == "cooldown"
        )

    def test_daily_limit_blocks(self) -> None:
        clock = FakeTime()
        policy = self._policy(clock, participation={"daily_limit": 2, "cooldown_seconds": 0})
        policy.record_send("g")
        clock.advance(200)
        policy.record_send("g")
        clock.advance(200)
        assert (
            policy.block_reason(
                "g",
                group_enabled=True,
                text="你好",
                min_length=1,
                addressed_other=False,
                hard_block=None,
            )
            == "daily_limit"
        )

    def test_disabled_group_blocks(self) -> None:
        clock = FakeTime()
        policy = self._policy(clock)
        assert (
            policy.block_reason(
                "g",
                group_enabled=False,
                text="你好",
                min_length=1,
                addressed_other=False,
                hard_block=None,
            )
            == "group_disabled"
        )

    def test_sleeping_blocks(self) -> None:
        clock = FakeTime()
        policy = self._policy(clock)
        assert (
            policy.block_reason(
                "g",
                group_enabled=True,
                text="你好",
                min_length=1,
                addressed_other=False,
                hard_block="sleeping",
            )
            == "sleeping"
        )


class TestAttention:
    def test_mention_boosts_and_decays(self) -> None:
        clock = FakeTime()
        attention = SocialAttention(clock=clock)
        attention.note_mention("g")
        assert attention.snapshot("g")["attention_level"] > 0.5
        clock.advance(3600)
        assert attention.snapshot("g")["attention_level"] < 0.9

    def test_reply_raises_fatigue(self) -> None:
        clock = FakeTime()
        attention = SocialAttention(clock=clock)
        before = attention.snapshot("g")["fatigue"]
        attention.note_reply("g")
        assert attention.snapshot("g")["fatigue"] > before


class TestCognitionEngine:
    def _engine(self, tmp_path):
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        return bot, bot.social

    async def test_direct_mention_is_high_priority(self, tmp_path) -> None:
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        decision = await social.decide(
            group_id="g1",
            message_id="1",
            user_id="u1",
            nickname="A",
            text="在干嘛",
            mentioned=True,
            reply_to_bot=False,
            group_enabled=False,  # even group disabled
        )
        assert decision.should_reply is True
        assert decision.reason_code == "direct_mention"
        await bot.database.close()

    async def test_direct_mention_not_blocked_by_sleeping(self, tmp_path) -> None:
        """P0：@ 她时，即使时钟窗口判她睡着，决策也必须是 direct_mention。

        The mention check runs before the presence gate, so 'sleeping' must
        never swallow an @-mention (a missing self_id once made every mention
        look like a non-mention, and the sleep gate ate it).
        """
        from app.web.services.behavior import BehaviorService

        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        await BehaviorService(bot).apply_overrides(
            {"schedule": {"sleep_enabled": True, "sleep_start": "00:00", "sleep_end": "23:59"}}
        )
        assert bot.presence.is_sleeping() is True
        decision = await social.decide(
            group_id="g1",
            message_id="1",
            user_id="u1",
            nickname="A",
            text="在干嘛",
            mentioned=True,
            reply_to_bot=False,
            group_enabled=False,
        )
        assert decision.should_reply is True
        assert decision.reason_code == "direct_mention"
        await bot.shutdown()

    async def test_reply_to_bot_is_high_priority(self, tmp_path) -> None:
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        decision = await social.decide(
            group_id="g1",
            message_id="1",
            user_id="u1",
            nickname="A",
            text="好看吗",
            mentioned=False,
            reply_to_bot=True,
            group_enabled=True,
        )
        assert decision.should_reply is True
        assert decision.reason_code == "reply_to_bot"
        await bot.database.close()

    async def test_four_messages_observe_then_fifth_triggers(self, tmp_path) -> None:
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        for index in range(4):
            decision = await social.decide(
                group_id="g2",
                message_id=str(index),
                user_id=f"u{index}",
                nickname="A",
                text=f"普通消息{index}",
                mentioned=False,
                reply_to_bot=False,
                group_enabled=True,
            )
            assert decision.decision in ("observe", "ignore")
        # the 5th message triggers the observer (rule-based fallback → ignore for
        # unrelated filler, but it must not be a bare "observe")
        decision = await social.decide(
            group_id="g2",
            message_id="5",
            user_id="u5",
            nickname="A",
            text="哈哈哈哈",
            mentioned=False,
            reply_to_bot=False,
            group_enabled=True,
        )
        assert decision.decision in ("ignore", "reply", "defer")
        await bot.database.close()

    async def test_follow_up_after_reply_beats_observer(self, tmp_path) -> None:
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        await social.after_reply(
            group_id="g3",
            message_id="bot1",
            content="我在看剧",
            topic="看剧",
            participants=["u1"],
        )
        decision = await social.decide(
            group_id="g3",
            message_id="2",
            user_id="u1",
            nickname="A",
            text="看的什么剧？",
            mentioned=False,
            reply_to_bot=False,
            group_enabled=True,
        )
        assert decision.should_reply is True
        assert decision.reason_code in ("direct_follow_up", "topic_continuation")
        await bot.database.close()

    async def test_no_random_in_decision_path(self, tmp_path) -> None:
        """The same message always yields the same decision (deterministic)."""
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        results = []
        for _ in range(5):
            results.append(
                await social.decide(
                    group_id="g4",
                    message_id="1",
                    user_id="u1",
                    nickname="A",
                    text="在干嘛",
                    mentioned=True,
                    reply_to_bot=False,
                    group_enabled=True,
                )
            )
        assert all(d.should_reply and d.reason_code == "direct_mention" for d in results)
        await bot.database.close()

    async def test_observation_is_recorded(self, tmp_path) -> None:
        bot, social = self._engine(tmp_path)
        await bot.database.connect()
        await social._record_observation(  # noqa: SLF001
            "g5",
            [msg("g5", "1", "a", "你好"), msg("g5", "2", "b", "大家好")],
            ParticipationDecision(
                decision="ignore", reason_code="no_relevance", scores=RelevanceScores()
            ),
            "闲聊",
        )
        row = await bot.database.fetchone("SELECT decision FROM social_observations LIMIT 1")
        assert row is not None and row["decision"] == "ignore"
        await bot.database.close()


class FakeRelevance:
    """Deterministic relevance scores for the observer's rule fallback."""

    def __init__(self, scores):
        self._scores = scores

    async def evaluate(self, *, messages, topic, interests, activity_text, focus):
        return self._scores


class FakeEngine:
    """Captures the observer's request and returns a canned response."""

    def __init__(self, response=None, error=None):
        self.enabled = True
        self._response = response
        self._error = error
        self.last_request = None

    async def chat(self, request):
        self.last_request = request
        if self._error is not None:
            raise self._error
        return self._response


class TestObserver:
    def test_parse_three_state(self) -> None:
        assert GroupObserver._parse('{"decision":"ignore"}') == ("ok", {"decision": "ignore"})
        assert GroupObserver._parse("不是 JSON") == ("parse_failed", None)
        assert GroupObserver._parse('["list", "not", "dict"]') == ("parse_failed", None)
        # tolerant: leading/trailing chatter around the JSON object
        assert GroupObserver._parse('前面废话 {"decision":"reply"} 尾巴') == (
            "ok",
            {"decision": "reply"},
        )

    async def test_model_decision_tags_purpose(self) -> None:
        engine = FakeEngine(
            response=AIResponse(
                content='{"decision":"ignore","reason_code":"no_relevance","confidence":0.0}'
            )
        )
        observer = GroupObserver(config=SocialConfig(), engine=engine)
        decision = await observer._model_decision(
            group_id="g",
            batch=[msg("g", "1", "a", "你好")],
            recent_context=[],
            character_block="",
            topic="",
        )
        assert engine.last_request is not None
        assert engine.last_request.metadata["purpose"] == "social_observer"
        assert decision is not None and decision.decision == "ignore"

    async def test_invalid_json_logs_excerpt(self, caplog) -> None:
        engine = FakeEngine(response=AIResponse(content="抱歉，我现在没法判断，稍后再看"))
        observer = GroupObserver(config=SocialConfig(), engine=engine)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Social"):
            result = await observer._model_decision(
                group_id="g",
                batch=[msg("g", "1", "a", "你好")],
                recent_context=[],
                character_block="",
                topic="",
            )
        assert result is None
        assert any("no valid JSON" in r.message for r in caplog.records)

    async def test_rule_fallback_poor_timing_surface(self) -> None:
        """The offline fallback: reply / defer(poor_timing) / ignore boundaries."""
        batch = [msg("g", "1", "a", "你好")]

        def decide(scores):
            observer = GroupObserver(config=SocialConfig(), relevance=FakeRelevance(scores))
            return observer._rule_decision(batch, "")

        high = await decide(
            RelevanceScores(topic_relevance=0.8, contribution_value=0.8, social_fit=0.8)
        )
        assert high.decision == "reply"

        # mid topic relevance + low contribution/social-fit → defer, not ignore
        mid = await decide(
            RelevanceScores(topic_relevance=0.5, contribution_value=0.1, social_fit=0.1)
        )
        assert mid.decision == "defer" and mid.reason_code == "poor_timing"

        low = await decide(RelevanceScores(topic_relevance=0.2))
        assert low.decision == "ignore" and low.reason_code == "no_relevance"


class TestDeferNeverVetoes:
    """poor_timing defers but never vetoes: the streak must release the credit."""

    async def _setup(self, tmp_path, rate):
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        # _participation_rate reads this live off the bot config
        bot.config.behavior.group.participation_probability = rate
        return bot, bot.social

    @staticmethod
    def _batch(label: str) -> list[GroupMessage]:
        return [msg("g", f"{label}-{i}", f"u{i}", f"普通聊天消息 {i}") for i in range(5)]

    @staticmethod
    def _defer() -> ParticipationDecision:
        return ParticipationDecision(decision="defer", reason_code="poor_timing", confidence=0.5)

    async def test_defer_releases_within_limit(self, tmp_path) -> None:
        bot, social = await self._setup(tmp_path, rate=0.3)
        # max_consecutive_defer defaults to 3: three defers, then released
        for _ in range(3):
            decision = social._resolve_defer("g", self._batch("b"), self._defer())
            assert decision.decision == "defer"
        released = social._resolve_defer("g", self._batch("b"), self._defer())
        assert released.decision == "reply"
        assert released.reason_code == "participation_rate"
        await bot.database.close()

    async def test_defer_rate_zero_stays_silent(self, tmp_path) -> None:
        bot, social = await self._setup(tmp_path, rate=0.0)
        for _ in range(6):
            decision = social._resolve_defer("g", self._batch("b"), self._defer())
            assert decision.decision != "reply"
        await bot.database.close()

    async def test_participation_rate_charged_per_message(self, tmp_path) -> None:
        """额度按消息计费：一批 5 条 = rate×5，单条 = rate，空批 = 0。"""
        bot, social = await self._setup(tmp_path, rate=0.04)
        assert social._participation_rate_amount([str(i) for i in range(5)]) == 0.04 * 5
        assert social._participation_rate_amount(["x"]) == 0.04
        assert social._participation_rate_amount([]) == 0.0
        await bot.database.close()

    async def test_too_short_never_speaks(self, tmp_path) -> None:
        bot, social = await self._setup(tmp_path, rate=1.0)
        decision = await social.decide(
            group_id="g",
            message_id="1",
            user_id="u1",
            nickname="A",
            text="哈",
            mentioned=False,
            reply_to_bot=False,
            group_enabled=True,
        )
        assert decision.decision == "ignore" and decision.reason_code == "too_short"
        await bot.database.close()
