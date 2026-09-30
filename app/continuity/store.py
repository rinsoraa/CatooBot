"""Continuity persistence: dedicated tables + JSON state, TTL decay on load.

Migration 12 adds ``open_loops`` / ``shared_experiences`` /
``interaction_profiles`` / ``micro_events`` / ``affective_events`` /
``conversation_turns``. The assembled :class:`CharacterContinuityState` rides
the existing settings JSON table (spec §108: reuse equivalent tables).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from typing import Any

from app.continuity.models import (
    AffectiveContext,
    CharacterContinuityState,
    InteractionPattern,
    InteractionProfile,
    MicroEvent,
    OpenLoop,
    OpenLoopStatus,
    SharedExperience,
)

_STATE_KEY = "character_continuity"


class ContinuityStore:
    def __init__(self, database: Any, config: Any, logger: logging.Logger | None = None,
                 clock: Any = time.time) -> None:
        self._db = database
        self._config = config
        self._log = logger or logging.getLogger("CatooBot.Continuity")
        self._clock = clock

    # ------------------------------------------------------- assembled state

    async def load_state(self) -> CharacterContinuityState:
        cfg = self._config
        now = float(self._clock())
        state = CharacterContinuityState()
        if self._db is None:
            return state
        try:
            row = await self._db.fetchone(
                "SELECT value FROM settings WHERE key = ?", (_STATE_KEY,)
            )
        except Exception:  # noqa: BLE001
            return state
        if row is not None and row["value"]:
            try:
                state = CharacterContinuityState.model_validate(json.loads(row["value"]))
            except (TypeError, ValueError):
                self._log.warning("[Continuity] stored state invalid; starting fresh")

        # TTL decay on read (§105/§106) — stale fields fall back to empty.
        ttl = {
            ("current_interest", "interest_updated_at"): cfg.current_interest_ttl_hours * 3600,
            ("current_focus", "focus_updated_at"): cfg.current_interest_ttl_hours * 3600,
            ("unfinished_thought", "thought_updated_at"): cfg.unfinished_thought_ttl_hours * 3600,
            ("recent_emotion", "emotion_updated_at"): cfg.recent_emotion_ttl_minutes * 60,
        }
        updates: dict[str, Any] = {}
        for field_name, stamp_name in ttl:
            stamp = getattr(state, stamp_name)
            if getattr(state, field_name) and stamp and now - stamp > ttl[(field_name, stamp_name)]:
                updates[field_name] = ""
        if updates:
            state = state.model_copy(update=updates)
        state.affect.decay(now)
        state.recent_events = state.recent_events[-cfg.max_recent_events:]
        return state

    async def save_state(self, state: CharacterContinuityState) -> None:
        if self._db is None:
            return
        state.updated_at = float(self._clock())
        try:
            await self._db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (
                    _STATE_KEY,
                    json.dumps(state.model_dump(mode="json"), ensure_ascii=False),
                    int(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to save state")

    # ------------------------------------------------------------ open loops

    async def save_open_loop(self, loop: OpenLoop) -> None:
        if self._db is None:
            return
        now = int(self._clock())
        loop.updated_at = now
        if not loop.id:
            loop.id = f"loop_{uuid.uuid4().hex[:10]}"
            loop.created_at = now
        try:
            await self._db.execute(
                """INSERT INTO open_loops (id, type, summary, detail, status, scope_key,
                       progress, source, confidence, created_at, updated_at, expires_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET type=excluded.type, summary=excluded.summary,
                       detail=excluded.detail, status=excluded.status, progress=excluded.progress,
                       confidence=excluded.confidence, updated_at=excluded.updated_at,
                       expires_at=excluded.expires_at""",
                (
                    loop.id, loop.type.value, loop.summary, loop.detail, loop.status.value,
                    loop.scope_key, loop.progress, loop.source, loop.confidence,
                    int(loop.created_at), int(loop.updated_at),
                    int(loop.expires_at) if loop.expires_at else None,
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to save open loop")

    async def open_loops(self, *, include_closed: bool = False) -> list[OpenLoop]:
        if self._db is None:
            return []
        now = int(self._clock())
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM open_loops ORDER BY updated_at DESC LIMIT 100"
            )
        except Exception:  # noqa: BLE001
            return []
        loops: list[OpenLoop] = []
        for row in rows:
            try:
                loop = OpenLoop(
                    id=row["id"], type=row["type"], summary=row["summary"],
                    detail=row["detail"] or "", status=row["status"],
                    scope_key=row["scope_key"] or "", progress=row["progress"],
                    source=row["source"] or "", confidence=row["confidence"],
                    created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
                    expires_at=float(row["expires_at"]) if row["expires_at"] else None,
                )
            except (TypeError, ValueError):
                continue
            if loop.status == OpenLoopStatus.open and loop.expires_at and loop.expires_at < now:
                loop.status = OpenLoopStatus.expired
                await self.save_open_loop(loop)
            if include_closed or loop.status == OpenLoopStatus.open:
                loops.append(loop)
        return loops

    # ----------------------------------------------------- shared experiences

    async def save_shared_experience(self, exp: SharedExperience) -> None:
        if self._db is None:
            return
        now = int(self._clock())
        exp.updated_at = now
        if not exp.id:
            exp.id = f"shx_{uuid.uuid4().hex[:10]}"
            exp.created_at = now
        try:
            await self._db.execute(
                """INSERT INTO shared_experiences (id, user_id, type, summary, detail,
                       keywords, times_referenced, confidence, source, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET summary=excluded.summary,
                       detail=excluded.detail, keywords=excluded.keywords,
                       times_referenced=excluded.times_referenced,
                       confidence=excluded.confidence, updated_at=excluded.updated_at""",
                (
                    exp.id, exp.user_id, exp.type.value, exp.summary, exp.detail,
                    json.dumps(exp.keywords, ensure_ascii=False), exp.times_referenced,
                    exp.confidence, exp.source, int(exp.created_at), int(exp.updated_at),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to save shared experience")

    async def shared_experiences(self, user_id: str, *, limit: int = 50) -> list[SharedExperience]:
        if self._db is None:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM shared_experiences WHERE user_id = ?"
                " ORDER BY updated_at DESC LIMIT ?",
                (user_id, limit),
            )
        except Exception:  # noqa: BLE001
            return []
        out: list[SharedExperience] = []
        for row in rows:
            try:
                out.append(
                    SharedExperience(
                        id=row["id"], user_id=row["user_id"], type=row["type"],
                        summary=row["summary"], detail=row["detail"] or "",
                        keywords=json.loads(row["keywords"] or "[]"),
                        times_referenced=row["times_referenced"],
                        confidence=row["confidence"], source=row["source"] or "",
                        created_at=float(row["created_at"]), updated_at=float(row["updated_at"]),
                    )
                )
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        return out

    # ---------------------------------------------------- interaction profile

    async def load_profile(self, user_id: str) -> InteractionProfile:
        if self._db is None:
            return InteractionProfile(user_id=user_id)
        try:
            row = await self._db.fetchone(
                "SELECT patterns, favorite_topics, updated_at FROM interaction_profiles"
                " WHERE user_id = ?",
                (user_id,),
            )
        except Exception:  # noqa: BLE001
            return InteractionProfile(user_id=user_id)
        if row is None:
            return InteractionProfile(user_id=user_id)
        try:
            profile = InteractionProfile(
                user_id=user_id,
                patterns={
                    key: InteractionPattern(**value)
                    for key, value in (json.loads(row["patterns"] or "{}")).items()
                },
                favorite_topics=json.loads(row["favorite_topics"] or "[]"),
                updated_at=float(row["updated_at"] or 0),
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return InteractionProfile(user_id=user_id)
        profile.decay(float(self._clock()))
        return profile

    async def save_profile(self, profile: InteractionProfile) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO interaction_profiles (user_id, patterns, favorite_topics, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(user_id) DO UPDATE SET patterns=excluded.patterns,"
                " favorite_topics=excluded.favorite_topics, updated_at=excluded.updated_at",
                (
                    profile.user_id,
                    json.dumps({k: v.model_dump() for k, v in profile.patterns.items()}),
                    json.dumps(profile.favorite_topics, ensure_ascii=False),
                    int(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to save profile")

    # ------------------------------------------------------------ micro events

    async def add_micro_event(self, event: MicroEvent) -> None:
        if self._db is None:
            return
        if not event.id:
            event.id = f"mic_{uuid.uuid4().hex[:10]}"
        if not event.created_at:
            event.created_at = float(self._clock())
        try:
            await self._db.execute(
                "INSERT INTO micro_events (id, summary, kind, related_activity,"
                " reason_code, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    event.id, event.summary, event.kind, event.related_activity,
                    event.reason_code, int(event.created_at),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to add micro event")

    async def recent_micro_events(self, *, limit: int = 5) -> list[MicroEvent]:
        if self._db is None:
            return []
        cutoff = int(self._clock()) - self._config.micro_event_ttl_minutes * 60
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM micro_events WHERE created_at >= ?"
                " ORDER BY created_at DESC LIMIT ?",
                (cutoff, limit),
            )
        except Exception:  # noqa: BLE001
            return []
        return [
            MicroEvent(
                id=row["id"], summary=row["summary"], kind=row["kind"],
                related_activity=row["related_activity"] or "",
                reason_code=row["reason_code"] or "", created_at=float(row["created_at"]),
            )
            for row in rows
        ]

    # --------------------------------------------------------- affective audit

    async def log_affective_event(self, dimension: str, delta: float, reason: str) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                "INSERT INTO affective_events (dimension, delta, reason_code, created_at)"
                " VALUES (?, ?, ?, ?)",
                (dimension, delta, reason, int(self._clock())),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to log affective event")

    # ------------------------------------------------------------------- turns

    async def save_turn(self, turn: Any) -> None:
        if self._db is None:
            return
        try:
            await self._db.execute(
                """INSERT INTO conversation_turns (turn_id, generation_id, session_id, user_id,
                       group_id, classification, status, text, silence_reason, message_ids,
                       started_at, ended_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(turn_id) DO UPDATE SET status=excluded.status,
                       ended_at=excluded.ended_at, silence_reason=excluded.silence_reason""",
                (
                    turn.turn_id, turn.generation_id, turn.session_id, turn.user_id,
                    turn.group_id or "", turn.classification.value, turn.status.value,
                    turn.text[:2000], turn.silence_reason,
                    json.dumps([m.message_id for m in turn.messages]),
                    int(turn.started_at), int(turn.ended_at or self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[Continuity] failed to save turn")

    # ------------------------------------------------------------- shared state

    def affect(self, state: CharacterContinuityState) -> AffectiveContext:
        return state.affect
