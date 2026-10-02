"""Phase 14.1 tests (§47): the runtime scheduler is the only world-tick owner.

The contract: the legacy "every 10 minutes" sandbox job is gone from the
production path, the world advances by *real elapsed time* (10 seconds of
reality is 10 seconds of world, never 10 minutes), and only one owner ever
calls ``SandboxRuntime.tick()`` — before and after a restart.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import AppConfig, RuntimeConfig, SandboxConfig
from app.runtime.scheduler import RuntimeScheduler
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

APP_ROOT = Path(__file__).resolve().parent.parent / "app"


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


def make_scheduler(runtime, *, clock, **cfg):  # type: ignore[no-untyped-def]
    return RuntimeScheduler(runtime, config=RuntimeConfig(**cfg), clock=clock)


async def make_promise(runtime, qq: str, hint: str, *, activity: str = "gaming"):  # type: ignore[no-untyped-def]
    await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=runtime.persons.for_qq(qq).person_id,
            interaction_type="invitation_accepted",
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=InteractionSignificance.meaningful,
            metadata={"time_hint": hint, "target_activity": activity},
        )
    )
    return runtime.commitments.open()[-1]


# ----------------------------------------------------- §6/§15: the legacy job


class TestNoLegacyTick:
    def test_the_legacy_job_name_is_gone_from_the_code(self) -> None:
        """§6/§32: not disabled by config — no longer registered, anywhere."""
        offenders = [
            path.relative_to(APP_ROOT).as_posix()
            for path in APP_ROOT.rglob("*.py")
            if "sandbox_tick" in path.read_text(encoding="utf-8")
        ]
        assert offenders == [], f"legacy sandbox_tick still referenced in {offenders}"

    async def test_starting_sandbox_jobs_registers_no_world_tick(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§15: the shared scheduler keeps its jobs but never drives the world."""
        from app.core.bot import Bot
        from tests.conftest import FakeAdapter

        config = AppConfig(
            bot={"name": "TestBot", "debug": False},
            database={"url": f"sqlite:///{tmp_path / 'sole_tick.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            media={
                "sticker_dir": str(tmp_path / "stickers"),
                "media_dir": str(tmp_path / "media"),
            },
            sandbox={"enabled": True, "bible_path": str(FIXTURE_BIBLE)},
            runtime={"enabled": True},
        )
        bot = Bot(config, FakeAdapter())
        try:
            assert bot.sandbox is not None, "the sandbox really was constructed"
            assert bot.runtime_scheduler is not None, "the world tick owner exists"
            await bot.database.connect()  # the store needs the DB before start()
            await bot.sandbox.start()
            await bot._start_sandbox_jobs()  # noqa: SLF001
            names = [job.name for job in bot.scheduler.jobs()]
            assert "sandbox_tick" not in names
            assert not any(
                getattr(job.handler, "__self__", None) is bot.sandbox
                for job in bot.scheduler.jobs()
                if job.handler is not None
            ), "no shared-scheduler job may own sandbox.tick()"
        finally:
            await bot.scheduler.stop()
            if bot.sandbox is not None:
                await bot.sandbox.shutdown()
            await bot.database.close()


# --------------------------------------------- §10-§14/§26: real elapsed time


