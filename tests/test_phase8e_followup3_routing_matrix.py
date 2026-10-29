"""Phase 8E Follow-up 3 — §9.2 全入口路由矩阵（九类决策 × 真实哨兵）。

每一个自然语言消息都必须经过**统一决策入口**，然后落到某一类**已审计**路径上；本文件逐类断言
「谁接走了这句话」「有没有裸工具调用」「有没有建任务」「有没有产生 action_id」。

真实哨兵：CharacterRuntime / ToolRuntime / ToolExecutor / Policy / MinecraftAgentBridge /
MinecraftService 全是真的（`tests.test_minecraft_chat_bridge.stack`）；只有 AI provider 脚本化、
任务入口与计划层用最小替身（它们记录的调用次数就是哨兵）。
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.turn import TaskTurnOutcome
from app.tasks.turn_decision import TurnDecisionKind
from app.tools.models import RouteTarget
from tests.test_minecraft_chat_bridge import stack
from tests.turn_decision_stub import TurnDecisionStub

USER = "示例好友"


class SpyAgent:
    """AgentRuntime 面的哨兵：任何一次 handle_control/run 都必须被记下来。"""

    def __init__(self) -> None:
        self.control_calls: list[Any] = []
        self.run_calls: list[Any] = []

    async def handle_control(self, *args: Any, **kwargs: Any) -> Any:
        self.control_calls.append((args, kwargs))
        return SimpleNamespace(handled=False)

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        self.run_calls.append((args, kwargs))
        raise AssertionError("统一路由下，Minecraft 聊天回合不得进入 AgentRuntime.run")


class CountingTurns:
    """既有任务入口替身：有 detector/current_task，并记录 handle 次数。"""

    def __init__(self, *, handled: bool = False, action: str = "created") -> None:
        from app.tasks.intent import TaskIntentDetector

        self.detector = TaskIntentDetector()
        self.handled = handled
        self.action = action
        self.calls = 0

    async def current_task(self, session_id: str) -> Any:
        return None

    async def handle(self, *, session_id: str, user_id: str, text: str, **_: Any) -> Any:
        self.calls += 1
        if not self.handled:
            return TaskTurnOutcome(False)
        return TaskTurnOutcome(True, action=self.action, task_id="t_matrix", reply="任务侧回答。")


class CountingPlans:
    """计划层替身：记录 plan_goal_from_user 的调用（一律拒绝，矩阵只关心「有没有被叫」）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(kwargs)
        return {
            "action": "plan_rejected",
            "reply": "这件事我还没法安全地安排。",
            "outcome": "UNSUPPORTED",
            "plan": None,
            "record": None,
        }


def wire(
    env: Any, *, decision: Any, turns: CountingTurns | None = None
) -> tuple[Any, CountingTurns]:
    turns = turns or CountingTurns()
    plans = CountingPlans()
    spy = SpyAgent()
    env.chat.bot.task_turns = turns
    env.chat.bot.agent_plans = plans
    env.chat.turn_decisions = decision
    env.runtime.agent = spy
    return plans, turns


def no_side_effects(env: Any, plans: CountingPlans, turns: CountingTurns) -> None:
    """统一的零副作用断言：零工具、零 Agent、零任务、零 action_id。"""
    assert env.tools.executor.traces == [], "不得有任何工具执行"
    assert env.runtime.agent.control_calls == [], "不得进入 AgentRuntime.handle_control"
    assert env.runtime.agent.run_calls == [], "不得进入 AgentRuntime.run"
    assert plans.calls == [], "不得进入计划层"
    assert turns.calls == 0, "不得进入既有任务入口"
    for item in env.runtime.routes.snapshot():
        assert not str(item.get("task_id") or ""), f"不得产生 task_id：{item}"


# ---------------------------------------------------------------- 九类：终止型


