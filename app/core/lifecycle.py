"""Startup / shutdown bookkeeping and graceful Ctrl+C handling."""

from __future__ import annotations

import asyncio
import signal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot


class Lifecycle:
    """Tracks bot state and runs until interrupted."""

    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._stop_event = asyncio.Event()
        self._ready = False

    @property
    def is_ready(self) -> bool:
        return self._ready

    def mark_ready(self) -> None:
        self._ready = True

    def mark_stopped(self) -> None:
        self._ready = False
        self._stop_event.set()

    def request_stop(self) -> None:
        """Ask the main loop to finish (used by signal handlers)."""
        self._stop_event.set()

    async def wait_for_signal(self) -> None:
        """Block until Ctrl+C / SIGTERM or mark_stopped()."""
        loop = asyncio.get_running_loop()
        stop_task = asyncio.create_task(self._stop_event.wait())
        for sig_name in ("SIGINT", "SIGTERM"):
            sig = getattr(signal, sig_name, None)
            if sig is None:  # e.g. SIGTERM absent on some Windows setups
                continue
            try:
                loop.add_signal_handler(sig, self._on_signal, sig_name)
            except NotImplementedError:
                # Windows ProactorEventLoop: fall back to signal.signal
                import functools

                signal.signal(sig, functools.partial(self._on_signal_sync, sig_name))

        try:
            await stop_task
        finally:
            for sig_name in ("SIGINT", "SIGTERM"):
                sig = getattr(signal, sig_name, None)
                if sig is not None:
                    try:
                        loop.remove_signal_handler(sig)
                    except NotImplementedError:
                        pass

    # ------------------------------------------------------------- handlers

    def _on_signal(self, sig_name: str) -> None:
        self._bot.log.info("Received %s, shutting down gracefully...", sig_name)
        self._stop_event.set()

    def _on_signal_sync(self, sig_name: str, frame: Any) -> None:
        self._bot.log.info("Received %s, shutting down gracefully...", sig_name)
        self._stop_event.set()
