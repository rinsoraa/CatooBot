"""Relevance evaluation (v0.9 §33/§34/§59/§70).

Computes *structured* scores, never a roll. Semantic similarity (when the
embedding service is available) is blended with a keyword/bigram overlap so the
engine still works with embeddings off — and so a single dimension can never
"make the character speak" on its own (spec v0.9 §34/§96).
"""

from __future__ import annotations

import logging
from typing import Any

from app.memory.retrieval import bigrams
from app.memory.vector_store import cosine_similarity
from app.social.models import GroupMessage, RelevanceScores

#: Generic "I'm just agreeing" filler that should never count as contribution.
GENERIC_FILLER = (
    "哈哈",
    "哈哈哈",
    "确实",
    "是啊",
    "我也是",
    "我也觉得",
    "笑死",
    "666",
    "嗯嗯",
    "还行",
    "都行",
    "随便",
    "不知道",
    "同意",
)


def lexical_similarity(left: str, right: str) -> float:
    """Keyword fallback: unigram + bigram overlap (CJK-aware).

    Unigram overlap catches single shared characters ("看剧" vs "什么剧" shares
    "剧"), bigrams catch two-character phrases; we blend them so the fallback
    still recognises short follow-ups when embeddings are off.
    """
    left = (left or "").strip()
    right = (right or "").strip()
    if not left or not right:
        return 0.0
    left_uni = set(left)
    right_uni = set(right)
    uni = len(left_uni & right_uni) / max(1, min(len(left_uni), len(right_uni)))
    left_b = bigrams(left)
    right_b = bigrams(right)
    bi = 0.0
    if left_b and right_b:
        bi = len(left_b & right_b) / max(1, min(len(left_b), len(right_b)))
    return min(1.0, max(uni, bi * 1.2))


class RelevanceEvaluator:
    def __init__(
        self,
        *,
        embeddings: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._embeddings = embeddings
        self._log = logger or logging.getLogger("CatooBot.Social")

    @property
    def semantic_available(self) -> bool:
        return bool(self._embeddings is not None and getattr(self._embeddings, "available", False))

    async def _similarity(self, left: str, right: str) -> float:
        """Semantic cosine when available, else a keyword-overlap proxy."""
        left = (left or "").strip()
        right = (right or "").strip()
        if not left or not right:
            return 0.0
        if self.semantic_available:
            try:
                vec_left = await self._embeddings.embed_one(left)
                vec_right = await self._embeddings.embed_one(right)
                if vec_left is not None and vec_right is not None:
                    return cosine_similarity(vec_left, vec_right)
            except Exception:  # noqa: BLE001 - degrade to lexical
                self._log.debug("semantic similarity failed, using lexical", exc_info=True)
        return lexical_similarity(left, right)

    async def evaluate(
        self,
        *,
        messages: list[GroupMessage],
        topic: str,
        interests: list[str],
        activity_text: str,
        focus: str,
        thread_topic: str = "",
        attention_level: float = 0.5,
        fatigue: float = 0.0,
    ) -> RelevanceScores:
        """Score how worth-joining the current conversation is."""
        external = [m for m in messages if m.external]
        if not external:
            return RelevanceScores()

        joined = " ".join(m.content for m in external)
        latest = external[-1].content

        # topic_relevance: how close the talk is to the active topic / thread.
        topic_relevance = 0.0
        if topic:
            topic_relevance = await self._similarity(joined, topic)
        if thread_topic and thread_topic != topic:
            topic_relevance = max(topic_relevance, await self._similarity(latest, thread_topic))

        # character_relevance: interests + current activity + focus.
        character_sims: list[float] = []
        for interest in interests or []:
            character_sims.append(await self._similarity(joined, interest))
        if activity_text:
            character_sims.append(await self._similarity(latest, activity_text))
        if focus:
            character_sims.append(await self._similarity(joined, focus))
        character_relevance = max(character_sims, default=0.0)

        # conversation_relevance: is this a live exchange (not stale filler)?
        conversation_relevance = min(1.0, len(external) / 6.0)

        # social_fit: attention pulls up, fatigue pulls down (soft, never a ban).
        social_fit = max(0.0, min(1.0, 0.5 + (attention_level - 0.5) - fatigue * 0.3))

        # contribution_value: relevance without a real addition is still low.
        contribution_value = 0.6 * max(topic_relevance, character_relevance)
        if any(token in latest for token in GENERIC_FILLER) and len(latest) < 8:
            contribution_value *= 0.3

        return RelevanceScores(
            topic_relevance=round(topic_relevance, 3),
            character_relevance=round(character_relevance, 3),
            conversation_relevance=round(conversation_relevance, 3),
            social_fit=round(social_fit, 3),
            contribution_value=round(min(1.0, contribution_value), 3),
        )
