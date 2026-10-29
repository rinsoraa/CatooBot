"""InteractionResolver (§11/§12): what the world *allows*, not what she wants.

Given two entities it answers "is there an executable interaction, and what
does it require?" — never "should she do it" (that is the decision layer).
Candidates and execution work on entity **kinds** and ids (character/pet/
object/space, inventory keys, anchor items) — a character name or pet name can
never appear here (§18).

Execution follows the Phase 2 spine:

    InteractionRequest → validation → Mutation → State change → Events

No LLM anywhere in this module.
"""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from app.sandbox.events import SandboxEventType


class InteractionCandidate(BaseModel):
    """One executable interaction between two entities (§11)."""

    source_kind: str = "character"
    target_id: str
    target_kind: str  # pet / object / space
    interaction_type: str  # feed / observe / take_item / inspect / move
    requirements: list[str] = Field(default_factory=list)
    effects: dict[str, Any] = Field(default_factory=dict)


class InteractionRequest(BaseModel):
    source_entity_id: str = "character"
    target_id: str
    target_kind: str  # pet / object / space
    interaction_type: str
    payload: dict[str, Any] = Field(default_factory=dict)


class InteractionResult(BaseModel):
    ok: bool
    interaction_type: str = ""
    reason: str = ""
    correlation_id: str = ""
    mutations: list[dict[str, Any]] = Field(default_factory=list)


