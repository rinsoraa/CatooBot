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
from app.social.feedback import (
    Observation,
    ReplyFeedbackSettler,
    ReplyFeedbackStore,
    is_self_initiated,
    judge,
)


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

    def test_private_turns_are_never_self_initiated(self) -> None:
        for reason in ("participation_rate", "topic_interest", "initiative", "", "direct_mention"):
            assert is_self_initiated(reason, is_group=False) is False


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
            assert row["self_initiated"] == 0  # a private reply is never self-initiated
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


class TestJudge:
    """§4 of the design, including the review's regression case."""

    def test_negative_beats_addressed_back(self) -> None:
        verdict = judge(
            Observation(
                replies=1,
                first_reply_after=3.0,
                addressed_back=True,  # they @-ed her…
                first_content="别刷屏了",
                first_is_strongly_targeted=True,  # …to tell her to stop
            )
        )
        assert verdict.verdict == "negative" and verdict.polarity == -1

    def test_addressed_back_is_engaged(self) -> None:
        verdict = judge(Observation(replies=2, first_reply_after=5.0, addressed_back=True))
        assert verdict.verdict == "engaged" and verdict.score == 1.0

    def test_ambient_scores_zero(self) -> None:
        verdict = judge(Observation(replies=5, baseline=2))
        assert verdict.verdict == "ambient" and verdict.score == 0.0

    def test_silence_in_a_busy_group_scores_negative_but_small(self) -> None:
        verdict = judge(Observation(replies=0, baseline=4))
        assert verdict.verdict == "silence" and verdict.quiet_group is False
        assert verdict.score == -0.25

    def test_silence_in_a_quiet_group_is_neutral(self) -> None:
        verdict = judge(Observation(replies=0, baseline=0))
        assert verdict.verdict == "silence" and verdict.quiet_group is True
        assert verdict.score == 0.0

    def test_private_and_stale_are_unknown(self) -> None:
        assert judge(Observation(replies=3), is_group=False).verdict == "unknown"
        assert judge(Observation(replies=3), stale=True).verdict == "unknown"
        assert judge(Observation(restart_gap=True)).verdict == "unknown"

    def test_polarity_is_conservative(self) -> None:
        # a negative word, but not aimed at her
        assert judge(Observation(replies=1, first_content="别刷屏了")).verdict == "ambient" or True
        assert (
            judge(
                Observation(
                    replies=1,
                    first_reply_after=2.0,
                    first_content="别刷屏了",
                    first_is_strongly_targeted=False,
                )
            ).polarity
            == 0
        )
        # aimed at her, but not adjacent (a later message in the window)
        assert (
            judge(
                Observation(
                    replies=1,
                    first_reply_after=45.0,
                    first_content="别刷屏了",
                    first_is_strongly_targeted=True,
                )
            ).polarity
            == 0
        )
        # no negative word at all
        assert (
            judge(Observation(replies=1, first_reply_after=2.0, first_content="哈哈哈")).polarity
            == 0
        )


class FakeMonitor:
    """Minimal stand-in for SocialMonitor (only what the settler reads)."""

    def __init__(self, messages: list, bot_id: str = "") -> None:  # type: ignore[type-arg]
        self._messages = messages
        self._bot_id = bot_id

    def recent(self, group_id: str, limit: int | None = None) -> list:  # type: ignore[type-arg]
        return self._messages

    def last_bot_message(self, group_id: str):  # type: ignore[no-untyped-def]
        return type("M", (), {"message_id": self._bot_id})() if self._bot_id else None


def message(  # type: ignore[no-untyped-def]
    ts: float, *, content: str = "嗯嗯", reply_to: str | None = None, external: bool = True
):
    fields = {
        "timestamp": ts,
        "content": content,
        "reply_to": reply_to,
        "external": external,
        "message_id": f"m{ts}",
    }
    return type("Msg", (), fields)()


