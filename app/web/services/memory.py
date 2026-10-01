"""Memory admin service: semantic search, timeline, health, debugger, upkeep.

Everything here is WebUI-only (spec §56): the retrieval debugger explains *why*
a memory was recalled, but nothing in this module is reachable from QQ.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot

SETTINGS_KEY = "memory_overrides"


class MemoryAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Memory")

    # ------------------------------------------------------------ browsing

    async def list_memories(
        self,
        *,
        keyword: str = "",
        scope_key: str = "",
        category: str = "",
        layer: str = "",
        status: str = "active",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if self.bot.memory is None:
            return []
        memories = await self.bot.memory.list_memories(
            keyword=keyword,
            scope_key=scope_key,
            category=category,
            layer=layer,
            status=status,
            limit=limit,
        )
        return [m.model_dump() for m in memories]

    async def search(
        self, query: str, *, mode: str = "hybrid", scope_key: str = "", limit: int = 10
    ) -> dict[str, Any]:
        """Search modes: keyword | semantic | hybrid (spec §87)."""
        if self.bot.memory is None:
            return {"mode": mode, "results": [], "error": "memory disabled"}
        scopes = [scope_key] if scope_key else await self.bot.memory.repository.all_scopes()
        memory = self.bot.memory

        if mode == "keyword":
            rows = await memory.repository.search(keyword=query, limit=limit, status="active")
            results = [
                {
                    "id": m.id,
                    "content": m.display_text,
                    "final": None,
                    "layer": m.layer,
                    "category": m.category,
                    "scope_key": m.scope_key,
                    "status": m.status,
                    "origin": "keyword",
                }
                for m in rows
            ]
            return {"mode": mode, "results": results, "semantic_available": False}

        scored, trace = await memory.retrieve_with_trace(
            query, scope_keys=scopes, topic_titles=await self._topic_titles(scopes)
        )
        if mode == "semantic":
            scored = [item for item in scored if item.semantic > 0] or scored
        return {
            "mode": mode,
            "results": [item.to_dict() for item in scored[:limit]],
            "semantic_available": trace.semantic_available,
            "trace": trace.to_dict() if hasattr(trace, "to_dict") else {},
        }

    async def _topic_titles(self, scope_keys: list[str]) -> list[str]:
        titles: list[str] = []
        for key in scope_keys[:3]:
            titles.extend(await self._active_topics(key))
        return titles

    async def _active_topics(self, scope_key: str) -> list[str]:
        topics = await self.bot.behavior.topics.get_active_topics(scope_key, limit=5)
        return [topic.title for topic in topics]

    async def timeline(self, scope_key: str = "", limit: int = 200) -> list[dict[str, Any]]:
        if self.bot.memory is None:
            return []
        memories = await self.bot.memory.timeline(scope_key, limit)
        return [m.model_dump() for m in memories]

    async def detail(self, memory_id: int) -> dict[str, Any]:
        """One memory plus its relations — 'where did this come from?' (§52)."""
        if self.bot.memory is None:
            return {}
        memory = await self.bot.memory.repository.get(memory_id)
        if memory is None:
            return {}
        relations = await self.bot.memory.repository.relations(memory_id)
        superseded_by: list[dict[str, Any]] = []
        if memory.supersedes_id:
            previous = await self.bot.memory.repository.get(memory.supersedes_id)
            if previous is not None:
                superseded_by.append(previous.model_dump())
        return {
            "memory": memory.model_dump(),
            "relations": relations,
            "supersedes": superseded_by,
        }

    # -------------------------------------------------------------- actions

    async def action(self, action: str, memory_id: int, data: dict[str, Any] | None = None) -> bool:
        if self.bot.memory is None:
            return False
        memory = self.bot.memory
        if action == "delete":
            return await memory.delete_memory(memory_id)
        if action == "archive":
            return await memory.set_status(memory_id, "archived")
        if action == "activate":
            return await memory.set_status(memory_id, "active")
        if action == "edit" and data:
            return await memory.edit_memory(
                memory_id,
                str(data.get("content", "")).strip(),
                str(data.get("category", "fact")),
                float(data.get("importance", 0.5)),
                status=str(data.get("status") or "") or None,
                summary=str(data.get("summary", "")),
            )
        if action == "reembed":
            if self.bot.memory is None:
                return False
            record = await memory.repository.get(memory_id)
            if record is None:
                return False
            await memory._ensure_embedding(record)  # noqa: SLF001
            return True
        return False

    # --------------------------------------------------------- debug + ops

    async def retrieval_debug(self, query: str, scope_key: str) -> dict[str, Any]:
        """Show the full retrieval pipeline for one query (spec §88)."""
        if self.bot.memory is None:
            return {"error": "memory disabled"}
        scopes = [scope_key] if scope_key else await self.bot.memory.repository.all_scopes()
        topics = await self._topic_titles(scopes)
        scored, trace = await self.bot.memory.retrieve_with_trace(
            query, scope_keys=scopes, topic_titles=topics
        )
        return {
            "query": query,
            "scopes": scopes,
            "topics": topics,
            "candidates": trace.candidate_count,
            "keyword_candidates": trace.keyword_candidates,
            "semantic_candidates": trace.semantic_candidates,
            "merged_candidates": trace.merged_candidates,
            "injected": trace.injected,
            "dropped_by_guard": trace.dropped,
            "top_score": trace.top_score,
            "duration_ms": trace.duration_ms,
            "semantic_available": trace.semantic_available,
            "weights": self.bot.config.memory.retrieval.weights.model_dump(),
            "min_final_score": self.bot.config.memory.retrieval.min_final_score,
            "results": [item.to_dict() for item in scored],
        }

    async def health(self) -> dict[str, Any]:
        if self.bot.consolidator is None:
            return {"enabled": False}
        data = await self.bot.consolidator.health()
        data["enabled"] = True
        data["embedding"] = (
            self.bot.embeddings.snapshot() if self.bot.embeddings is not None else {}
        )
        scheduler = self.bot.consolidation_scheduler
        data["scheduler"] = {
            "schedule": self.bot.config.memory.consolidation.schedule,
            "running": bool(scheduler and scheduler.running),
            "runs": scheduler.runs if scheduler else 0,
            "next_run_in": scheduler.next_run_in() if scheduler else None,
        }
        data["policy"] = self.bot.config.memory.policy.model_dump()
        data["retention"] = self.bot.config.memory.retention.model_dump()
        return data

    async def embedding_status(self) -> dict[str, Any]:
        if self.bot.memory is None:
            return {"available": False}
        stats = await self.bot.memory.vectors.stats()
        service = self.bot.embeddings
        return {
            **stats,
            **(service.snapshot() if service is not None else {"available": False}),
            "pending": len(
                await self.bot.memory.vectors.missing_memory_ids(limit=1000)  # type: ignore[attr-defined]
            ),
        }

    async def consolidation_status(self) -> dict[str, Any]:
        if self.bot.consolidator is None:
            return {"enabled": False}
        report = self.bot.consolidator.last_report
        scheduler = self.bot.consolidation_scheduler
        return {
            "enabled": True,
            "schedule": self.bot.config.memory.consolidation.schedule,
            "duplicate_threshold": self.bot.config.memory.consolidation.duplicate_threshold,
            "compression_min_cluster": self.bot.config.memory.consolidation.compression_min_cluster,
            "last_report": report.to_dict() if report else None,
            "runs": scheduler.runs if scheduler else 0,
        }

    async def run_consolidation(self, scope_key: str = "") -> dict[str, Any]:
        """Manual trigger (spec §89). Runs in the background, never in chat."""
        if self.bot.consolidator is None:
            return {"error": "memory disabled"}
        report = await self.bot.consolidator.run(scope_key)
        return report.to_dict()

    async def rebuild_embeddings(self, limit: int = 200) -> dict[str, Any]:
        """Backfill missing vectors in the background (spec §57/§90)."""
        if self.bot.memory is None or self.bot.embeddings is None:
            return {"error": "semantic memory disabled"}
        if not self.bot.embeddings.available:
            return {"error": "embedding provider unavailable"}
        done = await self.bot.memory.backfill_embeddings(limit=limit)
        return {"rebuilt": done}

    async def retry_failed(self) -> dict[str, Any]:
        return await self.rebuild_embeddings(limit=200)

    async def clear_embedding_cache(self) -> dict[str, Any]:
        if self.bot.embeddings is None:
            return {"error": "semantic memory disabled"}
        await self.bot.embeddings.clear_cache()
        return {"cleared": True, "at": int(time.time())}