@pytest.mark.parametrize("kind", [TurnDecisionKind.CHAT, TurnDecisionKind.NARRATION])
async def test_chat_and_narration_are_chat_only(tmp_path: Any, kind: Any) -> None:
    async with stack(tmp_path, "嗯嗯～") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(kind))
        await env.say(USER, "今天天气不错呀")
        assert env.chat_replies, "CHAT_ONLY 回合仍要回话"
        route = env.runtime.routes.last
        assert route is not None and route.route_target == RouteTarget.CHAT_ONLY.value
        no_side_effects(env, plans, turns)


async def test_clarify_is_reported_and_has_no_side_effects(tmp_path: Any) -> None:
    async with stack(tmp_path, "嗯嗯～") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(
                TurnDecisionKind.CLARIFY, clarification_question="你是想让我去砍树吗？"
            ),
        )
        await env.say(USER, "那个东西你看着办")
        assert env.chat_replies == ["你是想让我去砍树吗？"]
        assert env.runtime.routes.last.route_target == RouteTarget.CLARIFY.value
        no_side_effects(env, plans, turns)


async def test_unsupported_is_blocked_with_zero_side_effects(tmp_path: Any) -> None:
    async with stack(tmp_path, "嗯嗯～") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.UNSUPPORTED))
        await env.say(USER, "帮我把整片森林清空")
        assert env.runtime.routes.last.route_target == RouteTarget.BLOCKED.value
        no_side_effects(env, plans, turns)


# ---------------------------------------------------------------- 只读与单步能力


async def test_query_runs_exactly_one_audited_read_only_capability(tmp_path: Any) -> None:
    async with stack(tmp_path, "附近有橡树。") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(
                TurnDecisionKind.QUERY, capability_id="minecraft_world", arguments={}
            ),
        )
        await env.say(USER, "现在是什么时候")
        traces = env.tools.executor.traces
        assert len(traces) == 1, f"恰好一次已审计能力：{traces}"
        assert traces[0].tool_name == "minecraft_world"
        assert env.runtime.routes.last.route_target == RouteTarget.MINECRAFT_TOOL.value
        assert turns.calls == 0
        assert plans.calls == []
        assert env.runtime.agent.control_calls == []


async def test_atomic_action_runs_exactly_one_audited_capability(tmp_path: Any) -> None:
    async with stack(tmp_path, "停下来了。") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(
                TurnDecisionKind.ATOMIC_ACTION, capability_id="minecraft_stop", arguments={}
            ),
        )
        await env.say(USER, "别动了")
        traces = env.tools.executor.traces
        assert len(traces) == 1, f"恰好一次已审计能力：{traces}"
        assert traces[0].tool_name == "minecraft_stop"
        assert turns.calls == 0 and plans.calls == []


async def test_medium_capability_cannot_be_smuggled_in_as_atomic_action(tmp_path: Any) -> None:
    """契约层就拒绝：minecraft_dig 不在动作白名单里（构造即 schema 失败）。"""
    async with stack(tmp_path, "嗯嗯～") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        from pydantic import ValidationError

        from app.tasks.turn_decision import TurnDecision

        with pytest.raises(ValidationError):
            TurnDecision(
                kind=TurnDecisionKind.ATOMIC_ACTION.value,
                capability_id="minecraft_dig",
                arguments={},
            )
        await env.say(USER, "帮我挖点石头")
        no_side_effects(env, plans, turns)


# ---------------------------------------------------------------- 任务与计划层


async def test_task_proposal_consults_the_existing_entry_first(tmp_path: Any) -> None:
    async with stack(tmp_path, "任务侧回答。") as env:
        # 注意：消息里**不能出现木材词**（那会被确定性木材链按 §4.3 接管）。
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(
                TurnDecisionKind.TASK_PROPOSAL, task_goal="把那边一堆石头搬过来"
            ),
            turns=CountingTurns(handled=True),
        )
        await env.say(USER, "帮我把那边一堆石头搬过来")
        assert turns.calls == 1, "既有任务入口必须先被咨询"
        assert plans.calls == [], "入口接管后不得再进计划层"
        assert env.tools.executor.traces == []
        assert env.runtime.routes.last.task_id == "t_matrix"


