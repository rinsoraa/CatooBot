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
