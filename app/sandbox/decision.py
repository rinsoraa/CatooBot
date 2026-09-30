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

#: preference nudges from the bible (§180) — ids hard-anchored to the character
PREFERENCE_BONUS: dict[str, float] = {
    "play_minecraft": 0.35,
    "play_singleplayer": 0.2,
    "eat_pudding": 0.3,
    "eat_cake": 0.25,
    "eat_fruit": 0.2,
    "drink_cola": 0.3,
    "buy_sweets": 0.15,
    "film_cat": 0.15,
    "talk_to_cat": 0.1,
    "watch_animation": 0.15,
    "browse_social": 0.15,
}

#: deep-night actions get a nudge only inside the window
DEEP_NIGHT_BONUS = {"think": 0.35, "eat_pudding": 0.1}


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
        pet: PetSystem,
        rng: random.Random,
        clock: Any,
        ai_decider: Any = None,
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

    # ------------------------------------------------------------ candidates

    def candidates(
        self, *, space_id: str, asleep: bool = False
    ) -> list[tuple[ActionDefinition, float, list[str]]]:
        """All currently sensible actions with (definition, score, reasons)."""
        out: list[tuple[ActionDefinition, float, list[str]]] = []
        if asleep:
            definition = self.actions.definitions.get("sleep")
            if definition is not None:
                return [(definition, 1.0, ["asleep"])]
        hour = _hour_of(self._clock())
        sleep_pressure = self.needs.pressure("sleepiness")
        for action_id, definition in self.actions.definitions.items():
            if definition.id == "sleep" and not (
                sleep_pressure >= 0.6 or 1 <= hour < 7
            ):
                continue  # sleep appears at night or under real sleep pressure
            if definition.id == "nap" and self.needs.level("sleepiness") >= 0.9:
                continue  # not a substitute for the real thing
            if definition.id == "nap" and 2 <= hour < 7:
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
        """requires_absent: only sensible while the listed items are empty."""
        for key, items in definition.requires_absent.items():
            inventory = self.inventories.all().get(key)
            for item in items:
                if inventory is not None and inventory.count(item) > 0:
                    return False
        return True

    def _objects_available(self, definition: ActionDefinition) -> bool:
        for object_id in definition.required_objects:
            if self.objects.get(object_id) is None:
                return False
        return True

    def _reachable_from(self, space_id: str, targets: list[str]) -> bool:
        """Walking to another home space is allowed; shops are handled by needs."""
        allowed = {
            "kitchen", "livingroom", "bedroom", "bathroom", "entrance", "apartment",
        }
        return any(target in allowed for target in targets) or "*" in targets

    def _score(self, definition: ActionDefinition, *, hour: int) -> float:
        score = 0.0
        score += self.needs.weight_for(definition)              # need pressure
        score += PREFERENCE_BONUS.get(definition.id, 0.0)       # bible preference
        if definition.requires_absent:
            # It only appears when something she relies on ran out (§33/§175):
            # a real, deferrable pull — not a forced action.
            score += 0.5
        if 2 <= hour < 5:
            score += DEEP_NIGHT_BONUS.get(definition.id, 0.0)
        # The bible's clock: 凌晨三四点睡，白天补觉 (night_owl rule).
        if definition.id == "sleep" and (1 <= hour < 7 or self.needs.pressure("sleepiness") >= 0.6):
            score += 0.8
        if definition.id == "nap" and 2 <= hour < 7:
            score -= 0.5
        if definition.tags and "core" in definition.tags:
            score = max(score, self.needs.pressure("sleepiness") * 1.2)
        return round(score, 4)

    def _score_reasons(self, definition: ActionDefinition, *, hour: int) -> list[str]:
        reasons: list[str] = []
        for key, relief in definition.need_relief.items():
            if self.needs.pressure(key) > 0.0 and relief > 0.0:
                reasons.append(f"need:{key}")
        if definition.id in PREFERENCE_BONUS:
            reasons.append("preference")
        if 2 <= hour < 5 and definition.id in DEEP_NIGHT_BONUS:
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
            definition = self.actions.definitions.get("sleep")
            decision = SandboxDecision(
                decision="continue", action_id="sleep", reason_codes=["asleep"],
            )
            trace = DecisionTrace(
                ts=float(self._clock()), kind="decision",
                summary="继续睡", reason_code="asleep",
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
                decision="switch", action_id=pick.id,
                reason_codes=[f"critical:{need.key}"],
                factors={"relief": pick.need_relief.get(need.key, 0.0)},
            )
            trace = DecisionTrace(
                ts=float(self._clock()), kind="decision",
                summary=f"{need.key} 到临界 → {pick.name}",
                reason_code=f"critical:{need.key}", factors=[pick.id],
            )
            return decision, trace

        if not options:
            fallback = self.actions.definitions.get("idle") or next(
                iter(self.actions.definitions.values())
            )
            decision = SandboxDecision(
                decision="switch", action_id=fallback.id, reason_codes=["no_options"],
            )
            trace = DecisionTrace(
                ts=float(self._clock()), kind="decision",
                summary=f"没得选 → {fallback.name}", reason_code="no_options",
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
                decision="extend", action_id=definition.id,
                reason_codes=["still_best", *reasons], factors={"score": score},
            )
            trace = DecisionTrace(
                ts=float(self._clock()), kind="decision",
                summary=f"继续 {definition.name}", reason_code="still_best", factors=reasons,
            )
            return decision, trace
        decision = SandboxDecision(
            decision="switch", action_id=definition.id,
            reason_codes=reasons or ["best_candidate"], factors={"score": score},
        )
        trace = DecisionTrace(
            ts=float(self._clock()), kind="decision",
            summary=f"转向 {definition.name}",
            reason_code=(reasons[0] if reasons else "best_candidate"), factors=reasons,
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
            definition.id: (definition, score, reasons)
            for definition, score, reasons in options
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
                decision="switch", action_id=definition.id,
                reason_codes=["deterministic_fallback", *reasons],
                factors={"score": score},
            )
        trace = DecisionTrace(
            ts=float(self._clock()), kind="decision",
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
