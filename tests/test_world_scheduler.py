"""Scheduler tests (v0.8 §34/§35): ONE scheduler, many jobs.

Spec coverage: the behaviour scheduler is the only loop in the process; world
tick, world upkeep and memory consolidation register as :class:`ScheduledJob`
entries on it; a failing job is isolated and a missed window is skipped, not
stacked.
"""

from __future__ import annotations

import asyncio
import time

from app.world.models import ScheduledJob


class TestJobModel:
    def test_not_due_before_interval(self) -> None:
        job = ScheduledJob(name="x", handler=None, interval_seconds=60, last_run=1000.0)
        assert job.due(1030.0, "2026-06-15") is False
        assert job.due(1060.0, "2026-06-15") is True

    def test_run_immediately_flag(self) -> None:
        lazy = ScheduledJob(name="lazy", handler=None, interval_seconds=60)
        eager = ScheduledJob(
            name="eager", handler=None, interval_seconds=60, run_immediately=True
        )
        assert lazy.due(1000.0, "2026-06-15") is False
        assert eager.due(1000.0, "2026-06-15") is True

    def test_disabled_job_never_runs(self) -> None:
        job = ScheduledJob(
            name="off", handler=None, interval_seconds=1, enabled=False, run_immediately=True
        )
        assert job.due(1000.0, "2026-06-15") is False

    def test_daily_cap_resets_on_a_new_day(self) -> None:
        job = ScheduledJob(
            name="capped", handler=None, interval_seconds=1, max_runs_per_day=1
        )
        assert job.due(1000.0, "2026-06-15") is False  # run_immediately off
        job.last_run = 1000.0
        job.runs_today = 1
        job.day_key = "2026-06-15"
        assert job.due(1100.0, "2026-06-15") is False
        assert job.due(1100.0, "2026-06-16") is True

    def test_as_dict_for_webui(self) -> None:
        job = ScheduledJob(name="world_tick", handler=None, interval_seconds=60)
        payload = job.as_dict()
        assert payload["name"] == "world_tick"
        assert payload["interval_seconds"] == 60


class TestRegistry:
    class _Behavior:
        enabled = False
        initiative = type("I", (), {"enabled": False})()

        async def tick(self) -> None:
            return None

    class _Bot:
        def __init__(self) -> None:
            self.world = None
            self.agent = None

    def _scheduler(self):
        from app.behavior.scheduler import BehaviorScheduler

        return BehaviorScheduler(self._Bot(), self._Behavior())

    async def test_job_runs_and_counts(self) -> None:
        scheduler = self._scheduler()
        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)

        scheduler.register_job(
            ScheduledJob(
                name="demo", handler=handler, interval_seconds=0.01, run_immediately=True
            )
        )
        await scheduler.tick()
        assert calls == [1]
        job = scheduler.jobs()[0]
        assert job.runs == 1 and job.failures == 0

    async def test_failing_job_is_isolated(self) -> None:
        scheduler = self._scheduler()
        other: list[int] = []

        async def boom() -> None:
            raise RuntimeError("job exploded")

        async def fine() -> None:
            other.append(1)

        scheduler.register_job(
            ScheduledJob(name="boom", handler=boom, interval_seconds=0.01, run_immediately=True)
        )
        scheduler.register_job(
            ScheduledJob(name="fine", handler=fine, interval_seconds=0.01, run_immediately=True)
        )
        await scheduler.tick()
        assert other == [1]
        failing = next(job for job in scheduler.jobs() if job.name == "boom")
        assert failing.failures == 1 and "exploded" in failing.detail

    async def test_missed_window_is_skipped_not_stacked(self) -> None:
        scheduler = self._scheduler()
        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)

        scheduler.register_job(
            ScheduledJob(
                name="misfire",
                handler=handler,
                interval_seconds=60,
                last_run=time.time() - 3600,
                misfire_policy="skip",
            )
        )
        await scheduler.tick()
        assert calls == []
        assert scheduler.jobs()[0].detail == "skipped missed window"

    async def test_catch_up_policy_runs_late_job(self) -> None:
        scheduler = self._scheduler()
        calls: list[int] = []

        async def handler() -> None:
            calls.append(1)

        scheduler.register_job(
            ScheduledJob(
                name="catch_up",
                handler=handler,
                interval_seconds=60,
                last_run=time.time() - 3600,
                misfire_policy="catch_up",
            )
        )
        await scheduler.tick()
        assert calls == [1]

    async def test_unregister(self) -> None:
        scheduler = self._scheduler()
        scheduler.register_job(ScheduledJob(name="temp", handler=None, interval_seconds=1))
        scheduler.unregister_job("temp")
        assert scheduler.jobs() == []


