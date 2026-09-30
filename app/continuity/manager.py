"""ContinuityManager: the single API surface for character continuity.

Everything that enters continuity passes a relevance gate first (§111/§112);
everything updates in the background after a reply (§110); everything decays
by its own TTL (§105). Only structured summaries with reason codes are kept
(§113) — never hidden chain-of-thought.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from typing import Any

from app.continuity.models import (
    AffectDimension,
    CharacterContinuityState,
    InteractionProfile,
    MicroEvent,
    OpenLoop,
    OpenLoopStatus,
    OpenLoopType,
    SharedExperience,
    SharedExperienceType,
)
from app.continuity.store import ContinuityStore

# Trivial exchanges never reach continuity (§111).
_TRIVIAL = re.compile(r"^(?:[哈呃嗯哦噢喔额]{1,6}|草+|笑死|确实|行|好[的吧]?|6+|……+|[?？]+)$")
# Cues that something is worth keeping as an unfinished thread / shared story.
_OPEN_LOOP_CUES = re.compile(r"还没|还没好|下次|打算|准备|想试|想做|想看|还没看完|还没建完|一直想")
_SHARED_CUES = re.compile(
    r"我们|一起|上次|那个问题|折腾|终于|搞定了|解决了|又遇到|老问题|那个梗|咱们"
)
_QUESTION_CUE = re.compile(r"[?？]|吗$|怎么|为什么|啥|什么|哪")
# Keywords: latin words whole + CJK character bigrams (fixed-window CJK
# "words" never overlap between two sentences; bigrams do).
_CJK = re.compile(r"[一-鿿]+")
_LATIN = re.compile(r"[A-Za-z]{2,}|\d{2,}")


def _keywords(text: str) -> list[str]:
    seen: list[str] = []
    for word in _LATIN.findall(text):
        low = word.lower()
        if low not in seen:
            seen.append(low)
    for chunk in _CJK.findall(text):
        if len(chunk) == 1:
            if chunk not in seen:
                seen.append(chunk)
            continue
        for index in range(len(chunk) - 1):
            bigram = chunk[index : index + 2]
            if bigram not in seen:
                seen.append(bigram)
    return seen[:16]


class ContinuityManager:
    def __init__(
        self,
        store: ContinuityStore,
        *,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self.store = store
        self._log = logger or logging.getLogger("CatooBot.Continuity")
        self._clock = clock
        # Usable without start(): fresh state until load_state() replaces it.
        self._state: CharacterContinuityState = CharacterContinuityState()
        self._profiles: dict[str, InteractionProfile] = {}

    # ------------------------------------------------------------------ load

    async def start(self) -> None:
        self._state = await self.store.load_state()

    @property
    def state(self) -> CharacterContinuityState:
        return self._state

    async def reload(self) -> None:
        self._state = await self.store.load_state()

    # ------------------------------------------------------- micro events (§98)

    async def add_micro_event(self, summary: str, *, kind: str = "ambient",
                              related_activity: str = "", reason_code: str = "") -> None:
        if _TRIVIAL.match(summary.strip()):
            return
        event = MicroEvent(
            summary=summary.strip()[:120], kind=kind,
            related_activity=related_activity, reason_code=reason_code or "world_tick",
            created_at=float(self._clock()),
        )
        await self.store.add_micro_event(event)
        self.state.recent_events.append(event.summary)
        capped = self.state.recent_events[-self.store._config.max_recent_events:]
        self.state.recent_events = capped
        await self.store.save_state(self.state)

    # -------------------------------------------------- incoming message gate (§112)

    async def observe_message(self, user_id: str, text: str, *, session_id: str) -> None:
        """Cheap gate after a user message; may create open loops / candidates.
        Runs inline but is SQL-only — no model calls, no blocking work."""
        if not text or _TRIVIAL.match(text.strip()):
            return
        now = float(self._clock())

        if _OPEN_LOOP_CUES.search(text) and len(text) >= 6:
            await self._ensure_open_loop(
                summary=text.strip()[:80],
                loop_type=(
                    OpenLoopType.conversation
                    if session_id.startswith("private:")
                    else OpenLoopType.plan
                ),
                scope_key=f"user:{user_id}" if user_id else "",
                source="user_mentioned_unfinished",
                confidence=0.55,
            )

        if _SHARED_CUES.search(text) and len(text) >= 8:
            await self._candidate_shared_experience(user_id, text, now)

        # interests: user talks about something → curiosity/interest bump (§47)
        if _QUESTION_CUE.search(text):
            self.state.affect.bump(
                AffectDimension.curiosity.value, 0.05, reason="user_question", now=now
            )

    # ------------------------------------------------------ after a reply (§50)

    def schedule_update_after_reply(self, turn: Any, decision: Any, reply: str) -> None:
        """Background continuity update — never blocks the reply (§110)."""

        async def update() -> None:
            try:
                await self._update_after_reply(turn, decision, reply)
            except Exception:  # noqa: BLE001
                self._log.exception("[Continuity] post-reply update failed")

        asyncio.create_task(update())

    async def _update_after_reply(self, turn: Any, decision: Any, reply: str) -> None:
        now = float(self._clock())
        state = self.state
        state.last_response_context = reply[:200]
        state.emotion_updated_at = now

        # Interaction profile learning (§114): statistics + decay, never labels.
        profile = await self._profile(turn.user_id)
        profile.observe("burst_length", min(1.0, len(turn.messages) / 4.0), now=now)
        profile.observe(
            "message_length", min(1.0, len(turn.text) / 60.0), now=now
        )
        if turn.classification.value in ("follow_up", "continuation"):
            profile.observe("follow_up_habit", 1.0, now=now)
        else:
            profile.observe("follow_up_habit", 0.0, now=now)
        hour = time.localtime(now).tm_hour
        profile.observe("late_night_habit", 1.0 if hour >= 23 or hour < 6 else 0.0, now=now)
        await self.store.save_profile(profile)

        # Topic of the turn may become her current interest (engagement-gated).
        if decision.engagement >= 0.55 and len(turn.text) >= 8:
            topic = turn.text.strip().split("\n")[0][:30]
            if not _TRIVIAL.match(topic):
                state.current_interest = topic
                state.interest_updated_at = now
        state.updated_at = now
        await self.store.save_state(state)

    # --------------------------------------------------------------- open loops

    async def _ensure_open_loop(
        self, summary: str, *, loop_type: OpenLoopType, scope_key: str,
        source: str, confidence: float,
    ) -> OpenLoop | None:
        existing = await self.store.open_loops()
        for loop in existing:
            if loop.summary[:24] == summary[:24] and loop.status == OpenLoopStatus.open:
                loop.confidence = min(1.0, loop.confidence + 0.05)
                await self.store.save_open_loop(loop)
                return loop
        if len(existing) >= self.store._config.max_open_loops:
            return None
        loop = OpenLoop(
            type=loop_type, summary=summary, scope_key=scope_key,
            source=source, confidence=confidence,
            expires_at=float(self._clock()) + self.store._config.open_loop_ttl_days * 86400,
        )
        await self.store.save_open_loop(loop)
        self._log.info("[Continuity] open loop created: %s (%s)", summary[:40], source)
        return loop

    async def advance_open_loop(self, loop_id: str, progress_delta: float) -> None:
        for loop in await self.store.open_loops(include_closed=True):
            if loop.id == loop_id and loop.status == OpenLoopStatus.open:
                loop.progress = max(0.0, min(1.0, loop.progress + progress_delta))
                if loop.progress >= 1.0:
                    loop.status = OpenLoopStatus.completed
                await self.store.save_open_loop(loop)
                return

    async def open_loop_context(self, user_id: str = "") -> list[OpenLoop]:
        loops = await self.store.open_loops()
        if not user_id:
            return [loop for loop in loops if not loop.scope_key]
        return [
            loop for loop in loops
            if not loop.scope_key or loop.scope_key == f"user:{user_id}"
        ]

    # ------------------------------------------------------ shared experiences

    async def _candidate_shared_experience(self, user_id: str, text: str, now: float) -> None:
        """First mention is a *candidate*; repeated overlap confirms it (§35)."""
        words = set(_keywords(text))
        candidates = await self.store.shared_experiences(user_id, limit=30)
        best: tuple[int, SharedExperience] | None = None
        for exp in candidates:
            overlap = len(words & set(exp.keywords))
            if overlap >= 2 and (best is None or overlap > best[0]):
                best = (overlap, exp)
        if best is not None:
            exp = best[1]
            exp.times_referenced += 1
            exp.confidence = min(1.0, exp.confidence + 0.15)
            await self.store.save_shared_experience(exp)
            return
        if len(text) < 12 or not words:
            return
        exp = SharedExperience(
            user_id=user_id,
            type=SharedExperienceType.shared_topic,
            summary=text.strip()[:120],
            keywords=list(_keywords(text)),
            confidence=0.45, source="conversation_cue",
        )
        await self.store.save_shared_experience(exp)

    async def recall_shared(
        self, user_id: str, text: str, *, limit: int = 2
    ) -> list[SharedExperience]:
        """Relevance-gated recall (§37): only topic/entity related entries."""
        experiences = await self.store.shared_experiences(user_id, limit=50)
        threshold = self.store._config.shared_experience_min_confidence
        scored: list[tuple[float, SharedExperience]] = []
        words = set(_keywords(text))
        for exp in experiences:
            if exp.confidence < threshold:
                continue
            overlap = len(words & set(exp.keywords))
            if overlap == 0:
                continue
            scored.append((overlap / max(1, len(words)) + exp.confidence * 0.3, exp))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [exp for _, exp in scored[:limit]]

    # ------------------------------------------------------------------ profile

    async def profile(self, user_id: str) -> InteractionProfile:
        return await self._profile(user_id)

    async def _profile(self, user_id: str) -> InteractionProfile:
        if user_id not in self._profiles:
            self._profiles[user_id] = await self.store.load_profile(user_id)
        return self._profiles[user_id]

    # --------------------------------------------------------- world hooks (§100)

    async def note_world_micro_event(
        self, summary: str, *, activity: str = "", interest_hint: str = ""
    ) -> None:
        await self.add_micro_event(
            summary, kind="world", related_activity=activity, reason_code="world_event"
        )
        if interest_hint:
            self.state.current_interest = interest_hint[:30]
            self.state.interest_updated_at = float(self._clock())
            await self.store.save_state(self.state)

    def new_id(self) -> str:
        return uuid.uuid4().hex[:10]
