"""Timeline tests (v0.8 §27/§28/§52): order, views, replay, prompt lines.

Spec coverage: the world timeline is separate from Memory/Topic/Goal, keeps a
readable order, and replay is strictly read-only.
"""

from __future__ import annotations

from tests.world_helpers import make_world, table_count


class TestViews:
    async def test_day_view_returns_todays_events(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="在房间打游戏", key="a")
        rows = await world.timeline.day()
        assert [row["summary"] for row in rows] == ["在房间打游戏"]
        assert rows[0]["type_label"] == "活动"
        await database.close()

    async def test_other_day_is_empty(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="今天做的事", key="a")
        assert await world.timeline.day("2026-06-01") == []
        await database.close()

    async def test_recent_is_newest_first(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="先发生的", key="1")
        fake.advance(600)
        await world.events.emit(type="activity", summary="后发生的", key="2")
        rows = await world.timeline.recent(limit=5)
        assert [row["summary"] for row in rows] == ["后发生的", "先发生的"]
        await database.close()

    async def test_range_buckets_by_day(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="今天", key="today")
        buckets = await world.timeline.range(days=2)
        keys = list(buckets)
        assert len(keys) == 2 and keys[-1] == "2026-06-15"
        assert buckets["2026-06-15"][0]["summary"] == "今天"
        await database.close()


class TestPromptLines:
    async def test_lines_carry_time_and_summary(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="goal", summary="把房子推进了一点", key="g", importance=0.5)
        lines = await world.timeline.prompt_lines(limit=3)
        assert lines and "把房子推进了一点" in lines[0]
        await database.close()

    async def test_old_events_fall_out_of_the_window(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.events.emit(type="goal", summary="很久以前的事", key="old", importance=0.6)
        fake.advance(4 * 86400)
        assert await world.timeline.prompt_lines(limit=3, hours=48) == []
        await database.close()

    async def test_low_importance_colour_is_not_prompted(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="ambient", summary="打了个哈欠", key="a", importance=0.1)
        assert await world.timeline.prompt_lines(limit=3) == []
        await database.close()


class TestReplay:
    async def test_replay_lists_events_in_order(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="开始打游戏", key="1")
        fake.advance(3600)
        await world.events.emit(type="activity", summary="开始看剧", key="2")
        story = await world.timeline.replay(days=1)
        assert [item["summary"] for item in story] == ["开始打游戏", "开始看剧"]
        assert story[0]["changed"] is False and story[1]["changed"] is True
        await database.close()

    async def test_replay_writes_nothing(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="唯一的事件", key="1")
        before = await table_count(database, "world_events")
        await world.timeline.replay(days=7)
        assert await table_count(database, "world_events") == before
        await database.close()

    async def test_replay_window_excludes_older(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="上周的事", key="old")
        fake.advance(8 * 86400)
        await world.events.emit(type="activity", summary="今天的事", key="new")
        story = await world.timeline.replay(days=1)
        assert [item["summary"] for item in story] == ["今天的事"]
        await database.close()


class TestSummary:
    async def test_summary_counts_by_type(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.events.emit(type="activity", summary="一", key="1")
        await world.events.emit(type="ambient", summary="二", key="2")
        summary = await world.timeline.summary(days=1)
        assert summary["total"] == 2
        assert summary["by_type"]["activity"] == 1
        await database.close()

    async def test_timeline_is_not_memory(self, tmp_path) -> None:
        """Timeline code never imports the memory pipeline (§27/§28)."""
        import inspect

        from app.world import timeline

        source = inspect.getsource(timeline)
        assert "app.memory" not in source
        assert "MemoryManager" not in source
