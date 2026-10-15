"""Phase 8F §9.1：合成路由的安全边界（真实聊天桥 + 真实决策契约；只有计划层是替身）。

这些用例证明：合成请求只在 TASK_PROPOSAL 分支里被接手；歧义/未知/否定/零数量与
计划层不可用都有**零副作用**的明确归宿；合成路径绝不回落到旧的单步工具链。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.models import ExpectedFinalState, TaskPlan, TaskStep
from app.tasks.turn_decision import TurnDecisionKind
from tests.test_minecraft_chat_bridge import stack
from tests.test_phase8e_followup3_routing_matrix import no_side_effects, wire
from tests.turn_decision_stub import TurnDecisionStub

USER = "IdCraft8F"
STICK_ID = "stick*4=oak_planks*2"


class CraftPlans:
    """计划层替身：记录调用参数，按用例返回 created / rejected。"""

    def __init__(self, *, created: bool = True, reply: str = "") -> None:
        self.calls: list[dict[str, Any]] = []
        self.created = created
        self.reply = reply

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        if not self.created:
            return {
                "action": "plan_rejected",
                "reply": self.reply or "材料不够：还缺 1 个橡木原木，所以我什么都没做。",
                "outcome": "BLOCKED_BY_PRECONDITION",
                "plan": None,
                "record": None,
            }
        plan = TaskPlan(
            objective=str(kwargs.get("objective") or ""),
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_craft",
                    arguments={"recipe_id": STICK_ID},
                    risk="MEDIUM",
                ),
                TaskStep(step_id="step_2", tool="minecraft_inventory", arguments={}, risk="SAFE"),
            ],
            expected_final_state=ExpectedFinalState(
                inventory_delta={"stick": 4},
                inventory_consumed={"oak_planks": 2},
            ),
        )
        result = SimpleNamespace(
            checks=(
                {
                    "check": "plan",
                    "ok": True,
                    "detail": {
                        "item": "stick",
                        "requested": 4,
                        "batches": 1,
                        "produced": 4,
                        "capped": False,
                        "default_batch": False,
                        "materials": {"oak_planks": 2},
                    },
                },
            )
        )
        return {
            "action": "created",
            "reply": "",
            "plan": result,
            "record": SimpleNamespace(task_id="t_craft_8f", plan=plan),
        }


async def _route(env: Any, message: str) -> Any:
    await env.say(USER, message)
    return env.runtime.routes.last


@pytest.mark.asyncio
async def test_craft_request_reaches_the_plan_layer_with_the_craft_shape(tmp_path: Any) -> None:
    """肯定请求 → 既有计划链（shape_hint=craft）；规划阶段零工具、零世界修改。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        craft = CraftPlans()
        env.chat.bot.agent_plans = craft
        route = await _route(env, "帮我用橡木原木做一根木棍")
        assert route.reason_code == "plan_created", route.reason_code
        assert str(route.route_target) == "TASK"
        assert route.task_id == "t_craft_8f"
        assert craft.calls and craft.calls[0]["shape_hint"] == "craft"
        assert env.tools.executor.traces == [], "规划阶段不得执行任何工具"
        assert turns.calls == 0, "合成请求不得交给旧的单目标资源入口"


@pytest.mark.asyncio
async def test_plan_layer_rejection_is_reported_honestly_with_zero_side_effects(
    tmp_path: Any,
) -> None:
    """材料不足等由计划层如实拒绝 → 澄清路由，零工具、零任务。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        env.chat.bot.agent_plans = CraftPlans(created=False)
        route = await _route(env, "帮我用橡木原木做一根木棍")
        # 既有回复层对「计划被拒」的稳定原因码就是 plan_<action>（本阶段沿用，不改）
        assert route.reason_code == "plan_plan_rejected", route.reason_code
        assert str(route.route_target) == "CLARIFY"
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_ambiguous_planks_clarifies_without_touching_the_plan_layer(tmp_path: Any) -> None:
    """只说「木板」= 树种歧义 → 澄清；**绝不**替用户挑树种，也绝不进计划层。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        craft = CraftPlans()
        env.chat.bot.agent_plans = craft
        route = await _route(env, "帮我做一点木板")
        assert route.reason_code == "craft_ambiguous_item", route.reason_code
        assert str(route.route_target) == "CLARIFY"
        assert craft.calls == []
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_negated_craft_is_never_upgraded_even_when_the_model_says_task_proposal(
    tmp_path: Any,
) -> None:
    """否定语气 → 明确不做（模型判成 TASK_PROPOSAL 也拦得住）。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        craft = CraftPlans()
        env.chat.bot.agent_plans = craft
        route = await _route(env, "别做木棍了")
        assert route.reason_code == "craft_negated", route.reason_code
        assert str(route.route_target) == "CHAT_ONLY"
        assert craft.calls == []
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_unknown_target_and_zero_count_both_clarify(tmp_path: Any) -> None:
    """§9.1 用例 3/12：目标未知与「0 个」都澄清（不猜、不建任务）。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        craft = CraftPlans()
        env.chat.bot.agent_plans = craft
        unknown = await _route(env, "帮我做点东西")
        assert unknown.reason_code == "craft_unknown_item", unknown.reason_code
        zero = await _route(env, "做 0 个橡木木板")
        assert zero.reason_code == "craft_zero_count", zero.reason_code
        assert craft.calls == []
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_wood_gathering_requests_are_not_hijacked_by_the_craft_route(tmp_path: Any) -> None:
    """采集请求（有木材动作词、没有合成动词）仍归木材入口。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        env.chat.bot.agent_plans = CraftPlans()
        route = await _route(env, "帮我砍一根橡木原木带回来")
        assert not str(route.reason_code).startswith("craft_"), route.reason_code
        assert str(route.reason_code).startswith("wood_") or route.reason_code in {
            "plan_service_unavailable",
            "decision_unsupported",
        }
        assert env.tools.executor.traces == [], "规划阶段不得执行任何工具"


@pytest.mark.asyncio
async def test_missing_plan_layer_is_blocked_and_never_falls_back_to_tools(tmp_path: Any) -> None:
    """计划层没装配 → 明确 BLOCKED（绝不回落到旧的单步工具链）。"""
    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        env.chat.bot.agent_plans = None
        route = await _route(env, "帮我做一根木棍")
        assert route.reason_code == "plan_service_unavailable", route.reason_code
        assert str(route.route_target) == "BLOCKED"
        no_side_effects(env, plans, turns)
