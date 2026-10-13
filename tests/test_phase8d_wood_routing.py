"""Phase 8D §7.4：木材请求在 MC 入口的**唯一接管**与如实文案（确定性、不用 sleep、不用真实 LLM）。

证明的核心事实：
  * 木材请求在**任务入口之前**被计划层接管 → 旧单目标 plan_resource_task 绝不会抢先接管；
  * 控制命令 / 状态查询 / 活动任务 busy / 闲聊 一律交回既有入口（行为与文案不变）；
  * 计划层缺失或出错时绝不返回 None 回落普通工具链（8C Follow-up 约束）；
  * 每个请求只创建一个任务、一份计划，并留下可关联的路由证据。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.intent import TaskIntentDetector
from app.tasks.turn import TaskTurnOutcome
from app.tools.models import RouteTarget
from tests.test_minecraft_chat_bridge import stack

WOOD_ASK = "帮我砍两根橡木原木带回来"


class WoodTurns:
    """任务入口替身：带 Phase 8D 的两个只读访问器，并记录是否被调用。"""

    def __init__(self, *, current: Any = None, handled: bool = False) -> None:
        self.detector = TaskIntentDetector()
        self._current = current
        self.handled = handled
        self.calls = 0

    async def current_task(self, session_id: str) -> Any:
        return self._current

    async def handle(self, *, session_id: str, user_id: str, text: str, **_: Any) -> Any:
        self.calls += 1
        if not self.handled:
            return TaskTurnOutcome(False)
        return TaskTurnOutcome(
            True, action="busy", task_id="t_busy", reply="我正忙着上一件事呢（既有 busy 文案）。"
        )


class WoodPlans:
    """计划层替身：记录调用参数，返回可编排的结果。"""

    def __init__(
        self, result: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.result = result
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result if self.result is not None else wood_created()


def wood_created(
    *, task_id: str = "task_8d", log: str = "oak_log", count: int = 1, capped: bool = False
) -> dict[str, Any]:
    """created 结果：record.plan 是**冻结计划**（含 inventory_delta），plan.checks 带细节。"""
    plan = SimpleNamespace(
        steps=[SimpleNamespace(step_id="step_1")],
        expected_final_state=SimpleNamespace(inventory_delta={log: count}),
    )
    detail = {"targets": count, "requested": count, "capped": capped, "truncated": capped}
    return {
        "action": "created",
        "reply": "木材摘要。",
        "outcome": "READY_FOR_APPROVAL",
        "plan": SimpleNamespace(
            target=SimpleNamespace(player_name=""), risk_summary={}, checks=[{"detail": detail}]
        ),
        "record": SimpleNamespace(task_id=task_id, plan=plan),
    }


def wire(env: Any, *, turns: WoodTurns, plans: Any) -> None:
    bot = env.chat.bot
    bot.task_turns = turns
    bot.agent_plans = plans


def last_route(env: Any) -> Any:
    route = env.runtime.routes.last
    assert route is not None, "每个被测试的请求都必须留下一条路由记录"
    return route


def terminal_task() -> Any:
    return SimpleNamespace(state=SimpleNamespace(terminal=True), task_id="t_done")


def busy_task() -> Any:
    return SimpleNamespace(state=SimpleNamespace(terminal=False), task_id="t_running")


async def test_wood_request_takes_over_before_the_task_entry(tmp_path: Any) -> None:
    """木材请求在任务入口之前被计划层接走（唯一接管）。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", WOOD_ASK)
        assert turns.calls == 0, "木材请求不得再进任务入口（否则旧单目标路径会抢先）"
        assert len(plans.calls) == 1, "每个请求只排一份计划"
        call = plans.calls[0]
        assert call["shape_hint"] == "wood"
        assert call["source"] == "minecraft"
        assert call["user_id"] == "IdSmoke8D"
        route = last_route(env)
        assert route.route_target == RouteTarget.TASK.value
        assert route.reason_code == "plan_created"
        assert route.task_id == "task_8d"


