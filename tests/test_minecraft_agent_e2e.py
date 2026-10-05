"""自然语言真实闭环（Phase 3E §三十九）：用户说话 → 模型选 Tool → Service → Runtime。

这里跑的是**真实**的 Tool 链路（ToolRegistry + ToolOrchestrator + policy + 真实
ToolRuntime 的 schema/预算/循环守卫），只有两处是替身：

* 模型 = :class:`tests.ai_mocks.MockAIProvider`（脚本化决定调用哪个 Tool）——
  CI 不能打真模型，但「模型决定 → Tool → Service」这条路径完全是真的；
* MinecraftService = 假 Service（同形异步方法 + 可编排的返回）——
  真 runtime 的行为由 `minecraft_runtime/test/e2e.js`（flying-squid）负责。

安全断言（§五十）：dig/place/attack/craft 这类工具**不存在**，模型调用只会得到
「未知工具」；LOW 动作没有用户请求就会被拒；Action 事件绝不自动开启新的 Agent Turn。
"""

from __future__ import annotations

import json
import time
from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.character.relationship import Relationship
from app.character.state import CharacterState
from app.config.settings import AIConfig, ToolsConfig
from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge
from app.tools.models import ToolContext
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_tools import (
    FakeMinecraftService,
    event,
    make_bridge,
)

# ---------------------------------------------------------------- 测试台


def decide(tool: str, **arguments: Any) -> str:
    payload = {"tool_call": {"name": tool, "arguments": arguments, "reason": "用户要求"}}
    return json.dumps(payload, ensure_ascii=False)


async def make_stack(
    *,
    script: list[str],
    service: FakeMinecraftService | None = None,
    explicit: bool = True,
    max_calls_per_turn: int = 3,
) -> tuple[ToolRuntime, AIEngine, MockAIProvider, MinecraftAgentBridge, ToolContext]:
    runtime = ToolRuntime(
        ToolsConfig(enabled=True, decision_mode="json", max_calls_per_turn=max_calls_per_turn)
    )
    await runtime.start()
    provider = MockAIProvider(name="mock", behaviors={"A": list(script)})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        providers={"mock": provider},
    )
    minecraft = service or FakeMinecraftService()
    bridge = MinecraftAgentBridge(minecraft)
    context = ToolContext(
        user_id="10001",
        session_id="s1",
        metadata=({BRIDGE_KEY: bridge, INTENT_KEY: True} if explicit else {BRIDGE_KEY: bridge}),
    )
    return runtime, engine, provider, bridge, context


async def say(
    runtime: ToolRuntime, engine: AIEngine, context: ToolContext, text: str
) -> tuple[str, list[Any]]:
    return await runtime.orchestrator.run(
        engine,
        [ChatMessage.user(text)],
        context=context,
        query=text,
    )


# ------------------------------------------------------------- Test A/B/C


async def test_test_a_come_here_reads_world_then_moves() -> None:
    """「罐头，你过来。」→ minecraft_world → minecraft_move_to（空凛的位置）。"""
    service = FakeMinecraftService()
    runtime, engine, provider, bridge, context = await make_stack(
        script=[
            decide("minecraft_world"),
            decide("minecraft_move_to", x=126, y=64, z=-228),
            "我这就过去～",
        ],
        service=service,
    )
    text, results = await say(runtime, engine, context, "罐头，你过来。")

    assert text == "我这就过去～"
    assert [result.tool_name for result in results] == ["minecraft_world", "minecraft_move_to"]
    assert all(result.success for result in results), [r.error for r in results]
    # §四十一：坐标来自世界感知（空凛在 126/-228），不是猜的
    assert service.action_calls("move_to") == [{"x": 126, "y": 64, "z": -228}]
    move = results[-1]
    assert move.data["status"] == "RUNNING" and move.data["action_id"] == "act_move_1"
    # 模型的第二轮决策确实看到了世界结果（提示里带上了 structured data）
    assert (
        "空凛" in provider.calls[1]["last_user"]
        or "minecraft_world" in provider.calls[1]["messages"][-1].content
    )


async def test_test_b_follow_me_returns_running_immediately() -> None:
    """「罐头跟着我。」→ minecraft_follow_player(username) 立刻 RUNNING。"""
    service = FakeMinecraftService()
    runtime, engine, _provider, bridge, context = await make_stack(
        script=[
            decide("minecraft_world"),
            decide("minecraft_follow_player", username="空凛", distance=2.5),
            "好呀，我跟着你。",
        ],
        service=service,
    )
    started = time.perf_counter()
    text, results = await say(runtime, engine, context, "罐头跟着我。")
    elapsed = time.perf_counter() - started

    assert text == "好呀，我跟着你。"
    assert service.action_calls("follow_player") == [{"username": "空凛", "distance": 2.5}]
    follow = next(result for result in results if result.tool_name == "minecraft_follow_player")
    assert follow.success and follow.data["status"] == "RUNNING"
    # §三十七：跟随最长 120s，工具请求绝不能等它跑完
    assert elapsed < 2.0, f"follow 工具请求不该阻塞（{elapsed:.2f}s）"


async def test_test_c_stop_cancels_the_running_follow() -> None:
    """「停。」→ minecraft_stop 取消正在跑的 follow（忙也不拦 stop）。"""
    service = FakeMinecraftService()
    service.enqueue("stop", {"ok": True, "status": "IDLE", "cancelled": ["act_follow_1"]})
    runtime, engine, _provider, bridge, context = await make_stack(
        script=[decide("minecraft_stop"), "好，我停下了。"],
        service=service,
    )
    bridge.context.apply_event(
        event("minecraft.action.started", action="follow_player", action_id="act_follow_1")
    )
    text, results = await say(runtime, engine, context, "停。")

    assert text == "好，我停下了。"
    assert service.action_calls("stop"), "stop 必须真的调用 Service"
    assert results[0].success and results[0].data["cancelled"] == ["act_follow_1"]
    assert "act_follow_1" in results[0].summary


