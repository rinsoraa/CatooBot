"""Embedding abstraction + OpenAI-compatible implementation + cached service.

Deliberately independent from the chat provider (spec v0.5 §14): a deployment may
chat with model A and embed with cheap model B, or even a different vendor.
Failures never propagate — the service degrades to "semantic unavailable"
and retrieval falls back to keyword + metadata ranking (spec v0.5 §60/§61).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import httpx

from app.config.settings import MemoryEmbeddingConfig
from app.memory.repository import content_hash

if TYPE_CHECKING:
    from app.database.database import Database


class EmbeddingError(Exception):
    """Any embedding failure (transport, HTTP, malformed payload)."""


class EmbeddingProvider(ABC):
    """Every embedding backend implements this."""

    name: str = ""
    model: str = ""
    dimensions: int | None = None

    @abstractmethod
    async def embed(self, texts: list[str]) -> list[list[float]]:
        """Vectorize a batch of texts. Raises :class:`EmbeddingError`."""

    async def close(self) -> None:
        return None


class OpenAICompatibleEmbeddingProvider(EmbeddingProvider):
    """``POST {base_url}/embeddings`` with ``Authorization: Bearer <key>``."""

    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        model: str,
        *,
        timeout: float = 10.0,
        dimensions: int | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.name = name
        self.model = model
        self.dimensions = dimensions
        self._log = logger or logging.getLogger("CatooBot.Memory.Embedding")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=min(5.0, timeout)),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            transport=transport,
        )

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        payload: dict[str, Any] = {"model": self.model, "input": texts}
        if self.dimensions:
            payload["dimensions"] = self.dimensions
        try:
            response = await self._client.post("/embeddings", json=payload)
        except httpx.TimeoutException as exc:
            raise EmbeddingError(f"embedding timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise EmbeddingError(f"embedding transport error: {exc.__class__.__name__}") from exc

        if response.status_code != 200:
            detail = response.text[:160]
            hint = ""
            lowered = detail.lower()
            if "does not exist" in lowered or "model not found" in lowered:
                hint = (
                    "（该名称在提供商侧不存在：若你填的是模型别名，请确认它确实在 ai.models 里，"
                    "或直接填提供商模型 id，例如 BAAI/bge-m3）"
                )
            elif "dimensions" in lowered or (
                response.status_code == 400 and "parameter is invalid" in lowered
            ):
                hint = (
                    "（该模型可能不接受 dimensions 参数：SiliconFlow 的 BAAI/bge-m3 固定 1024 维，"
                    "请把 memory.semantic.embedding.dimensions 留空）"
                )
            raise EmbeddingError(f"embedding HTTP {response.status_code}: {detail}{hint}")

        try:
            data = response.json()
            items = sorted(data["data"], key=lambda item: item.get("index", 0))
            vectors = [[float(x) for x in item["embedding"]] for item in items]
        except (KeyError, TypeError, ValueError) as exc:
            raise EmbeddingError(f"embedding response malformed: {exc}") from exc

        if len(vectors) != len(texts):
            raise EmbeddingError(
                f"embedding count mismatch: asked {len(texts)}, got {len(vectors)}"
            )
        if self.dimensions and any(len(v) != self.dimensions for v in vectors):
            raise EmbeddingError("embedding dimension mismatch")
        return vectors

    async def close(self) -> None:
        await self._client.aclose()


def resolve_model_alias(name: str, ai_models: Sequence[Any] | None) -> tuple[str, str] | None:
    """把 ``ai.models`` 里的 **别名** 解析为 ``(真实 model id, provider 名)``。

    不是别名（或没有模型表）时返回 ``None``，调用方按原样把该值当模型 id 使用。
    """
    if not name:
        return None
    for item in ai_models or ():
        if str(getattr(item, "name", "")) == name:
            model_id = str(getattr(item, "model", "") or name)
            provider = str(getattr(item, "provider", "") or "")
            return model_id, provider
    return None


class EmbeddingService:
    """Cached embedding access with timeout + circuit-style degradation.

    The cache is keyed by (content_hash, model, dimensions) so switching the
    embedding model never reuses stale vectors (spec v0.5 §15/§58/§59).
    """

    def __init__(
        self,
        config: MemoryEmbeddingConfig,
        database: Database | None = None,
        provider: EmbeddingProvider | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._db = database
        self._provider = provider
        self._log = logger or logging.getLogger("CatooBot.Memory.Embedding")
        self._clock = clock
        self._memory_cache: dict[str, list[float]] = {}
        self.available = provider is not None
        self.last_error = ""
        self.failures = 0
        self.hits = 0
        self.misses = 0
        self.dimension: int | None = provider.dimensions if provider else None

    # ----------------------------------------------------------- factories

    @classmethod
    def from_config(
        cls,
        config: MemoryEmbeddingConfig,
        database: Database | None = None,
        ai_providers: dict[str, Any] | None = None,
        ai_models: Sequence[Any] | None = None,
        logger: logging.Logger | None = None,
        provider: EmbeddingProvider | None = None,
    ) -> EmbeddingService:
        """Build the service, resolving credentials from the environment.

        ``config.provider`` may point at an ``ai.providers`` entry to reuse its
        base_url + credential; otherwise ``base_url``/``api_key_env`` are used.
        """
        log = logger or logging.getLogger("CatooBot.Memory.Embedding")
        if provider is not None:
            return cls(config, database, provider, logger=log)

        # 「模型用途 → 向量检索」绑定的值可能是一个模型别名：其他角色都经 AI Router
        # 按别名寻址，而嵌入不走 Router，必须在这里把别名解析成提供商侧的模型 id，
        # 并继承该别名所属 provider 的 base_url / 凭据（与 UI 语义保持一致）。
        model_id = config.model
        provider_name = config.provider
        resolved = resolve_model_alias(config.model, ai_models)
        if resolved is not None:
            model_id, alias_provider = resolved
            provider_name = alias_provider or provider_name
            log.info(
                "[Memory.Embedding] model alias %r resolved to %r (provider=%s)",
                config.model,
                model_id,
                provider_name or "embedding",
            )
        elif config.model and ai_models:
            log.info("[Memory.Embedding] using %r as a provider model id", config.model)

        base_url = config.base_url
        api_key_env = config.api_key_env
        if provider_name:
            entry = (ai_providers or {}).get(provider_name)
            if entry is not None:
                base_url = base_url or getattr(entry, "base_url", "")
                api_key_env = api_key_env or getattr(entry, "api_key_env", "")
        if not (config.model and base_url):
            log.info(
                "[Memory.Embedding] Not configured (model/base_url missing) — "
                "semantic search disabled"
            )
            return cls(config, database, None, logger=log)

        api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        if not api_key:
            log.warning(
                "[Memory.Embedding] Environment variable '%s' is empty — semantic search disabled",
                api_key_env or "<none>",
            )
            return cls(config, database, None, logger=log)

        built = OpenAICompatibleEmbeddingProvider(
            name=provider_name or "embedding",
            base_url=base_url,
            api_key=api_key,
            # 解析后的提供商模型 id（别名已在上面换掉）
            model=model_id,
            timeout=config.timeout,
            dimensions=config.dimensions,
            logger=log,
        )
        log.info(
            "[Memory.Embedding] provider=%s model=%s dimensions=%s",
            built.name,
            built.model,
            config.dimensions or "auto",
        )
        return cls(config, database, built, logger=log)

    # ------------------------------------------------------------- queries

    @property
    def key(self) -> str:
        """Cache namespace for the current model + dimensions."""
        if self._provider is None:
            return ""
        return f"{self._provider.model}:{self.dimension or 0}"

    async def embed_texts(self, texts: list[str]) -> list[list[float]] | None:
        """Embed a batch; None means 'semantic unavailable' (never raises)."""
        if self._provider is None or not texts:
            return None
        vectors: list[list[float] | None] = []
        pending: list[str] = []
        pending_index: list[int] = []
        for text in texts:
            cached = await self._cache_get(text)
            if cached is not None:
                vectors.append(cached)
            else:
                vectors.append(None)
                pending.append(text)
                pending_index.append(len(vectors) - 1)

        if pending:
            try:
                fresh = await asyncio.wait_for(
                    self._provider.embed(pending), timeout=self._config.timeout
                )
            except (TimeoutError, EmbeddingError) as exc:
                self._mark_failure(str(exc))
                return None if all(v is None for v in vectors) else self._compact(vectors)
            except Exception as exc:  # noqa: BLE001 - never break chat
                self._mark_failure(f"unexpected: {exc}")
                return None if all(v is None for v in vectors) else self._compact(vectors)

            self.available = True
            self.last_error = ""
            if fresh and self.dimension is None:
                self.dimension = len(fresh[0])
            for slot, text, vector in zip(pending_index, pending, fresh, strict=True):
                vectors[slot] = vector
                await self._cache_put(text, vector)
        return self._compact(vectors)

    async def embed_one(self, text: str) -> list[float] | None:
        result = await self.embed_texts([text])
        if not result:
            return None
        return result[0]

    def _compact(self, vectors: list[list[float] | None]) -> list[list[float]] | None:
        if any(v is None for v in vectors):
            missing = sum(1 for v in vectors if v is None)
            self._log.warning(
                "[Memory.Embedding] %d/%d texts could not be embedded",
                missing,
                len(vectors),
            )
            return None
        return [v for v in vectors if v is not None]

    def _mark_failure(self, detail: str) -> None:
        self.failures += 1
        self.last_error = detail[:200]
        self._log.warning("[Memory.Embedding] Failed (%d): %s", self.failures, detail[:160])

    # --------------------------------------------------------------- cache

    async def _cache_get(self, text: str) -> list[float] | None:
        key = f"{self.key}|{content_hash(text)}"
        if key in self._memory_cache:
            self.hits += 1
            return self._memory_cache[key]
        if self._db is None or not self.key:
            return None
        model = self._provider.model if self._provider else ""
        row = await self._db.fetchone(
            "SELECT vector, dimensions FROM embedding_cache WHERE content_hash = ? AND model = ?",
            (content_hash(text), model),
        )
        if row is None:
            self.misses += 1
            return None
        if self.dimension is not None and int(row["dimensions"]) != self.dimension:
            # Same model name, different vector size: do not mix them (spec v0.5 §59).
            self.misses += 1
            return None
        vector = json.loads(row["vector"])
        if self.dimension is None:
            self.dimension = len(vector)
        self._memory_cache[key] = vector
        self.hits += 1
        return vector

    async def _cache_put(self, text: str, vector: list[float]) -> None:
        if not text:
            return
        key = f"{self.key}|{content_hash(text)}"
        self._memory_cache[key] = vector
        if self._db is None or self._provider is None:
            return
        await self._db.execute(
            """INSERT INTO embedding_cache (content_hash, model, dimensions, vector, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(content_hash, model, dimensions) DO UPDATE SET
                   vector=excluded.vector, created_at=excluded.created_at""",
            (
                content_hash(text),
                self._provider.model,
                self.dimension or len(vector),
                json.dumps(vector),
                int(self._clock()),
            ),
        )

    async def clear_cache(self) -> None:
        self._memory_cache.clear()
        if self._db is not None:
            await self._db.execute("DELETE FROM embedding_cache")
        self._log.info("[Memory.Embedding] cache cleared")

    async def close(self) -> None:
        if self._provider is not None:
            await self._provider.close()

    def snapshot(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "provider": self._provider.name if self._provider else "",
            "model": self._provider.model if self._provider else "",
            "dimensions": self.dimension,
            "hits": self.hits,
            "misses": self.misses,
            "failures": self.failures,
            "last_error": self.last_error,
            "timeout": self._config.timeout,
        }
