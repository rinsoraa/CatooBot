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

from app.tasks.intent import (
    TaskIntentDetector,
    WoodNegation,
    classify_wood_negation,
    is_wood_request,
    parse_wood_request,
)
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


class WoodIntentStub:
    """替身解释器：沿用 Phase 8D Follow-up 2 的确定性结论，映射成解释器结果形状。

    Phase 8E 把「木材意图判定」收敛为**单一决策源**（解释器），所以这些既有契约
    测试通过注入替身继续断言原来的端到端行为（零计划/澄清/计划层错误等）。
    它只验证**路由契约**，不证明模型能力；旧判定失手的两种说法（`能不能别砍` /
    `我不砍`）在 `test_phase8e_wood_intent.py` 里单独覆盖。
    """

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def interpret(self, *, message: str, **_: Any) -> Any:
        self.seen.append(message)
        return _stub_result(message)


def _result(intent: str, polarity: str, **extra: Any) -> Any:
    fields: dict[str, Any] = {
        "intent": intent,
        "polarity": polarity,
        "target_species": None,
        "requested_count": None,
        "task_control": None,
        "needs_clarification": False,
        "reason_code": intent,
    }
    fields.update(extra)
    return SimpleNamespace(**fields)


def _stub_result(message: str) -> Any:
    verdict = classify_wood_negation(message)
    if verdict is WoodNegation.NEGATED:
        return _result("negated_request", "negative", reason_code="negated_request")
    if verdict is WoodNegation.AMBIGUOUS:
        return _result(
            "ambiguous_intent", "mixed", needs_clarification=True, reason_code="ambiguous_intent"
        )
    if not is_wood_request(message):
        # 叙述/回忆/纯查询不算指令（沿用 Follow-up 2 的确定性门槛）。
        return _result("narration", "unknown", reason_code="narration")
    wood = parse_wood_request(message)
    if wood is not None and wood.problem:
        return _result(
            "wood_collection_request",
            "positive",
            requested_count=wood.requested,
            reason_code="wood_collection_request",
        )
    return _result("wood_collection_request", "positive", reason_code="wood_collection_request")


def wire(env: Any, *, turns: WoodTurns, plans: Any) -> None:
    bot = env.chat.bot
    bot.task_turns = turns
    bot.agent_plans = plans
    env.chat.wood_intent = WoodIntentStub()


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

    monkeypatch.setattr(turn_module, "plan_collection_task", _boom)
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
        # Phase 8E Follow-up §4.2/§12.4：叙述走普通对话语义 —— **不经过**
        # 可创建任务的旧入口（此前断言 turns.calls == 1，那正是被修的回落路径）。
        assert turns.calls == 0, "叙述绝不能落到旧任务入口"
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


# ============================================================ Phase 8D Follow-up §B 回归


async def test_imperative_unsupported_request_is_blocked_and_never_falls_back(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§B 缺陷 2：命令句（无礼貌词）也必须拒绝，绝不落回旧单目标模板。"""
    import app.tasks.turn as turn_module

    def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("旧单目标 plan_resource_task 不应该被木材请求调用")

    monkeypatch.setattr(turn_module, "plan_collection_task", _boom)
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "去砍两根竹子带回来")
        assert turns.calls == 0, "命令句同样不得绕过木材入口"
        assert plans.calls == [], "不支持的木材不得进入计划层"
        route = last_route(env)
        assert route.reason_code == "wood_unsupported_species"
        assert route.route_target == RouteTarget.BLOCKED.value
        assert env.tools.executor.traces == [], "不得发生任何工具执行"


async def test_imperative_mixed_species_clarifies_without_picking_one(tmp_path: Any) -> None:
    """§B：命令句里的多树种必须澄清，不得自动选橡木。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "去砍两根橡木和云杉原木带回来")
        assert turns.calls == 0
        assert plans.calls == []
        route = last_route(env)
        assert route.reason_code == "wood_ambiguous_species"
        assert route.route_target == RouteTarget.CLARIFY.value
        assert env.tools.executor.traces == []


async def test_narration_with_imperative_marker_free_phrasing_stays_narration(
    tmp_path: Any,
) -> None:
    """§B：过去时叙述保持普通语义（即使句中有木材动作词）。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "我刚刚砍了两根竹子")
        # Phase 8E Follow-up §4.2：同上 —— 叙述不进任务入口。
        assert turns.calls == 0, "过去时叙述绝不能落到旧任务入口"
        assert plans.calls == []


async def test_zero_count_clarifies_instead_of_creating_a_task(tmp_path: Any) -> None:
    """§C：「0 根」必须澄清，不得静默变成 1 根任务。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "砍0根橡木原木")
        assert plans.calls == []
        route = last_route(env)
        assert route.reason_code == "wood_zero_count"
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


