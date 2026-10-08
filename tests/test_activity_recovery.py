"""Phase 6A §二十七/§二十八/§五十一/§五十二：重启恢复（矩阵 K + 持久化 + 降级）。

真实 SQLite（不是内存桩）：写 → 关库 → 重开 → 新的 runtime 接管同一个 Episode。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.activity import (
    ACTIVITY_RECOVERED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    SqliteActivityStore,
    TransitionReason,
)
from app.config.settings import DatabaseConfig
from app.database.database import Database


async def make_db(tmp_path: Any, name: str = "activity.db") -> Database:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    return database


class RecordingStates:
    def __init__(self) -> None:
        self.data: dict[str, object] = {}

    async def update(self, **changes: object) -> dict[str, object]:
        changes.pop("reason", None)
        self.data.update(changes)
        return dict(self.data)


def build(
    database: Database,
    *,
    clock: FakeClock | None = None,
    character_id: str = "罐头@deadbeef",
    states: Any = None,
    events: list[tuple[str, dict[str, Any]]] | None = None,
) -> ActivityRuntime:
    return ActivityRuntime(
        store=SqliteActivityStore(database),
        clock=clock or FakeClock(1_700_000_000.0),
        character_id=character_id,
        planner=ActivityPlanner(),
        publisher=ActivityEventPublisher(
            sink=(lambda name, payload: events.append((name, payload)))
            if events is not None
            else None
        ),
        projection=ActivityProjection(states) if states is not None else None,
    )


class TestPersistence:
    async def test_episode_survives_a_real_restart(self, tmp_path: Any) -> None:
        """K：Episode 落盘；重启后**同一个** episode_id 被接管，不会多出第二条 ACTIVE。"""
        database = await make_db(tmp_path)
        clock = FakeClock()
        first = build(database, clock=clock)
        episode = await first.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await database.close()

        reopened = await make_db(tmp_path)
        second = build(reopened, clock=clock)
        current = await second.current()
        assert current is not None and current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE
        live = [
            item
            for item in await second.recent(20)
            if item.status in {ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}
        ]
        assert len(live) == 1
        await reopened.close()

    async def test_recovery_keeps_the_episode_and_says_so(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        events: list[tuple[str, dict[str, Any]]] = []
        first = build(database, clock=clock, events=events)
        episode = await first.start(activity_name="reading", now=clock.now())
        assert episode is not None

        second = build(database, clock=clock, events=events)
        result = await second.recover()
        assert result["action"] == "resumed"
        assert result["episode_id"] == episode.episode_id
        assert ACTIVITY_RECOVERED in [name for name, _payload in events]
        # §五十五：数据库里也要能回答"重启后是不是同一个 Episode"（审计行，可重复）
        rows = await second.recent_transitions(episode.episode_id, limit=10)
        recovered = [row for row in rows if row["transition"] == "RECOVERED"]
        assert recovered and recovered[0]["reason"] == TransitionReason.RECOVERY.value
        current = await second.current()
        assert current is not None and current.episode_id == episode.episode_id
        await database.close()

    async def test_repeated_restarts_keep_auditing_each_takeover(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        first = build(database, clock=clock)
        episode = await first.start(activity_name="reading", now=clock.now())
        assert episode is not None
        for _ in range(3):  # 连续三次重启
            again = build(database, clock=clock)
            await again.recover()
        rows = await first.recent_transitions(episode.episode_id, limit=10)
        assert len([row for row in rows if row["transition"] == "RECOVERED"]) == 3
        await database.close()

    async def test_case_a_within_the_window_stays_active(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        clock.advance_minutes(10)
        result = await runtime.recover()
        assert result["action"] == "resumed"
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.ACTIVE
        await database.close()

    async def test_case_b_overdue_asks_the_planner(self, tmp_path: Any) -> None:
        """§二十七 情况 B：计划结束时间已过、还没到硬上限 → 延长或换下一个。"""
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        clock.advance_minutes(70)  # reading: typical 60min, max 150min
        result = await runtime.recover()
        assert result["action"] == "extend"
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.EXTENDED
        await database.close()

    async def test_case_c_beyond_max_must_end(self, tmp_path: Any) -> None:
        """§二十七 情况 C：超过 max_duration 必须终结（绝不无限延长）。"""
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        clock.advance_hours(6)
        result = await runtime.recover()
        assert result["action"] == "expired"
        closed = [
            item for item in await runtime.recent(10) if item.episode_id == episode.episode_id
        ]
        assert closed and closed[0].status is ActivityStatus.EXPIRED
        current = await runtime.current()
        assert current is None or current.episode_id != episode.episode_id
        await database.close()

    async def test_fifty_two_no_episode_is_a_legal_state(self, tmp_path: Any) -> None:
        """§五十二：旧数据没有 Episode 就不许凭空造一个。"""
        database = await make_db(tmp_path)
        runtime = build(database)
        result = await runtime.recover()
        assert result == {"action": "none", "episode_id": "", "reason": "no_episode"}
        assert await runtime.current() is None
        assert await runtime.recent(10) == []
        await database.close()

    async def test_twenty_eight_offline_gap_is_never_faked(self, tmp_path: Any) -> None:
        """§二十八：掉线 8 小时之后，**不许**声称这 8 小时她在做某个活动。"""
        database = await make_db(tmp_path)
        clock = FakeClock()
        events: list[tuple[str, dict[str, Any]]] = []
        before = build(database, clock=clock, events=events)
        old = await before.start(activity_name="reading", now=clock.now())
        assert old is not None
        await database.close()

        # 离线 8 小时（世界时间照走），重启
        clock.advance_hours(8)
        reopened = await make_db(tmp_path)
        after = build(reopened, clock=clock, events=events)
        recovered = await after.recover()
        # 旧的那条只能被终结，绝不会"续着算"（它在离线期间没有任何真实证据）
        closed = [item for item in await after.recent(10) if item.episode_id == old.episode_id]
        assert closed and closed[0].status.terminal
        assert closed[0].ended_at == clock.now()
        assert recovered["action"] in {"expired", "extend"}

        # 她"现在"的活动只能是**现在**开始的：started_at = now，且没跨过离线窗口
        current = await after.current()
        if current is not None:
            assert current.started_at == clock.now()
            assert current.parent_episode_id in {"", old.episode_id}
        # 离线期间没有任何 Episode（没有一条的时间窗覆盖那 8 小时）
        for item in await after.recent(20):
            if item.episode_id == old.episode_id:
                continue
            assert item.started_at >= clock.now() - 1
        await reopened.close()


class TestRecoveryGrace:
    async def test_grace_keeps_a_just_expired_episode_alive(self, tmp_path: Any) -> None:
        """§五十三：刚过期几秒（在宽限内）不算过期 —— 免得每次重启都强行转移。"""
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        runtime.recovery_grace_seconds = 120.0
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        clock.advance_minutes(61)  # 刚过 typical(60min)，但在 120s 宽限内
        result = await runtime.recover()
        assert result["action"] == "resumed"
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.ACTIVE
        await database.close()

    async def test_without_grace_it_evaluates_immediately(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        runtime.recovery_grace_seconds = 0.0
        await runtime.start(activity_name="reading", now=clock.now())
        clock.advance_minutes(61)
        result = await runtime.recover()
        assert result["action"] == "extend"
        await database.close()


class TestDegradation:
    async def test_fifty_one_broken_db_only_degrades_activity(self, tmp_path: Any) -> None:
        """§五十一：Activity 数据库不可用 → 只降级，不抛异常、不影响别的层。"""
        database = await make_db(tmp_path)
        runtime = build(database)
        await database.close()  # 库没了
        assert await runtime.current() is None
        assert await runtime.recent(5) == []
        assert await runtime.advance() is None  # 不抛；如实降级
        assert runtime.degraded_reason
        result = await runtime.recover()
        assert result["action"] == "none"
        assert await runtime.context_block() == ""

    async def test_broken_store_does_not_raise_on_start(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        runtime = build(database)
        await database.close()
        assert await runtime.start(activity_name="reading") is None
        assert runtime.degraded_reason


class TestTransitionAudit:
    async def test_transitions_are_recorded_once_and_readable(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        runtime = build(database, clock=clock)
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await runtime.complete(reason=TransitionReason.MANUAL, now=clock.now())
        rows = await runtime.recent_transitions(episode.episode_id, limit=5)
        assert [row["transition"] for row in rows] == ["ACTIVE", "COMPLETED"]
        assert rows[-1]["reason"] == TransitionReason.MANUAL.value
        await database.close()

    async def test_context_block_reads_current_and_recent(self, tmp_path: Any) -> None:
        database = await make_db(tmp_path)
        clock = FakeClock()
        states = RecordingStates()
        runtime = build(database, clock=clock, states=states)
        assert await runtime.context_block() == ""  # 没有 Episode 就不编
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        block = await runtime.context_block()
        assert "看书" in block and "reading" in block
        assert block.count("\n") <= 2  # 1 条当前 + ≤1 条变化
        await database.close()


class TestTaskScopedPersistence:
    async def test_task_episode_keeps_its_task_link_across_restart(self, tmp_path: Any) -> None:
        """§四十三：重启后那条 Episode 仍然绑着同一个任务（不新建、不换 id）。"""
        from app.activity.adapters import TaskActivityAdapter

        database = await make_db(tmp_path)
        clock = FakeClock()
        first = build(database, clock=clock)
        adapter = TaskActivityAdapter(first)
        episode = await adapter.on_task_event("task.started", {"task_id": "task_abc"})
        assert episode is not None
        await database.close()

        reopened = await make_db(tmp_path)
        second = build(reopened, clock=clock)
        current = await second.current()
        assert current is not None
        assert current.episode_id == episode.episode_id
        assert current.related_task_id == "task_abc"
        assert current.source is ActivitySource.TASK
        assert current.activity_type is ActivityType.TASK_EXECUTION
        # 再来一次 started（恢复路径）也不会建第二个
        adapter2 = TaskActivityAdapter(second)
        again = await adapter2.on_task_event("task.started", {"task_id": "task_abc"})
        assert again is not None and again.episode_id == episode.episode_id
        await reopened.close()


@pytest.mark.parametrize("status", [ActivityStatus.COMPLETED, ActivityStatus.CANCELLED])
async def test_terminal_episodes_are_not_resumed(tmp_path: Any, status: ActivityStatus) -> None:
    """已经终结的 Episode 不会被恢复成 ACTIVE（状态机不允许）。"""
    database = await make_db(tmp_path)
    clock = FakeClock()
    runtime = build(database, clock=clock)
    episode = await runtime.start(activity_name="reading", now=clock.now())
    assert episode is not None
    if status is ActivityStatus.COMPLETED:
        await runtime.complete(now=clock.now())
    else:
        await runtime.cancel(now=clock.now())
    result = await runtime.recover()
    assert result["action"] == "none"
    closed = [item for item in await runtime.recent(10) if item.episode_id == episode.episode_id]
    assert closed and closed[0].status is status
    await database.close()