class TestWorldTimeSemantics:
    async def test_ten_real_seconds_advance_ten_world_seconds(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§12/§17: ten seconds of reality ≠ ten minutes of world."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            action = runtime.current_action
            assert action is not None
            before_progress = runtime.actions.progress(action)
            clock.advance(10.0)
            report = await scheduler.tick_once()
            assert report["minutes"] == 10.0 / 60.0  # exactly the real elapsed time
            expected = runtime.actions.progress(action)
            assert expected >= before_progress
            # the world did *not* jump a whole minute or ten
            assert expected < before_progress + 1.0 / max(
                1.0, runtime.actions.expected_minutes(action)
            )
            assert runtime._last_tick == clock.now  # noqa: SLF001 - the anchor moved with reality
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_real_ten_minute_gap_advances_ten_minutes_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§16: 600 seconds is processed once — not sixty one-minute legacy ticks."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            clock.advance(600.0)
            report = await scheduler.tick_once()
            assert scheduler.ticks == 1
            assert scheduler.catchups == 1  # a catch-up step, not per-second replay
            assert report["minutes"] == 5.0  # capped by max_catchup_seconds (300s)
            assert runtime._last_tick == clock.now  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_short_ticks_accumulate_without_drift(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§26: the scheduler frequency never becomes a world-time multiplier."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            advanced = 0.0
            for _ in range(120):  # two minutes of reality, one second at a time
                clock.advance(1.0)
                advanced += 1.0
                report = await scheduler.tick_once()
                assert report["minutes"] == 1.0 / 60.0
            assert scheduler.ticks == 120
            assert runtime._last_tick == clock.now  # noqa: SLF001
            assert advanced == 120.0
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------- §3/§16/§38: exactly one tick owner


class TestSingleTickOwner:
    async def test_only_the_scheduler_advances_the_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§3/§16: counting tick calls shows one owner, with no legacy double."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock)
        try:
            calls = {"n": 0}
            original = runtime._tick_locked  # noqa: SLF001

            async def counted(*, minutes=None):  # type: ignore[no-untyped-def]
                calls["n"] += 1
                return await original(minutes=minutes)

            runtime._tick_locked = counted  # type: ignore[method-assign]
            clock.advance(600.0)
            await scheduler.tick_once()
            assert calls["n"] == 1  # one world step for ten real minutes
            assert scheduler.ticks == 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_restart_leaves_exactly_one_owner(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§38/§47-12: start → stop → start keeps one loop and one owner."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=0.05)
        try:
            await scheduler.start()
            first_task = scheduler._task  # noqa: SLF001
            await scheduler.start()  # §37: idempotent
            assert scheduler._task is first_task  # noqa: SLF001
            await scheduler.stop()
            await scheduler.start()
            assert scheduler._task is not None and scheduler._task is not first_task  # noqa: SLF001
            await scheduler.stop()
            assert scheduler._task is None  # noqa: SLF001
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------- §17-§22: the autonomous systems follow real time


class TestAutonomousTime:
    async def test_one_action_is_never_duplicated_by_ticks(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§17/§47-6: ticking twice as often must not double the actions."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=600) is not None  # noqa: SLF001
            instance = runtime.current_action.id  # type: ignore[union-attr]
            started = len(runtime.events.of_type(ET.ACTION_STARTED))
            for _ in range(120):  # two world minutes, second by second
                clock.advance(1.0)
                await scheduler.tick_once()
            assert runtime.current_action is not None
            assert runtime.current_action.id == instance
            assert len(runtime.events.of_type(ET.ACTION_STARTED)) == started
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_goals_experiences_and_memories_stay_unique(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19-§21/§47-7/8/9: many small ticks, no duplicated state."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            assert await runtime._start_action("watch_animation", duration_minutes=60) is not None  # noqa: SLF001
            action = runtime.current_action
            assert action is not None
            # walk the action to its end in coarse real steps (60s each)
            while clock.now <= action.planned_end_at + 60.0:
                clock.advance(60.0)
                await scheduler.tick_once()
            await runtime.flush_experiences()
            completions = [
                event
                for event in runtime.events.of_type(ET.ACTION_COMPLETED)
                if event.payload.get("action_instance_id") == action.id
            ]
            assert len(completions) == 1  # §20: one instance, one completion
            experiences = [
                record
                for record in runtime.experiences.emitted()
                if record.metadata.get("action_instance_id") == action.id
            ]
            assert len(experiences) <= 1  # and at most one episode
            goal_keys = [goal.dedupe_key for goal in runtime.goals.all()]
            assert len(goal_keys) == len(set(goal_keys))  # §47-7
            memories = await runtime.memory.active_memories(limit=50)
            dedupe_keys = [row.get("dedupe_key", "") for row in memories]
            assert len(dedupe_keys) == len(set(dedupe_keys))  # §47-9
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_commitment_activates_and_breaks_on_real_time(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§18/§22: the promise lifecycle is driven by elapsed time alone."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            commitment = await make_promise(runtime, "33001", "今晚一起打游戏")
            assert commitment.status.value == "pending"
            # not due yet: real minutes pass without activating the promise
            for _ in range(30):
                clock.advance(60.0)
                await scheduler.tick_once()
            assert commitment.status.value == "pending"
            # cross into the window in real time
            while clock.now < commitment.earliest_at:
                clock.advance(60.0)
                await scheduler.tick_once()
            assert commitment.status.value in ("active", "in_progress")
            # and past due + grace exactly once
            while clock.now <= commitment.due_at + 2 * 3600.0:
                clock.advance(300.0)
                await scheduler.tick_once()
            assert commitment.status.value == "broken"
            assert runtime.commitments.broken == 1  # settled once, not repeatedly
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_llm_is_never_called_once_per_tick(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§41: many ticks, a handful of model calls at most."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            for _ in range(100):
                clock.advance(60.0)
                await scheduler.tick_once()
            assert scheduler.ticks == 100  # a hundred world minutes
            assert runtime.decisions.llm_calls < 10
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_autonomous_ticks_never_send_qq(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§43: living is not messaging — no outbound path exists here."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, tick_interval_seconds=1.0)
        try:
            for _ in range(300):
                clock.advance(60.0)
                await scheduler.tick_once()
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_QUEUED) is None
            assert runtime.events.last(ET.OUTBOUND_RESPONSE_SENT) is None
            assert runtime.events.last(ET.CONVERSATION_RESPONSE_EMITTED) is None
        finally:
            await runtime.shutdown()
            await db.close()


class TestWorldTimePersistence:
    async def test_the_anchor_is_the_persisted_world_time(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§23/§24: recovery uses elapsed downtime through the scheduler, once."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(runtime, clock=clock, max_catchup_seconds=300.0)
        try:
            clock.advance(60.0)
            await scheduler.tick_once()
            await runtime.shutdown()
        finally:
            pass

        clock.advance(1800.0)  # the process was down for half an hour
        restored = await make_sandbox(db=db, clock=clock)
        scheduler = make_scheduler(restored, clock=clock, max_catchup_seconds=300.0)
        try:
            # startup already settled the downtime through the sandbox's own
            # bounded recovery: the anchor is *now*, never half an hour of replay
            assert restored._last_tick == clock.now  # noqa: SLF001
            report = await scheduler.tick_once()
            assert scheduler.catchups == 0  # nothing left to catch up
            assert report["minutes"] == 0.0  # no extra step either
            assert json.dumps(report)
        finally:
            await restored.shutdown()
            await db.close()
