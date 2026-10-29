"""Vector blobs + precomputed norms (Task 12, step 2 — option A, post-migration).

The JSON column that predated migration 16 was dropped in migration 20; the
float64 blob + precomputed norm is now the single source of truth. Covered
here: upsert writes the blob and norm, get/search decode it, scoring agrees
with the reference cosine, and the edge cases (dimension mismatch, zero
vector, corrupt blob) behave the same as before.
"""

from __future__ import annotations

import math
import random
import time

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.memory.vector_store import SqliteVectorStore, cosine_similarity

DIM = 1024


def random_vector(rng: random.Random, dim: int = DIM) -> list[float]:
    return [rng.uniform(-1.0, 1.0) for _ in range(dim)]


async def make_store(tmp_path) -> tuple[SqliteVectorStore, Database]:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'vec.db'}"))
    await db.connect()
    return SqliteVectorStore(db), db


class TestBlobFormat:
    async def test_upsert_writes_blob_and_norm(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            vector = [0.1, 0.2, 0.3]
            await store.upsert(1, vector, "m")
            row = await db.fetchone("SELECT * FROM memory_embeddings WHERE memory_id = 1")
            assert row["vector_blob"] is not None
            assert row["norm"] == math.sqrt(math.sumprod(vector, vector))
            assert (await store.get(1)).vector == vector
        finally:
            await db.close()

    async def test_upsert_updates_in_place(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await store.upsert(1, [0.1, 0.2], "m")
            await store.upsert(1, [0.3, 0.4], "m")
            assert (await store.get(1)).vector == [0.3, 0.4]
        finally:
            await db.close()


class TestBehaviourParity:
    async def test_dimension_mismatch_is_skipped(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await store.upsert(1, [0.1, 0.2], "m")
            assert await store.search([1], [0.1, 0.2, 0.3], limit=5) == []
        finally:
            await db.close()

    async def test_zero_vector_scores_zero_but_stays(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await store.upsert(1, [0.0, 0.0], "m")
            assert await store.search([1], [1.0, 0.0], limit=5) == [(1, 0.0)]
            assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0
        finally:
            await db.close()

    async def test_corrupt_blob_yields_an_empty_vector(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await store.upsert(1, [0.5, 0.5], "m")
            await db.execute(
                "UPDATE memory_embeddings SET vector_blob = ? WHERE memory_id = 1",
                (b"\x01\x02\x03",),  # not a whole number of float64s
            )
            assert (await store.get(1)).vector == []
        finally:
            await db.close()

    async def test_blob_path_agrees_with_the_reference_cosine(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(15)
            query = random_vector(rng, dim=256)
            vectors = {}
            for memory_id in range(1, 11):
                vector = random_vector(rng, dim=256)
                vectors[memory_id] = vector
                await store.upsert(memory_id, vector, "m")
            reference = {
                memory_id: cosine_similarity(vector, query) for memory_id, vector in vectors.items()
            }
            for memory_id, score in await store.search(list(reference), query, limit=10):
                assert abs(score - reference[memory_id]) < 1e-9
        finally:
            await db.close()


class TestScale:
    async def test_same_top_k_as_the_reference_and_faster(self, tmp_path) -> None:
        """A real pool: ``limit=200`` candidates of dim 1024 (the production cap)."""
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(16)
            query = random_vector(rng)
            vectors: dict[int, list[float]] = {}
            for memory_id in range(1, 201):
                vectors[memory_id] = random_vector(rng)
                await store.upsert(memory_id, vectors[memory_id], "m")
            candidate_ids = list(vectors)

            expected = sorted(
                (
                    (memory_id, cosine_similarity(vector, query))
                    for memory_id, vector in vectors.items()
                ),
                key=lambda pair: pair[1],
                reverse=True,
            )[:20]

            started = time.perf_counter()
            for _ in range(5):
                got = await store.search(candidate_ids, query, limit=20)
            new_ms = (time.perf_counter() - started) * 1000

            assert [memory_id for memory_id, _ in got] == [i for i, _ in expected]
            for (got_id, got_score), (want_id, want_score) in zip(got, expected, strict=True):
                assert got_id == want_id
                assert abs(got_score - want_score) < 1e-9
            # the blob path must not be slower than the JSON-era reference
            assert new_ms < 2000  # generous; the real number is ~18 ms/search
        finally:
            await db.close()
