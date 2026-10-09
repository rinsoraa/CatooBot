"""Phase 7C §十二：TaskProposal 事件。

五个（**没有** ``proposal.executed`` —— 本阶段执行层是 ``NONE``）：

```text
proposal.created / proposal.deduplicated / proposal.rejected
proposal.expired / proposal.cancelled
```

与 7A 同一纪律（§四十八 的精神）：事件只是 **state update** —— 发出去之后谁都不许
因此调模型、发 QQ 或碰世界。载荷只够审计与只读展示，**不含**任何隐藏推理。
"""

from __future__ import annotations

from typing import Any

from app.tasks.proposal import ProposalStatus, TaskProposal

PROPOSAL_CREATED = "proposal.created"
PROPOSAL_DEDUPLICATED = "proposal.deduplicated"
PROPOSAL_REJECTED = "proposal.rejected"
PROPOSAL_EXPIRED = "proposal.expired"
PROPOSAL_CANCELLED = "proposal.cancelled"

PROPOSAL_EVENT_NAMES = (
    PROPOSAL_CREATED,
    PROPOSAL_DEDUPLICATED,
    PROPOSAL_REJECTED,
    PROPOSAL_EXPIRED,
    PROPOSAL_CANCELLED,
)


def capability_gaps(proposal: TaskProposal) -> list[str]:
    """还没被满足的能力（给审计/展示用；``SUPPORTED`` 不算缺口）。"""
    return [
        item.capability_id
        for item in proposal.requirements
        if item.capability_id and str(item.gap) != "SUPPORTED"
    ]


def proposal_payload(
    proposal: TaskProposal, *, timestamp: float, reason: str = ""
) -> dict[str, Any]:
    """最小载荷：审计 + 只读展示需要什么就给什么。"""
    target = dict(proposal.target or {})
    return {
        "proposal_id": proposal.proposal_id,
        "source": str(proposal.source),
        "status": str(proposal.status),
        "objective": str(proposal.objective),
        "intent_id": str(proposal.intent_id),
        "initiator": str(proposal.initiator),
        "feasibility": str(proposal.feasibility),
        "target_status": str(target.get("status") or ""),
        "capability_gaps": capability_gaps(proposal),
        "max_risk": str((proposal.risk_summary or {}).get("max_risk") or ""),
        "timestamp": float(timestamp),
        "reason": str(reason or proposal.reason or ""),
    }


class ProposalEventPublisher:
    """提案状态变化 → 先落 append-only 审计，再通知订阅者（顺序与 7A 一致）。"""

    def __init__(self, *, sink: Any = None, logger: Any = None) -> None:
        self._sink = sink
        self._log = logger
        self._subscribers: list[Any] = []
        #: 同一份提案的同一种事件只发一次（store 的状态机已经保证，这里是进程内兜底）
        self._seen: set[str] = set()

    def subscribe(self, handler: Any) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Any) -> None:
        if handler in self._subscribers:
            self._subscribers.remove(handler)

    async def publish(
        self,
        name: str,
        proposal: TaskProposal,
        *,
        store: Any,
        timestamp: float,
        reason: str = "",
    ) -> bool:
        key = f"{name}:{proposal.proposal_id}"
        if key in self._seen:
            return False
        if hasattr(store, "log_event"):
            await store.log_event(
                proposal_id=proposal.proposal_id,
                event=name,
                reason=str(reason or proposal.reason or ""),
                at=timestamp,
                detail={
                    "source": str(proposal.source),
                    "status": str(proposal.status),
                    "feasibility": str(proposal.feasibility),
                    "intent_id": str(proposal.intent_id),
                    "capability_gaps": capability_gaps(proposal),
                },
            )
        self._seen.add(key)
        payload = proposal_payload(proposal, timestamp=timestamp, reason=reason)
        for handler in list(self._subscribers):
            try:
                handler(name, payload)
            except Exception:  # noqa: BLE001 - 订阅者坏了不能影响状态机
                if self._log is not None:
                    self._log.debug("[TaskProposal] 事件订阅者异常（忽略）", exc_info=True)
        if self._sink is not None:
            try:
                self._sink(name, payload)
            except Exception:  # noqa: BLE001
                if self._log is not None:
                    self._log.debug("[TaskProposal] sink 异常（忽略）", exc_info=True)
        return True


def event_for_status(status: str) -> str:
    """状态 → 事件名（只认这张表；默认按"新建"处理）。"""
    return {
        ProposalStatus.REJECTED.value: PROPOSAL_REJECTED,
        ProposalStatus.EXPIRED.value: PROPOSAL_EXPIRED,
        ProposalStatus.CANCELLED.value: PROPOSAL_CANCELLED,
    }.get(str(status), PROPOSAL_CREATED)
