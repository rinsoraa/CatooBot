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
    _acceptance_remainder,
    _TurnReply,
    _WoodRoute,
    classify_handoff_reply,
    handoff_target_verdict,
)
from app.tasks.intent import is_permissive_wood_acceptance, unexplained_acceptance_tail
from app.tasks.turn_decision import TurnDecisionKind, TurnDecisionOutcome

PENDING: dict[str, Any] = {
    "item": "oak_log",
    "block": "oak_log",
    "amount": 2,
    "requested": 2,
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
        bridge.capability_calls = []  # type: ignore[attr-defined]
        bridge.delegate_calls = []  # type: ignore[attr-defined]
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

        async def _run_capability(
            username: str, session_id: str, message: str, kind: Any, decision: Any
        ) -> _TurnReply:
            bridge.capability_calls.append((kind, message))  # type: ignore[attr-defined]
            return _TurnReply("能力入口（桩）")

        async def _delegate_task(
            handler: Any, username: str, session_id: str, message: str
        ) -> _TurnReply:
            bridge.delegate_calls.append(message)  # type: ignore[attr-defined]
            return _TurnReply(None)

        async def _plan_reply(username: str, session_id: str, message: str) -> Any:
            return None

        bridge._wood_reply = _wood_reply  # type: ignore[method-assign]
        bridge._wood_route = _wood_route  # type: ignore[method-assign]
        bridge._craft_turn = _craft_turn  # type: ignore[method-assign]
        bridge._run_capability = _run_capability  # type: ignore[method-assign]
        bridge.accept_calls = []  # type: ignore[attr-defined]
        original_accept = MinecraftChatBridge._accept_pending_handoff

        async def _accept_pending(username: str, session_id: str, message: str) -> Any:
            bridge.accept_calls.append((username, session_id, message))  # type: ignore[attr-defined]
            return await original_accept(bridge, username, session_id, message)

        bridge._accept_pending_handoff = _accept_pending  # type: ignore[method-assign]
        bridge._delegate_task = _delegate_task  # type: ignore[method-assign]
        bridge._plan_reply = _plan_reply  # type: ignore[method-assign]

        self.bridge = bridge
        self.plans = plans
        if pending is not None:
            item = dict(pending)
            item["at"] = time.monotonic() - 10_000.0 if expired else time.monotonic()
            bridge._pending_handoffs[("IdCraft", "s")] = item

    async def send(
        self,
        message: str,
        *,
        user: str = "IdCraft",
        session: str = "s",
        kind: TurnDecisionKind = TurnDecisionKind.TASK_PROPOSAL,
    ) -> _TurnReply | None:
        outcome = TurnDecisionOutcome(None, "test", 0, 0.0, {})
        return await self.bridge._route_turn(None, user, session, message, kind, outcome)

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


# ---- Phase 8I FU4 §4B：跨上游 TurnDecisionKind 的完整路由矩阵 ----

ALL_KINDS = [
    TurnDecisionKind.TASK_PROPOSAL,
    TurnDecisionKind.QUERY,
    TurnDecisionKind.ATOMIC_ACTION,
    TurnDecisionKind.CHAT,
    TurnDecisionKind.NARRATION,
    TurnDecisionKind.CLARIFY,
    TurnDecisionKind.UNSUPPORTED,
]


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_fu4_accept_is_kind_independent(kind: TurnDecisionKind) -> None:
    """E6 同构：上游判成任何 kind，明确接受都必须消费有效提案一次并交既有资源入口。

    FU3 的真实缺陷：接受门只在 TASK_PROPOSAL 分支内，QUERY/ATOMIC_ACTION 等提前
    返回的分支根本到不了接受门。
    """
    h = Harness(pending=PENDING)
    reply = await h.send("好，去砍橡木原木", kind=kind)
    assert reply is not None
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == [("IdCraft", "s", "砍橡木原木 2 根")]  # type: ignore[attr-defined]
    assert "handoff_accepted" in h.reasons
    assert len(h.bridge.wood_replies) == 1, "提案必须恰好消费一次"  # type: ignore[attr-defined]
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]
    assert h.bridge.capability_calls == [], "接受消息不得继续进 _run_capability"  # type: ignore[attr-defined]
    assert h.bridge.delegate_calls == [], "接受消息不得继续进既有任务入口"  # type: ignore[attr-defined]
    assert h.plans.calls == []


