"""Phase 6 tests (§27): the cognitive decision & intent layer.

Twelve checks around one contract: **the LLM may propose; only the Sandbox
decides.** Candidates come from the world, proposals are strictly validated
against live state, failures fall back deterministically, and no LLM is ever
called per tick or per chat message.
"""

from __future__ import annotations

from pathlib import Path

from app.ai.engine import AIEngine
from app.ai.errors import AITimeoutError
from app.config.settings import AIConfig, DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.intent import (
    CandidateKind,
    DecisionCandidate,
    DecisionTrigger,
)
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


def make_engine(responses: list) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(behaviors={"A": list(responses)})
    engine = AIEngine(
        AIConfig(
            enabled=True,
            models=[{"name": "A", "provider": "mock", "model": "A"}],
        ),
        providers={"mock": provider},
    )
    return engine, provider


async def make_sandbox(*, db, clock, bible_path=None, engine=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


def json_proposal(candidate: str, confidence: float = 0.9) -> str:
    return f'{{"candidate_id": "{candidate}", "confidence": {confidence}}}'


def invitation(*, core: bool = True, mid: str = "inv-1"):  # type: ignore[no-untyped-def]
    return adapt_qq_message(
        message_id=mid,
        actor_id="u-core" if core else "u-guest",
        text="来一起联机",
        is_core_actor=core,
    )


async def run_invitation(runtime, *, core: bool = True, mid: str = "inv-1") -> dict:  # type: ignore[no-untyped-def]
    await runtime.submit_external(invitation(core=core, mid=mid))
    results = await runtime.wakeup()
    return results[0]


# ---------------------------------------------------------------- Test 1/10


class TestDeterministicPath:
    async def test_single_candidate_never_calls_the_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:whatever")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            feed = DecisionCandidate(
                candidate_id="action:feed_cat",
                kind=CandidateKind.action,
                action_id="feed_cat",
                label="喂宠物",
            )
            outcome = await runtime.decisions.decide(
                trigger=DecisionTrigger.pet_interaction, candidates=[feed]
            )
            assert outcome.accepted and outcome.source == "deterministic"
            assert outcome.candidate_id == "action:feed_cat"
            assert runtime.decisions.llm_calls == 0
            assert provider.calls == []
            assert runtime.events.last(ET.DECISION_ACCEPTED) is not None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_real_pet_chain_uses_no_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:play_minecraft")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            from app.sandbox.interactions import InteractionRequest

            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)
            await runtime.interactions.execute(
                InteractionRequest(
                    target_id=runtime.pet.id,  # type: ignore[union-attr]
                    target_kind="pet",
                    interaction_type="feed",
                )
            )
            await runtime.flush_experiences()
            assert runtime.decisions.llm_calls == 0
            assert provider.calls == []
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ticks_alone_never_call_the_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:play_minecraft")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            for _ in range(4):
                clock.advance(600)
                await runtime.tick(minutes=10)
            assert provider.calls == []
            assert runtime.decisions.llm_calls == 0
            # goal steps may be *requested* deterministically (§7/§28), but the
            # model is never consulted: no proposal event exists
            assert runtime.events.last(ET.DECISION_PROPOSED) is None
            assert all(
                event.payload.get("source") == "deterministic"
                for event in runtime.events.of_type(ET.DECISION_ACCEPTED)
            )
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 2/7


class TestLlmDecisionPath:
    async def test_model_pick_is_validated_and_executed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:play_minecraft", 0.9)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(10 * 60)
            result = await run_invitation(runtime)

            assert result["interrupt"] is True
            assert result["action"] == "play_minecraft"
            assert runtime.current_action.definition_id == "play_minecraft"
            decision = result["decision"]
            assert decision["accepted"] and decision["source"] == "llm"
            assert runtime.decisions.llm_calls == 1
            # 推理预算护栏（2026-10-05 复盘：300 会被推理打满 → 空 JSON → 静默退化）
            assert provider.calls[0]["max_tokens"] == 800
            # the model only saw a candidate list
            prompt = provider.calls[0]["last_user"]
            assert "action:play_minecraft" in prompt and "continue_current_action" in prompt
            assert "只能" in prompt
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_decision_events_chain_with_causation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:play_minecraft")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await run_invitation(runtime)

            received = runtime.events.last(ET.EXTERNAL_EVENT_RECEIVED)
            requested = runtime.events.last(ET.DECISION_REQUESTED)
            proposed = runtime.events.last(ET.DECISION_PROPOSED)
            accepted = runtime.events.last(ET.DECISION_ACCEPTED)
            action_requested = runtime.events.last(ET.ACTION_REQUESTED)
            assert None not in (received, requested, proposed, accepted, action_requested)
            assert requested.causation_id == received.event_id
            assert proposed.causation_id == requested.event_id
            assert accepted.causation_id == requested.event_id
            assert action_requested.causation_id == received.event_id
            # one behaviour, one correlation
            assert (
                len({received.correlation_id, requested.correlation_id, proposed.correlation_id})
                == 1
            )
            assert proposed.payload["candidate_id"] == "action:play_minecraft"
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- Test 3/4


