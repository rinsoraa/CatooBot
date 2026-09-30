"""MemoryManager: the write path (dedup / merge / conflict) + retrieval facade.

v0.5 upgrades:

* every memory carries a ``layer`` (semantic vs episodic) and a ``status``;
  conflicts mark the old row ``superseded`` instead of deleting it (§11);
* retrieval goes through the hybrid retriever (semantic + keyword + metadata);
* quota enforcement archives the least valuable rows instead of growing
  without bound (§72/§73);
* a relevance guard prevents vaguely-similar memories from reaching the prompt.
"""

from __future__ import annotations

import logging
import re
import time
from typing import TYPE_CHECKING, Any

from app.memory.embedding import EmbeddingService
from app.memory.model import SOURCES, Memory
from app.memory.model import scope_key as make_scope_key
from app.memory.repository import MemoryRepository
from app.memory.retrieval import HybridRetriever, ScoredMemory, bigrams
from app.memory.vector_store import SqliteVectorStore, VectorStore

if TYPE_CHECKING:
    from app.config.settings import MemoryConfig
    from app.database.database import Database

_FORBIDDEN_PATTERNS = re.compile(
    r"(很蠢|很笨|智力低|智商低|性格缺陷|心理有问题|心理疾病|人格缺陷|人格评分|好感度\s*\d+|恋爱值\s*\d+)"
)

# Attempts to rewrite the system prompt are stored as a plain user wish at
# most, never as an instruction the character must obey (spec §50).
_INJECTION_PATTERNS = re.compile(
    r"(系统提示词|system\s*prompt|ignore previous|忽略之前|你现在是|你的设定改成)"
)


