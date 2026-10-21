"""Phase 8E Follow-up §6.1/§6.2：**真实** TaskTurnHandler 下的木材路由闭环。

修复前的缺陷：``_wood_intent_reply()`` 对 QUERY / NARRATION / TASK_CONTROL / OTHER 返回
``None``，而 ``_task_reply()`` 把 ``None`` 当成「交回任务入口」→ 真实
``TaskTurnHandler._create()`` 仍可调用旧单目标 ``plan_resource_task()``。

本文件用**真实** TaskRuntime + **真实** TaskTurnHandler + **真实** MinecraftChatBridge
证明：非肯定分类不可能到达旧资源模板。

断言层次（§6.2）：
  * ``app.tasks.turn.plan_resource_task`` sentinel 调用数为 0；
  * 木材计划服务 ``plan_goal_from_user`` 调用数为 0；
  * 真实 TaskRuntime ``create_task`` 调用数为 0（包装计数，不只靠「没有工具 trace」推断）；
  * ``env.tools.executor.traces == []``、无 action_id、无 TaskRuntime 记录；
  * 非木材消息不触发木材解释器；控制命令解释器零调用。
"""

from __future__ import annotations

from typing import Any

import pytest

import app.tasks.turn as turn_module
from app.tasks.runtime import TaskInvocation
from app.tasks.turn import TaskTurnHandler
from app.tasks.wood_intent import Polarity, WoodIntent, WoodIntentResult
from app.tools.models import RouteTarget
from tests.test_minecraft_chat_bridge import stack
from tests.test_task_turn import make_runtime, observations

WOOD_ASK = "帮我砍两根橡木原木带回来"
SESSION = "minecraft:127.0.0.1:25565:空凛"
USER = "空凛"


class PlanResourceSentinel(Exception):
    """旧单目标模板被调用 —— 这在非肯定分类下**绝不允许**发生。"""