# ----------------------------------------------------------- 安全边界


async def test_model_cannot_dig_place_attack_or_craft() -> None:
    """§五十：这些 Tool 压根不存在——模型调用只会得到「未知工具」。"""
    service = FakeMinecraftService()
    for forbidden in ("minecraft_dig", "minecraft_place", "minecraft_attack", "minecraft_craft"):
        runtime, engine, _provider, _bridge, context = await make_stack(
            script=[decide(forbidden, x=1, y=2, z=3), "我不会做那个。"],
            service=service,
        )
        registry = runtime.registry
        assert registry.maybe_get(forbidden) is None
        _text, results = await say(runtime, engine, context, "去挖点矿")
        assert results and results[0].success is False
        assert results[0].error_type == "unknown_tool"
    assert service.calls == [], "被禁止的工具不该产生任何 Minecraft 调用"


async def test_low_action_is_rejected_in_an_autonomous_turn() -> None:
    """§十五：没有用户请求的回合里，模型自己决定移动也会被拒。"""
    service = FakeMinecraftService()
    runtime, engine, _provider, _bridge, context = await make_stack(
        script=[decide("minecraft_move_to", x=1, y=2, z=3), "（没有真的动）"],
        service=service,
        explicit=False,
    )
    _text, results = await say(runtime, engine, context, "（自己想想）")
    assert results and results[0].success is False
    assert results[0].error_type == "minecraft.action_not_allowed"
    assert service.calls == []


async def test_safe_action_still_works_without_explicit_intent() -> None:
    """SAFE 动作（查世界）在自主回合里依然可用——自主不等于不能看。"""
    service = FakeMinecraftService()
    runtime, engine, _provider, _bridge, context = await make_stack(
        script=[decide("minecraft_world"), "我看看。"],
        service=service,
        explicit=False,
    )
    _text, results = await say(runtime, engine, context, "你那边有什么？")
    assert results and results[0].success is True


async def test_repeated_identical_move_is_stopped_by_the_loop_guard() -> None:
    """§四十/§四十六：同一个动作不许原地重复调用，且被拦住也要让回合收敛。

    LOOP_MIN_REPEATS=2 → 第三次相同的调用是最后一次真的执行；之后被循环守卫拦住，
    拦下的尝试照样记账 → 本轮预算一到，Agent 回合立刻结束（不会一直轰模型）。
    """
    service = FakeMinecraftService()
    runtime, engine, _provider, _bridge, _context = await make_stack(
        script=[decide("minecraft_move_to", x=130, y=64, z=-230)] * 8,
        service=service,
        max_calls_per_turn=4,
    )
    _text, results = await say(runtime, engine, _context, "罐头，你过来。")
    assert len(service.action_calls("move_to")) == 3, "第三次之后必须被循环守卫拦住"
    assert any(result.error_type == "loop_detected" for result in results)
    assert len(results) <= 4, "被拦下的调用也要消耗回合预算（否则会一直转）"


async def test_action_events_never_start_a_new_agent_turn() -> None:
    """§二十一/§四十六：事件只更新 Minecraft Context，绝不自动再叫一次模型。"""
    service = FakeMinecraftService()
    runtime, engine, provider, bridge, context = await make_stack(
        script=[decide("minecraft_move_to", x=130, y=64, z=-230), "我这就过去～"],
        service=service,
    )
    await say(runtime, engine, context, "罐头，你过来。")
    calls_after_turn = len(provider.calls)
    assert calls_after_turn >= 2

    # 先走一遍完整生命周期：started → completed
    bridge.apply_event(event("minecraft.action.started", action="move_to", action_id="act_move_1"))
    assert bridge.context.current_action is not None
    bridge.apply_event(
        event(
            "minecraft.action.completed",
            action="move_to",
            action_id="act_move_1",
            status="SUCCEEDED",
            result={"final_position": {"x": 130, "y": 64, "z": -230}},
        )
    )
    assert len(provider.calls) == calls_after_turn, "事件不得触发新的模型调用"
    assert bridge.context.current_action is None
    assert bridge.context.activity == "刚走到 (130, 64, -230)"
    assert "刚走到" in bridge.context_line()

    # 再来一次失败：只更新 last_action，仍然不叫模型
    bridge.apply_event(
        event("minecraft.action.failed", action="move_to", action_id="act_x", status="FAILED")
    )
    assert len(provider.calls) == calls_after_turn
    assert bridge.context.activity == "刚走到 (130, 64, -230)"  # 上一次失败不改写「刚做过的事」
    assert "没成功" in bridge.context_line()


async def test_context_line_reaches_the_model_prompt() -> None:
    """§十九：她此刻在游戏里的处境随每轮上下文进入 prompt（一行，不是快照）。"""
    from app.character.context import CharacterContextBuilder

    bridge = make_bridge(FakeMinecraftService())
    bridge.context.apply_event(
        event("minecraft.action.started", action="follow_player", action_id="act_1")
    )
    line = bridge.context_line()
    builder = CharacterContextBuilder()
    from tests.test_context_builder import PERSONA

    messages = builder.build(
        PERSONA,
        CharacterState(mood="happy", activity="发呆"),
        Relationship(user_id="1", stage="close", interaction_count=3),
        [],
        [],
        "在干嘛呢",
        minecraft=line,
        context_trace={},
    )
    system = next(message for message in messages if message.role == "system")
    assert "Minecraft" in system.content and "空凛" in system.content
    assert line in system.content
