"""Conversation turn data models (v1.2).

Everything here is structured state with a reason code — never hidden chain
of thought (spec v1.2 §113). A turn is the unit of conversation: one user burst,
one classification, one decision, at most one response generation.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TurnClassification(str, Enum):  # noqa: UP042 - pydantic serializes .value via str
    """What kind of exchange this turn is (spec v1.2 §14)."""

    single = "single"  # one ordinary message
    multi_message = "multi_message"  # one user burst merged into one turn
    continuation = "continuation"  # user kept talking (follow-up folding)
    follow_up = "follow_up"  # short追问 of the previous exchange
    correction = "correction"  # "不是，我说的是另一个" — stale the old reply
    interruption = "interruption"  # "等等" — abort what is being generated
    topic_shift = "topic_shift"  # "对了还有个事"
    closing = "closing"  # "晚安" / "先这样"


class TurnStatus(str, Enum):  # noqa: UP042
    open = "open"  # buffering messages
    pending = "pending"  # queued, waiting for the session worker
    generating = "generating"  # a response generation is in flight
    delivered = "delivered"
    silent = "silent"  # decision said don't respond (reason kept)
    stale = "stale"  # superseded before/while sending
    cancelled = "cancelled"
    error = "error"


class TurnMessage(BaseModel):
    """One raw QQ message inside a turn."""

    message_id: str = ""
    user_id: str
    nickname: str = ""
    text: str = ""
    mentioned: bool = False
    reply_to_bot: bool = False
    media_count: int = 0
    created_at: float = Field(default_factory=time.time)


class ConversationTurn(BaseModel):
    """One conversational exchange unit (spec v1.2 §20/§21)."""

    turn_id: str
    generation_id: int = 0
    session_id: str  # private:<uid> / group:<gid>
    user_id: str
    nickname: str = ""
    group_id: str | None = None
    messages: list[TurnMessage] = Field(default_factory=list)
    started_at: float = Field(default_factory=time.time)
    ended_at: float | None = None
    classification: TurnClassification = TurnClassification.single
    topic: str = ""
    status: TurnStatus = TurnStatus.open
    silence_reason: str = ""  # structured reason when status=silent
    media_count: int = 0
    # In-memory extras from the plugin (media items, deferred images, hard
    # block, context trace). Never persisted with the turn.
    meta: dict[str, Any] = Field(default_factory=dict)

    @property
    def text(self) -> str:
        """The user's side of the turn: burst messages folded with line breaks."""
        return "\n".join(m.text for m in self.messages if m.text)

    @property
    def mentioned(self) -> bool:
        return any(m.mentioned for m in self.messages)

    @property
    def reply_to_bot(self) -> bool:
        return any(m.reply_to_bot for m in self.messages)

    def ended(self, clock: Any = time.time) -> None:
        self.ended_at = float(clock())


class ConversationDecision(BaseModel):
    """Structured answer to "should I respond, and how?" (spec v1.2 §60).

    Produced by rules + state + social context (v1.2 §61) — never a dice roll.
    """

    respond: bool = True
    intent: str = "chat"  # chat / follow_up / correction / closing / ...
    engagement: float = Field(default=0.5, ge=0.0, le=1.0)
    response_style: str = "casual"  # casual / brief / engaged
    message_shape: str = "auto"  # auto / short / single / text_and_sticker
    should_ask: bool = False  # may attach one natural follow-up question
    should_continue: bool = False  # topic has momentum worth carrying
    expression_opportunity: bool = False
    silence_reason: str = ""  # v1.2 §81 structured reasons when respond=False
    reasons: list[str] = Field(default_factory=list)


class ResponseStep(BaseModel):
    """One step of an outgoing sequence (spec v1.2 §72/§73): text / sticker / pause."""

    type: str  # text | sticker | pause
    text: str = ""
    attachment: Any = None  # StickerAsset | NativeFace when type=sticker
    duration: float = 0.0  # pause seconds
