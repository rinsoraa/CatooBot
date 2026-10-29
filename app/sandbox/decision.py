"""Decision engine + interrupt evaluator + validator (v2.0 §43-§47/§124-§126).

Pipeline: candidate generation (rules) → deterministic constraints (hard
rules, spaces, objects, inventories) → optional AI tie-break for genuinely
ambiguous choices → validation → state mutation. Never `random() < p` as the
driver (§44); the RNG only separates equally-valid candidates (§44/§122).
"""

from __future__ import annotations

import inspect
import random
from typing import Any

from app.sandbox.actions import ActionSystem
from app.sandbox.bible import CharacterBible
from app.sandbox.entities import PetSystem
from app.sandbox.models import (
    ActionDefinition,
    ActionInstance,
    DecisionTrace,
    EventPriority,
    ExternalEvent,
    SandboxDecision,
)
from app.sandbox.modes import ModeRuntime
from app.sandbox.needs import NeedSystem
from app.sandbox.world import InventorySystem, ObjectSystem, SpaceSystem, WorldRuleEngine

#: generic sleep/nap guard rails — conventional action ids, system mechanics
SLEEP_ACTION = "sleep"
NAP_ACTION = "nap"
IDLE_ACTION = "idle"
#: deep-night window (system convention, also the conventional deep_night mode)
DEEP_NIGHT_HOURS = (2, 5)


