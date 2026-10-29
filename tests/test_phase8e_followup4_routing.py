"""Phase 8E Follow-up 4：语义决策矩阵、查询闭环与控制非阻塞（任务书 §五/§六/§八）。

这些用例全部走**真实聊天桥 + 真实 TaskTurnHandler/ToolRuntime/Policy**；
零副作用一律用真实哨兵（工具 trace / Agent 调用记录 / 任务创建计数）证明，
而不是只看最终回复。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.tasks.turn_decision import TurnDecisionKind
from app.tasks.wood_intent import Polarity, WoodIntent
from tests.test_minecraft_chat_bridge import stack
from tests.test_phase8e_followup3_routing_matrix import (
    CountingPlans,
    CountingTurns,
    no_side_effects,
    wire,
)
from tests.test_phase8e_followup_routing_closure import ScriptedInterpreter
from tests.test_phase8e_followup_routing_closure import result as wood_result
from tests.turn_decision_stub import TurnDecisionStub

USER = "IdWood8D"


async def _turn(env: Any, message: str) -> Any:
    await env.say(USER, message)
    return env.runtime.routes.last


# ------------------------------------------------- 语义矩阵（任务书 §六 17 行）


@pytest.mark.asyncio
async def test_row1_mentioning_a_tall_tree_is_only_chat(tmp_path: Any) -> None:
    """①「这棵树真高啊」→ 聊天/叙述，不建木材任务（§5.1 删掉关键词闸后仍成立）。"""

    async with stack(tmp_path, "是挺高的") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        route = await _turn(env, "这棵树真高啊")
        assert str(route.route_target) == "CHAT_ONLY"
        # 先记决策路由（decision_chat），随后 CHAT_ONLY 角色回合会再记一条 chat_only_turn。
        assert route.reason_code in {"decision_chat", "chat_only_turn"}, route.reason_code
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row2_positive_wood_request_never_touches_tools_or_agent(tmp_path: Any) -> None:
    """②「帮我砍 8 个橡木原木」→ 走既有木材确定性链（计划层没装配时如实 BLOCKED）。"""

    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub())  # 替身按消息推导：木材→TASK_PROPOSAL
        route = await _turn(env, "帮我砍8个橡木原木")
        assert str(route.reason_code).startswith("wood_") or route.reason_code in (
            "plan_service_unavailable",
            "decision_unsupported",
        )
        assert env.tools.executor.traces == [], "规划阶段不得产生任何工具执行"
        assert plans.calls == [], "本用例不注入计划服务，木材链不应调用它"


@pytest.mark.asyncio
async def test_row3_and_row17_negated_wood_is_vetoed_even_when_llm_says_task_proposal(
    tmp_path: Any,
) -> None:
    """③/⑰「不要砍树」被 LLM 误判成 TASK_PROPOSAL 时，确定性安全约束仍必须拦住。"""

    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        env.chat.wood_intent = ScriptedInterpreter(
            wood_result(
                WoodIntent.NEGATED_REQUEST, Polarity.NEGATIVE, reason_code="negated_request"
            )
        )
        route = await _turn(env, "不要砍树")
        assert route.reason_code == "wood_negated_intent", route.reason_code
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row17_deterministic_veto_still_blocks_when_the_wood_model_is_unusable(
    tmp_path: Any,
) -> None:
    """⑰ 木材解释器坏掉/输出非法时，确定性链仍必须拦住木材任务（安全失败）。"""

    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        env.chat.wood_intent = ScriptedInterpreter(error=RuntimeError("provider down"))
        route = await _turn(env, "帮我砍一根橡木原木带回来")
        assert str(route.reason_code).startswith("wood_intent"), route.reason_code
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row4_insufficient_wood_is_not_an_automatic_harvest(tmp_path: Any) -> None:
    """④「木头不够了」→ 不默认采集（LLM 说 CHAT 时就是聊天；说 TASK_PROPOSAL 时由木材链澄清）。"""

    async with stack(tmp_path, "嗯嗯") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        await _turn(env, "木头不够了")
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row5_and_row16_no_active_task_is_not_guessed(tmp_path: Any) -> None:
    """⑤/⑯「继续刚才的工作」但没有活动任务 → 澄清，绝不回落到 Agent/Tool 执行。"""

    async with stack(tmp_path, "嗯") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.TASK_PROPOSAL))
        await _turn(env, "继续刚才的工作")
        assert turns.calls >= 1, "TASK_PROPOSAL 必须先咨询既有任务入口"
        assert env.tools.executor.traces == []
        assert env.runtime.agent.run_calls == [] and env.runtime.agent.control_calls == []


@pytest.mark.asyncio
async def test_row14_unsupported_place_and_attack_are_blocked(tmp_path: Any) -> None:
    """⑭ 放置/攻击请求 → UNSUPPORTED（BLOCKED），零工具调用。"""

    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.UNSUPPORTED))
        route = await _turn(env, "帮我在那边放个方块然后打死那只羊")
        assert str(route.route_target) == "BLOCKED"
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row11_illegal_decision_output_fails_safe(tmp_path: Any) -> None:
    """⑪ 非法决策输出（schema 失败）→ 安全澄清，零副作用。"""

    async with stack(tmp_path, "嗯") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(reason_code="decision_schema_invalid"))
        route = await _turn(env, "帮我砍一根橡木原木")
        assert route.reason_code == "decision_schema_invalid"
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_row12_tool_call_json_in_chat_never_executes(tmp_path: Any) -> None:
    """⑫ 普通聊天里出现工具调用 JSON → 零世界动作。"""

    async with stack(tmp_path, '{"tool": "minecraft_world", "arguments": {}}') as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        await _turn(env, "你觉得这个 JSON 是什么")
        assert env.tools.executor.traces == [], "聊天文本里的 JSON 绝不能被当成执行结果"
        no_side_effects(env, plans, turns)


# ------------------------------------------------- 查询闭环（任务书 §五 QUERY）


@pytest.mark.asyncio
async def test_world_query_reply_comes_from_the_real_tool_result(tmp_path: Any) -> None:
    """⑥ 世界查询闭环：真实只读工具 → 真实结果 → 回复依据真实结果。"""

    async with stack(tmp_path, "好") as env:
        plans, _turns = wire(
            env,
            decision=TurnDecisionStub(TurnDecisionKind.QUERY, capability_id="minecraft_world"),
        )
        seen: list[Any] = []
        render = env.chat._observed_text

        def spy(capability: str, result: Any) -> str:
            seen.append((capability, result))
            return render(capability, result)

        env.chat._observed_text = spy  # type: ignore[method-assign]
        await _turn(env, "看看附近有什么方块")
        assert [t.tool_name for t in env.tools.executor.traces] == ["minecraft_world"]
        assert seen and seen[0][0] == "minecraft_world"
        assert getattr(seen[0][1], "data", None), "只读工具必须返回真实数据"
        assert env.chat_replies[-1] == render("minecraft_world", seen[0][1])


@pytest.mark.asyncio
async def test_inventory_query_reply_comes_from_the_real_tool_result(tmp_path: Any) -> None:
    """⑦ 背包查询闭环：回复里的物品信息来自真实工具返回值，不是模型猜的。"""

    async with stack(tmp_path, "好") as env:
        wire(
            env,
            decision=TurnDecisionStub(TurnDecisionKind.QUERY, capability_id="minecraft_inventory"),
        )
        seen: list[Any] = []
        render = env.chat._observed_text

        def spy(capability: str, result: Any) -> str:
            seen.append((capability, result))
            return render(capability, result)

        env.chat._observed_text = spy  # type: ignore[method-assign]
        await _turn(env, "我的背包里有什么")
        assert [t.tool_name for t in env.tools.executor.traces] == ["minecraft_inventory"]
        data = getattr(seen[0][1], "data", {}) or {}
        assert "items" in data or "held_item" in data, f"背包查询必须拿到真实字段：{data}"
        assert env.chat_replies[-1] == render("minecraft_inventory", seen[0][1])
        assert env.runtime.agent.run_calls == [], "查询答复不得进入 Agent 执行面"


# ------------------------------------------------- 控制非阻塞（任务书 §八）


@pytest.mark.asyncio
async def test_stop_does_not_wait_for_a_hanging_provider(tmp_path: Any) -> None:
    """§八：模型挂起时，停止/取消仍然必须立刻到达既有控制路径。"""

    class Hanging(TurnDecisionStub):
        def __init__(self) -> None:
            super().__init__(TurnDecisionKind.CHAT)
            self.entered = 0

        async def decide(self, **kwargs: Any) -> Any:
            self.entered += 1
            await asyncio.sleep(30)  # 模型永远不回来
            return await super().decide(**kwargs)

    async with stack(tmp_path, "好") as env:
        hanging = Hanging()
        plans, turns = wire(env, decision=hanging)
        await asyncio.wait_for(_turn(env, "停下"), timeout=5.0)
        assert hanging.entered == 0, "控制回合绝不等待模型"
        assert turns.calls == 1 and plans.calls == [], "控制必须走既有入口"
        assert env.tools.executor.traces == []


@pytest.mark.asyncio
async def test_cancel_prefix_with_action_words_still_controls(tmp_path: Any) -> None:
    """⑬ 控制与行动诉求同句冲突 → 控制优先（且不产生任何动作）。"""

    async with stack(tmp_path, "好") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.ATOMIC_ACTION))
        await _turn(env, "取消，别砍树了")
        assert turns.calls == 1, "冲突时控制优先，交回既有入口"
        assert env.tools.executor.traces == [], "控制回合不得产生任何工具执行"
        assert plans.calls == [], "控制回合不得进入计划层"
        assert env.runtime.agent.run_calls == [] and env.runtime.agent.control_calls == []


@pytest.mark.asyncio
async def test_row15_concurrent_chat_and_action_stay_isolated(tmp_path: Any) -> None:
    """⑮ 并发聊天与行动请求：决策与回合模式互不污染。"""

    async with stack(tmp_path, "嗯", "好") as env:
        env.chat.bot.task_turns = CountingTurns()
        env.chat.bot.agent_plans = CountingPlans()
        env.chat.turn_decisions = TurnDecisionStub()
        route_a, route_b = await asyncio.gather(
            _turn(env, "今天天气不错呀"),
            _turn(env, "看看我的背包"),
        )
        reasons = [str(r.reason_code) for r in (route_a, route_b)]
        assert "chat_only_turn" in reasons or "decision_chat" in reasons
        assert len(env.tools.executor.traces) <= 1, "并发回合不得各自额外执行工具"


# ------------------------------------------------- §七 M2/M3 变异夹具（必须可被捕获）


@pytest.mark.asyncio
async def test_m2_fixture_chat_with_real_candidates_and_a_model_tool_call_stays_chat_only(
    tmp_path: Any,
) -> None:
    """M2 夹具：消息**有真实工具候选**，且脚本化模型**愿意发工具调用**。

    若把 CHAT 错误地送进 NORMAL 模式（= 变异），ToolOrchestrator 会真的执行
    ``minecraft_inventory``，下面的 ``traces == []`` 就会失败 —— 变异可被捕获。
    """

    async with stack(tmp_path, '{"tool": "minecraft_inventory", "arguments": {}}') as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        await _turn(env, "看看我的背包")
        assert env.tools.executor.traces == [], "聊天回合绝不允许执行工具"
        no_side_effects(env, plans, turns)


@pytest.mark.asyncio
async def test_m3_fixture_invalid_decision_never_falls_through_to_candidates(tmp_path: Any) -> None:
    """M3 夹具：非法决策输出时，即使候选与工具调用都就绪，也必须零执行。

    变异（失败后继续走普通路径）会真的执行工具 → 断言失败 → 变异可被捕获。
    """

    async with stack(tmp_path, '{"tool": "minecraft_inventory", "arguments": {}}') as env:
        plans, turns = wire(env, decision=TurnDecisionStub(reason_code="decision_schema_invalid"))
        await _turn(env, "看看我的背包")
        assert env.tools.executor.traces == [], "非法决策输出后绝不允许执行工具"
        assert env.chat_replies and env.chat_replies[-1], "必须给出安全澄清"
        no_side_effects(env, plans, turns)