@pytest.mark.parametrize(
    "kind",
    [TurnDecisionKind.TASK_PROPOSAL, TurnDecisionKind.QUERY, TurnDecisionKind.ATOMIC_ACTION],
)
async def test_fu4_new_gather_goal_not_swallowed(kind: TurnDecisionKind) -> None:
    """§3.3：新采集目标既不消费旧提案，也不被仲裁吞掉（仍进自己的既有路由）。"""
    h = Harness(pending=PENDING)
    await h.send("砍五根白桦原木", kind=kind)
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert "handoff_new_goal" in h.reasons
    assert h.bridge.delegate_calls != [] or h.bridge.capability_calls != []
    assert "砍橡木原木 2 根" not in "".join(h.bridge.wood_routes)  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "kind",
    [TurnDecisionKind.CHAT, TurnDecisionKind.NARRATION, TurnDecisionKind.TASK_PROPOSAL],
)
async def test_fu4_question_query_behaviour_preserved(kind: TurnDecisionKind) -> None:
    """§3.4：询问不消费提案，且按其自身 kind 继续得到既有行为。"""
    h = Harness(pending=PENDING)
    await h.send("这个要多久？", kind=kind)
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert h.plans.calls == []
    if kind in (TurnDecisionKind.CHAT, TurnDecisionKind.NARRATION):
        expected = f"decision_{kind.value}"
        assert any(reason == expected for _t, reason in h.bridge.turn_routes)  # type: ignore[attr-defined]


@pytest.mark.parametrize("kind", [TurnDecisionKind.CANCEL, TurnDecisionKind.STOP])
async def test_fu4_control_kind_stays_authoritative(kind: TurnDecisionKind) -> None:
    """§3.2/§2：CANCEL/STOP 的既有控制语义优先 —— 即便措辞命中接受词也不消费提案。"""
    h = Harness(pending=PENDING)
    reply = await h.send("同意", kind=kind)
    assert reply is None, "控制类必须交回既有控制路径"
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert h.plans.calls == []


@pytest.mark.parametrize("kind", ALL_KINDS)
async def test_fu4_accept_word_without_pending_creates_no_resource_task(
    kind: TurnDecisionKind,
) -> None:
    """§3.1：没有有效待决提案时，接受词单独绝不创建资源任务。"""
    h = Harness(pending=None)
    await h.send("同意", kind=kind)
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert h.plans.calls == []
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]


async def test_fu4_reject_voids_even_when_kind_is_query() -> None:
    """§3.2 + §3.4：拒绝作废提案（不复活），消息仍按自身 kind 走既有查询行为。"""
    h = Harness(pending=PENDING)
    await h.send("算了别做了", kind=TurnDecisionKind.QUERY)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert "handoff_reject_proposal" in h.reasons
    assert h.bridge.capability_calls == [(TurnDecisionKind.QUERY, "算了别做了")]  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    await h.send("同意", kind=TurnDecisionKind.ATOMIC_ACTION)  # 已作废 → 不复活
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]


async def test_fu4_cross_session_isolation_holds_across_kinds() -> None:
    """§3.4：同用户不同 session、不同用户都不得消费他人提案（跨 kind 亦然）。"""
    h = Harness(pending=PENDING)
    await h.send("好，去砍橡木原木", session="s2", kind=TurnDecisionKind.ATOMIC_ACTION)
    await h.send("好，去砍橡木原木", user="SomeoneElse", kind=TurnDecisionKind.QUERY)
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert h.plans.calls == []


async def test_fu4_prior_task_entry_not_advanced_by_accept() -> None:
    """E6 同构（run 3 现场）：有先前任务时，接受语被误判成 ATOMIC_ACTION 也不得推进它。"""
    h = Harness(pending=PENDING)
    await h.send("好，去砍橡木原木", kind=TurnDecisionKind.ATOMIC_ACTION)
    assert h.bridge.delegate_calls == []  # type: ignore[attr-defined]
    assert h.bridge.capability_calls == []  # type: ignore[attr-defined]
    # 仲裁只接管这一条消息：随后无关消息仍能正常进入既有任务入口
    await h.send("跟我去那边", kind=TurnDecisionKind.TASK_PROPOSAL)
    assert h.bridge.delegate_calls == ["跟我去那边"]  # type: ignore[attr-defined]


# ---- Phase 8I FU5 §4：接受表达与待决提案目标一致性（真实 _route_turn + 真实仲裁） ----

PENDING_OBJECTIVE = "砍橡木原木 2 根"


def _fu5_negative(h: Harness) -> None:
    """§4.2 负向公共断言：没消费、没把消息喂给资源入口、没有真实观察/计划调用。"""
    assert h.bridge.wood_replies == [], "冲突的新目标不得消费旧提案"  # type: ignore[attr-defined]
    assert h.plans.calls == []


