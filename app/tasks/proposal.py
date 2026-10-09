"""Phase 7C §三-§九：**TaskProposal** —— 从"为什么想做"到"准备怎样做"的提案。

四件事必须分清（§五）：

```text
LifeIntent   = 为什么想做        （7A）
TaskProposal = 准备怎样做        （本模块；**不是**任务）
Task         = 已进入执行系统的任务（5A，本阶段 LIFE 提案**绝不**创建它）
Action       = 实际发生的世界操作  （4B+，本阶段执行层仍是 NONE）
```

三条硬性质：

* **提案不是权限**（§一/§九）：`READY_FOR_FUTURE_EXECUTION` 只代表通过了当前的**静态**检查，
  它不会创建任务、不会调用工具、不会改世界，也不会比 USER 来源多任何权限；
* **能力缺口必须如实**（§六/§七）：不知道就是 `UNKNOWN`，绝不猜成 `SUPPORTED`；
  有工具 ≠ 目标可完成（"刷铁机"这类需要方案的，一律 `NEEDS_MORE_INFORMATION`）；
* **来源隔离**（§四）：`USER` / `LIFE` / `SYSTEM` 三种来源共用同一套 schema 与检查，
  但授权规则不同 —— LIFE 只能停在提案。
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.tasks.capabilities import (
    Capability,
    capability_catalog,
    needs_design_information,
    required_capabilities,
)

# ---------------------------------------------------------------- 枚举


class ProposalSource(str, Enum):  # noqa: UP042
    """提案来源（§三）。``LIFE`` = 自主意图提出的候选任务，**本身不授予额外权限**。"""

    USER = "USER"
    LIFE = "LIFE"
    SYSTEM = "SYSTEM"


class ProposalStatus(str, Enum):  # noqa: UP042
    """提案状态（§一）。**没有** RUNNING/EXECUTING —— 这一阶段执行层是 NONE。"""

    REJECTED = "REJECTED"
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
    NEEDS_USER_APPROVAL = "NEEDS_USER_APPROVAL"
    READY_FOR_FUTURE_EXECUTION = "READY_FOR_FUTURE_EXECUTION"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_PROPOSAL_STATUSES

    @property
    def open(self) -> bool:
        return self in OPEN_PROPOSAL_STATUSES


#: 终态：到了这里就结束了，**重启恢复也不许覆盖**（§十）
TERMINAL_PROPOSAL_STATUSES = frozenset(
    {ProposalStatus.REJECTED, ProposalStatus.EXPIRED, ProposalStatus.CANCELLED}
)
#: 还开着的状态（可以继续被评估 / 推进）
OPEN_PROPOSAL_STATUSES = frozenset(
    {
        ProposalStatus.NEEDS_MORE_INFORMATION,
        ProposalStatus.NEEDS_USER_APPROVAL,
        ProposalStatus.READY_FOR_FUTURE_EXECUTION,
    }
)

#: 允许的状态转移（§十：终态没有出口）
ALLOWED_PROPOSAL_TRANSITIONS: dict[ProposalStatus, frozenset[ProposalStatus]] = {
    ProposalStatus.NEEDS_MORE_INFORMATION: frozenset(
        {
            ProposalStatus.NEEDS_USER_APPROVAL,
            ProposalStatus.READY_FOR_FUTURE_EXECUTION,
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.CANCELLED,
        }
    ),
    ProposalStatus.NEEDS_USER_APPROVAL: frozenset(
        {
            ProposalStatus.READY_FOR_FUTURE_EXECUTION,
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.CANCELLED,
        }
    ),
    ProposalStatus.READY_FOR_FUTURE_EXECUTION: frozenset(
        {
            ProposalStatus.REJECTED,
            ProposalStatus.EXPIRED,
            ProposalStatus.CANCELLED,
            ProposalStatus.NEEDS_USER_APPROVAL,
        }
    ),
    ProposalStatus.REJECTED: frozenset(),
    ProposalStatus.EXPIRED: frozenset(),
    ProposalStatus.CANCELLED: frozenset(),
}


def proposal_transition_allowed(current: ProposalStatus, target: ProposalStatus) -> bool:
    return target in ALLOWED_PROPOSAL_TRANSITIONS.get(current, frozenset())


class CapabilityGap(str, Enum):  # noqa: UP042
    """能力缺口（§七）。**不得**靠猜把 ``UNKNOWN`` 升级成 ``SUPPORTED``。"""

    SUPPORTED = "SUPPORTED"
    PARTIALLY_SUPPORTED = "PARTIALLY_SUPPORTED"
    UNSUPPORTED = "UNSUPPORTED"
    UNKNOWN = "UNKNOWN"


class GapSuggestion(str, Enum):  # noqa: UP042
    """能力不足时给出的**后续工作建议**（§七）—— 只是建议，不许自动写/装/执行任何工具。"""

    NEEDS_NEW_SKILL = "NEEDS_NEW_SKILL"
    NEEDS_NEW_TOOL = "NEEDS_NEW_TOOL"
    NEEDS_WORLD_OBSERVATION = "NEEDS_WORLD_OBSERVATION"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"


#: 身份解析状态（§八；复用 5C 的 VERIFIED/REVOKED/CONFLICT 词表）
IDENTITY_VERIFIED = "VERIFIED"
IDENTITY_REVOKED = "REVOKED"
IDENTITY_CONFLICT = "CONFLICT"
IDENTITY_MISSING = "MISSING"

#: 需要"设计/方案"的占位能力（不是工具：它代表"这件事得先有方案"）
DESIGN_CAPABILITY_ID = "design_plan"

#: 提案的存活时间（§十：复用 7A 的 TTL 思路，不无限挂着）
DEFAULT_PROPOSAL_TTL_SECONDS = 6 * 3600.0
#: 去重桶（相同来源+目标在同一个桶里只提一次）
PROPOSAL_BUCKET_SECONDS = 3600.0


# ---------------------------------------------------------------- 目标解析


@dataclass(frozen=True)
class TargetResolution:
    """提案的**目标**（§八）：必须是可信身份桥解析出来的，绝不猜。"""

    status: str = IDENTITY_MISSING
    server_id: str = ""
    player_uuid: str = ""
    player_name: str = ""
    source: str = ""
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
            "source": str(self.source),
            "reason": str(self.reason),
        }


#: 不需要指定玩家的目标（例如"去收点橡木"）
NO_TARGET = TargetResolution(status=IDENTITY_VERIFIED, reason="no_player_target")


# ---------------------------------------------------------------- 评估结果


@dataclass(frozen=True)
class CapabilityRequirement:
    """一条能力要求 + 它的缺口判定（§六/§七）。"""

    capability_id: str
    gap: str = CapabilityGap.UNKNOWN.value
    risk_class: str = ""
    available: bool = False
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "capability_id": str(self.capability_id),
            "gap": str(self.gap),
            "risk_class": str(self.risk_class),
            "available": bool(self.available),
            "reason": str(self.reason),
        }


@dataclass(frozen=True)
class ProposalAssessment:
    """静态检查结论（§一/§六-§九）—— 只描述，不授权。"""

    status: str
    feasibility: str
    requirements: tuple[CapabilityRequirement, ...] = ()
    suggestions: tuple[str, ...] = ()
    expected_effects: tuple[str, ...] = ()
    risk_summary: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "status": str(self.status),
            "feasibility": str(self.feasibility),
            "requirements": [item.to_payload() for item in self.requirements],
            "suggestions": [str(item) for item in self.suggestions],
            "expected_effects": [str(item) for item in self.expected_effects],
            "risk_summary": dict(self.risk_summary),
            "reason": str(self.reason),
        }


# ---------------------------------------------------------------- 提案本体


@dataclass(frozen=True)
class TaskProposal:
    """一份"准备怎样做"（§三）。**它永远不会自己变成 Task**（§五）。"""

    proposal_id: str
    source: str
    objective: str
    status: str = ProposalStatus.NEEDS_MORE_INFORMATION.value
    intent_id: str = ""
    initiator: str = ""
    target: dict[str, Any] = field(default_factory=dict)
    required_capabilities: tuple[str, ...] = ()
    requirements: tuple[CapabilityRequirement, ...] = ()
    expected_effects: tuple[str, ...] = ()
    risk_summary: dict[str, Any] = field(default_factory=dict)
    feasibility: str = CapabilityGap.UNKNOWN.value
    suggestions: tuple[str, ...] = ()
    reason: str = ""
    fingerprint: str = ""
    created_at: float = 0.0
    expires_at: float = 0.0
    updated_at: float = 0.0

    @property
    def terminal(self) -> bool:
        return ProposalStatus(self.status).terminal

    @property
    def open(self) -> bool:
        return ProposalStatus(self.status).open

    def expired_at(self, now: float) -> bool:
        return bool(self.expires_at) and float(now) >= float(self.expires_at)

    def with_assessment(self, assessment: ProposalAssessment) -> TaskProposal:
        """把一次静态检查的结果盖上去（**不**改来源/目标/时间）。"""
        payload = self.to_payload()
        payload.update(assessment.to_payload())
        payload["updated_at"] = float(payload.get("updated_at") or 0.0)
        return TaskProposal.from_payload(payload)

    def to_payload(self) -> dict[str, Any]:
        return {
            "proposal_id": str(self.proposal_id),
            "source": _text(self.source),
            "objective": str(self.objective),
            "status": _text(self.status),
            "intent_id": str(self.intent_id),
            "initiator": str(self.initiator),
            "target": dict(self.target),
            "required_capabilities": [str(item) for item in self.required_capabilities],
            "requirements": [item.to_payload() for item in self.requirements],
            "expected_effects": [str(item) for item in self.expected_effects],
            "risk_summary": dict(self.risk_summary),
            "feasibility": str(self.feasibility),
            "suggestions": [str(item) for item in self.suggestions],
            "reason": str(self.reason),
            "fingerprint": str(self.fingerprint),
            "created_at": float(self.created_at),
            "expires_at": float(self.expires_at),
            "updated_at": float(self.updated_at),
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskProposal:
        """从 JSON/DB 行还原（坏数据不猜：未知枚举退回安全默认）。"""
        requirements = payload.get("requirements") or ()
        return cls(
            proposal_id=str(payload.get("proposal_id") or ""),
            source=_text(_enum_or(ProposalSource, payload.get("source"), ProposalSource.SYSTEM)),
            objective=str(payload.get("objective") or ""),
            status=_text(
                _enum_or(
                    ProposalStatus, payload.get("status"), ProposalStatus.NEEDS_MORE_INFORMATION
                )
            ),
            intent_id=str(payload.get("intent_id") or ""),
            initiator=str(payload.get("initiator") or ""),
            target=dict(payload.get("target") or {}),
            required_capabilities=tuple(
                str(item) for item in (payload.get("required_capabilities") or ())
            ),
            requirements=tuple(
                CapabilityRequirement(
                    capability_id=str(item.get("capability_id") or ""),
                    gap=str(item.get("gap") or CapabilityGap.UNKNOWN.value),
                    risk_class=str(item.get("risk_class") or ""),
                    available=bool(item.get("available")),
                    reason=str(item.get("reason") or ""),
                )
                for item in requirements
                if isinstance(item, dict)
            ),
            expected_effects=tuple(str(item) for item in (payload.get("expected_effects") or ())),
            risk_summary=dict(payload.get("risk_summary") or {}),
            feasibility=str(payload.get("feasibility") or CapabilityGap.UNKNOWN.value),
            suggestions=tuple(str(item) for item in (payload.get("suggestions") or ())),
            reason=str(payload.get("reason") or ""),
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
    """枚举 → 它的 ``value``，其它 → ``str``。

    ★ 必须走 ``.value``：这些枚举是 ``(str, Enum)``，``str(ProposalStatus.X)`` 会给出
    ``"ProposalStatus.X"``，那样写进 JSON 之后 ``from_payload`` 会解析失败并**静默退回默认值**
    （来源退回 SYSTEM、状态退回 NEEDS_MORE_INFORMATION）—— 7A 踩过同一个坑。
    """
    return str(getattr(value, "value", value))


# ---------------------------------------------------------------- 指纹（§十）


def proposal_fingerprint(
    *,
    source: ProposalSource | str,
    objective: str,
    target_key: str = "",
    intent_id: str = "",
    bucket: int = 0,
) -> str:
    """确定性指纹（§十：复用 7A 的"来源+语义键+时间桶"思路，不建第二套计时器）。"""
    kind = _text(source)
    parts = (
        kind,
        " ".join(str(objective or "").split()).lower()[:120],
        str(target_key or "").strip().lower(),
        str(intent_id or "").strip().lower(),
        str(int(bucket)),
    )
    return "|".join(parts)


def proposal_time_bucket(now: float, *, bucket_seconds: float = PROPOSAL_BUCKET_SECONDS) -> int:
    return int(float(now) // max(1.0, float(bucket_seconds)))


# ---------------------------------------------------------------- 静态检查


def assess_proposal(
    *,
    objective: str,
    source: ProposalSource | str,
    target: TargetResolution = NO_TARGET,
    catalog: Mapping[str, Capability] | None = None,
    require_target: bool = False,
) -> ProposalAssessment:
    """跑一遍**静态**检查（§六-§九）：能力 → 缺口 → 可行性 → 状态。

    它只读能力目录、目标解析和既有风险表；**不**执行、**不**写盘、**不**碰世界。
    """
    book = dict(catalog or capability_catalog())
    wanted = list(required_capabilities(objective))
    design = needs_design_information(objective)
    if require_target:
        identity = check_target(target)
        if identity is not None:
            return identity
    requirements: list[CapabilityRequirement] = []
    for capability_id in wanted:
        capability = book.get(capability_id)
        if capability is None:
            requirements.append(
                CapabilityRequirement(
                    capability_id=capability_id,
                    gap=CapabilityGap.UNSUPPORTED.value,
                    reason="not_registered",
                )
            )
            continue
        if capability.available:
            requirements.append(
                CapabilityRequirement(
                    capability_id=capability_id,
                    gap=CapabilityGap.SUPPORTED.value,
                    risk_class=capability.risk_class,
                    available=True,
                )
            )
            continue
        requirements.append(
            CapabilityRequirement(
                capability_id=capability_id,
                gap=CapabilityGap.PARTIALLY_SUPPORTED.value,
                risk_class=capability.risk_class,
                available=False,
                reason="unavailable_now",
            )
        )
    if design:
        requirements.append(
            CapabilityRequirement(
                capability_id=DESIGN_CAPABILITY_ID,
                gap=CapabilityGap.UNKNOWN.value,
                reason="needs_design",
            )
        )
    if not wanted and not design:
        # 目标太模糊：连"要什么能力"都说不清 → 如实记为 UNKNOWN（绝不假装可执行）
        requirements.append(
            CapabilityRequirement(
                capability_id="",
                gap=CapabilityGap.UNKNOWN.value,
                reason="objective_too_vague",
            )
        )
    return _decide(requirements, book=book, source=source)


def check_target(target: TargetResolution) -> ProposalAssessment | None:
    """目标不合法时的**直接**结论（§八/§十四）。返回 ``None`` = 目标没问题。"""
    status = str(target.status)
    if status == IDENTITY_VERIFIED:
        return None
    if status in {IDENTITY_REVOKED, IDENTITY_CONFLICT}:
        return ProposalAssessment(
            status=ProposalStatus.REJECTED.value,
            feasibility=CapabilityGap.UNSUPPORTED.value,
            suggestions=(GapSuggestion.NEEDS_USER_INPUT.value,),
            reason=f"target_{status.lower()}",
        )
    return ProposalAssessment(
        status=ProposalStatus.NEEDS_MORE_INFORMATION.value,
        feasibility=CapabilityGap.UNKNOWN.value,
        suggestions=(GapSuggestion.NEEDS_USER_INPUT.value,),
        reason="target_unresolved",
    )


def _decide(
    requirements: Sequence[CapabilityRequirement],
    *,
    book: Mapping[str, Capability],
    source: ProposalSource | str,
) -> ProposalAssessment:
    """按 §一/§六/§七/§九 决定状态与可行性（确定性，单向保守）。"""
    gaps = {item.gap for item in requirements}
    suggestions: list[str] = []
    blocked = bool(gaps & {CapabilityGap.UNSUPPORTED.value, CapabilityGap.UNKNOWN.value})
    partial = CapabilityGap.PARTIALLY_SUPPORTED.value in gaps
    unknown = CapabilityGap.UNKNOWN.value in gaps
    if CapabilityGap.UNSUPPORTED.value in gaps:
        suggestions.append(GapSuggestion.NEEDS_NEW_TOOL.value)
    if partial:
        suggestions.append(GapSuggestion.NEEDS_WORLD_OBSERVATION.value)
    if unknown:
        suggestions.append(GapSuggestion.NEEDS_USER_INPUT.value)
    if blocked or partial:
        # 有任何缺口 → 一律"信息不足"，绝不假装提案已可执行（§六/§七）
        status = ProposalStatus.NEEDS_MORE_INFORMATION.value
    else:
        risks = {_risk_of(item, book) for item in requirements}
        status = (
            ProposalStatus.NEEDS_USER_APPROVAL.value
            if risks & {"MEDIUM", "HIGH", "DESTRUCTIVE"}
            else ProposalStatus.READY_FOR_FUTURE_EXECUTION.value
        )
    feasibility = (
        CapabilityGap.UNKNOWN.value
        if unknown
        else CapabilityGap.PARTIALLY_SUPPORTED.value
        if partial
        else CapabilityGap.UNSUPPORTED.value
        if blocked
        else CapabilityGap.SUPPORTED.value
    )
    effects: list[str] = []
    for item in requirements:
        effect = str(getattr(book.get(item.capability_id), "expected_effect", "") or "")
        if effect and effect not in effects:
            effects.append(effect)
    risks_all = sorted({_risk_of(item, book) for item in requirements if item.capability_id})
    risk_summary = {
        "classes": risks_all,
        "max_risk": _max_risk(risks_all),
        # 只是**描述**：未来真要执行时，这些动作仍然必须走既有确认链（§九）
        "would_require_confirmation": bool(set(risks_all) & {"MEDIUM", "HIGH", "DESTRUCTIVE"}),
        "source": str(getattr(source, "value", source)),
    }
    return ProposalAssessment(
        status=status,
        feasibility=feasibility,
        requirements=tuple(requirements),
        suggestions=tuple(dict.fromkeys(suggestions)),
        expected_effects=tuple(effects),
        risk_summary=risk_summary,
        reason="capability_gap" if (blocked or partial) else "capabilities_supported",
    )


def _risk_of(item: CapabilityRequirement, book: Mapping[str, Capability]) -> str:
    """这条要求的风险等级（没有登记的能力按 SAFE 记，绝不当成"更危险"）。"""
    if item.risk_class:
        return str(item.risk_class)
    capability = book.get(item.capability_id)
    return str(getattr(capability, "risk_class", "") or "SAFE")


_RISK_ORDER = ("SAFE", "LOW", "MEDIUM", "HIGH", "DESTRUCTIVE")


def _max_risk(classes: Sequence[str]) -> str:
    ranked = [item for item in _RISK_ORDER if item in set(classes)]
    return ranked[-1] if ranked else "SAFE"


def proposal_ttl_seconds(status: str) -> float:
    """不同状态的存活时间（§十）—— 终态不需要 TTL（不会再过期）。"""
    return DEFAULT_PROPOSAL_TTL_SECONDS


def build_proposal(
    *,
    source: ProposalSource | str,
    objective: str,
    intent_id: str = "",
    initiator: str = "",
    target: TargetResolution = NO_TARGET,
    require_target: bool = False,
    catalog: Mapping[str, Capability] | None = None,
    now: float | None = None,
    proposal_id: str = "",
    ttl_seconds: float | None = None,
) -> TaskProposal:
    """组装一份提案（**纯函数**：不落盘、不执行、不改世界）。"""
    moment = float(now if now is not None else time.time())
    book = dict(catalog or capability_catalog())
    assessment = assess_proposal(
        objective=objective,
        source=source,
        target=target,
        catalog=book,
        require_target=require_target,
    )
    ttl = float(ttl_seconds) if ttl_seconds is not None else proposal_ttl_seconds(assessment.status)
    payload = {
        "proposal_id": str(proposal_id),
        "source": _text(source),
        "objective": " ".join(str(objective or "").split())[:200],
        "intent_id": str(intent_id),
        "initiator": str(initiator),
        "target": target.to_payload(),
        "required_capabilities": [
            item.capability_id for item in assessment.requirements if item.capability_id
        ],
        "created_at": moment,
        "expires_at": moment + max(1.0, ttl),
        "updated_at": moment,
        "fingerprint": proposal_fingerprint(
            source=source,
            objective=objective,
            # 目标**状态**也要进指纹：解析不到 / 被撤销 / 已验证是三种不同的事实，
            # 不能因为都"没有 uuid"就被当成同一份提案合并掉（§八/§十）。
            target_key=f"{target.status}:{target.player_uuid or target.server_id or ''}",
            intent_id=intent_id,
            bucket=proposal_time_bucket(moment),
        ),
    }
    payload.update(assessment.to_payload())
    return TaskProposal.from_payload(payload)
