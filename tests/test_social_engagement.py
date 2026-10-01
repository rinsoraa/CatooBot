"""Engagement memory (Task 20, milestone 3): time decay, dead zone, soft feed.

The point of this module is what it *refuses* to do: no sample-based decay (the
half-life would depend on how often she speaks — the quantity this loop
controls), no factor before eight settled turns, no effect on the hard gate at
all (cooldown / daily budget / switches never read it).
"""

from __future__ import annotations

import pytest

from app.config.settings import SocialConfig
from app.social.attention import SocialAttention
from app.social.engagement import (
    FACTOR_MAX,
    FACTOR_MIN,
    HALF_LIFE_SECONDS,
    MIN_SAMPLES,
    SocialEngagement,
)
from app.social.policy import ParticipationPolicy


def engagement(*, now: float = 1_000_000.0):  # type: ignore[no-untyped-def]
    clock = [now]
    return SocialEngagement(clock=lambda: clock[0]), clock


class TestDecay:
    def test_decay_is_time_based_not_sample_based(self) -> None:
        engine, clock = engagement()
        engine.note("g", 1.0)
        assert engine.ema("g") == pytest.approx(1.0)
        clock[0] += HALF_LIFE_SECONDS  # one half-life later…
        assert engine.ema("g") == pytest.approx(0.5, abs=1e-3)  # …half of it is gone

    def test_many_samples_do_not_slow_the_decay(self) -> None:
        busy, busy_clock = engagement()
        idle, idle_clock = engagement()
        for _ in range(50):
            busy.note("g", 1.0)
        idle.note("g", 1.0)
        busy_clock[0] += HALF_LIFE_SECONDS
        idle_clock[0] += HALF_LIFE_SECONDS
        assert busy.ema("g") == pytest.approx(idle.ema("g"), abs=1e-6)

    def test_zero_elapsed_decays_nothing(self) -> None:
        engine, _clock = engagement()
        engine.note("g", 0.4)
        assert engine.ema("g") == pytest.approx(0.4)


class TestDeadZoneAndFactor:
    def test_factor_is_exactly_one_below_the_sample_floor(self) -> None:
        engine, _clock = engagement()
        for _ in range(MIN_SAMPLES - 1):
            engine.note("g", -1.0)  # even a terrible record…
        assert engine.factor("g") == 1.0  # …cannot move the factor yet

    def test_factor_moves_after_the_floor(self) -> None:
        engine, _clock = engagement()
        for _ in range(MIN_SAMPLES):
            engine.note("g", -1.0)
        assert engine.factor("g") == pytest.approx(FACTOR_MIN, abs=1e-6)

    def test_factor_is_clamped_in_both_directions(self) -> None:
        engine, _clock = engagement()
        for _ in range(40):
            engine.note("g", 1.0)
        assert engine.factor("g") == pytest.approx(FACTOR_MAX, abs=1e-6)
        assert engine.factor("unknown-group") == 1.0

    def test_truly_neutral_ema_is_one(self) -> None:
        engine, _clock = engagement()
        for _ in range(20):
            engine.note("g", 0.0)
        assert engine.factor("g") == 1.0


class TestPersistence:
    def test_snapshot_round_trips_and_decays_on_read(self) -> None:
        engine, clock = engagement()
        for _ in range(MIN_SAMPLES):
            engine.note("g", -1.0)
        saved = engine.snapshot()

        clock[0] += HALF_LIFE_SECONDS
        restored, restored_clock = engagement(now=clock[0])
        restored.load(saved)
        # half a week later the memory is worth half as much — and it survived
        assert restored.samples("g") == MIN_SAMPLES
        assert restored.ema("g") == pytest.approx(-0.5, abs=1e-3)

    def test_garbage_snapshots_are_ignored(self) -> None:
        engine, _clock = engagement()
        engine.load(None)
        engine.load({"groups": "nope"})
        assert engine.stats()["groups"] == 0


class TestSoftFeedback:
    def test_credit_is_unchanged_while_the_ema_is_neutral(self) -> None:
        """The hard-gate premise: with no samples the factor is exactly 1.0."""
        base = ParticipationPolicy(config=SocialConfig())
        with_engine = ParticipationPolicy(
            config=SocialConfig(), engagement=SocialEngagement(clock=lambda: 0.0)
        )
        for policy in (base, with_engine):
            assert policy.credit_participation("g", 0.5) is False
            assert policy.credit_participation("g", 0.5) is True  # 1.0 reached
            assert policy.credit_participation("g", 0.5) is False

    def test_a_low_engagement_slows_the_credit_within_bounds(self) -> None:
        engine = SocialEngagement(clock=lambda: 0.0)
        for _ in range(MIN_SAMPLES):
            engine.note("g", -1.0)
        policy = ParticipationPolicy(config=SocialConfig(), engagement=engine)
        policy.credit_participation("g", 0.9)  # 0.9 × 0.9 = 0.81, not enough
        assert policy.credit_participation("g", 0.25) is True  # 0.81 + 0.225 ≥ 1

    def test_a_high_engagement_speeds_it_up_within_bounds(self) -> None:
        engine = SocialEngagement(clock=lambda: 0.0)
        for _ in range(MIN_SAMPLES):
            engine.note("g", 1.0)
        policy = ParticipationPolicy(config=SocialConfig(), engagement=engine)
        assert policy.credit_participation("g", 0.95) is True  # 0.95 × 1.1 > 1


