"""Phase 6C 测试夹具：假时钟 + 只读状态替身 + 假目标来源 + 最小的 runtime 组装。

刻意**不**引入任何真实数据库/沙盒/Minecraft 部件：6C 的规划全是纯计算，
所以测试要跑得快、要能"快进 3 天"而不 sleep（§二十四/§六十二）。
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from typing import Any

from app.activity import (
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    FakeClock,
    GoalSnapshot,
    InMemoryActivityStore,
    build_goal,
)

#: 假时钟的时区（与项目默认一致）
TZ_OFFSET_HOURS = 8


def clock_at(hour: int, minute: int = 0, *, day: int = 15) -> FakeClock:
    """停在新加坡时间某一点的假时钟（6C 的锚点/时段都按配置时区算）。"""
    naive = _dt.datetime(2026, 10, day, hour, minute)
    epoch = naive.replace(tzinfo=_dt.timezone(_dt.timedelta(hours=TZ_OFFSET_HOURS))).timestamp()
    return FakeClock(start=epoch)


def local_text(clock: Any, timestamp: float) -> str:
    """时间戳 → 本地 ``HH:MM``（断言用，绝不用机器时区）。"""
    return clock.local(float(timestamp)).strftime("%H:%M")


@dataclass
class State:
    """只读角色状态替身（字段名与 ``CharacterState`` 一致：§二十六 适配而非新建）。"""

    energy: float = 0.8
    current_focus: Any = ""
    mood: str = "neutral"
    schedule_state: str = "awake"
    social_state: str = "alone"


@dataclass
class GoalSourceStub:
    """假的只读目标来源（只实现 ``snapshot``；没有 create/complete —— §二十五）。"""

    goals: tuple[Any, ...] = ()
    source: str = "stub"
    calls: int = 0

    def snapshot(self) -> GoalSnapshot:
        self.calls += 1
        return GoalSnapshot(goals=tuple(self.goals), source=self.source)


@dataclass
class BrokenGoalSource:
    """读目标就炸的来源（§四十八：只降级，绝不阻塞规划）。"""

    def snapshot(self) -> GoalSnapshot:
        raise RuntimeError("goal source exploded")


class BrokenPlanner(ActivityPlanner):
    """规划就炸的 Planner（§四十八：保留现有活动，绝不让 activity 变空）。"""

    def plan_next(self, *args: Any, **kwargs: Any) -> Any:
        raise RuntimeError("planner exploded")


def goal(
    goal_id: str = "g1",
    kind: str = "complete_project",
    *,
    priority: float = 0.8,
    progress: float = 0.2,
    status: Any = "ACTIVE",
    target_project: str = "小屋",
    title: str = "",
) -> Any:
    return build_goal(
        goal_id=goal_id,
        kind=kind,
        status=status,
        priority=priority,
        progress=progress,
        description="测试用目标",
        target_project=target_project,
        title=title,
    )


@dataclass
class GoalStates:
    """既有的最小状态替身（记录投影写了什么），供计划恢复类用例使用。"""

    data: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    async def update(self, **changes: Any) -> dict[str, Any]:
        reason = str(changes.pop("reason", ""))
        self.reasons.append(reason)
        self.data.update(changes)
        return dict(self.data)


@dataclass
class PlanRig:
    """一套真的部件（store/planner/runtime）+ 假时钟；没有数据库、没有沙盒、没有 Minecraft。"""

    clock: FakeClock = field(default_factory=lambda: clock_at(14, 0))
    store: InMemoryActivityStore = field(default_factory=InMemoryActivityStore)
    states: GoalStates = field(default_factory=GoalStates)
    #: Planner 的只读状态输入（能量/专注/作息），可被用例替换
    state: State = field(default_factory=State)
    events: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    planner: ActivityPlanner | None = None
    runtime: ActivityRuntime | None = None
    #: 可选 logger（6D.1：用例要断言 [Activity.Model] 那一行日志时必须给一个）
    logger: Any = None

    def __post_init__(self) -> None:
        if self.planner is None:
            self.planner = ActivityPlanner()
        self.runtime = ActivityRuntime(
            store=self.store,
            clock=self.clock,
            character_id="罐头@deadbeef",
            planner=self.planner,
            publisher=ActivityEventPublisher(sink=self._record),
            projection=ActivityProjection(self.states),
            state_provider=lambda: self.state,
            logger=self.logger,
        )

    def _record(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))

    def names(self) -> list[str]:
        return [name for name, _payload in self.events]
