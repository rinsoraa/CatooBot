"""Phase 5A §四十二/§四十三/§六十：用户回合 → 任务动作（识别 + 控制命令）。

要钉死两件事：

1. **普通对话仍然是普通对话** —— "你在哪""背包里有什么""附近有铁矿吗"这类请求
   一律不创建任务、也不做任何观察；
2. 需要跨步骤的请求 → 先 SAFE 观察再生成**冻结计划**，让用户确认；确认/暂停/继续/取消
   直接作用于当前任务（不新增任何 minecraft_task_* 工具）。
"""

from __future__ import annotations

import time
from typing import Any

import pytest

from app.tasks.intent import TaskIntentDetector
from app.tasks.models import StepState, TaskPlan, TaskState
from app.tasks.runtime import TaskAuthorizationError, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from app.tasks.turn import TaskTurnHandler, block_for

SESSION = "minecraft:127.0.0.1:25565:空凛"
USER = "空凛"


class FakeConfirmations:
    """计划确认门的内存替身（只记录，不做真正的用户回合判断）。"""

    def __init__(self) -> None:
        self.created: list[dict[str, Any]] = []
        self.consumed: list[str] = []

    async def request(self, **kwargs: Any) -> str:
        self.created.append(dict(kwargs))
        return f"confirm_{len(self.created)}"

    async def consume(self, confirmation_id: str, **kwargs: Any) -> tuple[bool, str]:
        self.consumed.append(confirmation_id)
        return True, ""

    async def cancel(self, confirmation_id: str) -> None:
        return None


class Recorder:
    """假的步骤调用器：同步动作直接成功，持续型动作给一个 action_id。"""

    def __init__(self, **detached: str) -> None:
        self.calls: list[dict[str, Any]] = []
        self._detached = set(detached)
        self._inventory_reads = 0

    async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
        self.calls.append({"tool": tool, "arguments": dict(arguments), **kwargs})
        if tool in self._detached:
            return TaskInvocation(ok=True, status="RUNNING", action_id=f"act_{tool}")
        if tool == "minecraft_dropped_items":
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={
                    "ok": True,
                    "items": [{"entity_id": 42, "item": {"name": "oak_log", "count": 1}}],
                },
            )
        if tool == "minecraft_inventory":
            # 第一次是"计划确认时的基线"，之后是"挖到之后的重读"
            self._inventory_reads += 1
            count = 0 if self._inventory_reads == 1 else 1
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={"ok": True, "items": [{"name": "oak_log", "count": count}]},
            )
        return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})


def _registry() -> dict[str, Any]:
    return {
        "minecraft_move_to": {"type": "object"},
        "minecraft_dig": {"type": "object"},
        "minecraft_inventory": {"type": "object"},
        "minecraft_dropped_items": {"type": "object"},
        "minecraft_pickup_item": {"type": "object"},
        "minecraft_stop": {"type": "object"},
    }


def make_runtime(invoke: Any) -> tuple[TaskRuntime, FakeConfirmations]:
    confirmations = FakeConfirmations()
    runtime = TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=confirmations,
        config=None,
        risk_of=lambda tool: {
            "minecraft_move_to": "LOW",
            "minecraft_dig": "MEDIUM",
            "minecraft_pickup_item": "MEDIUM",
        }.get(tool, "SAFE"),
        is_registered=lambda tool: tool in _registry(),
        schema_of=lambda tool: _registry().get(tool),
        validate_arguments=lambda schema, arguments: [],
    )
    return runtime, confirmations


def observations(
    *,
    matches: list[dict[str, Any]] | None = None,
    can_dig: bool = False,
    reason: str | None = "too_far",
) -> Any:
    """SAFE 观察替身（world / find_blocks / inventory / dig_capability）。"""
    found = (
        matches
        if matches is not None
        else [
            {
                "block": {"name": "oak_log"},
                "position": {"x": 12, "y": 64, "z": 9},
                "distance": {"goal_near": 3.0, "raw": 3.2},
            }
        ]
    )

    async def observe(tool: str, arguments: Any) -> TaskInvocation:
        if tool == "minecraft_world":
            return TaskInvocation(
                ok=True, result={"ok": True, "position": {"x": 10, "y": 64, "z": 8}}
            )
        if tool == "minecraft_find_blocks":
            return TaskInvocation(
                ok=True, result={"ok": True, "matches": found, "truncated": False}
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "held_item": {"name": "stone_pickaxe", "count": 1},
                    "items": [],
                },
            )
        if tool == "minecraft_dig_capability":
            return TaskInvocation(
                ok=True,
                result={"ok": True, "can_dig": can_dig, "reason": reason, "dig_time_ms": 900},
            )
        raise AssertionError(f"不该观察到这里：{tool}")

    return observe


