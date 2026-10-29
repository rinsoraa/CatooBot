"""Conversation Turn Runtime (v1.2).

Messages become *turns* before anything answers them: a user burst ("你干嘛呢
/ 在吗 / 我突然想到一件事") is one turn, one decision, one response — and a
correction that arrives while a reply is being generated marks that reply
stale instead of letting it go out.
"""

from app.conversation.models import (
    ConversationDecision,
    ConversationTurn,
    ResponseStep,
    TurnClassification,
    TurnMessage,
    TurnStatus,
)
from app.conversation.runtime import ConversationTurnRuntime

__all__ = [
    "ConversationDecision",
    "ConversationTurn",
    "ConversationTurnRuntime",
    "ResponseStep",
    "TurnClassification",
    "TurnMessage",
    "TurnStatus",
]
