"""Phase 6B §十九：相邻同活动合并（矩阵 H）。

* 优先 **EXTEND**（别把一件连续的事拆成两个 Episode）；
* 真的产生了两个相邻同活动时，**展示层**合并（原始 ``episode_ids`` 一个都不能丢）。
"""

from __future__ import annotations

from typing import Any

from app.activity import (
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    InMemoryActivityStore,
)
from app.activity.decision import is_mergeable, merge_adjacent

PROFILE = (5 * 60.0, 20 * 60.0, 600 * 60.0)


def episode(name: str, *, episode_id: str, started: float, ended: float, status: Any) -> Any:
    return ActivityEpisodeStub(name, episode_id, started, ended, status)


class ActivityEpisodeStub:
    """最小的只读形状（``merge_adjacent`` 只读这几个字段）。"""

    def __init__(
        self, name: str, episode_id: str, started: float, ended: float, status: Any
    ) -> None:
        self.activity_name = name
        self.episode_id = episode_id
        self.started_at = started
        self.ended_at = ended
        self.updated_at = ended
        self.created_at = started
        self.status = status
        self.source = ActivitySource.ROUTINE


class _States:
    async def update(self, **_changes: Any) -> dict[str, Any]:
        return {}


class TestMergePreference:
    async def test_h_same_activity_is_extended_instead_of_split(self) -> None:
        """§十九：她还在做同一件事 → EXTEND，不制造"结束 + 又开一个一样的"。"""

        class SamePlanner(ActivityPlanner):
            def next_after(self, *args: Any, **kwargs: Any) -> Any:
                from app.activity.planner import ActivityDecision as PlannerDecision
                from app.activity.planner import TransitionReason

                return PlannerDecision(
                    action="next",
                    reason=TransitionReason.TIME_EXPIRED,
                    activity_name="reading",  # 与当前同名
                )

        clock = FakeClock(1_700_000_000.0)
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=SamePlanner(),
            publisher=ActivityEventPublisher(),
            projection=ActivityProjection(_States()),
            transition_window_seconds=60.0,
            max_extensions_per_episode=2,
        )
        episode = await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        assert episode is not None
        clock.advance_minutes(21)
        after = await runtime.advance()
        assert after is not None
        assert after.episode_id == episode.episode_id  # 同一条 Episode
        assert after.status is ActivityStatus.EXTENDED
        assert len(await runtime.recent(10)) == 1  # 没有第二个 Episode
        # §十九 的"优先延长"就落在决策流水线的第 9 步（到期且可延长 → EXTEND），
        # trace 里能看到它：reason=TRANSITION_WINDOW、decision=EXTEND。
        trace = runtime.engine.traces[-1]
        assert trace.decision == "EXTEND"
        assert trace.reason_code == "TRANSITION_WINDOW"
        assert trace.guard_results["extension"]["ok"] is True

    async def test_h_same_activity_without_budget_really_changes(self) -> None:
        """延长预算用完时，同名的"下一个"必须换成别的（否则 Episode 永远结束不了）。"""

        class SamePlanner(ActivityPlanner):
            def next_after(self, *args: Any, **kwargs: Any) -> Any:
                from app.activity.planner import ActivityDecision as PlannerDecision
                from app.activity.planner import TransitionReason

                return PlannerDecision(
                    action="next",
                    reason=TransitionReason.TIME_EXPIRED,
                    activity_name="reading",
                )

        clock = FakeClock(1_700_000_000.0)
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=SamePlanner(),
            publisher=ActivityEventPublisher(),
            projection=ActivityProjection(_States()),
            transition_window_seconds=60.0,
            max_extensions_per_episode=0,
        )
        episode = await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        assert episode is not None
        clock.advance_minutes(21)
        after = await runtime.advance()
        assert after is not None
        assert after.episode_id != episode.episode_id
        assert after.activity_name != "reading"

    def test_is_mergeable_is_case_insensitive_and_name_based(self) -> None:
        current = ActivityEpisodeStub("Reading", "ACT-1", 0.0, 0.0, ActivityStatus.ACTIVE)
        assert is_mergeable(current, "reading")
        assert not is_mergeable(current, "gaming")
        assert not is_mergeable(current, "")


class TestPresentationMerge:
    def test_adjacent_same_activity_collapses_but_keeps_audit(self) -> None:
        """§十九：展示层合并 —— 原始 id 全部保留（**绝不丢审计**）。"""
        rows = [
            episode(
                "reading",
                episode_id="ACT-1",
                started=10.0,
                ended=20.0,
                status=ActivityStatus.COMPLETED,
            ),
            episode(
                "reading",
                episode_id="ACT-2",
                started=20.0,
                ended=30.0,
                status=ActivityStatus.COMPLETED,
            ),
            episode(
                "gaming",
                episode_id="ACT-3",
                started=30.0,
                ended=40.0,
                status=ActivityStatus.COMPLETED,
            ),
            episode(
                "reading",
                episode_id="ACT-4",
                started=40.0,
                ended=50.0,
                status=ActivityStatus.ACTIVE,
            ),
        ]
        merged = merge_adjacent(rows)
        assert [segment["activity"] for segment in merged] == ["reading", "gaming", "reading"]
        assert merged[0]["episode_ids"] == ["ACT-1", "ACT-2"]  # 两个原始 id 都在
        assert merged[0]["merged"] == 2
        assert merged[0]["started_at"] == 10.0 and merged[0]["ended_at"] == 30.0
        assert merged[1]["merged"] == 1
        assert merged[2]["status"] == ActivityStatus.ACTIVE.value

    def test_no_adjacent_means_no_merge(self) -> None:
        rows = [
            episode(
                "a", episode_id="ACT-1", started=1.0, ended=2.0, status=ActivityStatus.COMPLETED
            ),
            episode(
                "b", episode_id="ACT-2", started=2.0, ended=3.0, status=ActivityStatus.COMPLETED
            ),
        ]
        merged = merge_adjacent(rows)
        assert [segment["merged"] for segment in merged] == [1, 1]

    def test_limit_is_respected(self) -> None:
        rows = [
            episode(
                name,
                episode_id=f"ACT-{index}",
                started=float(index),
                ended=float(index + 1),
                status=ActivityStatus.COMPLETED,
            )
            for index, name in enumerate(["a", "b", "c", "d"], start=1)
        ]
        assert len(merge_adjacent(rows, limit=2)) == 2

    async def test_runtime_status_exposes_the_merged_timeline(self) -> None:
        clock = FakeClock(1_700_000_000.0)
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            projection=ActivityProjection(None),
        )
        first = await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        assert first is not None
        clock.advance_minutes(5)
        await runtime.complete(now=clock.now())
        second = await runtime.start(
            activity_name="reading",
            activity_type=ActivityType.VIRTUAL_LIFE,
            source=ActivitySource.ROUTINE,
            duration=PROFILE,
            now=clock.now(),
        )
        assert second is not None
        status = await runtime.status()
        assert status["merged_timeline"][0]["episode_ids"] == [first.episode_id, second.episode_id]
        assert status["merged_timeline"][0]["merged"] == 2
        # 原始审计（recent）仍然是两条
        assert len(status["recent"]) == 2
