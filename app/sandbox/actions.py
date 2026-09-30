"""ActionSystem (v2.0 §38-§42): the character's current action lives until it
completes, is interrupted, blocked or expires — never re-rolled per tick (§40).

Duration follows the definition's min/typical/max band; which end is picked
depends on needs and preferences (deterministic given the sandbox RNG seed).
"""

from __future__ import annotations

import random
import time
import uuid
from typing import Any

from app.sandbox.models import (
    ActionDefinition,
    ActionInstance,
    ActionStatus,
)


class ActionSystem:
    def __init__(
        self,
        definitions: dict[str, ActionDefinition],
        *,
        rng: random.Random,
        clock: Any = time.time,
    ) -> None:
        self.definitions = definitions
        self._rng = rng
        self._clock = clock

    # ------------------------------------------------------------------ start

    def plan_duration(self, definition: ActionDefinition, *, urgency: float) -> float:
        """Minutes for this run: urgent needs → shorter; leisure → longer."""
        low, mid, high = (
            definition.min_minutes, definition.typical_minutes, definition.max_minutes,
        )
        if urgency >= 0.6:
            span = mid - low
            minutes = low + span * urgency
        else:
            lever = self._rng.random()
            if lever < 0.25:
                minutes = low + (mid - low) * self._rng.random()
            elif lever < 0.8:
                minutes = mid + (high - mid) * 0.3 * self._rng.random()
            else:
                minutes = mid + (high - mid) * 0.8 * self._rng.random()
        return max(low, min(high, minutes))

    def start(
        self,
        definition: ActionDefinition,
        *,
        space_id: str,
        reason_code: str,
        urgency: float = 0.0,
        source: str = "decision",
        previous_definition_id: str = "",
        detail: str = "",
    ) -> ActionInstance:
        now = float(self._clock())
        minutes = self.plan_duration(definition, urgency=urgency)
        if not detail and definition.detail_pool:
            detail = self._rng.choice(definition.detail_pool)
        return ActionInstance(
            id=f"act_{uuid.uuid4().hex[:10]}",
            definition_id=definition.id,
            started_at=now,
            planned_end_at=now + minutes * 60.0,
            detail=detail,
            reason_code=reason_code,
            source=source,
            space_id=space_id,
            previous_definition_id=previous_definition_id,
        )

    # --------------------------------------------------------------- progress

    def elapsed_minutes(self, instance: ActionInstance, *, now: float | None = None) -> float:
        stamp = float(self._clock()) if now is None else now
        return max(0.0, (stamp - instance.started_at) / 60.0)

    def expected_minutes(self, instance: ActionInstance) -> float:
        return max(1.0, (instance.planned_end_at - instance.started_at) / 60.0)

    def progress(self, instance: ActionInstance, *, now: float | None = None) -> float:
        expected = self.expected_minutes(instance)
        return max(0.0, min(1.0, self.elapsed_minutes(instance, now=now) / expected))

    def is_due(self, instance: ActionInstance, *, now: float | None = None) -> bool:
        stamp = float(self._clock()) if now is None else now
        return stamp >= instance.planned_end_at

    def finish(
        self, instance: ActionInstance, status: ActionStatus, *, now: float | None = None
    ) -> None:
        stamp = float(self._clock()) if now is None else now
        instance.status = status
        instance.ended_at = stamp
        instance.progress = self.progress(instance, now=stamp)

    # ---------------------------------------------------------- continuation

    def should_continue(
        self,
        instance: ActionInstance,
        definition: ActionDefinition,
        *,
        need_pressure_next: float,
        energy: float,
        now: float | None = None,
    ) -> tuple[bool, str]:
        """§42: continue / extend / switch — reason-coded, no dice as the driver.

        Returns ``(should_continue, reason_code)``; the *decision engine* makes
        the final call, this only reads the action's own signals.
        """
        if not self.is_due(instance, now=now):
            return True, "not_due"
        if definition.tags and "core" in definition.tags and need_pressure_next >= 0.7:
            return True, "basic_need_pending"
        if need_pressure_next >= 0.75:
            return False, "need_pressure"
        if energy <= 0.15 and "rest" not in definition.tags:
            return False, "tired"
        # Extend within the max band when the character is absorbed (§42).
        if self.progress(instance, now=now) < 1.0:
            return True, "still_engaged"
        return False, "natural_completion"

    def can_extend(self, instance: ActionInstance, definition: ActionDefinition) -> bool:
        return (
            (instance.planned_end_at - instance.started_at) / 60.0
            < definition.max_minutes * 0.95
        )

    def extend(self, instance: ActionInstance, definition: ActionDefinition) -> float:
        """Push the planned end toward the max band; returns extra minutes."""
        current_minutes = (instance.planned_end_at - instance.started_at) / 60.0
        step = min(
            max(10.0, current_minutes * 0.4),
            definition.max_minutes - current_minutes,
        )
        if step <= 1.0:
            return 0.0
        instance.planned_end_at += step * 60.0
        return step

    # ------------------------------------------------------------------ reads

    def definition(self, instance: ActionInstance | None) -> ActionDefinition | None:
        if instance is None:
            return None
        return self.definitions.get(instance.definition_id)
