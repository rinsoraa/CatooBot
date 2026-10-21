"""Phase 8E Follow-up 2 §6.1：非肯定木材路由的**单回合能力隔离**（CHAT_ONLY）。

阻断门禁（双执行面反例）：
  * 工具面：QUERY / NARRATION 回合即便模型输出工具调用 JSON，也不得被执行（traces 为空）；
  * Agent 面：同一回合不得调用 AgentRuntime.handle_control() / run()。

真实 CharacterRuntime / ToolRuntime / 真实 TaskTurnHandler / 真实桥接；只有模型、解释器、
计划层与 sentinel 是替身。CHAT_ONLY 是**每回合参数**：断言它没有写回任何共享配置。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.character.runtime import CharacterTurnMode
from app.tasks.wood_intent import Polarity, WoodIntent
from app.tools.models import RouteTarget
from tests.test_minecraft_chat_bridge import stack
from tests.test_phase8e_followup_routing_closure import (
    USER,
    WOOD_ASK,
    Env,
    PlanResourceSentinel,
    ScriptedInterpreter,
    real_entry,
    result,
    turn_module,
    wire,
)

TOOL_CALL_JSON = '{"tool": "minecraft_world", "arguments": {}}'
QUERY_ASK = "附近有什么木头？"


@pytest.fixture
def sentinel(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """§6.2：把旧单目标 ``plan_resource_task`` 换成可辨识 sentinel（本模块局部定义）。"""
    calls: list[Any] = []

    def boom(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise PlanResourceSentinel("plan_resource_task 不该被调用")

    monkeypatch.setattr(turn_module, "plan_collection_task", boom)
    return calls


class SpyAgent:
    """Agent 面探针：任何一次控制/运行调用都被记录（§6.1 阻断门禁）。"""

    def __init__(self) -> None:
        self.enabled = True
        self.control_calls: list[Any] = []
        self.run_calls: list[Any] = []

    async def handle_control(self, *args: Any, **kwargs: Any) -> Any:
        self.control_calls.append((args, kwargs))
        return None

    async def run(self, *args: Any, **kwargs: Any) -> Any:
        self.run_calls.append((args, kwargs))
        raise AssertionError("CHAT_ONLY 回合不得运行 AgentRuntime")


def build(chat: Any, handler: Any, runtime: Any, counter: dict[str, int], interpreter: Any) -> Env:
    env = Env(chat, handler, runtime, counter)
    env.interpreter = interpreter
    wire(chat, handler, env.plans, interpreter)
    return env


# ------------------------------------------------------------------ §6.1 工具面


@pytest.mark.parametrize(
    "message",
    [
        "先别动手。我只是问问：如果挖一个橡木原木，你会先检查哪些条件？",
        "不要砍两根橡木原木带回来",
        "不要砍橡木，改砍云杉原木带回来",
    ],
)
async def test_normal_character_turn_blocks_explicit_non_action_wood(tmp_path, message):
    """真实角色 NORMAL 路径也不得让 Agent/工具重新接管否定或询问。"""
    from app.character.turn import TurnOrigin

    async with stack(tmp_path, TOOL_CALL_JSON) as chat:
        spy = SpyAgent()
        chat.runtime.agent = spy
        reply = await chat.runtime.respond(
            "private:p3", "p3-user", message, turn_origin=TurnOrigin.USER
        )
        assert spy.control_calls == [] and spy.run_calls == []
        assert chat.tools.executor.traces == []
        assert '{"tool"' not in reply
        assert chat.runtime.routes.last.reason_code == "minecraft_non_action_turn"
        assert chat.tools.enabled is True


async def test_query_turn_cannot_reach_the_tool_surface(tmp_path: Any, sentinel: list[Any]) -> None:
    """模型输出工具调用 JSON 也只当纯文本：本回合**零工具执行**（M1 反例）。"""
    async with stack(tmp_path, TOOL_CALL_JSON) as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.QUERY, reason_code="query"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await chat.say(USER, QUERY_ASK)
        assert chat.turns == 1, "查询应当由角色自然语言回答"
        assert chat.tools.executor.traces == [], "CHAT_ONLY 回合不得执行任何工具"
        assert chat.chat_replies, "角色应当回了话"
        assert '{"tool"' not in "".join(chat.chat_replies), "工具调用 JSON 不得被原样发出"
        route = chat.runtime.routes.last
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.reason_code == "chat_only_turn"
        env.assert_no_side_effects()


# ------------------------------------------------------------------ §6.1 Agent 面


async def test_query_turn_cannot_reach_the_agent_surface(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """同一回合不得调用 AgentRuntime（M2 反例）；对照：普通回合仍会咨询 Agent。"""
    async with stack(tmp_path, "附近树挺多的呢～", "我没动手，只是说说。") as chat:
        handler, runtime, counter = real_entry()
        spy = SpyAgent()
        chat.runtime.agent = spy
        interpreter = ScriptedInterpreter(result(WoodIntent.QUERY, reason_code="query"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await chat.say(USER, QUERY_ASK)
        assert spy.control_calls == [], "CHAT_ONLY 回合不得调用 handle_control"
        assert spy.run_calls == [], "CHAT_ONLY 回合不得调用 AgentRuntime.run"
        assert chat.turns == 1
        # §4.1 语义变化（如实记录）：非木材消息现在也由**统一决策**接管，CHAT 类别 →
        # CHAT_ONLY 角色回合，因此 Minecraft 聊天路径不再进入 Agent 面。
        await chat.say(USER, "今天过得怎么样")
        assert spy.control_calls == [], "普通消息同样不再咨询 Agent（统一决策接管）"
        assert spy.run_calls == [], "普通消息同样不得进入 AgentRuntime.run"


# ------------------------------------------------------------------ 模式是每回合参数


async def test_chat_only_is_a_per_turn_parameter_not_global(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """CHAT_ONLY 只作用于这一次调用，不写回共享配置。"""
    async with stack(tmp_path, "嗯嗯～", "我在呢。") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.QUERY, reason_code="query"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        seen: list[Any] = []
        inner = chat.runtime.respond

        async def capture(*args: Any, **kwargs: Any) -> str:
            seen.append(kwargs.get("mode"))
            return await inner(*args, **kwargs)

        chat.runtime.respond = capture  # type: ignore[method-assign]
        await chat.say(USER, QUERY_ASK)
        await chat.say(USER, "今天天气不错呀")
        # §4.1 语义变化：Minecraft 聊天路径的回合**一律** CHAT_ONLY（不再有 NORMAL 的聊天回合），
        # 而隔离仍然只靠「每回合参数」——全局开关未被改写（下面两条断言保持不变）。
        assert seen, "这两个回合都必须经过角色侧"
        assert all(mode is CharacterTurnMode.CHAT_ONLY for mode in seen), seen
        assert chat.tools.enabled is True, "不得通过全局开关实现能力隔离"
        assert chat.runtime.tools is chat.tools


async def test_parallel_turns_do_not_share_the_mode(tmp_path: Any, sentinel: list[Any]) -> None:
    """并发回合不共享模式：恰好一个 chat_only_turn，另一个不是。"""
    async with stack(tmp_path, "嗯嗯～", "我在呢。") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.QUERY, reason_code="query"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await asyncio.gather(chat.say(USER, QUERY_ASK), chat.say(USER, "今天天气不错呀"))
        reasons = [str(item.get("reason_code") or "") for item in chat.runtime.routes.snapshot()]
        # §4.1 语义变化：Minecraft 聊天路径的**每个**回合都是 CHAT_ONLY（按回合传递），
        # 因此并发两回合各自留下自己的 chat_only_turn 记录；模式没有被全局改写。
        assert reasons.count("chat_only_turn") == 2, reasons
        assert "decision_chat" in reasons, reasons
        assert chat.tools.enabled is True, "不得通过全局开关实现能力隔离"
        assert chat.runtime.tools is chat.tools
        assert chat.turns == 2
        assert chat.tools.executor.traces == []


# ------------------------------------------------------------------ 非肯定分类：零角色回合


@pytest.mark.parametrize(
    ("message", "interpreter_result", "label"),
    [
        (WOOD_ASK, result(WoodIntent.OTHER, reason_code="other"), "OTHER"),
        (
            WOOD_ASK,
            result(WoodIntent.NEGATED_REQUEST, Polarity.NEGATIVE, reason_code="negated_request"),
            "NEGATED_REQUEST",
        ),
        (
            WOOD_ASK,
            result(
                WoodIntent.AMBIGUOUS_INTENT,
                Polarity.MIXED,
                needs_clarification=True,
                reason_code="ambiguous_intent",
            ),
            "AMBIGUOUS_INTENT",
        ),
        (
            WOOD_ASK,
            result(
                WoodIntent.OTHER,
                needs_clarification=True,
                reason_code="needs_clarification",
            ),
            "needs_clarification",
        ),
    ],
)
async def test_non_affirmative_never_enters_the_character_turn(
    tmp_path: Any, sentinel: list[Any], message: str, interpreter_result: Any, label: str
) -> None:
    """§6.3：非肯定分类必须直接结束路由 —— **零角色回合**、零工具、零计划、零任务。"""
    async with stack(tmp_path, "不该被用到的模型回复") as chat:
        handler, runtime, counter = real_entry()
        spy = SpyAgent()
        chat.runtime.agent = spy
        interpreter = ScriptedInterpreter(interpreter_result)
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await chat.say(USER, message)
        assert chat.turns == 0, f"{label}：不得产生角色回合"
        assert spy.control_calls == [] and spy.run_calls == []
        assert chat.tools.executor.traces == []
        env.assert_no_side_effects()


async def test_narration_is_chat_only_with_a_character_turn(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """NARRATION：允许角色自然语言回答，但本回合能力受限（§4.3 NORMAL_CHAT）。"""
    async with stack(tmp_path, "我只是在回忆呢～") as chat:
        handler, runtime, counter = real_entry()
        spy = SpyAgent()
        chat.runtime.agent = spy
        interpreter = ScriptedInterpreter(result(WoodIntent.NARRATION, reason_code="narration"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await chat.say(USER, "帮我砍一根云杉原木")
        assert chat.turns == 1, "叙述应当由角色自然语言回应"
        assert chat.tools.executor.traces == [], "CHAT_ONLY 回合不得执行工具"
        assert spy.control_calls == [] and spy.run_calls == [], "也不得进入 Agent"
        route = chat.runtime.routes.last
        assert route.route_target == RouteTarget.CHAT_ONLY.value
        assert route.reason_code == "chat_only_turn"
        env.assert_no_side_effects()


async def test_interpreter_failure_never_enters_the_character_turn(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """解释器抛异常 → fail-closed 澄清，零角色回合（§4.3）。"""
    async with stack(tmp_path, "不该被用到的模型回复") as chat:
        handler, runtime, counter = real_entry()
        spy = SpyAgent()
        chat.runtime.agent = spy
        interpreter = ScriptedInterpreter(error=RuntimeError("boom"))
        env = build(chat, handler, runtime, counter, interpreter)
        env.sentinel_calls = sentinel
        await chat.say(USER, WOOD_ASK)
        assert chat.turns == 0
        assert spy.control_calls == [] and spy.run_calls == []
        env.assert_no_side_effects()
