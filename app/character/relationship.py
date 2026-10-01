"""Relationship system: per-user interaction history and familiarity stage.

Deliberately NOT a gamified score (no 恋爱值/好感度 numbers). Stages reflect
real interaction history:

    new (< 5 interactions) → familiar (< 30) → close (< 100) → very_close

Only observable, chat-useful facts belong here; the memory extractor must
never write personality judgements about users (enforced in memory.manager).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.database.database import Database

STAGES = ("new", "familiar", "close", "very_close")


def stage_for_interactions(count: int) -> str:
    if count >= 100:
        return "very_close"
    if count >= 30:
        return "close"
    if count >= 5:
        return "familiar"
    return "new"


class Relationship(BaseModel):
    user_id: str
    stage: str = "new"
    preferred_tone: str = ""
    notes: str = ""
    interaction_count: int = 0
    first_seen: int = 0
    last_seen: int = 0

    def to_json(self) -> str:
        import json

        return json.dumps(self.model_dump(), ensure_ascii=False)


class RelationshipManager:
    def __init__(
        self,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Character")
        self._clock = clock
        self._cache: dict[str, Relationship] = {}

    async def get(self, user_id: int | str) -> Relationship:
        key = str(user_id)
        if key not in self._cache:
            await self._load(key)
        return self._cache[key]

    async def record_interaction(
        self, user_id: int | str, preferred_tone: str | None = None
    ) -> Relationship:
        """Count one interaction and advance the stage automatically."""
        key = str(user_id)
        rel = await self.get(key)
        now = int(self._clock())
        changes: dict[str, Any] = {
            "interaction_count": rel.interaction_count + 1,
            "last_seen": now,
            "stage": stage_for_interactions(rel.interaction_count + 1),
        }
        if not rel.first_seen:
            changes["first_seen"] = now
        if preferred_tone:
            changes["preferred_tone"] = preferred_tone
        updated = rel.model_copy(update=changes)
        self._cache[key] = updated
        await self._persist(updated)
        return updated

    async def set_notes(self, user_id: int | str, notes: str) -> Relationship:
        key = str(user_id)
        rel = await self.get(key)
        updated = rel.model_copy(update={"notes": notes})
        self._cache[key] = updated
        await self._persist(updated)
        return updated

    async def all(self) -> list[Relationship]:
        if self._db is None:
            return list(self._cache.values())
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM relationships ORDER BY interaction_count DESC"
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to list relationships")
            return list(self._cache.values())
        return [Relationship.model_validate(dict(row)) for row in rows]

    # ------------------------------------------------------------ internals

    async def _load(self, key: str) -> None:
        if self._db is None:
            self._cache[key] = Relationship(user_id=key)
            return
        try:
            row = await self._db.fetchone("SELECT * FROM relationships WHERE user_id = ?", (key,))
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to load relationship for %s", key)
            row = None
        self._cache[key] = (
            Relationship.model_validate(dict(row)) if row is not None else Relationship(user_id=key)
        )

    async def _persist(self, rel: Relationship) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO relationships
                       (user_id, stage, preferred_tone, notes, interaction_count,
                        first_seen, last_seen, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(user_id) DO UPDATE SET
                       stage=excluded.stage, preferred_tone=excluded.preferred_tone,
                       notes=excluded.notes, interaction_count=excluded.interaction_count,
                       last_seen=excluded.last_seen, updated_at=excluded.updated_at""",
                (
                    rel.user_id,
                    rel.stage,
                    rel.preferred_tone,
                    rel.notes,
                    rel.interaction_count,
                    rel.first_seen,
                    rel.last_seen,
                    int(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to persist relationship for %s", rel.user_id)
