"""Long-run tests (v0.8 §58/§60/§70): 1 / 3 / 7 days without explosion.

Spec coverage: letting the world run for days must not blow up events, goals,
messages or memory. These are the acceptance tests for "background life stays
believable and bounded".
"""

from __future__ import annotations

from app.config.settings import WorldConfig
from tests.world_helpers import events_per_day, make_world, table_count


async def run_days(world, fake, *, days: int, step_minutes: int = 15) -> None:
    """Advance the character clock and tick the world like the scheduler would."""
    steps = int(days * 24 * 60 / step_minutes)
    for _ in range(steps):
        fake.advance(step_minutes * 60)
        await world.tick()


class TestOneDay:
    async def test_state_stays_sane(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.goals.create("把房子盖完", milestones=["打地基", "搭墙", "装内饰"])
        await run_days(world, fake, days=1)

        state = world.state.state
        assert 0.05 <= state.energy <= 1.0
        assert state.activity
        assert state.schedule_state in ("awake", "resting", "sleeping", "busy")
        assert state.mood in ("down", "quiet", "neutral", "happy", "cheerful")
        await database.close()

    async def test_events_respect_daily_limits(self, tmp_path) -> None:
        config = WorldConfig(events={"max_per_hour": 6, "max_per_day": 20})
        world, database, fake = await make_world(tmp_path, config=config)
        await run_days(world, fake, days=1)
        per_day = await events_per_day(world, database)
        # v1.0: the daily cap is enforced per character-day (a 24h span crosses
        # midnight), never unbounded.
        assert per_day and max(per_day.values()) <= 20
        await database.close()

    async def test_ambient_stays_low_frequency(self, tmp_path) -> None:
        config = WorldConfig(ambient={"min_interval_minutes": 120, "max_per_day": 3})
        world, database, fake = await make_world(tmp_path, config=config)
        await run_days(world, fake, days=1)
        per_day = await events_per_day(world, database, "ambient")
        assert per_day and max(per_day.values()) <= 3
        await database.close()

    async def test_activity_changes_are_few(self, tmp_path) -> None:
        """A life with dozens of scene changes a day is not a life (§22/§102).

        v1.0: activities are episodes with durations, so transitions are bounded
        per character-day (a 24h span crosses midnight → two days).
        """
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=1)
        per_day = await events_per_day(world, database, "activity")
        assert per_day and max(per_day.values()) <= 20
        # each day has >0 transitions but well under the "alert" 4~6/hour
        assert max(per_day.values()) < 4 * 16
        await database.close()

    async def test_no_message_is_ever_sent_by_the_world(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=1)
        assert await table_count(database, "behavior_events") == 0
        await database.close()

    async def test_memory_is_untouched(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=1)
        for table in ("memories", "topics"):
            assert await table_count(database, table) == 0
        await database.close()


class TestThreeDays:
    async def test_goal_progress_is_bounded(self, tmp_path) -> None:
        config = WorldConfig(
            goals={"auto_advance": True, "advance_interval_hours": 12,
                   "max_progress_events_per_day": 2}
        )
        world, database, fake = await make_world(tmp_path, config=config)
        goal = await world.goals.create("慢慢推进的目标")
        await world.goals.set_current(goal.goal_id)
        await run_days(world, fake, days=3)
        reloaded = await world.goals.get(goal.goal_id)
        assert reloaded.progress < 1.0             # three days is not a whole project
        assert reloaded.progress <= 0.08 * 2 * 3 + 1e-9  # at most the daily budget
        await database.close()

    async def test_goal_events_per_day_capped(self, tmp_path) -> None:
        config = WorldConfig(
            goals={"auto_advance": True, "advance_interval_hours": 1,
                   "max_progress_events_per_day": 2}
        )
        world, database, fake = await make_world(tmp_path, config=config)
        goal = await world.goals.create("高频尝试")
        await world.goals.set_current(goal.goal_id)
        await run_days(world, fake, days=3)
        per_day = await events_per_day(world, database, "goal")
        # the creation day carries one extra "create" event on top of the budget
        assert max(per_day.values()) <= 2 + 1
        await database.close()

    async def test_events_do_not_accumulate_without_bound(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=3)
        per_day = await events_per_day(world, database)
        assert max(per_day.values()) <= 40          # the daily ceiling holds
        assert sum(per_day.values()) < 3 * 24       # and never one per hour
        await database.close()

    async def test_snapshots_stay_bounded(self, tmp_path) -> None:
        config = WorldConfig(snapshot={"interval_minutes": 60, "keep": 5})
        world, database, fake = await make_world(tmp_path, config=config)
        await run_days(world, fake, days=3)
        assert 1 <= await table_count(database, "world_snapshots") <= 5
        await database.close()


class TestSevenDays:
    async def test_week_of_world_is_quiet_and_stable(self, tmp_path) -> None:
        config = WorldConfig(ambient={"min_interval_minutes": 90, "max_per_day": 4})
        world, database, fake = await make_world(tmp_path, config=config)
        await world.goals.create("一周的小目标", milestones=["第一步", "第二步"])
        await run_days(world, fake, days=7, step_minutes=30)

        events = await table_count(database, "world_events")
        assert events <= 40 * 7                # the global daily ceiling
        assert events < 7 * 24                 # far below one per hour
        state = world.state.state
        assert 0.05 <= state.energy <= 1.0
        assert await table_count(database, "behavior_events") == 0
        assert await table_count(database, "memories") == 0
        await database.close()

    async def test_seven_day_run_is_idempotent_per_window(self, tmp_path) -> None:
        """Re-ticking the same instant must not duplicate anything (§26)."""
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=1, step_minutes=60)
        before = await table_count(database, "world_events")
        for _ in range(5):
            await world.tick()  # same clock, repeated passes
        assert await table_count(database, "world_events") == before
        await database.close()

    async def test_no_unbounded_growth_in_state_json(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await run_days(world, fake, days=7, step_minutes=60)
        row = await database.fetchone("SELECT data FROM character_states WHERE id = 1")
        assert row is not None and len(row["data"]) < 2000
        await database.close()


class TestSimulation:
    async def test_simulate_writes_nothing(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.goals.create("预演目标")
        before = await table_count(database, "world_events")
        await world.simulate(days=3, step_minutes=30, seed=1)
        assert await table_count(database, "world_events") == before
        await database.close()

    async def test_simulate_is_deterministic_with_seed(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        first = await world.simulate(days=1, step_minutes=60, seed=42)
        second = await world.simulate(days=1, step_minutes=60, seed=42)
        assert [e["text"] for e in first["events"]] == [e["text"] for e in second["events"]]
        await database.close()

    async def test_simulate_output_is_ordered_and_bounded(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        result = await world.simulate(days=7, step_minutes=10, seed=3)
        times = [item["at"] for item in result["events"]]
        assert times == sorted(times)
        assert len(result["events"]) <= 500
        assert result["counts"].get("ambient", 0) <= 4 * 7
        await database.close()

    async def test_simulate_marks_reality_boundary(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        result = await world.simulate(days=1, step_minutes=60)
        assert any("不会" in note for note in result["notes"])
        await database.close()