class TestWorldJobsOnSharedScheduler:
    async def test_world_registers_tick_and_prune(self, tmp_path) -> None:
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        assert bot.world is not None
        await bot._start_world()  # noqa: SLF001 - the documented startup hook
        names = [job.name for job in bot.scheduler.jobs()]
        assert "world_tick" in names
        assert "world_prune" in names
        await bot.shutdown()

    async def test_world_tick_job_interval_follows_config(self, tmp_path) -> None:
        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from tests.conftest import FakeAdapter

        config = AppConfig(
            database={"url": f"sqlite:///{tmp_path / 'jobs.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            world={"enabled": True, "tick_seconds": 90},
            sandbox={"enabled": False},
        )
        bot = Bot(config, FakeAdapter())
        await bot.database.connect()
        await bot._start_world()  # noqa: SLF001
        job = next(job for job in bot.scheduler.jobs() if job.name == "world_tick")
        assert job.interval_seconds == 90
        await bot.shutdown()

    async def test_world_disabled_means_no_jobs(self, tmp_path) -> None:
        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from tests.conftest import FakeAdapter

        config = AppConfig(
            database={"url": f"sqlite:///{tmp_path / 'noworld.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            world={"enabled": False},
            sandbox={"enabled": False},
        )
        bot = Bot(config, FakeAdapter())
        await bot.database.connect()
        assert bot.world is None
        await bot._start_world()  # noqa: SLF001 - must be a no-op
        assert bot.scheduler.jobs() == []
        await bot.shutdown()

    async def test_memory_consolidation_rides_the_shared_loop(self, tmp_path) -> None:
        from app.config.settings import AppConfig
        from app.core.bot import Bot
        from tests.conftest import FakeAdapter

        config = AppConfig(
            database={"url": f"sqlite:///{tmp_path / 'shared.db'}"},
            logging={"log_dir": str(tmp_path / "logs")},
            behavior={"enabled": True, "initiative": {"enabled": False}},
            world={"enabled": False},
            sandbox={"enabled": False},
            memory={"consolidation": {"schedule": "daily"}},
        )
        bot = Bot(config, FakeAdapter())
        await bot.database.connect()
        bot.event_bus.on("message", bot.core_router.on_message)
        await bot.start()
        try:
            assert bot.scheduler.running
            consolidation = bot.consolidation_scheduler
            if consolidation is not None and consolidation.enabled:
                names = [job.name for job in bot.scheduler.jobs()]
                assert "memory_consolidation" in names
                assert consolidation.external_driver is True
                assert consolidation.running is True  # driven, not stale
        finally:
            await bot.shutdown()

    async def test_scheduler_stop_is_clean(self) -> None:
        from app.behavior.scheduler import BehaviorScheduler

        class Behavior:
            enabled = False
            initiative = type("I", (), {"enabled": False})()

            async def tick(self) -> None:
                return None

        class Bot:
            world = None
            agent = None

        scheduler = BehaviorScheduler(Bot(), Behavior(), tick_seconds=1.0)
        await scheduler.start()
        await asyncio.sleep(0)
        assert scheduler.running
        await scheduler.stop()
        assert not scheduler.running