class InteractionResolver:
    """Kind-driven interaction catalogue + executor; owned by the runtime."""

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime

    # ------------------------------------------------------------- catalogue

    def candidates(
        self, target_id: str, target_kind: str, *, source_kind: str = "character"
    ) -> list[InteractionCandidate]:
        """What can a character do with this target right now (§11)?"""
        rt = self._rt
        out: list[InteractionCandidate] = []
        if target_kind == "pet" and rt.pet_system is not None:
            food_ok = rt._pet_food_available()  # noqa: SLF001 - same aggregate
            out.append(
                InteractionCandidate(
                    target_id=target_id,
                    target_kind="pet",
                    interaction_type="feed",
                    requirements=[] if food_ok else ["pet_food_available"],
                    effects={"hunger": "-0.6", "food": "1"},
                )
            )
            out.append(
                InteractionCandidate(
                    target_id=target_id,
                    target_kind="pet",
                    interaction_type="observe",
                    effects={},
                )
            )
            return out
        if target_kind == "object":
            obj = rt.objects.get(target_id)
            if obj is None:
                return out
            out.append(
                InteractionCandidate(
                    target_id=target_id, target_kind="object", interaction_type="inspect"
                )
            )
            if obj.inventory_key:
                inventory = rt.inventories.get(obj.inventory_key)
                if inventory.items:
                    out.append(
                        InteractionCandidate(
                            target_id=target_id,
                            target_kind="object",
                            interaction_type="take_item",
                            requirements=["item_in_inventory"],
                            effects={"items": dict(inventory.items)},
                        )
                    )
            return out
        if target_kind == "space":
            out.append(
                InteractionCandidate(
                    target_id=target_id, target_kind="space", interaction_type="move"
                )
            )
        return out

    # -------------------------------------------------------------- executor

    async def execute(self, request: InteractionRequest) -> InteractionResult:
        """Validate → mutate → events (§6). Never decides *whether* she wants it."""
        rt = self._rt
        correlation = f"ia_{uuid.uuid4().hex[:10]}"
        started = rt.events.publish(
            SandboxEventType.INTERACTION_STARTED,
            source=request.source_entity_id,
            target=request.target_id,
            payload={"interaction_type": request.interaction_type, **request.payload},
            correlation_id=correlation,
        )
        result = await self._dispatch(request, correlation, started.event_id if started else "")
        result.correlation_id = correlation
        return result

    async def _dispatch(
        self, request: InteractionRequest, correlation: str, causation: str
    ) -> InteractionResult:
        rt = self._rt
        itype = request.interaction_type
        if itype == "feed":
            return await self._feed(correlation, causation)
        if itype == "take_item":
            return self._take_item(request, correlation, causation)
        if itype == "move":
            return await self._move(request, correlation, causation)
        if itype == "observe":
            rt.events.publish(
                SandboxEventType.INTERACTION_COMPLETED,
                source=request.source_entity_id,
                target=request.target_id,
                payload={"interaction_type": itype},
                causation_id=causation,
                correlation_id=correlation,
            )
            return InteractionResult(ok=True, interaction_type=itype)
        if itype == "inspect":
            return InteractionResult(ok=True, interaction_type=itype)
        return self._reject(request, correlation, causation, "unknown_interaction")

    # ---------------------------------------------------------------- cases

    async def _feed(self, correlation: str, causation: str) -> InteractionResult:
        rt = self._rt
        pet_id = rt.pet.id if rt.pet else ""
        fed = rt.feed_pet(
            source="interaction",
            reason="interaction:feed",
            correlation=correlation,
            causation_id=causation,
        )
        if not fed:
            rt.events.publish(
                SandboxEventType.INTERACTION_REJECTED,
                target=pet_id,
                payload={"interaction_type": "feed", "reason": "no_pet_food"},
                causation_id=causation,
                correlation_id=correlation,
            )
            return InteractionResult(
                ok=False, interaction_type="feed", reason="no_pet_food", correlation_id=correlation
            )
        rt.events.publish(
            SandboxEventType.INTERACTION_COMPLETED,
            source="character",
            target=pet_id,
            payload={"interaction_type": "feed"},
            causation_id=causation,
            correlation_id=correlation,
        )
        return InteractionResult(ok=True, interaction_type="feed", correlation_id=correlation)

    def _take_item(
        self, request: InteractionRequest, correlation: str, causation: str
    ) -> InteractionResult:
        rt = self._rt
        obj = rt.objects.get(request.target_id)
        if obj is None or not obj.inventory_key:
            return self._reject(request, correlation, causation, "not_a_container")
        item = str(request.payload.get("item", ""))
        quantity = int(request.payload.get("quantity", 1))
        if not item:
            inventory = rt.inventories.get(obj.inventory_key)
            if not inventory.items:
                return self._reject(request, correlation, causation, "inventory_empty")
            item = next(iter(inventory.items))
        taken = rt.take_item(
            obj.inventory_key,
            item,
            quantity=quantity,
            source="interaction",
            reason="interaction:take_item",
            correlation=correlation,
            causation_id=causation,
        )
        if not taken.ok:
            rt.events.publish(
                SandboxEventType.INTERACTION_REJECTED,
                source=request.source_entity_id,
                target=request.target_id,
                payload={"interaction_type": "take_item", "reason": taken.error},
                causation_id=causation,
                correlation_id=correlation,
            )
            return InteractionResult(
                ok=False,
                interaction_type="take_item",
                reason=taken.error,
                correlation_id=correlation,
            )
        rt.events.publish(
            SandboxEventType.INTERACTION_COMPLETED,
            source=request.source_entity_id,
            target=request.target_id,
            payload={"interaction_type": "take_item", "item": item},
            causation_id=causation,
            correlation_id=correlation,
        )
        return InteractionResult(
            ok=True,
            interaction_type="take_item",
            mutations=taken.as_payload()["mutations"],
            correlation_id=correlation,
        )

    async def _move(
        self, request: InteractionRequest, correlation: str, causation: str
    ) -> InteractionResult:
        rt = self._rt
        to_space = str(request.payload.get("to_space", request.target_id))
        moved = rt.move_entity(
            entity_id=request.source_entity_id,
            to_space=to_space,
            source="interaction",
            reason="interaction:move",
            correlation=correlation,
            causation_id=causation,
        )
        if not moved.ok:
            return InteractionResult(
                ok=False,
                interaction_type="move",
                reason=moved.error,
                mutations=moved.as_payload()["mutations"],
                correlation_id=correlation,
            )
        rt.events.publish(
            SandboxEventType.INTERACTION_COMPLETED,
            source=request.source_entity_id,
            target=to_space,
            payload={"interaction_type": "move"},
            causation_id=causation,
            correlation_id=correlation,
        )
        return InteractionResult(
            ok=True,
            interaction_type="move",
            mutations=moved.as_payload()["mutations"],
            correlation_id=correlation,
        )

    def _reject(
        self, request: InteractionRequest, correlation: str, causation: str, reason: str
    ) -> InteractionResult:
        self._rt.events.publish(
            SandboxEventType.INTERACTION_REJECTED,
            source=request.source_entity_id,
            target=request.target_id,
            payload={"interaction_type": request.interaction_type, "reason": reason},
            causation_id=causation,
            correlation_id=correlation,
        )
        return InteractionResult(
            ok=False,
            interaction_type=request.interaction_type,
            reason=reason,
            correlation_id=correlation,
        )
