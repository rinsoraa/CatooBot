"""External World layer (v2.1 Phase 3 §4/§6/§8/§9/§13-§15).

The boundary between "outside happened" and "her world changed":

    External World → ExternalWorldEvent → Influence → Wakeup → Action → Mutation

Three rules anchor the design:

* an ``ExternalWorldEvent`` is protocol-agnostic — QQ/OneBot objects never
  cross into the sandbox (§4/§5; the adapter in ``external_adapters`` does the
  translation);
* the :class:`ExternalInfluenceEvaluator` only answers *"is this worth handing
  to the sandbox, and how"* (§6/§7) — it never mutates state, never picks an
  action, never calls an LLM;
* the queue gives FIFO + urgency ordering, dedupe by ``event_id`` (§9/§19) and
  is snapshottable, so a restart neither replays nor drops important events.
"""

from __future__ import annotations

import time
from collections import deque
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExternalSource(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    """Where the outside event came from (§4) — QQ is one source among many."""

    qq = "qq"
    delivery = "delivery"
    timer = "timer"
    system = "system"
    web = "web"
    other = "other"


class ExternalUrgency(str, Enum):  # noqa: UP042
    low = "low"
    normal = "normal"
    high = "high"
    critical = "critical"

    @property
    def rank(self) -> int:
        return {"low": 0, "normal": 1, "high": 2, "critical": 3}[self.value]


class InfluenceAction(str, Enum):  # noqa: UP042 - §6 decision vocabulary
    """What the sandbox should do with this external fact — and nothing more."""

    NO_EFFECT = "no_effect"  # record for diagnostics only
    OBSERVE = "observe"  # accepted as a stimulus; the world keeps running
    WAKE = "wake"  # evaluate the world *now* instead of waiting for the tick
    INTERRUPT = "interrupt"  # worth breaking the current action for
    REJECT = "reject"  # cannot be represented in this world (§17)


class ExternalWorldEvent(BaseModel):
    """One outside-world fact (§4). Protocol-free by construction."""

    event_id: str
    timestamp: float = 0.0
    source: ExternalSource = ExternalSource.other
    #: who caused it (a user id, a courier, "system") — an id, never a name
    actor_id: str = ""
    #: coarse protocol-level kind: message / delivery_arrived / admin / timer…
    event_type: str = "message"
    content: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)
    urgency: ExternalUrgency = ExternalUrgency.normal
    #: the adapter's semantics (invitation / share / question…) — empty when none
    semantic_kind: str = ""
    #: what the event would engage, in activity vocabulary (gaming / outdoors…)
    target_activity: str = ""
    #: how the sandbox knows the actor: core_friend / known / unknown
    actor_relationship: str = "unknown"
    correlation_id: str = ""

    def model_post_init(self, __context: Any) -> None:
        if not self.timestamp:
            self.timestamp = time.time()


class InfluenceDecision(BaseModel):
    """The evaluator's verdict (§6) — a routing hint, not an action (§7)."""

    action: InfluenceAction
    reason_code: str = ""
    semantic_kind: str = ""
    target_activity: str = ""
    notes: str = ""


class ExternalEventQueue:
    """FIFO + urgency ordering, dedupe by event_id, snapshot-able (§9/§19)."""

    def __init__(self, *, maxlen: int = 128, seen_size: int = 256) -> None:
        self._pending: deque[ExternalWorldEvent] = deque()
        self._seen: deque[str] = deque(maxlen=seen_size)
        self._seen_set: set[str] = set()
        self._maxlen = maxlen
        self.dropped = 0

    # ------------------------------------------------------------------ api

    def push(self, event: ExternalWorldEvent) -> bool:
        """Enqueue unless this event_id was already accepted (§19)."""
        if event.event_id in self._seen_set:
            return False
        if len(self._pending) >= self._maxlen:
            self._pending.popleft()  # oldest non-critical yields to the new one
            self.dropped += 1
        self._pending.append(event)
        self._remember(event.event_id)
        return True

    def _remember(self, event_id: str) -> None:
        if len(self._seen) == self._seen.maxlen:
            oldest = self._seen.popleft()
            self._seen_set.discard(oldest)
        self._seen.append(event_id)
        self._seen_set.add(event_id)

    def note_processed(self, event_id: str) -> None:
        """Mark an id as consumed (also for events processed off-queue)."""
        if event_id and event_id not in self._seen_set:
            self._remember(event_id)

    def seen(self, event_id: str) -> bool:
        return event_id in self._seen_set

    def pop(self) -> ExternalWorldEvent | None:
        """Highest urgency first, FIFO within the same urgency (§13)."""
        if not self._pending:
            return None
        best = max(
            range(len(self._pending)),
            key=lambda i: (self._pending[i].urgency.rank, -i),
        )
        if best != 0:
            self._pending.rotate(-best)
            event = self._pending.popleft()
            self._pending.rotate(best)
            return event
        return self._pending.popleft()

    def __len__(self) -> int:
        return len(self._pending)

    def pending(self) -> list[ExternalWorldEvent]:
        return list(self._pending)

    # -------------------------------------------------------------- snapshot

    def snapshot(self) -> dict[str, Any]:
        return {
            "pending": [e.model_dump(mode="json") for e in self._pending],
            "seen": list(self._seen),
        }

    def restore(self, data: Any) -> None:
        if not isinstance(data, dict):
            return
        self._pending.clear()
        self._seen.clear()
        self._seen_set.clear()
        for payload in data.get("seen", []) or []:
            self._remember(str(payload))
        for payload in data.get("pending", []) or []:
            try:
                self._pending.append(ExternalWorldEvent.model_validate(payload))
            except Exception:  # noqa: BLE001 - a corrupt row never blocks startup
                continue


