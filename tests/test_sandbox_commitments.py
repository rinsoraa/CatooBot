"""Phase 9 tests (§42/§43): social commitment & obligation.

The contract: a promise is *state*, not a mood and not a memory. It exists only
because a verified fact said so, it lives in its own character-scoped world, it
reaches action only through Goal → Decision → Validator, and it changes the
relationship only through a Phase 8 interaction fact.
"""

from __future__ import annotations

from pathlib import Path

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.commitments import (
    MIN_FULFILL_DURATION_MINUTES,
    CommitmentKind,
    CommitmentStatus,
    parse_time_hint,
)
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent
from app.sandbox.goals import GoalKind, GoalSource, GoalStatus
from app.sandbox.intent import DecisionTrigger
from app.sandbox.relations import (
    InteractionSignificance,
    SocialInteractionFact,
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
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    return engine, provider


async def make_sandbox(*, db, clock, bible_path=None, engine=None, **cfg):  # type: ignore[no-untyped-def]
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


async def promise(  # type: ignore[no-untyped-def]
    runtime,
    qq: str,
    hint: str,
    *,
    activity: str = "gaming",
    interaction_type: str = "invitation_accepted",
    kind: str = "",
    significance: InteractionSignificance = InteractionSignificance.meaningful,
):
    """The production path: a verified fact carrying explicit arrangement material."""
    person = runtime.persons.for_qq(qq).person_id
    metadata: dict[str, object] = {"time_hint": hint, "target_activity": activity}
    if kind:
        metadata["commitment_kind"] = kind
    return await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person,
            interaction_type=interaction_type,
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=significance,
            external_ids={"qq": qq},
            metadata=metadata,
        )
    )


def advance_to_window(runtime, clock, commitment) -> None:  # type: ignore[no-untyped-def]
    """Move the fake clock into the commitment's execution window (seconds)."""
    target = float(commitment.earliest_at) + 60.0
    clock.advance(max(0.0, target - float(clock.now)))


def fulfill_goals(runtime) -> list:  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.fulfill_commitment]


# ------------------------------------------------------- Test 1/2/3/§9-§12


class TestDetection:
    async def test_explicit_future_arrangement_creates_a_commitment(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.persons.for_qq("9001")
            await promise(runtime, "9001", "今晚一起打 Minecraft")
            open_items = runtime.commitments.open()
            assert len(open_items) == 1
            commitment = open_items[0]
            assert commitment.kind is CommitmentKind.shared_activity
            assert commitment.status is CommitmentStatus.pending
            assert commitment.strength.value == "explicit"
            assert commitment.target_activity == "gaming"
            assert commitment.due_at > clock.now
            assert commitment.source_interaction_id and commitment.source_event_id  # §8
            created = runtime.events.last(ET.COMMITMENT_CREATED)
            assert (
                created is not None and created.payload["commitment_id"] == commitment.commitment_id
            )
            assert created.causation_id  # commitment ← fact event (§41)
            # Case A: a promise is state, never an immediate action
            assert runtime.current_action is None
            assert runtime.world_revision == 0  # §39: social state, not world state
            assert runtime.cognitive_revision > 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_vague_intent_creates_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            for hint in ("下次一起玩吧", "有空一起玩", "以后有机会再说"):
                await promise(runtime, "9002", hint)
            assert runtime.commitments.open() == []
            assert runtime.commitment_detector.vague == 3  # §12: intent, not a promise
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_invitation_is_not_a_commitment(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10/§35: acceptance without a future arrangement owes nobody anything."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = runtime.persons.for_qq("9003").person_id
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=person,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=runtime._clock(),  # noqa: SLF001
                    significance=InteractionSignificance.meaningful,
                )
            )
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=person,
                    interaction_type="game_invitation",  # the invite itself
                    source="test",
                    timestamp=runtime._clock(),  # noqa: SLF001
                    metadata={"time_hint": "今晚一起玩"},
                )
            )
            assert runtime.commitments.open() == []
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- Test 4/5/6/§6/§28/§29