def make_handler(invoke: Any, *, observe: Any = None) -> tuple[TaskTurnHandler, TaskRuntime]:
    runtime, _ = make_runtime(invoke)
    handler = TaskTurnHandler(runtime, observe=observe or observations())
    return handler, runtime


# --------------------------------------------------------------- 词表


def test_block_aliases_are_deterministic() -> None:
    assert block_for("去砍一棵橡树，挖一块原木并捡回来") == "minecraft:oak_log"
    assert block_for("帮我挖点石头") == "minecraft:stone"
    assert block_for("随便聊聊今天天气") == ""


# --------------------------------------------------------------- 普通对话


@pytest.mark.parametrize(
    "message",
    [
        "你现在在哪里？",
        "背包里有什么？",
        "看看附近有没有铁矿",
        "你好呀",
        "今天服务器的天气怎么样",
    ],
)
async def test_ordinary_talk_never_becomes_a_task(message: str) -> None:
    seen: list[str] = []

    async def observe(tool: str, arguments: Any) -> TaskInvocation:
        seen.append(tool)
        raise AssertionError("普通对话不该做任何观察")

    handler, _ = make_handler(Recorder(), observe=observe)
    outcome = await handler.handle(session_id=SESSION, user_id=USER, text=message)
    assert outcome.handled is False
    assert seen == []
    assert TaskIntentDetector().detect(message).is_task is False


# --------------------------------------------------------------- 创建任务


async def test_multi_step_request_creates_a_frozen_plan_and_asks_for_confirmation() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    outcome = await handler.handle(
        session_id=SESSION, user_id=USER, text="去砍一棵橡树，挖一块原木并捡回来"
    )
    assert outcome.handled is True and outcome.action == "created"
    assert outcome.state == TaskState.PENDING_CONFIRMATION.value
    assert "确认" in outcome.reply
    assert "oak_log" in outcome.reply, "确认摘要里必须写清要挖什么、在哪里"

    record = await runtime.get(outcome.task_id)
    assert record is not None
    assert record.state is TaskState.PENDING_CONFIRMATION
    tools = [step.tool for step in record.plan.steps]
    assert tools == [
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_dropped_items",
        "minecraft_pickup_item",
        "minecraft_inventory",
    ]
    assert record.plan.steps[1].arguments["expected_block"] == "oak_log"
    assert record.user_id == USER and record.session_id == SESSION
    # 计划阶段只做了 SAFE 观察，什么都没动世界
    assert record.authorization is None


async def test_second_request_while_busy_does_not_create_another_task() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    first = await handler.handle(
        session_id=SESSION, user_id=USER, text="去砍一棵橡树，挖一块原木并捡回来"
    )
    assert first.action == "created"
    second = await handler.handle(session_id=SESSION, user_id=USER, text="再去挖点石头带回来")
    assert second.handled is True and second.action == "busy"
    assert second.task_id == first.task_id
    assert await runtime.current(SESSION) is not None


async def test_plan_failure_is_reported_honestly_without_creating_a_task() -> None:
    handler, runtime = make_handler(Recorder(), observe=observations(matches=[]))
    outcome = await handler.handle(
        session_id=SESSION, user_id=USER, text="去砍一棵橡树，挖一块原木并捡回来"
    )
    assert outcome.handled is True and outcome.action == "plan_failed"
    assert "做不了" in outcome.reply
    assert await runtime.current(SESSION) is None


async def test_a_block_that_cannot_be_dug_is_reported_instead_of_planned() -> None:
    handler, _ = make_handler(
        Recorder(), observe=observations(can_dig=False, reason="not_diggable")
    )
    outcome = await handler.handle(
        session_id=SESSION, user_id=USER, text="去砍一棵橡树，挖一块原木并捡回来"
    )
    assert outcome.handled is True and outcome.action == "plan_failed"


# --------------------------------------------------------------- 控制命令


async def _created(handler: TaskTurnHandler) -> str:
    outcome = await handler.handle(
        session_id=SESSION, user_id=USER, text="去砍一棵橡树，挖一块原木并捡回来"
    )
    assert outcome.action == "created"
    return outcome.task_id


