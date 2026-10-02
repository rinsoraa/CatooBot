"""Phase 8 tests (§32-§34): social & relationship dynamics.

The contract: a relationship only moves when a *verified interaction fact*
says something happened — deterministically, in small steps, character-scoped,
and never because a model or a memory said so. Invitations and accepted
activities are strictly different facts.
"""

from __future__ import annotations

from pathlib import Path

from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.intent import DecisionOutcome
from app.sandbox.relations import (
    InteractionSignificance,
    SocialInteractionFact,
    classify_significance,
    person_id_for,
)
from tests.test_sandbox_goals import Clock, make_db, make_sandbox
from tests.test_sandbox_memory_foundation import make_runtime  # noqa: F401 - shared fixture path

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


async def fact(runtime, person_id: str, interaction_type: str, *, significance=None, **kw):  # type: ignore[no-untyped-def]
    return await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person_id,
            interaction_type=interaction_type,
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=significance or InteractionSignificance.normal,
            **kw,
        )
    )


# ------------------------------------------------------------- Test 1/2/3


class TestDeterministicUpdates:
    async def test_first_message_barely_moves_anything(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7001")
            state = await fact(runtime, person, "message_received")
            # a first ordinary message: familiarity ticks, nothing else
            assert state.familiarity > 0.1
            assert state.trust == 0.3 and state.closeness == 0.1
            assert state.interaction_count == 1
            assert runtime.events.last(ET.SOCIAL_INTERACTION) is not None
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            assert changed is not None and changed.target_entity_id == person
            interaction = runtime.events.last(ET.SOCIAL_INTERACTION)
            assert changed.causation_id == interaction.event_id  # §16 chain
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_repeated_meaningful_interactions_accumulate_gradually(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7002")
            await fact(runtime, person, "shared_activity")
            first = await runtime.relationships_dyn.get(person)
            assert first is not None
            for _ in range(5):
                await fact(runtime, person, "shared_activity")
            later = await runtime.relationships_dyn.get(person)
            assert later is not None
            assert later.closeness > first.closeness  # gradual, monotone
            assert later.closeness <= first.closeness + 6 * 0.05  # §12 inertia
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_major_event_small_trust_step(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7003")
            await fact(runtime, person, "help_received", significance=InteractionSignificance.major)
            state = await runtime.relationships_dyn.get(person)
            assert state is not None
            assert 0.3 < state.trust <= 0.3 + 0.05  # small, capped step (§12)
            entries = [
                entry
                for entry in runtime.mutations.recent(limit=50)
                if entry["target"] == f"relationship:{person}" and entry["field"] == "trust"
            ]
            assert entries and entries[0]["before"] == 0.3
            assert entries[0]["after"] - entries[0]["before"] <= 0.05  # capped step (§12)
        finally:
            await runtime.shutdown()
            await db.close()

    def test_trivial_messages_are_classified_as_such(self) -> None:
        for text in ("嗯", "哈哈", "在吗", "……", "？？"):
            assert (
                classify_significance(text, interaction_type="message_received")
                is InteractionSignificance.trivial
            )
        assert (
            classify_significance("周末要不要一起联机打游戏", interaction_type="message_received")
            is InteractionSignificance.normal
        )

    async def test_trivial_message_does_not_touch_the_relationship(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7004")
            await runtime.submit_external(
                adapt_qq_message(
                    message_id="triv-1", actor_id="7004", text="哈哈", is_core_actor=False
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()
            state = await runtime.relationships_dyn.get(person)
            # the fact is recorded (count) but no dimension moved
            assert state is not None and state.interaction_count == 1
            assert state.familiarity == 0.1 and state.closeness == 0.1
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 4/5/33


class TestInvitationIsNotAcceptance:
    async def test_invitation_alone_creates_no_shared_activity(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A stranger's invitation is noticed, not decided (§19/§33)."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p8-inv-decline",
                    source=ExternalSource.qq,
                    actor_id="7005",
                    content="来一起联机",
                    urgency=ExternalUrgency.high,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="known",  # an acquaintance: observe only
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()

            types = [
                e.payload.get("interaction_type")
                for e in runtime.events.of_type(ET.SOCIAL_INTERACTION)
            ]
            assert "game_invitation" in types  # the invite is a fact
            assert "shared_activity" not in types  # §19/§33: the invite is not a bond
            # and no decision was made, so there is nothing to accept or decline
            assert "invitation_accepted" not in types
            assert "invitation_declined" not in types
            opinion = await runtime.relationships_dyn.get(person_id_for(kind="qq", value="7005"))
            assert opinion is not None
            assert opinion.closeness == 0.1 and opinion.trust == 0.3  # a bond needs acts
            assert runtime.current_action.definition_id == "watch_animation"  # type: ignore[union-attr]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_declined_invitation_is_recorded_when_the_world_cannot_accept(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        """An invitation the sandbox actually weighed and turned down (§20).

        The decline is a fact of its own — recorded, small, recoverable — and
        still not a shared activity.
        """
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            # the world cannot host the activity right now (a canonical block)
            original = runtime.rules.allows_action

            def blocked(action_id: str):  # type: ignore[no-untyped-def]
                if action_id == "play_minecraft":
                    return False, "rule:test_block"
                return original(action_id)

            runtime.rules.allows_action = blocked  # type: ignore[method-assign]
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p8-inv-decline-decided",
                    source=ExternalSource.qq,
                    actor_id="7009",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,  # the gate opens
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()
            types = [
                e.payload.get("interaction_type")
                for e in runtime.events.of_type(ET.SOCIAL_INTERACTION)
            ]
            assert "invitation_declined" in types
            assert "invitation_accepted" not in types
            assert "shared_activity" not in types
            assert runtime.current_action.definition_id == "watch_animation"  # type: ignore[union-attr]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_accepted_invitation_becomes_shared_activity_after_it_runs(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p8-inv-accept",
                    source=ExternalSource.qq,
                    actor_id="7006",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,  # interrupts → accepted
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()
            types = [
                e.payload.get("interaction_type")
                for e in runtime.events.of_type(ET.SOCIAL_INTERACTION)
            ]
            assert "invitation_accepted" in types
            assert "shared_activity" not in types  # not until it actually runs (§19)

            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            await runtime.wait_social()
            types = [
                e.payload.get("interaction_type")
                for e in runtime.events.of_type(ET.SOCIAL_INTERACTION)
            ]
            assert "shared_activity" in types  # §19: now the activity happened
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 6


class TestNegativeInteractions:
    async def test_repeated_ignoring_lowers_comfort_gradually(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7007")
            await fact(runtime, person, "interaction_ignored")
            first = await runtime.relationships_dyn.get(person)
            assert first is not None and first.social_comfort < 0.3
            for _ in range(3):
                await fact(runtime, person, "interaction_ignored")
            later = await runtime.relationships_dyn.get(person)
            assert later is not None
            assert later.social_comfort < first.social_comfort
            assert later.negative_interactions >= 4
            assert later.social_comfort > 0.0  # degraded, never destroyed (§20)
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- Test 7/14


class TestCharacterIsolation:
    async def test_same_person_is_two_states_in_two_worlds(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            person = person_id_for(kind="qq", value="7008")
            await fact(first, person, "shared_activity")
            assert await second.relationships_dyn.get(person) is None  # isolated (§6)
            state_a = await first.relationships_dyn.get(person)
            assert state_a is not None and state_a.character_id == first.character_id
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_core_friend_keeps_his_canonical_initial_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§28: the bible relationship is the starting point, not a guess."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            assert runtime.seed.core_friend_names, "fixture bible has a core friend"
            name = runtime.seed.core_friend_names[0]
            initial = runtime.relationships_dyn.initial_for_name(name)
            assert initial is not None
            assert initial.source == "character_bible"
            assert initial.trust > 0.5  # canonical closeness, from the bible
            # the seeded row is persisted for restart continuity
            rows = await db.fetchall(
                "SELECT person_id FROM sandbox_relationships WHERE character_id = ?",
                (runtime.character_id,),
            )
            assert any(str(row["person_id"]) == initial.person_id for row in rows)
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- Test 8/9


class TestRelationshipInfluencesDecision:
    async def test_closeness_nudges_candidate_priority_only(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§21/§22: closeness raises *desirability*; it never executes anything."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            captured: list[list] = []
            original_decide = runtime.decisions.decide

            async def spy(**kwargs):  # type: ignore[no-untyped-def]
                captured.append(list(kwargs["candidates"]))
                # execute nothing: this test is about the candidate set
                return DecisionOutcome(
                    accepted=False, reason="spy", correlation_id=kwargs.get("correlation_id", "")
                )

            runtime.decisions.decide = spy  # type: ignore[method-assign]
            try:
                await runtime._run_invitation_decision(  # noqa: SLF001
                    activity="gaming",
                    from_core=True,
                    actor="7011",
                    reason_code="test",
                    received_id="",
                    correlation="p8-priority-base",
                )
                baseline = next(c for c in captured[-1] if c.action_id)

                person = runtime.persons.for_qq("7011")
                for _ in range(4):
                    await fact(runtime, person.person_id, "shared_activity")
                state = await runtime.relationships_dyn.get(person.person_id)
                assert state is not None and state.closeness > 0.1

                await runtime._run_invitation_decision(  # noqa: SLF001
                    activity="gaming",
                    from_core=True,
                    actor="7011",
                    reason_code="test",
                    received_id="",
                    correlation="p8-priority-bonus",
                )
                boosted = next(c for c in captured[-1] if c.action_id)
                assert boosted.priority > baseline.priority  # desirability only
                # and nothing ran: a relationship never acts on its own (§22)
                assert runtime.current_action is None
            finally:
                runtime.decisions.decide = original_decide  # type: ignore[method-assign]
                await runtime.wait_social()
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_relationship_drift_does_not_stale_a_pending_decision(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§21: a relationship moves alongside the world, never inside it.

        Bookkeeping is audited and persisted, but it cannot change what an
        action requires or does — so it must not invalidate a proposal that is
        being validated against the world right now.
        """
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7017")
            revision = runtime.world_revision
            await fact(runtime, person, "shared_activity")
            assert runtime.world_revision == revision  # no world bump
            audited = [
                entry
                for entry in runtime.mutations.recent(limit=20)
                if entry["target"] == f"relationship:{person}"
            ]
            assert audited, "the change is still on the canonical mutation trail"

            runtime._adjust_need(  # noqa: SLF001
                "social_need", -0.01, source="test", reason="world_touch"
            )
            assert runtime.world_revision == revision + 1  # world state still counts
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_relationship_enters_cognitive_context_for_the_speaker_only(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            speaker = "7012"
            other = "7013"
            await fact(runtime, runtime.persons.for_qq(speaker).person_id, "shared_activity")
            await fact(runtime, runtime.persons.for_qq(other).person_id, "shared_activity")

            payload = (
                await runtime.cognitive_context(query="在吗", relationship_target=speaker)
            ).as_prompt_payload()
            relationships = payload["relationships"]
            assert len(relationships) == 1  # only the current speaker (§23/§24)
            assert relationships[0]["person_id"] == runtime.persons.for_qq(speaker).person_id
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 10/27


class TestStoreDiscipline:
    async def test_reads_are_copies_and_never_create(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§38: reading a relationship neither creates nor changes one."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            # a lookup for someone she has no history with yields nothing —
            # and does not plant an empty state for the continuity snapshot
            assert await runtime.relationship_for("7016") is None

            person = person_id_for(kind="qq", value="7016")
            await fact(runtime, person, "message_received")
            first = await runtime.relationships_dyn.get(person)
            assert first is not None
            first.trust = 0.99  # a caller's local edit
            second = await runtime.relationships_dyn.get(person)
            assert second is not None and second.trust < 0.99  # the store kept its own
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 10/27


class TestRelationshipMemoryBoundary:
    async def test_only_major_changes_become_experiences(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7014")
            await fact(runtime, person, "message_received")  # ordinary drift
            await runtime.flush_experiences()
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert "relationship_changed" not in kinds  # §27: 0.01 is not a memory

            # push a dimension across a milestone → a major change
            state = await runtime.relationships_dyn.get(person)
            assert state is not None
            state.trust = 0.69
            await runtime.relationships_dyn.save(state)
            await fact(runtime, person, "help_received")
            await runtime.flush_experiences()
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert "relationship_changed" in kinds
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            assert changed is not None and changed.payload["significance"] == "major"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_memory_retrieval_never_moves_the_relationship(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§25/§38: memories are downstream — reading them changes nothing."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="7015")
            await fact(runtime, person, "message_received")
            before = await runtime.relationships_dyn.get(person)
            assert before is not None
            snapshot = before.model_dump()

            await runtime.cognitive_context(query="以前的事", relationship_target="7015")
            await runtime.build_continuity()
            await runtime.memory.retrieve_relevant(query="以前的事")
            after = await runtime.relationships_dyn.get(person)
            assert after is not None and after.model_dump() == snapshot
        finally:
            await runtime.shutdown()
            await db.close()
