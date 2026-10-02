"""Phase 12 tests (§62/§63/§64): conversational response runtime.

The contract: a reply is *language*. It may cite only facts that exist in this
turn's cognitive context, it may name an action only from the world-derived
candidate list, and it is discarded rather than sent when the world (or what
the character knows) moved while the model was thinking. Producing a reply never
writes an experience, a memory, a relationship, a promise, a goal or the world.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


def make_engine(responses: list) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(behaviors={"A": list(responses)})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    return engine, provider


async def make_sandbox(*, db, clock, engine=None, bible_path=None, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


def proposal(**fields) -> str:  # type: ignore[no-untyped-def]
    data = {"mode": "reply", "text": "在的呀，我这边刚忙完。", "confidence": 0.9}
    data.update(fields)
    return json.dumps(data, ensure_ascii=False)


async def shared_session(runtime, qq: str, *, instance_id: str, activity: str = "gaming") -> None:  # type: ignore[no-untyped-def]
    await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=runtime.persons.for_qq(qq).person_id,
            interaction_type="shared_activity",
            source="character_action",
            timestamp=runtime._clock(),  # noqa: SLF001
            outcome="completed",
            significance=InteractionSignificance.major,
            metadata={
                "target_activity": activity,
                "duration_minutes": 40.0,
                "action_id": f"play_{activity}",
                "action_instance_id": instance_id,
            },
        )
    )
    await runtime.flush_experiences()


async def turn(runtime, *, message="在吗", actor_id="9601", **kwargs):  # type: ignore[no-untyped-def]
    return await runtime.conversation_turn(message=message, actor_id=actor_id, **kwargs)


# ------------------------------------------------------------- §62 A/B/S/T


class TestBasicConversation:
    async def test_a_private_message_becomes_a_validated_reply(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="刚在打游戏，怎么了？")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(runtime, message="在干嘛呢", actor_id="9601")
            assert response.mode == "reply"
            assert response.text == "刚在打游戏，怎么了？"
            assert response.source == "llm"
            assert response.person_id == runtime.persons.for_qq("9601").person_id
            assert response.character_id == runtime.character_id
            assert response.world_revision == runtime.world_revision
            assert response.cognitive_revision == runtime.cognitive_revision
            assert provider.calls, "the model was asked once"
            prompt = provider.calls[0]["last_user"]
            assert "在干嘛呢" in prompt  # §14: the message itself
            assert "不要创造过去发生过的事件" in prompt  # §38 boundaries
            assert runtime.events.last(ET.CONVERSATION_RESPONSE_EMITTED) is not None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_response_context_only_carries_this_person(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§50: A's turn sees A's situation — never B's promises or memories."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=runtime.persons.for_qq("9602").person_id,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=clock.now,
                    significance=InteractionSignificance.meaningful,
                    metadata={"time_hint": "今晚一起打游戏", "target_activity": "gaming"},
                )
            )
            await shared_session(runtime, "9603", instance_id="act_b")
            await turn(runtime, message="晚上有空吗", actor_id="9602")
            prompt = provider.calls[0]["last_user"]
            assert "今晚一起打游戏" in prompt  # A's promise is visible
            assert "act_b" not in prompt  # B's episode is not (§50)
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_characters_keep_their_own_conversation_context(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§49: same person, two worlds — separate turns, prompts and responses."""
        db = await make_db(tmp_path)
        clock = Clock()
        first_engine, first_provider = make_engine([proposal(text="我这边是小明。")])
        second_engine, second_provider = make_engine([proposal(text="我这边是另一个角色。")])
        first = await make_sandbox(db=db, clock=clock, engine=first_engine)
        second = await make_sandbox(
            db=db, clock=clock, engine=second_engine, bible_path=OTHER_BIBLE_PATH
        )
        try:
            await shared_session(first, "9604", instance_id="act_world1")
            await shared_session(second, "9604", instance_id="act_world2")
            first_response = await turn(first, actor_id="9604")
            second_response = await turn(second, actor_id="9604")
            assert first_response.character_id != second_response.character_id
            assert first_response.text != second_response.text
            assert "act_world1" in first_provider.calls[0]["last_user"]
            assert "act_world1" not in second_provider.calls[0]["last_user"]
            assert first.events.last(ET.CONVERSATION_RESPONSE_EMITTED).target_entity_id == "9604"
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()


