"""Phase 8I Follow-up 3 §5：显式接受门的真实 _route_turn 路由矩阵。

这些用例执行**真实的** MinecraftChatBridge._route_turn 与真实的
_accept_pending_handoff，只把「既有资源/合成入口」换成记录桩：既证明接受门本身，
也证明新的采集/合成目标不会复用旧提案的目标与数量、负向消息零副作用。
"""

from __future__ import annotations

import time
from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.minecraft.chat_bridge import (
    ACCEPT_PROPOSAL,
    NEW_GOAL,
    NOT_ACCEPT,
    REJECT_PROPOSAL,
    MinecraftChatBridge,
    _TurnReply,
    _WoodRoute,
    classify_handoff_reply,
)
from app.tasks.turn_decision import TurnDecisionKind, TurnDecisionOutcome

PENDING: dict[str, Any] = {
    "block": "oak_log",
    "amount": 2,
    "at": 0.0,
    "craft_goal": "给我做 4 块橡木木板",
}


class _Plans:
    """假计划服务：记录是否有真实的观察/计划调用（负向场景必须为空）。"""

    def __init__(self) -> None:
        self.calls: list[Any] = []

    async def observe(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(("observe", tool))
        return SimpleNamespace(result={}, summary=tool)

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(("plan", kwargs))
        return {
            "action": "blocked",
            "reply": "做不了",
            "outcome": "blocked_by_precondition",
            "plan": None,
            "record": None,
        }


class Harness:
    """真实桥接对象 + 记录桩：只替换「既有入口」，路由与接受门都是真代码。"""

    def __init__(self, *, pending: dict[str, Any] | None = None, expired: bool = False) -> None:
        bridge = object.__new__(MinecraftChatBridge)
        plans = _Plans()
        bridge.bot = SimpleNamespace(agent_plans=plans, character=None)
        bridge.service = None
        bridge._pending_handoffs = {}
        bridge._pending_craft_goals = {}
        bridge.routes = []  # type: ignore[attr-defined]
        bridge.turn_routes = []  # type: ignore[attr-defined]
        bridge.wood_replies = []  # type: ignore[attr-defined]
        bridge.wood_routes = []  # type: ignore[attr-defined]
        bridge.craft_calls = []  # type: ignore[attr-defined]
        bridge._route = lambda **kw: bridge.routes.append(kw)  # type: ignore[method-assign,attr-defined]
        bridge._turn_route = (  # type: ignore[method-assign]
            lambda session_id, message, target, reason: bridge.turn_routes.append((target, reason))
        )
        bridge._decision_text = (  # type: ignore[method-assign]
            lambda decision, fallback: fallback
        )

        async def _wood_reply(
            username: str, session_id: str, objective: str, wood: Any
        ) -> _TurnReply:
            bridge.wood_replies.append((username, session_id, objective))  # type: ignore[attr-defined]
            return _TurnReply("资源入口（桩）")

        async def _wood_route(handler: Any, username: str, session_id: str, message: str) -> Any:
            bridge.wood_routes.append(message)  # type: ignore[attr-defined]
            return SimpleNamespace(route=_WoodRoute.NOT_WOOD, reply=None)

        async def _craft_turn(
            username: str, session_id: str, message: str, request: Any
        ) -> _TurnReply:
            bridge.craft_calls.append(request)  # type: ignore[attr-defined]
            return _TurnReply("合成入口（桩）")

        async def _delegate_task(
            handler: Any, username: str, session_id: str, message: str
        ) -> _TurnReply:
            return _TurnReply(None)

        async def _plan_reply(username: str, session_id: str, message: str) -> Any:
            return None

        bridge._wood_reply = _wood_reply  # type: ignore[method-assign]
        bridge._wood_route = _wood_route  # type: ignore[method-assign]
        bridge._craft_turn = _craft_turn  # type: ignore[method-assign]
        bridge._delegate_task = _delegate_task  # type: ignore[method-assign]
        bridge._plan_reply = _plan_reply  # type: ignore[method-assign]

        self.bridge = bridge
        self.plans = plans
        if pending is not None:
            item = dict(pending)
            item["at"] = time.monotonic() - 10_000.0 if expired else time.monotonic()
            bridge._pending_handoffs[("IdCraft", "s")] = item

    async def send(
        self, message: str, *, user: str = "IdCraft", session: str = "s"
    ) -> _TurnReply | None:
        outcome = TurnDecisionOutcome(None, "test", 0, 0.0, {})
        return await self.bridge._route_turn(
            None, user, session, message, TurnDecisionKind.TASK_PROPOSAL, outcome
        )

    @property
    def reasons(self) -> list[str]:
        return [str(item.get("reason_code")) for item in self.bridge.routes]  # type: ignore[attr-defined]


def _no_proposal_task(h: Harness) -> None:
    """负向断言：旧提案未被消费、没有意外建立的资源任务、没有真实计划/观察调用。"""
    assert h.bridge.wood_replies == [], "旧提案被误消费（走了资源入口）"  # type: ignore[attr-defined]
    assert h.plans.calls == [], "不该出现真实的观察/计划调用"


# ---- 分类器契约（§4.1 可接受 / §4.2 必须识别为新意图） ----


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("同意", ACCEPT_PROPOSAL),
        ("可以", ACCEPT_PROPOSAL),
        ("好，去砍橡木原木", ACCEPT_PROPOSAL),
        ("好，就按这个方案来", ACCEPT_PROPOSAL),
        ("好的", ACCEPT_PROPOSAL),
        ("给我做 64 块橡木木板", NEW_GOAL),
        ("砍两根橡木原木", NEW_GOAL),
        ("砍五根白桦原木", NEW_GOAL),
        ("算了别做了", REJECT_PROPOSAL),
        ("停下", REJECT_PROPOSAL),
        ("取消", REJECT_PROPOSAL),
        ("这个要多久？", NOT_ACCEPT),
        ("可以吗", NOT_ACCEPT),
        ("跟我去那边", NOT_ACCEPT),
        ("嗯？", NOT_ACCEPT),
        ("好难", NOT_ACCEPT),
        ("", NOT_ACCEPT),
    ],
)
def test_classifier_contract(message: str, expected: str) -> None:
    assert classify_handoff_reply(message) == expected


