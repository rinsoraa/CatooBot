"""Activity, presence (time/sleep/DND) and state-inertia tests."""

from __future__ import annotations

import random
from datetime import datetime
from zoneinfo import ZoneInfo

from app.behavior.activity import ActivityManager
from app.behavior.presence import PresenceResolver
from app.character.state import (
    MOOD_LEVELS,
    CharacterState,
    StateManager,
    step_mood,
    step_toward_neutral,
)
from app.config.settings import BehaviorActivityConfig, BehaviorScheduleConfig

TZ = ZoneInfo("Asia/Singapore")


def makeup_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'act.db'}"))


def at(hour: int, minute: int = 0) -> datetime:
    return datetime(2026, 9, 29, hour, minute, tzinfo=TZ)


class TestPresence:
    def test_periods(self) -> None:
        presence = PresenceResolver("Asia/Singapore")
        assert presence.time_context(at(3)).period == "late_night"
        assert presence.time_context(at(7)).period == "early_morning"
        assert presence.time_context(at(10)).period == "morning"
        assert presence.time_context(at(15)).period == "afternoon"
        assert presence.time_context(at(20)).period == "evening"
        assert presence.time_context(at(23, 30)).period == "night"

    def test_natural_language_description(self) -> None:
        presence = PresenceResolver("Asia/Singapore")
        text = presence.time_context(at(20, 7)).describe()
        assert "晚上" in text and "星期二" in text
        assert "T20" not in text and "2026-09-29T" not in text  # never raw ISO

    def test_sleep_window_wraps_midnight(self) -> None:
        schedule = BehaviorScheduleConfig(
            sleep_enabled=True, sleep_start="00:30", sleep_end="08:00"
        )
        presence = PresenceResolver("Asia/Singapore", schedule)
        assert presence.is_sleeping(at(3))
        assert presence.is_sleeping(at(7, 59))
        assert not presence.is_sleeping(at(8, 0))
        assert not presence.is_sleeping(at(23))

    def test_sleep_can_be_disabled(self) -> None:
        presence = PresenceResolver(
            "Asia/Singapore", BehaviorScheduleConfig(sleep_enabled=False)
        )
        assert not presence.is_sleeping(at(4))

    def test_dnd_window(self) -> None:
        schedule = BehaviorScheduleConfig(dnd_enabled=True, dnd_start="23:00", dnd_end="08:00")
        presence = PresenceResolver("Asia/Singapore", schedule)
        assert presence.in_dnd(at(23, 30))
        assert presence.in_dnd(at(2))
        assert not presence.in_dnd(at(12))

    def test_hard_blocks(self) -> None:
        """Clock-independent: both cases pin their own moment in time."""
        schedule = BehaviorScheduleConfig(sleep_enabled=True, dnd_enabled=True)

        class _At(PresenceResolver):
            def __init__(self, moment):  # type: ignore[no-untyped-def]
                super().__init__("Asia/Singapore", schedule)
                self._moment = moment

            def now(self):  # type: ignore[override]
                return self._moment

        assert _At(at(3)).hard_block_reason(for_initiative=True) == "sleeping"
        # 15:00 is awake and outside a 23:00-08:00 DND window
        assert _At(at(15)).hard_block_reason(for_initiative=True) is None

    def test_unknown_timezone_falls_back(self) -> None:
        presence = PresenceResolver("Mars/Olympus")
        assert presence.time_context().local_time  # still usable


class TestActivityManager:
    async def test_pick_uses_configured_pool(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()
        states = StateManager(database)
        config = BehaviorActivityConfig(pool=["gaming", "reading"])
        manager = ActivityManager(
            config, PresenceResolver("Asia/Singapore"), states, rng=random.Random(1)
        )
        assert manager.pick("evening") in {"gaming", "reading"}
        await database.close()

    async def test_period_preference_narrows_pool(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()
        states = StateManager(database)
        config = BehaviorActivityConfig(
            pool=["gaming", "reading", "studying"],
            period_preferences={"night": ["gaming"]},
        )
        manager = ActivityManager(
            config, PresenceResolver("Asia/Singapore"), states, rng=random.Random(2)
        )
        assert manager.pick("night") == "gaming"
        await database.close()

    async def test_roll_sets_activity_and_respects_interval(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()
        clock = {"now": 1000.0}
        states = StateManager(database, clock=lambda: clock["now"])
        manager = ActivityManager(
            BehaviorActivityConfig(roll_interval_minutes=30),
            PresenceResolver("Asia/Singapore", BehaviorScheduleConfig(sleep_enabled=False)),
            states,
            rng=random.Random(3),
            clock=lambda: clock["now"],
        )
        first = await manager.roll()
        assert first.activity
        clock["now"] += 60  # too soon
        second = await manager.roll()
        assert second.activity == first.activity
        clock["now"] += 30 * 60
        third = await manager.roll()
        assert third.activity  # re-rolled (may coincidentally match)
        await database.close()

    async def test_sleeping_forces_resting_activity(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()

        class _Night(PresenceResolver):
            def now(self):  # type: ignore[override]
                return at(4)

        states = StateManager(database)
        manager = ActivityManager(
            BehaviorActivityConfig(pool=["gaming"]), _Night("Asia/Singapore"), states
        )
        state = await manager.roll(force=True)
        assert state.activity == "sleeping"
        await database.close()


class TestMoodInertia:
    def test_ladder_steps(self) -> None:
        assert step_mood("neutral", +1) == "happy"
        assert step_mood("happy", +1) == "cheerful"
        assert step_mood("cheerful", +1) == "cheerful"  # capped
        assert step_mood("down", -1) == "down"

    def test_custom_mood_preserved(self) -> None:
        assert step_mood("sleepy", +1) == "sleepy"

    def test_drift_toward_neutral(self) -> None:
        assert step_toward_neutral("cheerful") == "happy"
        assert step_toward_neutral("down") == "quiet"
        assert step_toward_neutral("neutral") == "neutral"

    def test_decay_moves_one_step_only(self) -> None:
        state = CharacterState(mood="cheerful", mood_updated_at=1000)
        later = state.decayed(1000 + 5 * 3600)
        assert later.mood == "happy"  # not straight to neutral

    def test_fresh_mood_does_not_decay(self) -> None:
        state = CharacterState(mood="cheerful", mood_updated_at=1000)
        assert state.decayed(1000 + 60).mood == "cheerful"

    async def test_nudge_respects_cooldown(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()
        clock = {"now": 10_000.0}
        manager = StateManager(database, clock=lambda: clock["now"])
        await manager.load()

        await manager.nudge_mood(+1, source="test")
        assert manager.state.mood == "happy"
        await manager.nudge_mood(-1, source="test")  # too soon → ignored
        assert manager.state.mood == "happy"

        clock["now"] += 700  # past the cooldown
        await manager.nudge_mood(-1, source="test")
        assert manager.state.mood == "neutral"
        await database.close()

    async def test_cannot_jump_across_the_ladder(self, tmp_path) -> None:
        database = makeup_db(tmp_path)
        await database.connect()
        clock = {"now": 5000.0}
        manager = StateManager(database, clock=lambda: clock["now"])
        await manager.update(mood="cheerful")
        for _ in range(6):
            clock["now"] += 700
            await manager.nudge_mood(-1, source="test")
            assert manager.state.mood in MOOD_LEVELS
            if manager.state.mood == "down":
                break
        assert manager.state.mood == "down"  # walked down one step at a time
        await database.close()
