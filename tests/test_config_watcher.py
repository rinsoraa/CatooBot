"""Config file watcher (Task 23): hand-edits to config.yaml hot-reload."""

from __future__ import annotations

import asyncio
import os

from app.config.watcher import ConfigFileWatcher


def _bump_mtime(path, offset: float = 2.0) -> None:
    """Force an mtime change so the poll reliably sees it (sub-second writes
    on some filesystems would otherwise look like no change)."""
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + offset))


async def _wait_for(predicate, timeout: float = 5.0) -> bool:  # type: ignore[no-untyped-def]
    for _ in range(int(timeout / 0.1)):
        if predicate():
            return True
        await asyncio.sleep(0.1)
    return predicate()


class TestConfigFileWatcher:
    async def test_fires_once_on_change(self, tmp_path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\n", encoding="utf-8")
        calls: list[int] = []

        async def on_change() -> None:
            calls.append(1)

        watcher = ConfigFileWatcher(paths=[path], interval_seconds=0.1, on_change=on_change)
        watcher.start()
        try:
            await asyncio.sleep(0.3)  # baseline — no fire on startup
            assert calls == []
            _bump_mtime(path)
            assert await _wait_for(lambda: bool(calls)), "改动后应触发回调"
            assert calls == [1], calls
        finally:
            await watcher.stop()

    async def test_survives_a_broken_reload(self, tmp_path) -> None:
        path = tmp_path / "config.yaml"
        path.write_text("a: 1\n", encoding="utf-8")
        calls: list[int] = []

        async def broken() -> None:
            calls.append(1)
            raise RuntimeError("reload failed")

        watcher = ConfigFileWatcher(paths=[path], interval_seconds=0.1, on_change=broken)
        watcher.start()
        try:
            await asyncio.sleep(0.3)
            _bump_mtime(path)
            assert await _wait_for(lambda: bool(calls)), "改动后应触发回调"
            assert calls == [1], "回调抛异常不应被重复调用"
            # the watcher stays alive for the next edit
            _bump_mtime(path)
            assert await _wait_for(lambda: len(calls) >= 2), "异常后 watcher 应继续工作"
        finally:
            await watcher.stop()
