"""Initiative engine: the character may open a conversation — under hard rules.

Every trigger flows through a Candidate → Gate pipeline (spec v0.8 §32). The Gate
enforces *hard* limits in code (cooldown, hourly/daily budget, DND, sleeping,
disabled user, awaiting-reply) and only then rolls a probability. The LLM
never decides whether it is allowed to speak (spec v0.8 §47).
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.behavior.models import BehaviorEvent, InitiativeState
from app.behavior.presence import PresenceResolver
from app.behavior.topics import TopicManager
from app.character.relationship import CORE_STAGE, STAGES
from app.config.settings import BehaviorInitiativeConfig
from app.memory.retrieval import bigrams

if TYPE_CHECKING:
    from app.database.database import Database
    from app.memory.manager import MemoryManager

DATE_FORMAT = "%Y-%m-%d"
BUCKET_FORMAT = "%Y-%m-%dT%H"


@dataclass
class InitiativeCandidate:
    """Why the character might speak, and what about."""

    scope_key: str
    user_id: str
    reason: str
    topic: str = ""
    prompt_hint: str = ""
    priority: float = 0.5


@dataclass
class GateResult:
    allowed: bool
    reason: str = ""
    probability: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


class InitiativeEngine:
    def __init__(
        self,
        config: BehaviorInitiativeConfig,
        presence: PresenceResolver,
        database: Database | None = None,
        topics: TopicManager | None = None,
        memory: MemoryManager | None = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.presence = presence
        self._db = database
        self.topics = topics
        self._memory = memory
        self._log = logger or logging.getLogger("CatooBot.Initiative")
        self._rng = rng or random.Random()
        self._clock = clock
        self._states: dict[str, InitiativeState] = {}

    @property
    def enabled(self) -> bool:
        """The *general* proactive-chat switch (core friends have their own)."""
        return bool(self.config.enabled and self._db is not None)

    def config_for(self, *, is_core: bool) -> Any:
        """The rule set that applies to this person (core friends are separate)."""
        return self.config.core_friend if is_core else self.config

    # ------------------------------------------------------------- state io

    async def load_state(self, scope_key: str) -> InitiativeState:
        if scope_key in self._states:
            return self._states[scope_key]
        state = InitiativeState(scope_key=scope_key)
        if self._db is not None:
            try:
                row = await self._db.fetchone(
                    "SELECT * FROM initiative_state WHERE scope_key = ?", (scope_key,)
                )
                if row is not None:
                    state = InitiativeState.model_validate(dict(row))
            except Exception:  # noqa: BLE001
                self._log.exception("Failed to load initiative state for %s", scope_key)
        self._states[scope_key] = state
        return state

    async def save_state(self, state: InitiativeState) -> None:
        state.updated_at = int(self._clock())
        self._states[state.scope_key] = state
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO initiative_state
                       (scope_key, last_sent_at, last_candidate_at, unanswered_count,
                        daily_count, daily_date, hourly_count, hourly_bucket,
                        last_message, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(scope_key) DO UPDATE SET
                       last_sent_at=excluded.last_sent_at,
                       last_candidate_at=excluded.last_candidate_at,
                       unanswered_count=excluded.unanswered_count,
                       daily_count=excluded.daily_count,
                       daily_date=excluded.daily_date,
                       hourly_count=excluded.hourly_count,
                       hourly_bucket=excluded.hourly_bucket,
                       last_message=excluded.last_message,
                       updated_at=excluded.updated_at""",
                (
                    state.scope_key,
                    state.last_sent_at,
                    state.last_candidate_at,
                    state.unanswered_count,
                    state.daily_count,
                    state.daily_date,
                    state.hourly_count,
                    state.hourly_bucket,
                    state.last_message,
                    state.updated_at,
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to persist initiative state for %s", state.scope_key)

    # ------------------------------------------------------------ candidates

    async def build_candidates(
        self,
        *,
        scope_key: str,
        user_id: str,
        last_seen: int | None,
        relationship_stage: str,
        world_moment: str = "",
        is_core: bool = False,
    ) -> list[InitiativeCandidate]:
        """Candidate topics for one user, strongest first (spec v0.8 §23/§27)."""
        candidates: list[InitiativeCandidate] = []
        if world_moment:
            # v0.8: something happened in her own life — a *reason*, never a
            # reason by itself. The same hard gate below still applies (v0.8 §42).
            candidates.append(
                InitiativeCandidate(
                    scope_key=scope_key,
                    user_id=user_id,
                    reason="life_event",
                    topic=world_moment,
                    priority=0.7,
                )
            )
        if self.topics is not None:
            for topic in await self.topics.get_active_topics(scope_key, limit=5):
                candidates.append(
                    InitiativeCandidate(
                        scope_key=scope_key,
                        user_id=user_id,
                        reason="unfinished_topic",
                        topic=topic.title,
                        priority=0.6 + topic.importance * 0.4,
                    )
                )
        now = self._clock()
        if last_seen and (now - last_seen) >= self.config_for(is_core=is_core).idle_hours * 3600:
            candidates.append(
                InitiativeCandidate(
                    scope_key=scope_key,
                    user_id=user_id,
                    reason="long_absence",
                    priority=0.4 + 0.1 * STAGES.index(relationship_stage)
                    if relationship_stage in STAGES
                    else 0.4,
                )
            )
        candidates.sort(key=lambda c: c.priority, reverse=True)
        return candidates

    # ----------------------------------------------------------------- gate

    async def evaluate(
        self,
        candidate: InitiativeCandidate,
        *,
        relationship_stage: str,
        user_enabled: bool = True,
        last_seen: int | None = None,
        is_core: bool = False,
    ) -> GateResult:
        """All hard rules first; probability is only the last hurdle.

        ``is_core`` selects the rule set: core friends use
        :class:`BehaviorInitiativeCoreConfig` with its own switch, interval,
        budgets, idle window and probability — none shared with the general
        rules. The per-person counters (``initiative_state``) stay per scope,
        so one person's sends never consume another's quota.
        """
        cfg = self.config_for(is_core=is_core)
        if not (cfg.enabled and self._db is not None):
            return GateResult(False, "disabled")

        block = self.presence.hard_block_reason(for_initiative=True)
        if block:
            return GateResult(False, block)

        if not user_enabled:
            return GateResult(False, "user_disabled")

        min_stage = getattr(cfg, "min_relationship_stage", "")
        if min_stage and STAGES.index(relationship_stage) < STAGES.index(min_stage):
            return GateResult(False, "relationship_too_new")

        state = await self.load_state(candidate.scope_key)
        now = int(self._clock())
        self._roll_clock_buckets(state, now)

        if state.unanswered_count >= cfg.max_unanswered:
            return GateResult(False, "awaiting_reply")

        if state.last_sent_at and now - state.last_sent_at < cfg.min_interval_minutes * 60:
            return GateResult(False, "cooldown")

        if cfg.hourly_limit and state.hourly_count >= cfg.hourly_limit:
            return GateResult(False, "hourly_limit")
        if cfg.daily_limit and state.daily_count >= cfg.daily_limit:
            return GateResult(False, "daily_limit")

        if not candidate.reason:
            return GateResult(False, "no_reason")

        if self._is_duplicate(candidate, state, is_core=is_core):
            return GateResult(False, "duplicate")

        probability = cfg.base_probability
        if candidate.reason == "unfinished_topic":
            probability += cfg.topic_bonus
        if candidate.reason == "long_absence":
            probability += cfg.relationship_bonus
        if relationship_stage in ("close", "very_close", CORE_STAGE):
            probability += cfg.relationship_bonus
        probability = max(0.0, min(1.0, probability))

        roll = self._rng.random()
        detail = {
            "probability": round(probability, 3),
            "roll": round(roll, 3),
            "reason": candidate.reason,
            "topic": candidate.topic,
        }
        if roll >= probability:
            return GateResult(False, "low_probability", probability, detail)
        return GateResult(True, candidate.reason, probability, detail)

    def _roll_clock_buckets(self, state: InitiativeState, now: int) -> None:
        """Reset daily/hourly counters when their window changed (spec v0.8 §49)."""
        today = time.strftime(DATE_FORMAT, time.localtime(now))
        bucket = time.strftime(BUCKET_FORMAT, time.localtime(now))
        if state.daily_date != today:
            state.daily_date = today
            state.daily_count = 0
        if state.hourly_bucket != bucket:
            state.hourly_bucket = bucket
            state.hourly_count = 0

    def _is_duplicate(
        self, candidate: InitiativeCandidate, state: InitiativeState, *, is_core: bool = False
    ) -> bool:
        """Reject near-identical repeats of the last proactive message (v0.8 §57)."""
        if not state.last_message:
            return False
        wanted = bigrams(candidate.topic or candidate.prompt_hint)
        if not wanted:
            return False
        overlap = len(wanted & bigrams(state.last_message)) / max(1, len(wanted))
        return overlap >= self.config_for(is_core=is_core).duplicate_similarity

    # -------------------------------------------------------------- exec

    async def record_sent(
        self,
        scope_key: str,
        message: str,
        *,
        reason: str,
        user_id: str | None = None,
        topic: str = "",
    ) -> InitiativeState:
        state = await self.load_state(scope_key)
        now = int(self._clock())
        self._roll_clock_buckets(state, now)
        state.last_sent_at = now
        state.last_message = message
        state.daily_count += 1
        state.hourly_count += 1
        state.unanswered_count += 1
        await self.save_state(state)
        await self.log_event(
            BehaviorEvent(
                type="initiative_sent",
                scope_key=scope_key,
                user_id=user_id,
                reason=reason,
                detail=topic or message,
                status="done",
                created_at=now,
                executed_at=now,
            )
        )
        return state

    async def mark_candidate(self, scope_key: str, reason: str) -> None:
        state = await self.load_state(scope_key)
        state.last_candidate_at = int(self._clock())
        await self.save_state(state)
        await self.log_event(
            BehaviorEvent(
                type="initiative_candidate",
                scope_key=scope_key,
                reason=reason,
                status="candidate",
                created_at=int(self._clock()),
            )
        )

    async def mark_skipped(
        self, scope_key: str, reason: str, detail: dict[str, Any] | None = None
    ) -> None:
        await self.log_event(
            BehaviorEvent(
                type="initiative_skipped",
                scope_key=scope_key,
                reason=reason,
                detail=str(detail or ""),
                status="skipped",
                created_at=int(self._clock()),
            )
        )

    async def note_user_activity(self, scope_key: str) -> None:
        """The user spoke: clear the awaiting-reply counter (spec v0.8 §58/§59)."""
        if not self.enabled:
            return
        state = await self.load_state(scope_key)
        if state.unanswered_count:
            state.unanswered_count = 0
            await self.save_state(state)

    # ---------------------------------------------------------------- audit

    async def log_event(self, event: BehaviorEvent) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO behavior_events
                       (type, scope_key, user_id, group_id, reason, detail, status,
                        created_at, executed_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.type,
                    event.scope_key,
                    event.user_id,
                    event.group_id,
                    event.reason,
                    event.detail[:300],
                    event.status,
                    event.created_at or int(self._clock()),
                    event.executed_at,
                ),
            )
        except Exception:  # noqa: BLE001 - auditing must never break behaviour
            self._log.exception("Failed to record behaviour event %s", event.type)

    async def recent_events(self, limit: int = 50, event_type: str = "") -> list[dict[str, Any]]:
        if self._db is None:
            return []
        sql = "SELECT * FROM behavior_events WHERE 1=1"
        params: list[Any] = []
        if event_type:
            sql += " AND type = ?"
            params.append(event_type)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))
        return [dict(row) for row in rows]

    async def snapshot(self) -> dict[str, Any]:
        """Dashboard view of the initiative subsystem (spec v0.8 §52)."""
        if self._db is None:
            return {"enabled": False}
        rows = await self._db.fetchall(
            "SELECT * FROM initiative_state ORDER BY updated_at DESC LIMIT 20"
        )
        today = time.strftime(DATE_FORMAT, time.localtime(self._clock()))
        total_today = sum(row["daily_count"] for row in rows if row["daily_date"] == today)
        sent = await self._db.fetchall(
            "SELECT created_at FROM behavior_events"
            " WHERE type = 'initiative_sent' ORDER BY id DESC LIMIT 1"
        )
        return {
            "enabled": self.config.enabled,
            "daily_limit": self.config.daily_limit,
            "hourly_limit": self.config.hourly_limit,
            "min_interval_minutes": self.config.min_interval_minutes,
            "max_unanswered": self.config.max_unanswered,
            "idle_hours": self.config.idle_hours,
            "core_friend": {
                "enabled": self.config.core_friend.enabled,
                "daily_limit": self.config.core_friend.daily_limit,
                "hourly_limit": self.config.core_friend.hourly_limit,
                "min_interval_minutes": self.config.core_friend.min_interval_minutes,
                "max_unanswered": self.config.core_friend.max_unanswered,
                "idle_hours": self.config.core_friend.idle_hours,
            },
            "sent_today_total": total_today,
            "last_sent_at": sent[0]["created_at"] if sent else None,
            "scopes": rows,
        }