# --------------------------------------------------------------- §62 C/D/E/F


class TestReferences:
    async def test_a_reference_to_an_existing_memory_is_kept(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)  # the model joins afterwards
        try:
            await shared_session(runtime, "9605", instance_id="act_mem")
            message = "上次一起 gaming 那次还记得吗"
            context = await runtime.cognitive_context(query=message, relationship_target="9605")
            assert context.relevant_memories, "the turn retrieved the shared memory"
            memory_id = str(context.relevant_memories[0]["memory_id"])
            engine, _provider = make_engine([proposal(memory_refs=[memory_id])])
            runtime.ai_engine = engine
            # §18: a fact this turn's context really contains may be cited
            response = await turn(runtime, message=message, actor_id="9605")
            assert response.mode == "reply"
            assert response.memory_refs == [memory_id]
            assert response.dropped_refs == []
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_reference_to_something_else_is_stripped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§18/§51: the model may not cite a fact this turn never saw."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine(
            [proposal(memory_refs=["424242"], experience_refs=["exp_x"])]
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await shared_session(runtime, "9606", instance_id="act_real")
            response = await turn(runtime, message="在吗", actor_id="9606")
            assert response.memory_refs == []
            assert response.experience_refs == []
            assert set(response.dropped_refs) == {"424242", "exp_x"}
            assert response.reason == "refs_stripped"
            assert response.text  # the language itself may still be sent
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_episode_key_of_a_shared_experience_is_citable(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19/§62 E: experience refs come from this turn's shared episodes."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(experience_refs=["action:act_cite"])])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await shared_session(runtime, "9607", instance_id="act_cite")
            response = await turn(runtime, message="上次那个游戏", actor_id="9607")
            assert response.experience_refs == ["action:act_cite"]
            assert response.dropped_refs == []
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_unknown_experience_reference_is_stripped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(experience_refs=["action:act_missing"])])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await shared_session(runtime, "9608", instance_id="act_known")
            response = await turn(runtime, message="上次那个", actor_id="9608")
            assert response.experience_refs == []
            assert response.dropped_refs == ["action:act_missing"]
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- §62 G/H/§23


class TestActionCandidate:
    async def test_a_world_derived_candidate_is_kept(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§23: the model may point at a candidate the world produced."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(action_candidate_id="action:play_minecraft")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(
                runtime,
                message="要不要一起玩",
                actor_id="9609",
                action_candidates=["action:play_minecraft", "postpone_invitation"],
            )
            assert response.action_candidate_id == "action:play_minecraft"
            assert response.dropped_refs == []
            # §24: naming a candidate is *not* executing it
            assert runtime.current_action is None
            assert runtime.events.last(ET.ACTION_STARTED) is None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_invented_action_is_dropped_and_never_executed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§52: no candidate, no action — the language survives, the claim does not."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(action_candidate_id="action:teleport_moon")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(
                runtime,
                message="去月球吧",
                actor_id="9610",
                action_candidates=["action:play_minecraft"],
            )
            assert response.action_candidate_id == ""
            assert response.dropped_refs == ["action:action:teleport_moon"]
            assert runtime.current_action is None
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- §62 I/J/§25-§27


