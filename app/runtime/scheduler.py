"""Runtime tick scheduler (Phase 14 §9-§16).

    real clock → runtime tick scheduler → SandboxRuntime.tick() → existing
    Autonomous Life Loop (Goal / Action / Experience / Continuity)
                                   ↘ ActivityRuntime.advance()（Phase 6A：Episode 生命周期）

Deliberately thin (§9): the scheduler owns *when* the world is asked to
advance, and nothing else. It never touches a Goal, an Action, Memory or a
Relationship — those stay where they already are — and one tick is one bounded
world step, never a per-second replay (§13-§15) and never a model call (§11).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.sandbox.events import SandboxEventType as ET

#: a tick that finds the world this far behind is a *catch-up*, not a step
CATCHUP_MIN_SECONDS = 60.0


class RuntimeScheduler:
    """Drives the sandbox's existing tick on real time (§8/§9)."""

    def __init__(
        self,
        runtime: Any,
        *,
        config: Any,
        activity: Any = None,
        clock: Any = time.time,
        loop_clock: Any = None,
        logger: Any = None,
    ) -> None:
        self.runtime = runtime
        #: Phase 6A：可选的 Episode 生命周期推进器（不认识沙盒，也不产生世界动作）
        self.activity = activity
        self.config = config
        self._clock = clock
        #: monotonic source for the deadline (no drift, §67)
        self._loop_clock = loop_clock or (lambda: asyncio.get_running_loop().time())
        self._log = logger or getattr(runtime, "_log", None) if runtime is not None else logger
        self._task: asyncio.Task[Any] | None = None
        self.ticks = 0
        self.catchups = 0
        self.last_tick_at = 0.0
        self.last_report: dict[str, Any] = {}

    # ------------------------------------------------------------- lifecycle

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    @property
    def interval(self) -> float:
        return max(0.05, float(getattr(self.config, "tick_interval_seconds", 1.0)))

    async def start(self) -> None:
        """Start the single tick loop (§53: two starts still mean one loop)."""
        if self.running:
            return
        self._task = asyncio.create_task(self._run())
        if self._log is not None:
            self._log.info("[Runtime] tick scheduler started (every %.2fs)", self.interval)

    async def stop(self, *, timeout: float | None = None) -> None:
        """Stop the loop; the world itself is not touched (no forced tick)."""
        task, self._task = self._task, None
        if task is None:
            return
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=timeout or 1.0)
        except (asyncio.CancelledError, TimeoutError, Exception):  # noqa: BLE001 - shutdown path
            pass
        if self._log is not None:
            self._log.info("[Runtime] tick scheduler stopped (%d ticks)", self.ticks)

    # ----------------------------------------------------------------- loop

    async def _run(self) -> None:
        """Monotonic deadline loop (§67) — a slow tick never accumulates drift."""
        interval = self.interval
        deadline = self._loop_clock() + interval
        while True:
            delay = deadline - self._loop_clock()
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                await self.tick_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - a bad tick must not stop the world (§39)
                if self._log is not None:
                    self._log.exception("[Runtime] tick failed")
            deadline += interval
            now = self._loop_clock()
            if deadline < now:  # far behind: re-anchor instead of bursting (§68)
                deadline = now + interval

    async def tick_once(self) -> dict[str, Any]:
        """One bounded world step (§13-§15): never a per-second replay."""
        interval = self.interval
        elapsed = self._elapsed_seconds()
        catchup = elapsed >= CATCHUP_MIN_SECONDS and elapsed > interval * 1.5
        bounded = elapsed
        if catchup:
            bounded = min(
                elapsed, max(0.0, float(getattr(self.config, "max_catchup_seconds", 300.0)))
            )
        # §8: the world step *is* the real elapsed time (never the sandbox's
        # one-minute floor, which would run her life 60× too fast at a 1s cadence)
        minutes = max(0.0, bounded / 60.0)
        report = await self.runtime.tick(minutes=minutes) if self.runtime is not None else {}
        # Phase 6A §八：世界时间前进之后，让 Activity Runtime 推进 Episode 生命周期。
        # 它**只**做"到期 / 超时 / 排下一个"，不重新选择活动、不产生任何世界动作。
        if self.activity is not None:
            try:
                await self.activity.advance()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001 - Activity 故障只降级（6A §五十一）
                if self._log is not None:
                    self._log.exception("[Runtime] activity advance failed (ignored)")
        self.ticks += 1
        self.last_tick_at = float(self._clock())
        self.last_report = dict(report or {})
        if catchup:
            self.catchups += 1
            self._trace(
                ET.RUNTIME_CATCHUP,
                {
                    "elapsed_seconds": round(elapsed, 3),
                    "processed_seconds": round(bounded, 3),
                    "minutes": minutes,
                },
            )
        self._trace(
            ET.RUNTIME_TICK,
            {
                "minutes": round(minutes, 4),
                "catchup": catchup,
                "completed": bool(self.last_report.get("completed")),
            },
        )
        return self.last_report

    def _elapsed_seconds(self) -> float:
        """World time is whatever the sandbox last advanced to (the anchor)."""
        if self.runtime is None:
            return 0.0
        last = float(getattr(self.runtime, "_last_tick", 0.0) or 0.0)  # noqa: SLF001 - same runtime
        if last <= 0:
            return 0.0
        return max(0.0, float(self._clock()) - last)

    def _trace(self, event_type: ET, payload: dict[str, Any]) -> Any:
        """Trace only (§65/§66): a tick log never moves a revision."""
        if self.runtime is None:
            return None
        return self.runtime.events.publish(event_type, source="runtime", payload=payload)
