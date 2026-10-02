"""Cognitive Context Bridge (v2.1 Phase 5): what the character may see now.

Three sources stay explicitly separated (§4/§6/§17):

* **conversation memory** — the existing ``MemoryManager`` path (user history);
* **sandbox memory** — Phase 4's long-term life memories (``SandboxMemoryStore``);
* **continuity** — the v2.1 ``ContinuitySnapshot`` (the character's life thread),
  deliberately distinct from the v1.2 conversation continuity that travels in
  its own builder argument.

The bridge is strictly **read-only** (§14): it reads world state, memories and
the snapshot, and builds a small payload for the prompt. It never calls a
mutation helper, never drives the decision engine, and never talks to an LLM
(§15) — retrieval is deterministic keyword/entity/importance/recency scoring
(§7/§8) with an explicit budget (§9).

Prompt authority (§10-§12): the *current* world facts keep the final word; the
snapshot and memories are labelled reference material so an old memory can
never overwrite what is true now.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class CognitiveContext(BaseModel):
    """The sandbox half of this turn's cognition, with provenance intact."""

    character_id: str = ""
    query: str = ""
    #: current world slice (state_line / mode / needs / location / action)
    current_world: dict[str, Any] = Field(default_factory=dict)
    current_action: str = ""
    current_location: str = ""
    #: the v2.1 sandbox continuity snapshot (read model, never persisted here)
    continuity: dict[str, Any] | None = None
    #: retrieved life memories — each row keeps memory_id + provenance (§28)
    relevant_memories: list[dict[str, Any]] = Field(default_factory=list)
    #: recent notable experiences (already stored, importance-filtered)
    recent_experiences: list[dict[str, Any]] = Field(default_factory=list)
    #: deterministic retrieval bookkeeping (candidates/selected/query/reason)
    retrieval: dict[str, Any] = Field(default_factory=dict)

    # ------------------------------------------------------------- prompt

    def as_prompt_payload(self) -> dict[str, Any]:
        """The shape the CharacterContextBuilder renders (source-tagged)."""
        continuity = self.continuity or {}
        projects = [
            {
                "name": str(item.get("name", "")),
                "progress": float(item.get("progress", 0.0)),
            }
            for item in (continuity.get("unfinished_projects") or [])[:3]
        ]
        pending = continuity.get("pending_external_events") or []
        return {
            "character_id": self.character_id,
            "world_line": str(self.current_world.get("state_line", "") or ""),
            "continuity": {
                "summary": str(continuity.get("current_world_state_summary", "") or ""),
                "projects": projects,
                "pending_external": len(pending),
                "goals": [
                    {
                        "description": str(item.get("description", "")),
                        "progress": float(item.get("progress", 0.0)),
                    }
                    for item in (continuity.get("active_goals") or [])[:2]
                ],
            },
            "memories": [
                {
                    "memory_id": int(row.get("memory_id", 0) or 0),
                    "text": str(row.get("text", "")),
                    "importance": float(row.get("importance", 0.0)),
                    "source": str(row.get("source", "sandbox")),
                }
                for row in self.relevant_memories
            ],
            "experiences": [
                {
                    "id": str(row.get("id", "")),
                    "kind": str(row.get("kind", "")),
                    "text": str(row.get("summary", "")),
                }
                for row in self.recent_experiences
            ],
            "retrieval": dict(self.retrieval),
        }


class CognitiveContextBuilder:
    """Assembles a :class:`CognitiveContext` from read-only runtime data."""

    def __init__(self, runtime: Any, *, clock: Any) -> None:
        self._rt = runtime
        self._clock = clock

    def _entities(self, relationship_target: str) -> list[str]:
        """Query entities: what is true about the character's world now (§19)."""
        rt = self._rt
        entities: list[str] = []
        if getattr(rt, "pet", None) is not None:
            entities.append(rt.pet.name)
        for payload in rt.projects.values():
            name = str(payload.get("name", ""))
            if name:
                entities.append(name)
        location = rt.spaces.name(rt.character.location)
        if location:
            entities.append(location)
        definition = rt.actions.definition(rt.current_action)
        if definition is not None and definition.name:
            entities.append(definition.name)
        if relationship_target:
            entities.append(str(relationship_target))
        return [entity for entity in entities if entity]

    async def build(self, *, query: str = "", relationship_target: str = "") -> CognitiveContext:
        """Read world + snapshot + memories; never write anything (§14)."""
        rt = self._rt
        config = rt.config
        entities = self._entities(relationship_target)

        memories: list[dict[str, Any]] = []
        retrieval: dict[str, Any] = {"query": query, "candidates": 0, "selected": 0}
        if config.memory_context_limit > 0 and rt.memory.available:
            memories, retrieval = await rt.memory.retrieve_relevant(
                query=query,
                entities=entities,
                limit=config.memory_context_limit,
                min_score=config.memory_context_min_score,
                max_chars=config.memory_context_max_chars,
                require_evidence=config.memory_context_require_evidence,
            )

        continuity: dict[str, Any] | None = None
        try:
            snapshot = await rt.continuity_snapshot.build()
            continuity = snapshot.model_dump(mode="json")
        except Exception:  # noqa: BLE001 - continuity is an aid, never a blocker
            rt._log.debug("[Cognitive] continuity snapshot unavailable", exc_info=True)  # noqa: SLF001

        experiences: list[dict[str, Any]] = []
        if config.experience_context_limit > 0 and rt.store.available:
            experiences = await rt.store.recent_experiences(
                character_id=rt.character_id,
                limit=config.experience_context_limit,
                min_importance=0.5,
            )

        world = rt.context()
        definition = rt.actions.definition(rt.current_action)
        return CognitiveContext(
            character_id=rt.character_id,
            query=query,
            current_world=world,
            current_action=definition.name if definition is not None else "",
            current_location=rt.spaces.name(rt.character.location),
            continuity=continuity,
            relevant_memories=memories,
            recent_experiences=experiences,
            retrieval=retrieval,
        )
