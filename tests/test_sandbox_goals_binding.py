"""Phase 7.1 remediation tests: resume binding integrity.

Two audits drove this round:

1. ``rebind_instance`` had no status guard — a failed/blocked/pending step or a
   non-active goal could be silently adopted. It now matches strictly (active
   goal + active step + same action + the *actual* interrupted instance) and
   refuses ambiguous or instance-less calls instead of guessing.
2. the new binding must reach the database before resume returns, so a crash
   right after cannot resurrect the stale instance id.
"""

from __future__ import annotations

from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalSource, GoalStatus, GoalStep, StepStatus
from tests.test_sandbox_goals import Clock, drink_last_cola, goals_of, make_db, make_sandbox


def add_step(
    runtime, goal_id: str, *, action_id: str, instance: str, step_status, goal_status=None
):  # type: ignore[no-untyped-def]
    goal = runtime.goals._goals[goal_id]  # noqa: SLF001
    goal.current_step = GoalStep(
        step_id=f"step_{goal_id}",
        goal_id=goal_id,
        action_id=action_id,
        action_instance_id=instance,
        status=step_status,
    )
    if goal_status is not None:
        goal.status = goal_status
    return goal


def make_goal(runtime, target: str):  # type: ignore[no-untyped-def]
    return runtime.goals.create(
        kind=GoalKind.restock_resource,
        source=GoalSource.unfinished_task,
        source_event_id="",
        reason="test",
        priority=0.5,
        target_item=target,
        metadata={"inventory_key": f"k{target}", "desired_quantity": 1},
    )


# ------------------------------------------------------------------ §6-§8


class TestResumeRebindOnlyTouchesActiveStep:
    async def test_blocked_or_failed_step_is_never_adopted(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            # Goal A: blocked + failed step, no instance (the §6 regression case)
            goal_a = make_goal(runtime, "A")
            add_step(
                runtime,
                goal_a,
                action_id="same",
                instance="",
                step_status=StepStatus.failed,
                goal_status=GoalStatus.blocked,
            )
            # Goal B: active + active step, holding the interrupted instance
            goal_b = make_goal(runtime, "B")
            add_step(
                runtime,
                goal_b,
                action_id="same",
                instance="OLD",
                step_status=StepStatus.active,
                goal_status=GoalStatus.active,
            )

            assert (
                await runtime.goals.rebind_instance(
                    "same", old_instance_id="OLD", new_instance_id="NEW"
                )
                is True
            )
            step_a = runtime.goals._goals[goal_a].current_step  # noqa: SLF001
            step_b = runtime.goals._goals[goal_b].current_step  # noqa: SLF001
            assert step_a.action_instance_id == ""  # untouched
            assert step_b.action_instance_id == "NEW"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_active_goal_with_failed_step_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            goal_id = make_goal(runtime, "A")
            add_step(
                runtime,
                goal_id,
                action_id="same",
                instance="OLD",
                step_status=StepStatus.failed,
                goal_status=GoalStatus.active,
            )
            assert (
                await runtime.goals.rebind_instance(
                    "same", old_instance_id="OLD", new_instance_id="NEW"
                )
                is False
            )
            assert runtime.goals._goals[goal_id].current_step.action_instance_id == "OLD"  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_blocked_goal_with_active_step_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            goal_id = make_goal(runtime, "A")
            add_step(
                runtime,
                goal_id,
                action_id="same",
                instance="OLD",
                step_status=StepStatus.active,
                goal_status=GoalStatus.blocked,
            )
            assert (
                await runtime.goals.rebind_instance(
                    "same", old_instance_id="OLD", new_instance_id="NEW"
                )
                is False
            )
            assert runtime.goals._goals[goal_id].current_step.action_instance_id == "OLD"  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_missing_old_instance_is_refused_without_changes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            goal_id = make_goal(runtime, "A")
            add_step(
                runtime,
                goal_id,
                action_id="same",
                instance="",
                step_status=StepStatus.active,
                goal_status=GoalStatus.active,
            )
            snapshot = runtime.goals._goals[goal_id].current_step.model_dump()  # noqa: SLF001
            assert (
                await runtime.goals.rebind_instance(
                    "same", old_instance_id="", new_instance_id="NEW"
                )
                is False
            )
            assert runtime.goals._goals[goal_id].current_step.model_dump() == snapshot  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ambiguous_match_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Two active steps holding the same instance id → refuse, change nothing."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            ids = []
            for target in ("A", "B"):
                goal_id = make_goal(runtime, target)
                add_step(
                    runtime,
                    goal_id,
                    action_id="same",
                    instance="OLD",
                    step_status=StepStatus.active,
                    goal_status=GoalStatus.active,
                )
                ids.append(goal_id)
            assert (
                await runtime.goals.rebind_instance(
                    "same", old_instance_id="OLD", new_instance_id="NEW"
                )
                is False
            )
            assert all(
                runtime.goals._goals[goal_id].current_step.action_instance_id == "OLD"  # noqa: SLF001
                for goal_id in ids
            )
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- §13/§14


class TestResumeRebindPersistsBeforeNextTick:
    async def test_new_instance_reaches_the_database_immediately(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        goal_id = ""
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            goal_id = goal.goal_id
            assert goal.current_step is not None
            instance_a = goal.current_step.action_instance_id

            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p71-inv",
                    source=ExternalSource.qq,
                    actor_id="u-core",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)

            instance_b = runtime.current_action.id  # type: ignore[union-attr]
            assert runtime.events.last(ET.ACTION_RESUMED) is not None

            # read the row *without* a further tick/flush: the write already happened
            row = await db.fetchone(
                "SELECT current_step FROM sandbox_goals WHERE goal_id = ?", (goal_id,)
            )
            assert row is not None
            import json

            stored = json.loads(row["current_step"])
            assert stored["action_instance_id"] == instance_b  # §13

            # a completion carrying the *old* instance must not advance the step
            runtime.events.publish(
                ET.ACTION_COMPLETED,
                source="character",
                target="buy_cola",
                payload={"action_id": "buy_cola", "action_instance_id": instance_a},
            )
            assert goal.current_step.status is StepStatus.active
            # ...while the live instance still does
            assert goal.current_step.action_instance_id == instance_b
            assert runtime.goals._dirty == set()  # type: ignore[attr-defined]  # write succeeded
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_rebuilt_runtime_reads_the_new_instance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Crash/restart: the goal reloads with the resumed instance id (§13)."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            original_a = goal.current_step.action_instance_id  # type: ignore[union-attr]

            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p71-inv2",
                    source=ExternalSource.qq,
                    actor_id="u-core",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            instance_b = runtime.current_action.id  # type: ignore[union-attr]
            goal_id = goal.goal_id
        finally:
            await runtime.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            reloaded = next(g for g in revived.goals.open_goals() if g.goal_id == goal_id)
            assert reloaded.current_step is not None
            assert reloaded.current_step.action_instance_id == instance_b
            assert reloaded.current_step.action_instance_id != original_a
        finally:
            await revived.shutdown()
            await db.close()
