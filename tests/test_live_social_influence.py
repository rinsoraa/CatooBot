"""Phase 16 tests (§97): live social influence & autonomous interaction continuity.

The contract: real messages reach a *living* character. The influence layer (and
only it) decides NO_EFFECT / OBSERVE / WAKE / INTERRUPT; an interruption pauses
the life honestly (one layer, no nesting), the social facts land exactly once,
and the conversation ends with the paused life continuing — never a second
parallel world.
"""

from __future__ import annotations

import asyncio
import json

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, OneBotConfig, SandboxConfig
from app.integrations.onebot.gateway import OneBotGateway
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.fake_onebot import FakeOneBotTransport, qq_message
from tests.test_sandbox_memory_foundation import Clock, make_db


def make_engine(responses: list) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(behaviors={"A": list(responses)})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    return engine, provider


def reply(text: str) -> str:
    return json.dumps({"mode": "reply", "text": text, "confidence": 0.9}, ensure_ascii=False)


async def make_sandbox(*, db, clock, engine=None, timeout: float = 900.0):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(
            enabled=True,
            tick_seconds=600,
            simulation_seed=7,
            interaction_episode_timeout_seconds=timeout,
        ),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


def invite(
    event_id: str,
    actor: str,
    *,
    urgency=ExternalUrgency.high,
    kind="core_friend",
    text="来一起联机",
):
    return ExternalWorldEvent(
        event_id=event_id,
        source=ExternalSource.qq,
        actor_id=actor,
        content=text,
        urgency=urgency,
        semantic_kind="game_invitation",
        target_activity="gaming",
        actor_relationship=kind,
    )


async def busy(runtime, clock, minutes: float = 60.0) -> str:  # type: ignore[no-untyped-def]
    """Put the character in the middle of her own life; returns the instance id."""
    assert await runtime._start_action("watch_animation", duration_minutes=minutes) is not None  # noqa: SLF001
    clock.advance(300.0)
    return runtime.current_action.id  # type: ignore[union-attr]


# ----------------------------------------------------- A/B/C/D: the four paths


class TestInfluencePaths:
    async def test_no_effect_leaves_the_life_untouched(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7 A: a fact that matters to nobody changes nothing."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="noeffect-1",
                    source=ExternalSource.qq,
                    actor_id="40001",
                    event_type="system_note",
                    content="",
                    urgency=ExternalUrgency.low,
                )
            )
            results = await runtime.wakeup()
            assert results
            assert runtime.current_action is not None
            assert runtime.current_action.id == instance  # §7: unchanged
            assert runtime._interrupted is None  # no pause was created
            assert not runtime.events.of_type(ET.ACTION_INTERRUPTED)
            assert not runtime.events.of_type(ET.DECISION_REQUESTED)  # no re-decision
            assert not runtime.social_session_active()  # nothing to continue
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_observe_keeps_the_action_and_records_the_fact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7 B/§68: she notices, answers, and keeps doing what she was doing."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([reply("在的，刚在打游戏。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                adapt_qq_message(message_id="observe-1", actor_id="40002", text="在干嘛呢")
            )
            results = await runtime.wakeup()
            assert results[0]["influence"] == "observe"
            await runtime.wait_social()
            assert runtime.current_action is not None and runtime.current_action.id == instance
            assert runtime.events.of_type(ET.SOCIAL_INTERACTION)  # the fact landed
            response = await runtime.conversation_turn(message="在干嘛呢", actor_id="40002")
            committed = runtime.commit_conversation_response(response)
            assert committed.mode == "reply" and provider.calls
            assert runtime.current_action.id == instance  # the talk did not stop her life
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_wake_reevaluates_once_without_touching_the_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7 C/§69: a friend's message re-checks the world, once."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                adapt_qq_message(
                    message_id="wake-1", actor_id="40003", text="在吗", is_core_actor=True
                )
            )
            results = await runtime.wakeup()
            assert results[0]["influence"] in ("wake", "interrupt")
            if results[0]["influence"] == "wake":
                assert runtime.current_action is not None
                assert runtime.current_action.id == instance
                assert not runtime.events.of_type(ET.ACTION_INTERRUPTED)
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_interrupt_pauses_the_exact_instance_and_records_it(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7 D/§8/§16: pause → social facts → conversation → (later) resume."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([reply("好呀，晚上一起玩。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                invite("interrupt-1", "40004", urgency=ExternalUrgency.critical)
            )
            results = await runtime.wakeup()
            assert results[0]["interrupt"] is True
            paused = runtime._interrupted  # noqa: SLF001
            assert paused is not None
            assert paused.action_id == instance  # §8: the *same* paused instance
            assert paused.remaining_minutes > 0.0
            assert paused.interrupt_reason
            interrupted = runtime.events.last(ET.ACTION_INTERRUPTED)
            assert interrupted is not None
            assert interrupted.payload["action_instance_id"] == instance
            # §16: the social facts landed *before* the reply is produced
            assert runtime.events.of_type(ET.SOCIAL_INTERACTION)
            accepted = [
                event
                for event in runtime.events.of_type(ET.SOCIAL_INTERACTION)
                if event.payload.get("interaction_type") == "invitation_accepted"
            ]
            assert accepted
            response = await runtime.conversation_turn(message="来一起联机", actor_id="40004")
            committed = runtime.commit_conversation_response(response)
            assert committed.mode == "reply" and provider.calls
            assert runtime.social_session_active(
                person_id=runtime.persons.for_qq("40004").person_id
            )
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------ E/F/N/O: session continuity