class TestStaleness:
    async def test_a_world_change_during_thinking_discards_the_reply(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:

            async def mutating_engine(request):  # type: ignore[no-untyped-def]
                # the world moves while the model is "thinking"
                runtime._adjust_need(  # noqa: SLF001
                    "social_need", -0.01, source="test", reason="world_moves"
                )
                return type("Answer", (), {"content": proposal()})

            engine = _StubEngine(mutating_engine)
            runtime.ai_engine = engine
            response = await turn(runtime, message="在吗", actor_id="9611")
            assert response.mode == "silent"
            assert response.source == "fallback"
            assert response.reason == "world_changed"
            assert response.text == ""
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_relationship_change_during_thinking_discards_the_reply(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§27: cognitive_revision alone is enough to invalidate a reply."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:

            async def mutating_engine(request):  # type: ignore[no-untyped-def]
                await runtime.apply_social_interaction(
                    SocialInteractionFact.create(
                        character_id=runtime.character_id,
                        person_id=runtime.persons.for_qq("9612").person_id,
                        interaction_type="message_received",
                        source="test",
                        timestamp=clock.now,
                    )
                )
                return type("Answer", (), {"content": proposal()})

            runtime.ai_engine = _StubEngine(mutating_engine)
            response = await turn(runtime, message="在吗", actor_id="9612")
            assert response.mode == "silent"
            assert response.reason == "cognitive_changed"
            assert runtime.world_revision == 0  # only the cognitive line moved
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- §62 K/L/M/N/V


class TestReadOnly:
    async def test_a_turn_changes_nothing_in_the_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=runtime.persons.for_qq("9613").person_id,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=clock.now,
                    significance=InteractionSignificance.meaningful,
                    metadata={"time_hint": "今晚一起打游戏", "target_activity": "gaming"},
                )
            )
            await shared_session(runtime, "9613", instance_id="act_ro")
            person = runtime.persons.for_qq("9613").person_id
            before = {
                "world": runtime.world_revision,
                "cognitive": runtime.cognitive_revision,
                "relationship": (await runtime.relationships_dyn.get(person)).model_dump(),  # type: ignore[union-attr]
                "commitments": [
                    (item.commitment_id, item.status.value, item.revision)
                    for item in runtime.commitments.all()
                ],
                "goals": [(goal.goal_id, goal.status.value) for goal in runtime.goals.all()],
                "memories": await runtime.memory.count(),
                "experiences": len(runtime.experiences.emitted()),
                "mutations": len(runtime.mutations.recent(limit=200)),
                "action": runtime.current_action,
            }
            response = await turn(runtime, message="在吗", actor_id="9613")
            assert response.mode == "reply"
            after = {
                "world": runtime.world_revision,
                "cognitive": runtime.cognitive_revision,
                "relationship": (await runtime.relationships_dyn.get(person)).model_dump(),  # type: ignore[union-attr]
                "commitments": [
                    (item.commitment_id, item.status.value, item.revision)
                    for item in runtime.commitments.all()
                ],
                "goals": [(goal.goal_id, goal.status.value) for goal in runtime.goals.all()],
                "memories": await runtime.memory.count(),
                "experiences": len(runtime.experiences.emitted()),
                "mutations": len(runtime.mutations.recent(limit=200)),
                "action": runtime.current_action,
            }
            assert before == after  # §2/§62 V: language never writes the world
            # the response events are trace-only (§33): no revisions moved
            emitted = runtime.events.last(ET.CONVERSATION_RESPONSE_EMITTED)
            assert emitted is not None and emitted.payload["mode"] == "reply"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_non_llm_layer_is_deterministic(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§62 W: same turn + same state → the same context, refs and candidates."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(), proposal()])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await shared_session(runtime, "9614", instance_id="act_det")
            first = await runtime.cognitive_context(query="在吗", relationship_target="9614")
            second = await runtime.cognitive_context(query="在吗", relationship_target="9614")
            assert first.social_situation == second.social_situation
            assert [row["memory_id"] for row in first.relevant_memories] == [
                row["memory_id"] for row in second.relevant_memories
            ]
            assert first.as_prompt_payload()["person"] == second.as_prompt_payload()["person"]
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- §62 O/P/Q/R/§53-§57


class TestFailureHandling:
    async def test_no_model_means_silence_not_an_exception(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)  # no engine at all
        try:
            response = await turn(runtime, message="在吗", actor_id="9615")
            assert response.mode == "silent"
            assert response.source == "fallback"
            assert response.reason == "llm_unavailable_or_invalid"
            assert response.text == ""  # §53: no fabricated fallback persona text
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_invalid_json_is_not_a_response(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine(["好的没问题，我马上就来！"])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(runtime, message="在吗", actor_id="9616")
            assert response.mode == "silent" and response.source == "fallback"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_confidence_is_not_sent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(confidence=0.05)])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(runtime, message="在吗", actor_id="9617")
            assert response.mode == "silent" and response.reason == "low_confidence"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_empty_reply_is_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="   ")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(runtime, message="在吗", actor_id="9618")
            assert response.mode == "silent" and response.reason == "empty_text"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_over_long_reply_is_rejected_not_truncated(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="啊" * 500)])
        runtime = await make_sandbox(
            db=db, clock=clock, engine=engine, conversation_max_response_chars=200
        )
        try:
            response = await turn(runtime, message="在吗", actor_id="9619")
            assert response.mode == "silent" and response.reason == "too_long"
            assert response.text == ""
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_mode_above_the_policy_ceiling_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§36: whether this turn may speak is the caller's policy, not the model's."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(mode="reply")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(
                runtime,
                message="群里随便说点什么",
                actor_id="9620",
                group_id="12345",
                social_space_id="game_group",
                mode_ceiling="silent",
            )
            assert response.mode == "silent"
            assert response.source == "policy"
            assert response.reason == "response_not_allowed"
            assert not provider.calls, "a forbidden turn never reaches the model"
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------------ §62 U/§16


