"""Phase 8E Follow-up 3 测试支持：统一回合决策的**替身解释器**。

替身只替换「模型怎么想」，**不放松契约**：它构造的是**真实** ``TurnDecision``
（真实 Pydantic 校验、真实白名单、真实 extra=forbid），返回**真实**
``TurnDecisionOutcome``。这样用例既能指定 kind，又不会绕过决策契约本身。
"""

from __future__ import annotations

from typing import Any

from app.tasks.turn_decision import (
    TurnDecision,
    TurnDecisionKind,
    TurnDecisionOutcome,
)


class TurnDecisionStub:
    """按预设 kind 回答的决策解释器（记录每次 decide 的原文，便于断言调用次数）。"""

    def __init__(
        self,
        kind: TurnDecisionKind | str = TurnDecisionKind.CHAT,
        *,
        reply_text: str = "",
        capability_id: str = "",
        arguments: dict[str, Any] | None = None,
        task_goal: str = "",
        clarification_question: str = "",
        reason_code: str = "",
    ) -> None:
        self.kind = kind
        self.reply_text = reply_text
        self.capability_id = capability_id
        self.arguments = dict(arguments or {})
        self.task_goal = task_goal
        self.clarification_question = clarification_question
        self.reason_code = reason_code
        self.calls: list[str] = []

    async def decide(
        self, *, message: str, recent_context: Any = (), task_context: str = ""
    ) -> TurnDecisionOutcome:
        self.calls.append(message)
        if self.reason_code:
            return TurnDecisionOutcome(None, self.reason_code, 1, 0.0, {})
        decision = TurnDecision(
            kind=self.kind,
            reply_text=self.reply_text,
            capability_id=self.capability_id,
            arguments=self.arguments,
            task_goal=self.task_goal,
            clarification_question=self.clarification_question,
        )
        return TurnDecisionOutcome(decision, "", 1, 0.0, {})


def chat_stub(**kwargs: Any) -> TurnDecisionStub:
    """普通对话（CHAT）：既不建任务也不碰工具。"""
    return TurnDecisionStub(TurnDecisionKind.CHAT, **kwargs)


def query_stub(capability_id: str, *, arguments: dict[str, Any] | None = None, **kwargs: Any):
    """只读查询（QUERY）：只允许 READ_ONLY 白名单里的能力。"""
    return TurnDecisionStub(
        TurnDecisionKind.QUERY, capability_id=capability_id, arguments=arguments, **kwargs
    )
