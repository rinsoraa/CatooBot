"""CharacterBehaviorEngine: the decision layer between QQ messages and speech.

It answers three questions and nothing else (spec §3/§46):

* should the character take part in this message?  (:meth:`consider_*`)
* what is she currently doing / how does she feel?  (:meth:`tick`)
* which topic could she resume on her own?          (:mod:`app.behavior.initiative`)

Language generation stays with the AI; hard limits stay here in code.
"""

from __future__ import annotations

import logging
import random
import time
from typing import TYPE_CHECKING, Any

from app.behavior.initiative import InitiativeEngine
from app.behavior.models import BehaviorDecision, BehaviorEvent
from app.behavior.presence import PresenceResolver
from app.behavior.topics import TopicManager
from app.config.settings import BehaviorConfig

if TYPE_CHECKING:
    from app.character.relationship import RelationshipManager
    from app.character.state import StateManager
    from app.database.database import Database
    from app.memory.manager import MemoryManager

# Lexical mood signals (spec §15: changes need a source; random is only a nudge).
POSITIVE_SIGNALS = ("哈哈", "太好", "开心", "喜欢", "谢谢", "厉害", "好耶", "棒", "嘿嘿", "笑死")
NEGATIVE_SIGNALS = ("累", "烦", "难过", "生气", "讨厌", "崩溃", "不想", "emo", "哭", "难受")


class CharacterBehaviorEngine:
    def __init__(
        self,
        config: BehaviorConfig,
        presence: PresenceResolver,
        states: StateManager,
        relationships: RelationshipManager | None = None,
        database: Database | None = None,
        memory: MemoryManager | None = None,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.presence = presence
        self.states = states
        self.relationships = relationships
        self._log = logger or logging.getLogger("CatooBot.Behavior")
        self._rng = rng or random.Random()
        self._clock = clock
        self._group_recent: dict[str, float] = {}
        self.topics = TopicManager(database, logger=self._log, clock=clock)
        self.initiative = InitiativeEngine(
            config.initiative,
            presence,
            database=database,
            topics=self.topics,
            memory=memory,
            logger=self._log,
            rng=self._rng,
            clock=clock,
        )

    @property
    def enabled(self) -> bool:
        return self.config.enabled

    # ------------------------------------------------------------- awareness

    def time_context(self):
        return self.presence.time_context()

    async def current_state(self):
        return await self.states.load()

    # ------------------------------------------------------------- decisions

    async def consider_private(self, event, text: str) -> BehaviorDecision:
        """Private chat: always answer unless DND is configured to block it."""
        if not self.enabled:
            return BehaviorDecision(True, "behavior_disabled")
        block = self.presence.hard_block_reason(for_initiative=False)
        if block == "dnd":
            return BehaviorDecision(False, "dnd", detail={"text": text[:40]})
        return BehaviorDecision(True, "private_direct")

    async def consider_group(
        self,
        event,
        text: str,
        *,
        mentioned: bool,
        group_enabled: bool = True,
    ) -> BehaviorDecision:
        """Group chat: @ always (if configured), otherwise a participation gate."""
        cfg = self.config.group
        if not self.enabled:
            return BehaviorDecision(True, "behavior_disabled")
        if mentioned:
            if cfg.mention_always_replies:
                return BehaviorDecision(True, "mentioned")
            return BehaviorDecision(False, "mention_replies_disabled")

        if not cfg.participation_enabled:
            return BehaviorDecision(False, "participation_disabled")
        if not group_enabled:
            return BehaviorDecision(False, "group_disabled")
        block = self.presence.hard_block_reason(for_initiative=True)
        if block:
            return BehaviorDecision(False, block)
        if len(text.strip()) < cfg.min_message_length:
            return BehaviorDecision(False, "too_short")
        if cfg.ignore_when_other_mentioned and self._mentions_other(event):
            return BehaviorDecision(False, "addressed_to_someone_else")

        group_id = event.group_id
        scope = f"group:{group_id}"
        state = await self.initiative.load_state(scope)
        now = int(self._clock())
        self.initiative._roll_clock_buckets(state, now)  # noqa: SLF001 - shared counters

        if state.last_sent_at and now - state.last_sent_at < cfg.cooldown_seconds:
            return BehaviorDecision(False, "cooldown")
        if cfg.hourly_limit and state.hourly_count >= cfg.hourly_limit:
            return BehaviorDecision(False, "hourly_limit")

        probability = cfg.participation_probability
        relates = await self._relates_to_character(scope, text)
        if relates:
            probability = min(1.0, probability + cfg.topic_bonus)
        roll = self._rng.random()
        detail = {"probability": round(probability, 3), "roll": round(roll, 3), "related": relates}
        if roll >= probability:
            return BehaviorDecision(False, "low_probability", probability, detail)
        return BehaviorDecision(True, "participation", probability, detail)

    def _mentions_other(self, event) -> bool:
        self_id = getattr(event, "self_id", None)
        for segment in event.message:
            if segment.type != "at":
                continue
            target = segment.data.get("qq")
            if str(target) != str(self_id):
                return True
        return False

    async def _relates_to_character(self, scope_key: str, text: str) -> bool:
        """Loose relevance check: does the message touch a known topic/memory?"""
        from app.memory.retrieval import bigrams

        wanted = bigrams(text)
        if not wanted:
            return False
        for topic in await self.topics.get_active_topics(scope_key, limit=5):
            if len(wanted & bigrams(topic.title)) / max(1, len(wanted)) >= 0.34:
                return True
        return False

    async def note_group_participation(self, group_id: int | str) -> None:
        scope = f"group:{group_id}"
        state = await self.initiative.load_state(scope)
        now = int(self._clock())
        self.initiative._roll_clock_buckets(state, now)  # noqa: SLF001
        state.last_sent_at = now
        state.hourly_count += 1
        state.daily_count += 1
        await self.initiative.save_state(state)

    async def note_user_activity(self, session_id: str) -> None:
        await self.initiative.note_user_activity(session_id)

    # ------------------------------------------------------------ perception

    async def observe_conversation(self, text: str, reply: str) -> None:
        """Derive a *sourced* mood nudge from the conversation (spec §15)."""
        if not self.enabled:
            return
        user_side = text or ""
        if any(signal in user_side for signal in NEGATIVE_SIGNALS):
            await self.states.nudge_mood(-1, source="conversation")
        elif any(signal in user_side for signal in POSITIVE_SIGNALS):
            await self.states.nudge_mood(+1, source="conversation")
        # Replies themselves never move the mood — only what the user said.

    async def tick(self) -> None:
        """Periodic upkeep: mood/state decay (spec §48).

        Her *life* belongs to the v2.0 sandbox; this engine keeps the
        conversational layer (mood decay, presence, participation gates).
        """
        if not self.enabled:
            return
        await self.states.load()
        await self.states.update()  # persists decayed mood

    # --------------------------------------------------------------- reports

    async def snapshot(self) -> dict[str, Any]:
        state = await self.states.load()
        ctx = self.presence.time_context()
        return {
            "enabled": self.enabled,
            "state": state.model_dump(),
            "time": {
                "period": ctx.period,
                "local_time": ctx.local_time,
                "weekday": ctx.weekday,
                "is_sleeping": ctx.is_sleeping,
                "in_dnd": ctx.in_dnd,
                "description": ctx.describe(),
            },
            "group": self.config.group.model_dump(),
            "topics_active": await self.topics.count(),
            "initiative": await self.initiative.snapshot(),
        }

    async def log_event(self, event: BehaviorEvent) -> None:
        await self.initiative.log_event(event)
