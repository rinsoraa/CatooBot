"""Embedding provider + cache + vector store tests (spec v0.5 §76)."""

from __future__ import annotations

import httpx
import pytest

from app.config.settings import MemoryEmbeddingConfig
from app.memory.embedding import (
    EmbeddingError,
    EmbeddingService,
    OpenAICompatibleEmbeddingProvider,
)
from app.memory.vector_store import SqliteVectorStore, cosine_similarity


def make_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'emb.db'}"))


def embedding_body(vectors: list[list[float]]) -> dict:
    return {
        "data": [{"index": i, "embedding": v} for i, v in enumerate(vectors)],
        "model": "test-embed",
    }


def make_provider(handler) -> OpenAICompatibleEmbeddingProvider:  # type: ignore[no-untyped-def]
    return OpenAICompatibleEmbeddingProvider(
        name="test",
        base_url="https://api.test/v1",
        api_key="sk-test",
        model="test-embed",
        timeout=5.0,
        transport=httpx.MockTransport(handler),
    )


class TestOpenAIEmbeddingProvider:
    async def test_success(self) -> None:
        provider = make_provider(
            lambda request: httpx.Response(200, json=embedding_body([[0.1, 0.2, 0.3]]))
        )
        vectors = await provider.embed(["你好"])
        assert vectors == [[0.1, 0.2, 0.3]]

    async def test_request_shape(self) -> None:
        captured: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request)
            import json as _json

            count = len(_json.loads(request.content)["input"])
            return httpx.Response(200, json=embedding_body([[1.0, 0.0]] * count))

        provider = make_provider(handler)
        await provider.embed(["a", "b"])
        request = captured[0]
        assert request.url.path == "/v1/embeddings"
        assert request.headers["Authorization"] == "Bearer sk-test"
        import json

        assert json.loads(request.content)["model"] == "test-embed"

    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow")

        provider = make_provider(handler)
        with pytest.raises(EmbeddingError):
            await provider.embed(["x"])

    async def test_provider_error_status(self) -> None:
        provider = make_provider(lambda request: httpx.Response(500, text="boom"))
        with pytest.raises(EmbeddingError):
            await provider.embed(["x"])

    async def test_invalid_response(self) -> None:
        provider = make_provider(lambda request: httpx.Response(200, json={"nope": 1}))
        with pytest.raises(EmbeddingError):
            await provider.embed(["x"])

    async def test_count_mismatch(self) -> None:
        provider = make_provider(lambda request: httpx.Response(200, json=embedding_body([[0.1]])))
        with pytest.raises(EmbeddingError):
            await provider.embed(["a", "b"])

    async def test_dimension_mismatch(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=embedding_body([[0.1, 0.2]]))

        provider = OpenAICompatibleEmbeddingProvider(
            name="t",
            base_url="https://api.test/v1",
            api_key="k",
            model="m",
            dimensions=3,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(EmbeddingError):
            await provider.embed(["x"])


class TestEmbeddingService:
    async def test_cache_hit_avoids_second_call(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=embedding_body([[0.5, 0.5]]))

        service = EmbeddingService(
            MemoryEmbeddingConfig(model="m"),
            database,
            provider=make_provider(handler),
        )
        first = await service.embed_one("同样的文本")
        second = await service.embed_one("同样的文本")
        assert first == second == [0.5, 0.5]
        assert calls["n"] == 1  # cached
        assert service.hits >= 1
        await database.close()

    async def test_cache_persists_across_instances(self, tmp_path) -> None:
        db_path = tmp_path / "cache.db"
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{db_path}"))
        await database.connect()
        calls = {"n": 0}

        def handler(request: httpx.Request) -> httpx.Response:
            calls["n"] += 1
            return httpx.Response(200, json=embedding_body([[0.1, 0.9]]))

        service = EmbeddingService(
            MemoryEmbeddingConfig(model="m"), database, provider=make_provider(handler)
        )
        await service.embed_one("持久化缓存")
        await database.close()

        database2 = Database(DatabaseConfig(url=f"sqlite:///{db_path}"))
        await database2.connect()
        service2 = EmbeddingService(
            MemoryEmbeddingConfig(model="m"), database2, provider=make_provider(handler)
        )
        vector = await service2.embed_one("持久化缓存")
        assert vector == [0.1, 0.9]
        assert calls["n"] == 1  # served from the SQLite cache
        await database2.close()

    async def test_provider_failure_returns_none(self, tmp_path) -> None:
        """A broken embedding API must degrade, not raise (spec v0.5 §60/§83)."""
        database = make_db(tmp_path)
        await database.connect()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(500, text="down")

        service = EmbeddingService(
            MemoryEmbeddingConfig(model="m"), database, provider=make_provider(handler)
        )
        assert await service.embed_one("x") is None
        assert service.failures == 1
        assert service.last_error
        await database.close()

    async def test_timeout_degrades(self, tmp_path) -> None:
        """A slow embedding endpoint must not stall chat (spec v0.5 §61)."""
        database = make_db(tmp_path)
        await database.connect()

        def timeout_handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("too slow")

        service = EmbeddingService(
            MemoryEmbeddingConfig(model="m", timeout=0.05),
            database,
            provider=make_provider(timeout_handler),
        )
        assert await service.embed_one("x") is None
        assert service.failures == 1
        assert "timeout" in service.last_error
        await database.close()

    async def test_unavailable_service_is_noop(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        service = EmbeddingService(MemoryEmbeddingConfig(model="m"), database, provider=None)
        assert service.available is False
        assert await service.embed_one("x") is None
        await database.close()

    async def test_clear_cache(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        provider = make_provider(
            lambda request: httpx.Response(200, json=embedding_body([[0.2, 0.2]]))
        )
        service = EmbeddingService(MemoryEmbeddingConfig(model="m"), database, provider=provider)
        await service.embed_one("abc")
        await service.clear_cache()
        row = await database.fetchone("SELECT COUNT(*) AS n FROM embedding_cache")
        assert row["n"] == 0
        await database.close()


class TestVectorStore:
    def test_cosine_similarity_bounds(self) -> None:
        assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == pytest.approx(1.0)
        assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(0.0)
        assert 0.4 < cosine_similarity([1.0, 0.0], [1.0, 1.0]) < 0.9
        assert cosine_similarity([], [1.0]) == 0.0
        assert cosine_similarity([1.0, 2.0], [1.0]) == 0.0  # dimension mismatch

    async def test_upsert_get_delete(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        store = SqliteVectorStore(database)
        await store.upsert(1, [0.1, 0.2], "m", 1)
        record = await store.get(1)
        assert record is not None and record.vector == [0.1, 0.2]
        await store.upsert(1, [0.3, 0.4], "m", 1)  # update in place
        assert (await store.get(1)).vector == [0.3, 0.4]
        await store.delete(1)
        assert await store.get(1) is None
        await database.close()

    async def test_search_only_over_candidates(self, tmp_path) -> None:
        """Never scan the whole table (spec v0.5 §18/§19)."""
        database = make_db(tmp_path)
        await database.connect()
        store = SqliteVectorStore(database)
        await store.upsert(1, [1.0, 0.0], "m")
        await store.upsert(2, [0.0, 1.0], "m")

        hits = await store.search([1], [1.0, 0.0], limit=5)
        assert [memory_id for memory_id, _ in hits] == [1]  # 2 was not a candidate
        await database.close()

    async def test_search_prefers_closest(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        store = SqliteVectorStore(database)
        await store.upsert(1, [1.0, 0.0], "m")
        await store.upsert(2, [0.9, 0.1], "m")
        hits = await store.search([1, 2], [1.0, 0.0], limit=5)
        assert hits[0][0] == 1 and hits[0][1] >= hits[1][1]
        await database.close()

    async def test_dimension_mismatch_skipped(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        store = SqliteVectorStore(database)
        await store.upsert(1, [1.0, 0.0, 0.0], "old-model")
        hits = await store.search([1], [1.0, 0.0], limit=5)
        assert hits == []  # incompatible vector is never compared
        await database.close()

    async def test_stats_and_missing(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        store = SqliteVectorStore(database)
        assert (await store.stats())["coverage"] == 0.0
        await database.close()