async def test_legacy_single_target_template_is_never_reached_for_wood(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """直接证明旧单目标模板不会被调用（调用即抛）。"""
    import app.tasks.turn as turn_module

    def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("旧单目标 plan_resource_task 不应该被木材请求调用")

    monkeypatch.setattr(turn_module, "plan_resource_task", _boom)
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", WOOD_ASK)
        assert turns.calls == 0
        assert len(plans.calls) == 1


async def test_control_command_still_goes_to_the_task_entry(tmp_path: Any) -> None:
    """控制命令（取消/确认…）绝不被木材分支抢走。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "取消，别砍树了")
        assert turns.calls == 1
        assert plans.calls == []


async def test_status_query_goes_to_the_entry_even_with_a_terminal_task(tmp_path: Any) -> None:
    """状态查询（有任务时）交回既有入口 —— 即使那个任务已经结束。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(current=terminal_task()), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "砍木头到哪了")
        assert turns.calls == 1
        assert plans.calls == []


async def test_active_task_keeps_the_existing_busy_path(tmp_path: Any) -> None:
    """会话里已有未终态任务 → 交回入口给出既有 busy 文案，不另排一份木材计划。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(current=busy_task(), handled=True), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", WOOD_ASK)
        assert turns.calls == 1
        assert plans.calls == []


async def test_chitchat_with_wood_words_is_not_hijacked(tmp_path: Any) -> None:
    """闲聊里出现木材词也不算指令（没有请求语气）。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "我昨天砍了两根竹子")
        assert turns.calls == 1
        assert plans.calls == []


async def test_unsupported_species_is_refused_without_the_plan_layer(tmp_path: Any) -> None:
    """不支持的树种：明确拒绝 + BLOCKED 证据，绝不静默映射成橡木。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "帮我砍两根竹子带回来")
        assert turns.calls == 0
        assert plans.calls == []
        route = last_route(env)
        assert route.reason_code == "wood_unsupported_species"
        assert route.route_target == RouteTarget.BLOCKED.value


async def test_ambiguous_species_clarifies(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "帮我砍两根橡木和云杉原木带回来")
        assert plans.calls == []
        route = last_route(env)
        assert route.reason_code == "wood_ambiguous_species"
        assert route.route_target == RouteTarget.CLARIFY.value


async def test_missing_plan_service_is_blocked_not_fallen_through(tmp_path: Any) -> None:
    """计划层没装配时也必须如实回答，绝不返回 None 回落工具链。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns = WoodTurns()
        wire(env, turns=turns, plans=None)
        await env.say("IdSmoke8D", WOOD_ASK)
        assert turns.calls == 0
        route = last_route(env)
        assert route.reason_code == "plan_service_unavailable"
        assert route.route_target == RouteTarget.BLOCKED.value


async def test_plan_layer_error_is_blocked_not_fallen_through(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        turns = WoodTurns()
        wire(env, turns=turns, plans=WoodPlans(error=RuntimeError("planner exploded")))
        await env.say("IdSmoke8D", WOOD_ASK)
        assert turns.calls == 0
        route = last_route(env)
        assert route.reason_code == "plan_service_error"
        assert route.route_target == RouteTarget.BLOCKED.value


async def test_summary_uses_the_frozen_plan_count_and_flags_capping(tmp_path: Any) -> None:
    """确认前的摘要必须来自**冻结计划**的真实数量，并如实说明超出上限。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns = WoodTurns()
        plans = WoodPlans(result=wood_created(count=2, capped=True))
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "帮我砍五根橡木原木带回来")
        assert turns.calls == 0
        assert len(plans.calls) == 1
        session = env.chat._session_id("IdSmoke8D")
        history = await env.chat.bot.ai.conversations.get_context(session)
        blob = " ".join(str(getattr(item, "content", "")) for item in history)
        assert "oak_log x2" in blob
        assert "本期一次最多" in blob
        assert "确认" in blob


async def test_no_raw_message_text_in_route_evidence(tmp_path: Any) -> None:
    """路由证据只留长度与稳定原因码，不留聊天原文。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", WOOD_ASK)
        blob = str(env.runtime.routes.snapshot())
        assert "帮我砍两根橡木原木带回来" not in blob
        assert "rt_" in blob
        assert last_route(env).text_chars == len(WOOD_ASK)
