"""Phase 7D §二：AgentPlan —— 规划与审计层（**不是**第二套任务状态机）。

四个概念严格分层（§二/修订 3）：

```text
TaskProposal = 知道需要什么能力（7C）
AgentPlan    = 准备怎样做的**结构化计划**（本模块；只有规划权，没有执行权）
Task         = 已进入执行系统的任务（5A；AgentPlan 只保存关联的 task_id）
Step 状态     = 永远**实时读** TaskRuntime 的 checkpoint/事件（本模块不持久化执行状态）
```

* 状态机里**没有** EXECUTING/RUNNING —— 执行状态是 Task 的事（修订 3）；
* 终态（REJECTED/EXPIRED/CANCELLED）没有出口；
* 指纹 = 来源|目标|目标键|proposal_id|时间桶，同桶幂等（与 7C 同一思路）。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 提案号前缀
PLAN_ID_PREFIX = "AP"
#: 计划去重桶（相同来源+目标在同一个桶里只规划一次）
PLAN_BUCKET_SECONDS = 600.0
#: 计划总时限（与既有 Task TTL 600s 对齐；授权 TTL 60s / 动作超时 120s 是另外两层，分层呈现）
DEFAULT_PLAN_TTL_SECONDS = 600.0
#: 每份计划的重规划上限（§六：不得无界重试）
DEFAULT_REPLAN_BUDGET = 2

#: 身份解析状态（复用 7C 的词表；AgentPlan 只保存"可信来源是什么"）
IDENTITY_VERIFIED = "VERIFIED"
IDENTITY_MISSING = "MISSING"


class PlanSource(str, Enum):  # noqa: UP042
    """计划来源（§四）。与 7C 的 ProposalSource 同词表。"""

    USER = "USER"
    LIFE = "LIFE"
    SYSTEM = "SYSTEM"


class PlanStatus(str, Enum):  # noqa: UP042
    """计划状态（§三 的可审计结论 + 生命周期）。**没有** EXECUTING/RUNNING（修订 3）。"""

    #: 还在规划（中间态，一般不落盘）
    PLANNING = "PLANNING"
    #: 规划成功，等用户批准（USER：与待确认任务同时建立；LIFE：只挂起，不建 Task）
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    #: 信息不足（缺能力/缺身份/世界不可观测）—— 如实给原因
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
    #: 能力目录撑不起这个目标（附缺口），已退回提案层
    UNSUPPORTED = "UNSUPPORTED"
    #: Policy/风险语义明确挡住（例如 allow_medium=false 时的高风险计划）
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    #: 前置条件不满足（目标不在线/身份失效/观察失败）
    BLOCKED_BY_PRECONDITION = "BLOCKED_BY_PRECONDITION"
    #: 用户已批准（LIFE 第二道门的前一刻；USER 侧由 Task 事件派生，一般跳过落盘）
    APPROVED = "APPROVED"
    #: 已沿既有链建了待确认任务（task_id 已关联；执行状态去 Task 那边读）
    LINKED = "LINKED"
    #: 需要重新规划（世界变了/授权失效），还没排出来
    REPLAN_REQUIRED = "REPLAN_REQUIRED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_PLAN_STATUSES

    @property
    def open(self) -> bool:
        return self in OPEN_PLAN_STATUSES


#: 终态：只进不出（重启恢复也不许覆盖）
TERMINAL_PLAN_STATUSES = frozenset({PlanStatus.REJECTED, PlanStatus.EXPIRED, PlanStatus.CANCELLED})
#: 还开着的状态
OPEN_PLAN_STATUSES = frozenset(
    {
        PlanStatus.PLANNING,
        PlanStatus.READY_FOR_APPROVAL,
        PlanStatus.NEEDS_MORE_INFORMATION,
        PlanStatus.UNSUPPORTED,
        PlanStatus.BLOCKED_BY_POLICY,
        PlanStatus.BLOCKED_BY_PRECONDITION,
        PlanStatus.APPROVED,
        PlanStatus.LINKED,
        PlanStatus.REPLAN_REQUIRED,
    }
)

#: 允许的状态转移（终态没有出口；LINKED 之后计划本身不再变 —— 执行状态去 Task 读）
ALLOWED_PLAN_TRANSITIONS: dict[PlanStatus, frozenset[PlanStatus]] = {
    PlanStatus.PLANNING: frozenset(
        {
            PlanStatus.READY_FOR_APPROVAL,
            PlanStatus.NEEDS_MORE_INFORMATION,
            PlanStatus.UNSUPPORTED,
            PlanStatus.BLOCKED_BY_POLICY,
            PlanStatus.BLOCKED_BY_PRECONDITION,
            PlanStatus.CANCELLED,
        }
    ),
    PlanStatus.READY_FOR_APPROVAL: frozenset(
        {
            PlanStatus.APPROVED,
            PlanStatus.LINKED,
            PlanStatus.REPLAN_REQUIRED,
            PlanStatus.REJECTED,
            PlanStatus.EXPIRED,
            PlanStatus.CANCELLED,
        }
    ),
    PlanStatus.NEEDS_MORE_INFORMATION: frozenset(
        {
            PlanStatus.READY_FOR_APPROVAL,
            PlanStatus.REJECTED,
            PlanStatus.EXPIRED,
            PlanStatus.CANCELLED,
        }
    ),
    PlanStatus.UNSUPPORTED: frozenset(
        {PlanStatus.REJECTED, PlanStatus.EXPIRED, PlanStatus.CANCELLED}
    ),
    PlanStatus.BLOCKED_BY_POLICY: frozenset(
        {PlanStatus.REJECTED, PlanStatus.EXPIRED, PlanStatus.CANCELLED}
    ),
    PlanStatus.BLOCKED_BY_PRECONDITION: frozenset(
        {
            PlanStatus.READY_FOR_APPROVAL,
            PlanStatus.REJECTED,
            PlanStatus.EXPIRED,
            PlanStatus.CANCELLED,
        }
    ),
    PlanStatus.APPROVED: frozenset({PlanStatus.LINKED, PlanStatus.EXPIRED, PlanStatus.CANCELLED}),
    PlanStatus.LINKED: frozenset(
        {PlanStatus.REPLAN_REQUIRED, PlanStatus.EXPIRED, PlanStatus.CANCELLED}
    ),
    PlanStatus.REPLAN_REQUIRED: frozenset(
        {
            PlanStatus.READY_FOR_APPROVAL,
            PlanStatus.NEEDS_MORE_INFORMATION,
            PlanStatus.UNSUPPORTED,
            PlanStatus.BLOCKED_BY_PRECONDITION,
            PlanStatus.EXPIRED,
            PlanStatus.CANCELLED,
        }
    ),
    PlanStatus.REJECTED: frozenset(),
    PlanStatus.EXPIRED: frozenset(),
    PlanStatus.CANCELLED: frozenset(),
}


def plan_transition_allowed(current: PlanStatus, target: PlanStatus) -> bool:
    return target in ALLOWED_PLAN_TRANSITIONS.get(current, frozenset())


def plan_fingerprint(
    *,
    source: PlanSource | str,
    objective: str,
    target_key: str = "",
    proposal_id: str = "",
    bucket: int = 0,
) -> str:
    """确定性指纹（同桶幂等；与 7C 的 proposal_fingerprint 同一思路）。"""
    kind = getattr(source, "value", source)
    parts = (
        str(kind),
        " ".join(str(objective or "").split()).lower()[:120],
        str(target_key or "").strip().lower(),
        str(proposal_id or "").strip(),
        str(int(bucket)),
    )
    return "|".join(parts)


def plan_time_bucket(now: float, *, bucket_seconds: float = PLAN_BUCKET_SECONDS) -> int:
    return int(float(now) // max(1.0, float(bucket_seconds)))


def plan_id_for(day: str, sequence: int) -> str:
    return f"{PLAN_ID_PREFIX}-{str(day).replace('-', '')}-{int(sequence):03d}"


def day_text(now: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now)))


@dataclass(frozen=True)
class PlanTarget:
    """计划的目标实体（§二/修订 2）：只保存**可信来源**解析出的事实，绝不存昵称猜测。"""

    #: 7C 的身份解析结论（VERIFIED/MISSING/...）；不需要指定玩家时 = VERIFIED + no_player_target
    status: str = IDENTITY_VERIFIED
    server_id: str = ""
    player_uuid: str = ""
    player_name: str = ""
    reason: str = ""

    @property
    def verified(self) -> bool:
        return self.status == IDENTITY_VERIFIED

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "server_id": str(self.server_id),
            "player_uuid": str(self.player_uuid),
            "player_name": str(self.player_name),
            "reason": str(self.reason),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> PlanTarget:
        data = dict(payload or {})
        return cls(
            status=str(data.get("status") or IDENTITY_MISSING),
            server_id=str(data.get("server_id") or ""),
            player_uuid=str(data.get("player_uuid") or ""),
            player_name=str(data.get("player_name") or ""),
            reason=str(data.get("reason") or ""),
        )


@dataclass(frozen=True)
class PlanVersion:
    """一次规划/重规划的快照（追加式：旧版本永不覆盖）。"""

    version: int
    plan_hash: str
    steps: tuple[dict[str, Any], ...] = ()
    reason: str = ""
    created_at: float = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": int(self.version),
            "plan_hash": str(self.plan_hash),
            "steps": [dict(item) for item in self.steps],
            "reason": str(self.reason),
            "created_at": float(self.created_at),
        }


@dataclass(frozen=True)
class AgentPlan:
    """一份"准备怎样做"（§二）。**它不携带任何执行状态**（修订 3）。"""

    plan_id: str
    source: str
    objective: str
    status: str = PlanStatus.PLANNING.value
    #: 关联（全是**引用**：proposal 在 7C、task 在 5A，这里只存 id）
    proposal_id: str = ""
    intent_id: str = ""
    task_id: str = ""
    initiator: str = ""
    #: LIFE 批准者（修订 1：批准后由**这个人**去建任务，第二道门天然校验同一人）
    approver_user_id: str = ""
    approver_session_id: str = ""
    approved_at: float = 0.0
    target: PlanTarget = field(default_factory=PlanTarget)
    #: 冻结计划（TaskPlan.to_payload 的形状；执行交给 TaskRuntime）
    plan: dict[str, Any] = field(default_factory=dict)
    plan_hash: str = ""
    version: int = 0
    history: tuple[PlanVersion, ...] = ()
    #: 规划期检查结论（§九：输入、结构化决策、检查结果 —— 不含模型思维链）
    checks: tuple[dict[str, Any], ...] = ()
    risk_summary: dict[str, Any] = field(default_factory=dict)
    reason: str = ""
    replans: int = 0
    replan_budget: int = DEFAULT_REPLAN_BUDGET
    replan_reason: str = ""
    fingerprint: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    updated_at: float = 0.0

    @property
    def terminal(self) -> bool:
        return PlanStatus(self.status).terminal

    @property
    def open(self) -> bool:
        return PlanStatus(self.status).open

    @property
    def needs_approval(self) -> bool:
        return self.status == PlanStatus.READY_FOR_APPROVAL.value

    def expired_at(self, now: float) -> bool:
        return bool(self.expires_at) and float(now) >= float(self.expires_at)

    def with_status(self, status: PlanStatus, *, reason: str = "") -> AgentPlan:
        if not plan_transition_allowed(PlanStatus(self.status), status):
            raise ValueError(f"illegal plan transition: {self.status} -> {status.value}")
        payload = self.to_payload()
        payload["status"] = status.value
        payload["reason"] = str(reason or self.reason)
        return AgentPlan.from_payload(payload)

    def to_payload(self) -> dict[str, Any]:
        return {
            "plan_id": str(self.plan_id),
            "source": _text(self.source),
            "objective": str(self.objective),
            "status": _text(self.status),
            "proposal_id": str(self.proposal_id),
            "intent_id": str(self.intent_id),
            "task_id": str(self.task_id),
            "initiator": str(self.initiator),
            "approver_user_id": str(self.approver_user_id),
            "approver_session_id": str(self.approver_session_id),
            "approved_at": float(self.approved_at),
            "target": self.target.to_payload(),
            "plan": dict(self.plan),
            "plan_hash": str(self.plan_hash),
            "version": int(self.version),
            "history": [item.to_payload() for item in self.history],
            "checks": [dict(item) for item in self.checks],
            "risk_summary": dict(self.risk_summary),
            "reason": str(self.reason),
            "replans": int(self.replans),
            "replan_budget": int(self.replan_budget),
            "replan_reason": str(self.replan_reason),
            "fingerprint": str(self.fingerprint),
            "created_at": float(self.created_at),
            "expires_at": float(self.expires_at),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> AgentPlan:
        history = payload.get("history") or ()
        checks = payload.get("checks") or ()
        return cls(
            plan_id=str(payload.get("plan_id") or ""),
            source=_text(_enum_or(PlanSource, payload.get("source"), PlanSource.SYSTEM)),
            objective=str(payload.get("objective") or ""),
            status=_text(_enum_or(PlanStatus, payload.get("status"), PlanStatus.PLANNING)),
            proposal_id=str(payload.get("proposal_id") or ""),
            intent_id=str(payload.get("intent_id") or ""),
            task_id=str(payload.get("task_id") or ""),
            initiator=str(payload.get("initiator") or ""),
            approver_user_id=str(payload.get("approver_user_id") or ""),
            approver_session_id=str(payload.get("approver_session_id") or ""),
            approved_at=float(payload.get("approved_at") or 0.0),
            target=PlanTarget.from_payload(payload.get("target")),
            plan=dict(payload.get("plan") or {}),
            plan_hash=str(payload.get("plan_hash") or ""),
            version=int(payload.get("version") or 0),
            history=tuple(
                PlanVersion(
                    version=int(item.get("version") or 0),
                    plan_hash=str(item.get("plan_hash") or ""),
                    steps=tuple(dict(s) for s in (item.get("steps") or ())),
                    reason=str(item.get("reason") or ""),
                    created_at=float(item.get("created_at") or 0.0),
                )
                for item in history
                if isinstance(item, Mapping)
            ),
            checks=tuple(dict(item) for item in checks if isinstance(item, Mapping)),
            risk_summary=dict(payload.get("risk_summary") or {}),
            reason=str(payload.get("reason") or ""),
            replans=int(payload.get("replans") or 0),
            replan_budget=int(payload.get("replan_budget") or DEFAULT_REPLAN_BUDGET),
            replan_reason=str(payload.get("replan_reason") or ""),
            fingerprint=str(payload.get("fingerprint") or ""),
            created_at=float(payload.get("created_at") or 0.0),
            expires_at=float(payload.get("expires_at") or 0.0),
            updated_at=float(payload.get("updated_at") or 0.0),
        )


def _enum_or(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(str(value))
    except (TypeError, ValueError):
        return default


def _text(value: Any) -> str:
    """枚举 → ``.value``（``str(枚举)`` 会给出 "PlanStatus.X"，回读会静默退默认 —— 7C 同坑）。"""
    return str(getattr(value, "value", value))
