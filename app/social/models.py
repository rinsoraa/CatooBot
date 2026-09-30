"""Social cognition data models (v0.9).

These describe *structured social judgment* — never hidden reasoning. Every
decision carries a ``reason_code`` so the WebUI and tests can audit exactly why
the character spoke or stayed silent. Scores are rule inputs, not probabilities
(spec §19/§34/§95): a confidence is compared to a threshold, never fed to
``random()``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

#: Thread lifecycle (spec §12)
ThreadStatus = Literal["active", "waiting", "idle", "closed"]

#: Continuation decision reason codes (spec §18)
CONTINUATION_REASONS: tuple[str, ...] = (
    "direct_follow_up",
    "reply_to_bot",
    "topic_continuation",
    "implicit_question",
    "ambiguous",
    "unrelated",
)

#: Participation decision reason codes (spec §38)
PARTICIPATION_REASONS: tuple[str, ...] = (
    # positive
    "direct_mention",
    "reply_to_bot",
    "direct_follow_up",
    "explicit_bot_name",
    "topic_interest",
    "current_activity_relevance",
    "memory_relevance",
    "user_relationship",
    "helpful_contribution",
    "humor_opportunity",
    "shared_interest",
    # negative / neutral
    "no_relevance",
    "already_discussed",
    "conversation_too_fast",
    "low_contribution_value",
    "poor_timing",
    # hard gates
    "cooldown",
    "daily_limit",
    "group_disabled",
    "social_disabled",
    "sleeping",
    "dnd",
    "too_short",
    "addressed_to_someone_else",
)

Decision = Literal["reply", "observe", "ignore", "defer"]


@dataclass
class GroupMessage:
    """One recorded group message (spec §9)."""

    message_id: str
    group_id: str
    user_id: str
    nickname: str
    timestamp: float
    content: str
    is_bot_message: bool = False
    reply_to: str | None = None

    @property
    def external(self) -> bool:
        """Only other users' messages count toward the 5-message observer."""
        return not self.is_bot_message


@dataclass
class ConversationThread:
    """A live exchange the character is part of (spec §12/§50)."""

    thread_id: str
    group_id: str
    topic: str = ""
    participants: set[str] = field(default_factory=set)
    last_message_id: str = ""
    last_bot_message_id: str = ""
    last_bot_message: str = ""
    last_user_message: str = ""
    status: str = "active"
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float = 0.0

    def is_alive(self, now: float) -> bool:
        return self.status == "active" and now < self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "group_id": self.group_id,
            "topic": self.topic,
            "participants": sorted(self.participants),
            "last_bot_message": self.last_bot_message,
            "last_user_message": self.last_user_message,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "expires_at": self.expires_at,
        }


class ContinuationDecision(BaseModel):
    """Is the incoming message a natural follow-up to the bot's last turn?"""

    is_follow_up: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    target_message_id: str = ""
    reason_code: str = "unrelated"


class RelevanceScores(BaseModel):
    """Structured evaluation of how worth-joining the current talk is (§33/§34)."""

    topic_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    character_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    conversation_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    social_fit: float = Field(default=0.0, ge=0.0, le=1.0)
    contribution_value: float = Field(default=0.0, ge=0.0, le=1.0)

    def as_dict(self) -> dict[str, float]:
        return {
            "topic_relevance": round(self.topic_relevance, 3),
            "character_relevance": round(self.character_relevance, 3),
            "conversation_relevance": round(self.conversation_relevance, 3),
            "social_fit": round(self.social_fit, 3),
            "contribution_value": round(self.contribution_value, 3),
        }


class ParticipationDecision(BaseModel):
    """The outcome of social cognition for one message / batch (§35/§78)."""

    decision: Decision = "ignore"
    reason_code: str = "no_relevance"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    topic: str = ""
    response_goal: str = ""
    target_message_ids: list[str] = Field(default_factory=list)
    scores: RelevanceScores = Field(default_factory=RelevanceScores)

    @property
    def should_reply(self) -> bool:
        return self.decision == "reply"

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reason_code": self.reason_code,
            "confidence": round(self.confidence, 3),
            "topic": self.topic,
            "response_goal": self.response_goal,
            "target_message_ids": self.target_message_ids,
            "scores": self.scores.as_dict(),
        }


class SocialObservation(BaseModel):
    """Audit row for one observer pass (spec §85) — WebUI debug, not Memory."""

    observation_id: str = ""
    group_id: str = ""
    message_range: str = ""
    topic: str = ""
    decision: str = "ignore"
    reason_code: str = ""
    confidence: float = 0.0
    created_at: int = 0
    detail: dict[str, Any] = Field(default_factory=dict)


@dataclass
class SocialAttentionState:
    """The character's current *attention* to one group (spec §62/§65).

    These are narrative-modelling numbers for the character's engagement, not
    psychological claims about any user. ``fatigue`` only softens the decision,
    it never forbids a reply (spec §101/§102).
    """

    attention_level: float = 0.5
    current_topic: str = ""
    fatigue: float = 0.0
    momentum: float = 0.0
    last_updated: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "attention_level": round(self.attention_level, 3),
            "current_topic": self.current_topic,
            "fatigue": round(self.fatigue, 3),
            "momentum": round(self.momentum, 3),
            "last_updated": self.last_updated,
        }
