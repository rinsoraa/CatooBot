"""Routine tests (v0.8 §17/§21/§22): WebUI-configured, never hardcoded.

Spec coverage: the routine comes from configuration (with a documented
default), user-written activity labels still work, and picking prefers a real
change of scene.
"""

from __future__ import annotations

from app.config.settings import WorldConfig
from app.world.routine import DEFAULT_ROUTINE, RoutineService, activity_for_label
from tests.world_helpers import make_clock


class TestDefaults:
    def test_default_routine_covers_every_period(self) -> None:
        service = RoutineService(WorldConfig().routine)
        for period in (
            "late_night",
            "early_morning",
            "morning",
            "noon",
            "afternoon",
            "evening",
            "night",
        ):
            assert service.labels_for(period), period

    def test_defaults_used_when_config_empty(self) -> None:
        service = RoutineService(WorldConfig().routine)
        assert service.periods() == dict(DEFAULT_ROUTINE)

    def test_configured_periods_win(self) -> None:
        config = WorldConfig(routine={"periods": {"morning": ["听歌", "发呆"]}})
        service = RoutineService(config.routine)
        assert service.labels_for("morning") == ["听歌", "发呆"]
        # unconfigured periods fall back to the built-in map entirely
        assert service.labels_for("evening") == []

    def test_reconfigure_applies_live(self) -> None:
        service = RoutineService(WorldConfig().routine)
        service.reconfigure(WorldConfig(routine={"periods": {"noon": ["吃午饭"]}}).routine)
        assert service.labels_for("noon") == ["吃午饭"]

    def test_transition_minutes_exposed(self) -> None:
        service = RoutineService(WorldConfig(routine={"transition_minutes": 45}).routine)
        assert service.transition_minutes == 45


class TestCatalogue:
    def test_known_label_carries_context(self) -> None:
        activity = activity_for_label("打游戏")
        assert activity.location == "房间"
        assert "game" in activity.tags

    def test_custom_label_still_usable(self) -> None:
        activity = activity_for_label("去楼下喂猫")
        assert activity.label == "去楼下喂猫"
        assert activity.tags == ["custom"]
        assert activity.weight > 0

    def test_activities_for_period(self) -> None:
        config = WorldConfig(routine={"periods": {"evening": ["打游戏", "看电影"]}})
        service = RoutineService(config.routine)
        labels = [item.label for item in service.activities_for("evening")]
        assert labels == ["打游戏", "看电影"]

    def test_default_activity_is_deterministic(self) -> None:
        service = RoutineService(WorldConfig().routine)
        assert service.default_activity("noon").label == service.default_activity("noon").label


class TestPicking:
    def test_pick_avoids_current_activity_when_possible(self) -> None:
        clock, _fake = make_clock()
        config = WorldConfig(routine={"periods": {"evening": ["打游戏", "看电影", "听歌"]}})
        service = RoutineService(config.routine, clock=clock)
        for _ in range(20):
            picked = service.pick("evening", avoid="打游戏")
            assert picked is not None and picked.label != "打游戏"

    def test_single_activity_period_repeats(self) -> None:
        clock, _fake = make_clock()
        config = WorldConfig(routine={"periods": {"late_night": ["睡觉"]}})
        service = RoutineService(config.routine, clock=clock)
        assert service.pick("late_night", avoid="睡觉").label == "睡觉"

    def test_pick_returns_none_for_empty_period(self) -> None:
        config = WorldConfig(routine={"periods": {"noon": ["吃午饭"]}})
        service = RoutineService(config.routine)
        assert service.pick("afternoon", avoid="") is None

    def test_as_dict_for_webui(self) -> None:
        service = RoutineService(WorldConfig().routine)
        payload = service.as_dict()
        assert payload["periods"] and payload["catalogue"]
        assert "sleeping" in payload["catalogue"] or "睡觉" in payload["catalogue"]
