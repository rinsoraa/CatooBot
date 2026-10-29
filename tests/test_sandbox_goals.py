"""Phase 7 tests (§36): the goal-driven autonomous life loop.

Twenty checks + the two simulation checks: goals are born from world facts,
deduped, persisted, driven one step at a time through the normal Action System
and the Phase 6 decision pipeline, blocked with backoff instead of spinning,
and they survive restarts and interruptions.
"""

from __future__ import annotations

from pathlib import Path

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import (
    MAX_RETRIES,
    RETRY_COOLDOWN_SECONDS,
    GoalKind,
    GoalStatus,
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


async def drink_last_cola(runtime, clock) -> None:  # type: ignore[no-untyped-def]
    """Consume the final staple drink through the canonical path."""
    key, item = runtime._fridge_key, runtime._drink_item  # noqa: SLF001
    inventory = runtime.inventories.get(key)
    # "last bottle" means *this* item runs out — the rest of the fridge stays
    remaining = dict(inventory.items)
    remaining[item] = 1
    inventory.items = remaining
    started = await runtime._start_action("drink_cola")  # noqa: SLF001
    assert started is not None
    runtime.current_action.planned_end_at = clock.now
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001


def goals_of(runtime, kind: GoalKind) -> list:  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is kind]


async def finish_errand(runtime, clock, *, limit: int = 60):  # type: ignore[no-untyped-def]
    """Tick until the single-item errand really ends: shelf satisfied *and* home.

    §C parity: a satisfied shelf no longer closes the restock goal while she is
    still at the shop — the goal stays open and drives the visible walk home
    (``return_home``), so the errand closes exactly when she is back.
    """
    for _ in range(limit):
        goals = goals_of(runtime, GoalKind.restock_resource)
        if goals and goals[0].status.terminal:
            assert runtime.spaces.is_home(runtime.character.location)
            return goals[0]
        clock.advance(600)
        await runtime.tick(minutes=10)
    raise AssertionError("the single-item errand never finished")


# ---------------------------------------------------------------- Test 1/2


class TestRestockGoalCreation:
    async def test_inventory_depletion_creates_exactly_one_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)

            assert runtime.events.last(ET.INVENTORY_DEPLETED) is not None
            restock = goals_of(runtime, GoalKind.restock_resource)
            assert len(restock) == 1
            goal = restock[0]
            assert goal.target_item == runtime._drink_item  # noqa: SLF001
            assert goal.metadata["inventory_key"] == runtime._fridge_key  # noqa: SLF001
            assert goal.metadata["desired_quantity"] >= 1
            assert goal.source.value == "inventory_depleted"
            created = runtime.events.last(ET.GOAL_CREATED)
            depleted = runtime.events.last(ET.INVENTORY_DEPLETED)
            assert created is not None and depleted is not None
            assert created.causation_id == depleted.event_id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_duplicate_depletion_does_not_duplicate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            first = goals_of(runtime, GoalKind.restock_resource)
            # the detector sees the shortage fact again (as a restart/replay would)
            depleted = runtime.events.last(ET.INVENTORY_DEPLETED)
            runtime.goal_detector.observe(depleted)
            runtime.goal_detector.sweep()  # and a startup scan too
            again = goals_of(runtime, GoalKind.restock_resource)
            assert len(again) == 1
            assert again[0].goal_id == first[0].goal_id
            assert runtime.goals.detected >= 1
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 3


