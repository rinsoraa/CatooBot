"""Social & Relationship Dynamics (v2.1 Phase 8 §4-§38).

    ExternalWorldEvent → SocialInteractionFact → RelationshipUpdateEngine
        → StateMutation → RELATIONSHIP_CHANGED → (Context / Decision / Memory)

Design boundaries this module holds:

* the bible's ``relationships`` section is the *canonical initial* definition
  (§4/§28) — runtime state never writes back to it;
* a **person** is not a QQ id (§7): ``PersonIdentity.external_ids`` maps
  platform handles onto a stable ``person_id``;
* relationship state is **character-scoped** (§6): the same person in two
  worlds is two states, and there is no global relationship map;
* every change is a deterministic, small, reversible step produced by an
  *interaction fact* — never by a model, never by memory retrieval
  (§10/§14/§25/§35/§38);
* an invitation is not an accepted activity (§19/§33): the invite, the
  acceptance and the shared activity are three separate facts.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.sandbox.events import SandboxEventType as ET
from app.sandbox.mutations import StateMutation


class InteractionSignificance(str, Enum):  # noqa: UP042 - §18
    trivial = "trivial"
    normal = "normal"
    meaningful = "meaningful"
    major = "major"


class PersonIdentity(BaseModel):
    """A person, independent of any single platform (§7/§31)."""

    person_id: str
    display_name: str = ""
    #: platform handle → id, e.g. {"qq": "2731431246"}
    external_ids: dict[str, str] = Field(default_factory=dict)
    source: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationshipState(BaseModel):
    """Dynamic relationship state for one (character, person) pair (§5)."""

    character_id: str = ""
    person_id: str = ""
    relation_type: str = "acquaintance"
    trust: float = Field(default=0.3, ge=0.0, le=1.0)
    familiarity: float = Field(default=0.1, ge=0.0, le=1.0)
    closeness: float = Field(default=0.1, ge=0.0, le=1.0)
    social_comfort: float = Field(default=0.3, ge=0.0, le=1.0)
    positive_interactions: int = 0
    negative_interactions: int = 0
    interaction_count: int = 0
    last_interaction_at: float = 0.0
    source: str = "runtime"
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def key(self) -> str:
        return f"{self.character_id}|{self.person_id}"


class SocialInteractionFact(BaseModel):
    """One *verified* interaction — the only input relationship accepts (§9)."""

    interaction_id: str
    character_id: str = ""
    person_id: str = ""
    interaction_type: str = "message_received"
    source: str = "qq"
    timestamp: float = 0.0
    social_space_id: str = ""
    outcome: str = ""
    significance: InteractionSignificance = InteractionSignificance.normal
    importance: float = Field(default=0.3, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(cls, **kwargs: Any) -> SocialInteractionFact:
        kwargs.setdefault("interaction_id", f"si_{uuid.uuid4().hex[:12]}")
        return cls(**kwargs)


#: deterministic deltas per interaction type (§11-§13) — small on purpose
INTERACTION_RULES: dict[str, dict[str, float]] = {
    "message_received": {"familiarity": 0.01},
    "direct_chat": {"familiarity": 0.01},
    "group_interaction": {"familiarity": 0.005},
    "game_invitation": {"familiarity": 0.01},  # the invite itself is not a bond
    "invitation_accepted": {"familiarity": 0.02, "closeness": 0.02},
    "invitation_declined": {"social_comfort": -0.01},
    "shared_activity": {
        "familiarity": 0.03,
        "closeness": 0.03,
        "trust": 0.01,
        "social_comfort": 0.02,
    },
    "game_played": {"familiarity": 0.03, "closeness": 0.03, "trust": 0.01, "social_comfort": 0.02},
    "help_received": {"trust": 0.03, "closeness": 0.02},
    "help_given": {"trust": 0.01, "closeness": 0.01},
    "interaction_ignored": {"social_comfort": -0.015, "closeness": -0.01},
    "no_response": {"trust": -0.01, "social_comfort": -0.01},
}

#: significance scales a fact's deltas — trivial traffic moves nothing (§18)
SIGNIFICANCE_MULTIPLIER: dict[InteractionSignificance, float] = {
    InteractionSignificance.trivial: 0.0,
    InteractionSignificance.normal: 1.0,
    InteractionSignificance.meaningful: 1.5,
    InteractionSignificance.major: 2.0,
}

#: inertia: no single fact may move a dimension further than this (§12)
MAX_DELTA_PER_FACT = 0.05

#: crossing one of these marks a *major* relationship change (§27)
MILESTONES = (0.5, 0.7, 0.85)

#: messages with no social substance never move the relationship (§18)
_TRIVIAL_RE = re.compile(r"^(哈+|呵+|嗯+|哦+|噢+|在吗|在不在|？+|\?+|\.{3,}|…+)$")


def classify_significance(
    content: str, *, interaction_type: str, core_person: bool = False
) -> InteractionSignificance:
    """Deterministic significance for a chat-derived fact (§18)."""
    if interaction_type not in ("message_received", "direct_chat", "group_interaction"):
        return InteractionSignificance.meaningful
    text = (content or "").strip()
    if len(text) <= 2 or _TRIVIAL_RE.match(text):
        return InteractionSignificance.trivial
    if core_person and len(text) >= 8:
        return InteractionSignificance.meaningful
    return InteractionSignificance.normal


def person_id_for(*, kind: str, value: str) -> str:
    """Stable id from one platform handle (deterministic across restarts)."""
    digest = hashlib.sha1(f"{kind}:{value}".encode()).hexdigest()[:10]  # noqa: S324 - identity, not security
    return f"person_{kind}_{digest}" if kind != "bible" else f"person_bible_{digest}"


class PersonIdentityResolver:
    """Maps platform handles (qq today, more later) onto person ids (§31)."""

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime

    def for_qq(self, qq_id: str, *, display_name: str = "") -> PersonIdentity:
        """QQ handle → identity; a bible core friend keeps his canonical id."""
        qq = str(qq_id)
        core_ids = {str(item) for item in getattr(self._rt.config, "core_friend_ids", []) or []}
        bible_person = self._bible_core_person() if qq in core_ids else None
        if bible_person is not None:
            identity = bible_person.model_copy(deep=True)
            identity.external_ids.setdefault("qq", qq)
            identity.display_name = identity.display_name or display_name
            return identity
        return PersonIdentity(
            person_id=person_id_for(kind="qq", value=qq),
            display_name=display_name,
            external_ids={"qq": qq},
            source="qq",
        )

    def _bible_core_person(self) -> PersonIdentity | None:
        core = list(getattr(self._rt.seed, "core_friend_names", []) or [])
        for name in core:
            state = self._rt.relationships_dyn.initial_for_name(name)
            if state is not None:
                return PersonIdentity(
                    person_id=state.person_id,
                    display_name=name,
                    external_ids={"character_bible": name},
                    source="bible",
                )
        return None


def initial_states(definition: Any, *, character_id: str, clock: Any) -> list[RelationshipState]:
    """Bible relationships → initial dynamic states (§28, no name matching)."""
    now = float(clock())
    states: list[RelationshipState] = []
    for relationship in getattr(definition, "relationships", []) or []:
        name = str(getattr(relationship, "name", "") or "")
        if not name:
            continue
        relation_type = str(getattr(relationship, "type", "acquaintance") or "acquaintance")
        core = bool(getattr(relationship, "core", False))
        states.append(
            RelationshipState(
                character_id=character_id,
                person_id=person_id_for(kind="bible", value=name),
                relation_type=relation_type,
                trust=0.7 if core else 0.3,
                familiarity=0.5 if core else 0.1,
                closeness=0.6 if core else 0.1,
                social_comfort=0.8 if core else 0.3,
                source="character_bible",
                metadata={"name": name},
                created_at=now,
                updated_at=now,
            )
        )
    return states


class RelationshipUpdateEngine:
    """InteractionFact → deterministic deltas → canonical mutation (§11-§14)."""

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime
        self.applied = 0
        self.milestones = 0

    def deltas_for(self, fact: SocialInteractionFact) -> dict[str, float]:
        base = dict(INTERACTION_RULES.get(fact.interaction_type, {}))
        if not base:
            return {}
        multiplier = SIGNIFICANCE_MULTIPLIER[fact.significance]
        if multiplier <= 0.0:
            return {}
        out: dict[str, float] = {}
        for field, delta in base.items():
            value = delta * multiplier
            out[field] = max(-MAX_DELTA_PER_FACT, min(MAX_DELTA_PER_FACT, value))
        return out

    async def apply(
        self, fact: SocialInteractionFact, *, correlation_id: str = "", causation_id: str = ""
    ) -> tuple[RelationshipState, bool]:
        """Apply one fact; returns (state, milestone_crossed)."""
        state = await self._rt.relationships_dyn.ensure(fact.person_id)
        mutation_facts = self.deltas_for(fact)
        positive = any(delta > 0 for delta in mutation_facts.values())
        negative = any(delta < 0 for delta in mutation_facts.values())
        crossed = False
        fields_before: dict[str, float] = {}
        for field, delta in mutation_facts.items():
            before = float(getattr(state, field))
            after = max(0.0, min(1.0, before + delta))
            if after == before:
                continue
            fields_before[field] = before
            setattr(state, field, after)
            if any(before < mark <= after for mark in MILESTONES):
                crossed = True
        state.interaction_count += 1
        state.last_interaction_at = float(fact.timestamp or self._rt._clock())  # noqa: SLF001
        state.updated_at = float(self._rt._clock())  # noqa: SLF001
        if positive:
            state.positive_interactions += 1
        if negative:
            state.negative_interactions += 1
        await self._rt.relationships_dyn.save(state)

        significance = "major" if crossed else fact.significance.value
        for field, before in fields_before.items():
            self._rt.mutations.record(  # canonical mutation trail (§14)
                StateMutation(
                    target=f"relationship:{fact.person_id}",
                    field=field,
                    before=before,
                    after=float(getattr(state, field)),
                    source=fact.source,
                    reason=fact.interaction_type,
                    # a relationship moves *alongside* the world, never inside
                    # it: it must not invalidate a decision already in flight
                    affects_world=False,
                )
            )
        self.applied += 1
        if crossed:
            self.milestones += 1
        self._rt.events.publish(
            ET.RELATIONSHIP_CHANGED,
            source=fact.source,
            target=fact.person_id,
            payload={
                "person_id": fact.person_id,
                "interaction_type": fact.interaction_type,
                "interaction_id": fact.interaction_id,
                "significance": significance,
                "fields": list(fields_before),
                "trust": round(state.trust, 3),
                "familiarity": round(state.familiarity, 3),
                "closeness": round(state.closeness, 3),
                "social_comfort": round(state.social_comfort, 3),
                "relation_type": state.relation_type,
            },
            causation_id=causation_id,
            correlation_id=correlation_id,
        )
        return state, crossed


class RelationshipStore:
    """Persistence + in-memory cache for dynamic relationship states (§30).

    Reuses the shared SQLite database (``sandbox_relationships`` /
    ``sandbox_persons``); character isolation is enforced by every query.
    """

    def __init__(self, runtime: Any) -> None:
        self._rt = runtime
        self._db = runtime.store.database
        self._cache: dict[str, RelationshipState] = {}
        self._initial: list[RelationshipState] = []

    @property
    def available(self) -> bool:
        return self._db is not None

    # ------------------------------------------------------------ initial

    def seed_initial(self) -> list[RelationshipState]:
        """Bible relationships → canonical starting states (§28)."""
        self._initial = initial_states(
            self._rt.definition, character_id=self._rt.character_id, clock=self._rt._clock
        )
        return list(self._initial)

    def initial_for_name(self, name: str) -> RelationshipState | None:
        for state in self._initial:
            if state.metadata.get("name") == name:
                return state
        return None

    async def seed_persisted(self) -> None:
        """Persist the canonical states that are not in the database yet."""
        for state in self._initial:
            existing = await self.get(state.person_id)
            if existing is None:
                self._cache[state.person_id] = state
                await self.save(state)

    # -------------------------------------------------------------- access

    async def _peek(self, person_id: str) -> RelationshipState | None:
        """The cached/loaded instance — for the engine and ``ensure`` only."""
        if person_id in self._cache:
            return self._cache[person_id]
        if not self.available:
            return None
        row = await self._db.fetchone(
            "SELECT data FROM sandbox_relationships WHERE character_id = ? AND person_id = ?",
            (self._rt.character_id, person_id),
        )
        if row is None:
            return None
        import json

        state = RelationshipState.model_validate(json.loads(row["data"]))
        self._cache[person_id] = state
        return state

    async def get(self, person_id: str) -> RelationshipState | None:
        """Read a state as a *copy*: reads are not a way to write (§38).

        Handing out the cached instance would let a caller (a prompt builder,
        a decision, a memory query) mutate the relationship in place, without
        a mutation record and without persistence. Only
        :meth:`RelationshipUpdateEngine.apply` writes, via ``ensure`` + ``save``.
        """
        state = await self._peek(person_id)
        return state.model_copy(deep=True) if state is not None else None

    async def ensure(self, person_id: str) -> RelationshipState:
        """The live state for the single writer path — engine/apply only.

        Loads, or starts a fresh neutral state (someone new); never persisted
        until :meth:`save` is called.
        """
        state = await self._peek(person_id)
        if state is not None:
            return state
        now = float(self._rt._clock())
        state = RelationshipState(
            character_id=self._rt.character_id, person_id=person_id, created_at=now, updated_at=now
        )
        self._cache[person_id] = state
        return state

    async def save(self, state: RelationshipState) -> bool:
        self._cache[state.person_id] = state
        if not self.available:
            return False
        import json

        now = float(self._rt._clock())
        state.updated_at = now
        try:
            await self._db.execute(
                """INSERT INTO sandbox_relationships (character_id, person_id, data, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(character_id, person_id) DO UPDATE SET
                       data = excluded.data, updated_at = excluded.updated_at""",
                (state.character_id, state.person_id, json.dumps(state.model_dump()), now),
            )
        except Exception:  # noqa: BLE001 - relationship bookkeeping never breaks the world
            return False
        return True

    async def important(self, *, limit: int = 3) -> list[RelationshipState]:
        """Most relevant states for continuity/context (never the whole DB, §24/§26)."""
        states = list(self._cache.values())
        if self.available:
            rows = await self._db.fetchall(
                "SELECT data FROM sandbox_relationships WHERE character_id = ?"
                " ORDER BY updated_at DESC LIMIT 50",
                (self._rt.character_id,),
            )
            import json

            cached = set(self._cache)
            for row in rows:
                state = RelationshipState.model_validate(json.loads(row["data"]))
                if state.person_id in cached:
                    continue
                self._cache[state.person_id] = state
                states.append(state)
        states.sort(key=lambda s: (-(s.closeness + s.trust), -s.last_interaction_at, s.person_id))
        return [state.model_copy(deep=True) for state in states[:limit]]

    async def remember_person(self, person_id: str, *, display_name: str = "") -> None:
        """Keep the platform→person mapping (§7/§31). Best-effort."""
        if not self.available:
            return
        import json

        try:
            await self._db.execute(
                """INSERT INTO sandbox_persons
                       (person_id, display_name, external_ids, source, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(person_id) DO UPDATE SET
                       display_name = CASE
                           WHEN excluded.display_name <> '' THEN excluded.display_name
                           ELSE sandbox_persons.display_name END,
                       updated_at = excluded.updated_at""",
                (
                    person_id,
                    display_name,
                    json.dumps({"person_id": person_id}),
                    "runtime",
                    float(self._rt._clock()),
                ),
            )
        except Exception:  # noqa: BLE001 - identity bookkeeping never breaks the world
            pass

    def describe(self, state: RelationshipState) -> str:
        """Neutral one-liner for prompts/decision context (§23)."""
        return (
            f"{state.metadata.get('name') or state.person_id}："
            f"{state.relation_type}，信任 {state.trust:.2f}，"
            f"熟悉 {state.familiarity:.2f}，亲近 {state.closeness:.2f}"
        )