class ExternalInfluenceEvaluator:
    """Answers *"is this worth the sandbox's attention, and how"* (§6/§7).

    Inputs are generically typed signals only (§17): urgency, the adapter's
    semantic kind, actor relationship, whether the target activity exists in
    this world, and how interruptible the current action is. No names, no
    content matching, no LLM, no state access.
    """

    #: how interruptible the running action must be for a core invitation
    CORE_INVITE_INTERRUPTIBILITY = 0.3
    #: critical external facts break anything that is not essentially locked
    CRITICAL_INTERRUPTIBILITY = 0.9

    def evaluate(
        self,
        event: ExternalWorldEvent,
        *,
        available_activities: set[str],
        has_action: bool,
        interruptibility: float,
    ) -> InfluenceDecision:
        semantic = event.semantic_kind
        target = event.target_activity

        # §13: critical facts always reach the sandbox immediately.
        if event.urgency is ExternalUrgency.critical:
            if has_action and interruptibility < self.CRITICAL_INTERRUPTIBILITY:
                return InfluenceDecision(
                    action=InfluenceAction.INTERRUPT,
                    reason_code="critical_urgency",
                    semantic_kind=semantic,
                    target_activity=target,
                )
            return InfluenceDecision(
                action=InfluenceAction.WAKE,
                reason_code="critical_urgency",
                semantic_kind=semantic,
                target_activity=target,
            )

        if semantic in ("game_invitation", "activity_invitation"):
            activity = target or "gaming"
            # §17: whether the activity exists is decided by the world seed
            if activity not in available_activities:
                return InfluenceDecision(
                    action=InfluenceAction.REJECT,
                    reason_code="activity_unavailable",
                    semantic_kind=semantic,
                    target_activity=activity,
                    notes="这个世界没有对应的活动",
                )
            if event.actor_relationship == "core_friend":
                if has_action and interruptibility >= self.CORE_INVITE_INTERRUPTIBILITY:
                    return InfluenceDecision(
                        action=InfluenceAction.INTERRUPT,
                        reason_code="core_friend_invitation",
                        semantic_kind=semantic,
                        target_activity=activity,
                    )
                return InfluenceDecision(
                    action=InfluenceAction.WAKE,
                    reason_code="core_friend_invitation",
                    semantic_kind=semantic,
                    target_activity=activity,
                )
            # an acquaintance's invitation is noticed, never obeyed out of hand
            return InfluenceDecision(
                action=InfluenceAction.OBSERVE,
                reason_code="invitation_from_acquaintance",
                semantic_kind=semantic,
                target_activity=activity,
            )

        if event.event_type == "delivery_arrived":
            if event.urgency.rank >= ExternalUrgency.high.rank:
                return InfluenceDecision(
                    action=InfluenceAction.WAKE, reason_code="delivery_arrived"
                )
            return InfluenceDecision(action=InfluenceAction.OBSERVE, reason_code="delivery_arrived")

        if event.event_type == "admin":
            return InfluenceDecision(action=InfluenceAction.OBSERVE, reason_code="admin_note")

        if (
            event.source is ExternalSource.qq
            and event.actor_relationship == "core_friend"
            and event.urgency.rank >= ExternalUrgency.high.rank
        ):
            # someone important spoke: evaluate now, but do not hijack her hands
            return InfluenceDecision(action=InfluenceAction.WAKE, reason_code="core_friend_message")

        if (
            event.urgency is ExternalUrgency.low
            and not semantic
            and event.actor_relationship == "unknown"
        ):
            return InfluenceDecision(action=InfluenceAction.NO_EFFECT, reason_code="no_relevance")

        return InfluenceDecision(action=InfluenceAction.OBSERVE, reason_code="ordinary_stimulus")
