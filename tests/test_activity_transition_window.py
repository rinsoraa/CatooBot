"""Phase 6B §八/§十/§十一/§三六/§三七：Transition Window（矩阵 F）。

钉死 §三十六 那条时间线：窗口内只立 ``transition_pending``，**绝不提前切活动**。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.activity import (
    ACTIVITY_COMPLETED,
    ACTIVITY_EXTENDED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivityStatus,
    FakeClock,
    InMemoryActivityStore,
)
from app.activity.decision import DecisionTrigger

#: 一小时的活动：planned_end = started + 60min；窗口 5 分钟
PROFILE = (10 * 60.0, 60 * 60.0, 300 * 60.0)


class _States:
    async def update(self, **_changes: Any) -> dict[str, Any]:
        return {}


class Rig:
    def __init__(self, *, window: float = 300.0) -> None:
        self.clock = FakeClock(1_700_000_000.0)
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=self.clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(sink=self._record),
            projection=ActivityProjection(_States()),
            transition_window_seconds=window,
            max_extensions_per_episode=2,
        )

    def _record(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))

    def names(self) -> list[str]:
        return [name for name, _payload in self.events]

    async def start(self) -> Any:
        episode = await self.runtime.start(
            activity_name="reading", duration=PROFILE, now=self.clock.now()
        )
        assert episode is not None
        return episode


@pytest.fixture()
def rig() -> Rig:
    return Rig()


class TestWindowTimeline:
    async def test_f_window_entry_does_not_switch_activity(self, rig: Rig) -> None:
        """§三六 的时间线：15:24 未进窗口 → 15:25 进入（pending）→ 15:29 仍 ACTIVE → 15:30 决策。"""
        episode = await rig.start()
        # planned_end = T+60min；窗口 5 分钟
        rig.clock.advance_minutes(54)  # 「15:24」
        current = await rig.runtime.advance()
        assert current is not None and current.episode_id == episode.episode_id
        view = rig.runtime.decision_view(current, now=rig.clock.now())
        assert view["transition_pending"] is False

        rig.clock.advance_minutes(1)  # 「15:25」进窗口
        current = await rig.runtime.advance()
        assert current is not None and current.episode_id == episode.episode_id
        view = rig.runtime.decision_view(current, now=rig.clock.now())
        assert view["transition_pending"] is True
        assert current.status is ActivityStatus.ACTIVE  # 只是准备，**没切**
        assert view["last_decision"]["decision"] == "CONTINUE"
        assert view["last_decision"]["reason_code"] == "TRANSITION_WINDOW"

        rig.clock.advance_minutes(4)  # 「15:29」还在窗口里
        current = await rig.runtime.advance()
        assert current is not None and current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE
        assert rig.names().count(ACTIVITY_COMPLETED) == 0
        assert rig.names().count(ACTIVITY_EXTENDED) == 0

        rig.clock.advance_minutes(1)  # 「15:30」到期 → 决策
        after = await rig.runtime.advance()
        assert after is not None
        assert after.episode_id == episode.episode_id  # 先尝试延长（有预算）
        assert after.status is ActivityStatus.EXTENDED

    async def test_window_is_configurable(self) -> None:
        wide = Rig(window=30 * 60.0)  # 半小时窗口
        episode = await wide.start()
        wide.clock.advance_minutes(31)
        view = wide.runtime.decision_view(episode, now=wide.clock.now())
        assert view["transition_pending"] is True
        narrow = Rig(window=60.0)  # 一分钟窗口
        episode2 = await narrow.start()
        narrow.clock.advance_minutes(31)
        assert (
            narrow.runtime.decision_view(episode2, now=narrow.clock.now())["transition_pending"]
            is False
        )

    async def test_zero_window_means_decide_at_expiry(self) -> None:
        rig = Rig(window=0.0)
        episode = await rig.start()
        rig.clock.advance_minutes(30)
        view = rig.runtime.decision_view(episode, now=rig.clock.now())
        assert view["transition_pending"] is False  # 没有窗口就没有"待命"
        assert view["transition_window_seconds"] == 0.0

    async def test_pending_is_only_for_live_episodes(self, rig: Rig) -> None:
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        await rig.runtime.complete(now=rig.clock.now())
        assert rig.runtime.decision_view(None, now=rig.clock.now())["transition_pending"] is False


class TestTickBehaviour:
    async def test_普通_tick_在窗口之前_什么都不做(self, rig: Rig) -> None:
        """§十：``now < planned_end - window`` → 不选新活动、不建 Episode、不写事件。"""
        episode = await rig.start()
        rig.clock.advance_minutes(10)
        current = await rig.runtime.advance()
        assert current is not None and current.episode_id == episode.episode_id
        assert rig.names() == ["activity.scheduled", "activity.started"]
        assert (await rig.runtime.recent(10))[0].episode_id == episode.episode_id

    async def test_tick_once_per_decision(self, rig: Rig) -> None:
        """§三七：一个 transition 只做**一次**决策（不会一次 tick 里决策好几轮）。"""
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        await rig.runtime.advance()
        traces = rig.runtime.engine.traces
        assert len(traces) == 1
        assert traces[0].episode_id == episode.episode_id
        assert traces[0].trigger in {
            DecisionTrigger.TIME_EXPIRED.value,
            DecisionTrigger.TIME_NEAR_END.value,
        }

    async def test_manual_decision_does_not_mutate(self, rig: Rig) -> None:
        """``decide_now`` 只给决策与 trace，**不改**状态（恢复/手动/测试都用它）。"""
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        decision = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert decision["decision"] in {"EXTEND", "TRANSITION"}
        current = await rig.runtime.current()
        assert current is not None
        assert current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE  # 还活着 —— 决策不等于执行
        assert current.extension_count == 0