class SandboxDecisionEngine:
    def __init__(
        self,
        *,
        bible: CharacterBible,
        actions: ActionSystem,
        needs: NeedSystem,
        spaces: SpaceSystem,
        objects: ObjectSystem,
        inventories: InventorySystem,
        rules: WorldRuleEngine,
        modes: ModeRuntime,
        pet: PetSystem | None,
        rng: random.Random,
        clock: Any,
        ai_decider: Any = None,
        preference_bonus: dict[str, float] | None = None,
        home_spaces: set[str] | None = None,
        recency_penalty: Any = None,
    ) -> None:
        self.bible = bible
        self.actions = actions
        self.needs = needs
        self.spaces = spaces
        self.objects = objects
        self.inventories = inventories
        self.rules = rules
        self.modes = modes
        self.pet = pet
        self._rng = rng
        self._clock = clock
        self._ai = ai_decider  # optional: returns SandboxDecision for ambiguity
        #: §180: seed-derived nudges — actions the bible marks as favorites
        self._preference_bonus = dict(preference_bonus or {})
        #: which spaces count as "home" for go-do-it candidates (from the seed)
        self._home_spaces = home_spaces or set()
        #: Phase A: soft repetition penalty — how much to subtract from a score
        #: because the same action was just done (0.0 = no penalty). Injected by
        #: the runtime so the engine stays free of runtime history.
        self._recency_penalty = recency_penalty

    # ------------------------------------------------------------ candidates

    def candidates(
        self, *, space_id: str, asleep: bool = False
    ) -> list[tuple[ActionDefinition, float, list[str]]]:
        """All currently sensible actions with (definition, score, reasons)."""
        out: list[tuple[ActionDefinition, float, list[str]]] = []
        if asleep:
            definition = self.actions.definitions.get(SLEEP_ACTION)
            if definition is not None:
                return [(definition, 1.0, ["asleep"])]
        hour = _hour_of(self._clock())
        sleep_pressure = self.needs.pressure("sleepiness")
        deep_start, deep_end = DEEP_NIGHT_HOURS
        for action_id, definition in self.actions.definitions.items():
            if definition.id == SLEEP_ACTION and not (sleep_pressure >= 0.6 or 1 <= hour < 7):
                continue  # sleep appears at night or under real sleep pressure
            if definition.id == NAP_ACTION and self.needs.level("sleepiness") >= 0.9:
                continue  # not a substitute for the real thing
            if definition.id == NAP_ACTION and deep_start <= hour < 7:
                continue  # at night she sleeps, she doesn't nap
            ok, _ = self.rules.validate_proposal(action_id, space_id, definition)
            if not ok:
                continue
            if "*" not in definition.spaces and space_id not in definition.spaces:
                # not here — allow "go do it" candidates only in home spaces
                if not self.spaces.is_home(space_id):
                    continue
                if not self._reachable_from(space_id, definition.spaces):
                    continue
            if not self._objects_available(definition):
                continue
            if not self.inventories.can_consume(definition.consumes):
                continue
            if not self._requirements_met(definition):
                continue
            score = self._score(definition, hour=hour)
            reasons = self._score_reasons(definition, hour=hour)
            if score <= 0.0:
                continue
            out.append((definition, score, reasons))
        out.sort(key=lambda item: item[1], reverse=True)
        return out

    def _requirements_met(self, definition: ActionDefinition) -> bool:
        """Hard state requirements: requires_absent + the restock threshold floor.

        A ``restock`` action is only sensible while its slot is at or below
        ``min`` — a stocked fridge must never produce another purchase.
        """
        for key, items in definition.requires_absent.items():
            inventory = self.inventories.all().get(key)
            for item in items:
                if inventory is not None and inventory.count(item) > 0:
                    return False
        allowed, _reason = self.restock_gate(definition)
        return allowed

    def restock_gate(self, definition: ActionDefinition) -> tuple[bool, str]:
        """Availability policy for procurement actions (Phase B).

        Returns ``(False, "restock_not_needed")`` when the world already holds
        more than ``restock.min`` of the slot — the decision validator uses
        this exact reason so a stale proposal is refused explicitly.
        """
        policy = definition.restock
        if not policy:
            return True, ""
        inventory_key = str(policy.get("inventory", "") or "")
        slot = str(policy.get("slot", "") or "")
        if not inventory_key or not slot:
            return True, ""
        inventory = self.inventories.all().get(inventory_key)
        stock = inventory.count(slot) if inventory is not None else 0
        if stock > int(policy.get("min", 0) or 0):
            return False, "restock_not_needed"
        return True, ""

    def _restock_bonus(self, definition: ActionDefinition) -> float:
        """Deterministic pull toward replenishment, proportional to shortfall.

        ``0.2 + 0.4 * (target - stock) / target`` (max 0.6 on an empty slot) —
        large enough to beat the legacy "buy-and-nibble" pull, small enough
        that critical needs still dominate. 0.0 for non-restock actions.
        """
        policy = definition.restock
        if not policy:
            return 0.0
        inventory_key = str(policy.get("inventory", "") or "")
        slot = str(policy.get("slot", "") or "")
        target = int(policy.get("target", 0) or 0)
        if not inventory_key or not slot or target <= 0:
            return 0.0
        inventory = self.inventories.all().get(inventory_key)
        stock = inventory.count(slot) if inventory is not None else 0
        shortfall = (target - stock) / target
        return max(0.0, 0.2 + 0.4 * shortfall)

    def _objects_available(self, definition: ActionDefinition) -> bool:
        for object_id in definition.required_objects:
            if self.objects.get(object_id) is None:
                return False
        return True

    def _reachable_from(self, space_id: str, targets: list[str]) -> bool:
        """Walking to another home space is allowed; shops are handled by needs."""
        return any(target in self._home_spaces for target in targets) or "*" in targets

    def _penalty(self, definition: ActionDefinition) -> float:
        """Soft repetition penalty for this action right now (0.0 when absent)."""
        if self._recency_penalty is None:
            return 0.0
        try:
            return max(0.0, float(self._recency_penalty(definition)))
        except Exception:  # noqa: BLE001 - a broken provider must not break scoring
            return 0.0

    def _score(self, definition: ActionDefinition, *, hour: int) -> float:
        score = 0.0
        score += self.needs.weight_for(definition)  # need pressure
        score += self._preference_bonus.get(definition.id, 0.0)  # bible preference
        # Phase A: a favorite that was *just* done loses its crown; the penalty
        # decays with time and critical needs shrink it (see runtime).
        score -= self._penalty(definition)
        if definition.requires_absent:
            # It only appears when something she relies on ran out (§33/§175):
            # a real, deferrable pull — not a forced action.
            score += 0.5
        # Phase B: a procurement action is pulled by how far below its shelf
        # target the slot sits (the gate already limited it to stock ≤ min).
        score += self._restock_bonus(definition)
        deep_start, deep_end = DEEP_NIGHT_HOURS
        if deep_start <= hour < deep_end:
            # quiet/reflective actions fit the deep-night window
            score += 0.35 if "idle" in definition.tags else 0.0
        # A night-owl clock: real sleep pressure or the small hours.
        if definition.id == SLEEP_ACTION and (
            1 <= hour < 7 or self.needs.pressure("sleepiness") >= 0.6
        ):
            score += 0.8
        if definition.id == NAP_ACTION and deep_start <= hour < 7:
            score -= 0.5
        if definition.tags and "core" in definition.tags:
            score = max(score, self.needs.pressure("sleepiness") * 1.2)
        return round(score, 4)

    def _score_reasons(self, definition: ActionDefinition, *, hour: int) -> list[str]:
        reasons: list[str] = []
        deep_start, deep_end = DEEP_NIGHT_HOURS
        for key, relief in definition.need_relief.items():
            if self.needs.pressure(key) > 0.0 and relief > 0.0:
                reasons.append(f"need:{key}")
        if definition.id in self._preference_bonus:
            reasons.append("preference")
        if definition.restock and self.restock_gate(definition)[0]:
            reasons.append("restock")
        if self._penalty(definition) > 0.0:
            reasons.append("recent")
        if deep_start <= hour < deep_end and "idle" in definition.tags:
            reasons.append("deep_night")
        return reasons

    # ------------------------------------------------------------ decisions

    async def decide_next(
        self,
        *,
        space_id: str,
        current: ActionInstance | None,
        asleep: bool = False,
    ) -> tuple[SandboxDecision, DecisionTrace]:
        """Choose continue / switch after completion or at a decision point."""
        if asleep:
            definition = self.actions.definitions.get(SLEEP_ACTION)
            decision = SandboxDecision(
                decision="continue",
                action_id=SLEEP_ACTION,
                reason_codes=["asleep"],
            )
            trace = DecisionTrace(
                ts=float(self._clock()),
                kind="decision",
                summary="继续睡",
                reason_code="asleep",
            )
            assert definition is not None
            return decision, trace

        options = self.candidates(space_id=space_id)
        # Critical needs always win (§37). Sleep and other non-candidates
        # (they only exist under pressure) are searched here directly.
        for need in self.needs.critical():
            relief_actions = [
                definition
                for definition in self.actions.definitions.values()
                if definition.need_relief.get(need.key, 0.0) >= 0.4
                and self._objects_available(definition)
                and self._requirements_met(definition)
                and self.inventories.can_consume(definition.consumes)
                and self.rules.allows_action(definition.id)[0]
            ]
            if not relief_actions:
                continue
            pick = max(
                relief_actions,
                key=lambda definition: definition.need_relief.get(need.key, 0.0),
            )
            decision = SandboxDecision(
                decision="switch",
                action_id=pick.id,
                reason_codes=[f"critical:{need.key}"],
                factors={"relief": pick.need_relief.get(need.key, 0.0)},
            )
            trace = DecisionTrace(
                ts=float(self._clock()),
                kind="decision",
                summary=f"{need.key} 到临界 → {pick.name}",
                reason_code=f"critical:{need.key}",
                factors=[pick.id],
            )
            return decision, trace

        if not options:
            fallback = self.actions.definitions.get(IDLE_ACTION) or next(
                iter(self.actions.definitions.values())
            )
            decision = SandboxDecision(
                decision="switch",
                action_id=fallback.id,
                reason_codes=["no_options"],
            )
            trace = DecisionTrace(
                ts=float(self._clock()),
                kind="decision",
                summary=f"没得选 → {fallback.name}",
                reason_code="no_options",
            )
            return decision, trace

        top = options[0]
        runner_up = options[1] if len(options) > 1 else None
        ambiguous = (
            runner_up is not None
            and abs(top[1] - runner_up[1]) < 0.05
            and min(top[1], runner_up[1]) >= 0.4
            and top[0].id != runner_up[0].id
        )
        if ambiguous and self._ai is not None:
            return await self._ai_break_tie(space_id, options, current)

        definition, score, reasons = top
        if current is not None and definition.id == current.definition_id:
            decision = SandboxDecision(
                decision="extend",
                action_id=definition.id,
                reason_codes=["still_best", *reasons],
                factors={"score": score},
            )
            trace = DecisionTrace(
                ts=float(self._clock()),
                kind="decision",
                summary=f"继续 {definition.name}",
                reason_code="still_best",
                factors=reasons,
            )
            return decision, trace
        decision = SandboxDecision(
            decision="switch",
            action_id=definition.id,
            reason_codes=reasons or ["best_candidate"],
            factors={"score": score},
        )
        trace = DecisionTrace(
            ts=float(self._clock()),
            kind="decision",
            summary=f"转向 {definition.name}",
            reason_code=(reasons[0] if reasons else "best_candidate"),
            factors=reasons,
        )
        return decision, trace

    async def _ai_break_tie(
        self,
        space_id: str,
        options: list[tuple[ActionDefinition, float, list[str]]],
        current: ActionInstance | None,
    ) -> tuple[SandboxDecision, DecisionTrace]:
        """Optional AI tie-break: structured JSON validated against candidates."""
        payload = {
            "options": [definition.id for definition, _, _ in options[:5]],
            "current": current.definition_id if current else "",
            "space": space_id,
        }
        proposed = None
        try:
            proposed = self._ai(payload) if self._ai is not None else None
            if inspect.isawaitable(proposed):
                proposed = await proposed
        except Exception:  # noqa: BLE001 - AI failure must not stall the world
            proposed = None
        allowed = {
            definition.id: (definition, score, reasons) for definition, score, reasons in options
        }
        if isinstance(proposed, SandboxDecision) and proposed.action_id in allowed:
            definition, score, reasons = allowed[proposed.action_id]
            decision = SandboxDecision(
                decision=proposed.decision or "switch",
                action_id=definition.id,
                reason_codes=[*proposed.reason_codes, "ai_tiebreak"],
                factors={"score": score},
                via_ai=True,
            )
        else:
            definition, score, reasons = options[0]
            decision = SandboxDecision(
                decision="switch",
                action_id=definition.id,
                reason_codes=["deterministic_fallback", *reasons],
                factors={"score": score},
            )
        trace = DecisionTrace(
            ts=float(self._clock()),
            kind="decision",
            summary=f"歧义裁决 → {definition.name}",
            reason_code="ai_tiebreak" if decision.via_ai else "deterministic_fallback",
            factors=[definition.id],
        )
        return decision, trace