async def test_confirm_starts_the_plan_and_pause_then_resume_follow() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    handler, runtime = make_handler(invoke)
    task_id = await _created(handler)

    confirmed = await handler.handle(session_id=SESSION, user_id=USER, text="确认")
    assert confirmed.handled is True and confirmed.action == "confirmed"
    record = await runtime.get(task_id)
    assert record is not None
    assert record.state is TaskState.WAITING_ACTION, "计划确认后真的开始执行第一步"
    assert record.authorization is not None, "整份计划的授权在这一刻签出"
    assert record.steps[0].state is StepState.WAITING_ACTION

    # §二十九：暂停先记在任务上，等当前动作**自然结束**才真的停（绝不在动作中间硬切）
    paused = await handler.handle(session_id=SESSION, user_id=USER, text="先停一下")
    assert paused.action == "paused"
    mid = await runtime.get(task_id)
    assert mid is not None and mid.pause_requested is True
    await runtime.on_action_event(
        action_id="act_minecraft_move_to", event="minecraft.action.completed", status="SUCCEEDED"
    )
    after_pause = await runtime.get(task_id)
    assert after_pause is not None and after_pause.state is TaskState.PAUSED

    resumed = await handler.handle(session_id=SESSION, user_id=USER, text="继续")
    assert resumed.action == "resumed"
    after_resume = await runtime.get(task_id)
    assert after_resume is not None
    assert after_resume.state is TaskState.SUCCEEDED, "继续之后把剩下的步骤做完"


async def test_cancel_stops_the_running_task_through_minecraft_stop() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    handler, runtime = make_handler(invoke)
    task_id = await _created(handler)
    await handler.handle(session_id=SESSION, user_id=USER, text="确认")

    cancelled = await handler.handle(session_id=SESSION, user_id=USER, text="停止这个任务")
    assert cancelled.handled is True and cancelled.action == "cancelled"
    after_cancel = await runtime.get(task_id)
    assert after_cancel is not None and after_cancel.state is TaskState.CANCELLED
    assert [call["tool"] for call in invoke.calls][-1] == "minecraft_stop", (
        "取消必须经 minecraft_stop 停掉前台动作（不直接操纵 Mineflayer）"
    )


async def test_confirm_without_any_task_is_just_chat() -> None:
    handler, _ = make_handler(Recorder())
    outcome = await handler.handle(session_id=SESSION, user_id=USER, text="确认")
    assert outcome.handled is False


async def test_control_command_does_not_touch_another_sessions_task() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    task_id = await _created(handler)
    other = await handler.handle(
        session_id="minecraft:127.0.0.1:25565:别人", user_id="别人", text="停止这个任务"
    )
    assert other.handled is False, "别的会话不该停掉这个任务"
    record = await runtime.get(task_id)
    assert record is not None and record.state is TaskState.PENDING_CONFIRMATION


async def test_confirm_failure_is_reported_instead_of_pretending() -> None:
    class RefusingConfirmations(FakeConfirmations):
        async def consume(self, confirmation_id: str, **kwargs: Any) -> tuple[bool, str]:
            return False, "minecraft.confirmation_expired"

    invoke = Recorder(minecraft_move_to="detached")
    runtime, _ = make_runtime(invoke)
    runtime._confirmations = RefusingConfirmations()  # noqa: SLF001 - 故意让确认过期
    handler = TaskTurnHandler(runtime, observe=observations())
    task_id = await _created(handler)
    outcome = await handler.handle(session_id=SESSION, user_id=USER, text="确认")
    assert outcome.handled is True and outcome.action == "confirm_failed"
    assert "没生效" in outcome.reply
    record = await runtime.get(task_id)
    assert record is not None and record.state is TaskState.PENDING_CONFIRMATION


async def test_authorization_errors_never_escape_as_exceptions() -> None:
    class BrokenRuntime(TaskRuntime):
        async def cancel(self, task_id: str, *, reason: str = "user cancel") -> Any:
            raise TaskAuthorizationError("坏掉了", code="task.internal")

    runtime, _ = make_runtime(Recorder())
    handler = TaskTurnHandler(runtime, observe=observations())
    task_id = await _created(handler)
    runtime.cancel = BrokenRuntime.cancel.__get__(runtime, type(runtime))  # type: ignore[method-assign]
    outcome = await handler.handle(session_id=SESSION, user_id=USER, text="停止这个任务")
    assert outcome.handled is True and outcome.action == "cancel_failed"
    assert "task.internal" in outcome.reply
    assert task_id


def test_intent_detector_reports_reasons_for_audit() -> None:
    intent = TaskIntentDetector().detect("去砍一棵橡树，挖一块原木并捡回来")
    assert intent.is_task is True
    assert any(reason.startswith("verb:") for reason in intent.reasons)
    assert any(reason.startswith("multi:") for reason in intent.reasons)
    assert any(reason.startswith("resource:") for reason in intent.reasons)
    assert intent.to_payload()["is_task"] is True


def test_task_plan_and_last_result_are_json_friendly() -> None:
    """审计用的计划快照必须是可序列化的纯数据（WebUI/日志都要用）。"""
    plan = TaskPlan(objective="挖木头")
    payload = plan.hash_payload()
    assert payload["objective"] == "挖木头"
    assert payload["steps"] == []
    assert isinstance(payload["expected_final_state"], dict)
    assert time.time() > 0
