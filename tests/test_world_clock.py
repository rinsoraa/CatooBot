"""World clock tests (v0.8 §12-§16): timezone, periods, elapsed, day bounds.

Spec coverage: logical time, configurable timezone, period mapping, day/hour
boundaries used by every limit in the world, and the simulator offset.
"""

from __future__ import annotations

from app.world.clock import WorldClock, is_sleep_period, period_for
from tests.world_helpers import FakeTime, local_stamp


class TestPeriods:
    def test_period_boundaries(self) -> None:
        assert period_for(0) == "late_night"
        assert period_for(4) == "late_night"
        assert period_for(5) == "early_morning"
        assert period_for(8) == "morning"
        assert period_for(12) == "noon"
        assert period_for(14) == "afternoon"
        assert period_for(18) == "evening"
        assert period_for(23) == "night"

    def test_sleep_period(self) -> None:
        assert is_sleep_period(23)
        assert is_sleep_period(3)
        assert not is_sleep_period(9)


class TestSnapshot:
    def test_local_time_resolved(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp(hour=14)))
        moment = clock.snapshot()
        assert moment.time_text == "14:30"
        assert moment.period == "afternoon"
        assert moment.day_key == "2026-06-15"
        assert moment.hour_bucket == "2026-06-15T14"
        assert moment.weekday == "星期一"
        assert moment.is_weekend is False

    def test_weekend_detected(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp(day=20, hour=11)))
        moment = clock.snapshot()
        assert moment.is_weekend is True
        assert "周末" in moment.describe()

    def test_unknown_timezone_falls_back(self) -> None:
        clock = WorldClock(timezone="Not/AZone", clock=FakeTime(local_stamp()))
        assert clock.now() is not None  # warns, does not raise

    def test_local_timezone_name_kept(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp()))
        assert clock.timezone_name == "Asia/Singapore"


class TestElapsed:
    def test_elapsed_and_hours(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp()))
        past = local_stamp() - 7200
        assert clock.elapsed_since(past) == 7200
        assert clock.hours_since(past) == 2.0

    def test_unknown_timestamp_is_zero(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp()))
        assert clock.elapsed_since(0) == 0.0
        assert clock.elapsed_since(None) == 0.0

    def test_days_between(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp()))
        start = local_stamp(day=13)
        assert round(clock.days_between(start, local_stamp()), 2) == 2.0

    def test_day_and_hour_bounds(self) -> None:
        clock = WorldClock(
            timezone="Asia/Singapore", clock=FakeTime(local_stamp(hour=14, minute=30))
        )
        assert clock.today_key() == "2026-06-15"
        assert clock.start_of_day() == local_stamp(hour=0, minute=0)
        assert clock.start_of_hour() == local_stamp(hour=14, minute=0)


class TestSimulationOffset:
    def test_advance_moves_character_time(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp(hour=14)))
        assert clock.snapshot().hour == 14
        clock.advance(3600 * 8)
        assert clock.snapshot().hour == 22
        assert clock.offset_seconds == 3600 * 8

    def test_reset_offset(self) -> None:
        clock = WorldClock(timezone="Asia/Singapore", clock=FakeTime(local_stamp()))
        clock.advance(600)
        clock.reset_offset()
        assert clock.offset_seconds == 0.0