@pytest.mark.parametrize("message", ["同意", "可以", "好，就按这个方案来"])
async def test_fu5_pure_accept_consumes_once(message: str) -> None:
    """§3.1：纯接受可以消费当前提案（目标只能来自提案）。"""
    h = Harness(pending=PENDING)
    await h.send(message)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == [("IdCraft", "s", PENDING_OBJECTIVE)]  # type: ignore[attr-defined]
    assert "handoff_accepted" in h.reasons
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "message",
    [
        "好，去砍橡木原木",
        "好，去砍原木",
        "好，去砍两根橡木原木",
        "好，去砍橡木原木带回来",
        "好，去砍橡木原木再回来",
    ],
)
async def test_fu5_matching_description_uses_pending_target(message: str) -> None:
    """§3.3：接受 + 与提案一致的描述 → 可接受，目标只能来自待决提案且不得重复建任务。"""
    h = Harness(pending=PENDING)
    await h.send(message)
    assert h.bridge.wood_replies == [("IdCraft", "s", PENDING_OBJECTIVE)]  # type: ignore[attr-defined]
    await h.send(message)  # 提案已被消费 → 第二次不得再建
    assert len(h.bridge.wood_replies) == 1  # type: ignore[attr-defined]
    assert "handoff_accepted" in h.reasons


@pytest.mark.parametrize(
    "message",
    ["好，去砍五根白桦原木", "同意，改砍白桦", "可以，不过这次砍五根橡木原木"],
)
async def test_fu5_conflicting_new_target_never_consumes(message: str) -> None:
    """§3.2：与提案冲突的新目标优先 —— 不消费旧提案，消息仍走自己的既有路由。"""
    h = Harness(pending=PENDING)
    await h.send(message)
    _fu5_negative(h)
    assert h.bridge._pending_handoffs == {}, "冲突的新目标必须作废旧提案"  # type: ignore[attr-defined]
    assert "handoff_new_goal" in h.reasons
    assert "handoff_accepted" not in h.reasons
    # 消息没有被吞掉：仍进入既有木材/任务路由（其自身授权链负责后续）
    assert h.bridge.wood_routes == [message] or h.bridge.delegate_calls == [message]  # type: ignore[attr-defined]


async def test_fu5_same_species_new_amount_not_silently_reused() -> None:
    """§3.2：同树种但明确新数量不得静默套用提案数量。"""
    h = Harness(pending=PENDING)
    await h.send("可以，不过这次砍五根橡木原木")
    _fu5_negative(h)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert "handoff_accepted" not in h.reasons
    assert "2 根" not in str(h.bridge.wood_routes)  # type: ignore[attr-defined]


async def test_fu5_new_craft_goal_re_evaluated_not_consumed() -> None:
    """§3.2：「好，给我做 64 块橡木木板」是新的合成目标 —— 不消费旧提案，重新评估合成。"""
    h = Harness(pending=PENDING)
    await h.send("好，给我做 64 块橡木木板")
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert h.bridge.craft_calls != []  # type: ignore[attr-defined]
    assert "handoff_accepted" not in h.reasons


async def test_fu5_unrelated_new_task_keeps_pending_and_own_route() -> None:
    """§3.2：接受词后跟无关内容 → 不消费、提案保留、原有任务意图照常处理。"""
    h = Harness(pending=PENDING)
    await h.send("好，先跟我去另一个地方")
    _fu5_negative(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert "handoff_accepted" not in h.reasons
    assert "handoff_not_accept" in h.reasons
    assert h.bridge.delegate_calls == ["好，先跟我去另一个地方"]  # type: ignore[attr-defined]


@pytest.mark.parametrize("message", ["好难", "可以吗？", "嗯？"])
async def test_fu5_ambiguous_never_consumes(message: str) -> None:
    """§4.1：不确定/询问一律 fail closed，提案保留。"""
    h = Harness(pending=PENDING)
    await h.send(message)
    _fu5_negative(h)
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]


async def test_fu5_reject_then_accept_never_revives() -> None:
    """§4.1：明确拒绝作废后，随后的接受词不得复活。"""
    h = Harness(pending=PENDING)
    await h.send("算了别做了")
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    await h.send("同意")
    _fu5_negative(h)
    assert "handoff_accepted" not in h.reasons


@pytest.mark.parametrize(
    "message",
    ["同意", "好，去砍五根白桦原木", "可以，不过这次砍五根橡木原木"],
)
async def test_fu5_no_pending_creates_no_resource_task(message: str) -> None:
    """§4.1：不存在待决提案时，接受词（含带新目标的说法）都不创建资源任务。"""
    h = Harness(pending=None)
    await h.send(message)
    _fu5_negative(h)
    assert "handoff_accepted" not in h.reasons


