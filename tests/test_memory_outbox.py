"""Memory write outbox (Task 14): a database outage must not lose memories.

Before this, a failed write was simply gone — extraction runs in a background
task, so even the exception stayed invisible. Now the *intent* is appended to a
JSONL outbox next to the database, and the scheduler replays it (through the
normal write path, dedup included) once the database answers again.
"""

from __future__ import annotations

import json
import logging

from app.config.settings import DatabaseConfig, MemoryConfig
from app.core.metrics import Metrics
from app.database.database import Database
from app.memory.consolidation import MemoryConsolidator
from app.memory.manager import MemoryManager
from app.memory.outbox import Outbox, OutboxEntry, outbox_path_for

MEMORY_TEXT = "用户养了一只叫抹茶的猫"


def make_wiring(tmp_path, *, url: str | None = None):  # type: ignore[no-untyped-def]
    database_url = url or f"sqlite:///{tmp_path / 'outbox.db'}"
    db = Database(DatabaseConfig(url=database_url))
    metrics = Metrics()
    outbox_file = outbox_path_for(database_url)
    outbox = Outbox(outbox_file, metrics=metrics) if outbox_file else None
    manager = MemoryManager(MemoryConfig(semantic={"enabled": False}), db, outbox=outbox)
    return manager, db, outbox, metrics


async def connect(tmp_path):  # type: ignore[no-untyped-def]
    manager, db, outbox, metrics = make_wiring(tmp_path)
    await db.connect()
    return manager, db, outbox, metrics


async def health_of(manager: MemoryManager) -> dict:  # type: ignore[type-arg]
    """The health dict the WebUI memory page renders (the consolidator owns it)."""
    consolidator = MemoryConsolidator(MemoryConfig(semantic={"enabled": False}), manager)
    return await consolidator.health()


class TestPathDerivation:
    def test_outbox_lives_beside_the_database(self) -> None:
        path = outbox_path_for("sqlite:///tmp/x/test.db")
        assert path is not None
        assert path.parent.name == "outbox" and path.name == "pending.jsonl"

    def test_non_sqlite_or_memory_databases_have_no_outbox(self) -> None:
        assert outbox_path_for("sqlite:///:memory:") is None
        assert outbox_path_for("postgresql://host/db") is None

    def test_entry_round_trip(self) -> None:
        """A queued entry keeps its kind/payload exactly — the file is the contract."""
        entry = OutboxEntry(kind="remember", payload={"scope": "user", "ref": "1"}, created_at=5.0)
        restored = OutboxEntry.from_line(entry.to_json(), now=0.0)
        assert restored is not None
        assert (restored.kind, restored.payload, restored.created_at) == (
            "remember",
            {"scope": "user", "ref": "1"},
            5.0,
        )
        assert OutboxEntry.from_line(json.dumps({"kind": ""}), now=0.0) is None


