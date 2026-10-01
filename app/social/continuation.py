"""Conversation continuation detector (v0.9 §14-§19/§47/§51).

Recognises the "user keeps talking to the character *without @*" case:

    Bot:   我在看剧。
    User:  看的什么剧？       <- no @, but a direct follow-up

The decision is **structured and rule-driven** (semantic similarity + dialogue
structure + time + speaker), never ``random() < confidence`` (§19). A confidence
above the threshold means *this is a follow-up*, not a lottery ticket.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.memory.vector_store import cosine_similarity
from app.social.models import ContinuationDecision, ConversationThread, GroupMessage
from app.social.relevance import lexical_similarity

#: Words that only make sense when continuing an already-opened exchange.
FOLLOW_UP_MARKERS = (
    "那",
    "呢",
    "什么",
    "哪个",
    "哪一",
    "哪部",
    "哪集",
    "为什么",
    "怎么",
    "谁",
    "哪里",
    "哪儿",
    "几",
    "多少",
    "好看吗",
    "好不好",
    "讲什么",
    "说什么",
    "然后",
    "所以",
    "那你",
    "那你呢",
    "这剧",
    "这部剧",
    "那部剧",
    "它",
)

#: Anaphora that points back to the bot's last message.
ANAPHORA = ("它", "这个", "那个", "这部", "那部", "这集", "那集", "这剧", "那剧", "你刚才", "你刚")

#: Explicit address cues (name/alias fallback lives in the plugin's @ detection).
EXPLICIT_ADDRESS = ("你在", "你在干嘛", "你觉得", "你最近", "你喜欢", "你会")


class ContinuationDetector:
    def __init__(
        self,
        *,
        embeddings: Any = None,
        follow_up_threshold: float = 0.75,
        window_minutes: int = 10,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._embeddings = embeddings
        self._threshold = follow_up_threshold
        self._window = max(1, window_minutes) * 60.0
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock

    async def detect(
        self,
        *,
        message: GroupMessage,
        thread: ConversationThread | None,
    ) -> ContinuationDecision:
        """Decide whether ``message`` continues the character's open exchange."""
        if thread is None or not thread.is_alive(self._clock()):
            return ContinuationDecision(reason_code="unrelated")

        content = (message.content or "").strip()
        if not content:
            return ContinuationDecision(reason_code="unrelated")

        confidence = 0.0
        signals: list[str] = []

        # 1. The bot spoke recently enough (thread alive already proves this).
        now = self._clock()
        gap = max(0.0, now - thread.updated_at)
        if gap <= self._window:
            confidence += 0.20
            signals.append("time")

        # 2. The speaker is someone already in this conversation.
        if str(message.user_id) in thread.participants:
            confidence += 0.25
            signals.append("participant")

        # 3. Structural cues: a question / anaphora that points at the bot turn.
        if any(marker in content for marker in FOLLOW_UP_MARKERS):
            confidence += 0.25
            signals.append("question_cue")
        if any(token in content for token in ANAPHORA):
            confidence += 0.15
            signals.append("anaphora")

        # 4. Semantic closeness to the bot's last message (the core signal).
        last_bot = thread.last_bot_message
        similarity = await self._similarity(content, last_bot)
        if similarity >= 0.30:
            confidence += 0.15 + min(0.25, similarity * 0.25)
            signals.append("semantic")

        confidence = min(1.0, confidence)

        if confidence >= self._threshold:
            reason = "direct_follow_up"
            if "question_cue" not in signals and similarity >= 0.30:
                reason = "topic_continuation"
        elif similarity >= 0.30:
            reason = "implicit_question"
        else:
            reason = "ambiguous" if "participant" in signals else "unrelated"

        is_follow_up = confidence >= self._threshold
        return ContinuationDecision(
            is_follow_up=is_follow_up,
            confidence=round(confidence, 3),
            target_message_id=thread.last_bot_message_id,
            reason_code=reason,
        )

    async def _similarity(self, left: str, right: str) -> float:
        left = (left or "").strip()
        right = (right or "").strip()
        if not left or not right:
            return 0.0
        if self._embeddings is not None and getattr(self._embeddings, "available", False):
            try:
                a = await self._embeddings.embed_one(left)
                b = await self._embeddings.embed_one(right)
                if a is not None and b is not None:
                    return cosine_similarity(a, b)
            except Exception:  # noqa: BLE001 - lexical fallback below
                self._log.debug("semantic similarity failed, using lexical", exc_info=True)
        return lexical_similarity(left, right)
