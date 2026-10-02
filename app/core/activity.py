"""Loop-blocking activity tracker for the watchdog.

CatooBot is one process with one event loop. A coroutine that runs a
*synchronous* heavy operation — pure-Python vector scoring, HTML string
building, JSON parsing, a sandbox simulation step — blocks every other
coroutine (QQ, the WebUI, the watchdog itself) for its whole duration. The
watchdog measures the resulting stall but cannot name the cause on its own:
by the time it wakes, the blocking call has already returned.

This tracker gives the watchdog that attribution. A blocking call site wraps
its synchronous body in :func:`track_blocking` (or marks a whole helper with
:func:`blocking_fn`)::

    with track_blocking("memory.vector scoring"):
        ...  # pure-Python dot products over the candidate pool

Each completed op is kept in a small ring buffer with its start/end timestamps;
:meth:`BlockingActivity.overlap` returns the ops that were running during a
given stall window, and the watchdog appends them to its WARNING. It is
best-effort and effectively free when idle (one deque append per op).

The tracker is deliberately a module-level singleton: it must outlive any
single component and be reachable from unrelated call sites without plumbing a
reference through every constructor.
"""

from __future__ import annotations

import functools
import time
from collections import deque
from collections.abc import Callable, Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import TypeVar

BlockingOp = tuple[str, float, float]  # (name, start, end) — monotonic seconds

F = TypeVar("F", bound=Callable[..., object])


class BlockingActivity:
    """Records sync, loop-blocking operations with their timing windows."""

    def __init__(self, *, maxlen: int = 64, clock: Callable[[], float] = time.perf_counter) -> None:
        self._history: deque[BlockingOp] = deque(maxlen=maxlen)
        #: nested begin() calls, innermost last — only relevant during a stall
        self._active: list[tuple[str, float]] = []
        self._clock = clock

    @contextmanager
    def track(self, name: str) -> Iterator[None]:
        start = self._clock()
        self._active.append((name, start))
        try:
            yield
        finally:
            end = self._clock()
            self._active.pop()
            self._history.append((name, start, end))

    def overlap(self, window_start: float, window_end: float) -> list[tuple[str, float]]:
        """Ops running at any point in ``[window_start, window_end]``, newest first.

        Returns ``(name, duration_ms)`` pairs; each duration is clamped to the
        part of the op that actually fell inside the window, so a short op that
        merely straddled the boundary is not reported as lasting the whole stall.
        """
        found: list[tuple[str, float]] = []
        for name, start, end in reversed(self._history):
            if end <= window_start or start >= window_end:
                continue
            duration = (min(end, window_end) - max(start, window_start)) * 1000.0
            found.append((name, duration))
        for name, start in reversed(self._active):
            if start < window_end:
                duration = (window_end - max(start, window_start)) * 1000.0
                found.append((name, duration))
        return found


_ACTIVITY = BlockingActivity()


def track_blocking(name: str) -> AbstractContextManager[None]:
    """Context manager marking a synchronous, loop-blocking operation."""
    return _ACTIVITY.track(name)


def blocking_fn(name: str) -> Callable[[F], F]:
    """Decorate a synchronous helper whose whole body blocks the loop."""

    def decorator(fn: F) -> F:
        @functools.wraps(fn)
        def wrapper(*args: object, **kwargs: object) -> object:
            with _ACTIVITY.track(name):
                return fn(*args, **kwargs)

        return wrapper  # type: ignore[return-value]

    return decorator