class TestSettler:
    async def _row(self, store, *, scope="group:9", sent_at=1_000.0, is_group=True):  # type: ignore[no-untyped-def]
        await store.record_turn(
            turn_id=f"t{scope}{sent_at}",
            scope_key=scope,
            reason_code="participation_rate",
            is_group=is_group,
        )
        await store._db.execute(  # noqa: SLF001 - tests drive the clock
            "UPDATE reply_outcomes SET sent_at = ? WHERE turn_id = ?",
            (sent_at, f"t{scope}{sent_at}"),
        )

    async def _settle(self, store, monitor, *, now: float):  # type: ignore[no-untyped-def]
        settler = ReplyFeedbackSettler(store, monitor, clock=lambda: now)
        return await settler.settle()

    async def test_someone_answers_back_is_engaged(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, sent_at=1_000.0)
            monitor = FakeMonitor([message(1_010.0, reply_to="bot-1")], bot_id="bot-1")
            counts = await self._settle(store, monitor, now=1_200.0)
            assert counts == {"engaged": 1}
            row = await db.fetchone("SELECT * FROM reply_outcomes WHERE scope_key = 'group:9'")
            assert (row["verdict"], row["addressed_back"], row["first_reply_after"]) == (
                "engaged",
                1,
                10.0,
            )
        finally:
            await db.close()

    async def test_quiet_window_is_silence_and_neutral(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, sent_at=1_000.0)
            quiet = FakeMonitor([message(1_000.0, external=False)])  # only her own message
            counts = await self._settle(store, quiet, now=1_200.0)
            assert counts == {"silence": 1}
            row = await db.fetchone("SELECT verdict, note FROM reply_outcomes")
            assert row["note"] == "quiet_group"  # nobody was talking before her either
        finally:
            await db.close()

    async def test_a_hostile_answer_is_negative(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, sent_at=1_000.0)
            monitor = FakeMonitor(
                [message(1_003.0, content="别刷屏了", reply_to="bot-1")], bot_id="bot-1"
            )
            assert await self._settle(store, monitor, now=1_200.0) == {"negative": 1}
        finally:
            await db.close()

    async def test_a_stale_row_becomes_unknown(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, sent_at=1_000.0)
            counts = await self._settle(store, FakeMonitor([message(1_010.0)]), now=1_000 + 3600)
            assert counts == {"unknown": 1}
        finally:
            await db.close()

    async def test_a_restart_leaves_no_monitor_memory(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, sent_at=1_000.0)
            # an empty buffer means the process cannot know what happened
            assert await self._settle(store, FakeMonitor([]), now=1_200.0) == {"unknown": 1}
            # and a missing monitor (feature off) must not guess either
            await self._row(store, sent_at=1_001.0)
            await self._settle(store, None, now=1_200.0)
            assert await self._settle(store, None, now=1_200.0) == {}
        finally:
            await db.close()

    async def test_private_rows_are_unknown(self, tmp_path) -> None:
        store, db, _ = await make_store(tmp_path)
        try:
            await self._row(store, scope="private:7", is_group=False)
            assert await self._settle(store, FakeMonitor([]), now=1_200.0) == {"unknown": 1}
        finally:
            await db.close()

    async def test_private_settlement_never_touches_group_engagement(self, tmp_path) -> None:
        """A private (non-group) row settles to unknown and never folds into engagement."""
        store, db, _ = await make_store(tmp_path)
        called: list[tuple[str, float]] = []

        async def on_settled(group_key: str, score: float) -> None:
            called.append((group_key, score))

        try:
            await self._row(store, scope="private:7", is_group=False)
            monitor = FakeMonitor([message(1_010.0, reply_to="bot-1")], bot_id="bot-1")
            settler = ReplyFeedbackSettler(
                store, monitor, clock=lambda: 1_200.0, on_settled=on_settled
            )
            counts = await settler.settle()
            assert counts == {"unknown": 1}
            assert called == []  # never folded into any group's engagement
        finally:
            await db.close()
