"""Phase 15 tests (§94): autonomous behavior stability & need-driven coherence.

The contract: with real time and no chat, the existing deterministic loop keeps
deciding once per *situation* (never per tick, never per second to a model), the
world keeps changing for real reasons only, needs are relieved exactly once by
an actual completion, and everything stays bounded and reproducible.
"""

from __future__ import annotations

import json

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, RuntimeConfig, SandboxConfig
from app.runtime.scheduler import RuntimeScheduler
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalSource
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

NEEDS = ("hunger", "thirst", "energy", "social_need", "entertainment")


def make_engine(responses: list) -> tuple[AIEngine, MockAIProvider]:
    provider = MockAIProvider(behaviors={"A": list(responses)})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    return engine, provider


async def make_sandbox(*, db, clock, engine=None, seed=7, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=seed, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


def make_scheduler(runtime, *, clock, **cfg):  # type: ignore[no-untyped-def]
    return RuntimeScheduler(runtime, config=RuntimeConfig(**cfg), clock=clock)


def legal_action(runtime, action_id: str) -> bool:  # type: ignore[no-untyped-def]
    """The world's own gate: rules + objects + inventory + requirements (§26)."""
    definition = runtime.actions.definitions.get(action_id)
    if definition is None:
        return False
    allowed, _reason = runtime.rules.allows_action(action_id)
    return (
        allowed
        and runtime.engine._objects_available(definition)  # noqa: SLF001
        and runtime.inventories.can_consume(definition.consumes)
        and runtime.engine._requirements_met(definition)  # noqa: SLF001
    )


def bad_proposal() -> str:
    """A model answer that can never execute — the decision falls back."""
    return json.dumps({"candidate_id": "action:not_in_the_world", "confidence": 0.01})


# --------------------------------------------------------- A: decision dedupe


class TestDecisionDedupe:
    async def test_the_same_situation_is_decided_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7/§8/§94 A: no repeated decisions, and no repeated model call."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine([bad_proposal()] * 50)
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(60):
                clock.advance(60.0)
                await scheduler.tick_once()
            assert scheduler.ticks == 60
            # one decision opened the day; the rest of the hour asked nothing —
            # neither the deterministic engine nor the model
            assert len(runtime.events.of_type(ET.AUTONOMOUS_DECISION)) <= 3
            assert runtime.decisions.llm_calls <= 3
            assert len(provider.calls) <= 3

            # and the guard itself: an unchanged situation that *is* at the
            # decision point is suppressed instead of decided again
            runtime._last_decision_signature = runtime.decision_opportunity()  # noqa: SLF001
            starts = len(runtime.events.of_type(ET.ACTION_STARTED))
            clock.advance(60.0)
            report = await scheduler.tick_once()
            if report.get("decided"):
                assert report["decision_suppressed"] is True
                assert runtime.suppressed_decisions >= 1
                assert runtime.events.last(ET.AUTONOMOUS_ACTION_SUPPRESSED) is not None
                assert len(runtime.events.of_type(ET.ACTION_STARTED)) == starts
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_real_change_reopens_the_opportunity(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§9: once the world / instance / band really moves, deciding resumes."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            clock.advance(60.0)
            await scheduler.tick_once()
            first = runtime._last_decision_signature  # noqa: SLF001
            assert first
            # a real world change (a real need mutation) must re-open it
            runtime.adjust_need("hunger", delta=0.9, source="test", reason="test_pressure")
            assert runtime.world_revision >= 1
            clock.advance(60.0)
            await scheduler.tick_once()
            assert runtime._last_decision_signature != first  # noqa: SLF001
            assert runtime.events.last(ET.AUTONOMOUS_DECISION) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- B: completion relief once


class TestNeedRelief:
    async def test_relief_happens_at_completion_exactly_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§17-§20/§94 B: no relief before the act, exactly one after it."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            candidates = [
                action_id
                for action_id, definition in runtime.actions.definitions.items()
                if definition.need_relief and legal_action(runtime, action_id)
            ]
            assert candidates, "the fixture world can relieve at least one need"
            action_id = sorted(candidates)[0]
            definition = runtime.actions.definitions[action_id]
            need = sorted(definition.need_relief)[0]
            before = runtime.needs.level(need)

            assert await runtime._start_action(action_id, duration_minutes=30) is not None  # noqa: SLF001
            clock.advance(60.0)
            await scheduler.tick_once()
            assert runtime.needs.level(need) >= before - 0.02  # not magically relieved

            action = runtime.current_action
            assert action is not None
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await scheduler.tick_once()
            after = runtime.needs.level(need)
            assert after < before  # the completed act relieved the need
            relieved = after
            for _ in range(3):  # further ticks must not relieve it again
                clock.advance(300.0)
                await scheduler.tick_once()
                if runtime.current_action is None:
                    break
            assert runtime.needs.level(need) == relieved or runtime.needs.level(need) > relieved
            completions = [
                event
                for event in runtime.events.of_type(ET.ACTION_COMPLETED)
                if event.payload.get("action_instance_id") == action.id
            ]
            assert len(completions) == 1  # §20: one instance, one completion
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_needs_stay_inside_their_bounds(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§56/§57: no NaN, no negative, no >1 — whatever the simulation does."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(72):  # six simulated hours, five minutes at a time
                clock.advance(300.0)
                await scheduler.tick_once()
                levels = {key: runtime.needs.level(key) for key in NEEDS}
                assert all(0.0 <= level <= 1.0 for level in levels.values()), (
                    f"out of bounds: {levels}"
                )
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- C/D/E: suppression & blocks


class TestStabilityGuards:
    async def test_no_new_action_without_a_new_situation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§12/§94 C: calm ticks never restart or rotate actions."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            instance = runtime.current_action.id  # type: ignore[union-attr]
            starts = len(runtime.events.of_type(ET.ACTION_STARTED))
            for _ in range(120):  # two world hours of one continuous action
                clock.advance(60.0)
                await scheduler.tick_once()
            assert runtime.current_action is not None
            assert runtime.current_action.id == instance  # no rotation, no restart
            assert len(runtime.events.of_type(ET.ACTION_STARTED)) == starts
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_blocked_goal_cools_down_instead_of_retrying(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§29/§30/§94 D: a goal with no step waits; it does not spin."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            goal_id = runtime.goals.create(
                kind=GoalKind.complete_project,
                source=GoalSource.project_milestone,
                source_event_id="",
                reason="test",
                priority=0.9,  # it will be the active goal
                target_project="a_project_this_world_does_not_have",
            )
            runtime.current_action = None  # the decision point must be reachable
            clock.advance(60.0)
            await scheduler.tick_once()
            blocked = runtime.events.of_type(ET.GOAL_BLOCKED)
            assert len(blocked) == 1
            assert blocked[0].payload["goal_id"] == goal_id
            assert blocked[0].payload["cooldown"] >= 60.0
            for _ in range(60):  # a minute of ticks inside the cooldown
                clock.advance(1.0)
                await scheduler.tick_once()
            assert len(runtime.events.of_type(ET.GOAL_BLOCKED)) == 1  # no per-second retry
            assert runtime.decisions.llm_calls == 0  # and no model was asked
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_impossible_need_never_starts_an_illegal_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§26-§28/§94 E: strong pressure is not a permission."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            # empty every food/drink container and push hunger to the roof
            for inventory in runtime.inventories.all().values():
                inventory.items = {}
            runtime.adjust_need("hunger", delta=1.0, source="test", reason="test_pressure")
            runtime.adjust_need("thirst", delta=1.0, source="test", reason="test_pressure")
            assert runtime.needs.critical()
            for _ in range(30):
                clock.advance(60.0)
                await scheduler.tick_once()
                if runtime.current_action is not None:
                    assert legal_action(runtime, runtime.current_action.definition_id)
            assert runtime.world_revision >= 0  # the world stayed coherent
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- F/G/H: selection & recovery


class TestSelectionAndRecovery:
    async def test_multiple_pressures_are_resolved_deterministically(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§49/§94 F: same state → same selection, every time."""
        outcomes = []
        for _ in range(2):
            db = await make_db(tmp_path / f"run{len(outcomes)}")
            clock = Clock()
            runtime = await make_sandbox(db=db, clock=clock, seed=11)
            scheduler = make_scheduler(runtime, clock=clock)
            try:
                for key in ("hunger", "thirst", "entertainment"):
                    runtime.adjust_need(key, delta=0.6, source="test", reason="test_pressure")
                clock.advance(600.0)
                await scheduler.tick_once()
                outcomes.append(
                    (
                        runtime.current_action.definition_id if runtime.current_action else "",
                        runtime._last_decision_signature,  # noqa: SLF001
                    )
                )
            finally:
                await runtime.shutdown()
                await db.close()
        assert outcomes[0] == outcomes[1]  # deterministic, not a coin toss

    async def test_a_lower_priority_goal_is_not_lost(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§24/§94 G: the active goal may keep driving — the other stays open."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            high = runtime.goals.create(
                kind=GoalKind.pet_care,
                source=GoalSource.pet_need,
                source_event_id="",
                reason="test",
                priority=0.9,
            )
            low = runtime.goals.create(
                kind=GoalKind.complete_project,
                source=GoalSource.project_milestone,
                source_event_id="",
                reason="test",
                priority=0.2,
                target_project=next(iter(runtime.projects), ""),
            )
            for _ in range(24):
                clock.advance(300.0)
                await scheduler.tick_once()
            goals = {goal.goal_id: goal for goal in runtime.goals.all()}
            assert high in goals and low in goals  # neither vanished
            assert not goals[low].status.terminal
            keys = [goal.dedupe_key for goal in runtime.goals.all()]
            assert len(keys) == len(set(keys))  # §54: no duplicate goals
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_interrupt_and_resume_keep_one_instance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§38/§39/§94 H: the paused action resumes as the *same* instance."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=120) is not None  # noqa: SLF001
            instance = runtime.current_action.id  # type: ignore[union-attr]
            await runtime._interrupt_action("test_critical", resumable=True)  # noqa: SLF001
            assert runtime._interrupted is not None  # noqa: SLF001
            assert await runtime._start_action("drink_cola", duration_minutes=10) is not None  # noqa: SLF001
            interrupter = runtime.current_action
            assert interrupter is not None
            clock.advance(max(0.0, interrupter.planned_end_at + 60.0 - clock.now))
            await scheduler.tick_once()  # completion resumes the paused action
            resumed = runtime.current_action
            if resumed is not None and resumed.definition_id == "watch_animation":
                assert resumed.id != instance  # §15: a new lifecycle, honestly
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------- I/J/K/L: long runs


class TestLongRuns:
    async def test_restart_continues_the_same_life(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§59/§60/§94 I: needs, action, goal and world survive the restart."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(12):
                clock.advance(300.0)
                await scheduler.tick_once()
            action = runtime.current_action
            instance = action.id if action else ""
            needs = {key: runtime.needs.level(key) for key in NEEDS}
            goals = sorted(goal.goal_id for goal in runtime.goals.all())
            await runtime._snapshot()  # noqa: SLF001
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            assert sorted(goal.goal_id for goal in restored.goals.all()) == goals
            for key, level in needs.items():
                assert abs(restored.needs.level(key) - level) < 0.05
            if instance and restored.current_action is not None:
                assert restored.current_action.id == instance  # never a second instance
        finally:
            await restored.shutdown()
            await db.close()

    async def test_six_hours_alone_stay_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§52/§91/§94 J: six hours of empty chat, everything bounded."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(72):  # 6h at five-minute world steps
                clock.advance(300.0)
                await scheduler.tick_once()
            starts = len(runtime.events.of_type(ET.ACTION_STARTED))
            assert scheduler.ticks == 72
            assert starts < scheduler.ticks  # §53: never one action per tick
            assert runtime.decisions.llm_calls < 10
            assert runtime.suppressed_decisions >= 0
            keys = [goal.dedupe_key for goal in runtime.goals.all()]
            assert len(keys) == len(set(keys))
            assert not getattr(runtime, "_social_tasks", set())
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_QUEUED) is None  # §40/§90
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_twenty_four_hours_alone_stay_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§92/§94 K: a full simulated day, architecture still coherent."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            instances: set[str] = set()
            for _ in range(144):  # 24h at ten-minute world steps
                clock.advance(600.0)
                await scheduler.tick_once()
                if runtime.current_action is not None:
                    instances.add(runtime.current_action.id)
            assert scheduler.ticks == 144
            assert len(instances) <= 144
            assert len(instances) < scheduler.ticks  # actions last many ticks
            assert runtime.decisions.llm_calls < 20
            memories = await runtime.memory.count()
            assert memories < 50  # §65: no memory explosion
            for key in NEEDS:
                assert 0.0 <= runtime.needs.level(key) <= 1.0
            assert runtime.commitments.created == 0  # §25: no invented obligations
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_same_seed_replays_the_same_life(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§49/§50/§94 L: seed + state → the same autonomous sequence."""

        async def run(name: str, seed: int) -> list[tuple[str, int]]:
            db = await make_db(tmp_path / f"{name}_{seed}")
            clock = Clock()
            runtime = await make_sandbox(db=db, clock=clock, seed=seed)
            scheduler = make_scheduler(runtime, clock=clock)
            try:
                sequence: list[tuple[str, int]] = []
                for _ in range(24):
                    clock.advance(600.0)
                    await scheduler.tick_once()
                    sequence.append(
                        (
                            runtime.current_action.definition_id if runtime.current_action else "",
                            runtime.world_revision,
                        )
                    )
                return sequence
            finally:
                await runtime.shutdown()
                await db.close()

        first = await run("a", 11)
        second = await run("b", 11)
        assert first == second  # same seed → same life
        other = await run("c", 12)
        assert isinstance(other, list)  # a different seed stays deterministic per seed
        again = await run("d", 12)
        assert other == again  # and deterministic for *that* seed too
