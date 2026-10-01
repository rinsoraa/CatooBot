"""Social Cognition Engine (v0.9 §6/§35/§150).

The orchestrator that turns "should the character speak in this group?" into a
structured :class:`ParticipationDecision`. Pipeline:

    hard priority (@ / reply-to-bot)
    -> hard gate (policy)
    -> active-thread continuation
    -> 5-message observer
    -> final gate (re-checked by the caller right before send)

It reuses the existing Behavior / Topic / Memory / State / World subsystems —
there is no second engine here (v0.9 §7), and no ``random() < probability`` anywhere
in the decision path (v0.9 §93).
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import TYPE_CHECKING, Any

from app.social.attention import SocialAttention
from app.social.context import SocialContextBuilder
from app.social.continuation import ContinuationDetector
from app.social.engagement import SocialEngagement
from app.social.models import ParticipationDecision, RelevanceScores, SocialObservation
from app.social.monitor import GroupConversationMonitor
from app.social.observer import GroupObserver
from app.social.policy import ParticipationPolicy
from app.social.relevance import RelevanceEvaluator
from app.social.thread import ThreadManager

if TYPE_CHECKING:
    from app.config.settings import SocialConfig
    from app.database.database import Database


class SocialCognitionEngine:
    def __init__(
        self,
        *,
        config: SocialConfig,
        bot: Any,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self.bot = bot
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock

        self.monitor = GroupConversationMonitor(
            max_messages=config.group_context.max_messages, logger=self._log
        )
        self.threads = ThreadManager(config=config.continuation, logger=self._log, clock=clock)
        self.attention = SocialAttention(logger=self._log, clock=clock)
        self.engagement = SocialEngagement(clock=clock, logger=self._log)
        self.policy = ParticipationPolicy(
            config=config, logger=self._log, clock=clock, engagement=self.engagement
        )
        self.context = SocialContextBuilder(logger=self._log)

        embeddings = getattr(getattr(bot, "memory", None), "embeddings", None)
        self.relevance = RelevanceEvaluator(embeddings=embeddings, logger=self._log)
        self.continuation = ContinuationDetector(
            embeddings=embeddings,
            follow_up_threshold=config.thresholds.follow_up,
            window_minutes=config.continuation.window_minutes,
            logger=self._log,
            clock=clock,
        )
        self.observer = GroupObserver(
            config=config,
            engine=getattr(bot, "ai", None),
            relevance=self.relevance,
            logger=self._log,
        )
        self._locks: dict[str, asyncio.Lock] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    # ---------------------------------------------------------------- decide

    async def observe_only(
        self,
        *,
        group_id: str,
        message_id: str,
        user_id: str,
        nickname: str,
        text: str,
        reply_to_bot: bool,
    ) -> None:
        """v1.2: record one raw group message WITHOUT deciding (v0.9 §82/§83).

        The Conversation Runtime feeds bursts here so the monitor keeps a
        faithful transcript; the participation decision happens once per turn
        via :meth:`decide` with ``record=False``.
        """
        if not self.enabled:
            return
        group_id = str(group_id)
        self.monitor.record(
            group_id,
            message_id,
            user_id,
            nickname,
            text,
            timestamp=self._clock(),
            reply_to=str(message_id) if reply_to_bot else None,
        )

    async def decide_for_turn(
        self,
        *,
        group_id: str,
        user_id: str,
        nickname: str,
        text: str,
        mentioned: bool,
        reply_to_bot: bool,
        group_enabled: bool,
    ) -> ParticipationDecision:
        """v1.2 adapter: run the social decision on a merged turn burst.

        The raw messages were already observed via :meth:`observe_only`, so
        this records nothing (spec v0.9 §83: one burst = one decision).
        """
        return await self.decide(
            group_id=group_id,
            message_id=f"turn_{int(self._clock())}",
            user_id=user_id,
            nickname=nickname,
            text=text,
            mentioned=mentioned,
            reply_to_bot=reply_to_bot,
            group_enabled=group_enabled,
            record=False,
        )

    async def decide(
        self,
        *,
        group_id: str,
        message_id: str,
        user_id: str,
        nickname: str,
        text: str,
        mentioned: bool,
        reply_to_bot: bool,
        group_enabled: bool,
        record: bool = True,
    ) -> ParticipationDecision:
        if not self.enabled:
            return ParticipationDecision(decision="ignore", reason_code="social_disabled")

        group_id = str(group_id)
        now = self._clock()
        if record:
            self.monitor.record(
                group_id,
                message_id,
                user_id,
                nickname,
                text,
                timestamp=now,
                reply_to=str(message_id) if reply_to_bot else None,
            )

        # ---- hard priority: direct address / reply-to-bot (spec v0.9 §20/§21) ----
        if mentioned:
            self.attention.note_mention(group_id, topic="")
            return ParticipationDecision(
                decision="reply",
                reason_code="direct_mention",
                confidence=1.0,
                target_message_ids=[str(message_id)],
            )
        if reply_to_bot:
            self.attention.note_mention(group_id, topic="")
            return ParticipationDecision(
                decision="reply",
                reason_code="reply_to_bot",
                confidence=1.0,
                target_message_ids=[str(message_id)],
            )

        # ---- active thread: immediate follow-up detection (spec v0.9 §47) ----
        # A follow-up is a *continuation* of a live exchange, so it outranks
        # the participation cooldown/daily budget (spec v0.9 §68: direct_follow_up
        # comes before cooldown_block). It still respects the group switch.
        thread = self.threads.get(group_id)
        if thread is not None:
            self.threads.note_user_message(
                group_id, user_id=user_id, message=text, message_id=str(message_id)
            )
            message = self.monitor.last_external_message(group_id)
            if message is not None:
                cont = await self.continuation.detect(message=message, thread=thread)
                if cont.is_follow_up:
                    if not group_enabled:
                        return ParticipationDecision(
                            decision="ignore", reason_code="group_disabled"
                        )
                    return ParticipationDecision(
                        decision="reply",
                        reason_code=cont.reason_code,
                        confidence=cont.confidence,
                        topic=thread.topic,
                        target_message_ids=[cont.target_message_id],
                    )

        # ---- hard gate (cooldown / daily / group / presence) ----
        hard_block = self._presence_block()
        block = self.policy.block_reason(
            group_id,
            group_enabled=group_enabled,
            text=text,
            min_length=getattr(self.bot.config.behavior.group, "min_message_length", 3),
            addressed_other=self._addressed_other(),
            hard_block=hard_block,
        )
        if block:
            return ParticipationDecision(decision="ignore", reason_code=block)

        # ---- the operator's non-@ switch (live) ----
        group_cfg = getattr(self.bot.config.behavior, "group", None)
        if group_cfg is not None and not getattr(group_cfg, "participation_enabled", True):
            return ParticipationDecision(decision="ignore", reason_code="participation_disabled")

        # ---- 5-message observer (spec v0.9 §24/§25) ----
        unobserved = self.monitor.unobserved(group_id)
        if len(unobserved) < self.config.observer.batch_size:
            fallback = self._participation_rate(
                group_id, target_ids=[m.message_id for m in unobserved]
            )
            if fallback is not None:
                return fallback
            return ParticipationDecision(decision="observe", reason_code="no_relevance")

        async with self._lock(group_id):
            batch = self.monitor.unobserved(group_id)
            if len(batch) < self.config.observer.batch_size:
                fallback = self._participation_rate(
                    group_id, target_ids=[m.message_id for m in batch]
                )
                if fallback is not None:
                    return fallback
                return ParticipationDecision(decision="observe", reason_code="no_relevance")
            decision = await self._observe(group_id, batch)
            self.monitor.mark_observed(group_id)
            # The structured judgment had "nothing to say": the operator's
            # configured participation rate decides whether she still chimes in.
            if decision.decision in ("observe", "ignore"):
                fallback = self._participation_rate(
                    group_id, target_ids=[m.message_id for m in batch]
                )
                if fallback is not None:
                    return fallback
            return decision

    def _participation_rate(
        self, group_id: str, *, target_ids: list[str]
    ) -> ParticipationDecision | None:
        """Deterministic rate fallback: credit accumulates toward one chime-in."""
        cfg = getattr(self.bot.config.behavior, "group", None)
        if cfg is None:
            return None
        rate = float(getattr(cfg, "participation_probability", 0.0) or 0.0)
        if rate <= 0.0:
            return None
        if not self.policy.credit_participation(str(group_id), rate):
            return None
        return ParticipationDecision(
            decision="reply",
            reason_code="participation_rate",
            confidence=min(1.0, rate),
            target_message_ids=list(target_ids),
        )

    # --------------------------------------------------------------- observe

    async def _observe(self, group_id: str, batch: list[Any]) -> ParticipationDecision:
        topic = await self._active_topic(group_id)
        character_block = await self._character_block(group_id, batch)
        recent = self.monitor.recent(group_id, limit=self.config.observer.min_context_messages)
        decision = await self.observer.observe(
            group_id=group_id,
            batch=batch,
            recent_context=recent,
            character_block=character_block,
            topic=topic,
        )
        await self._record_observation(group_id, batch, decision, topic)
        if decision.should_reply:
            self.attention.note_interesting_topic(group_id, decision.topic or topic)
        return decision

    # ------------------------------------------------------------ after reply

    async def after_reply(
        self,
        *,
        group_id: str,
        message_id: str,
        content: str,
        topic: str = "",
        participants: list[str] | None = None,
    ) -> None:
        """The plugin calls this after the character actually sent a message."""
        self.monitor.record_bot(str(group_id), message_id, content, self._clock())
        self.threads.open(
            str(group_id),
            topic=topic,
            bot_message=content,
            bot_message_id=str(message_id),
            participants=participants,
        )
        self.policy.record_send(str(group_id))
        self.attention.note_reply(str(group_id))
        self._log.info("[Social] replied in group=%s (%.40s)", group_id, content)

    async def note_group_participation(self, group_id: str, *, is_reply: bool = True) -> None:
        """Legacy hook: count a reply without the message plumbing (tests)."""
        if is_reply:
            self.policy.record_send(str(group_id))
            self.attention.note_reply(str(group_id))

    # ------------------------------------------------------------- internals

    def _lock(self, group_id: str) -> asyncio.Lock:
        lock = self._locks.get(group_id)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[group_id] = lock
        return lock

    def _presence_block(self) -> str | None:
        presence = getattr(self.bot, "presence", None)
        if presence is None:
            return None
        try:
            # This is a *reply* decision, not a proactive message — sleep must
            # not block it (she answers, just sleepily); only DND, when
            # configured to block replies, does.
            return presence.hard_block_reason(for_initiative=False)
        except Exception:  # noqa: BLE001 - presence is advisory
            return None

    def _addressed_other(self) -> bool:
        """Can't be determined from the flattened text; the plugin pre-checks
        @-others via the event and passes ``mentioned``/``reply_to_bot``. Kept
        as a safe False default."""
        return False

    async def _active_topic(self, group_id: str) -> str:
        topics = getattr(self.bot.behavior, "topics", None)
        if topics is None:
            return ""
        try:
            active = await topics.get_active_topics(f"group:{group_id}", limit=3)
            return active[0].title if active else ""
        except Exception:  # noqa: BLE001 - topics are optional
            return ""

    async def _character_block(self, group_id: str, batch: list[Any]) -> str:
        bot = self.bot
        persona = getattr(bot.character.personas, "persona", None)
        state = await bot.character.states.load()
        topics = []
        try:
            manager = getattr(bot.behavior, "topics", None)
            if manager is not None:
                topics = [
                    t.title for t in await manager.get_active_topics(f"group:{group_id}", limit=5)
                ]
        except Exception:  # noqa: BLE001
            topics = []
        memories = await self._relevant_memories(group_id, batch)
        relationships = await self._speaker_stages(batch)

        # v2.0: the sandbox is her life — expose its current-world slice.
        sandbox = getattr(bot, "sandbox", None)
        activity_context: dict | None = None
        if sandbox is not None and getattr(sandbox, "enabled", False):
            try:
                activity_context = {
                    "primary_activity": (
                        sandbox.current_action.definition_id if sandbox.current_action else None
                    ),
                    "activity_detail": (
                        sandbox.current_action.detail if sandbox.current_action else ""
                    ),
                    "location": sandbox.spaces.name(sandbox.character.location),
                    "context_line": sandbox.context().get("state_line", ""),
                }
            except Exception:  # noqa: BLE001 - context is best-effort
                activity_context = None

        return self.context.character_block(
            persona=persona,
            state=state,
            topics=topics,
            memories=memories,
            relationships=relationships,
            activity_context=activity_context,
        )

    async def _relevant_memories(self, group_id: str, batch: list[Any]) -> list[str]:
        memory = getattr(self.bot, "memory", None)
        if memory is None:
            return []
        query = " ".join(m.content for m in batch)[:200]
        try:
            rows = await memory.retrieve_for_session(f"group:{group_id}", query)
        except Exception:  # noqa: BLE001
            return []
        return [m.content for m in rows[:5]]

    async def _speaker_stages(self, batch: list[Any]) -> dict[str, str]:
        relationships = getattr(self.bot, "relationships", None)
        if relationships is None:
            return {}
        result: dict[str, str] = {}
        for message in batch:
            if message.external and str(message.user_id) not in result:
                try:
                    rel = await relationships.get(message.user_id)
                    result[str(message.user_id)] = rel.stage
                except Exception:  # noqa: BLE001
                    continue
        return result

    async def _record_observation(
        self, group_id: str, batch: list[Any], decision: ParticipationDecision, topic: str
    ) -> None:
        if self._db is None:
            return
        first = batch[0].message_id if batch else ""
        last = batch[-1].message_id if batch else ""
        observation = SocialObservation(
            observation_id=f"obs_{uuid.uuid4().hex[:10]}",
            group_id=str(group_id),
            message_range=f"{first}..{last}",
            topic=topic,
            decision=decision.decision,
            reason_code=decision.reason_code,
            confidence=round(decision.confidence, 3),
            created_at=int(self._clock()),
            detail={
                "scores": decision.scores.as_dict(),
                "response_goal": decision.response_goal,
            },
        )
        import json

        try:
            await self._db.execute(
                """INSERT INTO social_observations
                   (observation_id, group_id, message_range, topic, decision,
                    reason_code, confidence, created_at, detail)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    observation.observation_id,
                    observation.group_id,
                    observation.message_range,
                    observation.topic,
                    observation.decision,
                    observation.reason_code,
                    observation.confidence,
                    observation.created_at,
                    json.dumps(observation.detail, ensure_ascii=False),
                ),
            )
        except Exception:  # noqa: BLE001 - observation is best-effort
            self._log.exception("[Social] failed to record observation")

    # ---------------------------------------------------------------- views

    def group_snapshot(self, group_id: str) -> dict[str, Any]:
        thread = self.threads.snapshot(str(group_id))
        return {
            "monitor": self.monitor.snapshot(str(group_id)),
            "thread": thread,
            "attention": self.attention.snapshot(str(group_id)),
            "policy": self.policy.snapshot(str(group_id)),
        }

    def groups(self) -> list[str]:
        return sorted(set(self.monitor.groups()) | set(self.attention.groups()))

    def empty_decision(self, reason: str = "no_relevance") -> ParticipationDecision:
        return ParticipationDecision(
            decision="ignore", reason_code=reason, scores=RelevanceScores()
        )