@pytest.mark.parametrize("kind", [TurnDecisionKind.QUERY, TurnDecisionKind.ATOMIC_ACTION])
@pytest.mark.parametrize(
    "message",
    ["好，去砍五根白桦原木", "可以，不过这次砍五根橡木原木", "同意，改砍白桦"],
)
async def test_fu5_conflict_holds_across_decision_kinds(
    message: str,
    kind: TurnDecisionKind,
) -> None:
    """§4.2：一致性修复不得重新引入 FU4 的跨路由问题（非 task_proposal 也要守住）。"""
    h = Harness(pending=PENDING)
    await h.send(message, kind=kind)
    _fu5_negative(h)
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("同意", ACCEPT_PROPOSAL),
        ("可以", ACCEPT_PROPOSAL),
        ("好，就按这个方案来", ACCEPT_PROPOSAL),
        ("好，去砍橡木原木", ACCEPT_PROPOSAL),
        ("好，去砍原木", ACCEPT_PROPOSAL),
        ("好，去砍两根橡木原木", ACCEPT_PROPOSAL),
        ("好，去砍五根白桦原木", NEW_GOAL),
        ("同意，改砍白桦", NEW_GOAL),
        ("可以，不过这次砍五根橡木原木", NEW_GOAL),
        ("好，去砍橡木原木并跟我走", NOT_ACCEPT),
        ("好，去砍橡木原木，然后探索附近", NOT_ACCEPT),
        ("好，去砍橡木原木，帮我挖附近的石头", NOT_ACCEPT),
        ("好，去砍橡木原木，挖点石头", NOT_ACCEPT),
        ("好，去砍橡木原木，去挖石头", NOT_ACCEPT),
        ("好，去砍橡木原木再去挖石头", NOT_ACCEPT),
        ("好，去砍橡木原木再回来", ACCEPT_PROPOSAL),
        ("好，先跟我去另一个地方", NOT_ACCEPT),
        ("好，去砍竹子", NOT_ACCEPT),
        ("好，给我做 64 块橡木木板", NOT_ACCEPT),
    ],
)
def test_fu5_target_consistency_contract(message: str, expected: str) -> None:
    """§3 契约表（纯函数、无模型、无任务）：接受表达的目标一致性判定。"""
    assert handoff_target_verdict(message, PENDING) == expected


# ---- Phase 8I FU5 Follow-up：复合意图不得消费待决提案（§必测场景 3、4）----

#: 复合接受表达：目标与提案相容，但整条消息还有**未被许可语法完整解释**的片段。
#: 前五条是 FU5-FU/FU5-FU2 的连接词/跟随/标点类；中间三条是任务书 §3.3 的**无标点、
#: 无连接词**连续指令（本轮由完整消费判定覆盖）；最后三条是 §5.3 的未知尾部（未知资源、
#: 未知动作、无关闲聊），同样不得被默认为无害。
COMPOUND_MESSAGES = (
    "好，去砍橡木原木并跟我走",
    "好，去砍橡木原木，然后探索附近",
    "好，去砍橡木原木，帮我挖附近的石头",
    "好，去砍橡木原木，挖点石头",
    "好，去砍橡木原木再去挖石头",
    "好，去砍橡木原木去挖石头",
    "好，去砍橡木原木探索附近",
    "好，去砍橡木原木挖附近的石头",
    "好，去砍橡木原木，帮我找附近的煤",
    "好，去砍橡木原木然后去钓鱼吧",
    "好，去砍橡木原木挖附近的青金石",
    "好，去砍橡木原木顺手把栅栏修了",
)


@pytest.mark.parametrize("message", COMPOUND_MESSAGES)
async def test_fu5fu_compound_intent_never_consumes(message: str) -> None:
    """§必测 3/4：目标相容但整条消息还有别的意图 → 保留提案、绝不消费、不吞额外意图。"""
    h = Harness(pending=PENDING)
    reply = await h.send(message)
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert "handoff_not_accept" in h.reasons
    assert "handoff_accepted" not in h.reasons
    # 本轮补丁：复合消息**既不**消费提案，也**不**把第二个目标静默丢掉 —— 桥接改为澄清
    # （保留提案、不执行、不替用户猜），因此不进任何执行/任务路由。
    assert "handoff_compound_clarify" in h.reasons
    assert reply is not None and reply.reply
    assert "一件" in (reply.reply or "")
    assert h.bridge.wood_routes == []  # type: ignore[attr-defined]
    assert h.bridge.delegate_calls == []  # type: ignore[attr-defined]
    assert h.plans.calls == []


