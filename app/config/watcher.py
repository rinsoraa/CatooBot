"""Config file watcher (Task 23): hot-reload ``config.yaml`` on change.

Polling (not inotify/watchfiles) so no new dependency is needed and it works
everywhere. A hand-edit to ``config.yaml`` re-runs the full load → ``models:``
expansion → model-reference validation, then hot-applies the result through
the config admin service. A broken edit (syntax error, unknown model name) is
reported and the previous config stays live — the bot never restarts or
crashes because of a hand-edit in progress.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path

ChangeHandler = Callable[[], Awaitable[None]]


class ConfigFileWatcher:
    def __init__(
        self,
        *,
        paths: list[Path],
        interval_seconds: float = 2.0,
        on_change: ChangeHandler | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._paths = [Path(p) for p in paths]
        self._interval = max(0.1, float(interval_seconds))
        self._on_change = on_change
        self._log = logger or logging.getLogger("CatooBot.Config.Watch")
        self._task: asyncio.Task[None] | None = None
        self._mtimes: dict[Path, float | None] = {}

    # ------------------------------------------------------------- lifecycle

    def start(self) -> asyncio.Task[None]:
        """Run on the current loop (idempotent)."""
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.run(), name="config-file-watcher")
        return self._task

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
        self._task = None

    # ------------------------------------------------------------ internals

    def _snapshot(self) -> dict[Path, float | None]:
        out: dict[Path, float | None] = {}
        for path in self._paths:
            try:
                out[path] = path.stat().st_mtime
            except OSError:
                out[path] = None
        return out

    async def run(self) -> None:
        previous = self._snapshot()  # baseline: never fire on startup
        while True:
            await asyncio.sleep(self._interval)
            current = self._snapshot()
            if current == previous:
                continue
            changed = [p.name for p in self._paths if current.get(p) != previous.get(p)]
            previous = current
            self._log.info("[Config.Watch] 检测到配置改动：%s", ", ".join(changed))
            if self._on_change is None:
                continue
            try:
                await self._on_change()
            except Exception as exc:  # noqa: BLE001 - a bad edit must not kill the watcher
                self._log.error("[Config.Watch] 配置热重载失败（保持旧配置）：%s", exc)