class TestOutageAndRecovery:
    async def test_write_failure_is_queued_then_replayed(self, tmp_path) -> None:
        manager, db, outbox, metrics = await connect(tmp_path)
        try:
            await db.close()  # the outage
            try:
                await manager.remember("user", "1", MEMORY_TEXT)
                raise AssertionError("the write should have failed")
            except RuntimeError:
                pass  # expected: the connection is gone

            assert outbox is not None
            assert await outbox.pending_count() == 1
            assert metrics.get("outbox_enqueued") == 1

            await db.connect()  # the database is back
            report = await manager.replay_outbox()
            assert report["replayed"] == 1 and report["remaining"] == 0
            assert await outbox.pending_count() == 0
            assert metrics.get("outbox_replayed") == 1
            stored = await manager.list_memories(scope_key="user:1")
            assert [m.content for m in stored] == [MEMORY_TEXT]
        finally:
            await db.close()

    async def test_queue_survives_a_restart(self, tmp_path) -> None:
        manager, db, _outbox, _ = await connect(tmp_path)
        try:
            await db.close()
            try:
                await manager.remember("user", "1", MEMORY_TEXT)
            except RuntimeError:
                pass
        finally:
            await db.close()

        # a fresh process: new objects, same file
        manager2, db2, outbox2, _ = await connect(tmp_path)
        try:
            assert outbox2 is not None and await outbox2.pending_count() == 1
            assert (await manager2.replay_outbox())["replayed"] == 1
            stored = await manager2.list_memories(scope_key="user:1")
            assert [m.content for m in stored] == [MEMORY_TEXT]
        finally:
            await db2.close()

    async def test_replay_reuses_dedup_so_it_cannot_duplicate(self, tmp_path) -> None:
        manager, db, outbox, _ = await connect(tmp_path)
        try:
            await manager.remember("user", "1", "用户喜欢喝冰可乐")
            assert outbox is not None
            # the classic case: the row went in, the commit failed, the intent
            # still got queued — replay must reinforce, not duplicate
            await outbox.enqueue(
                "remember", {"scope": "user", "ref": "1", "content": "用户喜欢喝冰可乐"}
            )
            assert (await manager.replay_outbox())["replayed"] == 1
            stored = await manager.list_memories(scope_key="user:1")
            assert len(stored) == 1
        finally:
            await db.close()

    async def test_successful_writes_leave_the_outbox_empty(self, tmp_path) -> None:
        manager, db, outbox, metrics = await connect(tmp_path)
        try:
            await manager.remember("user", "1", MEMORY_TEXT)
            assert outbox is not None
            assert await outbox.pending_count() == 0
            assert metrics.get("outbox_enqueued") == 0
        finally:
            await db.close()

    async def test_rejected_content_is_not_queued(self, tmp_path) -> None:
        """Too-short content is a decision, not a failure — nothing to replay."""
        manager, db, outbox, _ = await connect(tmp_path)
        try:
            await db.close()
            assert await manager.remember("user", "1", "嗯") is None
            assert outbox is not None and await outbox.pending_count() == 0
        finally:
            await db.close()


class TestReplayHygiene:
    async def test_entries_that_still_fail_stay_queued(self, tmp_path) -> None:
        manager, db, outbox, metrics = await connect(tmp_path)
        try:
            assert outbox is not None
            await outbox.enqueue("unknown_kind", {"whatever": True})
            report = await manager.replay_outbox()
            assert report["failed"] == 1 and report["remaining"] == 1
            assert await outbox.pending_count() == 1
            assert metrics.get("outbox_failed") == 1
        finally:
            await db.close()

    async def test_unreadable_lines_are_dropped_once(self, tmp_path, caplog) -> None:
        manager, db, outbox, metrics = await connect(tmp_path)
        try:
            assert outbox is not None
            await outbox.enqueue("remember", {"scope": "user", "ref": "1", "content": MEMORY_TEXT})
            with outbox.path.open("a", encoding="utf-8") as handle:
                handle.write("{ this is not json\n")

            with caplog.at_level(logging.WARNING, logger="CatooBot.Memory.Outbox"):
                report = await manager.replay_outbox()
            assert report["replayed"] == 1 and report["dropped"] == 1
            assert metrics.get("outbox_dropped") == 1
            assert any("unreadable line" in r.getMessage() for r in caplog.records)
            assert await outbox.pending_count() == 0  # the corrupt line is gone for good
        finally:
            await db.close()

    async def test_stats_feed_the_webui_health(self, tmp_path) -> None:
        manager, db, outbox, _ = await connect(tmp_path)
        try:
            assert outbox is not None
            await outbox.enqueue("remember", {"scope": "user", "ref": "1", "content": MEMORY_TEXT})
            stats = await manager.outbox_stats()
            assert stats["enabled"] is True and stats["pending"] == 1
            health = await health_of(manager)
            assert health["outbox"]["pending"] == 1
        finally:
            await db.close()