async def test_task_proposal_falls_to_the_plan_layer_when_entry_declines(tmp_path: Any) -> None:
    async with stack(tmp_path, "这件事我还没法安全地安排。") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(
                TurnDecisionKind.TASK_PROPOSAL, task_goal="去附近转转探索一下"
            ),
        )
        await env.say(USER, "去附近转转探索一下")
        assert turns.calls == 1, "既有入口先被咨询"
        assert len(plans.calls) == 1, "入口不认领 → 计划层"
        assert env.tools.executor.traces == [], "计划层失败绝不回落裸工具"
        assert env.runtime.routes.last.route_target == RouteTarget.CLARIFY.value


# ---------------------------------------------------------------- 控制与失败


@pytest.mark.parametrize("kind", [TurnDecisionKind.CANCEL, TurnDecisionKind.STOP])
async def test_cancel_and_stop_go_to_the_existing_control_path(tmp_path: Any, kind: Any) -> None:
    async with stack(tmp_path, "好，停了。") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(kind),
            turns=CountingTurns(handled=True, action="cancelled"),
        )
        await env.say(USER, "确认")
        assert turns.calls == 1, "控制必须由既有入口处理"
        assert plans.calls == [] and env.tools.executor.traces == []


async def test_control_prefix_never_waits_for_the_model(tmp_path: Any) -> None:
    """§7.1：控制词在句首时，**不等待模型**（解释器一次都不调用）。"""
    async with stack(tmp_path, "嗯嗯～") as env:
        decision = TurnDecisionStub(TurnDecisionKind.CHAT)
        plans, turns = wire(
            env, decision=decision, turns=CountingTurns(handled=True, action="cancelled")
        )
        await env.say(USER, "取消，别砍树了")
        assert turns.calls == 1, "控制命令必须直达既有入口"
        assert decision.calls == [], "控制命令不得调用决策模型"
        assert plans.calls == [] and env.tools.executor.traces == []


async def test_decision_failure_is_an_honest_clarify(tmp_path: Any) -> None:
    async with stack(tmp_path, "嗯嗯～") as env:
        plans, turns = wire(
            env,
            decision=TurnDecisionStub(TurnDecisionKind.CHAT, reason_code="decision_timeout"),
        )
        await env.say(USER, "帮我砍两根橡木原木带回来")
        assert env.runtime.routes.last.route_target == RouteTarget.CLARIFY.value
        no_side_effects(env, plans, turns)


async def test_each_turn_uses_its_own_decision(tmp_path: Any) -> None:
    """§7.3：决策对象与路由结果**按回合传递**，绝不缓存复用（两回合两类决策）。"""
    async with stack(tmp_path, "嗯嗯～", "好。") as env:
        plans, turns = wire(env, decision=TurnDecisionStub(TurnDecisionKind.CHAT))
        await env.say(USER, "今天天气不错")
        env.chat.turn_decisions = TurnDecisionStub(TurnDecisionKind.UNSUPPORTED)
        await env.say(USER, "帮我把整片森林清空")
        targets = [str(item.get("route_target") or "") for item in env.runtime.routes.snapshot()]
        assert RouteTarget.CHAT_ONLY.value in targets, targets
        assert RouteTarget.BLOCKED.value in targets, targets
        no_side_effects(env, plans, turns)


async def test_non_wood_message_uses_the_decision_not_the_wood_interpreter(tmp_path: Any) -> None:
    async with stack(tmp_path, "嗯嗯～") as env:
        decision = TurnDecisionStub(TurnDecisionKind.CHAT)
        plans, turns = wire(env, decision=decision)
        wood = SimpleNamespace(calls=0)

        async def _decide(**_kwargs: Any) -> Any:
            wood.calls += 1
            raise AssertionError("非木材消息不得走木材解释器")

        env.chat.wood_intent = SimpleNamespace(decide=_decide)
        env.chat.wood_intent.calls = 0
        await env.say(USER, "今天天气不错")
        assert len(decision.calls) == 1, "统一决策恰好一次"
        no_side_effects(env, plans, turns)
