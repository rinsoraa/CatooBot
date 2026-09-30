"""Turn classification: rule-based, deterministic, no model calls.

The classifier looks at *how* the burst arrived (gaps, counts, prior turn)
and at *what the words do* (correction cues, topic-shift cues). It never
calls an LLM — classification happens on every flush and must be free.
"""

from __future__ import annotations

import re

from app.conversation.models import ConversationTurn, TurnClassification

# Word-level cues (§14). Ordered: correction beats interruption beats shift.
_CORRECTION_CUES = re.compile(r"不是[,，。！!？?\s]|我说的是|不对[,，]|搞错了|理解错了|另一个|重说")
_INTERRUPTION_CUES = re.compile(r"^等等|^等下|^停|^先别|^打断|等一下[,，。]")
_TOPIC_SHIFT_CUES = re.compile(r"^对了|还有个事|另外|换个话题|说起来|突然想到|对了还有")
_CLOSING_CUES = re.compile(r"^晚安|^拜拜|^先这样|^下了|^睡了|回见|下次聊|^我去了")
_FOLLOW_UP_CUES = re.compile(r"你觉得呢|怎么看|然后呢|哪一部|哪个|为什么|怎么样了|后来呢|继续说")
_SHORT_FOLLOW_UP = re.compile(r"^(看啥呢|看什么|你说|啥|什么|哪|怎么|然后|后来|真的吗|是吗)")


def classify(turn: ConversationTurn, *, previous_turn_text: str = "") -> TurnClassification:
    """Classify one closed turn. Pure function of the turn + last exchange."""
    text = turn.text.strip()
    if not text:
        return TurnClassification.single

    if _CORRECTION_CUES.search(text):
        return TurnClassification.correction
    if _INTERRUPTION_CUES.search(text):
        return TurnClassification.interruption
    if _CLOSING_CUES.search(text):
        return TurnClassification.closing

    first = turn.messages[0].text if turn.messages else ""
    if _TOPIC_SHIFT_CUES.search(first) and len(turn.messages) <= 2:
        return TurnClassification.topic_shift

    if len(turn.messages) > 1:
        # A burst is a follow-up only when it *adds* a question to the same
        # thread; otherwise it is one multi-message exchange.
        if previous_turn_text and _FOLLOW_UP_CUES.search(text):
            return TurnClassification.follow_up
        return TurnClassification.multi_message

    if previous_turn_text:
        # Single message right after an exchange: a short追问 folds into it.
        if _SHORT_FOLLOW_UP.match(text) or _FOLLOW_UP_CUES.search(text):
            return TurnClassification.follow_up
        return TurnClassification.continuation

    return TurnClassification.single
