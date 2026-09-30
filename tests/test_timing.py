"""Reply timing + message chunking + response planner tests (spec §71)."""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.behavior.presence import PresenceResolver
from app.character.relationship import Relationship
from app.character.state import CharacterState
from app.config.settings import (
    BehaviorChunkingConfig,
    BehaviorReplyTimingConfig,
    BehaviorScheduleConfig,
)
from app.response.planner import CharacterResponsePlanner
from app.response.splitter import MessageChunker
from app.response.timing import ReplyTiming

NL = chr(10)
PARA = NL + NL
TZ = ZoneInfo("Asia/Singapore")


def make_timing(**overrides) -> ReplyTiming:
    cfg = BehaviorReplyTimingConfig(**overrides)
    presence = PresenceResolver("Asia/Singapore", BehaviorScheduleConfig())
    return ReplyTiming(cfg, presence, rng=random.Random(7))


def state(**overrides) -> CharacterState:
    base = {"mood": "neutral", "energy": 0.8, "activity": "idle"}
    base.update(overrides)
    return CharacterState.model_validate(base)


class TestTiming:
    def test_delay_inside_configured_band(self) -> None:
        timing = make_timing(min_delay=1.0, max_delay=5.0)
        for length in (2, 20, 60, 200):
            delay = timing.compute(reply_text="字" * length, state=state())
            assert 1.0 <= delay <= 5.0

    def test_longer_reply_waits_longer(self) -> None:
        timing = make_timing(jitter=0.0, min_delay=0.0)
        short = timing.compute(reply_text="在", state=state())
        long = timing.compute(reply_text="字" * 150, state=state())
        assert long > short

    def test_busy_activity_slows_down(self) -> None:
        timing = make_timing(jitter=0.0, min_delay=0.0)
        idle = timing.compute(reply_text="好呀", state=state(activity="idle"))
        busy = timing.compute(reply_text="好呀", state=state(activity="gaming"))
        assert busy > idle

    def test_sleeping_slows_down(self) -> None:
        timing = make_timing(jitter=0.0)
        presence = PresenceResolver("Asia/Singapore", BehaviorScheduleConfig())
        awake_ctx = presence.time_context(datetime(2026, 9, 29, 15, 0, tzinfo=TZ))
        asleep_ctx = presence.time_context(datetime(2026, 9, 29, 3, 0, tzinfo=TZ))
        awake = timing.compute(reply_text="好呀", state=state(), time_context=awake_ctx)
        asleep = timing.compute(reply_text="好呀", state=state(), time_context=asleep_ctx)
        assert asleep > awake

    def test_fast_exchange_is_faster(self) -> None:
        # A long enough reply keeps both values above the min_delay floor so the
        # rhythm factor is observable.
        timing = make_timing(jitter=0.0, min_delay=0.0)
        slow = timing.compute(
            reply_text="字" * 80, state=state(), seconds_since_last_exchange=3600
        )
        fast = timing.compute(
            reply_text="字" * 80, state=state(), seconds_since_last_exchange=5
        )
        assert fast < slow

    def test_close_relationship_is_faster(self) -> None:
        # A pinned afternoon context keeps the comparison independent of the
        # wall clock (at night the sleep factor pushes both to the cap).
        timing = make_timing(jitter=0.0, min_delay=0.0, max_delay=60.0)
        day = PresenceResolver("Asia/Singapore", BehaviorScheduleConfig()).time_context(
            datetime(2026, 9, 29, 15, 0, tzinfo=TZ)
        )
        unfamiliar = timing.compute(
            reply_text="字" * 80,
            state=state(),
            relationship=Relationship(user_id="1", stage="new"),
            time_context=day,
        )
        close = timing.compute(
            reply_text="字" * 80,
            state=state(),
            relationship=Relationship(user_id="1", stage="close"),
            time_context=day,
        )
        assert close < unfamiliar

    def test_disabled_means_no_delay(self) -> None:
        timing = make_timing(enabled=False)
        assert timing.compute(reply_text="很长的回复" * 20, state=state()) == 0.0

    def test_delays_vary_between_calls(self) -> None:
        """Not a fixed number — the spec forbids 'always 2 seconds'."""
        timing = make_timing(min_delay=0.5, max_delay=6.0, jitter=0.3)
        delays = {round(timing.compute(reply_text="你好呀", state=state()), 2) for _ in range(12)}
        assert len(delays) > 1


