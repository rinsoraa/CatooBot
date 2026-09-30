"""Persistent goal tests (v0.8 §29-§31): slow, bounded, visible progress.

Spec coverage: goals live across restarts, they are capped in count, progress
advances in small steps with a daily event budget, milestones mark real change,
and background advancement is opt-in and interval-based.
"""

from __future__ import annotations

import pytest

from app.world.errors import GoalError, GoalLimitError


class TestCreating:
    async def test_create_goal_with_milestones(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create(
            "把房子盖完",
            description="在 Minecraft 里盖一栋自己的房子",
            milestones=["打地基", "搭墙", "装内饰"],
            next_action="打地基",
        )
        assert goal.goal_id and goal.progress_percent == 0
        assert "搭墙" in goal.summary() or "打地基" in goal.summary()
        stored = await world.goals.get(goal.goal_id)
        assert [m.name for m in stored.milestones] == ["打地基", "搭墙", "装内饰"]
        await database.close()

    async def test_goal_creation_emits_event(self, tmp_path) -> None:
        from tests.world_helpers import make_world, world_events

        world, database, _fake = await make_world(tmp_path)
        await world.goals.create("学一首新歌")
        events = await world_events(database, "goal")
        assert events and "目标" in events[0]["summary"]
        await database.close()

    async def test_active_goal_limit(self, tmp_path) -> None:
        from app.config.settings import WorldConfig
        from tests.world_helpers import make_world

        config = WorldConfig(goals={"max_active": 2})
        world, database, _fake = await make_world(tmp_path, config=config)
        await world.goals.create("第一件事")
        await world.goals.create("第二件事")
        with pytest.raises(GoalLimitError):
            await world.goals.create("第三件事")
        await database.close()

    async def test_empty_name_rejected(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        with pytest.raises(GoalError):
            await world.goals.create("   ")
        await database.close()

    async def test_disabled_goals_refuse_creates(self, tmp_path) -> None:
        from app.config.settings import WorldConfig
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(
            tmp_path, config=WorldConfig(goals={"enabled": False})
        )
        with pytest.raises(GoalError):
            await world.goals.create("不该创建")
        await database.close()


class TestAdvancing:
    async def test_advance_moves_progress_and_milestone(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("把房子盖完", milestones=["打地基", "搭墙"])
        goal = await world.goals.advance(goal.goal_id, milestone="打地基")
        assert 0 < goal.progress < 1
        assert goal.milestones[0].done is True
        assert goal.next_open_milestone().name == "搭墙"
        await database.close()

    async def test_step_size_is_bounded(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("慢慢来")
        goal = await world.goals.advance(goal.goal_id, amount=0.9)
        assert goal.progress <= 0.4  # one manual step can never finish a goal
        await database.close()

    async def test_daily_progress_event_cap(self, tmp_path) -> None:
        from app.config.settings import WorldConfig
        from tests.world_helpers import make_world, world_events

        config = WorldConfig(goals={"max_progress_events_per_day": 1})
        world, database, _fake = await make_world(tmp_path, config=config)
        goal = await world.goals.create("一天只推进一步")
        first = await world.goals.advance(goal.goal_id)
        second = await world.goals.advance(goal.goal_id)
        assert second.progress == first.progress       # capped for today
        assert len(await world_events(database, "goal")) == 2  # create + one progress
        await database.close()

    async def test_completion_marks_done_and_milestone_event(self, tmp_path) -> None:
        from tests.world_helpers import make_world, world_events

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("做完了")
        await world.goals.advance(goal.goal_id, amount=0.4)
        await world.goals.advance(goal.goal_id, amount=0.4)
        done = await world.goals.advance(goal.goal_id, amount=0.4)
        assert done.status == "done" and done.progress_percent == 100
        assert done.completed_at
        assert await world_events(database, "milestone")
        await database.close()

    async def test_unknown_or_inactive_goal_rejected(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        with pytest.raises(GoalError):
            await world.goals.advance("goal_missing")
        goal = await world.goals.create("会被暂停")
        await world.goals.set_status(goal.goal_id, "paused")
        with pytest.raises(GoalError):
            await world.goals.advance(goal.goal_id)
        await database.close()


class TestLifecycle:
    async def test_status_changes_and_current_goal_cleared(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("一个目标")
        await world.goals.set_current(goal.goal_id)
        assert world.state.state.current_goal == goal.goal_id
        await world.goals.set_status(goal.goal_id, "done")
        assert world.state.state.current_goal == ""
        await database.close()

    async def test_current_goal_falls_back_to_priority(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        await world.goals.create("次要的", priority=4)
        important = await world.goals.create("重要的", priority=1)
        current = await world.goals.current_goal()
        assert current.goal_id == important.goal_id
        await database.close()

    async def test_add_milestone_is_idempotent(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("加里程碑")
        await world.goals.add_milestone(goal.goal_id, "第一步")
        goal = await world.goals.add_milestone(goal.goal_id, "第一步")
        assert [m.name for m in goal.milestones] == ["第一步"]
        await database.close()

    async def test_prompt_line_mentions_progress_and_next_step(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        goal = await world.goals.create("把房子盖完", milestones=["打地基"])
        await world.goals.set_current(goal.goal_id)
        line = await world.goals.prompt_line()
        assert "把房子盖完" in line and "打地基" in line
        await database.close()

    async def test_goals_survive_restart(self, tmp_path) -> None:
        from app.character.state import StateManager
        from app.config.settings import WorldConfig
        from app.world.runtime import build_world
        from tests.world_helpers import make_clock, make_world

        world, database, _fake = await make_world(tmp_path, name="goals_restart.db")
        goal = await world.goals.create("活过重启")
        await world.goals.advance(goal.goal_id)
        clock, fake = make_clock()
        rebuilt = build_world(
            WorldConfig(),
            database=database,
            state_manager=StateManager(database, clock=fake),
            timezone="Asia/Singapore",
            clock=clock,
        )
        reloaded = await rebuilt.goals.get(goal.goal_id)
        assert reloaded is not None and reloaded.progress > 0
        await database.close()


class TestBackgroundAdvance:
    async def test_disabled_by_default(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, fake = await make_world(tmp_path)
        goal = await world.goals.create("不自动推进")
        fake.advance(48 * 3600)
        assert await world.goals.auto_advance_due() is None
        assert (await world.goals.get(goal.goal_id)).progress == 0
        await database.close()

    async def test_advances_after_interval_when_enabled(self, tmp_path) -> None:
        from app.config.settings import WorldConfig
        from tests.world_helpers import make_world

        config = WorldConfig(goals={"auto_advance": True, "advance_interval_hours": 6})
        world, database, fake = await make_world(tmp_path, config=config)
        await world.goals.create("慢速推进")
        fake.advance(7 * 3600)
        advanced = await world.goals.auto_advance_due()
        assert advanced is not None and advanced.progress > 0
        await database.close()

    async def test_not_advanced_twice_within_interval(self, tmp_path) -> None:
        from app.config.settings import WorldConfig
        from tests.world_helpers import make_world

        config = WorldConfig(goals={"auto_advance": True, "advance_interval_hours": 12})
        world, database, fake = await make_world(tmp_path, config=config)
        goal = await world.goals.create("间隔保护")
        fake.advance(13 * 3600)
        await world.goals.auto_advance_due()
        fake.advance(3600)
        assert await world.goals.auto_advance_due() is None
        assert (await world.goals.get(goal.goal_id)).progress < 0.2
        await database.close()


class TestProjects:
    async def test_create_and_list_project(self, tmp_path) -> None:
        from tests.world_helpers import make_world

        world, database, _fake = await make_world(tmp_path)
        project = await world.goals.create_project("我的 Minecraft 世界")
        projects = await world.goals.projects()
        assert [p.project_id for p in projects] == [project.project_id]
        view = await world.goals.view()
        assert view["projects"] and view["enabled"] is True
        await database.close()
