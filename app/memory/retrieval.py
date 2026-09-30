"""Hybrid memory retrieval: keyword + semantic + metadata + temporal.

    HybridScore =
        semantic·w_s + keyword·w_k + importance·w_i
      + confidence·w_c + recency·w_r + relationship·w_rel
      (+ topic bonus)

Weights come from config (spec §21). Semantic similarity alone must never
dominate (spec §22), so it is just one weighted term — and a relevance guard
(spec §98) drops everything below ``min_final_score`` so vaguely-similar but
irrelevant memories never reach the prompt.

Pipeline (spec §27): scope filter → keyword candidates + semantic candidates
→ merge → hybrid ranking → dedup → top K.
"""

from __future__ import annotations

import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.character.relationship import STAGES
from app.config.settings import MemoryRetrievalConfig
from app.memory.model import Memory
from app.memory.vector_store import VectorStore, cosine_similarity

__all__ = [
    "HybridRetriever",
    "MemoryRetriever",
    "RetrievalTrace",
    "ScoredMemory",
    "bigrams",
    "cosine_similarity",
    "keyword_overlap",
    "recency_factor",
]

if TYPE_CHECKING:
    from app.memory.embedding import EmbeddingService

_RECENCY_HALF_LIFE_DAYS = 30.0


def bigrams(text: str) -> set[str]:
    """Character bigrams (ASCII words kept whole). Good enough for zh/en mix."""
    tokens = set[str]()
    current_ascii: list[str] = []
    for char in text.lower():
        if "\u4e00" <= char <= "\u9fff":
            tokens.add(char)
            if current_ascii:
                tokens.add("".join(current_ascii))
                current_ascii = []
        elif char.isalnum():
            current_ascii.append(char)
        else:
            if current_ascii:
                tokens.add("".join(current_ascii))
                current_ascii = []
    if current_ascii:
        tokens.add("".join(current_ascii))
    for index in range(len(text) - 1):
        a, b = text[index], text[index + 1]
        if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
            tokens.add(a + b)
    return tokens


def recency_factor(updated_at: int, now: float) -> float:
    age_days = max(0.0, (now - updated_at) / 86400)
    return 0.5 ** (age_days / _RECENCY_HALF_LIFE_DAYS)


def keyword_overlap(query_tokens: set[str], text: str) -> float:
    if not query_tokens:
        return 0.0
    tokens = bigrams(text)
    if not tokens:
        return 0.0
    return len(query_tokens & tokens) / max(1, len(query_tokens))


@dataclass
class ScoredMemory:
    """A retrieval result carrying every component (feeds the debugger)."""

    memory: Memory
    final: float = 0.0
    semantic: float = 0.0
    keyword: float = 0.0
    importance: float = 0.0
    confidence: float = 0.0
    recency: float = 0.0
    relationship: float = 0.0
    topic_bonus: float = 0.0
    temporal: float = 0.0
    origin: str = ""  # keyword | semantic | both

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.memory.id,
            "content": self.memory.display_text,
            "layer": self.memory.layer,
            "category": self.memory.category,
            "scope_key": self.memory.scope_key,
            "status": self.memory.status,
            "final": round(self.final, 4),
            "semantic": round(self.semantic, 4),
            "keyword": round(self.keyword, 4),
            "importance": round(self.importance, 4),
            "confidence": round(self.confidence, 4),
            "recency": round(self.recency, 4),
            "relationship": round(self.relationship, 4),
            "topic_bonus": round(self.topic_bonus, 4),
            "temporal": round(self.temporal, 4),
            "origin": self.origin,
        }


@dataclass
class RetrievalTrace:
    """Diagnostics for the WebUI debugger (never sent to QQ, spec §56)."""

    query: str
    scope_keys: list[str] = field(default_factory=list)
    candidate_count: int = 0
    keyword_candidates: int = 0
    semantic_candidates: int = 0
    merged_candidates: int = 0
    injected: int = 0
    top_score: float = 0.0
    semantic_available: bool = False
    duration_ms: float = 0.0
    results: list[dict[str, Any]] = field(default_factory=list)
    dropped: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "scope_keys": self.scope_keys,
            "candidates": self.candidate_count,
            "keyword_candidates": self.keyword_candidates,
            "semantic_candidates": self.semantic_candidates,
            "merged_candidates": self.merged_candidates,
            "injected": self.injected,
            "dropped_by_guard": self.dropped,
            "top_score": self.top_score,
            "semantic_available": self.semantic_available,
            "duration_ms": self.duration_ms,
            "results": self.results,
        }


