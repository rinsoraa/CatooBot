"""SandboxRuntime (v2.0 §67-§74/§145-§146): the world that keeps living.

Tick (default 10 min) is CPU/DB only — no LLM unless a genuine ambiguity comes
up (§69/§193). External events wake it immediately (§70). Restart settles
through the gap bounded, never replaying every missed tick (§72). All state
mutations flow through ``_transaction`` and the only writer is this runtime
(§76/§77).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from collections.abc import Callable
from typing import Any

from app.sandbox.actions import ActionSystem
from app.sandbox.bible import CharacterBible
from app.sandbox.cognitive import CognitiveContext, CognitiveContextBuilder
from app.sandbox.continuity_snapshot import (
    ContinuitySnapshot,
    ContinuitySnapshotBuilder,
)
from app.sandbox.decision import (
    InterruptEvaluator,
    ProposalValidator,
    SandboxDecisionEngine,
)
from app.sandbox.definition import CharacterDefinition
from app.sandbox.entities import PetSystem
from app.sandbox.events import EventBus, SandboxEventType
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.experience import ExperienceBuilder
from app.sandbox.external import (
    ExternalEventQueue,
    ExternalInfluenceEvaluator,
    ExternalSource,
    ExternalWorldEvent,
    InfluenceAction,
)
from app.sandbox.external_adapters import from_legacy_event, legacy_meaning
from app.sandbox.goals import GoalDetector, GoalManager
from app.sandbox.intent import (
    CandidateKind,
    DecisionCandidate,
    DecisionCoordinator,
    DecisionTrigger,
)
from app.sandbox.interactions import InteractionResolver
from app.sandbox.memory_foundation import (
    MemoryCandidateBuilder,
    SandboxMemoryStore,
)
from app.sandbox.models import (
    ActionDefinition,
    ActionInstance,
    ActionStatus,
    CharacterEntity,
    Commission,
    DecisionTrace,
    EventLevel,
    EventSource,
    ExternalEvent,
    InterruptedActionContext,
    PetState,
    SandboxDecision,
    SandboxEventRecord,
    SandboxPhase,
    SandboxSnapshot,
    SocialSpace,
    SpaceNode,
    WorldObjectItem,
)
from app.sandbox.modes import ModeRuntime
from app.sandbox.mutations import MutationLog, MutationResult, StateMutation
from app.sandbox.needs import NeedSystem
from app.sandbox.relations import (
    InteractionSignificance,
    PersonIdentityResolver,
    RelationshipStore,
    RelationshipUpdateEngine,
    SocialInteractionFact,
    classify_significance,
)
from app.sandbox.seed import (
    build_action_definitions,
    build_inventories,
    build_needs,
    build_objects,
    build_projects,
    build_social_spaces,
    build_spaces,
    seeded_rng,
)
from app.sandbox.store import SandboxStore
from app.sandbox.world import (
    InventorySystem,
    ObjectSystem,
    SpaceSystem,
    WorldRuleEngine,
)
from app.sandbox.world_seed import build_world_seed
from app.utils.narrator import narrate

logger = logging.getLogger("CatooBot.Sandbox")

TickListener = Callable[[dict[str, Any]], None]


def seed_pets(runtime: Any) -> Any:
    """The runtime seed's first pet (or None) — helper for narrow uses."""
    pets = getattr(getattr(runtime, "seed", None), "pets", None)
    return pets[0] if pets else None


# ------------------------------------------------------- seed-derived helpers


def _pet_tags(pet: Any) -> list[str]:
    """Fact-injection tags for the pet: name + species + behavior keywords."""
    tags = [pet.name, pet.species]
    for behavior in pet.behaviors[:6]:
        for word in (behavior[:2], behavior[:3]):
            if word and word not in tags:
                tags.append(word)
                break
    return [tag for tag in tags if tag]


def _pet_food_name(seed: Any) -> str:
    return seed.pets[0].food_item if seed.pets else ""


def _locate_item(seed: Any, item: str) -> str:
    """The inventory key holding ``item`` (fridge / bowl / …), or ''."""
    if not item:
        return ""
    for key, items in seed.inventories.items():
        if item in items:
            return key
    return ""


def _first_action_with_tag(seed: Any, tag: str) -> str:
    for action in seed.actions:
        if tag in (action.get("tags") or []):
            return str(action["id"])
    return ""


def _gaming_action_ids(seed: Any) -> tuple[str, ...]:
    return tuple(
        str(action["id"]) for action in seed.actions if "gaming" in (action.get("modes") or [])
    )


def _preference_bonus(seed: Any) -> dict[str, float]:
    """§180: the bible's favorite actions get a deterministic nudge."""
    return {
        str(action["id"]): 0.3 if "favorite" in action.get("tags", []) else 0.15
        for action in seed.actions
        if "favorite" in action.get("tags", []) or "social" in action.get("tags", [])
    }


def _home_space_set(seed: Any) -> set[str]:
    """Home spaces = the home root plus its children (go-do-it reachability)."""
    home = seed.character.home_space
    ids = {home}
    ids.update(space.id for space in seed.spaces if space.parent == home)
    return ids