class TestWorldPrecedence:
    async def test_the_prompt_shows_sleep_not_the_old_gaming_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([proposal(text="我刚睡醒。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await shared_session(runtime, "9621", instance_id="act_past")
            assert await runtime._start_action("sleep", duration_minutes=60) is not None  # noqa: SLF001
            response = await turn(runtime, message="在干嘛", actor_id="9621")
            prompt = provider.calls[0]["last_user"]
            current_world = prompt.split('"current_world"', 1)[1].split('"person"', 1)[0]
            assert "sleep" in current_world or "睡觉" in current_world
            assert "gaming" not in current_world  # the memory is not the world
            assert response.text == "我刚睡醒。"
            assert runtime.current_action.definition_id == "sleep"  # nothing moved
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- §63 group propagation


class TestGroupTurn:
    async def test_group_context_is_carried_down_the_chain(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(mode="acknowledge", text="收到~")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            response = await turn(
                runtime,
                message="@我 在吗",
                actor_id="9622",
                source="qq",
                group_id="55501",
                social_space_id="game_group",
                event_id="evt_group_1",
                mode_ceiling="acknowledge",
            )
            assert response.mode == "acknowledge"
            assert response.person_id == runtime.persons.for_qq("9622").person_id
            emitted = runtime.events.last(ET.CONVERSATION_RESPONSE_EMITTED)
            assert emitted is not None and emitted.target_entity_id == "9622"
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------- §64 invitation regression


class TestInvitationRegression:
    async def test_the_invitation_flow_is_not_doubled_by_the_response_runtime(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        """§64: the influence/decision path owns actions; a reply only reports."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine([proposal(text="好呀，晚上一起玩。")])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await runtime.submit_external(
                # the same wording a QQ invitation would carry
                __import__(
                    "app.sandbox.external", fromlist=["ExternalWorldEvent"]
                ).ExternalWorldEvent(
                    event_id="p12-inv",
                    source=__import__(
                        "app.sandbox.external", fromlist=["ExternalSource"]
                    ).ExternalSource.qq,
                    actor_id="9623",
                    content="晚上一起玩？",
                    urgency=__import__(
                        "app.sandbox.external", fromlist=["ExternalUrgency"]
                    ).ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            decisions = runtime.events.of_type(ET.DECISION_REQUESTED)
            assert len(decisions) == 1  # the influence path decided, once

            response = await turn(runtime, message="晚上一起玩？", actor_id="9623")
            assert response.mode == "reply"
            assert len(runtime.events.of_type(ET.DECISION_REQUESTED)) == 1  # still one
            assert runtime.current_action is not None  # the accepted action kept running
            assert runtime.current_action.definition_id == "play_minecraft"
        finally:
            await runtime.shutdown()
            await db.close()


class _StubEngine:
    """An engine whose answer is produced by a callback (for staleness tests)."""

    enabled = True

    def __init__(self, answer):  # type: ignore[no-untyped-def]
        self._answer = answer

    async def chat(self, _request):  # type: ignore[no-untyped-def]
        return await self._answer(_request)
