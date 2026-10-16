"""R0 §3.1：待决提案的确定性仲裁**不依赖模型**。

FU4 把仲裁点放在 ``_route_turn`` 顶部，但 ``_task_reply`` 在「模型决策无效」时提前返回
澄清、根本不进入 ``_route_turn`` —— 于是「同意」这类最基础的接受会被一起丢掉（提案留着、
用户的话被当成无法理解的闲聊）。本文件用**真实** ``_task_reply`` + **真实**
``_arbitrate_pending_handoff`` + **真实** ``_accept_pending_handoff``（只把既有入口换成
记录桩）锁住这条优先级，并确认确定性控制仍然优先于仲裁。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.chat_bridge import _TurnReply
from tests.test_phase8i_handoff_route_matrix import PENDING, Harness


class _InvalidOutcome:
    """与真实 TurnDecisionOutcome 形状一致的「模型输出无效」结果。"""

    def __init__(self, reason: str = "model_no_tool_call") -> None:
        self.ok = False
        self.reason_code = reason
        self.decision = None


def _stub_invalid_decision(h: Harness) -> None:
    """确定性控制放行 + 模型决策无效（真实失败形态之一）。"""
    h.bridge.bot.task_turns = object()  # type: ignore[attr-defined]

    async def _control_turn(handler: Any, username: str, session_id: str, message: str) -> Any:
        return None

    async def _decide_turn(*args: Any, **kwargs: Any) -> Any:
        return _InvalidOutcome()

    h.bridge._control_turn = _control_turn  # type: ignore[method-assign]
    h.bridge._decide_turn = _decide_turn  # type: ignore[method-assign]


async def test_pure_acceptance_survives_invalid_model_decision() -> None:
    """模型这次没给出有效决策时，明确接受仍必须被消费一次。"""
    h = Harness(pending=PENDING)
    _stub_invalid_decision(h)
    await h.bridge._task_reply("IdCraft", "s", "同意")
    assert len(h.bridge.accept_calls) == 1  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == [("IdCraft", "s", "砍橡木原木 2 根")]  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


async def test_mixed_conflict_survives_invalid_model_decision() -> None:
    """混合冲突消息在模型失效时同样必须作废旧提案并澄清（不得静默执行子目标）。"""
    h = Harness(pending=PENDING)
    _stub_invalid_decision(h)
    await h.bridge._task_reply("IdCraft", "s", "好，去砍五根白桦原木，然后去挖石头")
    assert "handoff_conflict_compound_clarify" in h.reasons
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") not in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert h.bridge.wood_routes == []  # type: ignore[attr-defined]
    assert h.plans.calls == []


async def test_invalid_decision_without_pending_still_clarifies() -> None:
    """没有待决提案时，无效决策仍按既有安全澄清处理（不消费、不执行）。"""
    h = Harness()
    _stub_invalid_decision(h)
    reply = await h.bridge._task_reply("IdCraft", "s", "同意")
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    assert reply is not None and reply.reply
    assert h.plans.calls == []


async def test_deterministic_control_still_wins_over_arbitration() -> None:
    """STOP/CANCEL 由第 1 步确定性控制处理，绝不被早退路径或仲裁吞掉。"""
    h = Harness(pending=PENDING)
    h.bridge.bot.task_turns = object()  # type: ignore[attr-defined]

    async def _control_turn(handler: Any, username: str, session_id: str, message: str) -> Any:
        return _TurnReply("已停止")

    async def _decide_turn(*args: Any, **kwargs: Any) -> Any:
        return _InvalidOutcome()

    h.bridge._control_turn = _control_turn  # type: ignore[method-assign]
    h.bridge._decide_turn = _decide_turn  # type: ignore[method-assign]
    reply = await h.bridge._task_reply("IdCraft", "s", "停止")
    assert reply is not None and reply.reply == "已停止"
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
