"""Memory consolidation: background upkeep that keeps memory useful.

Runs on its own schedule — never per message (spec §35) — and never blocks
chat. Everything it does is reversible bookkeeping:

* duplicates  → keep the strongest row, archive the rest (spec §30/§31)
* conflicts   → not touched here; they are resolved at write time (§33)
* retention   → old *episodic* memories are archived, not deleted (§74)
* compression → a cluster of related episodes becomes one semantic memory,
                the episodes are archived and linked (§37/§38)
* quota       → over-quota scopes shed their least valuable rows (§72)

Health checks (spec §75) report counts; failures are logged and swallowed.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.memory.model import Memory
from app.memory.retrieval import bigrams
from app.memory.vector_store import cosine_similarity

if TYPE_CHECKING:
    from app.config.settings import MemoryConfig
    from app.memory.manager import MemoryManager

_SCHEDULE_INTERVALS = {"hourly": 3600.0, "daily": 86400.0, "manual": 0.0}


@dataclass
class ConsolidationReport:
    scanned: int = 0
    duplicates_merged: int = 0
    archived: int = 0
    compressed_clusters: int = 0
    compressed_sources: int = 0
    conflicts: int = 0
    quota_archived: int = 0
    errors: int = 0
    duration_ms: float = 0.0
    scopes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"merged={self.duplicates_merged} compressed={self.compressed_clusters}"
            f"({self.compressed_sources} sources) archived={self.archived}"
            f" conflicts={self.conflicts} quota={self.quota_archived}"
            f" scanned={self.scanned}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "scanned": self.scanned,
            "duplicates_merged": self.duplicates_merged,
            "archived": self.archived,
            "compressed_clusters": self.compressed_clusters,
            "compressed_sources": self.compressed_sources,
            "conflicts": self.conflicts,
            "quota_archived": self.quota_archived,
            "errors": self.errors,
            "duration_ms": round(self.duration_ms, 1),
            "scopes": self.scopes,
            "summary": self.summary(),
        }


class MemoryConsolidator:
    def __init__(
        self,
        config: MemoryConfig,
        manager: MemoryManager,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._manager = manager
        self._log = logger or logging.getLogger("CatooBot.Memory.Consolidation")
        self._clock = clock
        self.last_report: ConsolidationReport | None = None

    # ------------------------------------------------------------------ api

    async def run(self, scope_key: str = "", *, aggressive: bool = False) -> ConsolidationReport:
        """One consolidation pass. Never raises (spec §75)."""
        started = self._clock()
        report = ConsolidationReport()
        try:
            scopes = [scope_key] if scope_key else await self._manager.repository.all_scopes()
            report.scopes = scopes
            for scope in scopes:
                try:
                    await self._consolidate_scope(scope, report, aggressive=aggressive)
                except Exception:  # noqa: BLE001 - one scope must not stop the run
                    report.errors += 1
                    self._log.exception("[Memory.Consolidation] scope %s failed", scope)
        except Exception:  # noqa: BLE001
            report.errors += 1
            self._log.exception("[Memory.Consolidation] run failed")
        report.duration_ms = (self._clock() - started) * 1000
        self.last_report = report
        self._log.info("[Memory.Consolidation] %s (%.0fms)", report.summary(), report.duration_ms)
        return report

    # --------------------------------------------------------- per scope

    async def _consolidate_scope(
        self, scope_key: str, report: ConsolidationReport, *, aggressive: bool
    ) -> None:
        cfg = self._config.consolidation
        limit = cfg.max_scan if aggressive else cfg.max_scan
        active = await self._manager.repository.candidates([scope_key], limit=limit)
        report.scanned += len(active)
        if not active:
            return

        vectors = await self._load_vectors([m.id for m in active])
        kept = await self._merge_duplicates(active, vectors, report)
        kept = await self._apply_retention(kept, report)
        await self._compress_clusters(kept, vectors, report, scope_key)

    async def _load_vectors(self, memory_ids: list[int]) -> dict[int, list[float]]:
        store = self._manager.vectors
        vectors: dict[int, list[float]] = {}
        for memory_id in memory_ids:
            record = await store.get(memory_id)
            if record is not None and record.vector:
                vectors[memory_id] = record.vector
        return vectors

    async def _merge_duplicates(
        self,
        memories: list[Memory],
        vectors: dict[int, list[float]],
        report: ConsolidationReport,
    ) -> list[Memory]:
        """Collapse near-identical rows, keeping the strongest (spec §30/§31)."""
        threshold = self._config.consolidation.duplicate_threshold
        ordered = sorted(memories, key=lambda m: (m.confidence, m.importance), reverse=True)
        superseded: set[int] = set()
        for index, primary in enumerate(ordered):
            if primary.id in superseded:
                continue
            for other in ordered[index + 1 :]:
                if other.id in superseded:
                    continue
                score = self._pair_similarity(primary, other, vectors)
                if score < threshold:
                    continue
                # Same information twice: archive the weaker row, keep history.
                await self._manager.repository.set_status(other.id, "archived")
                await self._manager.repository.add_relation(primary.id, other.id, "related_to")
                superseded.add(other.id)
                report.duplicates_merged += 1
                report.archived += 1
        return [memory for memory in ordered if memory.id not in superseded]

    @staticmethod
    def _pair_similarity(left: Memory, right: Memory, vectors: dict[int, list[float]]) -> float:
        left_vector, right_vector = vectors.get(left.id), vectors.get(right.id)
        if left_vector and right_vector:
            return cosine_similarity(left_vector, right_vector)
        tokens_left, tokens_right = bigrams(left.content), bigrams(right.content)
        if not tokens_left or not tokens_right:
            return 0.0
        return len(tokens_left & tokens_right) / max(1, min(len(tokens_left), len(tokens_right)))

    async def _apply_retention(
        self, memories: list[Memory], report: ConsolidationReport
    ) -> list[Memory]:
        """Archive episodic memories past the retention window (spec §74)."""
        days = self._config.retention.episodic_days
        cutoff = int(self._clock()) - days * 86400
        surviving: list[Memory] = []
        for memory in memories:
            stamp = memory.event_at or memory.created_at
            if (
                memory.layer == "episodic"
                and stamp
                and stamp < cutoff
                and memory.status == "active"
            ):
                await self._manager.repository.set_status(memory.id, "archived")
                report.archived += 1
                continue
            is_expired = (
                memory.valid_until is not None
                and memory.valid_until < int(self._clock())
                and memory.status == "active"
            )
            if is_expired:
                await self._manager.repository.set_status(memory.id, "expired")
                report.archived += 1
                continue
            surviving.append(memory)
        return surviving

    async def _compress_clusters(
        self,
        memories: list[Memory],
        vectors: dict[int, list[float]],
        report: ConsolidationReport,
        scope_key: str,
    ) -> None:
        """Turn a cluster of related episodes into one semantic memory (§37)."""
        cfg = self._config.consolidation
        episodes = [m for m in memories if m.layer == "episodic"]
        if len(episodes) < cfg.compression_min_cluster:
            return

        clusters = self._cluster(episodes, vectors)
        for cluster in clusters:
            if len(cluster) < cfg.compression_min_cluster:
                continue
            summary = self._summarize(cluster)
            if not summary:
                continue
            primary = max(cluster, key=lambda m: m.importance)
            compressed = await self._manager.repository.add(
                Memory(
                    scope_key=scope_key,
                    user_id=primary.user_id,
                    group_id=primary.group_id,
                    category="project" if primary.category in ("project", "event") else "fact",
                    content=summary,
                    summary=summary,
                    importance=min(0.95, max(m.importance for m in cluster) + 0.05),
                    confidence=0.75,
                    layer="semantic",
                    source="system",
                )
            )
            for memory in cluster:
                await self._manager.repository.set_status(memory.id, "archived")
                await self._manager.repository.add_relation(
                    compressed.id, memory.id, "compressed_from"
                )
            report.compressed_clusters += 1
            report.compressed_sources += len(cluster)
            report.archived += len(cluster)
            await self._manager._ensure_embedding(compressed)  # noqa: SLF001
            self._log.info(
                "[Memory.Consolidation] compressed %d episodes -> #%d",
                len(cluster),
                compressed.id,
            )

    def _cluster(
        self, episodes: list[Memory], vectors: dict[int, list[float]]
    ) -> list[list[Memory]]:
        """Single-link clustering by similarity — enough for v0.5 (spec §29)."""
        threshold = self._config.consolidation.conflict_threshold
        clusters: list[list[Memory]] = []
        for episode in episodes:
            placed = False
            for cluster in clusters:
                if any(
                    self._pair_similarity(episode, member, vectors) >= threshold
                    for member in cluster
                ):
                    cluster.append(episode)
                    placed = True
                    break
            if not placed:
                clusters.append([episode])
        return clusters

    @staticmethod
    def _summarize(cluster: list[Memory]) -> str:
        """Rule-based summary (LLM compression is opt-in, spec §37)."""
        ordered = sorted(cluster, key=lambda m: m.event_at or m.created_at)
        pieces: list[str] = []
        for memory in ordered:
            text = memory.display_text.rstrip("。！.")
            if text and text not in pieces:
                pieces.append(text)
        if not pieces:
            return ""
        if len(pieces) == 1:
            return pieces[0] + "。"
        joined = "；".join(pieces[:4])
        return f"用户在这段时间里陆续聊到：{joined}。"

    # -------------------------------------------------------------- health

    async def health(self) -> dict[str, Any]:
        """Read-only memory health report for the WebUI (spec §55/§75)."""
        repository = self._manager.repository
        status_counts = await repository.status_counts()
        total = sum(status_counts.values())
        active = status_counts.get("active", 0)
        stats = await self._manager.vectors.stats()
        rows = await repository.candidates(
            await repository.all_scopes(), limit=self._config.consolidation.max_scan
        )
        avg_importance = round(sum(m.importance for m in rows) / len(rows), 3) if rows else 0.0
        avg_confidence = round(sum(m.confidence for m in rows) / len(rows), 3) if rows else 0.0
        return {
            "total": total,
            "active": active,
            "archived": status_counts.get("archived", 0),
            "superseded": status_counts.get("superseded", 0),
            "expired": status_counts.get("expired", 0),
            "episodic": await repository.count(status="active", layer="episodic"),
            "semantic": await repository.count(status="active", layer="semantic"),
            "embedding_coverage": stats.get("coverage", 0.0),
            "embedded": stats.get("embedded", 0),
            "average_importance": avg_importance,
            "average_confidence": avg_confidence,
            "retrieval": self._manager.retriever.snapshot(),
            "last_consolidation": (self.last_report.to_dict() if self.last_report else None),
        }


class ConsolidationScheduler:
    """Background runner: daily/hourly/manual, one task, error-isolated (§34)."""

    def __init__(
        self,
        consolidator: MemoryConsolidator,
        schedule: str = "daily",
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._consolidator = consolidator
        self._schedule = schedule if schedule in _SCHEDULE_INTERVALS else "daily"
        self._interval = _SCHEDULE_INTERVALS[self._schedule]
        self._log = logger or logging.getLogger("CatooBot.Memory.Consolidation")
        self._clock = clock
        self._task: asyncio.Task[None] | None = None
        self._stopping = asyncio.Event()
        self.runs = 0
        self.last_run_at: float | None = None
        # When the shared BehaviourScheduler drives this job (v0.8 §34) there is
        # no task of our own, but the feature is still very much running.
        self.external_driver = False

    @property
    def enabled(self) -> bool:
        return self._interval > 0

    @property
    def interval_seconds(self) -> float:
        return self._interval

    @property
    def running(self) -> bool:
        if self.external_driver:
            return self.enabled
        return self._task is not None and not self._task.done()

    def next_run_in(self) -> float | None:
        if not self.enabled or self.last_run_at is None:
            return 0.0 if self.enabled else None
        return max(0.0, self._interval - (self._clock() - self.last_run_at))

    async def start(self) -> None:
        if not self.enabled or self._task is not None:
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run(), name="memory-consolidation")
        self._log.info(
            "[Memory.Consolidation] scheduler started (schedule=%s, every %.0fs)",
            self._schedule,
            self._interval,
        )

    async def stop(self) -> None:
        self._stopping.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        """Wait one interval, run, repeat — the first pass is never a cold start spike."""
        while not self._stopping.is_set():
            await self.wait_interval()
            if self._stopping.is_set():
                return
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                self._log.exception("[Memory.Consolidation] scheduled run failed")

    async def tick(self) -> ConsolidationReport:
        """Force one pass (used by tests and the WebUI button)."""
        report = await self._consolidator.run()
        self.runs += 1
        self.last_run_at = self._clock()
        return report

    async def wait_interval(self) -> None:
        try:
            await asyncio.wait_for(self._stopping.wait(), timeout=self._interval)
        except TimeoutError:
            return
