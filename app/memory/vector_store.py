"""Vector store abstraction + SQLite-backed implementation.

Swappable by design (spec §16): the interface is what the memory system uses,
so a future pgvector/Qdrant backend only has to implement ``VectorStore``.
The SQLite implementation keeps vectors as JSON and computes cosine
similarity in Python — but **only over an already-filtered candidate set**
(spec §18/§19), never over the whole table.
"""

from __future__ import annotations

import json
import logging
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database

CURRENT_VERSION = 1


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity clamped to 0..1 (negative angles count as unrelated).

    Kept as the *raw* cosine — not rescaled — because the configured
    thresholds mean absolute values: ~0.92 duplicate, ~0.75 related.
    """
    if not left or not right or len(left) != len(right):
        return 0.0
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    norm_left = math.sqrt(sum(a * a for a in left))
    norm_right = math.sqrt(sum(b * b for b in right))
    if norm_left == 0 or norm_right == 0:
        return 0.0
    return max(0.0, min(1.0, dot / (norm_left * norm_right)))


@dataclass
class VectorRecord:
    memory_id: int
    model: str
    dimensions: int
    version: int
    vector: list[float]
    created_at: int = 0
    updated_at: int = 0


class VectorStore(ABC):
    @abstractmethod
    async def upsert(
        self, memory_id: int, vector: list[float], model: str, version: int = CURRENT_VERSION
    ) -> None: ...

    @abstractmethod
    async def delete(self, memory_id: int) -> None: ...

    @abstractmethod
    async def get(self, memory_id: int) -> VectorRecord | None: ...

    @abstractmethod
    async def search(
        self, candidate_ids: list[int], query_vector: list[float], limit: int = 20
    ) -> list[tuple[int, float]]: ...

    @abstractmethod
    async def stats(self) -> dict[str, Any]: ...


class SqliteVectorStore(VectorStore):
    def __init__(
        self,
        database: Database,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Memory.Semantic")
        self._clock = clock

    async def upsert(
        self, memory_id: int, vector: list[float], model: str, version: int = CURRENT_VERSION
    ) -> None:
        now = int(self._clock())
        await self._db.execute(
            """INSERT INTO memory_embeddings
                   (memory_id, model, dimensions, version, vector, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(memory_id) DO UPDATE SET
                   model=excluded.model, dimensions=excluded.dimensions,
                   version=excluded.version, vector=excluded.vector,
                   updated_at=excluded.updated_at""",
            (memory_id, model, len(vector), version, json.dumps(vector), now, now),
        )

    async def delete(self, memory_id: int) -> None:
        await self._db.execute("DELETE FROM memory_embeddings WHERE memory_id = ?", (memory_id,))

    async def get(self, memory_id: int) -> VectorRecord | None:
        row = await self._db.fetchone(
            "SELECT * FROM memory_embeddings WHERE memory_id = ?", (memory_id,)
        )
        return self._to_record(row) if row else None

    async def search(
        self, candidate_ids: list[int], query_vector: list[float], limit: int = 20
    ) -> list[tuple[int, float]]:
        if not candidate_ids or not query_vector:
            return []
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = await self._db.fetchall(
            f"SELECT * FROM memory_embeddings WHERE memory_id IN ({placeholders})",
            tuple(candidate_ids),
        )
        scored: list[tuple[int, float]] = []
        for row in rows:
            record = self._to_record(row)
            if len(record.vector) != len(query_vector):
                continue  # different embedding model/dimension — skip, never compare
            scored.append((record.memory_id, cosine_similarity(record.vector, query_vector)))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    async def stats(self) -> dict[str, Any]:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT model) AS models FROM memory_embeddings"
        )
        total = int(row["n"]) if row else 0
        memory_row = await self._db.fetchone("SELECT COUNT(*) AS n FROM memories")
        total_memories = int(memory_row["n"]) if memory_row else 0
        return {
            "embedded": total,
            "memories": total_memories,
            "coverage": round(total / total_memories, 3) if total_memories else 0.0,
            "models": int(row["models"]) if row else 0,
        }

    async def missing_memory_ids(self, limit: int = 200) -> list[int]:
        """Active memories without a vector — drives coverage + rebuild (spec §57)."""
        rows = await self._db.fetchall(
            """SELECT m.id FROM memories m
               LEFT JOIN memory_embeddings e ON e.memory_id = m.id
               WHERE e.memory_id IS NULL AND m.status = 'active'
               ORDER BY m.id DESC LIMIT ?""",
            (limit,),
        )
        return [int(row["id"]) for row in rows]

    @staticmethod
    def _to_record(row: Any) -> VectorRecord:
        data = dict(row)
        try:
            vector = json.loads(data["vector"])
        except (TypeError, ValueError):
            vector = []
        return VectorRecord(
            memory_id=int(data["memory_id"]),
            model=data["model"],
            dimensions=int(data["dimensions"]),
            version=int(data.get("version", CURRENT_VERSION)),
            vector=[float(x) for x in vector],
            created_at=int(data.get("created_at", 0)),
            updated_at=int(data.get("updated_at", 0)),
        )
