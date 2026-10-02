"""Sandbox event spine (§5/§13/§15): facts about the world, causally linked.

One bus per SandboxRuntime (§14) — never a global. Events record *what
happened* (PET_HUNGRY, ITEM_CONSUMED, ACTION_COMPLETED…), never what the
character should think about it; interpretation belongs to the decision layer.

Causality (§13):

* ``causation_id``  — the event that directly caused this one (the parent in
  the dispatch stack);
* ``correlation_id`` — the root of the chain, so one behaviour ("why did the
  pet get fed?") can be replayed end-to-end.

Bounds (§15): nested publish is legal and expected (Event → Mutation →
Event); a per-dispatch depth guard plus a per-correlation chain limit turn
infinite loops into a dropped event + warning instead of a crash. The bus is
transient (§20) — state lives in the runtime, persistence stays with the
existing SandboxEventRecord/store path.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import deque
from collections.abc import Callable
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("CatooBot.Sandbox.Events")


class SandboxEventType(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    """Factual world events (§5) — what happened, not what to do about it."""

    # pet
    PET_HUNGRY = "pet_hungry"
    PET_APPROACHED = "pet_approached"
    PET_FED = "pet_fed"
    # inventory / objects
    ITEM_CONSUMED = "item_consumed"
    ITEM_ACQUIRED = "item_acquired"
    INVENTORY_DEPLETED = "inventory_depleted"
    OBJECT_STATE_CHANGED = "object_state_changed"
    # entity movement
    ENTITY_MOVE_REQUESTED = "entity_move_requested"
    ENTITY_MOVED = "entity_moved"
    ENTITY_MOVE_REJECTED = "entity_move_rejected"
    # action lifecycle (§10)
    ACTION_REQUESTED = "action_requested"
    ACTION_STARTED = "action_started"
    ACTION_EFFECT_APPLIED = "action_effect_applied"
    ACTION_COMPLETED = "action_completed"
    ACTION_FAILED = "action_failed"
    ACTION_INTERRUPTED = "action_interrupted"
    # interactions (§11)
    INTERACTION_STARTED = "interaction_started"
    INTERACTION_COMPLETED = "interaction_completed"
    INTERACTION_REJECTED = "interaction_rejected"
    # goal layer (Phase 7)
    GOAL_CREATED = "goal_created"
    GOAL_ACTIVATED = "goal_activated"
    GOAL_PROGRESS = "goal_progress"
    GOAL_BLOCKED = "goal_blocked"
    GOAL_COMPLETED = "goal_completed"
    GOAL_CANCELLED = "goal_cancelled"
    GOAL_STEP_STARTED = "goal_step_started"
    GOAL_STEP_COMPLETED = "goal_step_completed"
    GOAL_STEP_FAILED = "goal_step_failed"
    # cognitive decision layer (Phase 6)
    DECISION_REQUESTED = "decision_requested"
    DECISION_PROPOSED = "decision_proposed"
    DECISION_ACCEPTED = "decision_accepted"
    DECISION_REJECTED = "decision_rejected"
    DECISION_FALLBACK = "decision_fallback"
    # social / relationship dynamics (Phase 8)
    SOCIAL_INTERACTION = "social_interaction"
    RELATIONSHIP_CHANGED = "relationship_changed"
    # state authority closure (Phase 3.5)
    PROJECT_PROGRESS_CHANGED = "project_progress_changed"
    KNOWLEDGE_CHANGED = "knowledge_changed"
    # social spaces / character scalars (Phase 3 remediation)
    SOCIAL_SPACE_CHANGED = "social_space_changed"
    CHARACTER_FIELD_CHANGED = "character_field_changed"
    # external world influence (Phase 3)
    EXTERNAL_EVENT_RECEIVED = "external_event_received"
    EXTERNAL_EVENT_REJECTED = "external_event_rejected"
    WORLD_EXTERNAL_INFLUENCE = "world_external_influence"
    ACTION_RESUMED = "action_resumed"
    # needs / generic
    NEED_CHANGED = "need_changed"
    WORLD_NOTE = "world_note"


class SandboxEvent(BaseModel):
    """One immutable fact (§13): who, what, when, and why it happened."""

    event_id: str
    timestamp: float
    event_type: SandboxEventType
    source_entity_id: str = ""
    target_entity_id: str = ""
    payload: dict[str, Any] = Field(default_factory=dict)
    #: the directly-causing event (empty for chain roots)
    causation_id: str = ""
    #: the root of this behaviour chain
    correlation_id: str = ""


Handler = Callable[[SandboxEvent], Any]


class EventBus:
    """In-process, per-runtime pub/sub with bounded causal chains (§14/§15)."""

    def __init__(
        self,
        *,
        clock: Any = time.time,
        history_size: int = 256,
        max_depth: int = 6,
        max_chain: int = 48,
        logger_: logging.Logger | None = None,
    ) -> None:
        self._clock = clock
        self._handlers: list[Handler] = []
        self._history: deque[SandboxEvent] = deque(maxlen=history_size)
        self._depth = 0
        self._root: SandboxEvent | None = None  # correlation root of the live chain
        self._max_depth = max_depth
        self._max_chain = max_chain
        self._chain_counts: dict[str, int] = {}
        self.dropped = 0
        self.handler_errors = 0
        self._log = logger_ or logger

    # ----------------------------------------------------------------- api

    def subscribe(self, handler: Handler) -> None:
        """Register a synchronous handler; exceptions never break the bus."""
        self._handlers.append(handler)

    def publish(
        self,
        event_type: SandboxEventType | str,
        *,
        source: str = "",
        target: str = "",
        payload: dict[str, Any] | None = None,
        causation_id: str = "",
        correlation_id: str = "",
        timestamp: float | None = None,
    ) -> SandboxEvent | None:
        """Create, record and dispatch one event. Returns it (or None if dropped)."""
        parent = self._root
        event = SandboxEvent(
            event_id=f"evt_{uuid.uuid4().hex[:12]}",
            timestamp=float(self._clock() if timestamp is None else timestamp),
            event_type=SandboxEventType(event_type),
            source_entity_id=source,
            target_entity_id=target,
            payload=dict(payload or {}),
            causation_id=causation_id or (parent.event_id if parent and self._depth else ""),
            correlation_id=correlation_id or (parent.correlation_id if parent else ""),
        )
        if not event.correlation_id:
            event.correlation_id = event.event_id  # chain root
        # §15: bounds — depth guard for recursion, chain guard for loops
        if self._depth >= self._max_depth:
            self._drop(event, "max_depth")
            return None
        count = self._chain_counts.get(event.correlation_id, 0)
        if count >= self._max_chain:
            self._drop(event, "max_chain")
            return None
        self._chain_counts[event.correlation_id] = count + 1
        self._history.append(event)
        self._dispatch(event)
        return event

    def _dispatch(self, event: SandboxEvent) -> None:
        self._depth += 1
        try:
            for handler in list(self._handlers):
                try:
                    handler(event)
                except Exception:  # noqa: BLE001 - a handler must never break the world
                    self.handler_errors += 1
                    self._log.warning(
                        "[EventBus] handler failed for %s", event.event_type.value, exc_info=True
                    )
        finally:
            self._depth -= 1
            if self._depth == 0:
                self._root = None
                self._chain_counts.clear()

    def _drop(self, event: SandboxEvent, reason: str) -> None:
        self.dropped += 1
        self._log.warning(
            "[EventBus] dropped %s (%s): correlation=%s exceeded bounds — "
            "possible event loop (§15)",
            event.event_type.value,
            reason,
            event.correlation_id,
        )

    # --------------------------------------------------------------- reads

    def recent(self, *, limit: int = 50) -> list[SandboxEvent]:
        return list(self._history)[-limit:]

    def of_type(self, *types: SandboxEventType) -> list[SandboxEvent]:
        wanted = {SandboxEventType(t) for t in types}
        return [event for event in self._history if event.event_type in wanted]

    def chain(self, correlation_id: str) -> list[SandboxEvent]:
        """The full causal chain of one behaviour, in order (§13 debugging)."""
        return [event for event in self._history if event.correlation_id == correlation_id]

    def last(self, event_type: SandboxEventType) -> SandboxEvent | None:
        for event in reversed(self._history):
            if event.event_type == event_type:
                return event
        return None

    def snapshot(self, *, limit: int = 30) -> list[dict[str, Any]]:
        """§23 debug trace: newest events as plain dicts."""
        return [
            {
                "event_id": e.event_id,
                "ts": e.timestamp,
                "type": e.event_type.value,
                "source": e.source_entity_id,
                "target": e.target_entity_id,
                "payload": e.payload,
                "causation": e.causation_id,
                "correlation": e.correlation_id,
            }
            for e in self.recent(limit=limit)
        ]
