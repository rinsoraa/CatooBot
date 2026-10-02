"""State mutation layer (§6/§52-§53): events describe facts, mutations change state.

Every state change carries source/reason/before/after/timestamp (§53) and is
recorded in a bounded in-memory audit log. The apply-helpers here are the
*only* sanctioned path for the interaction layer to touch runtime state —
direct field writes from interaction/decision code are the thing this module
exists to prevent (§52).

The event vocabulary lives in :mod:`app.sandbox.events`; this module stays
pure state bookkeeping.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from dataclasses import field as dfield
from typing import Any


@dataclass
class StateMutation:
    """One recorded state change (§53): who changed what, and why."""

    target: str  # e.g. "inventory:fridge", "character.location", "pet:hunger"
    field: str  # e.g. "可乐", "location", "level"
    before: Any
    after: Any
    source: str  # character_action / interaction / external_event / admin / system
    reason: str  # action id / interaction type / event kind
    timestamp: float = dfield(default_factory=time.time)
    #: False marks a *rejected* attempt (audit-trail record; state unchanged)
    ok: bool = True
    #: False marks bookkeeping that cannot change what the sandbox may do
    #: (relationship drift): it is audited and persisted, but it must not
    #: invalidate a decision proposal computed against the world (§18/§21)
    affects_world: bool = True


@dataclass
class MutationResult:
    """Outcome of a mutation batch (§52): what changed, or why nothing did."""

    ok: bool
    mutations: list[StateMutation] = dfield(default_factory=list)
    #: ids of the events this batch emitted (for causal chaining)
    event_ids: list[str] = dfield(default_factory=list)
    error: str = ""

    def as_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "error": self.error,
            "mutations": [
                {
                    "target": m.target,
                    "field": m.field,
                    "before": m.before,
                    "after": m.after,
                }
                for m in self.mutations
            ],
        }


class MutationLog:
    """Bounded in-memory audit trail of state mutations (§53).

    ``on_record`` lets the owning runtime bump its world revision — every
    *applied* world mutation is a state change a stale decision must notice
    (§20); mutations flagged ``affects_world=False`` are bookkeeping and are
    reported with the flag so the owner can leave the revision alone.
    """

    def __init__(self, *, maxlen: int = 500) -> None:
        self._entries: deque[StateMutation] = deque(maxlen=maxlen)
        #: called with each applied mutation (rejected attempts do not count)
        self.on_record: Any = None

    def record(self, mutation: StateMutation) -> StateMutation:
        self._entries.append(mutation)
        if mutation.ok and self.on_record is not None:
            try:
                self.on_record(mutation)
            except Exception:  # noqa: BLE001 - bookkeeping must never break a mutation
                pass
        return mutation

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


# The full decision-layer request lives in :mod:`app.sandbox.intent` (Phase 6);
# re-exported here so the Phase 2/3 name keeps resolving without a second copy.
from app.sandbox.intent import DecisionRequest  # noqa: E402,F401  (single definition)

__all__ = [
    "DecisionRequest",
    "MutationLog",
    "MutationResult",
    "StateMutation",
]
