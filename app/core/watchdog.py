"""Event-loop stall watchdog (Task 16).

CatooBot is one process with one event loop shared by QQ messages, the WebUI
and every background job: one blocking call anywhere (a sync DB read, a slow
file scan, a careless loop) freezes everything at once, and the symptom is
usually "the bot went quiet" with nothing in the log.

This watchdog measures the loop's own scheduling delay: every
``watchdog_interval_seconds`` it asks the loop to wake it up and compares when
it actually ran against when it should have. A delay above
``watchdog_threshold_ms`` is logged as a WARNING (with the size of the stall)
and counted.

It deliberately does *not* run as a scheduler job: a job measures the
scheduler's queue, not the loop. One small task, no thread, no second loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import Any


class EventLoopWatchdog:
    def __init__(
        self,
        *,
        interval_seconds: float = 1.0,
        threshold_ms: int = 500,
        logger: logging.Logger | None = None,
        metrics: Any = None,
        clock: Any = time.perf_counter,
    ) -> None:
        self._interval = max(0.05, float(interval_seconds))
        self._threshold = (threshold_ms / 1000.0, threshold_ms)
        self._log = logger or logging.getLogger("CatooBot.Watchdog")
        self._metrics = metrics
        self._clock = clock
        self._task: asyncio.Task | None = None
        self.lag_events = 0
        self.max_lag_ms = 0.0
        self.last_lag_ms = 0.0
        self.ticks = 0

    # ------------------------------------------------------------- lifecycle

    def start(self) -> asyncio.Task:
        """Run the watchdog on the current loop (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="event-loop-watchdog")
        return self._task

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    async def run(self) -> None:
        """Measure the gap between "should have woken" and "actually woke"."""
        while True:
            expected = self._clock() + self._interval
            await asyncio.sleep(self._interval)
            self.note_lag(self.measure(expected, self._clock()))

    # ---------------------------------------------------------- measurement

    def measure(self, expected: float, actual: float) -> float:
        """Seconds the loop was late waking this tick (never negative)."""
        return max(0.0, actual - expected)

    def note_lag(self, lag_seconds: float) -> bool:
        """Record one tick; report it when it crossed the threshold."""
        self.ticks += 1
        lag_ms = lag_seconds * 1000.0
        self.last_lag_ms = round(lag_ms, 1)
        if lag_ms > self.max_lag_ms:
            self.max_lag_ms = round(lag_ms, 1)
        if lag_seconds < self._threshold[0]:
            return False
        self.lag_events += 1
        if self._metrics is not None:
            self._metrics.inc("loop_lag_events")
        self._log.warning(
            "[Watchdog] 事件循环卡顿：本次晚了 %.0f ms（阈值 %d ms，累计 %d 次，最严重 %.0f ms）"
            " — 单进程单循环，QQ 与 WebUI 这段时间都被阻塞了",
            lag_ms,
            self._threshold[1],
            self.lag_events,
            self.max_lag_ms,
        )
        return True

    def stats(self) -> dict[str, Any]:
        return {
            "enabled": True,
            "interval_seconds": self._interval,
            "threshold_ms": self._threshold[1],
            "ticks": self.ticks,
            "lag_events": self.lag_events,
            "last_lag_ms": self.last_lag_ms,
            "max_lag_ms": self.max_lag_ms,
        }