# ---- 路由矩阵（§5 十行） ----


async def test_row1_explicit_accept_consumes_and_uses_existing_entry() -> None:
    h = Harness(pending=PENDING)
    reply = await h.send("同意")
    assert reply is not None
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == [("IdCraft", "s", "砍橡木原木 2 根")]  # type: ignore[attr-defined]
    assert "handoff_accepted" in h.reasons
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]


async def test_row2_repeated_craft_request_never_accepts() -> None:
    h = Harness(pending=PENDING)
    await h.send("给我做 64 块橡木木板")
    _no_proposal_task(h)
    assert h.bridge.craft_calls != []  # type: ignore[attr-defined]
    assert "handoff_new_goal" in h.reasons
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


async def test_row3_new_gathering_request_never_accepts() -> None:
    h = Harness(pending=PENDING)
    await h.send("砍两根橡木原木")
    _no_proposal_task(h)
    assert h.bridge.wood_routes == ["砍两根橡木原木"]  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


async def test_row4_unrelated_task_keeps_pending_untouched() -> None:
    h = Harness(pending=PENDING)
    await h.send("跟我去那边")
    _no_proposal_task(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert "handoff_not_accept" in h.reasons


async def test_row5_question_does_not_start_anything() -> None:
    h = Harness(pending=PENDING)
    await h.send("这个要多久？")
    _no_proposal_task(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]


async def test_row6_cancel_voids_proposal_and_never_revives() -> None:
    h = Harness(pending=PENDING)
    await h.send("算了别做了")
    _no_proposal_task(h)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert "handoff_reject_proposal" in h.reasons
    await h.send("同意")  # 已作废 → 不得复活
    _no_proposal_task(h)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


async def test_row7_expired_proposal_creates_nothing() -> None:
    h = Harness(pending=PENDING, expired=True)
    reply = await h.send("同意")
    assert reply is not None and "过期" in (reply.reply or "")
    _no_proposal_task(h)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


async def test_row8_other_session_cannot_consume() -> None:
    h = Harness(pending=PENDING)
    await h.send("同意", session="s2")
    _no_proposal_task(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    await h.send("同意", user="SomeoneElse")
    _no_proposal_task(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]


async def test_row9_second_accept_creates_no_second_resource_task() -> None:
    h = Harness(pending=PENDING)
    await h.send("同意")
    await h.send("同意")
    assert len(h.bridge.wood_replies) == 1  # type: ignore[attr-defined]


async def test_row10_new_request_does_not_reuse_old_target_or_amount() -> None:
    h = Harness(pending=PENDING)
    await h.send("砍五根白桦原木")
    _no_proposal_task(h)
    assert h.bridge.wood_routes == ["砍五根白桦原木"]  # type: ignore[attr-defined]
    assert "砍橡木原木 2 根" not in "".join(h.bridge.wood_routes)  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
