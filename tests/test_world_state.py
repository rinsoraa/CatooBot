"""World state tests (v0.8 §18-§22): reasons, atomicity, smooth transitions.

Spec coverage: every state change carries a reason, the character state is
*extended* (never duplicated), activity transitions are smooth, and the new
fields survive a restart.
"""

from __future__ import annotations

import pytest

from app.character.state import CharacterState, StateManager
from app.config.settings import DatabaseConfig, WorldConfig
from app.database.database import Database
from app.world.routine import activity_for_label
from app.world.state import WorldStateService
from tests.world_helpers import make_clock


async def make_state(tmp_path, *, transition_minutes: int = 20, name: str = "state.db"):
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    clock, fake = make_clock()
    config = WorldConfig(routine={"transition_minutes": transition_minutes})
    from app.world.routine import RoutineService

    routine = RoutineService(config.routine, clock=clock)
    service = WorldStateService(
        state_manager=StateManager(database, clock=fake), clock=clock, routine=routine
    )
    await service.load()
    return service, database, fake


class TestReasonEnforcement:
    async def test_change_requires_a_reason(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        with pytest.raises(ValueError):
            await service.change("", activity="打游戏")
        await database.close()

    async def test_unknown_reason_is_logged_not_fatal(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        state = await service.change("something_odd", activity="打游戏", force=True)
        assert state.activity == "打游戏"
        await database.close()

    async def test_reason_is_recorded_on_state(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        state = await service.change("user_interaction", social_state="chatting")
        assert state.last_change_reason == "user_interaction"
        assert state.social_state == "chatting"
        await database.close()


class TestActivityTransitions:
    async def test_first_activity_applies(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        state = await service.set_activity(activity_for_label("打游戏"))
        assert state.activity == "打游戏"
        assert state.location == "房间"
        assert state.last_activity_change > 0
        await database.close()

    async def test_second_change_within_dwell_is_deferred(self, tmp_path) -> None:
        service, database, fake = await make_state(tmp_path, transition_minutes=20)
        await service.set_activity(activity_for_label("打游戏"))
        fake.advance(60)  # only a minute later
        state = await service.change("activity", activity="看剧", location="客厅")
        assert state.activity == "打游戏"          # dwell not elapsed
        assert state.location == "客厅"            # other fields still applied
        await database.close()

    async def test_change_applies_after_dwell(self, tmp_path) -> None:
        service, database, fake = await make_state(tmp_path, transition_minutes=20)
        await service.set_activity(activity_for_label("打游戏"))
        fake.advance(21 * 60)
        state = await service.set_activity(activity_for_label("看剧"))
        assert state.activity == "看剧"
        await database.close()

    async def test_forced_change_ignores_dwell(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path, transition_minutes=60)
        await service.set_activity(activity_for_label("打游戏"))
        state = await service.set_activity(activity_for_label("睡觉"), force=True)
        assert state.activity == "睡觉"
        await database.close()

    async def test_energy_moves_with_activity(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        before = service.state.energy
        state = await service.set_activity(activity_for_label("打游戏"))
        assert state.energy < before

    async def test_time_passage_sleep_restores_energy(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        await service.change("manual", energy=0.4, emit=False)
        await service.set_activity(activity_for_label("睡觉"))
        low = service.state.energy
        await service.apply_time_passage("night", schedule_state="sleeping")
        assert service.state.energy > low
        assert service.state.schedule_state == "sleeping"
        await database.close()


class TestEmission:
    async def test_change_can_emit_an_event(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        captured: list[dict] = []

        async def sink(payload: dict) -> None:
            captured.append(payload)

        service.set_event_sink(sink)
        await service.set_activity(activity_for_label("打游戏"), summary="在房间打游戏")
        assert captured and captured[0]["type"] == "activity"
        assert "打游戏" in captured[0]["summary"]
        await database.close()

    async def test_mood_nudge_uses_ladder(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        state = await service.nudge_mood(1, source="test")
        assert state.mood == "happy"
        await database.close()


class TestPersistence:
    async def test_world_fields_survive_reload(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path, name="persist.db")
        await service.change(
            "manual",
            activity="打游戏",
            location="房间",
            social_state="with_friends",
            schedule_state="awake",
            current_goal="goal_1",
            current_project="proj_1",
        )
        reloaded = StateManager(database)
        state = await reloaded.load()
        assert state.activity == "打游戏"
        assert state.location == "房间"
        assert state.social_state == "with_friends"
        assert state.current_goal == "goal_1"
        assert state.current_project == "proj_1"
        await database.close()

    async def test_legacy_state_json_still_loads(self) -> None:
        legacy = (
            '{"mood": "happy", "energy": 0.5, "activity": "看剧", '
            '"activity_since": 1, "current_focus": "", "updated_at": 1}'
        )
        state = CharacterState.from_json(legacy)
        assert state.activity == "看剧"
        assert state.social_state == "alone"
        assert state.schedule_state == "awake"

    async def test_restore_from_snapshot(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        payload = CharacterState(activity="看剧", location="客厅").model_dump()
        await service.restore(payload)
        assert service.state.activity == "看剧"
        assert service.state.location == "客厅"
        await database.close()

    async def test_restore_survives_garbage(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        await service.restore({"activity": 123, "unknown": object()})
        assert service.state.activity == ""  # unchanged, no crash
        await database.close()


class TestDescribe:
    async def test_describe_mentions_place_and_goal(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        await service.change(
            "manual", activity="打游戏", location="房间", current_focus="把房子盖完"
        )
        text = service.describe()
        assert "房间" in text and "打游戏" in text and "把房子盖完" in text
        await database.close()

    async def test_view_exposes_world_fields(self, tmp_path) -> None:
        service, database, _fake = await make_state(tmp_path)
        await service.change("manual", activity="发呆", location="沙发", schedule_state="resting")
        view = service.view()
        for key in (
            "activity",
            "location",
            "social_state",
            "schedule_state",
            "current_goal",
            "current_project",
            "last_change_reason",
        ):
            assert key in view
        await database.close()


class TestCharacterStateExtension:
    def test_world_fields_have_defaults(self) -> None:
        state = CharacterState()
        assert state.location == ""
        assert state.social_state == "alone"
        assert state.schedule_state == "awake"
        assert state.current_goal == ""
        assert state.last_activity_change == 0

    def test_single_state_object_not_duplicated(self) -> None:
        """The world must extend CharacterState, not shadow it (§8)."""
        from app.character.state import CharacterState as CoreState
        from app.world import state as world_state_module

        assert world_state_module.CharacterState is CoreState
        locally_defined = [
            name
            for name, obj in vars(world_state_module).items()
            if isinstance(obj, type) and getattr(obj, "__module__", "") == "app.world.state"
        ]
        assert [name for name in locally_defined if name.endswith("State")] == []
