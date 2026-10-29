"""Phase 6A §三十二/§三十三/§三十四：Activity 事件（幂等、纯状态、绝不触发 LLM）。

事件名固定八个（§三十二）；载荷固定六个字段（§三十二）：

```
episode_id / character_id / activity / timestamp / reason / source
```

两条硬规则：

* **幂等**（§三十三）：同一个 ``episode_id`` + 同一个**一次性**转移只发布一次 ——
  靠 store 的转移日志（SQLite 上还有 partial unique index）做跨进程/跨重启的去重，
  重启或调度重试都**不会**产生第二个 ``activity.completed``。
* **不产生 AI Turn**（§三十四）：这里的 sink 只有一个同步回调 + 日志，
  绝不调用角色运行时/模型。谁要"用事件去说话"是以后阶段的事。
"""

from __future__ import annotations

from typing import Any

from app.activity.model import ActivityEpisode, TransitionReason

#: 八个事件名（§三十二）
ACTIVITY_SCHEDULED = "activity.scheduled"
ACTIVITY_STARTED = "activity.started"
ACTIVITY_EXTENDED = "activity.extended"
ACTIVITY_COMPLETED = "activity.completed"
ACTIVITY_INTERRUPTED = "activity.interrupted"
ACTIVITY_CANCELLED = "activity.cancelled"
ACTIVITY_EXPIRED = "activity.expired"
ACTIVITY_RECOVERED = "activity.recovered"

ACTIVITY_EVENT_NAMES = (
    ACTIVITY_SCHEDULED,
    ACTIVITY_STARTED,
    ACTIVITY_EXTENDED,
    ACTIVITY_COMPLETED,
    ACTIVITY_INTERRUPTED,
    ACTIVITY_CANCELLED,
    ACTIVITY_EXPIRED,
    ACTIVITY_RECOVERED,
)

#: 状态转移 → 事件名（runtime 只认这张表）
EVENT_BY_STATUS: dict[str, str] = {
    "SCHEDULED": ACTIVITY_SCHEDULED,
    "ACTIVE": ACTIVITY_STARTED,
    "EXTENDED": ACTIVITY_EXTENDED,
    "COMPLETED": ACTIVITY_COMPLETED,
    "INTERRUPTED": ACTIVITY_INTERRUPTED,
    "CANCELLED": ACTIVITY_CANCELLED,
    "EXPIRED": ACTIVITY_EXPIRED,
}


def event_payload(
    episode: ActivityEpisode, *, timestamp: float, reason: str = ""
) -> dict[str, Any]:
    """§三十二要求的最小载荷（不多塞内部字段）。"""
    return {
        "episode_id": episode.episode_id,
        "character_id": episode.character_id,
        "activity": episode.activity_name,
        "status": episode.status.value,
        "timestamp": float(timestamp),
        "reason": str(reason or episode.transition_reason or ""),
        "source": episode.source.value,
    }


class ActivityEventPublisher:
    """把 Episode 转移变成事件：**先落审计日志，再发一次（幂等）**。

    ``sink`` 是调用方注入的同步钩子（Bot 装配时给），默认什么都不做 —— 只能读事件、
    不能借事件去驱动对话或世界动作。
    """

    def __init__(self, *, sink: Any = None, logger: Any = None) -> None:
        self._sink = sink
        self._log = logger
        #: 进程内订阅者（WebUI 实时、测试取证都走这里）
        self._subscribers: list[Any] = []

    def subscribe(self, handler: Any) -> None:
        if handler not in self._subscribers:
            self._subscribers.append(handler)

    def unsubscribe(self, handler: Any) -> None:
        if handler in self._subscribers:
            self._subscribers.remove(handler)

    async def publish(
        self,
        name: str,
        episode: ActivityEpisode,
        *,
        store: Any,
        timestamp: float,
        reason: str = "",
        detail: str = "",
        audit: bool = True,
    ) -> bool:
        """发布一条事件。``audit=True`` 时先过幂等日志（一次性转移只发一次）。

        ``detail`` 只进**审计行**（例如延长多少秒），不进事件载荷 ——
        事件载荷保持 §三十二 的六个字段。

        返回是否真的发出去了（``False`` = 这条一次性事件之前已经发过）。
        """
        one_shot = {
            ACTIVITY_STARTED,
            ACTIVITY_COMPLETED,
            ACTIVITY_INTERRUPTED,
            ACTIVITY_CANCELLED,
            ACTIVITY_EXPIRED,
        }
        # Phase 6B §十六：**延长也要留审计行**（extension_seconds / 新的 planned_end）。
        # EXTENDED 不在一次性集合里：它可以合法地重复，所以只追加、不做幂等拦截。
        audited = one_shot | {ACTIVITY_EXTENDED}
        if audit and name in audited and hasattr(store, "log_transition"):
            logged_reason = str(reason or episode.transition_reason or "")
            if detail:
                logged_reason = f"{logged_reason} {detail}".strip()
            fresh = await store.log_transition(
                episode_id=episode.episode_id,
                transition=episode.status.value,
                reason=logged_reason,
                source=episode.source.value,
                at=float(timestamp),
            )
            if not fresh:
                if self._log is not None:
                    self._log.debug(
                        "[World.Activity] 事件已发过（幂等跳过）episode=%s %s",
                        episode.episode_id,
                        name,
                    )
                return False
        payload = event_payload(episode, timestamp=timestamp, reason=reason)
        if self._log is not None:
            self._log.info(
                "[World.Activity] episode=%s %s activity=%s status=%s reason=%s source=%s",
                payload["episode_id"],
                name,
                payload["activity"],
                payload["status"],
                payload["reason"],
                payload["source"],
            )
        if self._sink is not None:
            try:
                self._sink(name, payload)
            except Exception:  # noqa: BLE001 - 事件订阅者故障绝不影响 Episode 生命周期
                if self._log is not None:
                    self._log.exception("[World.Activity] 事件订阅者失败（忽略）")
        for handler in list(self._subscribers):
            try:
                result = handler(name, payload)
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - 同上
                if self._log is not None:
                    self._log.exception("[World.Activity] 事件处理失败（忽略）")
        return True


def reason_text(reason: TransitionReason | str) -> str:
    return reason.value if isinstance(reason, TransitionReason) else str(reason or "")
