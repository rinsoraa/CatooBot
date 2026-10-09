"""Phase 7D 测试夹具：真 store + 真规划器 + 真 service + 可控的执行/身份事实。

* **不联网、不开真库**；"假"的只有观察结果、任务运行时与身份解析；
* ``FakeTaskRuntime`` 只实现 AgentPlan 服务真正调用的两个方法
  （``create_task`` / ``summary_of``）—— 它故意**没有** execute/confirm 能力，
  这正是要测的性质：计划层只能通过既有 TaskRuntime 入口进入执行链；
* follow 生命周期（修订 2）的证据在 ``tests/test_agent_plan_follow.py``。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.tasks.agent_plan import AgentPlan, PlanStatus
from app.tasks.agent_plan_store import InMemoryAgentPlanStore
from app.tasks.agent_planner import BoundedAgentPlanner
from app.tasks.agent_service import AgentPlanService
from app.tasks.proposal import TaskProposal

#: 常用时间点（确定性；不用机器时钟）
T0 = 1_800_000_000.0
SERVER = "mc-1a2b3c"


@dataclass
class FakeRuntimeConfig:
    ttl_seconds: float = 600.0
    max_steps: int = 16
    max_replans: int = 2


class FakeTaskRecord:
    def __init__(self, task_id: str, plan: Any, *, session_id: str = "s") -> None:
        self.task_id = task_id
        self.plan = plan
        self.plan_hash = "hash-" + task_id
        self.objective = getattr(plan, "objective", "")
        self.state = type("S", (), {"value": "PENDING_CONFIRMATION"})()
        self.plan_version = 1
        self.session_id = session_id
        self.user_id = "u"


class FakeTaskRuntime:
    """只暴露 AgentPlan 服务需要的入口；create_task 记录调用（审计断言用）。"""

    def __init__(self, *, busy: bool = False) -> None:
        self.created: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self.busy = busy
        self._seq = 0
        self.records: dict[str, FakeTaskRecord] = {}

    async def create_task(
        self,
        objective: str,
        *,
        session_id: str,
        user_id: str,
        origin: str,
        plan: Any,
        observations: Any = None,
        source: str = "",
    ) -> FakeTaskRecord:
        if self.busy:
            from app.tasks.runtime import TaskBusy

            raise TaskBusy(FakeTaskRecord("busy", plan))
        self._seq += 1
        record = FakeTaskRecord(f"T-{self._seq:03d}", plan, session_id=session_id)
        record.user_id = user_id
        self.records[record.task_id] = record
        self.created.append(
            {
                "objective": objective,
                "session_id": session_id,
                "user_id": user_id,
                "origin": origin,
                "source": source,
                "steps": [step.tool for step in plan.steps],
            }
        )
        return record

    async def get(self, task_id: str) -> FakeTaskRecord | None:
        return self.records.get(str(task_id))

    async def cancel(self, task_id: str, *, reason: str = "") -> Any:
        self.cancelled.append(str(task_id))
        record = self.records.get(str(task_id))
        if record is not None:
            record.state = type("S", (), {"value": "CANCELLED"})()
        return record

    def summary_of(self, record: Any) -> str:
        return f"任务：{getattr(record, 'objective', '')}"


@dataclass
class FakeIdentity:
    """只读身份探针（与 7C 夹具同形）。"""

    links: list[Any] = field(default_factory=list)
    fails: bool = False

    async def links_for(
        self, *, platform: str, user_id: str, include_revoked: bool = True
    ) -> list[Any]:
        if self.fails:
            raise RuntimeError("identity store down")
        return list(self.links)


@dataclass
class FakeLink:
    status: str = "VERIFIED"
    player_uuid: str = ""
    username: str = ""
    server_id: str = SERVER
    source: str = "explicit_verification"

    @property
    def canonical_uuid(self) -> str:
        return str(self.player_uuid or "").replace("-", "").lower()


def link(uuid: str = "a" * 32, *, status: str = "VERIFIED", username: str = "Rinsora") -> FakeLink:
    return FakeLink(status=status, player_uuid=uuid, username=username)


def observe_ok(**extra: Any) -> Any:
    """一个最小的 SAFE 观察替身：find_blocks 永远找到 oak_log。"""

    async def observe(tool: str, arguments: dict[str, Any]) -> Any:
        if tool == "minecraft_find_blocks":
            return type(
                "R",
                (),
                {
                    "result": {
                        "ok": True,
                        "matches": [{"position": {"x": -10, "y": 70, "z": 5}, "distance": 3.0}],
                    },
                    "summary": "found",
                },
            )()
        if tool == "minecraft_world":
            return type(
                "R",
                (),
                {
                    "result": {"semantic": {"self": {"position": {"x": -8, "y": 70, "z": 5}}}},
                    "summary": "world",
                },
            )()
        if tool == "minecraft_inventory":
            return type(
                "R",
                (),
                {"result": {"held_item": {"name": "netherite_axe"}, "items": []}, "summary": "inv"},
            )()
        if tool == "minecraft_dig_capability":
            return type(
                "R",
                (),
                {"result": {"can_dig": True, "reason": ""}, "summary": "can dig"},
            )()
        raise AssertionError(f"意外工具 {tool}")

    return observe


@dataclass
class PlanRig:
    """真 store + 真规划器 + 真 service；身份/任务/观察由用例喂。"""

    identity: FakeIdentity = field(default_factory=FakeIdentity)
    store: InMemoryAgentPlanStore = field(default_factory=InMemoryAgentPlanStore)
    runtime: FakeTaskRuntime = field(default_factory=FakeTaskRuntime)
    online: bool = True
    observe: Any = field(default_factory=observe_ok)
    allow_medium: bool = True
    clock_now: float = T0
    service: AgentPlanService | None = None
    proposals: Any = None

    def __post_init__(self) -> None:
        planner = BoundedAgentPlanner(
            allow_safe=True,
            allow_low=True,
            allow_medium=self.allow_medium,
            follow_timeout_seconds=120.0,
        )
        self.service = AgentPlanService(
            store=self.store,
            planner=planner,
            task_runtime=self.runtime,
            observe=self.observe if self.online else None,
            character_id="罐头@deadbeef",
            plan_ttl_seconds=600.0,
            replan_budget=2,
            clock=lambda: self.clock_now,
        )

    def advance(self, seconds: float) -> None:
        self.clock_now += float(seconds)


def proposal(
    pid: str = "TP-001",
    *,
    source: str = "LIFE",
    objective: str = "想去 Minecraft 收集一点橡木",
    status: str = "NEEDS_USER_APPROVAL",
    intent_id: str = "INT-001",
    initiator: str = "罐头@deadbeef",
    target: dict[str, Any] | None = None,
) -> TaskProposal:
    from app.tasks.proposal import TaskProposal

    return TaskProposal(
        proposal_id=pid,
        source=source,
        objective=objective,
        status=status,
        intent_id=intent_id,
        initiator=initiator,
        target=target or {"status": "VERIFIED", "reason": "no_player_target"},
        created_at=T0 - 30,
        expires_at=T0 + 3600,
    )


__all__ = [
    "SERVER",
    "T0",
    "AgentPlan",
    "FakeIdentity",
    "FakeLink",
    "FakeRuntimeConfig",
    "FakeTaskRuntime",
    "PlanRig",
    "PlanStatus",
    "link",
    "observe_ok",
    "proposal",
]