class TestChunker:
    def test_single_short_reply_stays_one_message(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig(chunk_probability=1.0))
        assert chunker.plan("在的呀") == ["在的呀"]

    def test_paragraphs_split_when_allowed(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig(paragraph_always_split=True))
        text = f"今天挖矿挖了好久。{PARA}手都酸了想歇一会儿。"
        assert chunker.plan(text) == ["今天挖矿挖了好久。", "手都酸了想歇一会儿。"]

    def test_tiny_fragments_are_merged(self) -> None:
        """Bubbles below min_chunk_length join their neighbour (no '一。' spam)."""
        chunker = MessageChunker(BehaviorChunkingConfig(paragraph_always_split=True))
        assert chunker.plan(f"一。{PARA}二。") == [f"一。{NL}二。"]

    def test_zero_probability_keeps_single_message(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig(chunk_probability=0.0))
        text = "这句话挺长的。还有第二句。甚至还有第三句。"
        assert len(chunker.plan(text)) == 1

    def test_probability_one_splits_sentences(self) -> None:
        chunker = MessageChunker(
            BehaviorChunkingConfig(chunk_probability=1.0, max_chunks=3), rng=random.Random(1)
        )
        text = "第一句话在这里。第二句话也在这里。第三句话还是在。"
        chunks = chunker.plan(text)
        assert len(chunks) >= 2
        assert all(chunk.strip() for chunk in chunks)
        assert "".join(chunks).replace(NL, "") == text.replace(NL, "")

    def test_max_chunks_respected(self) -> None:
        chunker = MessageChunker(
            BehaviorChunkingConfig(chunk_probability=1.0, max_chunks=2), rng=random.Random(3)
        )
        assert len(chunker.plan("甲甲甲甲。乙乙乙乙。丙丙丙丙。丁丁丁丁。戊戊戊戊。")) <= 2

    def test_action_then_report_always_splits(self) -> None:
        """「啊？？等等我看看时间……哦对确实是晚上八点多😳」 is two bubbles (v1.1)."""
        chunker = MessageChunker(BehaviorChunkingConfig(chunk_probability=0.0))
        chunks = chunker.plan("啊？？等等我看看时间……哦对确实是晚上八点多😳")
        assert chunks == ["啊？？等等我看看时间……", "哦对确实是晚上八点多😳"]

    def test_plain_hesitation_does_not_split(self) -> None:
        """Ellipsis without a self-action cue stays one bubble."""
        chunker = MessageChunker(BehaviorChunkingConfig(chunk_probability=0.0))
        assert chunker.plan("好像也行……算了吧") == ["好像也行……算了吧"]

    def test_action_split_respects_force_single(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig(chunk_probability=0.0))
        text = "让我看看……好了"
        assert chunker.plan(text, force_single=True) == [text]

    def test_disabled_chunker_returns_single(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig(enabled=False, chunk_probability=1.0))
        assert len(chunker.plan("甲甲甲甲。乙乙乙乙。丙丙丙丙。")) == 1

    def test_very_long_text_falls_back_to_hard_split(self) -> None:
        chunker = MessageChunker(BehaviorChunkingConfig())
        chunks = chunker.plan("长" * 5000)
        assert len(chunks) >= 3
        assert all(len(chunk) <= 2000 for chunk in chunks)

    def test_empty_text_yields_no_chunks(self) -> None:
        assert MessageChunker(BehaviorChunkingConfig()).plan("   ") == []


class TestPlanner:
    def test_plan_contains_delay_and_chunks(self) -> None:
        timing = make_timing(min_delay=1.0, max_delay=4.0)
        planner = CharacterResponsePlanner(
            timing, BehaviorChunkingConfig(paragraph_always_split=True)
        )
        text = f"今天挖矿挖了好久。{PARA}手都酸了想歇一会儿。"
        plan = planner.plan_reply(text, state=state())
        assert plan.chunks == ["今天挖矿挖了好久。", "手都酸了想歇一会儿。"]
        assert 1.0 <= plan.delay <= 4.0
        assert len(plan.inter_chunk_delays) == 1

    def test_inter_chunk_delays_within_band(self) -> None:
        timing = make_timing()
        cfg = BehaviorChunkingConfig(
            paragraph_always_split=True, inter_chunk_delay_min=0.5, inter_chunk_delay_max=1.5
        )
        planner = CharacterResponsePlanner(timing, cfg)
        text = f"第一段话写在这里了。{PARA}第二段话也写在这里。{PARA}第三段话同样在这里。"
        plan = planner.plan_reply(text, state=state())
        assert len(plan.chunks) == 3
        assert all(0.5 <= gap <= 1.5 for gap in plan.inter_chunk_delays)

    def test_single_chunk_has_no_inter_delays(self) -> None:
        planner = CharacterResponsePlanner(make_timing(), BehaviorChunkingConfig())
        assert planner.plan_reply("就一句话", state=state()).inter_chunk_delays == []

    def test_force_single_message(self) -> None:
        planner = CharacterResponsePlanner(
            make_timing(), BehaviorChunkingConfig(paragraph_always_split=True)
        )
        text = f"今天状态不错。{PARA}等会儿继续打游戏。"
        plan = planner.plan_reply(text, state=state(), force_single_message=True)
        assert len(plan.chunks) == 1

    def test_empty_reply_yields_empty_plan(self) -> None:
        planner = CharacterResponsePlanner(make_timing(), BehaviorChunkingConfig())
        assert planner.plan_reply("", state=state()).is_empty


@pytest.mark.parametrize("seed", [1, 2, 3])
def test_planner_runs_per_seed(seed: int) -> None:
    timing = ReplyTiming(
        BehaviorReplyTimingConfig(),
        PresenceResolver("Asia/Singapore", BehaviorScheduleConfig()),
        rng=random.Random(seed),
    )
    planner = CharacterResponsePlanner(
        timing, BehaviorChunkingConfig(chunk_probability=1.0), rng=random.Random(seed)
    )
    plan = planner.plan_reply("今天不错。等会儿打游戏。晚点再聊。", state=state())
    assert plan.chunks
    assert plan.delay >= 0