class TestPersistenceAndScope:
    async def test_commitment_is_character_scoped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            # the same person exists in both worlds (same qq), two separate promises
            await promise(first, "9004", "今晚一起打游戏")
            await promise(second, "9004", "今晚一起打游戏")
            await first.commitments.flush()
            await second.commitments.flush()
            assert len(first.commitments.open()) == 1
            assert len(second.commitments.open()) == 1
            a, b = first.commitments.open()[0], second.commitments.open()[0]
            assert a.commitment_id != b.commitment_id
            assert a.character_id != b.character_id
            rows = await db.fetchall("SELECT character_id, commitment_id FROM sandbox_commitments")
            assert len(rows) == 2
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_commitment_persists_and_reloads(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9005", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            await runtime.commitments.flush()
            commitment_id, due_at, revision = (
                commitment.commitment_id,
                commitment.due_at,
                commitment.revision,
            )
            await runtime.shutdown()
        finally:
            pass
        clock2 = Clock()
        clock2.advance(0)
        restored = await make_sandbox(db=db, clock=clock)
        try:
            again = restored.commitments.get(commitment_id)
            assert again is not None
            assert again.due_at == due_at and again.revision == revision
            assert again.open
        finally:
            await restored.shutdown()
            await db.close()

    async def test_restart_recovery_re_evaluates_windows(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9006", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.commitments.flush()
            commitment_id = commitment.commitment_id
            await runtime.shutdown()
        finally:
            pass
        restored = await make_sandbox(db=db, clock=clock)
        try:
            goals = fulfill_goals(restored)
            assert len(goals) == 1  # §29: reloaded, evaluated, one goal
            assert goals[0].target_commitment == commitment_id
            assert restored.commitments.get(commitment_id).status in (
                CommitmentStatus.active,
                CommitmentStatus.in_progress,
            )
        finally:
            await restored.shutdown()
            await db.close()


# --------------------------------------------------------- Test 7/8/9/§15-§18


class TestCommitmentToGoal:
    async def test_commitment_becomes_a_goal_only_in_its_window(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9007", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            await runtime.tick(minutes=1)  # before the window: nothing to do yet
            assert not fulfill_goals(runtime)
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            goals = fulfill_goals(runtime)
            assert len(goals) == 1
            goal = goals[0]
            assert goal.source is GoalSource.social_commitment
            assert goal.target_commitment == commitment.commitment_id
            assert goal.metadata["commitment_id"] == commitment.commitment_id
            assert goal.priority > 0.5  # §17: explicit shared activity outranks idle
            assert runtime.events.last(ET.COMMITMENT_ACTIVATED) is not None
            assert runtime.events.last(ET.GOAL_CREATED) is not None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_one_commitment_never_creates_two_goals(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9008", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            for _ in range(4):
                await runtime.tick(minutes=1)
            goals = fulfill_goals(runtime)
            assert len(goals) == 1  # §16: the promise holds exactly one goal
            assert goals[0].target_commitment == commitment.commitment_id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_commitment_goal_runs_through_the_decision_layer(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine(
            ['{"candidate_id": "action:play_minecraft", "confidence": 0.9}']
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await promise(runtime, "9009", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "play_minecraft"
            requested = runtime.events.last(ET.DECISION_REQUESTED)
            assert requested is not None and requested.payload["reason"] == (
                DecisionTrigger.goal_step.value
            )
            assert requested.correlation_id.startswith("goal_")  # §41: its own thread
            assert runtime.events.last(ET.ACTION_STARTED) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------ Test 10/11/12/§20-§25


class TestOutcomes:
    async def test_shared_activity_fulfills_the_commitment(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9010", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            # she is busy when the friend's invitation lands (otherwise the
            # influence layer only wakes her — Phase 3 §6) …
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            # … and the agreed activity really runs: accept, then play
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p9-play",
                    source=ExternalSource.qq,
                    actor_id="9010",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            assert runtime.current_action.definition_id == "play_minecraft"  # type: ignore[union-attr]
            action = runtime.current_action
            assert action is not None
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()

            assert commitment.status is CommitmentStatus.completed
            assert commitment.revision >= 1
            fulfilled = runtime.events.last(ET.COMMITMENT_FULFILLED)
            assert fulfilled is not None
            assert fulfilled.payload["commitment_id"] == commitment.commitment_id
            shared = [
                e
                for e in runtime.events.of_type(ET.SOCIAL_INTERACTION)
                if e.payload.get("interaction_type") == "shared_activity"
            ]
            assert shared, "the shared activity itself was recorded (§21)"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_delays_inside_the_grace_window_are_not_broken(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9011", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            clock.advance(commitment.due_at + 60.0 - clock.now)  # one minute late
            report = runtime.commitments.sweep()
            assert report["broken"] == 0  # §24: grace, never hair-trigger
            assert runtime.commitments.get(commitment.commitment_id).open
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_missed_commitment_breaks_after_the_grace_window(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Case F: due + grace + no outcome → broken → relationship consequence."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9012", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            state_before = await runtime.relationships_dyn.get(commitment.person_id)
            clock.advance(commitment.due_at + 3 * 3600.0 - clock.now)
            report = runtime.commitments.sweep()
            assert report["broken"] == 1
            assert commitment.status is CommitmentStatus.broken
            broken = runtime.events.last(ET.COMMITMENT_BROKEN)
            assert broken is not None and broken.payload["significance"] == "major"
            await runtime.wait_social()  # the outcome fact reaches Phase 8
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            assert changed is not None and "trust" in changed.payload["fields"]
            state_after = await runtime.relationships_dyn.get(commitment.person_id)
            assert state_before is not None and state_after is not None
            assert state_after.trust < state_before.trust
            assert state_after.negative_interactions >= 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_reschedule_keeps_one_life_line(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Case E: same commitment id, revision + 1, old schedule archived."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9013", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            commitment_id, first_due = commitment.commitment_id, commitment.due_at
            tomorrow = parse_time_hint("明天晚上一起打游戏", now=clock.now)
            assert tomorrow is not None
            assert runtime.commitments.reschedule(commitment, window=tomorrow, reason="test")
            assert commitment.commitment_id == commitment_id  # one life line
            assert commitment.status is CommitmentStatus.rescheduled
            assert commitment.revision == 1
            assert commitment.due_at != first_due and commitment.due_at == tomorrow.due_at
            history = commitment.metadata["schedule_history"]
            assert history and history[-1]["due_at"] == first_due
            assert history[-1]["revision"] == 0
            moved = runtime.events.last(ET.COMMITMENT_RESCHEDULED)
            assert moved is not None and moved.payload["revision"] == 1

            # the new window re-activates the same promise
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert commitment.status in (
                CommitmentStatus.active,
                CommitmentStatus.in_progress,
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_polite_cancellation_is_not_a_broken_promise(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9014", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            assert runtime.commitments.cancel(
                commitment, by="character_cancelled", reason="told_them_early"
            )
            assert commitment.status is CommitmentStatus.cancelled
            cancelled = runtime.events.last(ET.COMMITMENT_CANCELLED)
            assert cancelled is not None
            assert cancelled.payload["by"] == "character_cancelled"
            await runtime.wait_social()
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            # §23: an early, honest cancellation is not a betrayal — the tiny
            # comfort dip is fine, the trust drop reserved for "broken" is not
            assert changed is not None and changed.payload["interaction_type"] == (
                "commitment_rescheduled"
            )
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ Test 13/24/§37-§38


class TestRevisionStaleness:
    async def test_goal_is_cancelled_when_its_commitment_moves(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9015", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert len(fulfill_goals(runtime)) == 1
            goal = fulfill_goals(runtime)[0]
            assert runtime.goal_precheck(goal) == ""  # born under this revision

            tomorrow = parse_time_hint("明天晚上一起打游戏", now=clock.now)
            assert tomorrow is not None
            runtime.commitments.reschedule(commitment, window=tomorrow, reason="test")
            ok, reason = runtime.commitment_bridge.validate(goal)
            assert (ok, reason) == (False, "commitment_rescheduled")
            assert runtime.commitment_bridge.cancel_stale() == [goal.goal_id]
            assert goal.status is GoalStatus.cancelled
            assert runtime.events.last(ET.GOAL_CANCELLED).payload["reason"] == (
                "commitment_rescheduled"
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stale_goal_never_starts_its_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§37/§38: the check happens *right before* execution, not just at sweep."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9016", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            # a goal is born while the promise is open …
            await runtime.commitment_bridge.evaluate()
            assert len(fulfill_goals(runtime)) == 1
            goal = fulfill_goals(runtime)[0]
            assert runtime.current_action is None
            # … and the promise is cancelled before that goal ever ran
            runtime.commitments.cancel(commitment, by="other_party_cancelled", reason="test")
            started = await runtime.goals.advance()
            assert started is False
            assert goal.status is GoalStatus.cancelled
            assert runtime.current_action is None  # the stale step never executed
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- Test 14/15/§27 resume


class TestInterruptAndResume:
    async def test_commitment_survives_an_action_interrupt_and_resume(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9017", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert len(fulfill_goals(runtime)) == 1
            goal = fulfill_goals(runtime)[0]
            step = goal.current_step
            assert step is not None and step.action_instance_id
            instance_before = step.action_instance_id

            # a critical need pulls her away mid-promise
            await runtime._interrupt_action("pet_critical", resumable=True)  # noqa: SLF001
            assert runtime.current_action is None
            assert commitment.open  # the promise itself is untouched
            interrupted = runtime.events.last(ET.ACTION_INTERRUPTED)
            assert interrupted is not None

            # she deals with the interruption, then comes back to the promise
            assert (
                await runtime._start_action("watch_animation", duration_minutes=10)  # noqa: SLF001
                is not None
            )
            breaker = runtime.current_action
            assert breaker is not None
            breaker.planned_end_at = clock.now
            clock.advance(1)
            await runtime.tick(minutes=1)
            await runtime.wait_social()

            resumed = runtime.events.last(ET.ACTION_RESUMED)
            assert resumed is not None  # she picked the promise back up
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "play_minecraft"
            # §27: the goal step follows the new instance, the commitment stays open
            assert runtime.current_action.id != instance_before
            assert step.action_instance_id == runtime.current_action.id
            assert commitment.open
            assert goal.status is not GoalStatus.cancelled
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------ Test 16/17/§22/§46


class TestRelationshipConsequence:
    async def test_fulfilled_commitment_moves_the_relationship_up(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9018", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            person = commitment.person_id
            before = await runtime.relationships_dyn.get(person)
            assert before is not None
            trust_before, interactions_before = before.trust, before.positive_interactions

            assert runtime.commitments.fulfill(commitment, duration_minutes=45.0)
            await runtime.wait_social()
            after = await runtime.relationships_dyn.get(person)
            assert after is not None
            assert after.trust > trust_before
            assert after.positive_interactions > interactions_before
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            assert changed is not None and changed.payload["interaction_type"] == (
                "commitment_fulfilled"
            )
            # §46: only through the Phase 8 engine's own rules — never a direct write
            assert runtime.social.applied >= 1
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- Test 18/19/§32/§33


class TestMemoryBoundary:
    async def test_creating_a_commitment_is_not_a_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            await promise(runtime, "9019", "今晚一起打游戏")
            await runtime.flush_experiences()
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert "commitment_outcome" not in kinds  # §32: creation is not an episode
            assert await runtime.memory.count() == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_major_commitment_outcome_becomes_an_experience_and_memory(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9020", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            clock.advance(commitment.due_at + 3 * 3600.0 - clock.now)
            runtime.commitments.sweep()  # → broken (major)
            await runtime.flush_experiences()
            records = [
                record
                for record in runtime.experiences.emitted()
                if record.kind.value == "commitment_outcome"
            ]
            assert records, "a missed promise is a lived episode (§32)"
            candidates = runtime.candidates.from_experience(records[0], now=clock.now)
            assert candidates, "the episode becomes a memory candidate (§47)"
            assert "commitment:" in str(candidates[0].dedupe_key)
            await runtime.memory.ingest_all(candidates)
            assert await runtime.memory.count() >= 1
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------- Test 20/21/§30/§31


class TestPromptSurfaces:
    async def test_continuity_carries_at_most_three_active_commitments(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            for index in range(5):
                await promise(runtime, f"902{index}", "今晚一起打游戏")
            assert len(runtime.commitments.open()) == 5
            snapshot = await runtime.build_continuity()
            assert len(snapshot.active_commitments) == 3  # §30: important few, never all
            due_order = [item["due_at"] for item in snapshot.active_commitments]
            assert due_order == sorted(due_order)  # closest first
            assert all(item["description"] for item in snapshot.active_commitments)
            # history never leaks either: a fulfilled one drops out
            runtime.commitments.fulfill(runtime.commitments.open()[0], duration_minutes=20)
            snapshot = await runtime.build_continuity()
            assert len(snapshot.active_commitments) == 3
            assert all(
                item["commitment_id"] != runtime.commitments.all()[0].commitment_id
                or runtime.commitments.all()[0].open
                for item in snapshot.active_commitments
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_cognitive_context_shows_promises_with_the_speaker(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            await promise(runtime, "9031", "今晚一起打游戏")
            await promise(
                runtime, "9032", "明天帮我看看电脑", activity="", interaction_type="promise_made"
            )
            payload = (
                await runtime.cognitive_context(query="你今晚还来吗", relationship_target="9031")
            ).as_prompt_payload()
            commitments = payload["commitments"]
            assert len(commitments) == 1  # §31: only the current speaker's promises
            assert commitments[0]["kind"] == "shared_activity"
            assert commitments[0]["commitment_id"] == runtime.commitments.open()[0].commitment_id
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------ Test 22/23/§45 LLM boundary


class TestLlmBoundary:
    async def test_the_lifecycle_never_calls_a_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)  # no engine at all
        try:
            await promise(runtime, "9023", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert runtime.decisions.llm_calls == 0  # §45: detection/creation are deterministic
            assert runtime.current_action is not None  # a single legal step is deterministic
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_model_only_decides_between_legal_ways_to_keep_a_promise(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine(
            ['{"candidate_id": "action:play_minecraft", "confidence": 0.9}']
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await promise(runtime, "9024", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            assert runtime.decisions.llm_calls == 1  # several legal candidates → gate opens
            assert provider.calls, "the model saw the candidates"
            prompt = provider.calls[0]["last_user"]
            assert "play_minecraft" in prompt
            assert runtime.current_action.definition_id == "play_minecraft"  # type: ignore[union-attr]
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------- Test 25 / cross-character


class TestCrossCharacterOutcomeIsolation:
    async def test_a_fulfilled_promise_in_one_world_leaves_the_other_alone(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            await promise(first, "9025", "今晚一起打游戏")
            await promise(second, "9025", "今晚一起打游戏")
            mine = first.commitments.open()[0]
            theirs = second.commitments.open()[0]
            first.commitments.fulfill(mine, duration_minutes=30.0)
            await first.wait_social()
            assert mine.status is CommitmentStatus.completed
            assert theirs.status is CommitmentStatus.pending  # untouched
            assert theirs.person_id == mine.person_id  # same person, two worlds
            assert theirs.character_id != mine.character_id
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()


# -------------------------------------------------------- §44 autonomous sim


class TestAutonomousSimulation:
    async def test_24h_without_qq_creates_no_commitments(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            for _ in range(24 * 6):  # 24h at 10-minute ticks
                clock.advance(600)
                await runtime.tick(minutes=10)
            assert runtime.commitments.all() == []  # no contact, no phantom promises
            assert runtime.commitment_detector.created == 0
            assert runtime.commitment_bridge.due() == []
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_scheduled_promise_runs_to_fulfillment_and_then_stops(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§44: promise → goal → action → fulfilment → nothing left to do."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9040", "今晚一起打游戏")
            commitment = runtime.commitments.open()[0]
            commitment_id = commitment.commitment_id
            advance_to_window(runtime, clock, commitment)
            await runtime.tick(minutes=1)
            action = runtime.current_action
            assert action is not None
            # the shared activity runs long enough to count (§20)
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()
            assert runtime.commitments.get(commitment_id).status is (CommitmentStatus.completed)

            # §41: one thread runs through the whole life of the promise
            created = runtime.events.last(ET.COMMITMENT_CREATED)
            activated = runtime.events.last(ET.COMMITMENT_ACTIVATED)
            fulfilled = runtime.events.last(ET.COMMITMENT_FULFILLED)
            assert None not in (created, activated, fulfilled)
            correlation = {
                created.correlation_id,
                activated.correlation_id,
                fulfilled.correlation_id,
            }
            assert len(correlation) == 1 and correlation != {""}
            # every link points back at the *verified fact* the promise came from
            social = next(
                event
                for event in runtime.events.of_type(ET.SOCIAL_INTERACTION)
                if event.payload.get("interaction_type") == "invitation_accepted"
            )
            goal_created = runtime.events.last(ET.GOAL_CREATED)
            assert goal_created is not None
            assert created.causation_id == social.event_id  # promise ← fact (§8)
            assert goal_created.causation_id == social.event_id  # goal ← fact (§15)
            assert goal_created.correlation_id.startswith("goal_")
            assert runtime.events.last(ET.COMMITMENT_ACTIVATED) is not None

            # 48 more hours of life produce no further promise and no busy loop
            for _ in range(48 * 6):
                clock.advance(600)
                await runtime.tick(minutes=10)
            assert runtime.commitments.created == 1  # no phantom promises appear
            assert len(runtime.commitments.all()) == 1
            assert not [g for g in fulfill_goals(runtime) if not g.status.terminal]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_same_seed_replays_the_same_promise_sequence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        outcomes = []
        for run in range(2):
            db = await make_db(tmp_path / f"run{run}")
            clock = Clock()
            runtime = await make_sandbox(db=db, clock=clock)
            try:
                await promise(runtime, "9041", "今晚一起打游戏")
                commitment = runtime.commitments.open()[0]
                clock.advance(commitment.due_at + 3 * 3600.0 - clock.now)
                runtime.commitments.sweep()
                await runtime.wait_social()
                state = await runtime.relationships_dyn.get(commitment.person_id)
                outcomes.append(
                    (
                        commitment.commitment_id.startswith("cm_"),
                        round(commitment.due_at, 3),
                        commitment.status.value,
                        commitment.revision,
                        round(state.trust, 6) if state else 0.0,
                        len(runtime.goals.all()),
                    )
                )
            finally:
                await runtime.shutdown()
                await db.close()
        assert outcomes[0] == outcomes[1]  # deterministic given the same facts


# ------------------------------------------------------------ helper sanity


class TestTimeParsing:
    def test_the_closed_vocabulary_parses_and_guessing_is_refused(self) -> None:
        now = 1_700_000_000.0
        tonight = parse_time_hint("今晚8点一起打游戏", now=now)
        assert tonight is not None and tonight.due_at > now
        assert parse_time_hint("下次一定", now=now) is None
        assert parse_time_hint("", now=now) is None
        window = parse_time_hint("30分钟后开一把", now=now)
        assert window is not None
        assert abs(window.due_at - (now + 1800.0)) < 1e-6
        # an already-past clock time rolls to tomorrow instead of guessing "now"
        assert parse_time_hint("凌晨3点", now=now).due_at > now  # type: ignore[union-attr]

    def test_shared_activity_needs_a_real_duration(self) -> None:
        assert MIN_FULFILL_DURATION_MINUTES >= 1.0  # §20: a dabble is not a kept promise
