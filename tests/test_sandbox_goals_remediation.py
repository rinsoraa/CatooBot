"""Phase 7 remediation tests: goal state recovery + action instance identity.

Three audits drove this round:

1. a blocked goal that becomes legal again after its cooldown must return to
   ``active`` (same goal id, same correlation — no clone);
2. ``GoalStep`` tracks the concrete ``ActionInstance`` it waits for, so that two
   goals sharing an action *definition* cannot both advance from one
   completion, and an interrupt/resume rebinds the step to the new instance;
3. the startup sweep discovers depleted resources through the world's own
   restock actions — not through leftover zero-count keys in the inventory map.
"""

from __future__ import annotations

from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import (
    RETRY_COOLDOWN_SECONDS,
    GoalKind,
    GoalStatus,
    GoalStep,
)
from tests.test_sandbox_goals import Clock, drink_last_cola, goals_of, make_db, make_sandbox


async def break_shops(runtime) -> None:  # type: ignore[no-untyped-def]
    """Remove the shops so an errand step cannot start (blocked path)."""
    from app.sandbox.world import SpaceSystem

    runtime.spaces = SpaceSystem(
        [
            space
            for space in runtime.spaces.all()
            if space.id not in ("convenience_store", "dessert_shop")
        ]
    )


async def restore_shops(runtime, db) -> None:  # type: ignore[no-untyped-def]
    """Rebuild the world's spaces from the seed (the shops come back)."""
    from app.sandbox.seed import build_spaces
    from app.sandbox.world import SpaceSystem

    runtime.spaces = SpaceSystem(build_spaces(runtime.seed))


# ------------------------------------------------------------------ §1


