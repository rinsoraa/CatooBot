"""Phase 7E §7.1：程序性技能（Procedural Skill）的结构化模型。

这里只有**数据**与**显式规则**：没有执行句柄、没有工具调用、没有第二套任务状态机。
技能描述的是"一条被真实证据支持过的可复用方法"：

* 步骤模板只能用**当前已注册**的工具 + 合法参数（由既有 ``validate_plan`` 兜底）；
* 一次性执行值（坐标 / entity_id / 会话 / UUID）一律归一化成**槽位**，复用前必须重新解析；
* 前置条件与成功判据只允许"实现确实验证得了"的种类（封闭枚举，见 §7.4）；
* 生命周期只有五个状态、转移表显式、终态不可复活（§7.1）；
* 技能**没有**权限：它既不能降低风险，也不能绕过批准/确认（§7.5）。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.tasks.models import TaskState

#: 技能 id 前缀（与任务/计划/提案一样可读）
SKILL_ID_PREFIX = "SK"
#: 技能 schema 版本：结构变化时 +1，旧版本会被标记待重新验证（§7.3）
SKILL_SCHEMA_VERSION = 1
#: 幂等时间桶（秒）：同一方法在同一时间桶里只建一条候选
SKILL_BUCKET_SECONDS = 3600.0


class SkillStatus(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """技能生命周期（§7.1）。**没有** RUNNING / EXECUTING —— 技能不是任务。"""

    #: 第一次合格证据：记录了，但**不作为可复用技能**（§7.2：未验证候选绝不直接可用）
    CANDIDATE = "CANDIDATE"
    #: 独立第二条合格证据（或更强后置验证）后晋升：可以出现在计划候选里
    ACTIVE = "ACTIVE"
    #: 工具 schema / 环境 / 服务器不匹配，或复用后出现反例 → 需要重新验证
    STALE = "STALE"
    #: 可靠反例（连续失败）或结构不可再验证 → 失效（终态）
    INVALIDATED = "INVALIDATED"
    #: 证据不合格（含 dig 归因歧义）→ 拒绝晋升（终态）
    REJECTED = "REJECTED"


#: 终态：只进不出（与 Plan/Proposal/Task 同一纪律）
TERMINAL_SKILL_STATUSES = frozenset({SkillStatus.INVALIDATED, SkillStatus.REJECTED})
#: 还开着的状态
OPEN_SKILL_STATUSES = frozenset({SkillStatus.CANDIDATE, SkillStatus.ACTIVE, SkillStatus.STALE})

#: 允许的状态转移：终态没有出口；CANDIDATE 只能向上晋升或被拒/失效
ALLOWED_SKILL_TRANSITIONS: dict[SkillStatus, frozenset[SkillStatus]] = {
    SkillStatus.CANDIDATE: frozenset(
        {SkillStatus.ACTIVE, SkillStatus.STALE, SkillStatus.INVALIDATED, SkillStatus.REJECTED}
    ),
    SkillStatus.ACTIVE: frozenset({SkillStatus.STALE, SkillStatus.INVALIDATED}),
    #: STALE 是"待重新验证"：新的合格证据可以把方法带回 ACTIVE（同一 skill_id，证据不丢）
    SkillStatus.STALE: frozenset({SkillStatus.ACTIVE, SkillStatus.INVALIDATED}),
    SkillStatus.INVALIDATED: frozenset(),
    SkillStatus.REJECTED: frozenset(),
}


def skill_transition_allowed(current: SkillStatus, target: SkillStatus) -> bool:
    return target in ALLOWED_SKILL_TRANSITIONS.get(current, frozenset())


class Applicability(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """适用性评估结论（§7.4）。``UNKNOWN`` **永远不能**自动升级成适用。"""

    APPLICABLE = "APPLICABLE"
    INAPPLICABLE = "INAPPLICABLE"
    UNKNOWN = "UNKNOWN"
    STALE = "STALE"


#: 前置条件种类（封闭集合）：每一种都必须能用既有 SAFE 观察真的验出来
PRECONDITION_KINDS = (
    #: 目标位置上是声明的方块（minecraft_dig_capability；空气/读不到 → 不满足/未知）
    "block_present",
    #: 背包里有某物品（minecraft_inventory）
    "inventory_has",
    #: 声明的玩家在线且可见（minecraft_world 的 players）
    "player_online",
)

#: 成功判据种类（封闭集合）：区分"工具调用成功"与"目标已实现"
SUCCESS_KINDS = (
    #: 运行时的**独立**后置验证：新鲜 SAFE 背包读的 inventory_delta（record.verification）
    "inventory_delta",
    #: 目标位置已不是声明的方块（新鲜 SAFE 读；学习期复核）
    "block_absent",
    #: 声明的玩家在指定距离内（新鲜 SAFE 读）
    "player_near",
)

#: 槽位种类（封闭集合）：复用前必须重新解析的动态参数
SLOT_KINDS = ("position", "block_name", "item", "player", "entity")


#: 学习证据结论
class EvidenceVerdict(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    #: 满足全部资格：可以作为正向证据（第一次→CANDIDATE，独立第二次→ACTIVE）
    POSITIVE = "POSITIVE"
    #: 证据不足 / 无法归因（含 dig 时长歧义）→ 保留来源，不计正向
    AMBIGUOUS = "AMBIGUOUS"
    #: 明确不合格（非成功终态、非最终计划版本、步骤未成功…）
    REJECTED = "REJECTED"
    #: 复用该技能的任务出现了可靠失败 → 反例
    COUNTEREXAMPLE = "COUNTEREXAMPLE"


@dataclass(frozen=True)
class SkillSlot:
    """一个动态参数槽位（§7.3）：一次性执行值必须落在这里，不能写死在步骤里。"""

    name: str
    kind: str = "position"
    description: str = ""
    required: bool = True


@dataclass(frozen=True)
class SkillStep:
    """一步可复用的方法（模板）：工具必须已注册，参数里只允许槽位与既有引用。"""

    step_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: str = "SAFE"
    depends_on: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkillPrecondition:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillSuccessCriteria:
    kind: str
    params: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SkillEvidence:
    """一条学习/复用证据（幂等：同一 task+plan 版本只算一次）。"""

    evidence_id: str
    task_id: str
    verdict: str
    reason_code: str
    skill_id: str = ""
    subject_key: str = ""
    plan_version: int = 0
    plan_hash: str = ""
    step_ids: tuple[str, ...] = ()
    outcome: str = ""
    postcondition_kind: str = ""
    postcondition_ok: bool = False
    detail: dict[str, Any] = field(default_factory=dict)
    at: float = 0.0

    def to_payload(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "skill_id": self.skill_id,
            "subject_key": self.subject_key,
            "task_id": self.task_id,
            "plan_version": int(self.plan_version),
            "plan_hash": self.plan_hash,
            "step_ids": list(self.step_ids),
            "outcome": self.outcome,
            "verdict": self.verdict,
            "reason_code": self.reason_code,
            "postcondition_kind": self.postcondition_kind,
            "postcondition_ok": bool(self.postcondition_ok),
            "detail": dict(self.detail),
            "at": float(self.at),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> SkillEvidence:
        return cls(
            evidence_id=str(payload.get("evidence_id") or ""),
            skill_id=str(payload.get("skill_id") or ""),
            subject_key=str(payload.get("subject_key") or ""),
            task_id=str(payload.get("task_id") or ""),
            plan_version=int(payload.get("plan_version") or 0),
            plan_hash=str(payload.get("plan_hash") or ""),
            step_ids=tuple(str(item) for item in (payload.get("step_ids") or [])),
            outcome=str(payload.get("outcome") or ""),
            verdict=str(payload.get("verdict") or ""),
            reason_code=str(payload.get("reason_code") or ""),
            postcondition_kind=str(payload.get("postcondition_kind") or ""),
            postcondition_ok=bool(payload.get("postcondition_ok")),
            detail=dict(payload.get("detail") or {}),
            at=float(payload.get("at") or 0.0),
        )


def skill_id_for(day: str, sequence: int) -> str:
    return f"{SKILL_ID_PREFIX}-{str(day).replace('-', '')}-{int(sequence):03d}"


def day_text(now: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now)))


def skill_time_bucket(now: float, *, bucket_seconds: float = SKILL_BUCKET_SECONDS) -> int:
    return int(float(now) // max(1.0, float(bucket_seconds)))


def normalized_steps(steps: tuple[SkillStep, ...] | list[SkillStep]) -> list[dict[str, Any]]:
    """归一化步骤序列（供指纹用）：工具 + 参数键序稳定 + 引用/槽位形状。"""

    out: list[dict[str, Any]] = []
    for step in steps:
        out.append(
            {
                "tool": str(step.tool),
                "risk": str(step.risk),
                "arguments": _canonical(step.arguments),
                "depends_on": list(step.depends_on),
            }
        )
    return out


def _canonical(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _canonical(value[key]) for key in sorted(value, key=str)}
    if isinstance(value, (list, tuple)):
        return [_canonical(item) for item in value]
    return value


def skill_fingerprint(
    *,
    character_id: str,
    server_id: str,
    tools_signature: str,
    objective_pattern: str,
    steps: tuple[SkillStep, ...] | list[SkillStep],
) -> str:
    """确定性方法指纹（§7.3）：同角色 + 同服务器 + 同工具契约 + 同归一化步骤 = 同一方法。"""

    blob = json.dumps(
        {
            "character_id": str(character_id),
            "server_id": str(server_id),
            "tools_signature": str(tools_signature),
            "objective_pattern": " ".join(str(objective_pattern or "").split()).lower()[:120],
            "steps": normalized_steps(steps),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def subject_key_for(*, character_id: str, server_id: str, objective_pattern: str) -> str:
    """学习账本的"方法主题"键：即使没晋升成技能，也能按主题聚合拒绝/歧义记录（§7.2 可解释）。"""

    blob = json.dumps(
        {
            "character_id": str(character_id),
            "server_id": str(server_id),
            "objective_pattern": " ".join(str(objective_pattern or "").split()).lower()[:120],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


@dataclass
class ProceduralSkill:
    """一条技能记录（§7.1 的最小 schema）。"""

    skill_id: str = ""
    schema_version: int = SKILL_SCHEMA_VERSION
    name: str = ""
    objective_pattern: str = ""
    summary: str = ""
    status: str = SkillStatus.CANDIDATE.value
    character_id: str = ""
    server_id: str = ""
    environment: str = ""
    tools_signature: str = ""
    slots: tuple[SkillSlot, ...] = ()
    steps: tuple[SkillStep, ...] = ()
    preconditions: tuple[SkillPrecondition, ...] = ()
    success_criteria: tuple[SkillSuccessCriteria, ...] = ()
    required_capabilities: tuple[str, ...] = ()
    risk_summary: dict[str, Any] = field(default_factory=dict)
    max_risk: str = "SAFE"
    version: int = 1
    supersedes_skill_id: str = ""
    superseded_by: str = ""
    fingerprint: str = ""
    success_count: int = 0
    failure_count: int = 0
    ambiguous_count: int = 0
    created_at: float = 0.0
    updated_at: float = 0.0
    last_learned_at: float = 0.0
    last_verified_at: float = 0.0
    last_used_at: float = 0.0
    reason: str = ""
    #: 自由审计字段（放来源证据 id、拒绝原因等；**不放**整包 Task payload / 世界快照）
    extra: dict[str, Any] = field(default_factory=dict)

    # ------------------------------------------------------------ 只读性质

    @property
    def status_enum(self) -> SkillStatus:
        return SkillStatus(self.status)

    @property
    def terminal(self) -> bool:
        return self.status_enum in TERMINAL_SKILL_STATUSES

    @property
    def open(self) -> bool:
        return self.status_enum in OPEN_SKILL_STATUSES

    @property
    def reusable(self) -> bool:
        """只有 ``ACTIVE`` 才可能进入计划候选（§7.2：候选不等于可用）。"""

        return self.status_enum is SkillStatus.ACTIVE

    @property
    def step_ids(self) -> tuple[str, ...]:
        return tuple(step.step_id for step in self.steps)

    def slot(self, name: str) -> SkillSlot | None:
        return next((item for item in self.slots if item.name == name), None)

    def evidence_ok(self) -> bool:
        """证据是否足以支持"罐头掌握了这条方法"（晋升门槛，§7.2 两段式）。"""

        return self.success_count >= 1 and self.ambiguous_count == 0

    # ------------------------------------------------------------ 序列化

    def to_payload(self) -> dict[str, Any]:
        return {
            "skill_id": self.skill_id,
            "schema_version": int(self.schema_version),
            "name": self.name,
            "objective_pattern": self.objective_pattern,
            "summary": self.summary,
            "status": str(self.status),
            "character_id": self.character_id,
            "server_id": self.server_id,
            "environment": self.environment,
            "tools_signature": self.tools_signature,
            "slots": [
                {
                    "name": item.name,
                    "kind": item.kind,
                    "description": item.description,
                    "required": bool(item.required),
                }
                for item in self.slots
            ],
            "steps": [
                {
                    "step_id": step.step_id,
                    "tool": step.tool,
                    "arguments": dict(step.arguments),
                    "risk": step.risk,
                    "depends_on": list(step.depends_on),
                }
                for step in self.steps
            ],
            "preconditions": [
                {"kind": item.kind, "params": dict(item.params)} for item in self.preconditions
            ],
            "success_criteria": [
                {"kind": item.kind, "params": dict(item.params)} for item in self.success_criteria
            ],
            "required_capabilities": list(self.required_capabilities),
            "risk_summary": dict(self.risk_summary),
            "max_risk": self.max_risk,
            "version": int(self.version),
            "supersedes_skill_id": self.supersedes_skill_id,
            "superseded_by": self.superseded_by,
            "fingerprint": self.fingerprint,
            "success_count": int(self.success_count),
            "failure_count": int(self.failure_count),
            "ambiguous_count": int(self.ambiguous_count),
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
            "last_learned_at": float(self.last_learned_at),
            "last_verified_at": float(self.last_verified_at),
            "last_used_at": float(self.last_used_at),
            "reason": self.reason,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ProceduralSkill:
        return cls(
            skill_id=str(payload.get("skill_id") or ""),
            schema_version=int(payload.get("schema_version") or SKILL_SCHEMA_VERSION),
            name=str(payload.get("name") or ""),
            objective_pattern=str(payload.get("objective_pattern") or ""),
            summary=str(payload.get("summary") or ""),
            status=str(payload.get("status") or SkillStatus.CANDIDATE.value),
            character_id=str(payload.get("character_id") or ""),
            server_id=str(payload.get("server_id") or ""),
            environment=str(payload.get("environment") or ""),
            tools_signature=str(payload.get("tools_signature") or ""),
            slots=tuple(
                SkillSlot(
                    name=str(item.get("name") or ""),
                    kind=str(item.get("kind") or "position"),
                    description=str(item.get("description") or ""),
                    required=bool(item.get("required", True)),
                )
                for item in (payload.get("slots") or [])
                if isinstance(item, dict)
            ),
            steps=tuple(
                SkillStep(
                    step_id=str(item.get("step_id") or ""),
                    tool=str(item.get("tool") or ""),
                    arguments=dict(item.get("arguments") or {}),
                    risk=str(item.get("risk") or "SAFE"),
                    depends_on=tuple(str(entry) for entry in (item.get("depends_on") or [])),
                )
                for item in (payload.get("steps") or [])
                if isinstance(item, dict)
            ),
            preconditions=tuple(
                SkillPrecondition(
                    kind=str(item.get("kind") or ""), params=dict(item.get("params") or {})
                )
                for item in (payload.get("preconditions") or [])
                if isinstance(item, dict)
            ),
            success_criteria=tuple(
                SkillSuccessCriteria(
                    kind=str(item.get("kind") or ""), params=dict(item.get("params") or {})
                )
                for item in (payload.get("success_criteria") or [])
                if isinstance(item, dict)
            ),
            required_capabilities=tuple(
                str(item) for item in (payload.get("required_capabilities") or [])
            ),
            risk_summary=dict(payload.get("risk_summary") or {}),
            max_risk=str(payload.get("max_risk") or "SAFE"),
            version=int(payload.get("version") or 1),
            supersedes_skill_id=str(payload.get("supersedes_skill_id") or ""),
            superseded_by=str(payload.get("superseded_by") or ""),
            fingerprint=str(payload.get("fingerprint") or ""),
            success_count=int(payload.get("success_count") or 0),
            failure_count=int(payload.get("failure_count") or 0),
            ambiguous_count=int(payload.get("ambiguous_count") or 0),
            created_at=float(payload.get("created_at") or 0.0),
            updated_at=float(payload.get("updated_at") or 0.0),
            last_learned_at=float(payload.get("last_learned_at") or 0.0),
            last_verified_at=float(payload.get("last_verified_at") or 0.0),
            last_used_at=float(payload.get("last_used_at") or 0.0),
            reason=str(payload.get("reason") or ""),
            extra=dict(payload.get("extra") or {}),
        )


def task_state_enum(value: Any) -> TaskState | None:
    """把 TaskRecord/步骤的状态（枚举或字符串）读成 TaskState；读不出来返回 None。"""

    raw = getattr(value, "value", value)
    try:
        return TaskState(str(raw))
    except ValueError:
        return None


__all__ = [
    "ALLOWED_SKILL_TRANSITIONS",
    "Applicability",
    "EvidenceVerdict",
    "OPEN_SKILL_STATUSES",
    "PRECONDITION_KINDS",
    "ProceduralSkill",
    "SKILL_ID_PREFIX",
    "SKILL_SCHEMA_VERSION",
    "SLOT_KINDS",
    "SUCCESS_KINDS",
    "SkillEvidence",
    "SkillPrecondition",
    "SkillSlot",
    "SkillStatus",
    "SkillStep",
    "SkillSuccessCriteria",
    "TERMINAL_SKILL_STATUSES",
    "day_text",
    "normalized_steps",
    "skill_fingerprint",
    "skill_id_for",
    "skill_time_bucket",
    "skill_transition_allowed",
    "subject_key_for",
    "task_state_enum",
]
