"""Vector store abstraction + SQLite-backed implementation.

Swappable by design (spec v0.5 §16): the interface is what the memory system uses,
so a future pgvector/Qdrant backend only has to implement ``VectorStore``.
The SQLite implementation stores each vector as a float64 blob plus a
precomputed norm, and computes cosine similarity in Python, but **only over an
already-filtered candidate set** (spec v0.5 §18/§19), never over the whole table.

Scoring is one ``math.sumprod`` over the blob (the norm is stored, the query
norm is computed once per search). The JSON column that predated migration 16
was dropped in migration 20 once the blob path had proven itself on real data —
the blob is now the single source of truth.

Re-evaluate numpy / sqlite-vec when *any* of these becomes true — until then
the pool is ≤ ~200 rows of dim 1024 and this path costs ~18 ms per search:

* candidate-pool search p95 > 50 ms on real data,
* the candidate cap grows to ≥ 1000 rows,
* approximate (ANN) retrieval is needed.
"""

from __future__ import annotations

import logging
import math
import time
from abc import ABC, abstractmethod
from array import array
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database

CURRENT_VERSION = 1


def encode_vector(vector: list[float]) -> tuple[bytes, float]:
    """float64 bytes + precomputed norm (native byte order, as ``array`` gives)."""
    return array("d", vector).tobytes(), math.sqrt(math.sumprod(vector, vector))


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Cosine similarity clamped to 0..1 (negative angles count as unrelated).

    Kept as the *raw* cosine — not rescaled — because the configured
    thresholds mean absolute values: ~0.92 duplicate, ~0.75 related.
    This is the reference implementation; the store uses the blob path, which
    agrees with it to the last ULP.
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
        blob, norm = encode_vector(vector)
        await self._db.execute(
            """INSERT INTO memory_embeddings
                   (memory_id, model, dimensions, version, vector_blob, norm,
                    created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(memory_id) DO UPDATE SET
                   model=excluded.model, dimensions=excluded.dimensions,
                   version=excluded.version,
                   vector_blob=excluded.vector_blob, norm=excluded.norm,
                   updated_at=excluded.updated_at""",
            (
                memory_id,
                model,
                len(vector),
                version,
                blob,
                norm,
                now,
                now,
            ),
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
        query_norm = math.sqrt(math.sumprod(query_vector, query_vector))
        if query_norm <= 0:
            return []
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = await self._db.fetchall(
            f"SELECT * FROM memory_embeddings WHERE memory_id IN ({placeholders})",
            tuple(candidate_ids),
        )
        scored: list[tuple[int, float]] = []
        for row in rows:
            data = dict(row)
            vector, norm = self._decode_vector(data)
            if len(vector) != len(query_vector):
                continue  # different embedding model/dimension — skip, never compare
            if norm <= 0:
                norm = math.sqrt(math.sumprod(vector, vector))  # legacy row: no stored norm
            if norm <= 0:
                scored.append((int(data["memory_id"]), 0.0))  # zero vector → unrelated
                continue
            dot = math.sumprod(vector, query_vector)
            scored.append((int(data["memory_id"]), max(0.0, min(1.0, dot / (norm * query_norm)))))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def _decode_vector(data: dict[str, Any]) -> tuple[list[float], float]:
        """(vector, norm) from the float64 blob — the single source of truth."""
        dimensions = int(data.get("dimensions") or 0)
        blob = data.get("vector_blob")
        if blob:
            try:
                values = array("d")
                values.frombytes(bytes(blob))
                if not dimensions or len(values) == dimensions:
                    return list(values), float(data.get("norm") or 0.0)
            except (TypeError, ValueError):
                pass  # corrupt blob → empty vector below
        return [], 0.0

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
        """Active memories without a vector — drives coverage + rebuild (spec v0.5 §57)."""
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
        vector, _norm = SqliteVectorStore._decode_vector(data)
        return VectorRecord(
            memory_id=int(data["memory_id"]),
            model=data["model"],
            dimensions=int(data["dimensions"]),
            version=int(data.get("version", CURRENT_VERSION)),
            vector=vector,
            created_at=int(data.get("created_at", 0)),
            updated_at=int(data.get("updated_at", 0)),
        )