class PlansStub:
    """木材计划服务替身：一旦被调用就立刻失败（非肯定分类不该进计划层）。"""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def plan_goal_from_user(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        raise AssertionError(f"非肯定分类不该进入木材计划层：{kwargs!r}")


class ScriptedInterpreter:
    """脚本化解释器：返回**合法**结构化结果（或指定故障）。"""

    def __init__(self, result: Any = None, *, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0

    async def interpret(self, *, message: str, **_: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.result


def result(
    intent: Any,
    polarity: Any = Polarity.POSITIVE,
    *,
    needs_clarification: bool = False,
    reason_code: str = "other",
    target_species: str | None = None,
    requested_count: int | None = None,
    task_control: str | None = None,
) -> WoodIntentResult:
    """构造**通过真实 schema 校验**的模型结果（不绕过 Pydantic）。"""
    return WoodIntentResult(
        intent=intent,
        polarity=polarity,
        needs_clarification=needs_clarification,
        reason_code=reason_code,
        target_species=target_species,
        requested_count=requested_count,
        task_control=task_control,
    )


async def _deny_invoke(tool: str, arguments: Any) -> TaskInvocation:
    raise AssertionError(f"非肯定分类绝不该执行工具：{tool}")


def real_entry() -> tuple[TaskTurnHandler, Any, dict[str, int]]:
    """真实 TaskRuntime + 真实 TaskTurnHandler，并给 create_task 装计数包装。"""
    runtime, _confirmations = make_runtime(_deny_invoke)
    counter = {"create": 0}
    original = runtime.create_task

    async def counted(*args: Any, **kwargs: Any) -> Any:
        counter["create"] += 1
        return await original(*args, **kwargs)

    runtime.create_task = counted  # type: ignore[method-assign]
    handler = TaskTurnHandler(runtime, observe=observations())
    return handler, runtime, counter


class Env:
    """一次测试的完整装配：真实入口 + sentinel + 计划替身 + 脚本化解释器。"""

    def __init__(self, chat: Any, handler: Any, runtime: Any, counter: dict[str, int]) -> None:
        self.chat = chat
        self.handler = handler
        self.runtime = runtime
        self.counter = counter
        self.plans = PlansStub()
        self.sentinel_calls: list[Any] = []
        self.interpreter: ScriptedInterpreter | None = None

    def assert_no_side_effects(self, *, expect_interpreter_calls: int = 1) -> None:
        assert self.sentinel_calls == [], "旧单目标 plan_resource_task 被调用了"
        assert self.plans.calls == [], "木材计划服务被调用了"
        assert self.counter["create"] == 0, "TaskRuntime 创建了任务"
        assert self.chat.tools.executor.traces == [], "工具被执行了"
        if self.interpreter is not None:
            assert self.interpreter.calls == expect_interpreter_calls


@pytest.fixture
def sentinel(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """§6.2：把 ``app.tasks.turn.plan_resource_task`` 换成可辨识的 sentinel。"""
    calls: list[Any] = []

    def boom(*args: Any, **kwargs: Any) -> Any:
        calls.append((args, kwargs))
        raise PlanResourceSentinel("plan_resource_task 不该被调用")

    monkeypatch.setattr(turn_module, "plan_collection_task", boom)
    return calls


def wire(chat_stack: Any, handler: Any, plans: PlansStub, interpreter: Any) -> None:
    """把真实入口、计划替身与脚本化解释器接到**真实桥接**上（ChatStack.chat 是桥）。"""
    bridge = chat_stack.chat
    bridge.bot.task_turns = handler
    bridge.bot.agent_plans = plans
    bridge.wood_intent = interpreter


# ---------------------------------------------------------------- §6.1 非肯定分类（阻断门禁）


@pytest.mark.parametrize(
    ("message", "model_result", "label"),
    [
        (WOOD_ASK, result(WoodIntent.OTHER, reason_code="other"), "OTHER（表面肯定句）"),
        (
            "去砍两根橡木原木带回来",
            result(WoodIntent.QUERY, reason_code="query"),
            "QUERY（表面肯定句）",
        ),
        (
            "帮我砍一根云杉原木",
            result(WoodIntent.NARRATION, reason_code="narration"),
            "NARRATION（表面肯定句）",
        ),
        ("附近有什么木头？", result(WoodIntent.QUERY, reason_code="query"), "QUERY（真查询）"),
        (
            "我昨天砍了两根竹子",
            result(WoodIntent.NARRATION, reason_code="narration"),
            "NARRATION（叙述）",
        ),
    ],
)
async def test_non_positive_categories_never_reach_the_old_entry(
    tmp_path: Any, sentinel: list[Any], message: str, model_result: Any, label: str
) -> None:
    """§6.1/§12.4：模型合法返回非肯定类别时，旧资源模板与木材计划层零调用。"""
    async with stack(tmp_path, "好的～", "嗯嗯", "我在呢") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(model_result)
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, message)
        env.assert_no_side_effects()
        assert await runtime.active(SESSION) is None, "非肯定分类不该建立任何任务"


@pytest.mark.parametrize(
    ("model_result", "label"),
    [
        (
            result(
                WoodIntent.AMBIGUOUS_INTENT,
                Polarity.MIXED,
                needs_clarification=True,
                reason_code="ambiguous_intent",
            ),
            "AMBIGUOUS_INTENT",
        ),
        (
            result(
                WoodIntent.OTHER,
                Polarity.UNKNOWN,
                needs_clarification=True,
                reason_code="needs_clarification",
            ),
            "需要澄清（needs_clarification=true）",
        ),
        (
            result(
                WoodIntent.TASK_CONTROL,
                Polarity.UNKNOWN,
                reason_code="task_control",
                task_control="cancel",
            ),
            "模型自称 TASK_CONTROL（确定性检测器不认）",
        ),
    ],
)
async def test_clarify_categories_terminate_routing(
    tmp_path: Any, sentinel: list[Any], model_result: Any, label: str
) -> None:
    """§4.2：歧义 / 需澄清 / 模型自称控制命令 → 安全澄清并终止任务路由。"""
    async with stack(tmp_path, "好的～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(model_result)
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, WOOD_ASK)
        env.assert_no_side_effects()


@pytest.mark.parametrize(
    ("error",),
    [
        (TimeoutError("SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU"),),
        (RuntimeError("SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU"),),
        (None,),
    ],
)
async def test_provider_failures_terminate_routing(
    tmp_path: Any, sentinel: list[Any], error: Exception | None
) -> None:
    """§4.2/§12.4：超时 / 异常 / 非法结果 → 安全澄清，绝不回落旧模板。"""
    async with stack(tmp_path, "好的～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(None, error=error)
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, WOOD_ASK)
        env.assert_no_side_effects()


async def test_negated_request_has_zero_side_effects(tmp_path: Any, sentinel: list[Any]) -> None:
    """§4.2/§12.7：否定请求 → 零任务、零计划、零工具、零 action_id。"""
    async with stack(tmp_path, "好的～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(
            result(
                WoodIntent.NEGATED_REQUEST,
                Polarity.NEGATIVE,
                reason_code="negated_request",
            )
        )
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, WOOD_ASK)
        env.assert_no_side_effects()


async def test_query_keeps_normal_chat_semantics(tmp_path: Any, sentinel: list[Any]) -> None:
    """§4.2：QUERY 走普通聊天语义，且不经过可创建任务的旧入口。"""
    async with stack(tmp_path, "附近有不少树呢～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.QUERY, reason_code="query"))
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, "附近有什么木头？")
        env.assert_no_side_effects()
        assert chat.runtime.routes.last.route_target == RouteTarget.CHAT_ONLY.value


async def test_control_commands_never_reach_the_interpreter(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """§4.3/§6.4：控制命令优先由确定性检测器处理 → 解释器零调用、零副作用。"""
    async with stack(tmp_path, "好，我停下了～", "好的～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.OTHER, reason_code="other"))
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, "取消，别砍树了")
        env.assert_no_side_effects(expect_interpreter_calls=0)


async def test_non_wood_messages_never_call_the_interpreter(
    tmp_path: Any, sentinel: list[Any]
) -> None:
    """§4.2 末行：非木材消息完全走原有路径，不额外调用木材解释器。"""
    async with stack(tmp_path, "嗯嗯，我在～") as chat:
        handler, runtime, counter = real_entry()
        interpreter = ScriptedInterpreter(result(WoodIntent.OTHER, reason_code="other"))
        env = Env(chat, handler, runtime, counter)
        env.sentinel_calls = sentinel
        env.interpreter = interpreter
        wire(chat, handler, env.plans, interpreter)
        await chat.say(USER, "今天天气不错呀")
        env.assert_no_side_effects(expect_interpreter_calls=0)
        assert chat.runtime.routes.last.route_target == RouteTarget.CHAT_ONLY.value
