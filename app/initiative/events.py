"""Phase 7A §四十七/§四十八：LifeIntent 事件。

四个（+ 一个取消）：

```text
initiative.created / initiative.suppressed / initiative.expired / initiative.resolved
initiative.cancelled
```

**没有** ``initiative.executed`` —— 本阶段不执行（§四十七）。
事件默认只是 **state update**：发出去之后谁都不许因此调模型或发 QQ（§四十八）。
"""

from __future__ import annotations

from typing import Any

from app.initiative.model import LifeIntent

INITIATIVE_CREATED = "initiative.created"
INITIATIVE_SUPPRESSED = "initiative.suppressed"
INITIATIVE_EXPIRED = "initiative.expired"
INITIATIVE_RESOLVED = "initiative.resolved"
INITIATIVE_CANCELLED = "initiative.cancelled"

INITIATIVE_EVENT_NAMES = (
    INITIATIVE_CREATED,
    INITIATIVE_SUPPRESSED,
    INITIATIVE_EXPIRED,
    INITIATIVE_RESOLVED,
    INITIATIVE_CANCELLED,
)


def intent_payload(intent: LifeIntent, *, timestamp: float, reason: str = "") -> dict[str, Any]:
    """最小载荷（§四十七）—— 只够审计与只读展示，不含任何内部思考。"""
    return {
        "intent_id": intent.intent_id,
        "character_id": intent.character_id,
        "intent_type": intent.intent_type.value,
        "source": intent.source.value,
        "status": intent.status.value,
        "priority": float(intent.priority),
        "timestamp": float(timestamp),
        "reason": str(reason or intent.suppression_reason or intent.resolution_reason or ""),
    }


class InitiativeEventPublisher:
    """把意图状态变化变成事件：**先落 append-only 审计，再通知订阅者**。

    ``sink`` 是调用方注入的同步钩子（Bot 装配时给）—— 与活动层同一纪律：
    订阅者只能**读**事件，不能借事件去驱动对话或世界动作（§四十八）。
    """

    def __init__(self, *, sink: Any = None, logger: Any = None) -> None:
        self._sink = sink
        self._log = logger
        self._subscribers: list[Any] = []
        #: 一次性事件（created/suppressed/expired/resolved/cancelled 都由 store 的
        #: 状态机保证只发生一次；这里额外记住进程内已经发过的，避免重复通知）
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
        intent: LifeIntent,
        *,
        store: Any,
        timestamp: float,
        reason: str = "",
    ) -> bool:
        """发布一条事件；返回是否真的发出去了（重复的只会被忽略）。"""
        key = f"{name}:{intent.intent_id}"
        if key in self._seen:
            return False
        if hasattr(store, "log_event"):
            await store.log_event(
                character_id=intent.character_id,
                intent_id=intent.intent_id,
                event=name,
                reason=str(reason or ""),
                source=intent.source.value,
                at=timestamp,
                detail={
                    "intent_type": intent.intent_type.value,
                    "status": intent.status.value,
                    "priority": float(intent.priority),
                },
            )
        self._seen.add(key)
        payload = intent_payload(intent, timestamp=timestamp, reason=reason)
        for handler in list(self._subscribers):
            try:
                handler(name, payload)
            except Exception:  # noqa: BLE001 - 订阅者坏了不能影响状态机
                if self._log is not None:
                    self._log.debug("[Initiative] 事件订阅者异常（忽略）", exc_info=True)
        if self._sink is not None:
            try:
                self._sink(name, payload)
            except Exception:  # noqa: BLE001
                if self._log is not None:
                    self._log.debug("[Initiative] sink 异常（忽略）", exc_info=True)
        return True


def event_for_status(status: str) -> str:
    """状态 → 事件名（只认这张表）。"""
    return {
        "PROPOSED": INITIATIVE_CREATED,
        "SUPPRESSED": INITIATIVE_SUPPRESSED,
        "EXPIRED": INITIATIVE_EXPIRED,
        "RESOLVED": INITIATIVE_RESOLVED,
        "CANCELLED": INITIATIVE_CANCELLED,
    }.get(str(status), INITIATIVE_CREATED)