@pytest.mark.parametrize("kind", [TurnDecisionKind.QUERY, TurnDecisionKind.ATOMIC_ACTION])
@pytest.mark.parametrize("message", COMPOUND_MESSAGES)
async def test_fu5fu_compound_holds_across_decision_kinds(
    message: str,
    kind: TurnDecisionKind,
) -> None:
    """§验收门禁：非 task_proposal 类别下复合意图同样不得消费（不重新引入 FU4 问题）。"""
    h = Harness(pending=PENDING)
    reply = await h.send(message, kind=kind)
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == []  # type: ignore[attr-defined]
    assert ("IdCraft", "s") in h.bridge._pending_handoffs  # type: ignore[attr-defined]
    assert h.bridge.craft_calls == []  # type: ignore[attr-defined]
    assert h.bridge.capability_calls == []  # type: ignore[attr-defined]
    assert "handoff_compound_clarify" in h.reasons
    assert reply is not None and reply.reply
    assert h.plans.calls == []


@pytest.mark.parametrize("message", COMPOUND_MESSAGES)
async def test_fu5fu_pure_acceptance_still_works_after_compound(message: str) -> None:
    """对照：复合消息之后，同一提案的**纯**接受仍必须正常消费（门不是一刀切）。"""
    h = Harness(pending=PENDING)
    await h.send(message)
    assert h.bridge.accept_calls == []  # type: ignore[attr-defined]
    await h.send("好，去砍橡木原木")
    assert len(h.bridge.accept_calls) == 1  # type: ignore[attr-defined]
    assert h.bridge.wood_replies == [("IdCraft", "s", "砍橡木原木 2 根")]  # type: ignore[attr-defined]
    assert h.bridge._pending_handoffs == {}  # type: ignore[attr-defined]


# ---- Phase 8I FU5 Follow-up 3 §4：正向许可接受语法（**完整消费**）----

#: 整段被许可语法完整消费的单任务接受描述（§3.1 的采集类表达 + 已审计收尾语/语气词）。
PERMISSIVE_ACCEPTANCES = (
    "去砍橡木原木",
    "去砍原木",
    "去砍两根橡木原木",
    "去砍橡木原木带回来",
    "去砍橡木原木再回来",
    "去砍橡木原木吧",
    "帮我砍点橡木原木",
)

#: §3.3 + §5.3：**未被许可语法解释**的剩余文本（第二个目标、未知尾部、无关闲聊）。
UNEXPLAINED_TAILS: tuple[tuple[str, str], ...] = (
    ("好，去砍橡木原木去挖石头", "石头"),
    ("好，去砍橡木原木探索附近", "探索附近"),
    ("好，去砍橡木原木挖附近的石头", "附近石头"),
    ("好，去砍橡木原木，帮我挖附近的石头", "附近石头"),
    ("好，去砍橡木原木再去挖石头", "石头"),
    ("好，去砍橡木原木，帮我找附近的煤", "找附近煤"),
    ("好，去砍橡木原木并跟我走", "并跟走"),
    ("好，去砍橡木原木，然后探索附近", "然后探索附近"),
    ("好，去砍橡木原木然后去钓鱼吧", "然后钓鱼"),
    ("好，去砍橡木原木挖附近的青金石", "附近青金石"),
    ("好，去砍橡木原木顺手把栅栏修了", "顺手把栅栏修"),
)


@pytest.mark.parametrize("message", PERMISSIVE_ACCEPTANCES)
def test_fu5fu3_permissive_grammar_fully_consumes(message: str) -> None:
    """§4：整段都能被许可语法解释 → 未解释尾部为空，允许作为单一接受。"""
    assert unexplained_acceptance_tail(message) == ""
    assert is_permissive_wood_acceptance(message) is True


@pytest.mark.parametrize(("message", "tail"), UNEXPLAINED_TAILS)
def test_fu5fu3_unexplained_tail_is_never_accepted(message: str, tail: str) -> None:
    """§5.3：未解释的剩余文本必须被如实指出（未知尾部**不得**默认为无害）。"""
    rest = _acceptance_remainder(message)
    assert rest, message
    assert unexplained_acceptance_tail(rest) == tail
    assert is_permissive_wood_acceptance(rest) is False
    assert handoff_target_verdict(message, PENDING) == NOT_ACCEPT


def test_fu5fu3_non_wood_text_is_not_applicable() -> None:
    """不是木材描述的文本 → ``None``（交给既有语义，不由许可语法裁定）。"""
    assert unexplained_acceptance_tail("去挖石头") is None
    assert unexplained_acceptance_tail("就按这个方案来") is None
