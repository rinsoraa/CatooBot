"""Embedding provider + cache + vector store tests (spec v0.5 §76)."""

from __future__ import annotations

from types import SimpleNamespace

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


class TestModelAliasResolution:
    """W6 后续：向量检索角色绑定的可能是「模型别名」，必须解析成提供商模型 id。"""

    @staticmethod
    def _model(name: str, model: str, provider: str) -> SimpleNamespace:
        return SimpleNamespace(name=name, model=model, provider=provider)

    def test_alias_resolves_to_the_provider_model_id(self) -> None:
        from app.memory.embedding import resolve_model_alias

        models = [self._model("Embedding", "BAAI/bge-m3", "SiliconFlow")]
        assert resolve_model_alias("Embedding", models) == ("BAAI/bge-m3", "SiliconFlow")
        # 已经是真实 id / 空值 / 无模型表 → 不做替换
        assert resolve_model_alias("BAAI/bge-m3", models) is None
        assert resolve_model_alias("", models) is None
        assert resolve_model_alias("Embedding", None) is None

    def test_from_config_resolves_the_alias_and_inherits_the_provider(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("FAKE_EMB_KEY", "k")
        provider = SimpleNamespace(
            base_url="http://embed.example/v1", api_key_env="FAKE_EMB_KEY", type="openai_compatible"
        )
        service = EmbeddingService.from_config(
            MemoryEmbeddingConfig(provider="SiliconFlow", model="Embedding"),
            None,
            ai_providers={"SiliconFlow": provider},
            ai_models=[self._model("Embedding", "BAAI/bge-m3", "SiliconFlow")],
        )
        assert service.available is True
        built = service._provider  # noqa: SLF001 - the built provider is the subject under test
        assert built is not None
        assert built.model == "BAAI/bge-m3"  # 真实 id，而不是别名
        assert built.name == "SiliconFlow"

    def test_a_raw_model_id_still_passes_through(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        monkeypatch.setenv("FAKE_EMB_KEY", "k")
        provider = SimpleNamespace(base_url="http://embed.example/v1", api_key_env="FAKE_EMB_KEY")
        service = EmbeddingService.from_config(
            MemoryEmbeddingConfig(provider="SiliconFlow", model="Qwen/Qwen3-Embedding-0.6B"),
            None,
            ai_providers={"SiliconFlow": provider},
            ai_models=[self._model("Embedding", "BAAI/bge-m3", "SiliconFlow")],
        )
        assert service.available is True
        assert service._provider is not None  # noqa: SLF001
        assert service._provider.model == "Qwen/Qwen3-Embedding-0.6B"  # noqa: SLF001

    async def test_error_message_explains_unknown_model(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": 20012, "message": "Model does not exist. Please check it carefully."},
            )

        provider = make_provider(handler)
        with pytest.raises(EmbeddingError) as raised:
            await provider.embed(["hi"])
        assert "does not exist" in str(raised.value)
        assert "模型别名" in str(raised.value)
        await provider.close()

    async def test_error_message_explains_rejected_dimensions(self) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": 20015, "message": "The parameter is invalid. Please check again."},
            )

        provider = OpenAICompatibleEmbeddingProvider(
            name="test",
            base_url="https://api.test/v1",
            api_key="sk-test",
            model="BAAI/bge-m3",
            timeout=5.0,
            transport=httpx.MockTransport(handler),
        )
        with pytest.raises(EmbeddingError) as raised:
            await provider.embed(["hi"])
        assert "dimensions" in str(raised.value)
        await provider.close()