class TestBlockedGoalReturnsToActiveAfterSuccessfulRetry:
    async def test_blocked_goal_reactivates_without_being_recreated(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            original_id = goal.goal_id
            original_correlation = f"goal_{original_id}"

            # attempt fails → blocked
            await break_shops(runtime)
            clock.advance(10)
            await runtime.goals.advance()
            assert goal.status is GoalStatus.blocked
            assert runtime.events.last(ET.GOAL_BLOCKED) is not None

            # the world becomes legal again; the cooldown expires → retry works
            await restore_shops(runtime, db)
            clock.advance(RETRY_COOLDOWN_SECONDS + 1)
            drove = await runtime.goals.advance()

            assert drove is True
            assert goal.goal_id == original_id  # same goal, no clone
            assert goal.status is GoalStatus.active  # §1: not left blocked
            assert goal.current_step is not None
            assert runtime.current_action is not None
            activations = [
                event
                for event in runtime.events.of_type(ET.GOAL_ACTIVATED)
                if event.payload.get("goal_id") == original_id
            ]
            assert any(
                event.payload.get("reason") == "resumed_after_cooldown" for event in activations
            )
            # the block stays in the audit history
            blocked_events = [
                event
                for event in runtime.events.of_type(ET.GOAL_BLOCKED)
                if event.payload.get("goal_id") == original_id
            ]
            assert blocked_events
            # one correlation thread for the whole goal lifetime
            assert {event.correlation_id for event in activations} == {original_correlation}
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- §2 / §5-6


class TestGoalStepUsesActionInstanceIdentity:
    async def test_completion_only_advances_the_matching_instance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            goal_a = runtime.goals.create(
                kind=GoalKind.restock_resource,
                source=runtime.goal_detector
                and __import__(
                    "app.sandbox.goals", fromlist=["GoalSource"]
                ).GoalSource.unfinished_task,
                source_event_id="",
                reason="test",
                priority=0.6,
                target_item="A",
                metadata={"inventory_key": "ka", "desired_quantity": 1},
            )
            goal_b = runtime.goals.create(
                kind=GoalKind.restock_resource,
                source=__import__(
                    "app.sandbox.goals", fromlist=["GoalSource"]
                ).GoalSource.unfinished_task,
                source_event_id="",
                reason="test",
                priority=0.5,
                target_item="B",
                metadata={"inventory_key": "kb", "desired_quantity": 1},
            )
            shared_action = "same_action"
            for goal_id, instance in ((goal_a, "inst_A"), (goal_b, "inst_B")):
                goal = runtime.goals._goals[goal_id]  # noqa: SLF001
                goal.current_step = GoalStep(
                    step_id=f"step_{instance}",
                    goal_id=goal_id,
                    action_id=shared_action,
                    action_instance_id=instance,
                    status=__import__(
                        "app.sandbox.goals", fromlist=["StepStatus"]
                    ).StepStatus.active,
                )

            runtime.events.publish(
                ET.ACTION_COMPLETED,
                source="character",
                target=shared_action,
                payload={"action_id": shared_action, "action_instance_id": "inst_A"},
            )

            step_a = runtime.goals._goals[goal_a].current_step  # noqa: SLF001
            step_b = runtime.goals._goals[goal_b].current_step  # noqa: SLF001
            assert step_a.status.value == "completed"
            assert step_b.status.value == "active"  # untouched — the core regression
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ambiguous_fallback_does_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§6: the action-id fallback must not fire when two steps could match."""
        from app.sandbox.goals import GoalSource, StepStatus

        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            ids = [
                runtime.goals.create(
                    kind=GoalKind.restock_resource,
                    source=GoalSource.unfinished_task,
                    source_event_id="",
                    reason="test",
                    priority=0.5,
                    target_item=item,
                    metadata={"inventory_key": f"k{item}", "desired_quantity": 1},
                )
                for item in ("A", "B")
            ]
            for goal_id in ids:
                goal = runtime.goals._goals[goal_id]  # noqa: SLF001
                goal.current_step = GoalStep(
                    step_id=f"step_{goal_id}",
                    goal_id=goal_id,
                    action_id="shared",
                    status=StepStatus.active,
                )
            # legacy event without an instance id and two candidate steps
            runtime.goals.on_action_completed("shared", action_instance_id="")
            assert all(
                runtime.goals._goals[goal_id].current_step.status is StepStatus.active  # noqa: SLF001
                for goal_id in ids
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_started_and_completed_share_the_instance_id(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            started = runtime.events.last(ET.ACTION_STARTED)
            assert started is not None
            instance_id = started.payload["action_instance_id"]
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            assert goal.current_step is not None
            assert goal.current_step.action_instance_id == instance_id

            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            await runtime.tick(minutes=1)
            completed = runtime.events.last(ET.ACTION_COMPLETED)
            assert completed is not None
            assert completed.payload["action_instance_id"] == instance_id
            assert goal.current_step.status.value == "completed"
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------------- §7


class TestInterruptedGoalRebindsResumedActionInstance:
    async def test_resume_rebinds_the_step_to_the_new_instance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            assert goal.current_step is not None
            instance_a = goal.current_step.action_instance_id
            assert instance_a

            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="remediation-inv",
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
            assert runtime.events.last(ET.ACTION_INTERRUPTED) is not None

            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)

            resumed = runtime.events.last(ET.ACTION_RESUMED)
            assert resumed is not None
            instance_b = runtime.current_action.id  # type: ignore[union-attr]
            assert instance_b != instance_a
            assert goal.current_step.action_instance_id == instance_b  # §7 rebound
            assert goal.current_step.status.value == "active"  # still the same step

            # completing the resumed instance advances the goal, not any other
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            assert goal.current_step.status.value == "completed"
            assert goal.status is GoalStatus.completed  # items came back
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------------ §10-13


class TestStartupSweepRecoversUnpersistedDepletedResource:
    async def test_sweep_finds_depletion_without_a_leftover_zero_key(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        target_item = ""
        try:
            key = runtime._fridge_key  # noqa: SLF001
            target_item = runtime._drink_item  # noqa: SLF001
            inventory = runtime.inventories.get(key)
            # the item vanished entirely from the mapping (no zero-count key)
            remaining = dict(inventory.items)
            remaining.pop(target_item, None)
            inventory.items = remaining
            # persist this world state so the restart sees it
            await runtime._snapshot()  # noqa: SLF001 - deterministic persistence
            await runtime.shutdown()
        finally:
            pass

        # simulate a lost goal table (crash between world write and goal write):
        # the world says "depleted", but nothing remembers why
        await db.execute("DELETE FROM sandbox_goals")
        rows = await db.fetchall(
            "SELECT goal_id FROM sandbox_goals WHERE kind = 'restock_resource'"
        )
        assert rows == []

        revived = await make_sandbox(db=db, clock=clock)
        try:
            assert revived.inventories.get(revived._fridge_key).count(target_item) == 0  # noqa: SLF001
            restock = goals_of(revived, GoalKind.restock_resource)
            assert [goal.target_item for goal in restock] == [target_item]
            assert restock[0].metadata["inventory_key"] == revived._fridge_key  # noqa: SLF001
            # ...and the sweep stays deduped on a second run
            revived.goal_detector.sweep()
            assert len(goals_of(revived, GoalKind.restock_resource)) == 1
        finally:
            await revived.shutdown()
            await db.close()

    async def test_non_restockable_items_never_become_goals(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Only items some owned action can replenish are goals (§11)."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            targets = {item for _key, item in runtime.goal_detector._restockable_targets()}  # noqa: SLF001
            assert runtime._drink_item in targets  # noqa: SLF001
            # every target is backed by a real owned action
            for key, item in runtime.goal_detector._restockable_targets():  # noqa: SLF001
                assert runtime.restock_actions(key, item), (key, item)
        finally:
            await runtime.shutdown()
            await db.close()
