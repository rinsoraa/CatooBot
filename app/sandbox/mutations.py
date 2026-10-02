"""Sandbox interfaces for later phases (§49-§53) — stable contracts, minimal impl.

Phase 1 only needs the *data structures* and the lightest possible wiring:

* :class:`StateMutation` — every state change records source/reason/before/
  after/timestamp (§53), appended to an in-memory audit log;
* :class:`EntityInteraction` / :class:`DecisionRequest` — stable payloads for
  the Phase 2 interaction work (§51); no behavior yet;
* :class:`SandboxEventBus` — a tiny pub/sub around :class:`ExternalEvent`
  (§50) so future QQ→Sandbox influence has one entry point.

Deliberately NOT here: complex entity interaction, memory feedback, emergent
behavior (§75 — later phases).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from dataclasses import field as dfield
from typing import Any


@dataclass
class StateMutation:
    """One recorded state change (§53): who changed what, and why."""

    target: str  # e.g. "inventory:fridge", "character.location", "needs:hunger"
    field: str  # e.g. "可乐", "location", "level"
    before: Any
    after: Any
    source: str  # character_action / external_event / admin / system / pet_action
    reason: str  # action id / event kind / rule id
    timestamp: float = dfield(default_factory=time.time)


class MutationLog:
    """Bounded in-memory audit trail of state mutations (§53)."""

    def __init__(self, *, maxlen: int = 500) -> None:
        self._entries: deque[StateMutation] = deque(maxlen=maxlen)

    def record(self, mutation: StateMutation) -> None:
        self._entries.append(mutation)

    def recent(self, *, limit: int = 50) -> list[dict[str, Any]]:
        rows = list(self._entries)[-limit:]
        return [
            {
                "target": m.target,
                "field": m.field,
                "before": m.before,
                "after": m.after,
                "source": m.source,
                "reason": m.reason,
                "timestamp": m.timestamp,
            }
            for m in reversed(rows)
        ]

    def __len__(self) -> int:
        return len(self._entries)


@dataclass
class EntityInteraction:
    """§51: one entity acting on another (Phase 2 payload — stable now)."""

    actor: str  # entity id ("character", "pet_001", "user:123")
    target: str  # entity/object id
    interaction_type: str  # touch / feed / move / use / talk …
    context: dict[str, Any] = dfield(default_factory=dict)
    effects: dict[str, float] = dfield(default_factory=dict)


@dataclass
class DecisionRequest:
    """§49: a request for the decision engine to (re)plan (Phase 2 payload)."""

    reason: str  # external_event / need_critical / admin / schedule
    space_id: str = ""
    payload: dict[str, Any] = dfield(default_factory=dict)


class SandboxEventBus:
    """Minimal pub/sub over ExternalEvents (§49-§50).

    The runtime's ``notify()`` stays the single handling path (§76/§77); the
    bus only adds fan-out so later phases can observe events without touching
    the runtime.
    """

    def __init__(self) -> None:
        self._subscribers: list[Callable[[Any], Any]] = []

    def subscribe(self, callback: Callable[[Any], Any]) -> None:
        self._subscribers.append(callback)

    async def publish(self, event: Any) -> None:
        for callback in self._subscribers:
            try:
                result = callback(event)
                if hasattr(result, "__await__"):
                    await result
            except Exception:  # noqa: BLE001 - a subscriber must never break the world
                continue
