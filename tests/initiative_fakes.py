"""Phase 7A 测试夹具：一套真部件（store / gate / service）+ 可注入的只读 context。

刻意**不联网、不调模型**（§六十二）：7A 本来就是纯确定性的一层，
所以这里的"假"只有一样东西 —— **喂进去的只读事实**。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

from app.activity.planner import ROUTINE_BY_PERIOD
from app.initiative import (
    CommitmentSignal,
    GoalSignal,
    InitiativeContext,
    InitiativeGate,
    InitiativeSource,
    InMemoryLifeIntentStore,
    LifeIntent,
    LifeIntentService,
    LifeIntentStatus,
    LifeIntentType,
    propose_intents,
)

#: 常用时间点（确定性；不用机器时钟）
T0 = 1_700_000_000.0


@dataclass
class Rig:
    """真 store + 真 gate + 真 service；context 由用例喂。"""

    context: InitiativeContext = field(
        default_factory=lambda: InitiativeContext(character_id="罐头@deadbeef", now=T0)
    )
    config: Any = None
    store: InMemoryLifeIntentStore = field(default_factory=InMemoryLifeIntentStore)
    clock_now: float = T0
    service: LifeIntentService | None = None

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = FakeConfig()
        self.service = LifeIntentService(
            store=self.store,
            config=self.config,
            context_provider=self._context,
            character_id=str(self.context.character_id),
            gate=InitiativeGate(),
            clock=lambda: self.clock_now,
        )
        # 夹具默认跳过"重启静默期"，需要它的用例自己打开
        self.service.recovered_at = 0.0

    async def _context(self, now: float) -> InitiativeContext:
        return replace(self.context, now=float(now))

    def feed(self, **changes: Any) -> None:
        """改一处只读事实（用例的主要手写入口）。"""
        self.context = replace(self.context, now=self.clock_now, **changes)

    async def check(self, *, advance: float = 0.0) -> dict[str, Any]:
        self.clock_now += float(advance)
        assert self.service is not None
        return await self.service.check(trigger="scheduled")

    async def propose(self, **changes: Any) -> list[LifeIntent]:
        """只跑候选生成（不碰门禁、不落盘），方便单独验证某个信号。"""
        if changes:
            self.feed(**changes)
        return list(propose_intents(self.context))


@dataclass
class FakeConfig:
    """`world.initiative` 的四个旋钮（默认值与 §六十四 一致）。"""

    enabled: bool = True
    cooldown_minutes: int = 20
    max_proposals_per_hour: int = 3
    recent_interaction_suppress_minutes: int = 10


def intent(
    *,
    intent_type: LifeIntentType = LifeIntentType.SOCIAL,
    source: InitiativeSource = InitiativeSource.SOCIAL,
    title: str = "想看看大家在聊什么",
    character_id: str = "罐头@deadbeef",
    created_at: float = T0,
    status: LifeIntentStatus = LifeIntentStatus.PROPOSED,
    fingerprint: str = "",
    tags: tuple[str, ...] = (),
    semantic_key: str = "k",
) -> LifeIntent:
    """造一条意图（用来喂门禁的"最近意图"）。"""
    return LifeIntent(
        intent_id=f"INT-TEST-{int(created_at)}",
        character_id=character_id,
        intent_type=intent_type,
        title=title,
        source=source,
        priority=0.5,
        created_at=float(created_at),
        expires_at=float(created_at) + 3600.0,
        status=status,
        fingerprint=(
            fingerprint or f"{character_id}|{intent_type.value}|{semantic_key}|{int(created_at)}"
        ),
        tags=tags,
    )


def goal(goal_id: str = "G-1", kind: str = "complete_project", label: str = "小城") -> GoalSignal:
    return GoalSignal(goal_id=goal_id, kind=kind, label=label, progress=0.3, priority=0.8)


def commitment(commitment_id: str = "C-1", label: str = "晚上一起玩 Minecraft") -> CommitmentSignal:
    return CommitmentSignal(commitment_id=commitment_id, kind="shared_activity", label=label)


#: 时段表（从 Planner 借来，测试里不必再抄一份）
ROUTINE = ROUTINE_BY_PERIOD