class TestAttentionFeed:
    def test_momentum_step_is_small(self) -> None:
        attention = SocialAttention(clock=lambda: 0.0)
        before = attention.get("g").momentum
        attention.note_reply_outcome("g", 1.0)
        assert attention.get("g").momentum == pytest.approx(before + 0.1, abs=1e-6)

    def test_negative_only_cancels_and_never_goes_below_zero(self) -> None:
        attention = SocialAttention(clock=lambda: 0.0)
        attention.note_reply_outcome("g", -1.0)
        assert attention.get("g").momentum == 0.0
        attention.note_reply_outcome("g", 1.0)
        raised = attention.get("g").momentum
        attention.note_reply_outcome("g", -1.0)
        assert 0.0 <= attention.get("g").momentum < raised

    def test_momentum_stays_within_bounds(self) -> None:
        attention = SocialAttention(clock=lambda: 0.0)
        for _ in range(50):
            attention.note_reply_outcome("g", 1.0)
        assert attention.get("g").momentum <= 1.0


class TestPersistenceWiring:
    async def test_store_round_trips_the_engagement_snapshot(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database
        from app.social.feedback import ReplyFeedbackStore

        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'eng.db'}"))
        await db.connect()
        try:
            store = ReplyFeedbackStore(db, clock=lambda: 1_700_000_000.0)
            local, _clock = engagement()
            for _ in range(MIN_SAMPLES):
                local.note("g", -1.0)
            assert await store.save_engagement(local.snapshot())
            restored, _c = engagement()
            restored.load(await store.load_engagement())
            assert restored.samples("g") == MIN_SAMPLES
            assert restored.factor("g") == pytest.approx(FACTOR_MIN, abs=1e-6)
        finally:
            await db.close()

    async def test_prune_drops_old_settled_rows_only(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database
        from app.social.feedback import ReplyFeedbackStore

        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'prune.db'}"))
        await db.connect()
        try:
            store = ReplyFeedbackStore(db, clock=lambda: 1_700_000_000.0)
            await store.record_turn(turn_id="old", scope_key="group:9", reason_code="x")
            await store.record_turn(turn_id="fresh", scope_key="group:9", reason_code="x")
            await db.execute(
                "UPDATE reply_outcomes SET verdict = 'silence', sent_at = sent_at - 40 * 86400"
                " WHERE turn_id = 'old'"
            )
            await db.execute(
                "UPDATE reply_outcomes SET verdict = 'engaged' WHERE turn_id = 'fresh'"
            )
            assert await store.prune(30) == 1
            remaining = [row["turn_id"] for row in await store.recent(days=90)]
            assert remaining == ["fresh"]
        finally:
            await db.close()

    async def test_outbox_replay_routes_reply_outcomes(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database
        from app.memory.outbox import Outbox
        from app.social.feedback import ReplyFeedbackStore

        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'replay.db'}"))
        await db.connect()
        try:
            box = Outbox(tmp_path / "outbox.jsonl", clock=lambda: 1_700_000_000.0)
            store = ReplyFeedbackStore(db, outbox=box, clock=lambda: 1_700_000_000.0)
            box.register("reply_outcome", store.replay_entry)
            await box.enqueue(
                "reply_outcome",
                {
                    "turn_id": "t-replay",
                    "scope_key": "group:9",
                    "reason_code": "participation_rate",
                    "self_initiated": True,
                    "sent_at": 1_699_999_000.0,
                    "window_seconds": 90.0,
                },
            )
            report = await box.replay()
            assert report.replayed == 1 and report.remaining == 0
            row = await db.fetchone("SELECT * FROM reply_outcomes WHERE turn_id = 't-replay'")
            assert row is not None and row["verdict"] == "pending" and row["self_initiated"] == 1
        finally:
            await db.close()


class TestWebuiCard:
    def _card(self, feedback: dict) -> str:  # type: ignore[type-arg]
        from app.web.routes.social import SocialRoutes

        return SocialRoutes._reply_feedback_card(feedback)

    def test_unavailable_and_empty_states(self) -> None:
        assert "暂无" in self._card({"available": False})
        assert "还没有可结算" in self._card({"available": True, "total": 0})

    def test_renders_the_rollup(self) -> None:
        html = self._card(
            {
                "available": True,
                "total": 10,
                "engaged_rate": 0.4,
                "median_first_reply": 12.0,
                "quiet_group": 3,
                "silence": 5,
                "negative": 1,
                "ambient": 2,
                "unknown": 2,
                "by_reason": {"participation_rate": {"total": 6, "engaged": 3}},
                "engagement": {"min_samples": 8, "per_group": {"9": {"ema": 0.2}}},
            }
        )
        assert "她最近说得怎么样" in html
        assert "40%" in html and "12s" in html
        assert "participation_rate" in html
        assert "不参与参与度系数" in html  # the addressed-turn note
