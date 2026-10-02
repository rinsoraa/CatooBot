"""Sandbox data models (v2.0 §19-§65).

The vocabulary of the Life Sandbox: Entity / Space / Object / Inventory /
Need / Action / Rule / Event / Mode. Everything is structured and persists as
JSON payloads (see ``store.py``); nothing here holds hidden reasoning.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

# --------------------------------------------------------------------- enums


class EntityType(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    character = "character"
    pet = "pet"
    npc = "npc"
    object = "object"
    location = "location"
    social_space = "social_space"


class ActionStatus(str, Enum):  # noqa: UP042
    active = "active"
    completed = "completed"
    interrupted = "interrupted"
    cancelled = "cancelled"
    blocked = "blocked"
    expired = "expired"


class EventPriority(str, Enum):  # noqa: UP042
    critical = "critical"
    high = "high"
    normal = "normal"
    low = "low"
    ambient = "ambient"


class EventSource(str, Enum):  # noqa: UP042
    system = "system"
    character_action = "character_action"
    pet_action = "pet_action"
    external_event = "external_event"
    user_interaction = "user_interaction"
    admin = "admin"
    simulation = "simulation"


class EventLevel(str, Enum):  # noqa: UP042
    major = "major"
    normal = "normal"
    micro = "micro"
    ambient = "ambient"


class SandboxPhase(str, Enum):  # noqa: UP042 - §75
    initializing = "initializing"
    running = "running"
    paused = "paused"
    recovering = "recovering"
    degraded = "degraded"
    stopped = "stopped"


class SpaceKind(str, Enum):  # noqa: UP042
    world = "world"
    apartment = "apartment"
    room = "room"
    transit = "transit"
    outdoor = "outdoor"
    shop = "shop"


class PetActivity(str, Enum):  # noqa: UP042 - §25
    sleeping = "sleeping"
    idle = "idle"
    wandering = "wandering"
    approaching_owner = "approaching_owner"
    sitting_near_owner = "sitting_near_owner"
    on_keyboard = "on_keyboard"
    eating = "eating"
    drinking = "drinking"
    grooming = "grooming"


# ------------------------------------------------------------------ sandbox core


class SpaceNode(BaseModel):
    """One place in the sandbox (§27-§29)."""

    id: str
    name: str
    kind: SpaceKind = SpaceKind.room
    parent_id: str = ""
    connects: list[str] = Field(default_factory=list)  # reachable space ids
    allowed_actions: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    private: bool = True  # indoors / needs outdoor mode to leave
    #: semantic tags for fact injection (吃喝/睡觉/游戏…) — see facts.py
    tags: list[str] = Field(default_factory=list)


class WorldObjectItem(BaseModel):
    """One interactable thing (§30)."""

    id: str
    name: str
    space_id: str
    kind: str = "object"  # furniture / appliance / container / wearable
    interactions: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    inventory_key: str = ""  # when it holds items (fridge…)
    owner: str = ""
    #: semantic tags for fact injection (吃喝/猫/游戏/快递…) — see facts.py
    tags: list[str] = Field(default_factory=list)


class Inventory(BaseModel):
    """Items somewhere: fridge / bowl / character pockets (§31)."""

    key: str
    items: dict[str, int] = Field(default_factory=dict)
    capacity: int = 0  # 0 = unlimited

    def count(self, item: str) -> int:
        return int(self.items.get(item, 0))

    def take(self, item: str, quantity: int = 1) -> bool:
        if self.count(item) < quantity:
            return False
        remaining = self.count(item) - quantity
        if remaining <= 0:
            self.items.pop(item, None)
        else:
            self.items[item] = remaining
        return True

    def add(self, item: str, quantity: int = 1) -> None:
        self.items[item] = self.count(item) + quantity


class NeedThresholds(BaseModel):
    """0..1 pressure thresholds (§36): soft→tendency, strong→weight, critical→forced."""

    soft: float = 0.5
    strong: float = 0.7
    critical: float = 0.85


class NeedState(BaseModel):
    """One growing need (§34-§37). Pressure grows with time; actions relieve it."""

    key: str
    level: float = 0.0
    growth_per_hour: float = 0.0
    thresholds: NeedThresholds = Field(default_factory=NeedThresholds)
    last_updated: float = 0.0

    def band(self) -> str:
        if self.level >= self.thresholds.critical:
            return "critical"
        if self.level >= self.thresholds.strong:
            return "strong"
        if self.level >= self.thresholds.soft:
            return "soft"
        return "calm"


class ActionDefinition(BaseModel):
    """What an action is and what it needs (§38-§41)."""

    id: str
    name: str
    spaces: list[str] = Field(default_factory=list)  # where it can run
    required_objects: list[str] = Field(default_factory=list)
    min_minutes: float = 5.0
    typical_minutes: float = 30.0
    max_minutes: float = 120.0
    interruptibility: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    tags: list[str] = Field(default_factory=list)
    # need key -> relief per completion (fraction of remaining pressure)
    need_relief: dict[str, float] = Field(default_factory=dict)
    # need key -> pressure *added* per completion (e.g. going out raises fatigue)
    need_cost: dict[str, float] = Field(default_factory=dict)
    # item consumption: inventory key -> {item: count}
    consumes: dict[str, dict[str, int]] = Field(default_factory=dict)
    # effects applied on completion: e.g. {"project:mc_city": 0.02}
    effects: dict[str, float] = Field(default_factory=dict)
    modes: list[str] = Field(default_factory=list)  # implied mode ids
    detail_pool: list[str] = Field(default_factory=list)  # deterministic labels
    social_space: str = ""
    # walk somewhere while it runs (shops); she returns home on completion
    destination: str = ""
    # only sensible while the listed items are empty: {"fridge": ["可乐"]}
    requires_absent: dict[str, list[str]] = Field(default_factory=dict)


class ActionInstance(BaseModel):
    """One running (or finished) action — the v1.0 episode, re-rooted (§18)."""

    id: str
    definition_id: str
    started_at: float
    planned_end_at: float
    ended_at: float = 0.0
    status: ActionStatus = ActionStatus.active
    detail: str = ""
    source: str = "decision"  # decision / seed / external / recovery
    reason_code: str = ""
    space_id: str = ""
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    involved_entities: list[str] = Field(default_factory=list)
    previous_definition_id: str = ""  # to resume after an interruption


class InterruptedActionContext(BaseModel):
    """What it takes to resume a paused action (Phase 3 §14).

    Deliberately small: the action's identity, how far it got, where she was
    standing, and why it stopped — never a snapshot of the whole runtime.
    """

    action_id: str
    definition_id: str
    progress: float = 0.0
    location: str = ""
    started_at: float = 0.0
    planned_end_at: float = 0.0
    remaining_minutes: float = 0.0
    interrupt_reason: str = ""
    source_action_id: str = ""
    resumable: bool = True


class PetState(BaseModel):
    """A pet as a real entity (§23-§26) — identity from the seed, state runtime.

    Character-specific fields (name/species/tags/location) have no defaults:
    a pet always comes from a :class:`~app.sandbox.world_seed.PetSeed`.
    """

    id: str = "pet_001"
    name: str
    species: str
    #: semantic tags for fact injection (compiled from the seed's inventories)
    tags: list[str] = Field(default_factory=list)
    location: str
    activity: PetActivity = PetActivity.idle
    activity_until: float = 0.0
    hunger: float = 0.3
    energy: float = 0.6
    affection: float = 0.7
    mood: str = "平静"
    last_interaction: float = 0.0
    habits: list[str] = Field(default_factory=list)
    owner_relationship: str = ""


class CharacterEntity(BaseModel):
    """The character inside the sandbox (§20-§21) — state, not personality.

    Name/location have no defaults (§9): a character entity is always built
    from the world seed's :class:`~app.sandbox.world_seed.CharacterSeed`.
    """

    id: str = "character"
    name: str
    type: EntityType = EntityType.character
    location: str
    modes: list[str] = Field(default_factory=list)
    current_action_id: str = ""
    inventory_key: str = "character"
    energy: float = 0.7
    focus: float = 0.5


class ModeState(BaseModel):
    """Active mode (§78-§81). Modes stack: HOME + DEEP_NIGHT + ONLINE_SOCIAL."""

    id: str
    priority: int = 0
    entered_at: float = 0.0
    trigger: str = ""


class Commission(BaseModel):
    """One casual online commission (§65)."""

    id: str
    kind: str = "mc_build"  # mc_build / game_guide / illustration
    title: str = ""
    client_type: str = "online"  # simulated community member
    scope: str = ""
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    deadline: float = 0.0
    reward: float = 0.0
    status: str = "open"  # open / active / delivered / expired
    detail: str = ""


class SocialSpace(BaseModel):
    """A place she hangs out online (§58-§62). QQ groups map onto these."""

    id: str
    name: str
    kind: str = "simulated"  # qq_group / simulated
    topic: str = ""
    qq_group_id: str = ""
    participants: list[str] = Field(default_factory=list)
    character_presence: str = "offline"  # active / lurking / offline
    interest: float = Field(default=0.5, ge=0.0, le=1.0)
    social_temperature: float = 0.5


class ExternalEvent(BaseModel):
    """Something the outside world did (§49-§50/§70)."""

    id: str
    kind: str  # user_message / group_mention / delivery_arrived / admin
    priority: EventPriority = EventPriority.normal
    source: EventSource = EventSource.external_event
    summary: str = ""
    reason_code: str = ""
    user_id: str = ""
    group_id: str = ""
    social_space_id: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    handled: bool = False


class SandboxEventRecord(BaseModel):
    """A world event that happened (§134-§136) — meaningful only."""

    id: str
    kind: str  # action_completed / need_critical / interrupt / pet / recovery
    level: EventLevel = EventLevel.normal
    source: EventSource = EventSource.system
    summary: str = ""
    reason_code: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0


class DecisionTrace(BaseModel):
    """Why the character chose what she chose (§98/§192) — factors, no CoT."""

    ts: float
    kind: str = "decision"
    summary: str = ""
    reason_code: str = ""
    factors: list[str] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)


class SandboxDecision(BaseModel):
    """Structured output of the decision engine (§43/§124)."""

    decision: str  # continue / extend / switch / interrupt / sleep / move / interact
    action_id: str = ""
    reason_codes: list[str] = Field(default_factory=list)
    factors: dict[str, Any] = Field(default_factory=dict)
    via_ai: bool = False


class SandboxSnapshot(BaseModel):
    """Full restorable state (§74)."""

    created_at: float = 0.0
    elapsed_minutes: float = 0.0
    phase: str = "running"
    location: str = ""
    character: dict[str, Any] = Field(default_factory=dict)
    pet: dict[str, Any] = Field(default_factory=dict)
    needs: dict[str, Any] = Field(default_factory=dict)
    action: dict[str, Any] | None = None
    modes: list[dict[str, Any]] = Field(default_factory=list)
    spaces: list[dict[str, Any]] = Field(default_factory=list)
    objects: list[dict[str, Any]] = Field(default_factory=list)
    inventories: dict[str, Any] = Field(default_factory=dict)
    social_spaces: list[dict[str, Any]] = Field(default_factory=list)
    commissions: list[dict[str, Any]] = Field(default_factory=list)
    knowledge: dict[str, Any] = Field(default_factory=dict)
    projects: dict[str, Any] = Field(default_factory=dict)
    bible_version: str = ""


class KnowledgeEntry(BaseModel):
    """Character knowledge (§88-§90): what *she* knows, not what the world is."""

    key: str
    known: bool = False
    source: str = ""  # observation / memory / interaction / discovered
    learned_at: float = 0.0
    data: dict[str, Any] = Field(default_factory=dict)