class MemoryManager:
    def __init__(
        self,
        config: MemoryConfig,
        database: Database,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
        embeddings: EmbeddingService | None = None,
        vector_store: VectorStore | None = None,
    ) -> None:
        self._config = config
        self._log = logger or logging.getLogger("CatooBot.Memory")
        self._clock = clock
        self.repository = MemoryRepository(database, logger=self._log, clock=clock)
        self.embeddings = embeddings
        self.vectors = vector_store or SqliteVectorStore(
            database, logger=self._log, clock=clock
        )
        self.retriever = HybridRetriever(
            config.retrieval,
            vector_store=self.vectors if embeddings is not None else None,
            embeddings=embeddings,
            logger=self._log,
            clock=clock,
        )

    # ---------------------------------------------------------------- write

    async def remember(
        self,
        scope: str,
        ref: str,
        content: str,
        *,
        category: str = "fact",
        importance: float = 0.5,
        confidence: float = 0.7,
        user_id: str | None = None,
        group_id: str | None = None,
        layer: str | None = None,
        summary: str = "",
        source: str = "conversation",
        temporal_scope: str = "long_term",
        event_at: int | None = None,
    ) -> Memory | None:
        """Store one memory with dedup/merge/conflict logic. None = skipped."""
        content = content.strip()
        if len(content) < self._config.extraction.min_content_length:
            return None
        if _FORBIDDEN_PATTERNS.search(content):
            self._log.warning("[Memory.Semantic] Rejected sensitive judgement memory")
            return None

        layer = layer or self._infer_layer(category)
        if _INJECTION_PATTERNS.search(content):
            # Keep it, but only as a user wish — never as a rule (spec §50).
            category = "instruction"
            confidence = min(confidence, 0.5)
            importance = min(importance, 0.4)

        if layer == "episodic" and event_at is None:
            event_at = int(self._clock())  # an episode happened "now" by default
        valid_from, valid_until = self._temporal_bounds(temporal_scope, event_at)

        key = make_scope_key(scope, ref)
        existing = await self.repository.find_same(key, content)
        if existing is not None:
            merged = existing.model_copy(
                update={
                    "confidence": min(0.99, existing.confidence + self._reinforce_step(source)),
                    "importance": max(existing.importance, importance),
                    "status": "active",
                }
            )
            await self.repository.update(merged)
            self.retriever.invalidate_cache()
            self._log.debug("[Memory.Semantic] reinforced #%d", existing.id)
            return merged

        stored: Memory | None = None
        similar = await self._find_similar(key, content, category)
        if similar is not None:
            stored = await self._resolve_similar(
                similar,
                content=content,
                category=category,
                importance=importance,
                confidence=confidence,
                layer=layer,
                summary=summary,
                source=source,
            )

        if stored is None:
            stored = await self.repository.add(
                Memory(
                    scope_key=key,
                    user_id=user_id,
                    group_id=group_id,
                    category=category,
                    content=content,
                    summary=summary.strip(),
                    importance=importance,
                    confidence=confidence,
                    layer=layer,
                    source=source,
                    valid_from=valid_from,
                    valid_until=valid_until,
                    event_at=event_at,
                )
            )
            self._log.info(
                "[Memory.Semantic] stored #%d (%s/%s): %.50s",
                stored.id,
                key,
                layer,
                content,
            )

        await self._ensure_embedding(stored)
        await self._enforce_quota(key)
        self.retriever.invalidate_cache()
        return stored

    async def _resolve_similar(
        self,
        similar: Memory,
        *,
        content: str,
        category: str,
        importance: float,
        confidence: float,
        layer: str,
        summary: str,
        source: str,
    ) -> Memory | None:
        """Same-topic memory: supersede it when the new statement updates it."""
        if not self._looks_like_update(similar.content, content) and (
            similar.confidence > confidence
        ):
            # Compatible statements ("喜欢 A" + "也喜欢 B") coexist for now.
            return None

        await self.repository.set_status(similar.id, "superseded")
        new_memory = await self.repository.add(
            Memory(
                scope_key=similar.scope_key,
                user_id=similar.user_id,
                group_id=similar.group_id,
                category=category if category != "fact" else similar.category,
                content=content,
                summary=summary.strip(),
                importance=max(similar.importance, importance),
                confidence=min(0.99, confidence + 0.05),
                layer=layer,
                source=source,
                supersedes_id=similar.id,
            )
        )
        await self.repository.add_relation(new_memory.id, similar.id, "supersedes")
        self._log.info("[Memory.Semantic] superseded #%d -> #%d", similar.id, new_memory.id)
        return new_memory

    async def _find_similar(self, key: str, content: str, category: str) -> Memory | None:
        candidates = await self.repository.candidates([key], limit=100)
        content_tokens = bigrams(content)
        if not content_tokens:
            return None
        best: tuple[float, Memory] | None = None
        for candidate in candidates:
            if candidate.content == content or candidate.category != category:
                continue
            overlap = len(content_tokens & bigrams(candidate.content)) / max(
                1, min(len(content_tokens), len(bigrams(candidate.content)))
            )
            if overlap >= 0.6 and (best is None or overlap > best[0]):
                best = (overlap, candidate)
        return best[1] if best else None

    @staticmethod
    def _looks_like_update(old: str, new: str) -> bool:
        """'现在/改成/不再/更喜欢' style statements replace the old fact."""
        markers = ("现在", "改成", "不再", "更喜欢", "其实是", "已经", "换了")
        return any(marker in new for marker in markers) and old != new

    @staticmethod
    def _infer_layer(category: str) -> str:
        return "episodic" if category == "event" else "semantic"

    @staticmethod
    def _reinforce_step(source: str) -> float:
        """Confidence only moves on real evidence (spec §43)."""
        return 0.08 if source == "explicit" else 0.05

    def _temporal_bounds(
        self, temporal_scope: str, event_at: int | None
    ) -> tuple[int | None, int | None]:
        now = int(self._clock())
        if temporal_scope == "event":
            return event_at or now, None
        if temporal_scope == "short_term":
            return now, now + 14 * 86400
        return None, None

    # ------------------------------------------------------------ embedding

    async def _ensure_embedding(self, memory: Memory) -> None:
        if self.embeddings is None or not self.embeddings.available or not memory.id:
            return
        vector = await self.embeddings.embed_one(memory.display_text)
        if not vector:
            return
        model = str(self.embeddings.snapshot().get("model") or "unknown")
        await self.vectors.upsert(memory.id, vector, model)

    async def backfill_embeddings(self, limit: int = 50) -> int:
        """Embed memories that have no vector yet (rebuild path, spec §57)."""
        if self.embeddings is None or not self.embeddings.available:
            return 0
        store = self.vectors
        missing = (
            await store.missing_memory_ids(limit=limit)
            if hasattr(store, "missing_memory_ids")
            else []
        )
        done = 0
        for memory_id in missing:
            memory = await self.repository.get(memory_id)
            if memory is None:
                continue
            await self._ensure_embedding(memory)
            done += 1
        if done:
            self._log.info("[Memory.Embedding] backfilled %d embedding(s)", done)
        return done

    # -------------------------------------------------------------- quotas

    async def _enforce_quota(self, scope_key: str) -> None:
        policy = self._config.policy
        if not policy.enabled:
            return
        limit = (
            policy.max_active_per_group
            if scope_key.startswith("group:")
            else policy.max_active_per_user
        )
        if limit <= 0:
            return
        active = await self.repository.count(scope_key, status="active")
        if active <= limit:
            return
        overflow = active - limit
        rows = await self.repository.candidates([scope_key], limit=overflow + 20)
        # Archive the least valuable: low importance, low usage, older first.
        rows.sort(key=lambda m: (m.importance, m.use_count, m.updated_at))
        for memory in rows[:overflow]:
            await self.repository.set_status(memory.id, "archived")
        self._log.info(
            "[Memory.Semantic] quota: archived %d memory(ies) in %s (limit=%d)",
            min(overflow, len(rows)),
            scope_key,
            limit,
        )

    # ----------------------------------------------------------------- read

    async def retrieve_for_session(
        self,
        session_id: str,
        query: str,
        extra_scopes: list[str] | None = None,
        *,
        relationship_stage: str = "",
        topic_titles: list[str] | None = None,
    ) -> list[Memory]:
        """Relevant memories for one chat turn (hybrid ranking + guard)."""
        scope_keys = [session_id.replace("private:", "user:")]
        if extra_scopes:
            scope_keys.extend(extra_scopes)
        candidates = await self.repository.candidates(
            scope_keys, limit=max(200, self._config.retrieval.keyword_candidates * 10)
        )
        scored = await self.retriever.retrieve(
            query,
            candidates,
            relationship_stage=relationship_stage,
            topic_titles=topic_titles,
        )
        if scored:
            await self.repository.mark_used([item.memory.id for item in scored])
        return [item.memory for item in scored]

    async def retrieve_scored(
        self,
        query: str,
        *,
        scope_keys: list[str],
        relationship_stage: str = "",
        topic_titles: list[str] | None = None,
    ) -> list[ScoredMemory]:
        candidates = await self.repository.candidates(scope_keys)
        return await self.retriever.retrieve(
            query,
            candidates,
            relationship_stage=relationship_stage,
            topic_titles=topic_titles,
            use_cache=False,
        )

    async def retrieve_with_trace(
        self,
        query: str,
        *,
        scope_keys: list[str],
        relationship_stage: str = "",
        topic_titles: list[str] | None = None,
    ) -> tuple[list[ScoredMemory], Any]:
        """Debugger entry point: full component scores (spec §88)."""
        candidates = await self.repository.candidates(scope_keys)
        return await self.retriever.retrieve_with_trace(
            query,
            candidates,
            relationship_stage=relationship_stage,
            topic_titles=topic_titles,
        )

    # ------------------------------------------------------------ admin api

    async def list_memories(self, **filters: Any) -> list[Memory]:
        return await self.repository.search(**filters)

    async def timeline(self, scope_key: str = "", limit: int = 200) -> list[Memory]:
        return await self.repository.timeline(scope_key, limit)

    async def edit_memory(
        self,
        memory_id: int,
        content: str,
        category: str,
        importance: float,
        *,
        status: str | None = None,
        summary: str | None = None,
    ) -> bool:
        memory = await self.repository.get(memory_id)
        if memory is None:
            return False
        changes: dict[str, Any] = {
            "content": content,
            "category": category,
            "importance": importance,
        }
        if status:
            changes["status"] = status
        if summary is not None:
            changes["summary"] = summary
        updated = memory.model_copy(update=changes)
        await self.repository.update(updated)
        await self._ensure_embedding(updated)
        self.retriever.invalidate_cache()
        return True

    async def set_status(self, memory_id: int, status: str) -> bool:
        ok = await self.repository.set_status(memory_id, status)
        if ok:
            self.retriever.invalidate_cache()
        return ok

    async def force_supersede(
        self,
        memory_id: int,
        *,
        content: str,
        category: str = "",
        importance: float | None = None,
        confidence: float = 0.95,
        summary: str = "",
        source: str = "explicit",
    ) -> Memory | None:
        """Authoritative correction: the old fact is retired, the new one is truth.

        Used by the WebUI memory-correction tool. The replacement is written as
        a *plain fact about the user* — deliberately without any "corrected"
        marker — so retrieval (and therefore the character) treats it as
        something that was always true. The superseded row stays in the
        timeline for auditing and is never retrieved again.
        """
        previous = await self.repository.get(memory_id)
        if previous is None:
            return None
        clean = " ".join(str(content).split())
        if not clean:
            raise ValueError("修正后的记忆内容不能为空")
        replacement = await self.repository.add(
            Memory(
                scope_key=previous.scope_key,
                user_id=previous.user_id,
                group_id=previous.group_id,
                category=category or previous.category,
                content=clean,
                summary=" ".join(str(summary).split()),
                importance=(
                    previous.importance
                    if importance is None
                    else max(0.0, min(1.0, importance))
                ),
                confidence=max(0.0, min(0.99, confidence)),
                layer=previous.layer,
                source=source if source in SOURCES else "explicit",
                supersedes_id=previous.id,
            )
        )
        await self.repository.set_status(previous.id, "superseded")
        await self.repository.add_relation(replacement.id, previous.id, "supersedes")
        await self._ensure_embedding(replacement)
        self.retriever.invalidate_cache()
        self._log.info(
            "[Memory] corrected #%d -> #%d (%s)", previous.id, replacement.id, clean[:40]
        )
        return replacement

    async def forget(self, memory_id: int, *, reason: str = "") -> bool:
        """Retire a memory from retrieval without deleting its history."""
        memory = await self.repository.get(memory_id)
        if memory is None:
            return False
        ok = await self.repository.set_status(memory_id, "archived")
        if ok:
            self.retriever.invalidate_cache()
            self._log.info("[Memory] archived #%d (%s)", memory_id, reason or "manual")
        return ok

    async def delete_memory(self, memory_id: int) -> bool:
        ok = await self.repository.delete(memory_id)
        if ok:
            await self.vectors.delete(memory_id)
            self.retriever.invalidate_cache()
        return ok

    async def count(self, scope_key: str = "") -> int:
        return await self.repository.count(scope_key)
