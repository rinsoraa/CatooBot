"""Memory foundation (v2.1 Phase 4 §8-§17): candidates, promotion, provenance.

    ExperienceRecord → MemoryCandidateBuilder → MemoryCandidate
                     → SandboxMemoryStore (dedupe → promote) → memories row

The store writes into the **existing** ``memories`` table through the existing
repository (§3/§26) — no second memory system. Two additions make sandbox
memories first-class without disturbing the conversation-era rows:

* ``character_id`` is the isolation boundary (§11): two characters sharing a
  database cannot read each other's sandbox memories;
* ``provenance`` carries memory → experience → sandbox-event ids (§10), and
  ``dedupe_key`` is the deterministic identity used to refuse duplicates (§15).

Everything here is deterministic and LLM-free (§14); embeddings are not
touched (§27) — the store deliberately bypasses the embedding path.
"""

from __future__ import annotations

import json
import uuid
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.memory.model import Memory, scope_key
from app.memory.repository import MemoryRepository
from app.sandbox.experience import ExperienceKind, ExperienceRecord


class MemoryType(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    episodic = "episodic"
    semantic = "semantic"
    social = "social"
    world_fact = "world_fact"


class MemoryScope(str, Enum):  # noqa: UP042 - §12
    private = "private"  # her own inner memories
    social = "social"  # interactions with others
    world = "world"  # world facts
    self_ = "self"  # long-term self knowledge


#: memory_type → (existing model layer, existing model category)
_TYPE_MAPPING: dict[MemoryType, tuple[str, str]] = {
    MemoryType.episodic: ("episodic", "event"),
    MemoryType.semantic: ("semantic", "fact"),
    MemoryType.social: ("episodic", "relationship"),
    MemoryType.world_fact: ("semantic", "fact"),
}


class MemoryCandidate(BaseModel):
    """A memory worth considering — not yet a long-term memory (§8/§16)."""

    candidate_id: str
    character_id: str = ""
    memory_type: MemoryType = MemoryType.episodic
    summary: str = ""
    content: str = ""
    scope: MemoryScope = MemoryScope.self_
    importance: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    status: str = "candidate"  # candidate / promoted / rejected / duplicate
    dedupe_key: str = ""
    source_experience_ids: list[str] = Field(default_factory=list)
    source_event_ids: list[str] = Field(default_factory=list)
    created_at: float = 0.0
    observed_at: float = 0.0
    reason_code: str = ""


class MemoryCandidateBuilder:
    """Deterministic Experience → candidate mapping (§19-§22). No LLM."""

    #: nothing below this becomes a candidate at all
    CANDIDATE_FLOOR = 0.3
    #: candidates at/above this are eligible for promotion to long-term memory
    PROMOTION_THRESHOLD = 0.5

    def from_experience(self, experience: ExperienceRecord, *, now: float) -> list[MemoryCandidate]:
        if experience.importance < self.CANDIDATE_FLOOR:
            return []
        spec = self._spec(experience)
        if spec is None:
            return []
        memory_type, scope, content, identity, confidence = spec
        candidate = MemoryCandidate(
            candidate_id=f"cand_{uuid.uuid4().hex[:12]}",
            character_id=experience.character_id,
            memory_type=memory_type,
            summary=experience.summary,
            content=content,
            scope=scope,
            importance=experience.importance,
            confidence=confidence,
            dedupe_key=f"{experience.character_id}|{memory_type.value}|{identity}",
            source_experience_ids=[experience.id],
            source_event_ids=list(experience.source_event_ids),
            created_at=now,
            observed_at=experience.timestamp,
            reason_code=experience.kind.value,
        )
        return [candidate]

    def _spec(  # noqa: PLR0911 - one branch per kind, flat by design
        self, experience: ExperienceRecord
    ) -> tuple[MemoryType, MemoryScope, str, str, float] | None:
        meta = experience.metadata
        kind = experience.kind
        # episodic identity is per-chain: the same event must dedupe, but a
        # genuinely repeated episode tomorrow is a *new* memory (§15)
        chain = experience.correlation_id or experience.id
        if kind is ExperienceKind.action_completed:
            name = str(meta.get("action_name", ""))
            return (
                MemoryType.episodic,
                MemoryScope.private,
                f"完成了{name}",
                f"action:{name}:{chain}",
                0.9,
            )
        if kind is ExperienceKind.action_interrupted:
            name = str(meta.get("action_name", ""))
            return (
                MemoryType.episodic,
                MemoryScope.private,
                f"{name}进行到一半被打断（{meta.get('reason', '')}）",
                f"interrupt:{name}:{chain}",
                0.85,
            )
        if kind is ExperienceKind.action_resumed:
            name = str(meta.get("action_name", ""))
            return (
                MemoryType.episodic,
                MemoryScope.private,
                f"继续进行{name}",
                f"resume:{name}:{chain}",
                0.8,
            )
        if kind is ExperienceKind.pet_care:
            pet = str(meta.get("pet", "宠物"))
            food = str(meta.get("food", ""))
            return (
                MemoryType.social,
                MemoryScope.social,
                f"给{pet}添了{food}".rstrip("了"),
                f"pet_fed:{pet}:{chain}",
                0.9,
            )
        if kind is ExperienceKind.project_milestone:
            project = str(meta.get("project", ""))
            name = str(meta.get("name", project))
            after = float(meta.get("after", 0.0))
            completed = after >= 1.0
            content = f"{name}完工了" if completed else f"{name}推进到了 {round(after * 100)}%"
            # world_fact identity is semantic: the same milestone dedupes, and
            # a later milestone supersedes the earlier fact (§17)
            return (
                MemoryType.world_fact if completed else MemoryType.episodic,
                MemoryScope.world,
                content,
                f"project:{project}:{'done' if completed else round(after, 2)}",
                0.95,
            )
        if kind is ExperienceKind.knowledge_learned:
            key = str(meta.get("knowledge_key", ""))
            return (
                MemoryType.world_fact,
                MemoryScope.world,
                f"首次获知：{key.replace('_', ' ')}",
                f"knowledge:{key}",
                0.8,
            )
        if kind is ExperienceKind.external_influence:
            actor = str(meta.get("actor", ""))
            return (
                MemoryType.social,
                MemoryScope.social,
                f"{actor}发出的邀请改变了当前安排" if actor else experience.summary,
                f"external:{actor}:{chain}",
                0.75,
            )
        if kind is ExperienceKind.relationship_changed:
            person = str(meta.get("person_id", ""))
            return (
                MemoryType.social,
                MemoryScope.social,
                f"与某人的关系发生了变化（{meta.get('interaction_type', '')}）"
                if not meta.get("name")
                else f"与{meta.get('name')}的关系发生了变化",
                f"relationship:{person}",
                0.85,
            )
        if kind is ExperienceKind.commitment_outcome:
            person = str(meta.get("person_id", ""))
            outcome = str(meta.get("outcome", ""))
            label = str(meta.get("name", "")) or "某人"
            if outcome == "fulfilled":
                summary, importance = f"说到做到：与{label}的约定履行了", 0.75
            elif outcome == "broken":
                summary, importance = f"没能守约：与{label}的约定没做到", 0.85
            else:
                summary, importance = f"与{label}的约定改期了", 0.5
            return (
                MemoryType.social,
                MemoryScope.social,
                summary,
                f"commitment:{meta.get('commitment_id', chain)}:{outcome}",
                importance,
            )
        if kind is ExperienceKind.goal_completed:
            description = str(meta.get("description", ""))
            return (
                MemoryType.episodic,
                MemoryScope.self_,
                f"完成目标：{description}",
                f"goal:{meta.get('goal_id', chain)}",
                0.9,
            )
        if kind is ExperienceKind.interaction_completed:
            return (
                MemoryType.episodic,
                MemoryScope.private,
                experience.summary,
                f"interaction:{meta.get('target', '')}:{chain}",
                0.8,
            )
        if kind is ExperienceKind.social_contact:
            # below the promotion threshold by construction: experiences only
            return None
        return None


class SandboxMemoryStore:
    """Persist candidates and promote the eligible ones into ``memories``.

    Promotion rules (§13/§16/§17): importance ≥ threshold, no duplicate
    dedupe_key, and — for semantic/world_fact identities — a changed value
    supersedes the previous row instead of duplicating it.
    """

    def __init__(
        self,
        database: Any,
        *,
        character_id: str,
        clock: Any,
        logger: Any = None,
    ) -> None:
        self._db = database
        self.character_id = character_id
        self._clock = clock
        self._log = logger
        self._repo = MemoryRepository(database, clock=clock) if database is not None else None
        self.promoted = 0
        self.duplicates = 0
        self.rejected = 0

    @property
    def available(self) -> bool:
        return self._db is not None and self._repo is not None

    # -------------------------------------------------------------- ingest

    async def ingest(self, candidate: MemoryCandidate) -> MemoryCandidate:
        """Persist the candidate, then promote it when it qualifies."""
        if not self.available:
            return candidate
        if await self._is_duplicate(candidate):
            self.duplicates += 1
            candidate.status = "duplicate"
            return candidate
        await self._insert_candidate(candidate)
        if candidate.importance < MemoryCandidateBuilder.PROMOTION_THRESHOLD:
            candidate.status = "rejected"
            candidate.reason_code = f"{candidate.reason_code}:below_threshold"
            await self._decide(candidate, memory_id=None)
            self.rejected += 1
            return candidate
        if await self._same_identity_exists(candidate):
            # the same fact is already active — dedupe, do not stack (§15)
            candidate.status = "duplicate"
            candidate.reason_code = f"{candidate.reason_code}:same_identity"
            await self._decide(candidate, memory_id=None)
            self.duplicates += 1
            return candidate
        memory = await self._promote(candidate)
        if memory is None:
            candidate.status = "rejected"
            candidate.reason_code = f"{candidate.reason_code}:promotion_failed"
            await self._decide(candidate, memory_id=None)
            self.rejected += 1
            return candidate
        candidate.status = "promoted"
        await self._decide(candidate, memory_id=memory.id)
        self.promoted += 1
        return candidate

    async def ingest_all(self, candidates: list[MemoryCandidate]) -> list[MemoryCandidate]:
        return [await self.ingest(candidate) for candidate in candidates]

    # ------------------------------------------------------------ internals

    async def _is_duplicate(self, candidate: MemoryCandidate) -> bool:
        if not candidate.dedupe_key:
            return False
        row = await self._db.fetchone(
            "SELECT candidate_id FROM sandbox_memory_candidates"
            " WHERE character_id = ? AND dedupe_key = ? AND status IN ('candidate','promoted')"
            " LIMIT 1",
            (candidate.character_id, candidate.dedupe_key),
        )
        return row is not None

    async def _insert_candidate(self, candidate: MemoryCandidate) -> None:
        await self._db.execute(
            """INSERT INTO sandbox_memory_candidates
                   (candidate_id, character_id, memory_type, summary, content, scope,
                    importance, confidence, status, dedupe_key,
                    source_experience_ids, source_event_ids, created_at, observed_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                candidate.candidate_id,
                candidate.character_id,
                candidate.memory_type.value,
                candidate.summary,
                candidate.content,
                candidate.scope.value,
                candidate.importance,
                candidate.confidence,
                "candidate",
                candidate.dedupe_key,
                json.dumps(candidate.source_experience_ids),
                json.dumps(candidate.source_event_ids),
                candidate.created_at,
                candidate.observed_at,
            ),
        )

    async def _decide(self, candidate: MemoryCandidate, *, memory_id: int | None) -> None:
        await self._db.execute(
            "UPDATE sandbox_memory_candidates SET status = ?, reason_code = ?,"
            " memory_id = ?, decided_at = ? WHERE candidate_id = ?",
            (
                candidate.status,
                candidate.reason_code,
                memory_id,
                float(self._clock()),
                candidate.candidate_id,
            ),
        )

    async def _promote(self, candidate: MemoryCandidate) -> Memory | None:
        """Write the long-term row; semantic identities supersede, not stack."""
        assert self._repo is not None
        layer, category = _TYPE_MAPPING[candidate.memory_type]
        supersedes_id = await self._supersede_previous(candidate)
        try:
            memory = await self._repo.add(
                Memory(
                    scope_key=scope_key("character", candidate.character_id),
                    category=category,
                    content=candidate.content,
                    summary=candidate.summary,
                    layer=layer,
                    source="sandbox",
                    status="active",
                    importance=candidate.importance,
                    confidence=candidate.confidence,
                    character_id=candidate.character_id,
                    dedupe_key=candidate.dedupe_key,
                    provenance={
                        "candidate_id": candidate.candidate_id,
                        "memory_type": candidate.memory_type.value,
                        "scope": candidate.scope.value,
                        "source_experience_ids": candidate.source_experience_ids,
                        "source_event_ids": candidate.source_event_ids,
                        "reason_code": candidate.reason_code,
                    },
                    supersedes_id=supersedes_id,
                    event_at=int(candidate.observed_at) if candidate.observed_at else None,
                )
            )
        except Exception:  # noqa: BLE001 - memory trouble must never break the world
            if self._log is not None:
                self._log.exception("[SandboxMemory] promotion failed")
            return None
        if supersedes_id is not None:
            await self._repo.add_relation(memory.id, supersedes_id, "supersedes")
        return memory

    async def _same_identity_exists(self, candidate: MemoryCandidate) -> bool:
        row = await self._db.fetchone(
            "SELECT id FROM memories WHERE character_id = ? AND dedupe_key = ?"
            " AND status = 'active' LIMIT 1",
            (candidate.character_id, candidate.dedupe_key),
        )
        return row is not None

    async def _supersede_previous(self, candidate: MemoryCandidate) -> int | None:
        """A changed semantic fact retires its predecessor (§17).

        Identity = dedupe_key without the value suffix, e.g. the previous
        ``project:<id>:0.5`` fact when ``project:<id>:0.75`` arrives.
        """
        if candidate.memory_type not in (MemoryType.world_fact, MemoryType.semantic):
            return None
        prefix = candidate.dedupe_key.rsplit(":", 1)[0]
        if prefix == candidate.dedupe_key:
            return None
        rows = await self._db.fetchall(
            "SELECT id FROM memories WHERE character_id = ? AND status = 'active'"
            " AND dedupe_key LIKE ? AND dedupe_key <> ?",
            (candidate.character_id, f"{prefix}:%", candidate.dedupe_key),
        )
        if not rows:
            return None
        previous_id = int(rows[-1]["id"])
        repo = self._repo
        if repo is None:  # pragma: no cover - guarded by available
            return None
        await repo.set_status(previous_id, "superseded")
        return previous_id

    # ---------------------------------------------------------- retrieval

    #: scoring weights (sum = 1.0) — deterministic, documented, tunable here
    RETRIEVAL_WEIGHTS = {
        "keyword": 0.45,
        "entity": 0.15,
        "importance": 0.25,
        "recency": 0.15,
    }
    #: candidate window: newest N active rows for this character (bounded, §34)
    RETRIEVAL_CANDIDATES = 200
    #: recency half-life in days (a fortnight roughly halves a memory's pull)
    RETRIEVAL_HALF_LIFE_DAYS = 14.0

    async def retrieve_relevant(
        self,
        *,
        query: str,
        entities: list[str] | None = None,
        limit: int = 3,
        min_score: float = 0.12,
        max_chars: int = 600,
        require_evidence: bool = True,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        """Relevant active memories for a turn, plus retrieval bookkeeping (§7-§9).

        Scoring is deterministic: 相同输入 + 相同数据库状态 → 相同排序。
        Only *active* rows are considered (§29): a superseded fact must never
        be injected next to its successor. No embedding, no LLM, bounded
        candidate window, explicit count/char budget.
        """
        from app.memory.keyword_index import bigrams

        trace: dict[str, Any] = {
            "query": query,
            "candidates": 0,
            "selected": 0,
            "min_score": min_score,
            "limit": limit,
            "require_evidence": require_evidence,
            "reason": "",
        }
        if not self.available or limit <= 0 or max_chars <= 0:
            trace["reason"] = "disabled_or_unavailable"
            return [], trace
        rows = await self._db.fetchall(
            "SELECT id, content, summary, importance, confidence, layer, category,"
            " created_at, provenance FROM memories"
            " WHERE character_id = ? AND status = 'active'"
            " ORDER BY id DESC LIMIT ?",
            (self.character_id, self.RETRIEVAL_CANDIDATES),
        )
        trace["candidates"] = len(rows)
        if not rows:
            trace["reason"] = "no_active_memories"
            return [], trace
        query_tokens = bigrams(query or "")
        entity_needles = [str(entity) for entity in (entities or []) if entity]
        now = float(self._clock())
        scored: list[dict[str, Any]] = []
        for row in rows:
            text = f"{row['summary']} {row['content']}".strip()
            tokens = bigrams(text)
            overlap = len(query_tokens & tokens) if query_tokens else 0
            keyword = min(1.0, overlap / max(4, len(query_tokens))) if query_tokens else 0.0
            entity_hit = 1.0 if any(needle in text for needle in entity_needles) else 0.0
            importance = max(0.0, min(1.0, float(row["importance"] or 0.0)))
            age_days = max(0.0, (now - float(row["created_at"] or now)) / 86400.0)
            recency = 0.5 ** (age_days / self.RETRIEVAL_HALF_LIFE_DAYS)
            score = (
                self.RETRIEVAL_WEIGHTS["keyword"] * keyword
                + self.RETRIEVAL_WEIGHTS["entity"] * entity_hit
                + self.RETRIEVAL_WEIGHTS["importance"] * importance
                + self.RETRIEVAL_WEIGHTS["recency"] * recency
            )
            # evidence gate (§8, mirrors the conversation-retrieval lesson):
            # importance + recency alone never pull an unrelated memory in.
            if require_evidence and not (overlap > 0 or entity_hit > 0.0):
                continue
            if score < min_score:
                continue
            scored.append(
                {
                    "memory_id": int(row["id"]),
                    "text": text,
                    "summary": str(row["summary"] or ""),
                    "importance": importance,
                    "score": round(score, 6),
                    "source": "sandbox",
                    "provenance": self._decode_provenance(row),
                    "_budget_len": len(text),
                }
            )
        # deterministic order: score desc, then newest id first
        scored.sort(key=lambda item: (-item["score"], -item["memory_id"]))
        selected: list[dict[str, Any]] = []
        used = 0
        for item in scored:
            if len(selected) >= limit:
                break
            remaining = max_chars - used
            text = str(item["text"])
            if remaining < 40:
                break
            if len(text) > remaining:
                item["text"] = text[:remaining].rstrip() + "…"
            used += len(str(item["text"]))
            selected.append(item)
        for item in selected:
            item.pop("_budget_len", None)
        trace["selected"] = len(selected)
        trace["reason"] = (
            f"{len(selected)} relevant memories" if selected else "no memory above min_score"
        )
        if selected:
            trace["memory_ids"] = [item["memory_id"] for item in selected]
        return selected, trace

    @staticmethod
    def _decode_provenance(row: Any) -> dict[str, Any]:
        try:
            data = json.loads(row["provenance"] or "{}")
        except (TypeError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    # ------------------------------------------------------------- reading

    async def active_memories(self, *, limit: int = 20) -> list[dict[str, Any]]:
        """Active sandbox memories for this character (read model, §23)."""
        if not self.available:
            return []
        rows = await self._db.fetchall(
            "SELECT id, content, summary, importance, confidence, layer, category,"
            " created_at, provenance FROM memories"
            " WHERE character_id = ? AND status = 'active'"
            " ORDER BY importance DESC, id DESC LIMIT ?",
            (self.character_id, limit),
        )
        return [self._memory_row(row) for row in rows]

    async def memory_provenance(self, memory_id: int) -> dict[str, Any] | None:
        """memory → experience → sandbox event ids (§10/§28-12)."""
        if not self.available:
            return None
        row = await self._db.fetchone(
            "SELECT id, character_id, provenance FROM memories WHERE id = ?", (memory_id,)
        )
        if row is None or str(row["character_id"]) != self.character_id:
            return None
        try:
            provenance = json.loads(row["provenance"] or "{}")
        except ValueError:
            provenance = {}
        return {"memory_id": int(row["id"]), **provenance}

    async def count(self, *, status: str = "active") -> int:
        if not self.available:
            return 0
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM memories WHERE character_id = ? AND status = ?",
            (self.character_id, status),
        )
        return int(row["n"]) if row else 0

    @staticmethod
    def _memory_row(row: Any) -> dict[str, Any]:
        data = dict(row)
        try:
            data["provenance"] = json.loads(data.get("provenance") or "{}")
        except ValueError:
            data["provenance"] = {}
        return data
