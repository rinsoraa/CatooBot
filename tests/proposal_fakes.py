"""Phase 7C 测试夹具：真 store + 真 service + 可控的只读事实（身份 / 在线状态）。

刻意**不联网、不调模型、不开真库**（§十三）：7C 本来就是纯确定性的一层，
所以这里的"假"只有三样 —— **喂进去的身份绑定、在线状态、时钟**。

:class:`ProposalRig` 里**没有** TaskRuntime / ActionRuntime / Minecraft 工具面 ——
这正是要测的性质：提案层连"能执行"的对象都拿不到（§五/§十三-10~13）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.initiative import (
    InitiativeSource,
    LifeIntent,
    LifeIntentStatus,
    LifeIntentType,
)
from app.integrations.minecraft.identity import CONFLICT, VERIFIED
from app.tasks.proposal_service import TaskProposalService
from app.tasks.proposal_store import InMemoryTaskProposalStore

#: 常用时间点（确定性；不用机器时钟）
T0 = 1_800_000_000.0

SERVER = "mc-1a2b3c"


@dataclass
class FakeConfig:
    """提案配置（与 ``WorldProposalConfig`` 同形）。"""

    enabled: bool = True
    recent_limit: int = 10
    max_per_pass: int = 3
    ttl_hours: int = 6


@dataclass
class FakeLink:
    """一条身份绑定（鸭子类型上等价于 5C 的 ``IdentityLink``）。"""

    status: str = VERIFIED
    player_uuid: str = ""
    username: str = ""
    server_id: str = SERVER
    source: str = "explicit_verification"

    @property
    def canonical_uuid(self) -> str:
        return str(self.player_uuid or "").replace("-", "").lower()


@dataclass
class FakeIdentity:
    """只读身份探针（``links_for`` 是 5C ``IdentityStore`` 的既有方法名）。"""

    links: list[FakeLink] = field(default_factory=list)
    fails: bool = False

    async def links_for(
        self, *, platform: str, user_id: str, include_revoked: bool = True
    ) -> list[FakeLink]:
        if self.fails:
            raise RuntimeError("identity store down")
        return list(self.links)


def link(
    uuid: str = "a" * 32,
    *,
    status: str = VERIFIED,
    username: str = "Rinsora",
    server_id: str = SERVER,
) -> FakeLink:
    return FakeLink(status=status, player_uuid=uuid, username=username, server_id=server_id)


@dataclass
class ProposalRig:
    """真 store + 真 service；身份 / 在线状态 / 时钟由用例喂。"""

    identity: FakeIdentity = field(default_factory=FakeIdentity)
    config: Any = None
    store: InMemoryTaskProposalStore = field(default_factory=InMemoryTaskProposalStore)
    online: bool = True
    clock_now: float = T0
    service: TaskProposalService | None = None

    def __post_init__(self) -> None:
        if self.config is None:
            self.config = FakeConfig()
        self.service = TaskProposalService(
            store=self.store,
            config=self.config,
            character_id="罐头@deadbeef",
            identity=self.identity,
            minecraft_online=lambda: bool(self.online),
            clock=lambda: self.clock_now,
        )

    def advance(self, seconds: float) -> None:
        self.clock_now += float(seconds)

    async def propose(self, **changes: Any) -> Any:
        """提一条 LIFE 提案（默认：在线 + 收集橡木）。"""
        return await self.service.propose_from_intent(intent(**changes))

    async def request(self, objective: str, user_id: str = "2731431246", **extra: Any) -> Any:
        return await self.service.record_user_request(objective=objective, user_id=user_id, **extra)


def intent(
    title: str = "想去 Minecraft 收集一点橡木",
    *,
    intent_id: str = "INT-0001",
    description: str = "想起那片树林了",
    status: LifeIntentStatus = LifeIntentStatus.PROPOSED,
    related_player: str = "",
    related_memory: str = "",
    expires_at: float | None = None,
    intent_type: LifeIntentType = LifeIntentType.MINECRAFT_INTEREST,
) -> LifeIntent:
    return LifeIntent(
        intent_id=intent_id,
        character_id="罐头@deadbeef",
        intent_type=intent_type,
        title=title,
        description=description,
        source=InitiativeSource.MEMORY,
        origin="memory:树林",
        priority=0.7,
        confidence=0.7,
        created_at=T0 - 60,
        expires_at=T0 + 3600 if expires_at is None else float(expires_at),
        related_memory=related_memory,
        related_player=related_player,
        status=status,
    )


__all__ = [
    "CONFLICT",
    "SERVER",
    "T0",
    "VERIFIED",
    "FakeConfig",
    "FakeIdentity",
    "FakeLink",
    "ProposalRig",
    "intent",
    "link",
]
