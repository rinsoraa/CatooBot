"""Autonomous Life Loop & Goal Layer (v2.1 Phase 7 §4-§34).

    fact events → GoalDetector → Goal (deduped, persisted)
        → next legal step → Phase 6 Decision → ActionSystem → Mutation
        → Event → Goal progress → next step → completed

A **goal** answers "why keep doing this"; an **action** answers "what am I
doing right now". The layer is entirely deterministic (§3/§28): goals come
from world facts, steps come from the actions the seed actually owns, and the
LLM is only consulted through the Phase 6 decision pipeline when several
legal directions genuinely exist. Nothing here matches on item or character
names — restockability, pet care and project progress are all derived from
``ActionDefinition`` metadata and live world state (§10/§11/§41).
"""

from __future__ import annotations

import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.sandbox.events import SandboxEvent
from app.sandbox.events import SandboxEventType as ET


class GoalKind(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    restock_resource = "restock_resource"
    pet_care = "pet_care"
    complete_project = "complete_project"
    #: Phase 9: honour a promise made to someone — the execution side of a commitment
    fulfill_commitment = "fulfill_commitment"


class GoalStatus(str, Enum):  # noqa: UP042
    pending = "pending"
    active = "active"
    blocked = "blocked"
    completed = "completed"
    cancelled = "cancelled"
    expired = "expired"

    @property
    def terminal(self) -> bool:
        return self in (GoalStatus.completed, GoalStatus.cancelled, GoalStatus.expired)


class GoalSource(str, Enum):  # noqa: UP042 - §7
    need_critical = "need_critical"
    inventory_depleted = "inventory_depleted"
    project_milestone = "project_milestone"
    pet_need = "pet_need"
    external_event = "external_event"
    unfinished_task = "unfinished_task"
    scheduled_need = "scheduled_need"
    #: Phase 9: born from a live social commitment, never from a need
    social_commitment = "social_commitment"


class StepStatus(str, Enum):  # noqa: UP042
    pending = "pending"
    active = "active"
    completed = "completed"
    blocked = "blocked"
    skipped = "skipped"
    failed = "failed"


class GoalStep(BaseModel):
    """Only the *current* next step is materialized (§13)."""

    step_id: str
    goal_id: str
    kind: str = "action"
    status: StepStatus = StepStatus.pending
    action_id: str = ""
    #: the concrete ActionInstance this step is waiting for (§4/§5)
    action_instance_id: str = ""
    target: str = ""
    order: int = 0
    requirements: list[str] = Field(default_factory=list)
    result: dict[str, Any] = Field(default_factory=dict)


class Goal(BaseModel):
    """A persistent intention (§4) — world context, never an action (§6)."""

    goal_id: str
    character_id: str = ""
    kind: GoalKind
    status: GoalStatus = GoalStatus.pending
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    reason: str = ""
    source: GoalSource = GoalSource.unfinished_task
    source_event_id: str = ""
    causation_id: str = ""
    correlation_id: str = ""
    target_entity: str = ""
    target_space: str = ""
    target_item: str = ""
    target_project: str = ""
    #: the commitment this goal exists to honour (§15: reference, never a copy)
    target_commitment: str = ""
    #: restock bookkeeping (generic — never an item-name special case)
    metadata: dict[str, Any] = Field(default_factory=dict)
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    current_step: GoalStep | None = None
    #: bounded retries (§24): a blocked goal waits before trying again
    retry_count: int = 0
    last_attempt_at: float = 0.0
    next_eligible_at: float = 0.0
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def dedupe_key(self) -> str:
        """(character, kind, target) — the same shortage never stacks (§9).

        A commitment goal targets the *commitment id* (§16), so one promise can
        only ever hold one goal — no matter how many ticks it stays due.
        """
        target = (
            self.target_commitment or self.target_item or self.target_project or self.target_entity
        )
        return f"{self.character_id}|{self.kind.value}|{target}"


#: §27 deterministic priority bands (world data decides the target, not the band)
PRIORITY_PET_CRITICAL = 0.8
PRIORITY_RESOURCE_SHORTAGE = 0.6
PRIORITY_UNFINISHED_PROJECT = 0.4
#: how long a blocked goal waits before another attempt (§24)
RETRY_COOLDOWN_SECONDS = 900.0
#: a goal gives up after this many consecutive blocked attempts
MAX_RETRIES = 5


class GoalDetector:
    """Turns sandbox facts into goals (§8). Deterministic, event-driven.

    This layer only decides *that a persistent goal exists*; the immediate
    reactions (pet approaches → pet_care need bump, feeding interaction) keep
    their existing paths (§25) — the goal observes and tracks them.
    """

    def __init__(self, manager: GoalManager, *, clock: Any) -> None:
        self._manager = manager
        self._clock = clock

    # ------------------------------------------------------------------ bus

    def observe(self, event: SandboxEvent) -> None:
        payload = event.payload
        if event.event_type is ET.INVENTORY_DEPLETED:
            self._on_depleted(event, payload)
        elif event.event_type is ET.PET_HUNGRY:
            self._on_pet_hungry(event, payload)
        elif event.event_type is ET.PROJECT_PROGRESS_CHANGED:
            self._on_project(event, payload)

    # --------------------------------------------------------------- sweeps

    def _restockable_targets(self) -> list[tuple[str, str]]:
        """(inventory_key, item) pairs some owned action can replenish (§11).

        Derived from ``ActionDefinition.effects``: an item whose count ran to
        zero — and which therefore vanished from ``inventory.items`` — is
        still discovered, because the *world's* own actions say it can be
        replenished. No item names anywhere.
        """
        runtime = self._manager.runtime
        seen: dict[tuple[str, str], None] = {}
        for definition in runtime.actions.definitions.values():
            for effect_key, gain in definition.effects.items():
                if not effect_key.startswith("inventory:") or float(gain or 0.0) <= 0:
                    continue
                parts = effect_key.split(":", 2)
                if len(parts) == 3:
                    seen[(parts[1], parts[2])] = None
        return list(seen)

    def sweep(self) -> None:
        """Startup/restore scan: worlds that already have open business (§34).

        Depletion is judged per *restockable* item (world-derived), never by
        whether a zero-count key happens to remain in the inventory mapping.
        """
        runtime = self._manager.runtime
        for inventory_key, item in self._restockable_targets():
            if runtime.inventories.get(inventory_key).count(item) <= 0:
                self._create_restock(
                    item=item,
                    inventory_key=inventory_key,
                    source_event_id="",
                    source=GoalSource.unfinished_task,
                )
        for project_id, project in runtime.projects.items():
            if float(project.get("progress", 0.0)) < 1.0:
                self._create_project_goal(project_id, source_event_id="")
        if runtime.pet is not None and runtime.pet.hunger >= 0.7:
            self._create_pet_goal(source_event_id="")

    # ------------------------------------------------------------ detectors

    def _on_depleted(self, event: SandboxEvent, payload: dict[str, Any]) -> None:
        key = event.target_entity_id.replace("inventory:", "")
        item = str(payload.get("was", "") or "")
        if not item:
            return
        self._create_restock(
            item=item,
            inventory_key=key,
            source_event_id=event.event_id,
            source=GoalSource.inventory_depleted,
            correlation_id=event.correlation_id,
        )

    def _create_restock(
        self,
        *,
        item: str,
        inventory_key: str,
        source_event_id: str,
        source: GoalSource,
        correlation_id: str = "",
    ) -> str:
        acquisitions = self._manager.runtime.restock_actions(inventory_key, item)
        if not acquisitions:
            return ""  # nothing in this world replenishes it → not a goal (§10)
        desired = max(quantity for _action_id, _definition, quantity in acquisitions)
        return self._manager.create(
            kind=GoalKind.restock_resource,
            source=source,
            source_event_id=source_event_id,
            reason="inventory_depleted",
            priority=PRIORITY_RESOURCE_SHORTAGE,
            target_item=item,
            metadata={"inventory_key": inventory_key, "desired_quantity": desired},
            correlation_id=correlation_id,
        )

    def _on_pet_hungry(self, event: SandboxEvent, payload: dict[str, Any]) -> None:
        if self._manager.runtime.pet is None:
            return
        self._create_pet_goal(source_event_id=event.event_id, correlation_id=event.correlation_id)

    def _create_pet_goal(self, *, source_event_id: str, correlation_id: str = "") -> str:
        runtime = self._manager.runtime
        if runtime.pet is None:
            return ""
        return self._manager.create(
            kind=GoalKind.pet_care,
            source=GoalSource.pet_need,
            source_event_id=source_event_id,
            reason="pet_hungry",
            priority=PRIORITY_PET_CRITICAL,
            target_entity=runtime.pet.id,
            metadata={"threshold": 0.7},
            correlation_id=correlation_id,
        )

    def _on_project(self, event: SandboxEvent, payload: dict[str, Any]) -> None:
        if payload.get("completed"):
            self._manager.on_project_completed(event.target_entity_id)
            return
        self._create_project_goal(event.target_entity_id, source_event_id=event.event_id)

    def _create_project_goal(self, project_id: str, *, source_event_id: str) -> str:
        runtime = self._manager.runtime
        if not runtime.project_actions(project_id):
            return ""  # no owned action can advance it → no goal (§26)
        return self._manager.create(
            kind=GoalKind.complete_project,
            source=GoalSource.project_milestone,
            source_event_id=source_event_id,
            reason="unfinished_project",
            priority=PRIORITY_UNFINISHED_PROJECT,
            target_project=project_id,
            correlation_id="",
        )


class GoalManager:
    """Holds the character's goals and drives *one* of them at a time (§29)."""

    def __init__(self, runtime: Any, *, clock: Any) -> None:
        import logging

        self.runtime = runtime
        self._clock = clock
        self._log = logging.getLogger("CatooBot.Sandbox.Goal")
        self._goals: dict[str, Goal] = {}
        #: goals whose last write failed — retried by flush()
        self._dirty: set[str] = set()
        self.detected = 0
        self.steps_started = 0
        self.blocked = 0
        self._flushed_terminal: set[str] = set()

    # ------------------------------------------------------------- lifecycle

    async def restore(self) -> None:
        """Reload persisted goals and re-check each one against the world (§34)."""
        rows = await self.runtime.store.list_goals(character_id=self.runtime.character_id)
        for payload in rows:
            if not payload.get("current_step"):
                payload["current_step"] = None  # no materialized step
            goal = Goal.model_validate(payload)
            if goal.status.terminal:
                continue
            # a reloaded plan is not blindly trusted: its step must still work
            if goal.current_step is not None and goal.current_step.status in (
                StepStatus.pending,
                StepStatus.active,
            ):
                action_id = goal.current_step.action_id
                if action_id and action_id not in self.runtime.actions.definitions:
                    goal.current_step = None
                    goal.status = GoalStatus.pending
            self._goals[goal.goal_id] = goal

    # ------------------------------------------------------------------ api

    def all(self, *, statuses: set[GoalStatus] | None = None) -> list[Goal]:
        goals = list(self._goals.values())
        if statuses is not None:
            goals = [goal for goal in goals if goal.status in statuses]
        return goals

    def open_goals(self) -> list[Goal]:
        return [goal for goal in self._goals.values() if not goal.status.terminal]

    def create(
        self,
        *,
        kind: GoalKind,
        source: GoalSource,
        source_event_id: str,
        reason: str,
        priority: float,
        correlation_id: str = "",
        **targets: Any,
    ) -> str:
        """Create or update — never duplicate (§9). Returns the goal id."""
        runtime = self.runtime
        candidate = Goal(
            goal_id=f"goal_{uuid.uuid4().hex[:10]}",
            character_id=runtime.character_id,
            kind=kind,
            source=source,
            source_event_id=source_event_id,
            reason=reason,
            priority=priority,
            correlation_id=correlation_id,
            metadata=dict(targets.pop("metadata", {}) or {}),
            target_item=str(targets.get("target_item", "") or ""),
            target_entity=str(targets.get("target_entity", "") or ""),
            target_project=str(targets.get("target_project", "") or ""),
            target_commitment=str(targets.get("target_commitment", "") or ""),
            target_space=str(targets.get("target_space", "") or ""),
            created_at=float(self._clock()),
            updated_at=float(self._clock()),
        )
        key = candidate.dedupe_key
        existing = next((goal for goal in self._goals.values() if goal.dedupe_key == key), None)
        if existing is not None and not existing.status.terminal:
            existing.updated_at = float(self._clock())
            existing.priority = max(existing.priority, priority)
            if source_event_id:
                existing.source_event_id = source_event_id
            return existing.goal_id
        self._goals[candidate.goal_id] = candidate
        self.detected += 1
        self._publish(ET.GOAL_CREATED, candidate, reason=reason, extra={"kind": kind.value})
        return candidate.goal_id

    # ---------------------------------------------------------------- drives

    def active_goal(self) -> Goal | None:
        """The single goal allowed to drive actions right now (§29)."""
        now = float(self._clock())
        # terminal goals are out; blocked goals become eligible again once
        # their cooldown passed (§24: block → cooldown → re-evaluate)
        eligible = [
            goal
            for goal in self._goals.values()
            if not goal.status.terminal and goal.next_eligible_at <= now
        ]
        if not eligible:
            return None
        return sorted(eligible, key=lambda g: (-g.priority, g.created_at, g.goal_id))[0]

    def step_candidates(self, goal: Goal) -> list[Any]:
        """Legal actions that advance this goal — from the seed, never invented."""
        from app.sandbox.intent import CandidateKind, DecisionCandidate

        runtime = self.runtime
        pairs: list[tuple[str, Any, int]] = []
        if goal.kind is GoalKind.restock_resource:
            inventory_key = str(goal.metadata.get("inventory_key", ""))
            pairs = runtime.restock_actions(inventory_key, goal.target_item)
        elif goal.kind is GoalKind.pet_care:
            pairs = [
                (action_id, definition, 0) for action_id, definition in runtime.pet_care_actions()
            ]
        elif goal.kind is GoalKind.complete_project:
            pairs = [
                (action_id, definition, 0)
                for action_id, definition in runtime.project_actions(goal.target_project)
            ]
        elif goal.kind is GoalKind.fulfill_commitment:
            # §15: the *world* says which action can honour the activity; the
            # bridge only says the promise is due
            pairs = [
                (action_id, definition, 0)
                for action_id, definition in runtime.commitment_actions(
                    str(goal.metadata.get("target_activity", ""))
                )
            ]
        candidates = []
        for action_id, definition, _quantity in pairs:
            requirements: list[str] = []
            allowed, rule_reason = runtime.rules.allows_action(action_id)
            if not allowed:
                requirements.append(rule_reason or "rule_blocked")
            if not runtime.engine._objects_available(definition):  # noqa: SLF001
                requirements.append("objects_unavailable")
            if not runtime.inventories.can_consume(definition.consumes):
                requirements.append("requirements_unmet")
            if not runtime.engine._requirements_met(definition):  # noqa: SLF001
                requirements.append("requirements_unmet")
            candidates.append(
                DecisionCandidate(
                    candidate_id=f"action:{action_id}",
                    kind=CandidateKind.action,
                    action_id=action_id,
                    label=definition.name,
                    reason=f"goal:{goal.kind.value}",
                    requirements=requirements,
                    priority=goal.priority,
                )
            )
        return candidates

    async def advance(self, *, space_id: str = "") -> bool:
        """Try to move the active goal one step forward. True when it acted.

        Deterministic when a single step is legal; multiple legal steps go
        through the Phase 6 decision pipeline (§16/§28). A blocked or
        failed step cools down instead of spinning (§24).
        """
        runtime = self.runtime
        if runtime.current_action is not None:
            return False  # the running action *is* the step's execution
        goal = self.active_goal()
        if goal is None:
            return False
        candidates = self.step_candidates(goal)
        if not candidates:
            self._block(goal, "no_step_candidate")
            return False
        outcome = await runtime.decisions.decide(
            trigger=DecisionTrigger.goal_step,
            candidates=candidates,
            correlation_id=f"goal_{goal.goal_id}",
            causation_id=goal.source_event_id,
            context={
                "world": runtime.context().get("state_line", ""),
                "mode": runtime.modes.prompt_line(),
                "needs": runtime.needs.summary_line(),
                "goal": self.describe(goal),
            },
        )
        if not outcome.accepted or not outcome.action_id:
            self._block(goal, outcome.reason or "decision_rejected")
            return False
        stale = runtime.goal_precheck(goal)
        if stale:
            # Phase 9 §37/§38: the social state behind this goal moved (the
            # commitment was rescheduled, cancelled or broken) — the goal dies
            # instead of executing an arrangement that no longer exists
            self.cancel(goal, reason=stale)
            return False
        step = GoalStep(
            step_id=f"step_{uuid.uuid4().hex[:10]}",
            goal_id=goal.goal_id,
            action_id=outcome.action_id,
            target=goal.target_item
            or goal.target_project
            or goal.target_commitment
            or goal.target_entity,
            order=goal.current_step.order + 1 if goal.current_step else 0,
            requirements=[],
            result={"source": outcome.source},
        )
        if goal.status is GoalStatus.blocked:
            # §1: a cooldown-expired goal that became legal again is *active*
            # again — same goal id, same correlation, no clone
            goal.status = GoalStatus.active
            goal.next_eligible_at = 0.0
            self._publish(ET.GOAL_ACTIVATED, goal, reason="resumed_after_cooldown")
        elif goal.status is GoalStatus.pending:
            goal.status = GoalStatus.active
            self._publish(ET.GOAL_ACTIVATED, goal, reason="step_selected")
        goal.current_step = step
        goal.last_attempt_at = float(self._clock())
        goal.updated_at = float(self._clock())
        await self._persist(goal)
        self._publish(
            ET.GOAL_STEP_STARTED,
            goal,
            reason=step.action_id,
            extra={"step_id": step.step_id, "action_id": step.action_id},
        )
        started = await runtime._start_action(  # noqa: SLF001 - same aggregate
            outcome.action_id, reason=[f"goal:{goal.kind.value}"]
        )
        if started is None:
            step.status = StepStatus.failed
            self._block(goal, "action_not_started")
            return False
        step.status = StepStatus.active
        step.action_instance_id = started.id  # §5: first-class identity
        self.steps_started += 1
        # an errand destination step may itself complete the acquisition later
        await self._persist(goal)
        return True

    # -------------------------------------------------------------- progress

    def on_action_completed(self, action_id: str, *, action_instance_id: str = "") -> None:
        """A step's action finished (§6).

        Matching prefers the *instance* identity. The action-id fallback only
        applies when no instance id is available **and exactly one** pending
        step could match — two goals sharing an action definition must never
        both advance from a single completion.
        """
        matches: list[tuple[Goal, GoalStep]] = []
        for goal in self._goals.values():
            step = goal.current_step
            if step is None or step.status is StepStatus.completed:
                continue
            if (
                action_instance_id
                and step.action_instance_id == action_instance_id
                or (
                    not action_instance_id
                    and not step.action_instance_id
                    and step.action_id == action_id
                )
            ):
                matches.append((goal, step))
        if len(matches) != 1:
            return  # nothing to advance — or ambiguous, in which case do nothing
        goal, step = matches[0]
        step.status = StepStatus.completed
        self._publish(
            ET.GOAL_STEP_COMPLETED,
            goal,
            reason=action_id,
            extra={
                "step_id": step.step_id,
                "action_id": action_id,
                "action_instance_id": step.action_instance_id,
            },
        )
        self._evaluate(goal)

    async def rebind_instance(
        self, action_id: str, *, old_instance_id: str, new_instance_id: str
    ) -> bool:
        """Resume rebinding (§7/§28x): *only* an active step that was actually
        running the interrupted instance may be rebound.

        Strict on purpose — an interrupted action always has the instance id it
        was running under, so an empty ``old_instance_id`` is a caller bug, and
        a failed/blocked/pending step must never be silently adopted. Ambiguous
        matches are refused rather than guessed at (§5).

        The new binding is persisted *before returning*, so a crash right after
        resume cannot resurrect the stale instance id (§9-§13). A failed write
        keeps the goal dirty for the next flush — the running action is never
        rolled back and no second action is started (§12).
        """
        if not old_instance_id or not new_instance_id:
            self._log.warning(
                "[Goal] rebind refused: missing instance id (action=%s old=%r new=%r)",
                action_id,
                old_instance_id,
                new_instance_id,
            )
            return False
        matches: list[Goal] = []
        for goal in self._goals.values():
            step = goal.current_step
            if goal.status is not GoalStatus.active:
                continue
            if step is None or step.status is not StepStatus.active:
                continue
            if step.action_id != action_id:
                continue
            if step.action_instance_id != old_instance_id:
                continue
            matches.append(goal)
        if len(matches) != 1:
            self._log.warning(
                "[Goal] rebind refused: %d matching active step(s) for %s (old=%s)",
                len(matches),
                action_id,
                old_instance_id,
            )
            return False
        goal = matches[0]
        step = goal.current_step
        assert step is not None  # matched above
        step.action_instance_id = new_instance_id
        goal.updated_at = float(self._clock())
        written = await self._persist(goal)
        if not written:
            self._dirty.add(goal.goal_id)
            self._log.error(
                "[Goal] rebind of %s not persisted (goal=%s) — kept dirty for the "
                "next flush; the running action is untouched",
                action_id,
                goal.goal_id,
            )
        else:
            self._dirty.discard(goal.goal_id)
        return True

    def on_pet_fed(self, *, pet_id: str) -> None:
        for goal in self._goals.values():
            if goal.kind is not GoalKind.pet_care or goal.status.terminal:
                continue
            if goal.target_entity and pet_id and goal.target_entity != pet_id:
                continue
            self._complete(goal, reason="pet_fed")

    def on_item_acquired(self, *, inventory_key: str, item: str, total: int) -> None:
        for goal in self._goals.values():
            if goal.kind is not GoalKind.restock_resource or goal.status.terminal:
                continue
            if goal.metadata.get("inventory_key") != inventory_key:
                continue
            if goal.target_item and goal.target_item != item:
                continue
            self._evaluate(goal)

    def on_project_completed(self, project_id: str) -> None:
        for goal in self._goals.values():
            if goal.kind is not GoalKind.complete_project or goal.status.terminal:
                continue
            if goal.target_project == project_id:
                self._complete(goal, reason="project_completed")

    def _evaluate(self, goal: Goal) -> None:
        """Recompute progress from world state; complete when satisfied."""
        runtime = self.runtime
        if goal.kind is GoalKind.restock_resource:
            inventory_key = str(goal.metadata.get("inventory_key", ""))
            desired = int(goal.metadata.get("desired_quantity", 1) or 1)
            have = runtime.inventories.get(inventory_key).count(goal.target_item)
            goal.progress = min(1.0, have / max(1, desired))
            goal.updated_at = float(self._clock())
            if have >= desired:
                self._complete(goal, reason="restocked")
            else:
                self._publish(
                    ET.GOAL_PROGRESS,
                    goal,
                    reason="partial",
                    extra={"progress": round(goal.progress, 3)},
                )
        elif goal.kind is GoalKind.fulfill_commitment:
            # the goal's own progress is the commitment's honesty, decided by
            # the commitment layer — never re-derived from the world here
            return
        elif goal.kind is GoalKind.complete_project:
            project = runtime.projects.get(goal.target_project, {})
            goal.progress = float(project.get("progress", 0.0))
            goal.updated_at = float(self._clock())
            if goal.progress >= 1.0:
                self._complete(goal, reason="project_completed")
            else:
                self._publish(
                    ET.GOAL_PROGRESS,
                    goal,
                    reason="advanced",
                    extra={"progress": round(goal.progress, 3)},
                )

    def _complete(self, goal: Goal, *, reason: str) -> None:
        goal.status = GoalStatus.completed
        goal.progress = 1.0
        goal.updated_at = float(self._clock())
        if goal.current_step is not None and goal.current_step.status is not StepStatus.completed:
            goal.current_step.status = StepStatus.completed
        self._publish(ET.GOAL_COMPLETED, goal, reason=reason, extra={"goal_kind": goal.kind.value})

    def on_commitment_fulfilled(self, event: Any) -> None:
        """Bus handler: a kept promise closes its *exact* goal (Phase 9.1 §2-§6).

        Identity is the commitment id the goal was born on — never the person,
        never the activity, never a guess. Completion here is pure state
        closure: no new goal, no new step, no action, and the goal keeps its
        own ``goal_<id>`` correlation (only the causation points at the fact
        that fulfilled the promise).
        """
        if event.event_type is not ET.COMMITMENT_FULFILLED:
            return  # the bus is type-agnostic: only a kept promise closes a goal
        commitment_id = str(event.payload.get("commitment_id", "") or "")
        if not commitment_id:
            return
        for goal in self._goals.values():
            if goal.kind is not GoalKind.fulfill_commitment or goal.status.terminal:
                continue
            if (
                goal.target_commitment != commitment_id
                and str(goal.metadata.get("commitment_id", "") or "") != commitment_id
            ):
                continue
            goal.status = GoalStatus.completed
            goal.progress = 1.0
            goal.updated_at = float(self._clock())
            if goal.current_step is not None:
                goal.current_step.status = StepStatus.completed
            self._publish(
                ET.GOAL_COMPLETED,
                goal,
                reason="commitment_fulfilled",
                extra={"goal_kind": goal.kind.value, "commitment_id": commitment_id},
                causation_id=event.event_id,
            )
            return  # one promise, one goal (§16)

    def cancel(self, goal: Goal, *, reason: str) -> None:
        """Cancel a live goal (Phase 9 §37: a stale commitment goal dies here)."""
        goal.status = GoalStatus.cancelled
        goal.current_step = None
        goal.updated_at = float(self._clock())
        self._publish(ET.GOAL_CANCELLED, goal, reason=reason)

    def _block(self, goal: Goal, reason: str) -> None:
        goal.retry_count += 1
        goal.updated_at = float(self._clock())
        goal.next_eligible_at = float(self._clock()) + RETRY_COOLDOWN_SECONDS
        if goal.retry_count >= MAX_RETRIES:
            goal.status = GoalStatus.cancelled
            goal.current_step = None
            self._publish(ET.GOAL_CANCELLED, goal, reason=f"gave_up:{reason}")
            return
        goal.status = GoalStatus.blocked
        self.blocked += 1
        self._publish(
            ET.GOAL_BLOCKED,
            goal,
            reason=reason,
            extra={"retry_count": goal.retry_count, "cooldown": RETRY_COOLDOWN_SECONDS},
        )

    # ---------------------------------------------------------------- helpers

    def describe(self, goal: Goal) -> str:
        """Short neutral description for prompts/debug (never a person's name)."""
        if goal.kind is GoalKind.restock_resource:
            return f"补给 {goal.target_item}"
        if goal.kind is GoalKind.pet_care:
            return "照顾宠物"
        if goal.kind is GoalKind.complete_project:
            project = self.runtime.projects.get(goal.target_project, {})
            return f"继续 {project.get('name', goal.target_project)}"
        if goal.kind is GoalKind.fulfill_commitment:
            activity = str(goal.metadata.get("target_activity", "")) or "约定"
            return f"履行约定：{activity}"
        return goal.kind.value

    def _publish(
        self,
        event_type: ET,
        goal: Goal,
        *,
        reason: str,
        extra: dict[str, Any] | None = None,
        causation_id: str = "",
    ) -> Any:
        payload: dict[str, Any] = {
            "goal_id": goal.goal_id,
            "goal_kind": goal.kind.value,
            "status": goal.status.value,
            "reason": reason,
            "progress": round(goal.progress, 3),
            "description": self.describe(goal),
            "target": goal.target_item or goal.target_project or goal.target_entity,
        }
        payload.update(extra or {})
        return self.runtime.events.publish(
            event_type,
            source="character",
            target=goal.goal_id,
            payload=payload,
            causation_id=causation_id or goal.source_event_id,
            # a goal is its own ongoing thread: the originating fact is linked
            # through causation_id, but the chain (and therefore the resulting
            # experience) belongs to the goal, not to the trigger's action
            correlation_id=f"goal_{goal.goal_id}",
        )

    async def _persist(self, goal: Goal) -> bool:
        return bool(await self.runtime.store.save_goal(goal))

    async def flush(self) -> None:
        """Persist every open goal (bus handlers are sync; this rides the
        existing flush cadence). Terminal goals are written once and skipped;
        goals whose write failed earlier are retried and stay dirty."""
        for goal in list(self._goals.values()):
            if goal.status.terminal and goal.goal_id in self._flushed_terminal:
                continue
            written = await self._persist(goal)
            if written:
                self._dirty.discard(goal.goal_id)
            elif not goal.status.terminal:
                self._dirty.add(goal.goal_id)
            if goal.status.terminal and written:
                self._flushed_terminal.add(goal.goal_id)


# imported late to avoid a cycle with intent (which imports events only)
from app.sandbox.intent import DecisionTrigger  # noqa: E402
