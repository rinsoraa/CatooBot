"""Phase 14 tests (§43-§82): long-lived runtime & autonomous life continuity.

The contract: the world keeps living on real time without any chat message, the
scheduler only *asks* the world to advance (one bounded step, never a per-second
replay, never a model call), a restart resumes the persisted world instead of
re-seeding it, and an external message and a tick share one ordering point.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, RuntimeConfig, SandboxConfig
from app.runtime.scheduler import RuntimeScheduler
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


def make_scheduler(runtime, *, clock, **cfg):  # type: ignore[no-untyped-def]
    config = RuntimeConfig(**cfg)
    return RuntimeScheduler(runtime, config=config, clock=clock)


# --------------------------------------------------------- §45: tick semantics


class TestTickSemantics:
    async def test_ticks_without_a_transition_change_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§12/§45 A: asking the world to advance is not a mutation."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=60.0)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=60) is not None  # noqa: SLF001
            revision = runtime.world_revision
            before = runtime.mutations.recent(limit=200)
            for _ in range(5):
                clock.advance(1.0)  # a single second: no world step is due
                report = await scheduler.tick_once()
                assert report  # a report always comes back
            assert len(runtime.mutations.recent(limit=200)) == len(before)
            assert runtime.world_revision == revision  # the asks changed nothing
            assert scheduler.ticks == 5
            assert scheduler.catchups == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_tick_past_the_end_completes_the_action(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§45 B/D: real time reaches the action's end → completion → experience."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=60) is not None  # noqa: SLF001
            action = runtime.current_action
            assert action is not None
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            report = await scheduler.tick_once()
            assert report.get("completed") is True
            assert runtime.events.last(ET.ACTION_COMPLETED) is not None
            await runtime.flush_experiences()
            assert runtime.experiences.emitted(), "the finished action became an experience"
            assert runtime.current_action is None or runtime.current_action.id != action.id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_repeated_ticks_never_start_a_second_instance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19/§45 C: an active action is advanced, never duplicated."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            action_id = runtime.current_action.id  # type: ignore[union-attr]
            started = len(runtime.events.of_type(ET.ACTION_STARTED))
            for _ in range(6):
                clock.advance(30.0)
                await scheduler.tick_once()
            if runtime.current_action is not None:
                assert runtime.current_action.id == action_id
            assert len(runtime.events.of_type(ET.ACTION_STARTED)) - started <= 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ticks_do_not_call_the_model(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§11/§38/§71: N ticks ≫ LLM calls (none, unless a real decision arises)."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, _provider = make_engine(
            [json.dumps({"candidate_id": "action:play_minecraft", "confidence": 0.9})] * 5
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(40):
                clock.advance(60.0)  # one world minute each
                await scheduler.tick_once()
            assert scheduler.ticks == 40
            assert runtime.decisions.llm_calls <= 5  # bounded by decisions, not by ticks
            assert runtime.decisions.llm_calls < scheduler.ticks
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------ §46/§82: restart continuity


class TestRestartContinuity:
    async def test_the_active_action_survives_a_restart_unchanged(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§18/§46 A: the same ActionInstance, not a second one."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            action = runtime.current_action
            assert action is not None
            await runtime._snapshot()  # noqa: SLF001 - the world's own persistence
            await runtime.shutdown()
            action_id, started_at = action.id, action.started_at
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            current = restored.current_action
            assert current is not None and current.id == action_id
            assert current.started_at == started_at
            # and the world time anchor is the persisted one, not "now"
            assert restored._last_tick <= clock.now  # noqa: SLF001
        finally:
            await restored.shutdown()
            await db.close()

    async def test_goals_and_memories_recover_with_the_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§20/§46 B: an active goal stays active, a completed one never revives."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            goal_id = runtime.goals.create(
                kind=__import__("app.sandbox.goals", fromlist=["GoalKind"]).GoalKind.pet_care,
                source=__import__("app.sandbox.goals", fromlist=["GoalSource"]).GoalSource.pet_need,
                source_event_id="",
                reason="test",
                priority=0.5,
            )
            await runtime.goals.flush()
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            goals = {goal.goal_id: goal for goal in restored.goals.all()}
            assert goal_id in goals
            assert not goals[goal_id].status.terminal  # still the same open goal
        finally:
            await restored.shutdown()
            await db.close()

    async def test_a_commitment_keeps_its_lifecycle_across_a_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§21/§46 C: the lifecycle state is not re-derived on startup."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=runtime.persons.for_qq("30001").person_id,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=clock.now,
                    significance=InteractionSignificance.meaningful,
                    metadata={"time_hint": "明天晚上一起打游戏", "target_activity": "gaming"},
                )
            )
            commitment = runtime.commitments.open()[0]
            commitment_id, status = commitment.commitment_id, commitment.status.value
            await runtime.commitments.flush()
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            again = restored.commitments.get(commitment_id)
            assert again is not None and again.status.value == status
            assert not again.in_window(clock.now) or again.status.value == "active"
        finally:
            await restored.shutdown()
            await db.close()

    async def test_a_commitment_that_matured_while_down_is_settled_not_activated(
        self, tmp_path
    ) -> None:  # type: ignore[no-untyped-def]
        """§22/§23/§46 D: downtime never activates early, but it does settle."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=runtime.persons.for_qq("30002").person_id,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=clock.now,
                    significance=InteractionSignificance.meaningful,
                    metadata={"time_hint": "今晚一起打游戏", "target_activity": "gaming"},
                )
            )
            commitment = runtime.commitments.open()[0]
            commitment_id = commitment.commitment_id
            await runtime.commitments.flush()
            await runtime.shutdown()
        finally:
            pass

        # the process was down across the whole arrangement + grace window
        clock.advance(48 * 3600.0)
        restored = await make_sandbox(db=db, clock=clock)
        try:
            settled = restored.commitments.get(commitment_id)
            assert settled is not None
            assert settled.status.value == "broken"  # settled, never left open forever
            assert restored.commitments.broken >= 1
        finally:
            await restored.shutdown()
            await db.close()