class TestProposalRejection:
    async def test_unknown_candidate_falls_back(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:teleport_moon", 0.99)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            result = await run_invitation(runtime)

            fallback = runtime.events.last(ET.DECISION_FALLBACK)
            assert fallback is not None
            assert fallback.payload["reason"] == "unknown_candidate"
            assert result["decision"]["source"] == "fallback"
            # the deterministic pick (core friend invitation) still ran
            assert result["action"] == "play_minecraft"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_confidence_is_treated_as_failure(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:play_minecraft", 0.1)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            result = await run_invitation(runtime)
            assert result["decision"]["source"] == "fallback"
            assert runtime.events.last(ET.DECISION_FALLBACK).payload["reason"] == (
                "llm_unavailable_or_invalid"
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_provider_failure_falls_back(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([AITimeoutError("mock", "A")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            result = await run_invitation(runtime)
            assert result["decision"]["accepted"] is True
            assert result["decision"]["source"] == "fallback"
            assert result["action"] == "play_minecraft"
            assert runtime.phase.value == "running"  # the sandbox never stalls
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 5


class TestStaleProposal:
    async def test_world_change_between_request_and_proposal_is_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            candidate = runtime._invitation_candidates("gaming", from_core=True)[1]  # noqa: SLF001
            request = runtime.decisions._request(  # noqa: SLF001 - build via the coordinator
                trigger=DecisionTrigger.external_invitation,
                candidates=[candidate],
            )
            proposal = runtime.decisions._parse(  # noqa: SLF001
                json_proposal(candidate.candidate_id)
            )
            assert proposal is not None
            proposal.request_id = request.request_id

            ok, why = runtime.decisions.validator.validate(request, proposal)
            assert ok is True  # still fresh

            runtime.adjust_need("hunger", delta=0.2, source="test", reason="world_moves")
            ok, why = runtime.decisions.validator.validate(request, proposal)
            assert ok is False and why == "world_changed"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_expired_request_is_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            candidate = runtime._invitation_candidates("gaming", from_core=True)[1]  # noqa: SLF001
            request = runtime.decisions._request(  # noqa: SLF001
                trigger=DecisionTrigger.external_invitation,
                candidates=[candidate],
            )
            request.deadline = clock.now - 1  # already past
            proposal = runtime.decisions._parse(  # noqa: SLF001
                json_proposal(candidate.candidate_id)
            )
            proposal.request_id = request.request_id  # type: ignore[union-attr]
            ok, why = runtime.decisions.validator.validate(request, proposal)  # type: ignore[arg-type]
            assert ok is False and why == "expired"
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 6


class TestNoBypass:
    async def test_decision_itself_changes_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:play_minecraft")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            started_before = len(runtime.events.of_type(ET.ACTION_STARTED))
            before = (
                runtime.character.location,
                runtime.current_action.definition_id,  # type: ignore[union-attr]
                {k: v.level for k, v in runtime.needs.all().items()},
                {k: dict(v.items) for k, v in runtime.inventories.all().items()},
                len(runtime.mutations),
            )
            outcome = await runtime.decisions.decide(
                trigger=DecisionTrigger.external_invitation,
                candidates=runtime._invitation_candidates("gaming", from_core=True),  # noqa: SLF001
            )
            after = (
                runtime.character.location,
                runtime.current_action.definition_id,  # type: ignore[union-attr]
                {k: v.level for k, v in runtime.needs.all().items()},
                {k: dict(v.items) for k, v in runtime.inventories.all().items()},
                len(runtime.mutations),
            )
            assert outcome.accepted is True
            assert len(runtime.events.of_type(ET.ACTION_STARTED)) == started_before
            assert before == after, "a decision must not touch the world (§18)"
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- Test 8/9


class TestValidationAuthority:
    async def test_second_character_rejects_a_nonexistent_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The world never owned a gaming action → the sandbox vetoes before any
        model call, and the validator would reject the id even if one arrived."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:play_minecraft", 0.99)])
        runtime = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH, engine=engine)
        try:
            await runtime._start_action("idle", duration_minutes=60)  # noqa: SLF001
            result = await run_invitation(runtime)

            assert result["influence"] == "reject"
            assert result["reason"] == "activity_unavailable"
            assert result.get("action") in (None, "")
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "idle"
            assert "play_minecraft" not in runtime.actions.definitions
            # the sandbox did not even ask the model
            assert provider.calls == [] and runtime.decisions.llm_calls == 0

            # last line of defence: a hand-forged proposal is rejected too
            from app.sandbox.intent import IntentProposal

            candidates = runtime._invitation_candidates("gaming", from_core=True)  # noqa: SLF001
            request = runtime.decisions._request(  # noqa: SLF001
                trigger=DecisionTrigger.external_invitation, candidates=candidates
            )
            forged = IntentProposal(
                request_id=request.request_id,
                candidate_id="action:play_minecraft",
                confidence=1.0,
            )
            ok, why = runtime.decisions.validator.validate(request, forged)
            assert ok is False and why == "unknown_candidate"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_character_rule_blocks_the_models_choice(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:play_minecraft", 0.99)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            original = runtime.rules.allows_action

            def guarded(action_id: str) -> tuple[bool, str]:
                if action_id == "play_minecraft":
                    return False, "rule:test_ban"
                return original(action_id)

            runtime.rules.allows_action = guarded  # type: ignore[method-assign]
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            result = await run_invitation(runtime)

            fallback = runtime.events.last(ET.DECISION_FALLBACK)
            assert fallback is not None and fallback.payload["reason"] == "rule:test_ban"
            assert result.get("action") in (None, "")
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "watch_animation"
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- Test 11/12


class TestContextAndMemory:
    async def test_decision_context_carries_relevant_memory_as_reference(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([json_proposal("action:play_minecraft", 0.9)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            from tests.test_sandbox_cognitive_bridge import write_memory

            await write_memory(
                db, runtime.character_id, "完成了 Minecraft 小城的建设", importance=0.9
            )
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await run_invitation(runtime)

            prompt = provider.calls[0]["last_user"]
            assert "小城" in prompt  # the memory reached the decision context
            assert "仅供参考" in prompt and "以当前世界为准" in prompt
            assert "需求：" in prompt  # world/needs lines are present too
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_decision_events_never_become_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([json_proposal("action:play_minecraft", 0.9)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await run_invitation(runtime)
            await runtime.flush_experiences()

            # the model's reasoning/proposal is not an experience or a memory
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert not any(kind.startswith("decision") for kind in kinds)
            rows = await db.fetchall(
                "SELECT content FROM memories WHERE character_id = ?", (runtime.character_id,)
            )
            for row in rows:
                assert "play_minecraft" not in str(row["content"])
                assert "candidate_id" not in str(row["content"])
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- gate surface


class TestDecisionGate:
    def test_gate_needs_two_executable_candidates(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.sandbox.intent import DecisionGate

        gate = DecisionGate(allow_llm=True)
        only = DecisionCandidate(candidate_id="a", kind=CandidateKind.postpone)
        blocked = DecisionCandidate(
            candidate_id="b", kind=CandidateKind.action, action_id="x", requirements=["nope"]
        )
        two = [only, DecisionCandidate(candidate_id="c", kind=CandidateKind.postpone)]
        assert gate.needs_llm([only]) is False
        assert gate.needs_llm([only, blocked]) is False  # unmet requirement ≠ executable
        assert gate.needs_llm(two) is True
        assert DecisionGate(allow_llm=False).needs_llm(two) is False

    def test_candidates_come_from_the_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Fixture world owns gaming; a stranger's invitation still adds it."""
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate.db'}"))
        import asyncio

        async def scenario() -> None:
            await db.connect()
            clock = Clock()
            runtime = await make_sandbox(db=db, clock=clock)
            try:
                await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
                candidates = runtime._invitation_candidates("gaming", from_core=False)  # noqa: SLF001
                ids = [c.candidate_id for c in candidates]
                assert "continue_current_action" in ids
                assert "action:play_minecraft" in ids
                assert "postpone_invitation" in ids
                core = runtime._invitation_candidates("gaming", from_core=True)  # noqa: SLF001
                action = next(c for c in core if c.kind is CandidateKind.action)
                continue_ = next(c for c in core if c.kind is CandidateKind.continue_current)
                assert action.priority > continue_.priority
            finally:
                await runtime.shutdown()
                await db.close()

        asyncio.run(scenario())