class TestGoalPersistence:
    async def test_goal_survives_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        goal_id = ""
        try:
            await drink_last_cola(runtime, clock)
            await runtime.goals.flush()
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            goal_id = goal.goal_id
        finally:
            await runtime.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            restored = [g for g in revived.goals.open_goals() if g.goal_id == goal_id]
            assert restored, "the goal did not survive the restart"
            assert restored[0].target_item == revived._drink_item  # noqa: SLF001
            assert restored[0].status in (GoalStatus.pending, GoalStatus.active, GoalStatus.blocked)
        finally:
            await revived.shutdown()
            await db.close()

    async def test_stale_persisted_step_is_replanned(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§34: a reloaded step whose action vanished is dropped, not trusted."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            from app.sandbox.goals import GoalStep

            goal.current_step = GoalStep(
                step_id="step_stale", goal_id=goal.goal_id, action_id="ghost_action"
            )
            await runtime.goals.flush()
        finally:
            await runtime.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            restored = [g for g in revived.goals.open_goals() if g.current_step]
            assert not [g for g in restored if g.current_step.action_id == "ghost_action"]
        finally:
            await revived.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 4


class TestGoalStepExecution:
    async def test_step_runs_through_the_action_system(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)

            assert runtime.current_action is not None
            action_id = runtime.current_action.definition_id
            assert action_id in {"buy_cola", "go_shopping_cola"}
            step_started = runtime.events.last(ET.GOAL_STEP_STARTED)
            assert step_started is not None and step_started.payload["action_id"] == action_id
            assert runtime.events.last(ET.ACTION_STARTED) is not None
            assert runtime.decisions.llm_calls == 0  # deterministic path (§16)

            # finish the purchase → items come back; the errand then closes
            # with the visible walk home (§C: satisfied shelf + back home)
            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            await runtime.tick(minutes=1)
            assert runtime.events.last(ET.ITEM_ACQUIRED) is not None
            goal = await finish_errand(runtime, clock)
            assert runtime.events.last(ET.GOAL_COMPLETED) is not None
            assert goal.status is GoalStatus.completed
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 5/6


class TestPetCareGoal:
    async def test_pet_hunger_creates_pet_care_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)
            pet_goals = goals_of(runtime, GoalKind.pet_care)
            assert len(pet_goals) == 1
            assert pet_goals[0].target_entity == runtime.pet.id  # type: ignore[union-attr]
            hungry = runtime.events.last(ET.PET_HUNGRY)
            created = runtime.events.last(ET.GOAL_CREATED)
            assert created is not None and hungry is not None
            assert created.causation_id == hungry.event_id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_pet_care_completes_after_pet_fed(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
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
            goal = goals_of(runtime, GoalKind.pet_care)[0]
            assert goal.status is GoalStatus.completed
            completed = runtime.events.last(ET.GOAL_COMPLETED)
            assert completed is not None and completed.payload["goal_kind"] == "pet_care"
            # the existing feed path did the work — the goal observed (§25)
            assert runtime.events.last(ET.PET_FED) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 7/8


class TestBlockedAndCooldown:
    async def test_blocked_goal_does_not_spin(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            # the shops cease to exist in this world → the step cannot start
            from app.sandbox.world import SpaceSystem

            runtime.spaces = SpaceSystem(
                [
                    space
                    for space in runtime.spaces.all()
                    if space.id not in ("convenience_store", "dessert_shop")
                ]
            )

            clock.advance(10)
            assert await runtime.goals.advance() is True or goal.retry_count >= 1
            first_attempts = goal.retry_count
            assert goal.status is GoalStatus.blocked
            blocked = runtime.events.last(ET.GOAL_BLOCKED)
            assert blocked is not None and blocked.payload["cooldown"] == RETRY_COOLDOWN_SECONDS

            # a second tick inside the cooldown must not try again
            clock.advance(60)
            await runtime.tick(minutes=1)
            assert goal.retry_count == first_attempts
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_retry_is_bounded_then_cancelled(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            # the shops are gone → every attempt fails and cools down (§24)
            from app.sandbox.world import SpaceSystem

            runtime.spaces = SpaceSystem(
                [
                    space
                    for space in runtime.spaces.all()
                    if space.id not in ("convenience_store", "dessert_shop")
                ]
            )
            for _ in range(MAX_RETRIES):
                clock.advance(RETRY_COOLDOWN_SECONDS + 1)
                await runtime.goals.advance()
            assert goal.status is GoalStatus.cancelled
            assert runtime.events.last(ET.GOAL_CANCELLED) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- Test 9/10


class TestPriority:
    async def test_multiple_goals_priority(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            # project goal exists from the startup sweep
            assert goals_of(runtime, GoalKind.complete_project)
            await drink_last_cola(runtime, clock)  # restock (0.6)
            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)  # pet care (0.8)

            active = runtime.goals.active_goal()
            assert active is not None and active.kind is GoalKind.pet_care
            # one goal drives at a time; the others stay open
            assert len(runtime.goals.open_goals()) >= 3
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_critical_pet_goal_outranks_project_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            project_goal = goals_of(runtime, GoalKind.complete_project)[0]
            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)
            pet_goal = goals_of(runtime, GoalKind.pet_care)[0]
            assert pet_goal.priority > project_goal.priority
            assert runtime.goals.active_goal().goal_id == pet_goal.goal_id  # type: ignore[union-attr]
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 11/12


class TestGoalInterruption:
    async def test_external_invitation_interrupts_goal_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            goal_action = runtime.current_action.definition_id

            # §30: a *critical* external fact outranks the errand in progress
            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="goal-inv",
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

            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "play_minecraft"
            assert runtime.events.last(ET.ACTION_INTERRUPTED) is not None
            # the goal is still open — it will continue later (§17)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            assert not goal.status.terminal
            assert goal_action in {"buy_cola", "go_shopping_cola"}
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_interrupted_goal_action_resumes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            goal_action = runtime.current_action.definition_id  # type: ignore[union-attr]

            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="goal-inv2",
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
            assert runtime.current_action.definition_id == "play_minecraft"  # type: ignore[union-attr]

            # finish the invitation action → Phase 3 resume brings the errand back
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == goal_action
            assert runtime.events.last(ET.ACTION_RESUMED) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 13


class TestGoalStepStaleness:
    async def test_stale_goal_step_proposal_is_rejected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            from app.sandbox.intent import DecisionTrigger, IntentProposal

            candidates = runtime.goals.step_candidates(goal)
            request = runtime.decisions._request(  # noqa: SLF001
                trigger=DecisionTrigger.goal_step, candidates=candidates
            )
            proposal = IntentProposal(
                request_id=request.request_id,
                candidate_id=candidates[0].candidate_id,
                confidence=1.0,
            )
            ok, _why = runtime.decisions.validator.validate(request, proposal)
            assert ok is True

            runtime.adjust_need("thirst", delta=0.3, source="test", reason="world_moves")
            ok, why = runtime.decisions.validator.validate(request, proposal)
            assert ok is False and why == "world_changed"
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 14/15


class TestCharacterIsolation:
    async def test_goals_are_character_scoped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            await drink_last_cola(first, clock)
            await first.goals.flush()
            assert goals_of(first, GoalKind.restock_resource)
            # 阿澈 shares the database but must not see 罐头's goals
            assert not goals_of(second, GoalKind.restock_resource)
            assert all(goal.character_id == second.character_id for goal in second.goals.all())
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_second_character_generates_no_alien_goals(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            assert runtime.pet is None
            # 阿澈's world: no pet, no project-advancing action, nothing restockable
            for goal in runtime.goals.all():
                assert goal.kind is not GoalKind.pet_care
                assert goal.target_item != "可乐"
                assert "Minecraft" not in runtime.goals.describe(goal)
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 16/17


class TestLlmBoundary:
    async def test_single_candidate_step_uses_no_llm(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine(['{"candidate_id": "action:buy_cola", "confidence": 0.9}'])
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            # leave exactly one restock action in this world
            del runtime.actions.definitions["go_shopping_cola"]
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "buy_cola"
            assert provider.calls == []
            assert runtime.decisions.llm_calls == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_multiple_candidates_go_through_phase_6(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine(
            ['{"candidate_id": "action:go_shopping_cola", "confidence": 0.9}']
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "go_shopping_cola"
            assert runtime.decisions.llm_calls == 1
            requested = runtime.events.last(ET.DECISION_REQUESTED)
            assert requested is not None and requested.payload["trigger"] == "goal_step"
            assert "补给" in provider.calls[0]["last_user"]  # the goal context line
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 18


class TestGoalCausation:
    async def test_goal_events_form_a_chain(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            clock.advance(10)
            await runtime.tick(minutes=1)

            depleted = runtime.events.last(ET.INVENTORY_DEPLETED)
            created = runtime.events.last(ET.GOAL_CREATED)
            activated = runtime.events.last(ET.GOAL_ACTIVATED)
            step = runtime.events.last(ET.GOAL_STEP_STARTED)
            assert None not in (depleted, created, activated, step)
            assert created.causation_id == depleted.event_id
            assert activated.causation_id == depleted.event_id
            assert step.causation_id == depleted.event_id
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            assert {created.payload["goal_id"], activated.payload["goal_id"]} == {goal.goal_id}
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- Test 19/20


class TestContinuityAndMemory:
    async def test_continuity_contains_active_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            snapshot = await runtime.build_continuity()
            goals = snapshot.active_goals
            assert goals, "active goals missing from the continuity snapshot"
            described = " ".join(str(goal.get("description", "")) for goal in goals)
            assert runtime._drink_item in described  # type: ignore[attr-defined]

            payload = (
                await runtime.cognitive_context(query=runtime._drink_item)  # type: ignore[attr-defined]
            ).as_prompt_payload()
            assert payload["continuity"]["goals"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_goal_created_is_not_memory_but_completion_is(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await drink_last_cola(runtime, clock)
            await runtime.flush_experiences()
            # creation alone never becomes an experience/memory (§20); the
            # drink's own first-time knowledge may, but nothing goal-shaped
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert "goal_completed" not in kinds
            memories = [m["content"] for m in await runtime.memory.active_memories()]
            assert not any("补给" in content or "完成目标" in content for content in memories)

            # finish the errand (purchase, then the walk home) → completion
            # does become one
            clock.advance(10)
            await runtime.tick(minutes=1)
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            await finish_errand(runtime, clock)
            await runtime.flush_experiences()
            kinds = {record.kind.value for record in runtime.experiences.emitted()}
            assert "goal_completed" in kinds
            memories = await runtime.memory.active_memories()
            assert any("补给" in str(m["content"]) or "目标" in str(m["content"]) for m in memories)
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------- §37/§38 simulation


class TestAutonomousLifeSimulation:
    async def test_24h_simulation_lives_without_any_input(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            transitions: list[str] = []
            for _ in range(24 * 6):  # 24h at 10-minute ticks
                clock.advance(600)
                before = runtime.current_action.definition_id if runtime.current_action else ""
                await runtime.simulate(hours=0, step_minutes=0) if False else None
                report = await runtime.tick(minutes=10)
                after = runtime.current_action.definition_id if runtime.current_action else ""
                if report.get("completed") or (before and after and before != after):
                    transitions.append(after or before)
            assert transitions, "she never did anything in a whole day"
            assert len(set(transitions)) >= 2, transitions[:10]  # never idle-bound
            assert runtime.decisions.llm_calls == 0  # pure deterministic world
            assert runtime.events.last(ET.ACTION_COMPLETED) is not None
            assert runtime.goals.all(), "no goals were ever formed"
            assert runtime.events.of_type(ET.GOAL_PROGRESS) or runtime.events.of_type(
                ET.GOAL_STEP_STARTED
            )
            assert runtime.character.location  # she moved around
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_simulation_is_reproducible(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        counter = {"n": 0}

        async def run(seed: int) -> tuple[list[str], list[str]]:
            counter["n"] += 1
            db = Database(
                DatabaseConfig(url=f"sqlite:///{tmp_path / f'repro_{counter[chr(110)]}.db'}")
            )
            await db.connect()
            try:
                clock = Clock()
                bible = BibleCompiler(FIXTURE_BIBLE).compile()
                runtime = SandboxRuntime(
                    SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=seed),
                    SandboxStore(db),
                    bible=bible,
                    clock=clock,
                )
                await runtime.start()
                try:
                    actions: list[str] = []
                    goal_kinds: list[str] = []
                    for _ in range(6 * 6):
                        clock.advance(600)
                        await runtime.tick(minutes=10)
                        if runtime.current_action is not None:
                            actions.append(runtime.current_action.definition_id)
                        goal_kinds.extend(
                            goal.kind.value
                            for goal in runtime.goals.all()
                            if goal.kind.value not in goal_kinds
                        )
                    return actions, goal_kinds
                finally:
                    await runtime.shutdown()
            finally:
                await db.close()

        first_actions, first_goals = await run(11)
        second_actions, second_goals = await run(11)
        assert first_actions == second_actions
        assert first_goals == second_goals
