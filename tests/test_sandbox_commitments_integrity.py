"""Phase 9.1 tests (§7/§8/§13/§14/§18/§19/§22/§27): commitment outcome integrity.

Four contracts:

* a kept promise closes its **exact** goal (and is never cancelled as stale);
* a shared activity fulfils a promise only by ``commitment_id`` — or, without
  one, only when exactly one open promise fits *and* the real window is open;
* a reschedule moves only the commitment it names (ambiguity changes nothing);
* the fulfilment fact requires the **exact** ActionInstance (Phase 7.1 rules),
  and the three states (commitment / goal / step) survive a restart together.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.commitments import MIN_FULFILL_DURATION_MINUTES
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalSource, GoalStatus, GoalStep, StepStatus
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


async def make_sandbox(*, db, clock, bible_path=None, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


async def promise(  # type: ignore[no-untyped-def]
    runtime, qq: str, hint: str, *, activity: str = "gaming", kind: str = ""
):
    person = runtime.persons.for_qq(qq).person_id
    metadata: dict[str, object] = {"time_hint": hint, "target_activity": activity}
    if kind:
        metadata["commitment_kind"] = kind
    await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person,
            interaction_type="invitation_accepted",
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=InteractionSignificance.meaningful,
            external_ids={"qq": qq},
            metadata=metadata,
        )
    )
    return runtime.commitments.open()[-1]


async def shared_activity(  # type: ignore[no-untyped-def]
    runtime,
    person_id: str,
    *,
    activity: str = "gaming",
    minutes: float = 30.0,
    commitment_id: str = "",
):
    """A *verified* shared activity, exactly as the runtime would emit it."""
    metadata: dict[str, object] = {
        "target_activity": activity,
        "duration_minutes": minutes,
        "commitment_id": commitment_id,
    }
    return await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person_id,
            interaction_type="shared_activity",
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            outcome="completed",
            significance=InteractionSignificance.meaningful,
            metadata=metadata,
        )
    )


def fulfill_goals(runtime) -> list:  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.fulfill_commitment]


def advance_to_window(clock, commitment) -> None:  # type: ignore[no-untyped-def]
    clock.advance(max(0.0, float(commitment.earliest_at) + 60.0 - float(clock.now)))


# ------------------------------------------------------------ §7/§8/§27


class TestFulfilledCommitmentCompletesExactGoal:
    async def test_kept_promise_closes_go_goal_and_step(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9101", "今晚一起打游戏")
            advance_to_window(clock, commitment)
            await runtime.tick(minutes=1)
            assert len(fulfill_goals(runtime)) == 1
            goal = fulfill_goals(runtime)[0]
            assert goal.current_step is not None  # the step really started
            assert runtime.current_action is not None
            action = runtime.current_action
            assert goal.current_step.action_instance_id == action.id

            # the agreed activity runs to its planned end
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()

            # §5: all three states hold at once
            assert commitment.status.value == "completed"
            assert goal.status is GoalStatus.completed
            assert goal.progress == 1.0
            assert goal.current_step is not None
            assert goal.current_step.status is StepStatus.completed

            completed = runtime.events.last(ET.GOAL_COMPLETED)
            fulfilled = runtime.events.last(ET.COMMITMENT_FULFILLED)
            assert completed is not None and fulfilled is not None
            assert completed.payload["commitment_id"] == commitment.commitment_id
            assert completed.causation_id == fulfilled.event_id  # §6 direct causation
            assert completed.correlation_id == f"goal_{goal.goal_id}"  # §26: its own thread
            assert "commitment_id" not in str(completed.payload.get("reason", ""))
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_kept_promise_is_never_cancelled_as_stale(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§8: a successful fulfilment must not be undone by the stale sweep."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9102", "今晚一起打游戏")
            advance_to_window(clock, commitment)
            await runtime.tick(minutes=1)
            goal = fulfill_goals(runtime)[0]
            action = runtime.current_action
            assert action is not None
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()

            assert runtime.commitment_bridge.cancel_stale() == []
            assert goal.status is GoalStatus.completed
            assert runtime.events.last(ET.GOAL_CANCELLED) is None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_commitment_goal_and_step_survive_a_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§27: never "commitment completed / goal active" after a reload."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9103", "今晚一起打游戏")
            advance_to_window(clock, commitment)
            await runtime.tick(minutes=1)
            goal = fulfill_goals(runtime)[0]
            action = runtime.current_action
            assert action is not None
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()
            commitment_id, goal_id = commitment.commitment_id, goal.goal_id
            await runtime.commitments.flush()
            await runtime.goals.flush()
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            # §27: the three states are consistent *in storage* — a completed
            # promise must never come back as "commitment done / goal active"
            commitment_row = await db.fetchone(
                "SELECT status FROM sandbox_commitments WHERE commitment_id = ?",
                (commitment_id,),
            )
            assert commitment_row is not None and commitment_row["status"] == "completed"
            goal_row = await db.fetchone(
                "SELECT status, progress, current_step FROM sandbox_goals WHERE goal_id = ?",
                (goal_id,),
            )
            assert goal_row is not None
            assert goal_row["status"] == "completed"
            assert float(goal_row["progress"]) == 1.0
            step = json.loads(goal_row["current_step"] or "{}")
            assert step.get("status") == "completed"
            # and the restart does not resurrect it: no goal, no second run
            assert fulfill_goals(restored) == []
        finally:
            await restored.shutdown()
            await db.close()


# ------------------------------------------------------- §9-§14 matching rules


class TestSharedActivityMatching:
    async def test_future_commitment_is_not_fulfilled_early(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§13: same person, same activity, but the window has not opened."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9104", "今晚一起打游戏")
            assert clock.now < commitment.earliest_at  # it is still afternoon
            await shared_activity(runtime, commitment.person_id, minutes=30.0)
            assert commitment.open  # nothing was fulfilled
            assert runtime.commitment_detector.fulfilled == 0
            assert commitment.status.value != "completed"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ambiguous_shared_activity_fulfils_nothing(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        """§14: two promises for the same activity → the fact cannot pick one."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            first = await promise(runtime, "9105", "今晚一起打游戏")
            second = await promise(runtime, "9105", "明天晚上一起打游戏")
            assert first.commitment_id != second.commitment_id
            with caplog.at_level(logging.WARNING, logger="CatooBot.Sandbox"):
                await shared_activity(runtime, first.person_id, minutes=30.0)
            assert first.open and second.open  # neither was guessed
            assert runtime.commitment_detector.fulfilled == 0
            assert runtime.commitment_detector.ambiguous >= 1
            assert "fits several open promises" in caplog.text
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_single_matching_promise_is_fulfilled_without_an_id(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§11: the fallback still works when exactly one promise fits."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9106", "今晚一起打游戏")
            advance_to_window(clock, commitment)
            await shared_activity(runtime, commitment.person_id, minutes=45.0)
            assert commitment.status.value == "completed"
            assert runtime.commitment_detector.fulfilled == 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_exact_commitment_id_fulfils_without_any_matching(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10: the goal-driven fact names the promise — nothing is inferred."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            first = await promise(runtime, "9107", "今晚一起打游戏")
            second = await promise(runtime, "9107", "明天晚上一起打游戏")
            advance_to_window(clock, first)
            await shared_activity(
                runtime,
                first.person_id,
                minutes=MIN_FULFILL_DURATION_MINUTES + 5,
                commitment_id=second.commitment_id,  # the *other* promise, exactly named
            )
            assert second.status.value == "completed"
            assert first.open  # the named one moved, the twin did not
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ §15-§19 reschedule


class TestRescheduleTargeting:
    async def test_reschedule_moves_only_the_named_commitment(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            gaming = await promise(runtime, "9108", "今晚一起打游戏")
            movie = await promise(
                runtime, "9108", "今晚一起看电影", activity="movie", kind="appointment"
            )
            assert gaming.commitment_id != movie.commitment_id
            movie_revision, movie_due = movie.revision, movie.due_at
            gaming_revision, gaming_due = gaming.revision, gaming.due_at

            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=movie.person_id,
                    interaction_type="appointment_rescheduled",
                    source="test",
                    timestamp=runtime._clock(),  # noqa: SLF001
                    metadata={
                        "time_hint": "明天晚上一起看电影",
                        "commitment_id": movie.commitment_id,
                    },
                )
            )
            assert movie.revision == movie_revision + 1
            assert movie.due_at != movie_due
            assert gaming.revision == gaming_revision  # untouched
            assert gaming.due_at == gaming_due
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ambiguous_reschedule_changes_nothing(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        """§19: two open promises and no commitment_id → refuse, and say so."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            first = await promise(runtime, "9109", "今晚一起打游戏")
            second = await promise(runtime, "9109", "明天晚上一起打游戏")
            before = [(c.commitment_id, c.revision, c.due_at) for c in (first, second)]
            with caplog.at_level(logging.WARNING, logger="CatooBot.Sandbox"):
                await runtime.apply_social_interaction(
                    SocialInteractionFact.create(
                        character_id=runtime.character_id,
                        person_id=first.person_id,
                        interaction_type="appointment_rescheduled",
                        source="test",
                        timestamp=runtime._clock(),  # noqa: SLF001
                        metadata={"time_hint": "明天晚上一起打游戏"},
                    )
                )
            after = [(c.commitment_id, c.revision, c.due_at) for c in (first, second)]
            assert after == before  # zero state change
            assert runtime.commitment_detector.ambiguous >= 1
            assert "fits several open promises" in caplog.text
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_single_open_promise_reschedules_without_an_id(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9110", "今晚一起打游戏")
            revision = commitment.revision
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=commitment.person_id,
                    interaction_type="appointment_rescheduled",
                    source="test",
                    timestamp=runtime._clock(),  # noqa: SLF001
                    metadata={"time_hint": "明天晚上一起打游戏"},
                )
            )
            assert commitment.revision == revision + 1
            assert commitment.metadata["schedule_history"]
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- §20-§22 exact ActionInstance


class TestCommitmentSharedActivityRequiresExactActionInstance:
    async def test_empty_step_instance_never_produces_a_shared_fact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§22/§21: an unbound step is a wildcard and must be refused."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            commitment = await promise(runtime, "9111", "今晚一起打游戏")
            advance_to_window(clock, commitment)
            goal_id = runtime.goals.create(
                kind=GoalKind.fulfill_commitment,
                source=GoalSource.social_commitment,
                source_event_id=commitment.source_event_id,
                reason="test",
                priority=0.6,
                correlation_id=commitment.correlation_id,
                target_commitment=commitment.commitment_id,
                target_entity=commitment.person_id,
                metadata={
                    "commitment_id": commitment.commitment_id,
                    "commitment_revision": commitment.revision,
                    "target_activity": "gaming",
                },
            )
            goal = next(g for g in runtime.goals.all() if g.goal_id == goal_id)
            # a step that is *active* but not bound to any instance (§22)
            goal.current_step = GoalStep(
                step_id="step_test",
                goal_id=goal.goal_id,
                action_id="play_minecraft",
                action_instance_id="",
                status=StepStatus.active,
            )
            goal.status = GoalStatus.active

            started = await runtime._start_action("play_minecraft", duration_minutes=30)  # noqa: SLF001
            assert started is not None
            instance = runtime.current_action
            assert instance is not None
            clock.advance(max(0.0, instance.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=31)
            await runtime.wait_social()

            shared = [
                event
                for event in runtime.events.of_type(ET.SOCIAL_INTERACTION)
                if event.payload.get("interaction_type") == "shared_activity"
            ]
            assert shared == []  # no wildcard match, no fact
            assert commitment.open
        finally:
            await runtime.shutdown()
            await db.close()