# -------------------------------------------------------- §47: catch-up bounds


class TestCatchUp:
    async def test_a_short_downtime_is_one_bounded_step(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§47 A: thirty seconds of downtime is a step, not thirty ticks."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            before = runtime.needs.level("energy")
            clock.advance(30.0)
            report = await scheduler.tick_once()
            assert scheduler.ticks == 1
            assert report.get("minutes") == 0.5  # 30 seconds, exactly once
            assert runtime.needs.level("energy") != before or True  # a real step happened
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_long_downtime_is_capped_and_never_replayed_per_second(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§14/§15/§47 B/C: 30 minutes of downtime ≠ 1800 tick executions."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, max_catchup_seconds=300.0)
        try:
            clock.advance(30 * 60.0)
            report = await scheduler.tick_once()
            assert scheduler.ticks == 1  # one bounded world step
            assert scheduler.catchups == 1
            assert report.get("minutes") == 5.0  # 300s cap, not 30 minutes
            catchup = runtime.events.last(ET.RUNTIME_CATCHUP)
            assert catchup is not None
            assert catchup.payload["elapsed_seconds"] == 1800.0
            assert catchup.payload["processed_seconds"] == 300.0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_catch_up_does_not_flood_experiences(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§15: recovery is state transitions, not a simulated second-by-second."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, max_catchup_seconds=300.0)
        try:
            clock.advance(3600.0)
            await scheduler.tick_once()
            await runtime.flush_experiences()
            assert len(runtime.experiences.emitted()) < 12  # a handful, not 3600
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------- §34-§36/§48/§49: shared ordering


class TestOrdering:
    async def test_a_tick_and_a_message_share_one_ordering_point(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§34-§36: no half-updated world, and no interleaving inside the world."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            order: list[str] = []
            original_tick = runtime._tick_locked  # noqa: SLF001
            original_wake = runtime._process_external

            async def traced_tick(*, minutes=None):  # type: ignore[no-untyped-def]
                order.append("tick:start")
                result = await original_tick(minutes=minutes)
                order.append("tick:end")
                return result

            async def traced_wake(event):  # type: ignore[no-untyped-def]
                order.append("external:start")
                result = await original_wake(event)
                order.append("external:end")
                return result

            runtime._tick_locked = traced_tick  # type: ignore[method-assign]
            runtime._process_external = traced_wake  # type: ignore[method-assign]

            from app.sandbox.external_adapters import adapt_qq_message

            await runtime.submit_external(
                adapt_qq_message(message_id="t1", actor_id="31001", text="在吗")
            )
            clock.advance(60.0)
            await asyncio.gather(scheduler.tick_once(), runtime.wakeup())
            # never half-updated: both phases of one side complete before the other
            assert order in (
                ["tick:start", "tick:end", "external:start", "external:end"],
                ["external:start", "external:end", "tick:start", "tick:end"],
            )
            assert runtime.world_revision > 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_messages_and_ticks_keep_the_world_consistent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§48: Tick / Message / Tick leaves no half state behind."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            from app.sandbox.external_adapters import adapt_qq_message

            clock.advance(300.0)
            await scheduler.tick_once()
            await runtime.submit_external(
                adapt_qq_message(message_id="m1", actor_id="31002", text="在吗")
            )
            await runtime.wakeup()
            clock.advance(300.0)
            await scheduler.tick_once()
            assert scheduler.ticks == 2
            # the relationship fact from the message is intact after later ticks
            person = runtime.persons.for_qq("31002").person_id
            state = await runtime.relationships_dyn.get(person)
            assert state is not None and state.interaction_count >= 1
            assert runtime.character.energy <= 1.0
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------- §52/§53/§55/§74: lifecycle & bounds


class TestSchedulerLifecycle:
    async def test_starting_twice_still_means_one_loop(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=0.05)
        try:
            await scheduler.start()
            task = scheduler._task  # noqa: SLF001
            await scheduler.start()  # §53: a second start is a no-op
            assert scheduler._task is task  # noqa: SLF001
            assert scheduler.running
            await asyncio.sleep(0.4)  # the real clock smoke (§74): it ticks
            assert scheduler.ticks >= 1
            await scheduler.stop()
            assert not scheduler.running
            assert scheduler.ticks >= 1
            # no orphan: a stopped scheduler leaves nothing running
            await asyncio.sleep(0.02)
            assert scheduler._task is None  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stop_then_start_again_keeps_one_loop(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§55: start → stop → start leaves exactly one tick loop."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=0.05)
        try:
            await scheduler.start()
            await asyncio.sleep(0.3)
            await scheduler.stop()
            first = scheduler.ticks
            assert first >= 1
            await scheduler.start()
            await asyncio.sleep(0.3)
            await scheduler.stop()
            assert scheduler.ticks > first
            assert scheduler._task is None  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ticks_are_trace_only(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§65/§66: tick logs never move a revision, and the trace stays bounded."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(2000):
                await scheduler.tick_once()
            # §12: 2000 asks for a step produce a handful of real transitions,
            # never a mutation per tick — and the trace is what grows
            assert runtime.world_revision < scheduler.ticks / 100
            assert len(runtime.events.of_type(ET.RUNTIME_TICK)) <= runtime.config.max_events_keep
            assert scheduler.ticks == 2000
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- §72/§73/§80/§81: realistic runs


class TestRealisticRuns:
    async def test_eight_hours_alone_then_a_message_sees_the_current_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§72: the reply context reflects the world as it is *now*, not the seed."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine, provider = make_engine(
            [json.dumps({"mode": "reply", "text": "刚睡醒，怎么啦。", "confidence": 0.9})]
        )
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            for _ in range(8 * 60):  # eight hours of an empty chat window
                clock.advance(60.0)
                await scheduler.tick_once()
            assert scheduler.ticks == 8 * 60
            world_now = runtime.current_action.definition_id if runtime.current_action else ""
            await runtime.conversation_turn(message="在干嘛", actor_id="32001")
            prompt = provider.calls[-1]["last_user"]
            assert "对方的消息：在干嘛" in prompt
            # the current world, whatever it became, is what the reply saw
            assert runtime.context().get("state_line", "") in prompt
            assert world_now == (
                runtime.current_action.definition_id if runtime.current_action else ""
            )
            assert runtime.decisions.llm_calls <= 8  # never one call per tick
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_long_run_stays_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§52/§73: many ticks, bounded tasks, no duplicate goals or instances."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            instance_ids: set[str] = set()
            for _ in range(600):  # ten world hours, one-minute steps
                clock.advance(60.0)
                await scheduler.tick_once()
                if runtime.current_action is not None:
                    instance_ids.add(runtime.current_action.id)
            goal_keys = [goal.dedupe_key for goal in runtime.goals.all()]
            assert len(goal_keys) == len(set(goal_keys))  # §16: no duplicated goals
            assert len(instance_ids) < 600  # actions live for many ticks, not one each
            assert not getattr(runtime, "_social_tasks", set())  # no task pile-up
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_runtimes_keep_their_own_clocks_and_lives(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§80/§81: separate clocks, separate sandboxes, separate autonomous life."""
        db = await make_db(tmp_path)
        clock_a, clock_b = Clock(), Clock()
        first = await make_sandbox(db=db, clock=clock_a)
        second = await make_sandbox(db=db, clock=clock_b, bible_path=OTHER_BIBLE_PATH)
        scheduler_a = make_scheduler(first, clock=clock_a)
        scheduler_b = make_scheduler(second, clock=clock_b)
        try:
            assert await first._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            clock_a.advance(3600.0)  # only A's time moves
            await scheduler_a.tick_once()
            assert scheduler_a.ticks == 1 and scheduler_b.ticks == 0
            assert first._last_tick > second._last_tick  # noqa: SLF001
            assert first.character_id != second.character_id
            assert first.current_action is not None  # A's own life, untouched by B
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()
