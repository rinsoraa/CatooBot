"""Continuity snapshot (v2.1 Phase 4 §23-§25): a read model, nothing more.

    World State + Recent Events + Knowledge + Important Memories
        + Unfinished Work → ContinuitySnapshot

The snapshot is *generated* from data others own — it never writes world
state, never feeds the decision engine, persona, speech or relationship
layers (§25). Phase 4 only has to prove the composition is right; using it
as conversation context is a later phase.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field


class ContinuitySnapshot(BaseModel):
    """What must survive a restart for the character to feel continuous (§23)."""

    character_id: str = ""
    generated_at: float = 0.0
    #: one-line world state (location / action / modes / needs)
    current_world_state_summary: str = ""
    current_action: str = ""
    current_location: str = ""
    active_knowledge: list[dict[str, Any]] = Field(default_factory=list)
    important_recent_experiences: list[dict[str, Any]] = Field(default_factory=list)
    active_memories: list[dict[str, Any]] = Field(default_factory=list)
    unfinished_projects: list[dict[str, Any]] = Field(default_factory=list)
    pending_external_events: list[dict[str, Any]] = Field(default_factory=list)
    #: stage/interaction counts from the existing relationship model (§23)
    relationship_context: dict[str, Any] = Field(default_factory=dict)


class ContinuitySnapshotBuilder:
    """Composes the snapshot from the runtime's own data (§24)."""

    #: how many recent experiences / memories the snapshot carries
    EXPERIENCE_LIMIT = 8
    MEMORY_LIMIT = 10

    def __init__(self, runtime: Any, *, clock: Any) -> None:
        self._rt = runtime
        self._clock = clock

    async def build(self) -> ContinuitySnapshot:
        rt = self._rt
        definition = rt.actions.definition(rt.current_action)
        action_line = definition.name if definition is not None else "闲着"
        snapshot = ContinuitySnapshot(
            character_id=getattr(rt, "character_id", ""),
            generated_at=float(self._clock()),
            current_world_state_summary=rt.status_line(),
            current_action=action_line,
            current_location=rt.spaces.name(rt.character.location),
            active_knowledge=[
                {
                    "key": key,
                    "known": bool(entry.get("known")),
                    "source": entry.get("source", ""),
                    "learned_at": float(entry.get("learned_at", 0) or 0),
                }
                for key, entry in rt.knowledge.items()
                if entry.get("known")
            ],
            important_recent_experiences=await self._recent_experiences(),
            active_memories=await self._active_memories(),
            unfinished_projects=[
                {
                    "id": project_id,
                    "name": payload.get("name", project_id),
                    "progress": float(payload.get("progress", 0.0)),
                    "next_action": payload.get("next_action", ""),
                }
                for project_id, payload in rt.projects.items()
                if float(payload.get("progress", 0.0)) < 1.0
            ],
            pending_external_events=[
                {
                    "event_id": event.event_id,
                    "source": event.source.value,
                    "event_type": event.event_type,
                    "urgency": event.urgency.value,
                }
                for event in rt.external_queue.pending()
            ],
            relationship_context=self._relationship_context(),
        )
        return snapshot

    async def _recent_experiences(self) -> list[dict[str, Any]]:
        rt = self._rt
        rows = await rt.store.recent_experiences(
            character_id=rt.character_id, limit=self.EXPERIENCE_LIMIT
        )
        if rows:
            return rows
        # fall back to the in-memory audit trail (fresh runtime, no flush yet)
        return [
            {
                "id": record.id,
                "kind": record.kind.value,
                "summary": record.summary,
                "importance": record.importance,
                "created_at": record.timestamp,
            }
            for record in sorted(
                rt.experiences.emitted(), key=lambda r: r.importance, reverse=True
            )[: self.EXPERIENCE_LIMIT]
        ]

    async def _active_memories(self) -> list[dict[str, Any]]:
        rt = self._rt
        if getattr(rt, "memory", None) is None:
            return []
        return await rt.memory.active_memories(limit=self.MEMORY_LIMIT)

    def _relationship_context(self) -> dict[str, Any]:
        """Existing relationship data only — never re-derived (§23)."""
        rt = self._rt
        context: dict[str, Any] = {}
        core = list(getattr(rt.seed, "core_friend_names", []) or [])
        if core:
            context["core_friends"] = core
        social = getattr(rt, "social_spaces", {})
        active = [space.id for space in social.values() if space.character_presence == "active"]
        if active:
            context["active_social_spaces"] = active
        return context

    #: legacy (pre-remediation) fixed key, kept readable for old databases
    LEGACY_KEY = "continuity_snapshot"

    def _key(self) -> str:
        """Character-scoped state key — snapshots never overwrite each other."""
        return f"{self.LEGACY_KEY}:{getattr(self._rt, 'character_id', '')}"

    async def persist(self, snapshot: ContinuitySnapshot | None = None) -> ContinuitySnapshot:
        """Store the latest snapshot, scoped to this character (§remediation-1)."""
        rt = self._rt
        snapshot = snapshot or await self.build()
        await rt.store.state_set(
            self._key(), json.dumps(snapshot.model_dump(mode="json"), ensure_ascii=False)
        )
        return snapshot

    async def load(self) -> dict[str, Any] | None:
        """Read this character's snapshot; fall back to the legacy key only
        when it is unambiguously *this* character's (never mis-attributed)."""
        raw = await self._rt.store.state_get(self._key())
        if raw:
            return self._parse(raw)
        legacy = await self._rt.store.state_get(self.LEGACY_KEY)
        if not legacy:
            return None
        payload = self._parse(legacy)
        if payload is None:
            return None
        # an old snapshot without a reliable owner stays unclaimed (§4)
        if str(payload.get("character_id", "")) != str(getattr(self._rt, "character_id", "")):
            return None
        return payload

    @staticmethod
    def _parse(raw: str) -> dict[str, Any] | None:
        try:
            payload = json.loads(raw)
        except ValueError:
            return None
        return payload if isinstance(payload, dict) else None