class HybridRetriever:
    def __init__(
        self,
        config: MemoryRetrievalConfig,
        vector_store: VectorStore | None = None,
        embeddings: EmbeddingService | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._vectors = vector_store
        self._embeddings = embeddings
        self._log = logger or logging.getLogger("CatooBot.Memory.Retrieval")
        self._clock = clock
        self._cache: dict[str, tuple[float, list[ScoredMemory], RetrievalTrace]] = {}

    # ------------------------------------------------------------------ api

    @property
    def semantic_available(self) -> bool:
        return bool(
            self._vectors is not None
            and self._embeddings is not None
            and self._embeddings.available
        )

    async def retrieve(
        self,
        query: str,
        candidates: list[Memory],
        *,
        top_k: int | None = None,
        relationship_stage: str = "",
        topic_titles: list[str] | None = None,
        use_cache: bool = True,
    ) -> list[ScoredMemory]:
        scored, _ = await self.retrieve_with_trace(
            query,
            candidates,
            top_k=top_k,
            relationship_stage=relationship_stage,
            topic_titles=topic_titles,
            use_cache=use_cache,
        )
        return scored

    async def retrieve_with_trace(
        self,
        query: str,
        candidates: list[Memory],
        *,
        top_k: int | None = None,
        relationship_stage: str = "",
        topic_titles: list[str] | None = None,
        use_cache: bool = True,
    ) -> tuple[list[ScoredMemory], RetrievalTrace]:
        started = self._clock()
        limit = top_k or self._config.top_k
        scope_keys = sorted({m.scope_key for m in candidates})
        trace = RetrievalTrace(query=query, scope_keys=scope_keys)
        trace.candidate_count = len(candidates)

        cache_key = self._cache_key(query, scope_keys, relationship_stage, topic_titles)
        if use_cache and self._config.cache_ttl_seconds > 0:
            cached = self._cache.get(cache_key)
            if cached and self._clock() - cached[0] <= self._config.cache_ttl_seconds:
                return cached[1], cached[2]

        if not query.strip() or not candidates:
            trace.duration_ms = (self._clock() - started) * 1000
            return [], trace

        now = float(self._clock())
        query_tokens = bigrams(query)
        by_id = {m.id: m for m in candidates if m.id}

        # 1) keyword candidates (lexical overlap)
        keyword_scores: dict[int, float] = {}
        for memory in candidates:
            if not memory.id:
                continue
            overlap = keyword_overlap(query_tokens, f"{memory.content} {memory.summary}")
            if overlap > 0:
                keyword_scores[memory.id] = overlap
        keyword_ranked = sorted(keyword_scores.items(), key=lambda p: p[1], reverse=True)
        trace.keyword_candidates = len(keyword_ranked)
        keyword_top = dict(keyword_ranked[: self._config.keyword_candidates])

        # 2) semantic candidates (vector similarity over the same candidate set)
        semantic_scores: dict[int, float] = {}
        if self.semantic_available:
            vector = await self._embeddings.embed_one(query)  # type: ignore[union-attr]
            if vector:
                trace.semantic_available = True
                hits = await self._vectors.search(  # type: ignore[union-attr]
                    list(by_id), vector, limit=self._config.semantic_candidates
                )
                semantic_scores = {memory_id: score for memory_id, score in hits}
        trace.semantic_candidates = len(semantic_scores)

        merged_ids = list(dict.fromkeys([*keyword_top, *semantic_scores]))
        trace.merged_candidates = len(merged_ids)
        if not merged_ids:
            trace.duration_ms = (self._clock() - started) * 1000
            return [], trace

        # 3) hybrid ranking
        weights = self._config.weights
        topic_tokens = [bigrams(title) for title in (topic_titles or [])]
        relationship_score = self._relationship_score(relationship_stage)
        now_int = int(now)

        results: list[ScoredMemory] = []
        for memory_id in merged_ids:
            candidate: Memory | None = by_id.get(memory_id)
            if candidate is None:
                continue
            memory = candidate
            semantic = semantic_scores.get(memory_id, 0.0)
            keyword = keyword_top.get(memory_id, 0.0)
            importance = memory.importance
            confidence = memory.confidence
            recency = recency_factor(memory.updated_at or memory.created_at, now)
            temporal = self._temporal_score(memory, now_int)
            bonus = self._topic_bonus(memory, topic_tokens, query_tokens)

            final = (
                semantic * weights.semantic
                + keyword * weights.keyword
                + importance * weights.importance
                + confidence * weights.confidence
                + recency * weights.recency
                + relationship_score * weights.relationship
            )
            final = min(1.0, final + bonus)
            # Temporal validity nudges the score; a stale or expired memory fades.
            final *= temporal
            # Usage reinforcement makes repeatedly useful memories easier to recall.
            final *= 1.0 + min(0.1, memory.use_count * 0.01)

            origin = "both" if semantic and keyword else ("semantic" if semantic else "keyword")
            results.append(
                ScoredMemory(
                    memory=memory,
                    final=final,
                    semantic=semantic,
                    keyword=keyword,
                    importance=importance,
                    confidence=confidence,
                    recency=recency,
                    relationship=relationship_score,
                    topic_bonus=bonus,
                    temporal=temporal,
                    origin=origin,
                )
            )

        results.sort(key=lambda item: item.final, reverse=True)

        # 4) relevance guard: semantic-similar but irrelevant never injects.
        #    Metadata alone is not evidence of relevance (§22/§98).
        floor = self._config.min_relevance
        kept = [
            item
            for item in results
            if max(item.semantic, item.keyword) >= floor
            and item.final >= self._config.min_final_score
        ]
        trace.dropped = len(results) - len(kept)
        selected = kept[:limit]

        trace.injected = len(selected)
        trace.top_score = round(selected[0].final, 4) if selected else 0.0
        trace.results = [item.to_dict() for item in kept[: max(limit, 10)]]
        trace.duration_ms = round((self._clock() - started) * 1000, 2)

        self._log.info(
            "[Memory.Retrieval] scope=%s keyword=%d semantic=%d merged=%d"
            " injected=%d top=%.3f (%.1fms)",
            scope_keys[0] if scope_keys else "-",
            trace.keyword_candidates,
            trace.semantic_candidates,
            trace.merged_candidates,
            trace.injected,
            trace.top_score,
            trace.duration_ms,
        )

        if use_cache and self._config.cache_ttl_seconds > 0:
            self._cache[cache_key] = (self._clock(), selected, trace)
            if len(self._cache) > 256:  # keep the cache bounded
                oldest = min(self._cache, key=lambda key: self._cache[key][0])
                self._cache.pop(oldest, None)
        return selected, trace

    # ------------------------------------------------------------- helpers

    def _relationship_score(self, stage: str) -> float:
        """Closer relationship slightly favours recalling long-term facts (spec §26)."""
        if stage in STAGES:
            return STAGES.index(stage) / max(1, len(STAGES) - 1)
        return 0.0

    def _temporal_score(self, memory: Memory, now: int) -> float:
        """Temporal relevance: fresh and currently-valid memories win (spec §23/§24)."""
        score = 1.0
        if memory.valid_until is not None and memory.valid_until < now:
            score *= 0.4  # expired but not deleted
        elif memory.valid_until is not None:
            score *= 1.05
        if memory.event_at:
            age_days = max(0.0, (now - memory.event_at) / 86400)
            # Episodes fade faster than semantic facts, but never to zero.
            score *= max(0.35, 0.5 ** (age_days / 45.0))
        return score

    def _topic_bonus(
        self, memory: Memory, topic_tokens: list[set[str]], query_tokens: set[str]
    ) -> float:
        """Active topics boost related memories (spec §25)."""
        if not topic_tokens:
            return 0.0
        text = f"{memory.content} {memory.summary}"
        best = 0.0
        for tokens in topic_tokens:
            if not tokens:
                continue
            best = max(best, keyword_overlap(tokens, text))
        if best <= 0:
            return 0.0
        # Only boost memories that are also about the current query at least a bit.
        if query_tokens and keyword_overlap(query_tokens, text) <= 0:
            return self._config.topic_bonus * best * 0.5
        return self._config.topic_bonus * best

    def _cache_key(
        self,
        query: str,
        scope_keys: list[str],
        relationship_stage: str,
        topic_titles: list[str] | None,
    ) -> str:
        blob = "|".join(
            [query, ",".join(scope_keys), relationship_stage, ",".join(topic_titles or [])]
        )
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def invalidate_cache(self) -> None:
        """Called when memories change so stale results are never reused (spec §64)."""
        self._cache.clear()

    def snapshot(self) -> dict[str, Any]:
        return {
            "semantic_available": self.semantic_available,
            "top_k": self._config.top_k,
            "min_final_score": self._config.min_final_score,
            "min_relevance": self._config.min_relevance,
            "weights": self._config.weights.model_dump(),
            "cache_entries": len(self._cache),
            "cache_ttl_seconds": self._config.cache_ttl_seconds,
        }


# v0.4 compatibility: the old lexical-only retriever stays available for tests
# and for callers that explicitly want keyword ranking.
class MemoryRetriever(HybridRetriever):
    """Backwards-compatible name: retrieve lexically, ignoring vectors."""

    def __init__(self, logger: logging.Logger | None = None, clock: Any = time.time) -> None:
        from app.config.settings import MemoryRetrievalConfig, MemoryWeightsConfig

        super().__init__(
            MemoryRetrievalConfig(
                weights=MemoryWeightsConfig(
                    semantic=0.0,
                    keyword=1.0,
                    importance=0.0,
                    confidence=0.0,
                    recency=0.0,
                    relationship=0.0,
                ),
                min_final_score=0.05,
            ),
            vector_store=None,
            embeddings=None,
            logger=logger,
            clock=clock,
        )

    def retrieve_simple(self, query: str, candidates: list[Memory], top_k: int = 8) -> list[Memory]:
        """Synchronous keyword ranking used by the v0.4 tests."""
        query_tokens = bigrams(query)
        now = float(self._clock())
        scored: list[tuple[float, Memory]] = []
        for memory in candidates:
            overlap = keyword_overlap(query_tokens, memory.content)
            factor = recency_factor(memory.updated_at, now)
            score = overlap * (0.5 + memory.importance) * factor
            if score >= 0.05:
                scored.append((score, memory))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [memory for _, memory in scored[:top_k]]