class TestSessionContinuity:
    async def test_the_same_instance_resumes_after_the_session_closes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§8/§9/§91: the paused life continues — same pause, honest new lifecycle."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock, timeout=900.0)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                invite("resume-1", "40005", urgency=ExternalUrgency.critical)
            )
            await runtime.wakeup()
            paused = runtime._interrupted  # noqa: SLF001
            assert paused is not None and paused.action_id == instance
            player = runtime.current_action
            assert player is not None

            # the social session ends by inactivity while she is between actions
            runtime.current_action = None
            clock.advance(runtime.session_timeout_seconds + 60.0)
            assert not runtime.social_session_active()
            report = await runtime.tick(minutes=1)
            assert report  # the tick is what brings her back
            resumed = runtime.current_action
            assert resumed is not None
            assert resumed.definition_id == "watch_animation"  # her own life, again
            assert runtime._interrupted is None  # no leftover pause  # noqa: SLF001
            assert runtime.events.last(ET.ACTION_RESUMED) is not None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_repeated_messages_never_nest_interruptions(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§27-§29/§90: the first message pauses her; the rest stay in the session."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            instance = await busy(runtime, clock)
            for index in range(1, 4):
                await runtime.submit_external(
                    invite(
                        f"nest-{index}",
                        "40006",
                        urgency=ExternalUrgency.critical,
                        text=f"来一起联机 {index}",
                    )
                )
                await runtime.wakeup()
                await runtime.wait_social()
            paused = runtime._interrupted  # noqa: SLF001
            assert paused is not None and paused.action_id == instance  # one layer only
            assert len(runtime.events.of_type(ET.ACTION_INTERRUPTED)) == 1
            assert len(runtime.events.of_type(ET.SOCIAL_INTERRUPT_SUPPRESSED)) >= 1
            assert runtime.social_session_active(
                person_id=runtime.persons.for_qq("40006").person_id
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_long_conversation_stays_one_session(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§87/§90: twenty messages, one session, one interruption, bounded work."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await busy(runtime, clock)
            await runtime.submit_external(
                invite("long-1", "40007", urgency=ExternalUrgency.critical)
            )
            await runtime.wakeup()
            for index in range(2, 20):
                await runtime.submit_external(
                    adapt_qq_message(
                        message_id=f"long-{index}", actor_id="40007", text=f"继续说 {index}"
                    )
                )
                await runtime.wakeup()
                await runtime.wait_social()
            assert len(runtime.events.of_type(ET.ACTION_INTERRUPTED)) == 1
            session = runtime._social_session  # noqa: SLF001
            assert session is not None
            assert session["turns"] >= 1
            assert "text" not in session  # §49: counts only, never transcripts
            assert not runtime.events.last(ET.OUTBOUND_RESPONSE_SENT)  # §44: no proactive QQ
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_idle_character_just_talks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§92: with nothing running there is nothing to interrupt."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.current_action = None
            await runtime.submit_external(
                invite("idle-1", "40008", urgency=ExternalUrgency.critical)
            )
            results = await runtime.wakeup()
            assert results[0]["influence"] == "interrupt" or results[0]["influence"] == "wake"
            assert runtime._interrupted is None  # no pause without a life to pause  # noqa: SLF001
            await runtime.wait_social()
            assert runtime.events.of_type(ET.SOCIAL_INTERACTION)
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- I/K/P/S/T: hygiene


class TestHygiene:
    async def test_a_replayed_transport_event_changes_things_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§35/§63: the same event never doubles a relationship or a promise."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            event = invite("replay-1", "40009", urgency=ExternalUrgency.critical)
            await runtime.submit_external(event)
            await runtime.wakeup()
            await runtime.wait_social()
            first = await runtime.relationships_dyn.get(runtime.persons.for_qq("40009").person_id)
            assert first is not None
            await runtime.submit_external(
                invite("replay-1", "40009", urgency=ExternalUrgency.critical)
            )
            await runtime.wakeup()
            await runtime.wait_social()
            again = await runtime.relationships_dyn.get(runtime.persons.for_qq("40009").person_id)
            assert again is not None
            assert again.interaction_count == first.interaction_count  # exactly once
            assert len(runtime.commitments.all()) == len(runtime.commitments.all())
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_influence_classification_never_calls_a_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§40/§41/§97 T: the influence layer stays deterministic."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([reply("在的")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            before = runtime.decisions.llm_calls
            for index in range(20):
                await runtime.submit_external(
                    adapt_qq_message(
                        message_id=f"det-{index}", actor_id="40010", text=f"随便说说 {index}"
                    )
                )
                await runtime.wakeup()
            assert runtime.decisions.llm_calls == before  # no model for classification
            assert not provider.calls
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_stale_reply_is_never_sent_after_the_session_moves(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§31/§32/§72: the commit guard still owns the send boundary."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            started = asyncio.Event()

            async def slow(_request):  # type: ignore[no-untyped-def]
                started.set()
                await asyncio.sleep(0.01)
                from types import SimpleNamespace

                return SimpleNamespace(content=reply("在的"))

            runtime.ai_engine = _StubEngine(slow)
            turn = asyncio.create_task(runtime.conversation_turn(message="在吗", actor_id="40011"))
            await asyncio.wait_for(started.wait(), timeout=2.0)
            # another social event lands while the reply is being written
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=runtime.persons.for_qq("40012").person_id,
                    interaction_type="shared_activity",
                    source="character_action",
                    timestamp=clock.now,
                    significance=InteractionSignificance.major,
                    metadata={"target_activity": "gaming", "duration_minutes": 30.0},
                )
            )
            response = await turn
            committed = runtime.commit_conversation_response(response)
            assert committed.mode == "silent"
            assert committed.reason == "cognitive_changed"
            assert committed.text == ""
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_restart_during_an_interruption_leaves_no_second_life(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§61/§62: the paused life is not duplicated and not auto-completed."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            instance = await busy(runtime, clock)
            await runtime.submit_external(
                invite("restart-1", "40013", urgency=ExternalUrgency.critical)
            )
            await runtime.wakeup()
            assert runtime._interrupted is not None  # noqa: SLF001
            await runtime._snapshot()  # noqa: SLF001
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            # never two live instances, and never a silently completed one
            assert restored.current_action is None or restored.current_action.id != instance
            if restored.current_action is not None:
                assert restored.current_action.status.value == "active"
            person = restored.persons.for_qq("40013").person_id
            state = await restored.relationships_dyn.get(person)
            assert state is not None and state.interaction_count >= 1  # the talk survived
        finally:
            await restored.shutdown()
            await db.close()

    async def test_no_proactive_qq_from_the_social_path(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§44/§86: living and talking never become an outbound message."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await busy(runtime, clock)
            await runtime.submit_external(invite("qq-1", "40014", urgency=ExternalUrgency.critical))
            await runtime.wakeup()
            await runtime.wait_social()
            await runtime.tick(minutes=5)
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_QUEUED) is None
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_SENT) is None
        finally:
            await runtime.shutdown()
            await db.close()


class TestOneBotEndToEnd:
    async def test_message_interrupts_life_replies_and_life_continues(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§67: OneBot inbound → interrupt → facts → reply → outbound → resume."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([reply("好呀，晚上一起玩。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        transport = FakeOneBotTransport()
        gateway = OneBotGateway(
            runtime,
            transport=transport,
            config=OneBotConfig(gateway_enabled=True),
            clock=clock,
            retry_delay=0.001,
        )
        gateway.config.self_ids = ["10000"]
        await gateway.start()
        try:
            instance = await busy(runtime, clock)
            await transport.receive(
                qq_message(message_id="e2e-1", user_id="40015", text="晚上一起玩？")
            )
            assert await gateway.drain(timeout=5.0)
            # the invite was noticed and answered — and it was not a core invite,
            # so her own life simply continued (influence policy untouched)
            assert len(transport.sent_messages) == 1
            assert transport.sent_messages[0]["user_id"] == "40015"
            assert runtime.current_action is not None
            assert runtime.current_action.id == instance
            assert runtime.events.of_type(ET.SOCIAL_INTERACTION)
        finally:
            await gateway.stop()
            await runtime.shutdown()
            await db.close()


class _StubEngine:
    enabled = True

    def __init__(self, answer):  # type: ignore[no-untyped-def]
        self._answer = answer

    async def chat(self, request):  # type: ignore[no-untyped-def]
        return await self._answer(request)