async def test_overbudget_request_clarifies_without_a_smaller_executable_plan(
    tmp_path: Any,
) -> None:
    """R3: no silent change from the requested five logs to a two-log plan."""
    async with stack(tmp_path, "角色回答。") as env:
        turns = WoodTurns()
        plans = WoodPlans(result=wood_created(count=2, capped=True))
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "帮我砍五根橡木原木带回来")
        assert turns.calls == 0
        assert plans.calls == []
        session = env.chat._session_id("IdSmoke8D")
        history = await env.chat.bot.ai.conversations.get_context(session)
        blob = " ".join(str(getattr(item, "content", "")) for item in history)
        assert "oak_log x2" not in blob
        assert "最多采 2 根" in blob
        assert last_route(env).reason_code == "resource_quantity_out_of_budget"
        assert env.tools.executor.traces == []


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


@pytest.mark.parametrize(
    "message",
    [
        "帮我砍一根紫杉原木带回来",
        "帮我砍二〇二根白桦原木带回来",
        "帮我采集一根云杉原木再采集原木",
    ],
)
async def test_shared_contract_vetoes_legacy_wood_parameter_guesses(
    tmp_path: Any, message: str
) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", message)
        assert turns.calls == 0 and plans.calls == []
        assert env.tools.executor.traces == []
        assert last_route(env).route_target == RouteTarget.CLARIFY.value


# ============================================================ Phase 8D Follow-up 2：否定护栏

NEGATED_LINES = [
    "不要砍两根橡木原木带回来",
    "我不想砍两根橡木原木带回来",
    "别砍两根橡木原木带回来",
    "先别砍两根橡木原木带回来",
    "暂时别砍两根橡木原木带回来",
    "我不打算砍两根橡木原木带回来",
    "不用帮我砍两根橡木原木带回来",
    "我不需要砍两根橡木原木带回来",
    "不要砍两根竹子带回来",
    "不要砍树",
]

AMBIGUOUS_LINES = [
    "不要砍橡木，改砍云杉",
    "不要砍橡木原木，砍云杉原木",
    "不是不想砍两根橡木原木带回来",
]


@pytest.mark.parametrize("text", NEGATED_LINES)
async def test_negated_wood_is_taken_over_with_zero_action(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """明确否定/暂缓：入口必须**明确接管**并给出非执行结论（绝不回落旧路径）。"""
    import app.tasks.turn as turn_module

    def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("否定请求不得进入旧单目标 plan_resource_task")

    monkeypatch.setattr(turn_module, "plan_collection_task", _boom)
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", text)
        assert plans.calls == [], "否定请求绝不能调用木材计划服务"
        assert turns.calls == 0, "否定由入口接管，不再交回任务入口"
        assert env.tools.executor.traces == [], "否定请求必须零 Minecraft 工具执行"
        route = last_route(env)
        assert route.reason_code == "wood_negated_intent"
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.task_id == ""


@pytest.mark.parametrize("text", AMBIGUOUS_LINES)
async def test_ambiguous_negation_clarifies_with_zero_action(
    tmp_path: Any, monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    """否定与肯定目标冲突／双重否定：CLARIFY，零计划零任务零工具，不替用户挑树种。"""
    import app.tasks.turn as turn_module

    def _boom(*_: Any, **__: Any) -> Any:
        raise AssertionError("冲突意图不得进入旧单目标 plan_resource_task")

    monkeypatch.setattr(turn_module, "plan_collection_task", _boom)
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", text)
        assert plans.calls == []
        assert turns.calls == 0
        assert env.tools.executor.traces == []
        route = last_route(env)
        assert route.reason_code == "wood_negation_ambiguous"
        assert route.route_target == RouteTarget.CLARIFY.value
        assert route.task_id == ""


async def test_negation_never_leaks_raw_text_into_route(tmp_path: Any) -> None:
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "不要砍两根橡木原木带回来")
        blob = str(env.runtime.routes.snapshot())
        assert "不要砍" not in blob
        route = last_route(env)
        assert route.text_chars == len("不要砍两根橡木原木带回来")


async def test_positive_wood_requests_still_reach_the_plan_layer(tmp_path: Any) -> None:
    """C 组：肯定请求仍然进入既有有界木材计划（shape_hint=wood），确认前零动作。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "去砍两根橡木原木带回来")
        assert turns.calls == 0
        assert len(plans.calls) == 1
        assert plans.calls[0]["shape_hint"] == "wood"
        assert env.tools.executor.traces == []
        route = last_route(env)
        assert route.reason_code == "plan_created"
        assert route.route_target == RouteTarget.TASK.value


async def test_negated_query_line_never_creates_a_collection_task(tmp_path: Any) -> None:
    """D 组：带否定的「我只问附近有什么树」不得建采集任务、不得调用工具。"""
    async with stack(tmp_path, "角色回答。") as env:
        turns, plans = WoodTurns(), WoodPlans()
        wire(env, turns=turns, plans=plans)
        await env.say("IdSmoke8D", "我不想砍木头，我只是问附近有什么树")
        assert plans.calls == []
        assert env.tools.executor.traces == []
        route = last_route(env)
        assert route.reason_code in ("wood_negated_intent", "wood_negation_ambiguous")
