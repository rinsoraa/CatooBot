"""Reply outcomes, milestone 1 (Task 20): the observation rows.

One row per answered *turn* (a turn is 2-3 bubbles, and MessageDelivery keeps
only the last id per scope), written as ``pending`` and settled later. Two
properties matter already at this stage:

* ``self_initiated`` — being @-ed nearly always ends in an answer, so those
  turns must never feed the engagement average;
* the row is idempotent per turn (a retried hook cannot double-count).
"""

from __future__ import annotations

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.memory.outbox import Outbox
from app.social.feedback import ReplyFeedbackStore, is_self_initiated


async def make_store(tmp_path, *, outbox: bool = False):  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'feedback.db'}"))
    await db.connect()
    box = Outbox(tmp_path / "outbox" / "pending.jsonl") if outbox else None
    return ReplyFeedbackStore(db, outbox=box, clock=lambda: 1_700_000_000.0), db, box


class TestSelfInitiated:
    def test_addressed_reasons_are_not_self_initiated(self) -> None:
        for reason in ("direct_mention", "reply_to_bot", "direct_follow_up"):
            assert is_self_initiated(reason) is False

    def test_her_own_openings_are(self) -> None:
        for reason in ("participation_rate", "topic_interest", "initiative", ""):
            assert is_self_initiated(reason) is True


class TestRecording:
    async def test_one_row_per_turn(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            assert await store.record_turn(
                turn_id="t1", scope_key="group:9", reason_code="participation_rate"
            )
            # a retried hook must not double-count
            await store.record_turn(
                turn_id="t1", scope_key="group:9", reason_code="participation_rate"
            )
            rows = await db.fetchall("SELECT * FROM reply_outcomes")
            assert len(rows) == 1
            row = rows[0]
            assert row["verdict"] == "pending" and row["scope_key"] == "group:9"
            assert row["self_initiated"] == 1 and row["window_seconds"] == 90.0
            assert row["polarity"] is None and row["settled_at"] is None
        finally:
            await db.close()

    async def test_addressed_turn_is_recorded_but_flagged(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await store.record_turn(turn_id="t2", scope_key="group:9", reason_code="direct_mention")
            row = await db.fetchone("SELECT * FROM reply_outcomes WHERE turn_id = 't2'")
            assert row is not None and row["self_initiated"] == 0  # recorded, not scored
        finally:
            await db.close()

    async def test_private_turns_are_recorded_without_a_group(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await store.record_turn(
                turn_id="t3", scope_key="private:7", reason_code="", is_group=False
            )
            row = await db.fetchone("SELECT * FROM reply_outcomes WHERE turn_id = 't3'")
            assert row is not None and row["is_group"] == 0
        finally:
            await db.close()

    async def test_empty_turn_id_is_refused(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            assert (
                await store.record_turn(turn_id="", scope_key="group:9", reason_code="x") is False
            )
            assert await db.fetchall("SELECT * FROM reply_outcomes") == []
        finally:
            await db.close()


class TestOutboxFallback:
    async def test_a_refused_write_is_queued(self, tmp_path) -> None:
        store, db, box = await make_store(tmp_path, outbox=True)
        await db.close()  # the database went away
        assert (
            await store.record_turn(turn_id="t4", scope_key="group:9", reason_code="topic_interest")
            is False
        )
        assert box is not None and await box.pending_count() == 1
        entry = (await box.pending())[0]
        assert entry.kind == "reply_outcome"
        assert entry.payload["turn_id"] == "t4" and entry.payload["self_initiated"] is True

    async def test_without_an_outbox_it_only_warns(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        await db.close()
        assert await store.record_turn(turn_id="t5", scope_key="group:9", reason_code="x") is False


class TestPendingSweep:
    async def test_only_rows_past_their_window_are_returned(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await store.record_turn(turn_id="old", scope_key="group:9", reason_code="x")
            await db.execute(
                "UPDATE reply_outcomes SET sent_at = sent_at - 120 WHERE turn_id = 'old'"
            )
            await store.record_turn(turn_id="new", scope_key="group:9", reason_code="x")
            pending = await store.pending_before(1_700_000_000.0)
            assert [row["turn_id"] for row in pending] == ["old"]
        finally:
            await db.close()

    async def test_mark_unknown_closes_rows(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await store.record_turn(turn_id="t6", scope_key="group:9", reason_code="x")
            row = await db.fetchone("SELECT id FROM reply_outcomes WHERE turn_id = 't6'")
            assert await store.mark_unknown([int(row["id"])], note="restart") == 1
            closed = await db.fetchone(
                "SELECT verdict, note FROM reply_outcomes WHERE turn_id = 't6'"
            )
            assert closed["verdict"] == "unknown" and closed["note"] == "restart"
            assert await store.pending_before(1_700_000_000.0) == []
        finally:
            await db.close()

    async def test_recent_returns_settled_rows_only(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await store.record_turn(turn_id="t7", scope_key="group:9", reason_code="x")
            assert await store.recent(days=7) == []  # still pending
            row = await db.fetchone("SELECT id FROM reply_outcomes WHERE turn_id = 't7'")
            await store.mark_unknown([int(row["id"])])
            assert [r["turn_id"] for r in await store.recent(days=7)] == ["t7"]
        finally:
            await db.close()


class TestMigration:
    async def test_table_and_indexes_exist(self, tmp_path) -> None:
        _store, db, _ = await make_store(tmp_path)
        try:
            names = {
                row["name"]
                for row in await db.fetchall(
                    "SELECT name FROM sqlite_master WHERE name LIKE '%reply_outcomes%'"
                )
            }
            assert {
                "reply_outcomes",
                "idx_reply_outcomes_turn",
                "idx_reply_outcomes_scope",
                "idx_reply_outcomes_verdict",
            } <= names
            applied = await db.fetchall("SELECT version FROM schema_migrations ORDER BY version")
            assert 18 in [row["version"] for row in applied]
        finally:
            await db.close()
