"""Snapshot + recovery tests (v0.8 §31-§34): latest snapshot, settle forward.

Spec coverage: snapshots are bounded and rotated, recovery restores from the
newest snapshot, downtime is never replayed as a burst of events, and the
missed-event policy is configurable (skip by default, one consolidated
catch-up event when asked for).
"""

from __future__ import annotations

from app.config.settings import WorldConfig
from tests.world_helpers import make_world, table_count, world_events


class TestSnapshots:
    async def test_snapshot_stores_state_and_goal(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.state.change("manual", activity="打游戏", location="房间")
        goal = await world.goals.create("把房子盖完")
        await world.goals.set_current(goal.goal_id)
        await world.save_snapshot()
        row = await database.fetchone("SELECT data FROM world_snapshots ORDER BY id DESC LIMIT 1")
        assert row is not None
        assert '"打游戏"' in row["data"]
        assert "把房子盖完" in row["data"]
        await database.close()

    async def test_snapshot_rotation_keeps_the_limit(self, tmp_path) -> None:
        config = WorldConfig(snapshot={"interval_minutes": 1, "keep": 2})
        world, database, fake = await make_world(tmp_path, config=config)
        for _ in range(4):
            fake.advance(120)
            await world.save_snapshot()
        assert await table_count(database, "world_snapshots") == 2
        await database.close()

    async def test_tick_takes_snapshots_on_interval(self, tmp_path) -> None:
        config = WorldConfig(snapshot={"interval_minutes": 30}, state_persist_seconds=5)
        world, database, fake = await make_world(tmp_path, config=config)
        await world.tick()
        assert await table_count(database, "world_snapshots") == 1
        await world.tick()  # nothing due yet
        assert await table_count(database, "world_snapshots") == 1
        fake.advance(31 * 60)
        await world.tick()
        assert await table_count(database, "world_snapshots") == 2
        await database.close()


class TestRecovery:
    async def test_first_boot_is_not_downtime(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        report = await world.restore()
        assert report["restored"] is False
        assert report["downtime_seconds"] == 0.0
        await database.close()

    async def test_restores_state_from_snapshot(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.state.change(
            "manual", activity="看电影", location="客厅", schedule_state="resting"
        )
        await world.save_snapshot()

        # "Restart": fresh state, same database.
        await world.state.restore({})
        assert world.state.state.activity == ""
        fake.advance(20 * 60)
        report = await world.restore()
        assert report["restored"] is True
        assert world.state.state.activity == "看电影"
        assert world.state.state.location == "客厅"
        assert report["downtime_seconds"] >= 1000
        await database.close()

    async def test_downtime_is_skipped_by_default(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.save_snapshot()
        before = await table_count(database, "world_events")
        fake.advance(6 * 3600)
        report = await world.restore()
        assert report["policy"] == "skip"
        assert await table_count(database, "world_events") == before
        await database.close()

    async def test_catch_up_policy_emits_one_event(self, tmp_path) -> None:
        config = WorldConfig(missed_event_policy="catch_up")
        world, database, fake = await make_world(tmp_path, config=config)
        await world.save_snapshot()
        before = len(await world_events(database))
        fake.advance(6 * 3600)
        report = await world.restore()
        events = await world_events(database)
        assert report["policy"] == "catch_up"
        assert len(events) == before + 1
        assert "小时" in events[-1]["summary"]
        await database.close()

    async def test_short_downtime_is_silent(self, tmp_path) -> None:
        config = WorldConfig(missed_event_policy="catch_up")
        world, database, fake = await make_world(tmp_path, config=config)
        await world.save_snapshot()
        before = len(await world_events(database))
        fake.advance(120)
        await world.restore()
        assert len(await world_events(database)) == before
        await database.close()

    async def test_corrupt_snapshot_does_not_block_boot(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await database.execute(
            "INSERT INTO world_snapshots (snapshot_at, data) VALUES (?, ?)",
            (1, "{not json"),
        )
        report = await world.restore()
        assert report["restored"] is False  # tolerated, world continues
        await database.close()

    async def test_invalid_state_payload_keeps_current_state(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.state.change("manual", activity="打游戏")
        await database.execute(
            "INSERT INTO world_snapshots (snapshot_at, data) VALUES (?, ?)",
            (999, '{"snapshot_at": 999, "state": {"energy": "high"}}'),
        )
        await world.restore()
        assert world.state.state.activity == "打游戏"
        await database.close()

    async def test_heartbeat_is_written(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.restore()
        row = await database.fetchone("SELECT value FROM bot_state WHERE key = 'world.last_seen'")
        assert row is not None and int(row["value"]) > 0
        await database.close()
