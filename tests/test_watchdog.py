"""Event-loop stall watchdog (Task 16).

The bot runs one process on one loop, so a single blocking call freezes QQ
chat and the WebUI together — usually visible only as "the bot went quiet".
The watchdog measures its own scheduling delay and reports the size of the
stall, which is what makes the next such incident diagnosable.
"""

from __future__ import annotations

import asyncio
import logging
import time

import pytest

from app.core.activity import BlockingActivity
from app.core.watchdog import EventLoopWatchdog


class TestMeasurement:
    def test_lag_is_wake_up_delay(self) -> None:
        watchdog = EventLoopWatchdog()
        assert watchdog.measure(expected=10.0, actual=10.6) == pytest.approx(0.6)
        assert watchdog.measure(expected=10.0, actual=9.9) == 0.0  # never negative

    def test_threshold_gates_the_report(self, caplog) -> None:
        watchdog = EventLoopWatchdog(threshold_ms=500)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Watchdog"):
            assert watchdog.note_lag(0.2) is False
            assert watchdog.note_lag(0.51) is True
        assert watchdog.lag_events == 1
        assert watchdog.max_lag_ms == pytest.approx(510.0, abs=1.0)
        assert any("卡顿" in record.getMessage() for record in caplog.records)

    def test_stats_shape(self) -> None:
        watchdog = EventLoopWatchdog(interval_seconds=2.0, threshold_ms=250)
        watchdog.note_lag(0.3)
        stats = watchdog.stats()
        assert stats["interval_seconds"] == 2.0 and stats["threshold_ms"] == 250
        assert stats["lag_events"] == 1 and stats["ticks"] == 1
        assert stats["last_lag_ms"] == pytest.approx(300.0, abs=1.0)
        assert stats["max_lag_ms"] == pytest.approx(300.0, abs=1.0)

    def test_metrics_counter(self) -> None:
        class FakeMetrics:
            def __init__(self) -> None:
                self.keys: list[str] = []

            def inc(self, key: str, amount: int = 1) -> None:
                self.keys.append(key)

        metrics = FakeMetrics()
        watchdog = EventLoopWatchdog(threshold_ms=100, metrics=metrics)
        watchdog.note_lag(0.2)
        watchdog.note_lag(0.05)
        assert metrics.keys == ["loop_lag_events"]  # only the breach counts


class TestAgainstARealLoop:
    async def test_blocking_the_loop_is_reported(self, caplog) -> None:
        """A synchronous sleep inside the loop is exactly the failure mode."""
        metrics = type(
            "M", (), {"keys": [], "inc": lambda self, key, amount=1: self.keys.append(key)}
        )()
        watchdog = EventLoopWatchdog(interval_seconds=0.05, threshold_ms=40, metrics=metrics)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Watchdog"):
            task = watchdog.start()
            await asyncio.sleep(0.12)  # a few clean ticks
            time.sleep(0.2)  # freeze the loop (and the watchdog with it)
            await asyncio.sleep(0.12)  # let it observe the stall
            await watchdog.stop()
            assert task.done()

        assert watchdog.max_lag_ms >= 150.0  # it saw the 200 ms freeze
        assert watchdog.lag_events >= 1
        assert metrics.keys == ["loop_lag_events"] * watchdog.lag_events
        assert any("卡顿" in record.getMessage() for record in caplog.records)
        assert watchdog.stats()["max_lag_ms"] == watchdog.max_lag_ms

    async def test_a_quiet_loop_reports_nothing_alarming(self, caplog) -> None:
        watchdog = EventLoopWatchdog(interval_seconds=0.05, threshold_ms=1000)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Watchdog"):
            watchdog.start()
            await asyncio.sleep(0.2)
            await watchdog.stop()
        assert watchdog.ticks >= 2  # it really ran
        assert watchdog.lag_events == 0
        assert not caplog.records

    async def test_start_is_idempotent_and_stop_is_safe(self) -> None:
        watchdog = EventLoopWatchdog(interval_seconds=0.05, threshold_ms=10_000)
        first = watchdog.start()
        second = watchdog.start()
        assert first is second
        await watchdog.stop()
        await watchdog.stop()  # second stop is a no-op


class FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value

    def advance(self, delta: float) -> None:
        self.value += delta


class TestAttribution:
    def test_activity_overlap_reports_ops_in_window(self) -> None:
        clock = FakeClock(0.0)
        activity = BlockingActivity(clock=clock)
        with activity.track("memory.vector scoring"):
            clock.advance(0.5)
        clock.advance(0.1)
        with activity.track("web.render config"):
            clock.advance(0.3)

        names = [name for name, _ in activity.overlap(0.0, 1.0)]
        assert "memory.vector scoring" in names and "web.render config" in names
        # a window after the first op excludes it
        assert [name for name, _ in activity.overlap(0.6, 1.0)] == ["web.render config"]

    def test_warning_names_the_blocking_op(self, caplog) -> None:
        clock = FakeClock(0.0)
        activity = BlockingActivity(clock=clock)
        with activity.track("memory.vector scoring"):
            clock.advance(0.2)
        watchdog = EventLoopWatchdog(threshold_ms=100, clock=clock, activity=activity)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Watchdog"):
            watchdog.note_lag(0.2, window=(0.0, 0.2))
        assert any("memory.vector scoring" in record.getMessage() for record in caplog.records)
