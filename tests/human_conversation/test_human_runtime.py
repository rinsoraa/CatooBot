"""Human Conversation Runtime regression tests (v1.2 §141-§163).

The hard acceptance behaviours: bursts become one turn, corrections stale the
in-flight reply, silence is structured, continuity persists and decays. These
tests use the real runtime with injected callbacks — no QQ, no model.
"""

from __future__ import annotations

import asyncio

import pytest

from app.config.settings import ContinuityConfig, ConversationConfig, DatabaseConfig
from app.continuity.manager import ContinuityManager
from app.continuity.store import ContinuityStore
from app.conversation.classifier import classify
from app.conversation.decision import ConversationDecisionEngine
from app.conversation.models import (
    ConversationTurn,
    TurnClassification,
    TurnMessage,
)
from app.conversation.runtime import ConversationTurnRuntime
from app.database.database import Database


def make_config() -> ConversationConfig:
    return ConversationConfig(debounce={"direct_message_ms": 20, "group_message_ms": 30})


class Harness:
    """A runtime wired to recording callbacks."""

    def __init__(self) -> None:
        self.config = make_config()
        self.decision_engine = ConversationDecisionEngine(self.config)
        self.plans: list[object] = []
        self.sent: list[str] = []
        self.social_calls: list[ConversationTurn] = []
        self.silences: list[str] = []
        self.respond_gate: dict[str, bool] = {"open": True}
        self.runtime = ConversationTurnRuntime(
            self.config,
            self.decision_engine,
            respond_callback=self._respond,
            deliver_callback=self._deliver,
            social_provider=self._social,
        )

    async def _respond(self, turn, decision, generation):  # type: ignore[no-untyped-def]
        # 模拟一次需要几个循环才能完成的生成
        for _ in range(3):
            await asyncio.sleep(0.01)
        if not self.respond_gate["open"] or not generation.is_current():
            return None
        plan = type("Plan", (), {"sequence_steps": lambda self=None: []})()
        self.plans.append(plan)
        return plan

    async def _deliver(self, plan, turn, is_current):  # type: ignore[no-untyped-def]
        self.sent.append(turn.text)
        return [turn.text]

    async def _social(self, turn):  # type: ignore[no-untyped-def]
        self.social_calls.append(turn)
        return None

    async def submit(self, session_id: str, text: str, **kwargs) -> None:
        await self.runtime.submit({"session_id": session_id, "text": text, **kwargs})

    async def wait(self) -> None:
        await self.runtime.wait_idle()


@pytest.mark.asyncio
async def test_burst_becomes_one_turn_and_one_reply():
    """§142: 你干嘛呢 / 在吗 / 我突然想到一件事 → one turn, one reply."""
    harness = Harness()
    for text in ("你干嘛呢", "在吗", "我突然想到一件事"):
        await harness.submit("private:1", text, user_id="1")
    await harness.wait()
    assert len(harness.sent) == 1
    # the folded user side contains all three lines
    assert "你干嘛呢" in harness.sent[0] and "在吗" in harness.sent[0]


@pytest.mark.asyncio
async def test_follow_up_after_reply_is_separate_turn():
    """§143: 你在看什么 → reply → 哪一部: the追问 is its own follow-up turn."""
    harness = Harness()
    await harness.submit("private:2", "你在看什么", user_id="2")
    await harness.wait()
    await harness.submit("private:2", "哪一部", user_id="2")
    await harness.wait()
    assert len(harness.sent) == 2
    turns = harness.runtime._sessions["private:2"]
    assert turns.last_turn_text == "哪一部"


@pytest.mark.asyncio
async def test_correction_makes_in_flight_reply_stale():
    """§144/§145: correction while generating → old reply never sent."""
    harness = Harness()

    slow_started = asyncio.Event()

    async def slow_respond(turn, decision, generation):  # type: ignore[no-untyped-def]
        slow_started.set()
        await asyncio.sleep(0.4)  # in-flight when the correction lands
        if not generation.is_current():
            return None
        return object()

    harness.runtime._respond_callback = slow_respond
    await harness.submit("private:3", "你说的是A？", user_id="3")
    await slow_started.wait()
    await harness.submit("private:3", "不对，我说的是B", user_id="3")
    await harness.wait()
    # the first reply never reached QQ
    assert all("你说的是A？" not in text for text in harness.sent)
    sessions = harness.runtime._sessions["private:3"]
    assert sessions.last_turn_text == "不对，我说的是B"


@pytest.mark.asyncio
async def test_low_information_message_gets_short_shape():
    """§146: 哈哈 → the decision allows a mirror-register reply."""
    engine = ConversationDecisionEngine(make_config())
    turn = ConversationTurn(
        turn_id="t",
        session_id="s",
        user_id="1",
        messages=[TurnMessage(user_id="1", text="哈哈")],
    )
    decision = await engine.decide(turn)
    assert decision.respond
    assert decision.message_shape == "short"
    assert decision.response_style == "brief"
    assert decision.expression_opportunity