class SandboxRuntime:
    """The character's persistent life. One instance per bot."""

    def __init__(
        self,
        config: Any,
        store: SandboxStore,
        *,
        bible: CharacterBible,
        bot: Any = None,
        clock: Any = time.time,
        ai_decider: Any = None,
        ai_engine: Any = None,
        logger_: logging.Logger | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.bible = bible
        self.bot = bot
        self._clock = clock
        self._log = logger_ or logger
        #: the shared AI engine (decision layer + tie-break calls); a Phase 2
        #: ``ai_decider`` that wraps one is used as the fallback source.
        self.ai_engine = (
            ai_engine if ai_engine is not None else getattr(ai_decider, "_engine", None)
        )
        #: monotonic counter bumped by every *applied* mutation — a proposal
        #: built against an older revision is stale and must be re-validated (§20)
        self.world_revision = 0
        simulation_seed = int(getattr(config, "simulation_seed", 0))
        self._rng = seeded_rng(simulation_seed)

        # ------------------------------------------- definition + seed (§27)
        #: CharacterDefinition is the runtime contract; the seed is the full
        #: initial world. Both derive from the bible alone — no hardcoded
        #: character anywhere downstream (§6/§26/§27).
        self.definition = CharacterDefinition.from_bible(bible)
        self.seed = build_world_seed(self.definition, bible, simulation_seed=simulation_seed)
        seed = self.seed

        # ------------------------------------------------------------ state
        self.phase = SandboxPhase.initializing
        self.character = CharacterEntity(
            name=seed.character.name,
            location=seed.character.start_location,
            modes=list(seed.character.start_modes),
        )
        pet_seed = seed.pets[0] if seed.pets else None
        self.pet: PetState | None = (
            PetState(
                name=pet_seed.name,
                species=pet_seed.species,
                tags=_pet_tags(pet_seed),
                location=pet_seed.start_location or seed.character.start_location,
                habits=list(pet_seed.behaviors),
                owner_relationship="companion",
            )
            if pet_seed is not None
            else None
        )
        self.projects = build_projects(seed)
        self.social_spaces: dict[str, SocialSpace] = {}
        self.commissions: dict[str, Commission] = {}
        self.knowledge: dict[str, dict[str, Any]] = {}
        self.pending_events: list[ExternalEvent] = []
        #: §53: every state change is recorded with source/reason/before/after
        self.mutations = MutationLog()
        #: §14: one bus per runtime — events are facts, owned by this world
        self.events = EventBus(clock=clock)
        #: §11: kind-driven interaction resolver (what the world allows)
        self.interactions = InteractionResolver(self)
        #: §6/§9 (Phase 3): outside world → queue → influence → wakeup
        self.external_queue = ExternalEventQueue()
        self.influence = ExternalInfluenceEvaluator()
        #: §14: the paused action waiting to resume, if any
        self._interrupted: InterruptedActionContext | None = None
        #: PET_HUNGRY fires once per crossing, not every tick
        self._pet_hungry_signaled = False
        self._last_pet_hungry: Any = None
        # §6: reactions subscribe to facts — a begging pet raises pet_care.
        self.events.subscribe(self._on_pet_approached)
        # --------------------------------------------- memory foundation (§4)
        #: the isolation boundary a memory belongs to (bible-content stable)
        self.character_id = f"{seed.character.name}@{bible.source_hash[:8]}"
        #: SandboxEvent → Experience (noise-filtered, one per behaviour chain)
        self.experiences = ExperienceBuilder(self, clock=clock)
        self.events.subscribe(self.experiences.observe)
        #: Experience → candidate → memories row (existing table, no embedding)
        self.candidates = MemoryCandidateBuilder()
        self.memory = SandboxMemoryStore(
            store.database, character_id=self.character_id, clock=clock, logger=self._log
        )
        #: read model only — never feeds decision/persona/speech (§25)
        self.continuity_snapshot = ContinuitySnapshotBuilder(self, clock=clock)
        #: Phase 5 bridge: read-only cognitive context for chat turns
        self.cognition = CognitiveContextBuilder(self, clock=clock)
        #: Phase 6 decision layer: gate → request → model → validate → events
        self.decisions = DecisionCoordinator(self)
        self.mutations.on_record = self._bump_world_revision
        #: Phase 8 social layer: relationship dynamics (character-scoped)
        self.relationships_dyn = RelationshipStore(self)
        self.persons = PersonIdentityResolver(self)
        self.social = RelationshipUpdateEngine(self)
        #: an accepted invitation waiting for its shared-activity outcome (§19)
        self._accepted_invitation: dict[str, Any] | None = None
        #: Phase 7 goal layer: why she keeps doing something (§4-§34)
        self.goals = GoalManager(self, clock=clock)
        self.goal_detector = GoalDetector(self.goals, clock=clock)
        self.events.subscribe(self.goal_detector.observe)
        self.events.subscribe(self._on_goal_facts)
        self._last_tick = float(clock())
        self._notes: list[str] = []  # micro-continuity feed
        self._restored = False
        self._private_chat_until = 0.0  # online-social window after a chat
        #: narrative-on-every-tick switch (logging.narrate_world_ticks)
        self.narrate_ticks = False
        #: optional ContinuityManager — meaningful events feed recent_events
        self.continuity: Any = None
        #: in-memory ring of recent events (works without a database too)
        self._recent_memory: list[SandboxEventRecord] = []
        #: optional async (activity, location, energy) -> None projection into
        #: CharacterState so WebUI/prompts/timing all read the same life.
        self.state_sync: Any = None
        #: seed-derived anchors: where her food/drink live, what she comes home to
        self._default_location = seed.character.start_location
        self._home_space = seed.character.home_space
        food_name = _pet_food_name(seed)
        self._pet_food: tuple[str, str] | None = (
            (_locate_item(seed, food_name), food_name) if food_name else None
        )
        self._drink_item = seed.anchors.get("drink", "")
        self._fridge_key = _locate_item(seed, self._drink_item)
        self._gaming_action_id = _first_action_with_tag(seed, "game")

        # ------------------------------------------------------------ systems
        self.spaces = SpaceSystem(build_spaces(seed))
        self.objects = ObjectSystem(build_objects(seed))
        self.inventories = InventorySystem(build_inventories(seed))
        self.needs = NeedSystem(build_needs(seed), clock=clock, labels=seed.need_labels)
        self.actions = ActionSystem(build_action_definitions(seed), rng=self._rng, clock=clock)
        self.rules = WorldRuleEngine(bible)
        self.modes = ModeRuntime(clock=clock, definitions=seed.modes)
        self.pet_system: PetSystem | None = (
            PetSystem(
                self.pet,
                rng=self._rng,
                clock=clock,
                nap_spots=self._nap_spots(),
                gaming_action_ids=self._gaming_action_ids(),
            )
            if self.pet is not None
            else None
        )
        self.interrupt = InterruptEvaluator(bible=bible, clock=clock)
        self.validator = ProposalValidator(self.rules, self.actions)
        self.social_spaces = build_social_spaces(seed)
        self.engine = SandboxDecisionEngine(
            bible=bible,
            actions=self.actions,
            needs=self.needs,
            spaces=self.spaces,
            objects=self.objects,
            inventories=self.inventories,
            rules=self.rules,
            modes=self.modes,
            pet=self.pet_system,
            rng=self._rng,
            clock=clock,
            ai_decider=(
                self.decisions.wrap_tie_breaker(ai_decider)
                if ai_decider is not None and getattr(config, "allow_ai_decisions", True)
                else None
            ),
            preference_bonus=_preference_bonus(seed),
            home_spaces=_home_space_set(seed),
        )
        if ai_decider is not None and hasattr(ai_decider, "set_character_context"):
            try:
                ai_decider.set_character_context(
                    name=seed.character.name,
                    traits=self.definition.personality.traits,
                    pet_name=seed.pets[0].name if seed.pets else "",
                )
            except Exception:  # noqa: BLE001 - cosmetic context only
                pass
        self.current_action: ActionInstance | None = None

    # ------------------------------------------------------------------ api

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    async def start(self) -> None:
        """Load persisted state; recover through any downtime (§72)."""
        restored = await self._restore()
        now = float(self._clock())
        if restored:
            elapsed_min = max(0.0, (now - self._last_tick) / 60.0)
            if elapsed_min >= 1.0:
                self.phase = SandboxPhase.recovering
                await self._settle_gap(elapsed_min)
        else:
            await self._seed_fresh()
        # §20: pending external events survive a restart; consumed ids too (§19)
        await self._restore_external_state()
        # §34: goals survive a restart; each is re-checked against the world,
        # and the world's existing open business is swept once
        await self.goals.restore()
        self.goal_detector.sweep()
        await self.goals.flush()
        # §28: the bible's relationships are the canonical starting states
        self.relationships_dyn.seed_initial()
        await self.relationships_dyn.seed_persisted()
        self.phase = SandboxPhase.running
        await self.store.state_set("phase", self.phase.value)
        self._derive_modes()
        await self._sync_state()

    async def shutdown(self) -> None:
        self.phase = SandboxPhase.stopped
        await self.wait_social()
        await self.flush_experiences()
        await self.build_continuity()
        await self._persist_all()
        await self.store.state_set("phase", self.phase.value)

    # ---------------------------------------------------------------- seed

    async def _seed_fresh(self) -> None:
        """First run of a new character: seed → initial world (§120)."""
        self._log.info(
            "[Sandbox] seeding a fresh world from %s (character=%s, pet=%s)",
            self.bible.version,
            self.character.name,
            self.pet.name if self.pet else "-",
        )
        self.character.location = self._default_location
        if self.pet is not None:
            self.pet.location = self._default_location
        await self.store.save_bible(self.bible)
        await self.store.state_set("bible_version", self.bible.version)
        await self._persist_all()
        await self._append_event(
            "sandbox_initialized",
            "世界初始化完成（根据人物档案）",
            source=EventSource.system,
            level=EventLevel.major,
            reason="bible_seed",
        )

    def _nap_spots(self) -> list[str]:
        """Pet nap spots that exist in *this* world's spaces."""
        if not seed_pets(self):
            return []
        known = {space.id for space in self.spaces.all()}
        spots = list(seed_pets(self).nap_spots)
        return [spot for spot in spots if spot in known] or [self._default_location]

    def _gaming_action_ids(self) -> tuple[str, ...]:
        """Owned actions tagged with the gaming mode (seed-derived)."""
        gaming_modes = {
            mode.get("id")
            for mode in self.seed.modes
            if mode.get("trigger_kind") == "action" and "游戏" in str(mode.get("trigger", ""))
        }
        return tuple(
            action["id"]
            for action in self.seed.actions
            if gaming_modes & set(action.get("modes") or [])
        )

    async def reinitialize(self) -> None:
        """Admin/Rebuild path (§155): wipe runtime, seed from the bible again."""
        await self._seed_fresh()
        self.phase = SandboxPhase.running

    # ------------------------------------------------------------ tick loop

    def _on_pet_approached(self, event: Any) -> None:
        """Bus handler: a hungry pet approaching bumps her care need (§177)."""
        if event.event_type is SandboxEventType.PET_APPROACHED:
            self._adjust_need("pet_care", 0.25, source="pet_action", reason="pet_approached")

    def adjust_need(
        self,
        key: str,
        *,
        delta: float = 0.0,
        relieve: float = 0.0,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Canonical need change (§3.5): mutation → NEED_CHANGED, or nothing.

        Two semantics, both recorded identically:

        * ``delta``   — absolute pressure change (action cost, stimulus);
        * ``relieve`` — proportional relief of the *remaining* pressure,
          matching :meth:`NeedSystem.relieve` (action completion).
        """
        before = self.needs.level(key)
        if delta:
            self.needs.add(key, delta)
        if relieve:
            self.needs.relieve({key: relieve})
        after = self.needs.level(key)
        if after == before:
            return MutationResult(ok=True)
        mutation = self.mutations.record(
            StateMutation(
                target=f"needs:{key}",
                field="level",
                before=before,
                after=after,
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.NEED_CHANGED,
            source=source,
            target=key,
            payload={
                "before": before,
                "after": after,
                "source": source,
                "reason": reason,
            },
            causation_id=causation_id,
            correlation_id=correlation,
        )
        return MutationResult(ok=True, mutations=[mutation])

    def _adjust_need(self, key: str, delta: float, *, source: str, reason: str) -> None:
        """Convenience alias (Phase 3 call sites): absolute delta, no causation."""
        self.adjust_need(key, delta=delta, source=source, reason=reason)

    def _note_need_drift(self, before_bands: dict[str, str]) -> None:
        """Time-driven drift (tick/settle): band crossings become facts (§3.5).

        Continuous level drift itself is time evolution, not a business
        mutation — but when a need enters a new band (soft/strong/critical)
        that *is* a world fact worth recording (payload carries both levels).
        """
        for key, band_after in self.needs.bands().items():
            band_before = before_bands.get(key, band_after)
            if band_before == band_after:
                continue
            need = self.needs.get(key)
            self.events.publish(
                SandboxEventType.NEED_CHANGED,
                source="time_advance",
                target=key,
                payload={
                    "before": need.level if need else 0.0,
                    "after": need.level if need else 0.0,
                    "band_before": band_before,
                    "band_after": band_after,
                    "source": "time_advance",
                    "reason": "time_advance",
                },
            )

    def update_project_progress(
        self,
        project_id: str,
        *,
        delta: float,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Canonical project progress (§4): mutation → PROJECT_PROGRESS_CHANGED.

        Completion (crossing into progress == 1.0) is reported in the same
        event's payload — no second project-event system.
        """
        project = self.projects.get(project_id)
        if project is None:
            return MutationResult(ok=False, error="unknown_project")
        before = float(project.get("progress", 0.0))
        after = max(0.0, min(1.0, before + delta))
        if after == before:
            return MutationResult(ok=True)
        project["progress"] = after
        completed = before < 1.0 <= after
        if completed:
            project["status"] = "completed"
        mutation = self.mutations.record(
            StateMutation(
                target=f"project:{project_id}",
                field="progress",
                before=before,
                after=after,
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.PROJECT_PROGRESS_CHANGED,
            source=source,
            target=project_id,
            payload={
                "before": before,
                "after": after,
                "delta": round(after - before, 6),
                "completed": completed,
                "source": source,
                "reason": reason,
            },
            causation_id=causation_id,
            correlation_id=correlation,
        )
        return MutationResult(ok=True, mutations=[mutation])

    def set_knowledge(
        self,
        key: str,
        *,
        known: bool = True,
        source: str,
        reason: str,
        data: dict[str, Any] | None = None,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Canonical knowledge write (§5): mutation → KNOWLEDGE_CHANGED.

        ``learned_at`` / ``source`` stay in the entry payload as before; the
        before/after pair is the entry itself, so a future Memory/Continuity
        pass can tell *when* she learned what and why.
        """
        before = self.knowledge.get(key)
        entry = {
            "known": bool(known),
            "source": source,
            "learned_at": float(self._clock()),
            "data": dict(data or {}),
        }
        if before == entry:
            return MutationResult(ok=True)
        self.knowledge[key] = entry
        mutation = self.mutations.record(
            StateMutation(
                target=f"knowledge:{key}",
                field="entry",
                before=before,
                after=dict(entry),
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.KNOWLEDGE_CHANGED,
            source=source,
            target=key,
            payload={
                "key": key,
                "before": before,
                "after": dict(entry),
                "source": source,
                "reason": reason,
            },
            causation_id=causation_id,
            correlation_id=correlation,
        )
        return MutationResult(ok=True, mutations=[mutation])

    async def tick(self, *, minutes: float | None = None) -> dict[str, Any]:
        """One sandbox tick (§68). Returns a small report for logs/tests."""
        if self.phase not in (SandboxPhase.running, SandboxPhase.degraded):
            return {"skipped": self.phase.value}
        step = minutes if minutes is not None else self._tick_minutes()
        now = float(self._clock())
        bands_before = self.needs.bands()
        self.needs.advance(step, rest=self._rest_coefficient())
        self._note_need_drift(bands_before)
        pet_events: list[tuple[str, str]] = []
        if self.pet_system is not None:
            pet_events = self.pet_system.tick(
                minutes=step,
                owner_space=self.character.location,
                owner_action=self.current_action.definition_id if self.current_action else "",
                owner_home=self.spaces.is_home(self.character.location),
                food_available=self._pet_food_available(),
            )
        for kind, line in pet_events:
            await self._append_event(
                "pet",
                line,
                source=EventSource.pet_action,
                level=EventLevel.micro,
                reason=kind,
            )
        # §7 Case A: hunger crossing becomes a fact event; the *reaction*
        # (need bump) rides the bus as a handler, not an inline if.
        if self.pet is not None:
            if self.pet.hunger >= 0.7 and not self._pet_hungry_signaled:
                self._pet_hungry_signaled = True
                hungry = self.events.publish(
                    SandboxEventType.PET_HUNGRY,
                    source=self.pet.id,
                    payload={"hunger": self.pet.hunger},
                )
                self._last_pet_hungry = hungry
            elif self.pet.hunger < 0.5:
                self._pet_hungry_signaled = False
            if any(kind == "hungry_approach" for kind, _l in pet_events):
                self.events.publish(
                    SandboxEventType.PET_APPROACHED,
                    source=self.pet.id,
                    target="character",
                    payload={"line": pet_events[0][1]},
                    causation_id=(self._last_pet_hungry.event_id if self._last_pet_hungry else ""),
                    correlation_id=(
                        self._last_pet_hungry.correlation_id if self._last_pet_hungry else ""
                    ),
                )

        report: dict[str, Any] = {"minutes": step, "pet": [line for _k, line in pet_events]}
        self._last_tick = now

        # Action lifecycle.
        if self.current_action is not None:
            self.current_action.progress = self.actions.progress(self.current_action, now=now)
            if self.actions.is_due(self.current_action, now=now):
                await self._complete_action()
                report["completed"] = True

        # Critical needs / no action → decision point. A goal with a legal
        # next step drives first (§15); otherwise the existing engine decides.
        need_decision = self.current_action is None or self.needs.critical()
        if need_decision:
            drove = await self.goals.advance(space_id=self.character.location)
            if not drove:
                await self._decide_and_apply(space_id=self.character.location)
            report["decided"] = True
            report["goal_drove"] = drove

        self._derive_modes()
        # derived projection: `needs:energy` is the writer; this mirror is
        # recomputed every tick (not an independent state change, §53 audit)
        self.character.energy = max(0.0, min(1.0, 1.0 - self.needs.level("energy")))
        await self._process_pending_events()
        if self._should_snapshot():
            await self._snapshot()
        await self._persist_deltas()
        await self.goals.flush()
        await self.flush_experiences()
        if self.narrate_ticks:
            pressing = self.needs.summary_line() if self.needs.pressing() else ""
            detail = "沙盒心跳" + (f"（{pressing}）" if pressing else "")
            narrate().world(f"{self.status_line()}  ·  {'+'.join(self.modes.ids())}", detail=detail)
        return report

    def _tick_minutes(self) -> float:
        if self._last_tick <= 0:
            return max(1.0, float(self.config.tick_seconds) / 60.0)
        elapsed = max(0.0, (float(self._clock()) - self._last_tick) / 60.0)
        cap = max(1.0, float(self.config.tick_seconds) / 60.0 * 2)
        return max(1.0, min(cap, elapsed))

    # ------------------------------------------------------------ actions

    def _pet_food_available(self) -> bool:
        """Is there pet food in its bowl? (key + item both come from the seed)"""
        if not self._pet_food:
            return False
        key, item = self._pet_food
        return self.inventories.get(key).count(item) > 0

    # ------------------------------------------ mutation helpers (§6/§52)

    def take_item(
        self,
        inventory_key: str,
        item: str,
        *,
        quantity: int = 1,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Consume N items — the only sanctioned inventory-consumption path.

        Emits ITEM_CONSUMED per call and INVENTORY_DEPLETED when the
        container runs empty, with before/after mutations recorded (§53).
        """
        inventory = self.inventories.get(inventory_key)
        if inventory.count(item) < quantity:
            return MutationResult(ok=False, error="not_enough_items")
        before = inventory.count(item)
        inventory.take(item, quantity)
        after = inventory.count(item)
        mutation = self.mutations.record(
            StateMutation(
                target=f"inventory:{inventory_key}",
                field=item,
                before=before,
                after=after,
                source=source,
                reason=reason,
            )
        )
        consumed = self.events.publish(
            SandboxEventType.ITEM_CONSUMED,
            source=source,
            target=f"inventory:{inventory_key}",
            payload={"item": item, "quantity": quantity, "left": after},
            causation_id=causation_id,
            correlation_id=correlation,
        )
        event_ids = [consumed.event_id] if consumed else []
        depleted = None
        if inventory.count(item) <= 0:
            # *this item* ran out ("the last bottle" — Phase 7 Case A); the
            # payload also says whether the container itself is now empty
            depleted = self.events.publish(
                SandboxEventType.INVENTORY_DEPLETED,
                source=source,
                target=f"inventory:{inventory_key}",
                payload={"was": item, "container_empty": not inventory.items},
                causation_id=causation_id or (consumed.event_id if consumed else ""),
                correlation_id=correlation or (consumed.correlation_id if consumed else ""),
            )
            if depleted:
                event_ids.append(depleted.event_id)
        return MutationResult(ok=True, mutations=[mutation], event_ids=event_ids)

    def acquire_item(
        self,
        inventory_key: str,
        item: str,
        *,
        quantity: int = 1,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Counterpart of take_item: items enter the world (shopping effects)."""
        inventory = self.inventories.get(inventory_key)
        before = inventory.count(item)
        inventory.add(item, quantity)
        mutation = self.mutations.record(
            StateMutation(
                target=f"inventory:{inventory_key}",
                field=item,
                before=before,
                after=inventory.count(item),
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.ITEM_ACQUIRED,
            source=source,
            target=f"inventory:{inventory_key}",
            payload={"item": item, "quantity": quantity, "total": inventory.count(item)},
            causation_id=causation_id,
            correlation_id=correlation,
        )
        return MutationResult(ok=True, mutations=[mutation])

    def move_entity(
        self,
        *,
        entity_id: str,
        to_space: str,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Canonical movement (§9): validate, mutate, ENTITY_MOVED/REJECTED.

        Feature code must never assign ``character.location`` directly —
        this gate owns existence + reachability validation.
        """
        requested = self.events.publish(
            SandboxEventType.ENTITY_MOVE_REQUESTED,
            source=entity_id,
            target=to_space,
            payload={"reason": reason},
            causation_id=causation_id,
            correlation_id=correlation,
        )
        parent = causation_id or (requested.event_id if requested else "")
        if self.spaces.get(to_space) is None:
            self._record_rejected(
                target="character.location",
                field="location",
                source=source,
                reason="move_rejected:unknown_space",
            )
            self.events.publish(
                SandboxEventType.ENTITY_MOVE_REJECTED,
                source=entity_id,
                target=to_space,
                payload={"reason": "unknown_space"},
                causation_id=parent,
                correlation_id=correlation or (requested.correlation_id if requested else ""),
            )
            return MutationResult(ok=False, error="unknown_space")
        if to_space != self.character.location and not self._space_enterable(
            self.character.location, to_space
        ):
            self._record_rejected(
                target="character.location",
                field="location",
                source=source,
                reason="move_rejected:not_reachable",
            )
            self.events.publish(
                SandboxEventType.ENTITY_MOVE_REJECTED,
                source=entity_id,
                target=to_space,
                payload={"reason": "not_reachable"},
                causation_id=parent,
                correlation_id=correlation or (requested.correlation_id if requested else ""),
            )
            return MutationResult(ok=False, error="not_reachable")
        before = self.character.location
        self.character.location = to_space
        mutation = self.mutations.record(
            StateMutation(
                target="character.location",
                field="location",
                before=before,
                after=to_space,
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.ENTITY_MOVED,
            source=entity_id,
            target=to_space,
            payload={"from": before, "reason": reason},
            causation_id=parent,
            correlation_id=correlation or (requested.correlation_id if requested else ""),
        )
        return MutationResult(ok=True, mutations=[mutation])

    def _record_rejected(
        self, *, target: str, field: str, source: str, reason: str
    ) -> StateMutation:
        """§53: a refused change is auditable too (state untouched, ok=False)."""
        return self.mutations.record(
            StateMutation(
                target=target,
                field=field,
                before=None,
                after=None,
                source=source,
                reason=reason,
                ok=False,
            )
        )

    def set_character_field(
        self,
        field: str,
        value: Any,
        *,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Character scalar state (current_action_id / focus / …) — always recorded."""
        before = getattr(self.character, field, None)
        if before == value:
            return MutationResult(ok=True)
        setattr(self.character, field, value)
        mutation = self.mutations.record(
            StateMutation(
                target=f"character.{field}",
                field=field,
                before=before,
                after=value,
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.CHARACTER_FIELD_CHANGED,
            target=f"character.{field}",
            payload={"before": before, "after": value, "reason": reason},
            causation_id=causation_id,
            correlation_id=correlation,
        )
        return MutationResult(ok=True, mutations=[mutation])

    def update_social_space(
        self,
        space_id: str,
        *,
        presence: str = "",
        temperature_delta: float = 0.0,
        source: str,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> MutationResult:
        """Canonical social-space change: mutation → SOCIAL_SPACE_CHANGED.

        External influence (and anything else) must call this instead of
        assigning ``character_presence`` / ``social_temperature`` directly.
        """
        social = self.social_spaces.get(space_id)
        if social is None:
            return MutationResult(ok=False, error="unknown_social_space")
        mutations: list[StateMutation] = []
        if presence and social.character_presence != presence:
            before = social.character_presence
            social.character_presence = presence
            mutations.append(
                self.mutations.record(
                    StateMutation(
                        target=f"social_space:{space_id}",
                        field="character_presence",
                        before=before,
                        after=presence,
                        source=source,
                        reason=reason,
                    )
                )
            )
        if temperature_delta:
            before_t = float(social.social_temperature)
            after_t = max(0.0, min(1.0, before_t + temperature_delta))
            if after_t != before_t:
                social.social_temperature = after_t
                mutations.append(
                    self.mutations.record(
                        StateMutation(
                            target=f"social_space:{space_id}",
                            field="social_temperature",
                            before=before_t,
                            after=after_t,
                            source=source,
                            reason=reason,
                        )
                    )
                )
        if mutations:
            self.events.publish(
                SandboxEventType.SOCIAL_SPACE_CHANGED,
                target=space_id,
                payload={
                    "fields": [m.field for m in mutations],
                    "presence": social.character_presence,
                    "temperature": round(float(social.social_temperature), 4),
                    "reason": reason,
                },
                causation_id=causation_id,
                correlation_id=correlation,
            )
        return MutationResult(ok=True, mutations=mutations)

    def _space_enterable(self, from_space: str, to_space: str) -> bool:
        """Graph reachability (multi-hop: errands walk through the world)."""
        return self.spaces.is_reachable(from_space, to_space)

    def feed_pet(
        self, *, source: str, reason: str, correlation: str = "", causation_id: str = ""
    ) -> bool:
        """Case A tail: consume pet food, relieve hunger, emit PET_FED.

        Generic on the seed's (bowl inventory, food item) — no names here.
        """
        if self.pet_system is None or self.pet is None or not self._pet_food:
            return False
        key, item = self._pet_food
        taken = self.take_item(
            key,
            item,
            quantity=1,
            source=source,
            reason=reason,
            correlation=correlation,
            causation_id=causation_id,
        )
        if not taken.ok:
            return False
        before = self.pet.hunger
        self.pet_system.feed()
        self.mutations.record(
            StateMutation(
                target=f"pet:{self.pet.id}",
                field="hunger",
                before=before,
                after=self.pet.hunger,
                source=source,
                reason=reason,
            )
        )
        self.events.publish(
            SandboxEventType.PET_FED,
            source=source,
            target=self.pet.id,
            payload={"food": item, "hunger_after": self.pet.hunger},
            causation_id=(taken.event_ids[-1] if taken.event_ids else causation_id),
            correlation_id=correlation,
        )
        return True

    def apply_object_effect(
        self,
        effect_key: str,
        value: float,
        *,
        reason: str,
        correlation: str = "",
        causation_id: str = "",
    ) -> None:
        """Object-state effects emit OBJECT_STATE_CHANGED (§6)."""
        self.objects.apply_effect(effect_key, value)
        rest = effect_key.partition(":")[2]
        obj_id, _, obj_field = rest.partition(".")
        obj = self.objects.get(obj_id)
        self.events.publish(
            SandboxEventType.OBJECT_STATE_CHANGED,
            target=obj_id,
            payload={"field": obj_field, "value": obj.state.get(obj_field) if obj else None},
            causation_id=causation_id,
            correlation_id=correlation,
        )

    async def _complete_action(self) -> None:
        action = self.current_action
        if action is None:
            return
        definition = self.actions.definition(action)
        if definition is None:
            action.status = ActionStatus.completed
            self.current_action = None
            return

        correlation = f"act_{action.id}"

        def apply() -> None:
            self.actions.finish(action, ActionStatus.completed)
            # §11 (3.5): every effect below hangs off this event — one chain,
            # no orphan facts.
            applied = self.events.publish(
                SandboxEventType.ACTION_EFFECT_APPLIED,
                source="character",
                target=definition.id,
                payload={
                    "need_relief": definition.need_relief,
                    "need_cost": definition.need_cost,
                    "consumes": definition.consumes,
                },
                correlation_id=correlation,
            )
            cause = applied.event_id if applied else ""

            def effect_context() -> dict[str, Any]:
                return {"correlation": correlation, "causation_id": cause}

            # consumption goes through the mutation path (ITEM_CONSUMED…)
            for inv_key, items in definition.consumes.items():
                for item, count in items.items():
                    if count > 0:
                        self.take_item(
                            inv_key,
                            item,
                            quantity=count,
                            source="character_action",
                            reason=definition.id,
                            **effect_context(),
                        )
                    elif count < 0:
                        # negative = "must exist, not consumed" (e.g. pet food)
                        pass
            # needs: relief and cost both go through adjust_need (§3.5)
            for key, amount in definition.need_relief.items():
                self.adjust_need(
                    key,
                    relieve=amount,
                    source="character_action",
                    reason=definition.id,
                    **effect_context(),
                )
            for key, amount in definition.need_cost.items():
                self.adjust_need(
                    key,
                    delta=amount,
                    source="character_action",
                    reason=definition.id,
                    **effect_context(),
                )
            for effect_key, value in definition.effects.items():
                if effect_key.startswith("object:"):
                    self.apply_object_effect(
                        effect_key,
                        value,
                        reason=definition.id,
                        **effect_context(),
                    )
                elif effect_key.startswith("inventory:"):
                    _, inv_key, item = effect_key.split(":", 2)
                    self.acquire_item(
                        inv_key,
                        item,
                        quantity=int(value),
                        source="character_action",
                        reason=definition.id,
                        **effect_context(),
                    )
                elif effect_key.startswith("pet:"):
                    # §18: generic pet effects — the seed decides what exists
                    self.feed_pet(
                        source="character_action",
                        reason=definition.id,
                        **effect_context(),
                    )
                elif effect_key.startswith("project:"):
                    name = effect_key.split(":", 1)[1]
                    self.update_project_progress(
                        name,
                        delta=float(value),
                        source="character_action",
                        reason=definition.id,
                        **effect_context(),
                    )
                elif effect_key.startswith("commission:"):
                    self._advance_commission(float(value))
            if definition.destination:
                # she walked home after the errand (§30: space affects behavior)
                self.move_entity(
                    entity_id="character",
                    to_space=self._default_location,
                    source="character_action",
                    reason=definition.id,
                    **effect_context(),
                )
            if definition.consumes:
                # eating/drinking from a container → she knows its stock now
                container = next(iter(definition.consumes), "")
                self.set_knowledge(
                    f"{container}_stock",
                    source="observation",
                    reason=definition.id,
                    **effect_context(),
                )
            # her staple drink ran out → replenishment awareness (Case B tail)
            if (
                self._fridge_key
                and self._drink_item
                and self.inventories.get(self._fridge_key).count(self._drink_item) == 0
            ):
                self.set_knowledge(
                    f"{self._fridge_key}_empty_{self._drink_item}",
                    source="observation",
                    reason=definition.id,
                    **effect_context(),
                )

        await self._transaction(apply, label=f"complete:{action.definition_id}")
        # the completion's experiences reach the store right away (§28-1)
        self.events.publish(
            SandboxEventType.ACTION_COMPLETED,
            source="character",
            target=definition.id,
            payload={
                "action_id": definition.id,
                "action_instance_id": action.id,
                "detail": action.detail,
                "reason": "natural_completion",
            },
            correlation_id=correlation,
        )
        await self._persist_deltas()
        await self._append_event(
            "action_completed",
            f"{definition.name}结束" + (f"（{action.detail}）" if action.detail else ""),
            source=EventSource.character_action,
            level=EventLevel.normal if definition.typical_minutes >= 20 else EventLevel.micro,
            reason="natural_completion",
        )
        self._notes.append(f"{definition.name}做完了")
        pending = self._accepted_invitation
        if pending is not None and pending.get("action_id") == definition.id:
            self._accepted_invitation = None
            fact = SocialInteractionFact.create(  # §19: the activity *did* happen
                character_id=self.character_id,
                person_id=str(pending["person_id"]),
                interaction_type="shared_activity",
                source="character_action",
                timestamp=float(self._clock()),
                outcome="completed",
            )
            task = asyncio.create_task(
                self.apply_social_interaction(
                    fact, correlation_id=str(pending.get("correlation", ""))
                )
            )
            self._track_background(task)
        self.current_action = None
        await self.store.save_action(action)
        await self._resume_interrupted()
        await self._sync_state()

    async def _resume_interrupted(self) -> str:
        """§15: pick the paused action back up — a *new* lifecycle, honestly.

        Skipped when a critical need now outranks the old plan (the context is
        dropped instead of pretending nothing happened). The remaining time is
        restored from the saved context, not re-rolled.
        """
        context = self._interrupted
        if context is None or not context.resumable:
            return ""
        self._interrupted = None
        if self.current_action is not None or self.needs.critical():
            return ""
        definition = self.actions.definitions.get(context.definition_id)
        if definition is None:
            return ""
        requested = self.events.publish(
            SandboxEventType.ACTION_REQUESTED,
            source="character",
            target=context.definition_id,
            payload={
                "reason": "resume_after_interrupt",
                "remaining_minutes": context.remaining_minutes,
            },
        )
        instance = await self._start_action(
            context.definition_id,
            reason=[f"resume:{context.interrupt_reason}"],
            duration_minutes=context.remaining_minutes,
        )
        if instance is None:
            return ""
        # §7/§9: resume creates a new lifecycle — the waiting goal step follows
        # and the new binding is persisted before this returns
        await self.goals.rebind_instance(
            context.definition_id,
            old_instance_id=context.action_id,
            new_instance_id=instance.id,
        )
        self.events.publish(
            SandboxEventType.ACTION_RESUMED,
            source="character",
            target=context.definition_id,
            payload={
                "remaining_minutes": round(context.remaining_minutes, 1),
                "interrupt_reason": context.interrupt_reason,
            },
            causation_id=requested.event_id if requested else "",
        )
        await self._append_event(
            "action_resumed",
            f"回到刚才没做完的{definition.name}",
            source=EventSource.character_action,
            level=EventLevel.micro,
            reason="resume_after_interrupt",
        )
        return context.definition_id

    async def _decide_and_apply(self, *, space_id: str) -> SandboxDecision:
        decision, trace = await self.engine.decide_next(
            space_id=space_id, current=self.current_action, asleep=self._is_asleep()
        )
        if decision.decision in ("switch", "interrupt") and decision.action_id:
            current_definition = self.current_action.definition_id if self.current_action else ""
            # Already doing the right thing (a critical need can re-fire every
            # tick): let it run to completion instead of restarting it.
            if (
                decision.action_id == current_definition
                and self.current_action is not None
                and not self.actions.is_due(self.current_action)
            ):
                return decision
            requested = self.events.publish(
                SandboxEventType.ACTION_REQUESTED,
                source="character",
                target=decision.action_id,
                payload={"space": space_id, "reason_codes": decision.reason_codes},
            )
            ok, reason = self.validator.validate(decision.action_id, space_id)
            if not ok:
                trace.factors.append(f"rejected:{reason}")
                self.events.publish(
                    SandboxEventType.ACTION_FAILED,
                    source="character",
                    target=decision.action_id,
                    payload={"reason": reason},
                    causation_id=requested.event_id if requested else "",
                )
                decision = SandboxDecision(
                    decision="continue",
                    action_id=decision.action_id,
                    reason_codes=["validator_rejected", reason],
                )
            else:
                await self._start_action(decision.action_id, reason=decision.reason_codes[:1])
        if self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            if (
                decision.decision == "extend"
                and definition is not None
                and self.actions.can_extend(self.current_action, definition)
            ):
                extra = self.actions.extend(self.current_action, definition)
                if extra:
                    trace.factors.append(f"extended:{int(extra)}m")
        await self.store.append_trace(trace)
        return decision

    async def _start_action(
        self,
        action_id: str,
        *,
        reason: list[str] | None = None,
        space_id: str | None = None,
        duration_minutes: float | None = None,
    ) -> ActionInstance | None:
        definition = self.actions.definitions.get(action_id)
        if definition is None:
            return None
        target_space = space_id or self._space_for(definition)
        urgency = max(
            (self.needs.pressure(key) for key in definition.need_relief),
            default=0.0,
        )
        if definition.destination:
            target_space = definition.destination
        instance = self.actions.start(
            definition,
            space_id=target_space,
            reason_code=(reason[0] if reason else "decision"),
            urgency=urgency,
            duration_minutes=duration_minutes,
        )
        correlation = f"act_{instance.id}"
        # §9/remediation: movement first, through the canonical gate only —
        # a rejected move fails the action instead of teleporting her there.
        if target_space != self.character.location:
            moved = self.move_entity(
                entity_id="character",
                to_space=target_space,
                source="character_action",
                reason=action_id,
                correlation=correlation,
            )
            if not moved.ok:
                instance.status = ActionStatus.blocked
                self.events.publish(
                    SandboxEventType.ACTION_FAILED,
                    source="character",
                    target=action_id,
                    payload={"reason": f"move_rejected:{moved.error}", "space": target_space},
                    correlation_id=correlation,
                )
                self._log.warning(
                    "[Sandbox] action %s not started: cannot reach %s (%s)",
                    action_id,
                    target_space,
                    moved.error,
                )
                return None
        await self._transaction(
            lambda: setattr(self, "current_action", instance), label=f"start:{action_id}"
        )
        self.set_character_field(
            "current_action_id",
            instance.id,
            source="character_action",
            reason=action_id,
            correlation=correlation,
        )
        self.events.publish(
            SandboxEventType.ACTION_STARTED,
            source="character",
            target=action_id,
            payload={
                "action_id": action_id,
                "action_instance_id": instance.id,
                "space": target_space,
                "detail": instance.detail,
            },
            correlation_id=correlation,
        )
        await self.store.save_action(instance)
        await self._sync_state()
        if definition.typical_minutes >= 20:
            await self._append_event(
                "action_started",
                f"开始{definition.name}" + (f"（{instance.detail}）" if instance.detail else ""),
                source=EventSource.character_action,
                level=EventLevel.micro,
                reason=instance.reason_code,
            )
            narrate().world(
                f"{definition.name}" + (f"·{instance.detail}" if instance.detail else ""),
                detail=f"在{self.spaces.name(target_space)}",
            )
        return instance

    def _space_for(self, definition: ActionDefinition) -> str:
        if "*" in definition.spaces:
            return self.character.location
        if self.character.location in definition.spaces:
            return self.character.location
        return definition.spaces[0] if definition.spaces else self.character.location

    # ------------------------------------------------------- external events

    # ---------------------------------------------- external world (§4-§13)

    def _bump_world_revision(self, mutation: Any) -> None:
        # relationship/social bookkeeping is state, but it cannot change what
        # an action requires or does — a pending decision stays valid
        if not getattr(mutation, "affects_world", True):
            return
        self.world_revision += 1

    # ---------------------------------------------------- goal layer (§7)

    def _on_goal_facts(self, event: Any) -> None:
        """Bus handler: world facts advance/complete goals (sync; flushed later)."""
        payload = event.payload
        if event.event_type is SandboxEventType.ACTION_COMPLETED:
            self.goals.on_action_completed(
                str(event.target_entity_id),
                action_instance_id=str(payload.get("action_instance_id", "") or ""),
            )
        elif event.event_type is SandboxEventType.PET_FED:
            self.goals.on_pet_fed(pet_id=str(event.target_entity_id))
        elif event.event_type is SandboxEventType.ITEM_ACQUIRED:
            self.goals.on_item_acquired(
                inventory_key=str(event.target_entity_id).replace("inventory:", ""),
                item=str(payload.get("item", "")),
                total=int(payload.get("total", 0) or 0),
            )

    def restock_actions(self, inventory_key: str, item: str) -> list[Any]:
        """Owned actions whose effects replenish (key, item) — seed-derived.

        This is the whole definition of "restockable" (§10): if no action in
        this world adds the item back, it simply is not a goal.
        """
        needle = f"inventory:{inventory_key}:{item}"
        result = []
        for action_id, definition in self.actions.definitions.items():
            gain = float(definition.effects.get(needle, 0.0) or 0.0)
            if gain > 0:
                result.append((action_id, definition, int(gain)))
        return result

    def pet_care_actions(self) -> list[Any]:
        """Owned actions that act on the pet (effects key ``pet:*``)."""
        return [
            (action_id, definition)
            for action_id, definition in self.actions.definitions.items()
            if any(key.startswith("pet:") for key in definition.effects)
        ]

    def project_actions(self, project_id: str) -> list[Any]:
        """Owned actions that advance this project (effects key ``project:<id>``)."""
        needle = f"project:{project_id}"
        return [
            (action_id, definition)
            for action_id, definition in self.actions.definitions.items()
            if needle in definition.effects
        ]

    def _invitation_candidates(self, activity: str, *, from_core: bool) -> list[DecisionCandidate]:
        """The legal options for an activity invitation — world-derived (§5).

        Priorities encode the *trigger's* strength (a core friend's invitation
        outranks continuing; a stranger's does not), so the deterministic
        fallback needs no model to stay in character.
        """
        candidates: list[DecisionCandidate] = []
        if self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            label = definition.name if definition is not None else "正在做的事"
            candidates.append(
                DecisionCandidate(
                    candidate_id="continue_current_action",
                    kind=CandidateKind.continue_current,
                    label=f"继续{label}",
                    reason="keep_running_action",
                    priority=0.5,
                )
            )
        action_id = self._action_for_activity(activity)
        if action_id:
            definition = self.actions.definitions[action_id]
            requirements: list[str] = []
            allowed, rule_reason = self.rules.allows_action(definition.id)
            if not allowed:
                requirements.append(rule_reason or "rule_blocked")
            if not self.engine._objects_available(definition):  # noqa: SLF001
                requirements.append("objects_unavailable")
            if not self.inventories.can_consume(definition.consumes):
                requirements.append("requirements_unmet")
            if not self.engine._requirements_met(definition):  # noqa: SLF001
                requirements.append("requirements_unmet")
            candidates.append(
                DecisionCandidate(
                    candidate_id=f"action:{action_id}",
                    kind=CandidateKind.action,
                    action_id=action_id,
                    label=definition.name,
                    reason="accept_invitation" if from_core else "join_activity",
                    requirements=requirements,
                    priority=0.85 if from_core else 0.6,
                )
            )
        candidates.append(
            DecisionCandidate(
                candidate_id="postpone_invitation",
                kind=CandidateKind.postpone,
                label="先不理会，按原计划来",
                reason="keep_own_plan",
                priority=0.4,
            )
        )
        return candidates

    async def _run_invitation_decision(
        self,
        *,
        activity: str,
        from_core: bool,
        actor: str,
        reason_code: str,
        received_id: str,
        correlation: str,
    ) -> dict[str, Any]:
        """§14: invitation → decision pipeline → (maybe) interrupt + action.

        Thin orchestration only: the coordinator decides and validates, this
        method executes the accepted outcome through the normal action path.
        """
        candidates = self._invitation_candidates(activity, from_core=from_core)
        relationship = await self.relationship_for(actor)
        context = {
            "world": self.context().get("state_line", ""),
            "mode": self.modes.prompt_line(),
            "needs": self.needs.summary_line(),
            "goal": self.context().get("goal", ""),
            "invitation": {"actor": actor, "activity": activity, "reason": reason_code},
            "relationship": self.relationships_dyn.describe(relationship) if relationship else "",
        }
        if relationship is not None:
            # §21/§22: closeness nudges *desirability* — the gate still decides
            bonus = min(0.15, relationship.closeness * 0.15 + relationship.trust * 0.05)
            for candidate in candidates:
                if candidate.kind is CandidateKind.action:
                    candidate.priority = min(1.0, candidate.priority + bonus)
        outcome = await self.decisions.decide(
            trigger=DecisionTrigger.external_invitation,
            candidates=candidates,
            correlation_id=correlation,
            causation_id=received_id,
            context=context,
        )
        result: dict[str, Any] = {
            "interrupt": False,
            "decision": outcome.model_dump(),
        }
        if not outcome.accepted or outcome.kind != CandidateKind.action.value:
            # §19/§20: declining is a fact of its own (a small comfort dip)
            self._record_invitation_outcome(
                actor=actor, declined=True, correlation=correlation, causation=received_id
            )
            return result
        self.events.publish(
            SandboxEventType.ACTION_REQUESTED,
            source="character",
            target=outcome.action_id,
            payload={"reason": f"decision:{outcome.source}", "space": self.character.location},
            causation_id=received_id,
            correlation_id=correlation,
        )
        if self.current_action is not None:
            await self._interrupt_action(reason_code, resumable=True)
        started = await self._start_action(outcome.action_id, reason=[f"decision:{outcome.source}"])
        if started is None:
            return result
        # §19/§33: acceptance is recorded, the shared activity only after it runs
        self._record_invitation_outcome(
            actor=actor, declined=False, correlation=correlation, causation=received_id
        )
        self._accepted_invitation = {
            "person_id": self.persons.for_qq(actor).person_id,
            "action_id": outcome.action_id,
            "correlation": correlation,
        }
        result["interrupt"] = True
        result["action"] = outcome.action_id
        self.events.publish(
            SandboxEventType.WORLD_EXTERNAL_INFLUENCE,
            source="external",
            target=outcome.action_id,
            payload={
                "actor": actor,
                "semantic_kind": "activity_invitation",
                "reason": reason_code,
                "decision_source": outcome.source,
            },
            causation_id=received_id,
            correlation_id=correlation,
        )
        return result

    def _record_invitation_outcome(
        self, *, actor: str, declined: bool, correlation: str, causation: str
    ) -> None:
        person = self.persons.for_qq(actor)
        fact = SocialInteractionFact.create(
            character_id=self.character_id,
            person_id=person.person_id,
            interaction_type="invitation_declined" if declined else "invitation_accepted",
            source="character_decision",
            timestamp=float(self._clock()),
            outcome="declined" if declined else "accepted",
            significance=InteractionSignificance.meaningful,
        )
        task = asyncio.create_task(
            self.apply_social_interaction(fact, correlation_id=correlation, causation_id=causation)
        )
        self._track_background(task)

    def _activity_index(self) -> set[str]:
        """Activities this world can engage — read from ActionDefinition (§18).

        No id→activity mapping lives here: the seed's action templates declare
        ``activity`` and the runtime only aggregates what it finds.
        """
        return {
            definition.activity
            for definition in self.actions.definitions.values()
            if definition.activity
        }

    def _action_for_activity(self, activity: str) -> str:
        """First owned action that declares this activity (seed-driven)."""
        if not activity:
            return ""
        for action_id, definition in self.actions.definitions.items():
            if definition.activity == activity:
                return action_id
        return ""

    async def submit_external(self, event: ExternalWorldEvent) -> bool:
        """Queue an outside fact (§9). Duplicate event_ids are refused (§19)."""
        accepted = self.external_queue.push(event)
        if accepted:
            await self._persist_external_state()
        return accepted

    async def wakeup(self) -> list[dict[str, Any]]:
        """§8: evaluate queued external events *now* instead of waiting for tick.

        Drains highest-urgency-first; each event flows through the same
        influence pipeline. Never mutates state directly — every effect goes
        through the Phase 2 mutation/event spine.
        """
        results: list[dict[str, Any]] = []
        while True:
            event = self.external_queue.pop()
            if event is None:
                break
            results.append(await self._process_external(event))
        if results:
            await self._persist_external_state()
        return results

    async def handle_external(self, event: ExternalEvent) -> dict[str, Any]:
        """Phase-2 compatibility entry: adapt → same pipeline as ``wakeup``."""
        world_event = from_legacy_event(event)
        self.external_queue.note_processed(world_event.event_id)
        result = await self._process_external(world_event)
        event.handled = True
        return result

    async def notify(self, event: ExternalEvent) -> dict[str, Any]:
        """Legacy alias: submit → immediate wakeup (Phase 3 semantics)."""
        world_event = from_legacy_event(event)
        queued = await self.submit_external(world_event)
        if not queued:
            return {"queued": False, "reason": "duplicate_event"}
        results = await self.wakeup()
        return results[0] if results else {"queued": True}

    # ------------------------------------------------------ the pipeline

    async def _process_external(self, event: ExternalWorldEvent) -> dict[str, Any]:
        """Influence → (wake/interrupt) → existing Action/Mutation spine (§7)."""
        definition = self.actions.definition(self.current_action)
        decision = self.influence.evaluate(
            event,
            available_activities=self._activity_index(),
            has_action=self.current_action is not None,
            interruptibility=definition.interruptibility if definition else 1.0,
        )
        received = self.events.publish(
            SandboxEventType.EXTERNAL_EVENT_RECEIVED,
            source=event.source.value,
            target=event.actor_id,
            payload={
                "event_type": event.event_type,
                "semantic_kind": event.semantic_kind,
                "urgency": event.urgency.value,
                "influence": decision.action.value,
                "reason": decision.reason_code,
                "content": event.content[:80],
            },
            correlation_id=event.correlation_id,
        )
        correlation = received.correlation_id if received else ""
        result: dict[str, Any] = {
            "meaning": legacy_meaning(event),
            "reason": decision.reason_code,
            "interrupt": False,
            "why": decision.reason_code,
            "influence": decision.action.value,
            "event_id": event.event_id,
        }
        if decision.action is InfluenceAction.REJECT:
            self.events.publish(
                SandboxEventType.EXTERNAL_EVENT_REJECTED,
                source=event.source.value,
                target=event.actor_id,
                payload={"reason": decision.reason_code, "semantic_kind": event.semantic_kind},
                causation_id=received.event_id if received else "",
                correlation_id=correlation,
            )
            await self._trace_external(event, decision, correlation)
            return result

        # accepted: the outside fact becomes a stimulus (§52 — never a command)
        self._apply_influence_effects(event, decision, correlation)

        if decision.action is InfluenceAction.INTERRUPT:
            # §14: the influence layer says "this matters"; the decision layer
            # (candidates → gate → model → validator) decides what happens.
            outcome = await self._run_invitation_decision(
                activity=decision.target_activity,
                from_core=event.actor_relationship == "core_friend",
                actor=event.actor_id,
                reason_code=decision.reason_code,
                received_id=received.event_id if received else "",
                correlation=correlation,
            )
            result["interrupt"] = bool(outcome.get("interrupt"))
            result["decision"] = outcome.get("decision")
            if outcome.get("action"):
                result["action"] = outcome["action"]
        elif (
            decision.action is InfluenceAction.WAKE
            and self.current_action is None
            and self.phase in (SandboxPhase.running, SandboxPhase.degraded)
        ):
            # §8: the wake is the *evaluation*, not a forced action; the
            # deterministic decision engine still chooses what (if anything).
            await self._decide_and_apply(space_id=self.character.location)

        await self._trace_external(event, decision, correlation)
        return result

    def _apply_influence_effects(
        self, event: ExternalWorldEvent, decision: Any, correlation: str
    ) -> None:
        """Non-action consequences of an accepted external fact."""
        if event.source is ExternalSource.qq:
            self._private_chat_until = float(self._clock()) + 1800.0
            familiar = bool(
                event.metadata.get("familiar", event.actor_relationship == "core_friend")
            )
            self._record_qq_interaction(event, familiar=familiar)
            self._adjust_need(
                "social_need",
                -0.08 if familiar else -0.04,
                source=event.source.value,
                reason="external_stimulus",
            )
            self._touch_social_space(
                str(event.metadata.get("social_space_id", "") or ""),
                str(event.metadata.get("group_id", "") or ""),
            )
        if event.event_type == "delivery_arrived":
            package_id = next(
                (obj.id for obj in self.objects.all() if obj.id.startswith("package")),
                "",
            )
            if package_id:
                self.apply_object_effect(
                    f"object:{package_id}.present",
                    1.0,
                    reason="delivery_arrived",
                    correlation=correlation,
                )
            self._adjust_need(
                "household_maintenance",
                0.05,
                source=event.source.value,
                reason="delivery_arrived",
            )

    def _record_qq_interaction(self, event: Any, *, familiar: bool) -> None:
        """QQ message → deterministic SocialInteractionFact (§9/§17/§18).

        Trivial small talk is classified as such and moves nothing; an
        invitation becomes a ``game_invitation`` fact — never a bond by itself
        (§19/§33: the invite is not the acceptance).
        """
        person = self.persons.for_qq(
            event.actor_id, display_name=str(event.metadata.get("display_name", "") or "")
        )
        interaction_type = (
            "game_invitation"
            if event.semantic_kind in ("game_invitation", "activity_invitation")
            else "message_received"
        )
        significance = classify_significance(
            event.content, interaction_type=interaction_type, core_person=familiar
        )
        fact = SocialInteractionFact.create(
            character_id=self.character_id,
            person_id=person.person_id,
            interaction_type=interaction_type,
            source="qq",
            timestamp=float(event.timestamp or self._clock()),
            social_space_id=str(event.metadata.get("social_space_id", "") or ""),
            significance=significance,
            metadata={"display_name": person.display_name, **person.external_ids},
        )
        task = asyncio.create_task(  # never blocks the influence pipeline
            self.apply_social_interaction(fact, correlation_id=event.correlation_id or "")
        )
        self._track_background(task)

    def _track_background(self, task: Any) -> None:
        tasks = getattr(self, "_social_tasks", None)
        if tasks is None:
            tasks = set()
            self._social_tasks = tasks
        tasks.add(task)
        task.add_done_callback(tasks.discard)

    async def wait_social(self) -> None:
        """Test/shutdown helper: let pending interaction facts land."""
        tasks = getattr(self, "_social_tasks", None)
        if tasks:
            await asyncio.gather(*list(tasks), return_exceptions=True)

    async def _trace_external(
        self, event: ExternalWorldEvent, decision: Any, correlation: str
    ) -> None:
        await self.store.append_trace(
            DecisionTrace(
                ts=float(self._clock()),
                kind="external_event",
                summary=(event.content or event.event_type)[:80],
                reason_code=decision.reason_code,
                factors=[
                    f"source:{event.source.value}",
                    f"urgency:{event.urgency.value}",
                    f"influence:{decision.action.value}",
                    *([f"semantic:{event.semantic_kind}"] if event.semantic_kind else []),
                ],
            )
        )
        await self._persist_deltas()
        await self.flush_experiences()

    def _touch_social_space(self, space_id: str, group_id: str) -> None:
        """A QQ message marks presence where it landed (generic, id-driven)."""
        target = space_id if space_id and space_id in self.social_spaces else ""
        if not target and group_id:
            candidate = f"qq:{group_id}"
            if candidate in self.social_spaces:
                target = candidate
        if target:
            self.update_social_space(
                target,
                presence="active",
                temperature_delta=0.05,
                source="external_event",
                reason="external_stimulus",
            )

    # ------------------------------------------------- external persistence

    async def _persist_external_state(self) -> None:
        await self.store.state_set(
            "external_queue_state",
            _json(self.external_queue.snapshot()),
        )

    async def _restore_external_state(self) -> None:
        raw = await self.store.state_get("external_queue_state")
        if not raw:
            return
        try:
            import json as _json_mod

            self.external_queue.restore(_json_mod.loads(raw))
        except (TypeError, ValueError):
            self._log.warning("[Sandbox] external queue state unreadable, starting empty")

    def _delivery_object_id(self) -> str:
        return next((obj.id for obj in self.objects.all() if obj.id.startswith("package")), "")

    async def _interrupt_action(self, why: str, *, resumable: bool = True) -> None:
        action = self.current_action
        if action is None:
            return
        definition = self.actions.definition(action)
        # §14: remember just enough to resume — never a runtime snapshot
        remaining = max(0.0, (action.planned_end_at - float(self._clock())) / 60.0)
        progress = self.actions.progress(action)
        if resumable and definition is not None and progress < 1.0 and remaining >= 1.0:
            self._interrupted = InterruptedActionContext(
                action_id=action.id,
                definition_id=action.definition_id,
                progress=progress,
                location=self.character.location,
                started_at=action.started_at,
                planned_end_at=action.planned_end_at,
                remaining_minutes=remaining,
                interrupt_reason=why,
                resumable=True,
            )
        else:
            self._interrupted = None
        await self._transaction(
            lambda: self.actions.finish(action, ActionStatus.interrupted),
            label=f"interrupt:{action.definition_id}",
        )
        await self.store.save_action(action)
        self.events.publish(
            SandboxEventType.ACTION_INTERRUPTED,
            source="external",
            target=action.definition_id,
            payload={
                "action_id": action.definition_id,
                "action_instance_id": action.id,
                "reason": why,
                "progress": round(progress, 3),
                "resumable": bool(self._interrupted),
            },
            correlation_id=f"act_{action.id}",
        )
        await self._persist_external_state()
        await self._append_event(
            "action_interrupted",
            f"{definition.name if definition else action.definition_id}被打断",
            source=EventSource.external_event,
            level=EventLevel.normal,
            reason=why,
        )
        self.current_action = None

    # -------------------------------------------------- initiative support

    def _day_key(self) -> str:
        import time as _time

        stamp = _time.localtime(float(self._clock()))
        return f"{stamp.tm_year}-{stamp.tm_mon}-{stamp.tm_mday}"

    async def background_budget_left(self) -> int:
        """Daily cap on *background messages* (separate from her life)."""
        cap = int(getattr(self.config, "max_background_messages_per_day", 3))
        key = await self.store.state_get("bg_msgs_day")
        used_raw = await self.store.state_get("bg_msgs_used")
        used = int(used_raw or 0) if key == self._day_key() else 0
        return max(0, cap - used)

    async def note_background_message(self) -> None:
        key = self._day_key()
        stored = await self.store.state_get("bg_msgs_day")
        used = int(await self.store.state_get("bg_msgs_used") or 0) if stored == key else 0
        await self.store.state_set("bg_msgs_day", key)
        await self.store.state_set("bg_msgs_used", str(used + 1))

    async def life_moment(self) -> tuple[str, str]:
        """A recent shareable life event (finished something / project news).

        Feeds the initiative topic pool — she may mention each moment at most
        once (surfaced ids ride ``sandbox_state``).
        """
        try:
            events = await self.store.recent_events(limit=12)
        except Exception:  # noqa: BLE001 - her life is an optional input
            events = []
        if not events:
            events = [
                {
                    "id": record.id,
                    "kind": record.kind,
                    "summary": record.summary,
                    "created_at": record.created_at,
                }
                for record in reversed(self._recent_memory[-12:])
            ]
        surfaced = await self._surfaced_moments()
        shareable = {"action_completed", "commission", "delivery", "sandbox_initialized"}
        for row in events:
            kind = str(row.get("kind", ""))
            event_id = str(row.get("id", ""))
            if kind in shareable and event_id not in surfaced:
                summary = str(row.get("summary", "")).strip()
                if summary:
                    return summary, event_id
        return "", ""

    async def _surfaced_moments(self) -> set[str]:
        raw = await self.store.state_get("surfaced_moments")
        if not raw:
            return set()
        try:
            import json as _json

            data = _json.loads(raw)
            return {str(item) for item in data} if isinstance(data, list) else set()
        except (TypeError, ValueError):
            return set()

    async def note_moment_surfaced(self, event_id: str) -> None:
        """A life moment became a message — never mention it twice."""
        if not event_id:
            return
        import json as _json

        surfaced = await self._surfaced_moments()
        surfaced.add(str(event_id))
        keep = list(surfaced)[-50:]
        await self.store.state_set("surfaced_moments", _json.dumps(keep))

    # ------------------------------------------------------------- facts

    def facts_for(self, text: str) -> Any:
        """Tag-driven world facts for this text (see app/sandbox/facts.py)."""
        from app.sandbox.facts import FactSelector

        selector = getattr(self, "_fact_selector", None)
        if selector is None:
            selector = FactSelector(self)
            self._fact_selector = selector
        return selector.select(text)

    def facts_block(self, text: str) -> str:
        """Facts + the must-not-invent constraint, narrated for the operator."""
        selection = self.facts_for(text)
        if selection.empty:
            self._log.debug("[Facts] no entity matched for %r", (text or "")[:40])
            return ""
        labels = "、".join(entry_id.split(":")[-1] for entry_id in selection.hits)
        narrate().say(
            "facts",
            f"命中标签 → 注入 {len(selection.lines)} 条世界事实",
            detail=f"{labels} · " + "；".join(selection.lines),
        )
        return selection.block()

    def audit_claims(self, text: str) -> str:
        """Strip sentences claiming something is gone while the sandbox says not."""
        from app.sandbox.facts import FactSelector

        selector = getattr(self, "_fact_selector", None)
        if selector is None:
            selector = FactSelector(self)
            self._fact_selector = selector
        cleaned, _violations = selector.audit_claims(text)
        return cleaned

    async def note_agent_result(
        self,
        *,
        task_type: str,
        status: str,
        summary: str,
        session_id: str = "",
        user_id: str = "",
    ) -> None:
        """An agent task finished — a life event, not a state command (§93-§94)."""
        done = status in ("completed", "partial")
        if done:
            self.adjust_need(
                "social_need",
                delta=-0.05,
                source="user_interaction",
                reason=f"agent:{task_type}:{status}",
            )
        self._notes.append(summary)
        await self._append_event(
            "agent_result",
            summary,
            source=EventSource.user_interaction,
            level=EventLevel.micro,
            reason=f"agent:{task_type}:{status}",
        )

    async def note_user_interaction(self, *, user_id: str, session_id: str) -> None:
        """A QQ user started talking: a social stimulus, not a command (§52)."""
        self.adjust_need(
            "social_need",
            delta=-0.05,
            source="user_interaction",
            reason="note_user_interaction",
        )
        self._private_chat_until = float(self._clock()) + 1800.0
        space_id = session_id.split(":", 1)[1] if ":" in session_id else ""
        if session_id.startswith("group:") and space_id:
            self.update_social_space(
                f"qq:{space_id}",
                presence="active",
                source="user_interaction",
                reason="note_user_interaction",
            )

    async def _process_pending_events(self) -> None:
        while self.pending_events:
            event = self.pending_events.pop(0)
            try:
                await self.handle_external(event)
            except Exception:  # noqa: BLE001 - an event must never kill the world
                self._log.exception("[Sandbox] pending event failed")

    # ----------------------------------------------------- recovery / replay

    async def _settle_gap(self, elapsed_minutes: float) -> None:
        """Bounded settle after downtime (§72): no per-tick replay."""
        hours = elapsed_minutes / 60.0
        assume_asleep = self._was_sleeping_through(elapsed_minutes)
        bands_before = self.needs.bands()
        self.needs.advance(min(elapsed_minutes, 24 * 60), rest=1.0 if assume_asleep else 0.0)
        self._note_need_drift(bands_before)
        if self.pet_system is not None:
            self.pet_system.advance(min(elapsed_minutes, 12 * 60))
        note: list[str] = [f"离线 {hours:.1f} 小时"]

        if self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            leftover = (self.current_action.planned_end_at - self._last_tick) / 60.0
            if leftover <= elapsed_minutes:
                # It finished while offline: settle once, not N times.
                await self._complete_action()
                note.append(f"{definition.name if definition else '某件事'}在离线期间做完了")
                await self._decide_and_apply(space_id=self.character.location)
            else:
                self.current_action.started_at += elapsed_minutes * 60.0
                self.current_action.planned_end_at += elapsed_minutes * 60.0
                note.append("回来后接着做手上的事")

        # Morning recovery: slept long enough → awake, day starts (§73 Recovery / §72 恢复).
        hour = time.localtime(float(self._clock())).tm_hour
        if assume_asleep and not (2 <= hour < 5) and self.current_action is not None:
            definition = self.actions.definition(self.current_action)
            if definition is not None and "rest" in definition.tags:
                await self._complete_action()
                note.append("睡醒了")
                await self._decide_and_apply(space_id=self.character.location)

        self._last_tick = float(self._clock())
        await self._append_event(
            "recovery",
            "；".join(note),
            source=EventSource.system,
            level=EventLevel.normal,
            reason="bounded_recovery",
        )
        await self._snapshot()

    def _was_sleeping_through(self, elapsed_minutes: float) -> bool:
        if self.current_action is None:
            return False
        definition = self.actions.definition(self.current_action)
        return definition is not None and "rest" in definition.tags

    async def replay(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """§190: what happened, in order — from events + traces (no CoT)."""
        events = await self.store.recent_events(limit=limit)
        traces = await self.store.recent_traces(limit=limit)
        merged = [
            *(
                {
                    "ts": row["created_at"],
                    "kind": row["kind"],
                    "summary": row["summary"],
                    "reason": row["reason_code"],
                    "level": row["priority"],
                }
                for row in events
            ),
            *(
                {
                    "ts": row["ts"],
                    "kind": f"trace:{row['kind']}",
                    "summary": row["summary"],
                    "reason": row["reason_code"],
                    "level": "trace",
                }
                for row in traces
            ),
        ]
        merged.sort(key=lambda item: item["ts"])
        return merged

    async def simulate(self, hours: float, *, step_minutes: float = 10.0) -> dict[str, Any]:
        """Dry-run simulation (§169-§171): advances in memory, never QQ, no LLM.

        Used by tests and the WebUI simulator. State changes are real in-memory
        changes for the duration of the run — callers snapshot/restore if needed.
        """
        ticks = max(1, int(hours * 60 / step_minutes))
        completed = 0
        events: list[str] = []
        for _ in range(ticks):
            before = self.current_action.id if self.current_action else ""
            report = await self.tick(minutes=step_minutes)
            after = self.current_action.id if self.current_action else ""
            if report.get("completed"):
                completed += 1
            if before and after and before != after:
                definition = self.actions.definition(self.current_action)
                if definition is not None:
                    events.append(definition.name)
            if len(events) > 200:
                break
        return {
            "hours": hours,
            "ticks": ticks,
            "completed_actions": completed,
            "transitions": events[-40:],
        }

    # ------------------------------------------------------------- snapshot

    async def _snapshot(self, *, persist: bool = True) -> SandboxSnapshot:
        snapshot = SandboxSnapshot(
            created_at=float(self._clock()),
            elapsed_minutes=max(0.0, (float(self._clock()) - self._last_tick) / 60.0),
            phase=self.phase.value,
            location=self.character.location,
            character=self.character.model_dump(mode="json"),
            pet=(self.pet_system.snapshot() if self.pet_system else {}),
            needs={key: need.model_dump(mode="json") for key, need in self.needs.all().items()},
            action=(self.current_action.model_dump(mode="json") if self.current_action else None),
            modes=self.modes.snapshot(),
            spaces=[space.model_dump(mode="json") for space in self.spaces.all()],
            objects=[obj.model_dump(mode="json") for obj in self.objects.all()],
            inventories={
                key: inv.model_dump(mode="json") for key, inv in self.inventories.all().items()
            },
            social_spaces=[space.model_dump(mode="json") for space in self.social_spaces.values()],
            commissions=[c.model_dump(mode="json") for c in self.commissions.values()],
            knowledge=dict(self.knowledge),
            projects=dict(self.projects),
            bible_version=self.bible.version,
        )
        if persist:
            await self.store.save_snapshot(
                snapshot, keep=int(getattr(self.config, "snapshot_keep", 48))
            )
        return snapshot

    async def _restore(self) -> bool:
        snapshot = await self.store.latest_snapshot()
        if snapshot is None:
            return False
        try:
            self._apply_snapshot(snapshot)
            self._restored = True
            self._log.info(
                "[Sandbox] restored (location=%s, action=%s)",
                self.character.location,
                self.current_action.definition_id if self.current_action else "-",
            )
            return True
        except Exception:  # noqa: BLE001 - bad snapshot → reseed instead of crashing
            self._log.exception("[Sandbox] snapshot restore failed; will reseed")
            return False

    def _apply_snapshot(self, snapshot: SandboxSnapshot) -> None:
        self.character = CharacterEntity.model_validate(snapshot.character)
        if self.pet_system is not None and snapshot.pet:
            self.pet_system.restore(snapshot.pet)
        self.needs.restore(snapshot.needs)
        if snapshot.action:
            self.current_action = ActionInstance.model_validate(snapshot.action)
        else:
            self.current_action = None
        self.modes.restore(snapshot.modes)
        if snapshot.spaces:
            self.spaces = SpaceSystem([SpaceNode.model_validate(s) for s in snapshot.spaces])
        if snapshot.objects:
            self.objects = ObjectSystem(
                [WorldObjectItem.model_validate(o) for o in snapshot.objects]
            )
        if snapshot.inventories:
            from app.sandbox.models import Inventory

            self.inventories = InventorySystem(
                {key: Inventory.model_validate(data) for key, data in snapshot.inventories.items()}
            )
        if snapshot.social_spaces:
            self.social_spaces = {
                item["id"]: SocialSpace.model_validate(item) for item in snapshot.social_spaces
            }
        if snapshot.commissions:
            self.commissions = {
                item["id"]: Commission.model_validate(item) for item in snapshot.commissions
            }
        self.knowledge = dict(snapshot.knowledge)
        self.projects = dict(snapshot.projects or self.projects)
        created = snapshot.created_at or float(self._clock())
        self._last_tick = created

    # ---------------------------------------------------------- persistence

    async def _persist_all(self) -> None:
        await self.store.save_entity(
            "character",
            type="character",
            name=self.character.name,
            space_id=self.character.location,
            data=self.character.model_dump(mode="json"),
        )
        if self.pet_system is not None:
            await self.store.save_entity(
                "pet",
                type="pet",
                name=self.pet_system.pet.name,
                space_id=self.pet_system.pet.location,
                data=self.pet_system.snapshot(),
            )
        for space in self.spaces.all():
            await self.store.save_space(
                space.id,
                name=space.name,
                parent_id=space.parent_id,
                kind=space.kind.value,
                data=space.model_dump(mode="json"),
            )
        for obj in self.objects.all():
            await self.store.save_object(
                obj.id,
                name=obj.name,
                space_id=obj.space_id,
                kind=obj.kind,
                data=obj.model_dump(mode="json"),
            )
        for key, inventory in self.inventories.all().items():
            await self.store.save_inventory(key, inventory.model_dump(mode="json"))
        for key, need in self.needs.all().items():
            await self.store.save_inventory(  # needs ride the same upsert
                f"need:{key}", need.model_dump(mode="json")
            )
        for social in self.social_spaces.values():
            await self.store.save_social_space(
                social.id,
                name=social.name,
                kind=social.kind,
                data=social.model_dump(mode="json"),
            )
        for commission in self.commissions.values():
            await self.store.save_commission(
                commission.id,
                kind=commission.kind,
                status=commission.status,
                progress=commission.progress,
                deadline=commission.deadline,
                reward=commission.reward,
                data=commission.model_dump(mode="json"),
            )
        for key, entry in self.knowledge.items():
            await self.store.save_knowledge(
                key,
                known=bool(entry.get("known")),
                source=str(entry.get("source", "")),
                learned_at=float(entry.get("learned_at", 0) or 0),
                data=dict(entry.get("data", {}) or {}),
            )
        await self.store.state_set("last_tick", str(self._last_tick))
        await self.store.state_set("projects", _json(self.projects))

    # ------------------------------------------------- memory foundation (§4)

    async def flush_experiences(self) -> list[Any]:
        """Drain buffered experiences → rows, then candidates → memories.

        Runs on the existing flush cadence (tick / external / completion) so
        the world never blocks on memory bookkeeping; the pipeline is strictly
        downstream — nothing here writes world state (§29).
        """
        records = self.experiences.sample()
        if not records:
            return []
        for record in records:
            await self.store.save_experience(record)
        candidates = [
            candidate
            for record in records
            for candidate in self.candidates.from_experience(record, now=float(self._clock()))
        ]
        if candidates and self.memory.available:
            await self.memory.ingest_all(candidates)
        return records

    async def apply_social_interaction(
        self,
        fact: SocialInteractionFact,
        *,
        correlation_id: str = "",
        causation_id: str = "",
    ) -> Any:
        """The one canonical entry for a verified interaction fact (§9/§14).

        Emits SOCIAL_INTERACTION (the fact) and lets the deterministic engine
        produce RELATIONSHIP_CHANGED; the engine is the only writer of
        relationship state.
        """
        published = self.events.publish(
            ET.SOCIAL_INTERACTION,
            source=fact.source,
            target=fact.person_id,
            payload={
                "interaction_id": fact.interaction_id,
                "interaction_type": fact.interaction_type,
                "significance": fact.significance.value,
                "social_space_id": fact.social_space_id,
                "outcome": fact.outcome,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        state, crossed = await self.social.apply(
            fact,
            correlation_id=correlation_id,
            causation_id=published.event_id if published else causation_id,
        )
        await self.relationships_dyn.remember_person(
            fact.person_id, display_name=str(fact.metadata.get("display_name", "") or "")
        )
        return state

    async def relationship_for(self, actor_id: str) -> Any:
        """Relationship state of a QQ actor, or None when there is none yet.

        Read-only *by design* (§38): looking someone up (a chat turn, a
        decision context) never creates or persists a relationship — only
        verified interaction facts do, through the update engine.
        """
        try:
            person = self.persons.for_qq(str(actor_id))
            return await self.relationships_dyn.get(person.person_id)
        except Exception:  # noqa: BLE001 - social context is an aid
            return None

    async def cognitive_context(
        self, *, query: str = "", relationship_target: str = ""
    ) -> CognitiveContext:
        """Read-only view for one chat turn (Phase 5 §25).

        Reads world state, sandbox memories and the continuity snapshot; it
        never mutates anything and never calls an LLM. Retrieval failure
        degrades to an empty layer, never to a broken chat (§33).
        """
        return await self.cognition.build(query=query, relationship_target=relationship_target)

    async def build_continuity(self) -> ContinuitySnapshot:
        """Generate (and persist) the read-model continuity snapshot (§23).

        Named apart from ``self.continuity`` (the v1.2 ContinuityManager feed),
        which this read model deliberately does not touch.
        """
        snapshot = await self.continuity_snapshot.build()
        await self.continuity_snapshot.persist(snapshot)
        return snapshot

    async def _persist_deltas(self) -> None:
        await self.store.save_entity(
            "character",
            type="character",
            name=self.character.name,
            space_id=self.character.location,
            data=self.character.model_dump(mode="json"),
        )
        if self.pet_system is not None:
            await self.store.save_entity(
                "pet",
                type="pet",
                name=self.pet_system.pet.name,
                space_id=self.pet_system.pet.location,
                data=self.pet_system.snapshot(),
            )
        for key in self.inventories.all():
            inventory = self.inventories.get(key)
            await self.store.save_inventory(key, inventory.model_dump(mode="json"))
        for key in self.knowledge:
            entry = self.knowledge[key]
            await self.store.save_knowledge(
                key,
                known=bool(entry.get("known")),
                source=str(entry.get("source", "")),
                learned_at=float(entry.get("learned_at", 0) or 0),
                data=dict(entry.get("data", {}) or {}),
            )

    async def _append_event(
        self,
        kind: str,
        summary: str,
        *,
        source: EventSource,
        level: EventLevel,
        reason: str,
    ) -> None:
        record = SandboxEventRecord(
            id=f"evt_{uuid.uuid4().hex[:10]}",
            kind=kind,
            level=level,
            source=source,
            summary=summary,
            reason_code=reason,
            created_at=float(self._clock()),
        )
        await self.store.append_event(record)
        self._recent_memory.append(record)
        del self._recent_memory[:-30]
        # v1.2 §139: meaningful world events become her recent continuity.
        if (
            self.continuity is not None
            and level in (EventLevel.major, EventLevel.normal)
            and kind not in ("recovery",)
        ):
            try:
                await self.continuity.add_micro_event(
                    summary, kind="world", reason_code=reason or kind
                )
            except Exception:  # noqa: BLE001 - continuity must never break the world
                self._log.debug("[Sandbox] continuity feed failed", exc_info=True)

    # ---------------------------------------------------------- transaction

    async def _transaction(self, mutation: Callable[[], None], *, label: str) -> bool:
        """Atomic-ish mutation (§127/§128): rollback on failure."""
        backup = self._backup_state()
        try:
            mutation()
            return True
        except Exception:  # noqa: BLE001 - restore consistency, keep running
            self._log.exception("[Sandbox] transaction failed: %s — rolling back", label)
            self._restore_state(backup)
            return False

    def _backup_state(self) -> dict[str, Any]:
        return {
            "action": self.current_action.model_copy(deep=True) if self.current_action else None,
            "needs": {k: n.model_copy(deep=True) for k, n in self.needs.all().items()},
            "inventories": {k: v.model_copy(deep=True) for k, v in self.inventories.all().items()},
            "objects": [o.model_copy(deep=True) for o in self.objects.all()],
            "projects": {k: dict(v) for k, v in self.projects.items()},
            "pet": (self.pet_system.pet.model_copy(deep=True) if self.pet_system else None),
        }

    def _restore_state(self, backup: dict[str, Any]) -> None:
        self.current_action = backup["action"]
        for key, need in backup["needs"].items():
            self.needs.all()[key] = need
        for key, inv in backup["inventories"].items():
            self.inventories.all()[key] = inv
        self.objects = ObjectSystem(backup["objects"])
        self.projects = backup["projects"]
        if self.pet_system is not None and backup["pet"] is not None:
            self.pet_system.pet = backup["pet"]

    # ------------------------------------------------------------- helpers

    def _is_asleep(self) -> bool:
        if self.current_action is None:
            return False
        definition = self.actions.definition(self.current_action)
        return definition is not None and "rest" in definition.tags

    def is_asleep(self) -> bool:
        """Public answer for the presence layer: is her current life 'rest'?"""
        return self._is_asleep()

    def _rest_coefficient(self) -> float:
        if self.current_action is None:
            return 0.0
        definition = self.actions.definition(self.current_action)
        if definition is None or "rest" not in definition.tags:
            return 0.0
        return 1.0 if "core" in definition.tags else 0.5

    def _should_snapshot(self) -> bool:
        stride = max(1, int(60.0 / max(1.0, float(self.config.tick_seconds) / 60.0)))
        return int(self._rng.random() * stride) == 0

    async def _sync_state(self) -> None:
        """Project her life onto CharacterState (WebUI/prompt/timing agree)."""
        if self.state_sync is None:
            return
        definition = self.actions.definition(self.current_action)
        # remediation-3: the activity is the definition's own metadata
        activity = (definition.activity or definition.id) if definition else "idle"
        location = self.spaces.name(self.character.location)
        energy = max(0.0, min(1.0, 1.0 - self.needs.level("energy")))
        try:
            await self.state_sync(activity, location, energy)
        except Exception:  # noqa: BLE001 - projection must never break the world
            self._log.debug("[Sandbox] state sync failed", exc_info=True)

    def _derive_modes(self) -> list[str]:
        definition = self.actions.definition(self.current_action)
        hour = time.localtime(float(self._clock())).tm_hour
        social_active = (
            any(space.character_presence == "active" for space in self.social_spaces.values())
            or float(self._clock()) < self._private_chat_until
        )
        modes = self.modes.derive(
            space_id=self.character.location,
            hour=hour,
            definition=definition,
            social_active=social_active,
            is_home=self.spaces.is_home(self.character.location),
        )
        entered = self.modes.update(modes)
        # derived projection: ModeRuntime.derive() owns the mode set (from
        # location/time/action/social); the entity field only mirrors it
        self.character.modes = modes
        return entered

    def _advance_commission(self, amount: float) -> None:
        active = next(
            (c for c in self.commissions.values() if c.status in ("open", "active")),
            None,
        )
        if active is None:
            return
        active.status = "active"
        active.progress = min(1.0, active.progress + amount)
        if active.progress >= 1.0:
            active.status = "delivered"
            self.adjust_need(
                "work_need",
                relieve=1.0,
                source="character_action",
                reason="commission_delivered",
            )

    # -------------------------------------------------------- context output

    def context(self) -> dict[str, Any]:
        """The current-world slice for the character prompt (§85-§87).

        Deliberately small: where she is, what she is doing (with detail),
        the mode, the cat, notable needs and the active project — never the
        whole sandbox (§86).
        """
        definition = self.actions.definition(self.current_action)
        action_line = ""
        if definition is not None:
            action_line = definition.name
            if self.current_action and self.current_action.detail:
                action_line += f"（{self.current_action.detail}）"
        elif self._is_asleep() is False and self.current_action is None:
            action_line = "闲着"
        location = self.spaces.name(self.character.location)
        pieces = [f"现在她在{location}"]
        if action_line:
            pieces.append(f"正在{action_line}")
        pet = self.pet_system.prompt_line() if self.pet_system else ""
        project = next(iter(self.projects.values()), None)
        goal = ""
        if project is not None:
            goal = f"{project['name']}：{project.get('next_action', '')}"
        commission = next(
            (c for c in self.commissions.values() if c.status in ("open", "active")),
            None,
        )
        if commission is not None:
            goal = f"{commission.title or commission.kind}（进度 {commission.progress:.0%}）"
        return {
            "state_line": "；".join(pieces + ([pet] if pet else [])),
            "mode_line": self.modes.prompt_line(),
            "need_line": self.needs.summary_line(),
            "goal": goal,
            "events": list(self._notes[-3:]),
            "location": location,
            "action": action_line,
            "modes": self.modes.ids(),
            "pet": pet,
            "interrupted": (self._interrupted.definition_id if self._interrupted else ""),
        }

    def status_line(self) -> str:
        definition = self.actions.definition(self.current_action)
        where = self.spaces.name(self.character.location)
        doing = definition.name if definition is not None else "闲着"
        return f"{doing}（{where}）"

    def snapshot_dict(self) -> dict[str, Any]:
        return self.context() | {"phase": self.phase.value}


def _json(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)
