"""Phase 6B §二二/§三九/§四八：Decision Trace（矩阵：trace 形状 + 只读 API + 不存思维链）。

trace 只保存**结构化决策事实**：episode / 时间 / 时长档 / 时段 / 原因 / 护栏结论 / 决策。
绝不保存 hidden chain of thought（§二二）。
"""

from __future__ import annotations

from typing import Any

from app.activity import (
    ACTIVITY_EXTENDED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivityStatus,
    FakeClock,
    InMemoryActivityStore,
)
from app.activity.decision import DecisionReason, DecisionTrigger

PROFILE = (5 * 60.0, 20 * 60.0, 600 * 60.0)

#: trace 必须具备的字段（§二二 的清单）
REQUIRED_TRACE_FIELDS = {
    "trace_id",
    "episode_id",
    "character_id",
    "current_activity",
    "trigger",
    "decision",
    "reason_code",
    "elapsed",
    "planned_end_at",
    "min_duration",
    "typical_duration",
    "max_duration",
    "extension_count",
    "time_period",
    "transition_pending",
    "next_activity_hint",
    "extension_seconds",
    "guard_results",
    "decided_at",
}


class _States:
    async def update(self, **_changes: Any) -> dict[str, Any]:
        return {}


def runtime_for(clock: FakeClock) -> ActivityRuntime:
    return ActivityRuntime(
        store=InMemoryActivityStore(),
        clock=clock,
        character_id="罐头@deadbeef",
        planner=ActivityPlanner(),
        publisher=ActivityEventPublisher(),
        projection=ActivityProjection(_States()),
        transition_window_seconds=300.0,
        max_extensions_per_episode=2,
    )


class TestTraceShape:
    async def test_every_decision_leaves_a_structured_trace(self) -> None:
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        episode = await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        assert episode is not None
        clock.advance_minutes(21)
        await runtime.advance()
        trace = runtime.engine.traces[-1]
        payload = trace.to_payload()
        assert set(payload) >= REQUIRED_TRACE_FIELDS
        assert payload["episode_id"] == episode.episode_id
        assert payload["current_activity"] == "reading"
        assert payload["decision"] == "EXTEND"
        assert payload["time_period"]  # 时段来自 WorldClock
        assert payload["guard_results"]["extension"]["ok"] is True
        assert payload["decided_at"] == clock.now()

    def test_no_chain_of_thought_anywhere_in_the_trace(self) -> None:
        """§二二 末句 / §四八 末句：绝不返回思维链。"""
        clock = FakeClock()
        runtime = runtime_for(clock)
        trace_payload = runtime.engine.view(None, now=clock.now())
        for forbidden in ("chain_of_thought", "thoughts", "reasoning", "thinking", "cot"):
            assert forbidden not in trace_payload
            assert (
                forbidden not in runtime.engine.last_trace().to_payload()
                if (runtime.engine.last_trace())
                else True
            )

    async def test_trace_ids_are_unique_and_traces_are_bounded(self) -> None:
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        for _ in range(40):
            clock.advance_minutes(1)
            await runtime.advance()
        traces = runtime.engine.traces
        ids = [trace.trace_id for trace in traces]
        assert len(ids) == len(set(ids))  # 每次 trace 一个 id
        assert len(traces) <= runtime.engine.max_traces  # 有界（不无限增长）

    async def test_decision_object_carries_the_trace_id(self) -> None:
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        clock.advance_minutes(21)
        decision = await runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert decision["trace"]["trace_id"]
        assert decision["decision"] == "EXTEND"
        assert decision["reason"] == DecisionReason.TRANSITION_WINDOW.value


class TestReadOnlyViews:
    async def test_decision_view_shape(self) -> None:
        """§四八 的只读形状：decision / reason / transition_pending / elapsed / planned_end / …"""
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        clock.advance_minutes(21)
        await runtime.advance()
        view = runtime.decision_view(await runtime.current(), now=clock.now())
        assert {
            "episode_id",
            "current_activity",
            "status",
            "elapsed_seconds",
            "planned_end_at",
            "transition_window_seconds",
            "transition_pending",
            "extension_count",
            "max_extensions",
            "last_decision",
            "guard",
        } == set(view)
        assert view["last_decision"]["decision"] == "EXTEND"
        assert view["elapsed_seconds"] >= 20 * 60
        assert view["extension_count"] == 1
        assert view["guard"]["bounce"]["cooldown_seconds"] > 0

    async def test_status_includes_decision_and_consistency(self) -> None:
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        status = await runtime.status()
        assert status["decision"]["transition_pending"] in {True, False}
        assert status["consistency"]["ok"] is True
        assert "merged_timeline" in status

    async def test_view_without_episode_is_honest(self) -> None:
        clock = FakeClock()
        runtime = runtime_for(clock)
        view = runtime.decision_view(None, now=clock.now())
        assert view["episode_id"] == ""
        assert view["transition_pending"] is False
        assert view["last_decision"] is None


class TestDecisionEvents:
    async def test_extend_and_transition_still_emit_existing_events(self) -> None:
        """§三九：不新增一堆事件 —— 决策的**结果**仍然走 6A 已有的生命周期事件。"""
        clock = FakeClock(1_700_000_000.0)
        seen: list[str] = []
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(sink=lambda name, _payload: seen.append(name)),
            projection=ActivityProjection(_States()),
            transition_window_seconds=60.0,
            max_extensions_per_episode=1,
        )
        await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        clock.advance_minutes(21)
        await runtime.advance()
        assert ACTIVITY_EXTENDED in seen
        clock.advance_minutes(21)  # 预算用完 → TRANSITION
        after = await runtime.advance()
        assert after is not None
        assert "activity.completed" in seen
        # 决策本身不产生新的事件名（只写 trace + 日志）
        assert "activity.decision" not in seen


class TestTraceOnRecovery:
    async def test_recovery_alignment_is_visible_in_the_episode_history(self) -> None:
        """恢复对齐（6A）与决策（6B）都要能追溯：Episode 的转移审计 + 决策 trace 各司其职。"""
        clock = FakeClock(1_700_000_000.0)
        runtime = runtime_for(clock)
        episode = await runtime.start(activity_name="reading", duration=PROFILE, now=clock.now())
        assert episode is not None
        clock.advance_minutes(21)
        await runtime.advance()
        rows = await runtime.recent_transitions(episode.episode_id, limit=10)
        assert [row["transition"] for row in rows] == ["ACTIVE", "EXTENDED"]
        assert runtime.engine.traces[-1].episode_id == episode.episode_id
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.EXTENDED
