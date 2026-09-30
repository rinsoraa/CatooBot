"""ConversationDecisionEngine: rules + state + context, never a dice (§61).

Answers, before any language generation: respond? intent? how much? may I
ask one question back? is there an expression opening? Silence carries a
structured reason (§81) — never `random() < p` (§80).
"""

from __future__ import annotations

import re
from typing import Any

from app.conversation.models import ConversationDecision, ConversationTurn, TurnClassification

# Low-information messages (§62): allowed as *input signal*, and the reply may
# mirror the register — but the choice is contextual, not a fixed ratio (§122).
_LOW_INFO = re.compile(r"^(?:[哈呃嗯哦噢喔额]{1,6}|草+|笑死|确实|行|好[的吧]?|啊\?|6+|……+|\?+)$")
_QUESTION_CUE = re.compile(r"[?？]$|[?？][?？]|吗[?？]?$|怎么|为什么|啥|什么|哪")
_MAX_CONSECUTIVE_QUESTIONS = 2


class ConversationDecisionEngine:
    def __init__(self, config: Any, logger: Any = None) -> None:
        self.config = config
        self._log = logger

    async def decide(
        self,
        turn: ConversationTurn,
        *,
        previous_turn_text: str = "",
        momentum: float = 0.5,
        consecutive_questions: int = 0,
        social_decision: Any = None,
        hard_block: str | None = None,
    ) -> ConversationDecision:
        reasons: list[str] = []
        text = turn.text.strip()
        low_info = bool(_LOW_INFO.match(text))

        # --- hard gates (structured, ordered) ----------------------------
        if hard_block:
            return ConversationDecision(
                respond=False, intent=turn.classification.value,
                silence_reason=hard_block, reasons=["hard_block"],
            )
        if social_decision is not None and not getattr(social_decision, "should_reply", True):
            return ConversationDecision(
                respond=False, intent=turn.classification.value,
                silence_reason=getattr(social_decision, "reason_code", "") or "social_declined",
                reasons=["social_declined"],
            )

        decision = ConversationDecision(
            intent=turn.classification.value,
            engagement=self._engagement(turn, momentum),
        )

        # --- shape & intent ----------------------------------------------
        if turn.classification in (TurnClassification.correction, TurnClassification.interruption):
            decision.intent = "repair"
            decision.response_style = "brief"
            reasons.append("repair_context_reset")
        elif turn.classification == TurnClassification.closing:
            decision.response_style = "brief"
            decision.message_shape = "short"
            reasons.append("closing_reply")
        elif low_info:
            decision.response_style = "brief"
            decision.message_shape = "short"
            decision.expression_opportunity = True
            reasons.append("mirror_register")
        elif turn.mentioned or turn.reply_to_bot:
            reasons.append("direct_address")

        # One natural follow-up question is allowed only when the user's side
        # invites it and the bot has not been interrogating (§64/§65).
        invites_question = bool(_QUESTION_CUE.search(text)) and not low_info
        decision.should_ask = (
            invites_question and consecutive_questions < _MAX_CONSECUTIVE_QUESTIONS
        )
        if decision.should_ask:
            reasons.append("question_opportunity")
        decision.should_continue = momentum >= 0.6 and turn.classification in (
            TurnClassification.continuation,
            TurnClassification.follow_up,
            TurnClassification.multi_message,
        )

        if reasons:
            decision.reasons.extend(reasons)
        return decision

    @staticmethod
    def _engagement(turn: ConversationTurn, momentum: float) -> float:
        """0..1 structured interest in this exchange — decision input only."""
        score = 0.3
        score += min(0.2, 0.05 * len(turn.messages))
        score += 0.15 if _QUESTION_CUE.search(turn.text) else 0.0
        score += 0.2 if turn.mentioned or turn.reply_to_bot else 0.0
        score += 0.1 * momentum
        return min(1.0, round(score, 3))