@pytest.mark.asyncio
async def test_consecutive_question_guard():
    """§147: after two bot questions the third turn gets should_ask=False."""
    engine = ConversationDecisionEngine(make_config())
    turn = ConversationTurn(
        turn_id="t",
        session_id="s",
        user_id="1",
        messages=[TurnMessage(user_id="1", text="然后呢？")],
    )
    ok = await engine.decide(turn, consecutive_questions=1)
    assert ok.should_ask
    denied = await engine.decide(turn, consecutive_questions=2)
    assert not denied.should_ask


@pytest.mark.asyncio
async def test_silence_has_structured_reason():
    """§80/§81: hard blocks produce a named silence reason, never a dice."""
    engine = ConversationDecisionEngine(make_config())
    turn = ConversationTurn(
        turn_id="t",
        session_id="s",
        user_id="1",
        messages=[TurnMessage(user_id="1", text="在吗")],
    )
    decision = await engine.decide(turn, hard_block="sleeping")
    assert not decision.respond
    assert decision.silence_reason == "sleeping"
    social_turn = ConversationTurn(
        turn_id="t2",
        session_id="s",
        user_id="1",
        messages=[TurnMessage(user_id="1", text="大家好")],
    )

    class Declined:
        should_reply = False
        reason_code = "daily_limit"

    decision = await engine.decide(social_turn, social_decision=Declined())
    assert not decision.respond
    assert decision.silence_reason == "daily_limit"


def test_classifier_cues():
    def turn_with(text: str) -> ConversationTurn:
        return ConversationTurn(
            turn_id="t",
            session_id="s",
            user_id="1",
            messages=[TurnMessage(user_id="1", text=text)],
        )

    assert classify(turn_with("不对，我说的是另一个")) is TurnClassification.correction
    assert classify(turn_with("等等，先别说这个")) is TurnClassification.interruption
    assert classify(turn_with("对了还有个事")) is TurnClassification.topic_shift
    assert classify(turn_with("晚安")) is TurnClassification.closing
    assert classify(turn_with("今天天气不错")) is TurnClassification.single


# ---------------------------------------------------------------- continuity


@pytest.mark.asyncio
async def test_open_loop_created_and_recalled():
    """§150: mentioning unfinished things creates an open loop she can recall."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp}/t.db"))
        await db.connect()
        store = ContinuityStore(db, ContinuityConfig())
        manager = ContinuityManager(store)
        await manager.observe_message("777", "我的木屋还没建完，下次继续", session_id="private:777")
        loops = await manager.open_loop_context("777")
        assert any("木屋" in loop.summary for loop in loops)
        await db.close()


@pytest.mark.asyncio
async def test_shared_experience_recall_is_relevance_gated():
    """§149: shared history returns only for related topics."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp}/t.db"))
        await db.connect()
        store = ContinuityStore(db, ContinuityConfig())
        manager = ContinuityManager(store)
        await manager.observe_message(
            "777", "我们一起折腾那个Minecraft服务器问题好久了", session_id="private:777"
        )
        # a second overlapping mention confirms the candidate (confidence ≥ 0.6)
        await manager.observe_message(
            "777", "我们又一起折腾Minecraft服务器问题了", session_id="private:777"
        )
        related = await manager.recall_shared("777", "Minecraft服务器又出问题了")
        assert related, "topic-related shared experience should be recalled"
        unrelated = await manager.recall_shared("777", "晚饭吃什么好")
        assert not any(exp in unrelated for exp in related) or not unrelated
        await db.close()


@pytest.mark.asyncio
async def test_state_decays_by_ttl():
    """§161: affect drains, profiles fade — nothing is permanent."""
    import time as time_mod

    from app.continuity.models import AffectiveContext, InteractionProfile

    now = time_mod.time()
    affect = AffectiveContext(dimensions={"amusement": 0.9}, updated_at=now - 3 * 3600)
    affect.decay(now, half_life_minutes=45)
    assert affect.level("amusement") < 0.1

    profile = InteractionProfile(user_id="1")
    profile.observe("burst_length", 1.0, now=now - 30 * 86400)
    profile.decay(now)
    assert profile.patterns["burst_length"].confidence < 0.2


@pytest.mark.asyncio
async def test_continuity_survives_restart():
    """§163: open loops persist across a restart (new manager, same DB)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp}/t.db"))
        await db.connect()
        first = ContinuityManager(ContinuityStore(db, ContinuityConfig()))
        await first.observe_message(
            "777",
            "那个动画还没看完，想试某个Mod",
            session_id="private:777",
        )
        second = ContinuityManager(ContinuityStore(db, ContinuityConfig()))
        await second.start()
        loops = await second.open_loop_context("777")
        assert any("动画" in loop.summary or "Mod" in loop.summary for loop in loops)
        await db.close()