class InterruptEvaluator:
    """§54/§57/§94: should an external event interrupt the current action?"""

    def __init__(self, *, bible: CharacterBible, clock: Any) -> None:
        self.bible = bible
        self._clock = clock

    def evaluate(
        self,
        event: ExternalEvent,
        *,
        current: ActionInstance | None,
        definition: ActionDefinition | None,
    ) -> tuple[bool, str]:
        if current is None or definition is None:
            return False, "idle"
        interruptibility = definition.interruptibility
        priority = event.priority
        meaning = str(event.data.get("meaning", "message"))
        is_core = bool(event.data.get("is_core_friend"))

        # Core-friend game invitation outranks entertainment (§57/§150).
        if is_core and meaning == "invitation_game":
            play_tags = definition.tags
            if play_tags and ("game" in play_tags or "entertainment" in play_tags):
                return True, "core_friend_invitation"
            if interruptibility >= 0.3:
                return True, "core_friend_invitation"

        if priority == EventPriority.critical:
            return interruptibility < 0.9, "critical_event"
        if priority == EventPriority.high and interruptibility >= 0.6:
            return True, "high_priority_event"
        # Ordinary messages never stop what she is doing (§52/§146-§147).
        return False, "no_interrupt"


class ProposalValidator:
    """Last gate before state mutation (§125-§128)."""

    def __init__(self, rules: WorldRuleEngine, actions: ActionSystem) -> None:
        self.rules = rules
        self.actions = actions

    def validate(self, action_id: str, space_id: str) -> tuple[bool, str]:
        definition = self.actions.definitions.get(action_id)
        if definition is None:
            return False, "unknown_action"
        return self.rules.validate_proposal(action_id, space_id, definition)


def _hour_of(stamp: float) -> int:
    import time as _time

    return _time.localtime(float(stamp)).tm_hour
