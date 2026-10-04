"""Relationship system: per-user interaction history and familiarity stage.

Deliberately NOT a gamified score (no 恋爱值/好感度 numbers). Stages reflect
real interaction history:

    new (< 5 interactions) → familiar (< 30) → close (< 100) → very_close

``core`` sits above all of them: it is not earned by counts but assigned to the
QQ ids the operator configured as core friends (``sandbox.core_friend_identities``
/ ``sandbox.core_friend_ids``), so 空凛 is a core friend in the relationship
table exactly as she is in the sandbox.

Only observable, chat-useful facts belong here; the memory extractor must
never write personality judgements about users (enforced in memory.manager).
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    from app.database.database import Database

STAGES = ("new", "familiar", "close", "very_close", "core")

#: the stage a configured core friend always holds — the top of :data:`STAGES`,
#: never derived from interaction counts (a core friend is one by identity,
#: not by how often they happened to talk).
CORE_STAGE = "core"

#: prompt/UI labels (the raw stage ids stay the machine values)
STAGE_LABELS: dict[str, str] = {
    "new": "刚认识",
    "familiar": "熟悉",
    "close": "亲近",
    "very_close": "非常亲密",
    "core": "核心好友",
}


def stage_label(stage: str) -> str:
    """Human label for a stage id (falls back to the raw value)."""
    return STAGE_LABELS.get(stage, stage)


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
        core_friend_ids: Iterable[str] = (),
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Character")
        self._clock = clock
        self._cache: dict[str, Relationship] = {}
        #: QQ ids the operator configured as core friends — their stage is
        #: :data:`CORE_STAGE` regardless of interaction counts.
        self._core_ids: set[str] = {str(uid) for uid in core_friend_ids if str(uid)}

    # --------------------------------------------------------- core friends

    def set_core_friends(self, ids: Iterable[str]) -> None:
        """Register the configured core friends (idempotent)."""
        self._core_ids = {str(uid) for uid in ids if str(uid)}

    @property
    def core_friend_ids(self) -> set[str]:
        return set(self._core_ids)

    def is_core(self, user_id: int | str) -> bool:
        return str(user_id) in self._core_ids

    def _stage_for(self, user_id: int | str, count: int) -> str:
        """Core friends keep the top stage; everyone else advances by count."""
        return CORE_STAGE if self.is_core(user_id) else stage_for_interactions(count)

    async def sync_core_stages(self) -> int:
        """Bring persisted rows in line with the configured core friends.

        Called once at startup: without it a core friend's row would keep the
        count-derived stage it had before they were configured (the WebUI reads
        the table directly, so an in-memory override alone would not show).
        """
        if self._db is None or not self._core_ids:
            return 0
        ids = sorted(self._core_ids)
        placeholders = ",".join("?" for _ in ids)
        try:
            rows = await self._db.fetchall(
                f"""SELECT user_id FROM relationships
                    WHERE user_id IN ({placeholders}) AND stage != ?""",
                (*ids, CORE_STAGE),
            )
            stale = [str(row["user_id"]) for row in rows]
            if not stale:
                return 0
            await self._db.execute(
                f"""UPDATE relationships SET stage = ?, updated_at = ?
                    WHERE user_id IN ({placeholders}) AND stage != ?""",
                (CORE_STAGE, int(self._clock()), *ids, CORE_STAGE),
            )
        except Exception:  # noqa: BLE001 - a broken sync must not stop startup
            self._log.exception("Failed to sync core friend stages")
            return 0
        for key in stale:
            self._cache.pop(key, None)  # re-read with the corrected stage
        self._log.info("[Relationship] %d core friend stage(s) set to %s", len(stale), CORE_STAGE)
        return len(stale)

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
            "stage": self._stage_for(key, rel.interaction_count + 1),
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
        result: list[Relationship] = []
        for row in rows:
            rel = Relationship.model_validate(dict(row))
            if self.is_core(rel.user_id) and rel.stage != CORE_STAGE:
                rel = rel.model_copy(update={"stage": CORE_STAGE})
            result.append(rel)
        return result

    # ------------------------------------------------------------ internals

    async def _load(self, key: str) -> None:
        if self._db is None:
            self._cache[key] = Relationship(user_id=key, stage=self._stage_for(key, 0))
            return
        try:
            row = await self._db.fetchone("SELECT * FROM relationships WHERE user_id = ?", (key,))
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to load relationship for %s", key)
            row = None
        if row is None:
            self._cache[key] = Relationship(user_id=key, stage=self._stage_for(key, 0))
            return
        rel = Relationship.model_validate(dict(row))
        if self.is_core(key) and rel.stage != CORE_STAGE:
            # a configured core friend keeps the top stage no matter what the
            # count-derived value in the table says (sync_core_stages persists)
            rel = rel.model_copy(update={"stage": CORE_STAGE})
        self._cache[key] = rel

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
