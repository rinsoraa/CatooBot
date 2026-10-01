"""Memory repository: SQLite CRUD over ``memories`` (+ relations).

v0.5 keeps the v0.4 API intact and adds status/layer awareness: nothing is
physically deleted by the system — superseded/archived rows stay queryable so
the WebUI can show how a character's knowledge evolved.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from app.memory.keyword_index import index_text
from app.memory.model import Memory

if TYPE_CHECKING:
    from app.database.database import Database

_INSERT_COLUMNS = (
    "scope_key, user_id, group_id, category, content, content_hash, importance,"
    " confidence, use_count, created_at, updated_at, last_used_at,"
    " layer, summary, source, status, supersedes_id, conflicts_with_id,"
    " valid_from, valid_until, event_at, search_text"
)


def content_hash(content: str) -> str:
    """Normalized hash used for dedup (case/whitespace-insensitive)."""
    normalized = "".join(content.lower().split())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:24]


class MemoryRepository:
    def __init__(
        self,
        database: Database,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Memory")
        self._clock = clock

    # ---------------------------------------------------------------- write

    async def add(self, memory: Memory) -> Memory:
        now = int(self._clock())
        memory = memory.model_copy(
            update={"created_at": memory.created_at or now, "updated_at": now}
        )
        hash_value = content_hash(memory.content)
        placeholders = ", ".join("?" for _ in range(len(_INSERT_COLUMNS.split(","))))
        await self._db.execute(
            f"INSERT INTO memories ({_INSERT_COLUMNS}) VALUES ({placeholders})",
            (
                memory.scope_key,
                memory.user_id,
                memory.group_id,
                memory.category,
                memory.content,
                hash_value,
                memory.importance,
                memory.confidence,
                memory.use_count,
                memory.created_at,
                memory.updated_at,
                memory.last_used_at,
                memory.layer,
                memory.summary,
                memory.source,
                memory.status,
                memory.supersedes_id,
                memory.conflicts_with_id,
                memory.valid_from,
                memory.valid_until,
                memory.event_at,
                index_text(memory.content, memory.summary),
            ),
        )
        row = await self._db.fetchone(
            "SELECT * FROM memories WHERE scope_key = ? AND content_hash = ?"
            " ORDER BY id DESC LIMIT 1",
            (memory.scope_key, hash_value),
        )
        return Memory.model_validate(dict(row)) if row else memory

    async def update(self, memory: Memory) -> None:
        await self._db.execute(
            """UPDATE memories SET category=?, content=?, content_hash=?, importance=?,
                   confidence=?, updated_at=?, layer=?, summary=?, source=?, status=?,
                   supersedes_id=?, conflicts_with_id=?, valid_from=?, valid_until=?,
                   event_at=?, search_text=?
               WHERE id=?""",
            (
                memory.category,
                memory.content,
                content_hash(memory.content),
                memory.importance,
                memory.confidence,
                int(self._clock()),
                memory.layer,
                memory.summary,
                memory.source,
                memory.status,
                memory.supersedes_id,
                memory.conflicts_with_id,
                memory.valid_from,
                memory.valid_until,
                memory.event_at,
                index_text(memory.content, memory.summary),
                memory.id,
            ),
        )

    async def set_status(self, memory_id: int, status: str) -> bool:
        """Status changes keep the row (spec v0.5 §10/§11)."""
        from app.memory.model import STATUSES

        if status not in STATUSES:
            raise ValueError(f"Invalid status: {status!r}")
        row = await self.get(memory_id)
        if row is None:
            return False
        await self._db.execute(
            "UPDATE memories SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(self._clock()), memory_id),
        )
        return True

    async def delete(self, memory_id: int) -> bool:
        """Hard delete — WebUI only (spec v0.5 §11 keeps it out of the pipeline)."""
        row = await self._db.fetchone("SELECT id FROM memories WHERE id = ?", (memory_id,))
        if row is None:
            return False
        await self._db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        return True

    async def mark_unanswered_conflicts(self, new_id: int, old_id: int) -> None:
        await self._db.execute(
            "UPDATE memories SET status='superseded', conflicts_with_id=?, updated_at=?"
            " WHERE id = ?",
            (new_id, int(self._clock()), old_id),
        )

    # ------------------------------------------------------------ relations

    async def add_relation(self, from_id: int, to_id: int, relation: str) -> None:
        await self._db.execute(
            "INSERT INTO memory_relations (from_id, to_id, relation, created_at)"
            " VALUES (?, ?, ?, ?)",
            (from_id, to_id, relation, int(self._clock())),
        )

    async def relations(self, memory_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            """SELECT r.*, m.content AS related_content, m.status AS related_status
               FROM memory_relations r
               LEFT JOIN memories m ON m.id = r.to_id
               WHERE r.from_id = ? ORDER BY r.id DESC LIMIT 50""",
            (memory_id,),
        )
        return [dict(row) for row in rows]

    # ----------------------------------------------------------------- read

    async def get(self, memory_id: int) -> Memory | None:
        row = await self._db.fetchone("SELECT * FROM memories WHERE id = ?", (memory_id,))
        return Memory.model_validate(dict(row)) if row else None

    async def by_ids(self, memory_ids: list[int]) -> list[Memory]:
        """Fetch specific rows (pulls keyword-index hits into the candidate pool)."""
        if not memory_ids:
            return []
        placeholders = ",".join("?" for _ in memory_ids)
        rows = await self._db.fetchall(
            f"SELECT * FROM memories WHERE id IN ({placeholders})", tuple(memory_ids)
        )
        return [Memory.model_validate(dict(row)) for row in rows]

    async def find_same(self, scope_key: str, text: str) -> Memory | None:
        """Exact-duplicate lookup within a scope (normalized content hash)."""
        row = await self._db.fetchone(
            "SELECT * FROM memories WHERE scope_key = ? AND content_hash = ? LIMIT 1",
            (scope_key, content_hash(text)),
        )
        return Memory.model_validate(dict(row)) if row else None

    async def candidates(
        self,
        scope_keys: list[str],
        limit: int = 200,
        *,
        statuses: tuple[str, ...] = ("active",),
        layers: tuple[str, ...] | None = None,
    ) -> list[Memory]:
        """Scope + status filtered candidate set (spec v0.5 §19/§20)."""
        if not scope_keys:
            return []
        scope_placeholders = ",".join("?" for _ in scope_keys)
        sql = f"SELECT * FROM memories WHERE scope_key IN ({scope_placeholders})"
        params: list[Any] = list(scope_keys)
        if statuses:
            status_placeholders = ",".join("?" for _ in statuses)
            sql += f" AND status IN ({status_placeholders})"
            params.extend(statuses)
        if layers:
            layer_placeholders = ",".join("?" for _ in layers)
            sql += f" AND layer IN ({layer_placeholders})"
            params.extend(layers)
        sql += " ORDER BY importance DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))
        return [Memory.model_validate(dict(row)) for row in rows]

    async def mark_used(self, memory_ids: list[int]) -> None:
        """Retrieval reinforcement: usage only — never confidence (spec v0.5 §40/§42)."""
        if not memory_ids:
            return
        now = int(self._clock())
        placeholders = ",".join("?" for _ in memory_ids)
        await self._db.execute(
            f"""UPDATE memories SET use_count = use_count + 1, last_used_at = ?
                WHERE id IN ({placeholders})""",
            (now, *memory_ids),
        )

    async def search(
        self,
        *,
        keyword: str = "",
        scope_key: str = "",
        category: str = "",
        layer: str = "",
        status: str = "",
        source: str = "",
        limit: int = 100,
        offset: int = 0,
    ) -> list[Memory]:
        """WebUI search: keyword LIKE plus metadata filters (spec v0.5 §54)."""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if keyword:
            sql += " AND (content LIKE ? OR summary LIKE ?)"
            params.extend([f"%{keyword}%", f"%{keyword}%"])
        if scope_key:
            sql += " AND scope_key = ?"
            params.append(scope_key)
        if category:
            sql += " AND category = ?"
            params.append(category)
        if layer:
            sql += " AND layer = ?"
            params.append(layer)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if source:
            sql += " AND source = ?"
            params.append(source)
        sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = await self._db.fetchall(sql, tuple(params))
        return [Memory.model_validate(dict(row)) for row in rows]

    async def timeline(self, scope_key: str = "", limit: int = 200) -> list[Memory]:
        """Chronological view (spec v0.5 §53) — oldest first for a readable story."""
        sql = "SELECT * FROM memories WHERE 1=1"
        params: list[Any] = []
        if scope_key:
            sql += " AND scope_key = ?"
            params.append(scope_key)
        sql += " ORDER BY COALESCE(event_at, created_at) ASC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))
        return [Memory.model_validate(dict(row)) for row in rows]

    async def count(
        self,
        scope_key_filter: str = "",
        status: str = "active",
        layer: str = "",
    ) -> int:
        sql = "SELECT COUNT(*) AS n FROM memories WHERE 1=1"
        params: list[Any] = []
        if scope_key_filter:
            sql += " AND scope_key = ?"
            params.append(scope_key_filter)
        if status:
            sql += " AND status = ?"
            params.append(status)
        if layer:
            sql += " AND layer = ?"
            params.append(layer)
        row = await self._db.fetchone(sql, tuple(params))
        return int(row["n"]) if row else 0

    async def status_counts(self) -> dict[str, int]:
        rows = await self._db.fetchall("SELECT status, COUNT(*) AS n FROM memories GROUP BY status")
        return {row["status"]: int(row["n"]) for row in rows}

    async def all_scopes(self) -> list[str]:
        rows = await self._db.fetchall("SELECT DISTINCT scope_key FROM memories ORDER BY scope_key")
        return [row["scope_key"] for row in rows]

    # -------------------------------------------------------------- helpers

    @staticmethod
    def parse_vector(raw: str) -> list[float]:
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return [float(x) for x in data] if isinstance(data, list) else []
