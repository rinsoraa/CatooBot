"""Vector blobs + precomputed norms (Task 12, step 2 — option A).

The blob stores the same float64 values as the JSON column (bit-identical), so
thresholds (~0.92 duplicate / ~0.75 related) and every existing number stay
put. What changed is the work per search: parse + two norms (159 ms on a real
200 × 1024 pool) became one ``math.sumprod`` over the blob (18 ms).

Covered here: blob/JSON row equivalence, upsert keeping both forms in sync,
legacy-row fallback and backfill, corrupt-blob resilience, and top-k parity
with the reference implementation over the same candidate set.
"""

from __future__ import annotations

import json
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


async def insert_legacy_row(db: Database, memory_id: int, vector: list[float]) -> None:
    """A row exactly as it looked before migration 16: JSON only, no blob/norm."""
    await db.execute(
        "INSERT INTO memory_embeddings"
        " (memory_id, model, dimensions, version, vector, created_at, updated_at)"
        " VALUES (?, 'm', ?, 1, ?, 0, 0)",
        (memory_id, len(vector), json.dumps(vector)),
    )


def reference_ranking(
    rows: list[tuple[int, str]], query: list[float], limit: int = 20
) -> list[tuple[int, float]]:
    """The pre-migration scoring path: JSON parse + reference cosine."""
    scored: list[tuple[int, float]] = []
    for memory_id, vector_text in rows:
        vector = [float(x) for x in json.loads(vector_text)]
        if len(vector) != len(query):
            continue
        scored.append((memory_id, cosine_similarity(vector, query)))
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:limit]


class TestBlobFormat:
    async def test_upsert_writes_both_forms_and_a_norm(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            vector = [0.1, 0.2, 0.3]
            await store.upsert(1, vector, "m")
            row = await db.fetchone("SELECT * FROM memory_embeddings WHERE memory_id = 1")
            assert json.loads(row["vector"]) == vector  # JSON column unchanged
            assert row["vector_blob"] is not None
            assert row["norm"] == math.sqrt(math.sumprod(vector, vector))
            assert (await store.get(1)).vector == vector  # read path handles blobs
        finally:
            await db.close()

    async def test_upsert_updates_both_forms_in_place(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await store.upsert(1, [0.1, 0.2], "m")
            await store.upsert(1, [0.3, 0.4], "m")
            row = await db.fetchone("SELECT * FROM memory_embeddings WHERE memory_id = 1")
            assert json.loads(row["vector"]) == [0.3, 0.4]
            assert (await store.get(1)).vector == [0.3, 0.4]
        finally:
            await db.close()


class TestBlobJsonEquivalence:
    async def test_same_vector_scores_identically_from_both_forms(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(11)
            query = random_vector(rng, dim=64)
            vector = random_vector(rng, dim=64)
            await store.upsert(1, vector, "m")  # blob + JSON
            await insert_legacy_row(db, 2, vector)  # JSON only

            scored = dict(await store.search([1, 2], query, limit=10))
            assert scored[1] == scored[2]  # bit-identical: same values, same math
            assert scored[1] > 0
        finally:
            await db.close()

    async def test_legacy_rows_score_without_a_stored_norm(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(12)
            query = random_vector(rng, dim=64)
            await insert_legacy_row(db, 7, query)  # identical direction
            scored = await store.search([7], query, limit=5)
            assert scored and scored[0][0] == 7
            assert scored[0][1] == 1.0  # same vector → cosine 1.0
        finally:
            await db.close()

    async def test_corrupt_blob_falls_back_to_json(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            vector = [0.5, 0.5]
            await store.upsert(1, vector, "m")
            await db.execute(
                "UPDATE memory_embeddings SET vector_blob = ? WHERE memory_id = 1",
                (b"\x01\x02\x03",),  # not a whole number of float64s
            )
            assert (await store.get(1)).vector == vector
            assert await store.search([1], vector, limit=5)
        finally:
            await db.close()


class TestBackfill:
    async def test_backfill_packs_legacy_rows_without_changing_scores(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(13)
            query = random_vector(rng, dim=64)
            vectors = {memory_id: random_vector(rng, dim=64) for memory_id in (1, 2, 3)}
            for memory_id, vector in vectors.items():
                await insert_legacy_row(db, memory_id, vector)

            before = await store.search(list(vectors), query, limit=5)
            assert await store.backfill() == 3
            after = await store.search(list(vectors), query, limit=5)
            assert before == after
            rows = await db.fetchall("SELECT vector_blob, norm FROM memory_embeddings")
            assert all(row["vector_blob"] is not None and row["norm"] > 0 for row in rows)
        finally:
            await db.close()

    async def test_ensure_ready_backfills_once(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(14)
            await insert_legacy_row(db, 1, random_vector(rng, dim=8))
            assert await store.ensure_ready() == 1
            assert await store.ensure_ready() == 0
        finally:
            await db.close()

    async def test_unreadable_row_is_left_alone(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            await db.execute(
                "INSERT INTO memory_embeddings (memory_id, model, dimensions, version, vector,"
                " created_at, updated_at) VALUES (9, 'm', 4, 1, 'not json', 0, 0)"
            )
            assert await store.backfill() == 0
            assert await store.search([9], [1.0, 0.0, 0.0, 0.0], limit=5) == []
        finally:
            await db.close()


class TestBehaviourParity:
    async def test_dimension_mismatch_is_still_skipped(self, tmp_path) -> None:
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
            assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0  # same reference
        finally:
            await db.close()

    async def test_blob_path_agrees_with_the_reference_cosine(self, tmp_path) -> None:
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(15)
            query = random_vector(rng, dim=256)
            for memory_id in range(1, 11):
                await store.upsert(memory_id, random_vector(rng, dim=256), "m")
            rows = await db.fetchall("SELECT memory_id, vector FROM memory_embeddings")
            reference = dict(
                reference_ranking([(int(r["memory_id"]), r["vector"]) for r in rows], query)
            )
            for memory_id, score in await store.search(list(reference), query, limit=10):
                assert score == reference[memory_id] or abs(score - reference[memory_id]) < 1e-9
        finally:
            await db.close()


class TestScale:
    async def test_same_top_k_as_the_old_path_and_faster(self, tmp_path) -> None:
        """A real pool: ``limit=200`` candidates of dim 1024 (the production cap)."""
        store, db = await make_store(tmp_path)
        try:
            rng = random.Random(16)
            query = random_vector(rng)
            for memory_id in range(1, 201):
                await store.upsert(memory_id, random_vector(rng), "m")
            rows = await db.fetchall("SELECT memory_id, vector FROM memory_embeddings")
            legacy = [(int(row["memory_id"]), row["vector"]) for row in rows]
            candidate_ids = [memory_id for memory_id, _ in legacy]

            expected = reference_ranking(legacy, query, limit=20)

            started = time.perf_counter()
            for _ in range(5):
                got = await store.search(candidate_ids, query, limit=20)
            new_ms = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            for _ in range(5):
                reference_ranking(legacy, query, limit=20)
            old_ms = (time.perf_counter() - started) * 1000

            assert [memory_id for memory_id, _ in got] == [i for i, _ in expected]
            for (got_id, got_score), (want_id, want_score) in zip(got, expected, strict=True):
                assert got_id == want_id
                assert abs(got_score - want_score) < 1e-9
            assert new_ms < old_ms
        finally:
            await db.close()
