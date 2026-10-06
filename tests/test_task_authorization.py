"""Task 授权安全测试（Phase 5A §八十七：1–6 条必须钉死）。

这一层测的是"谁能放行什么"：计划确认只能来自真实用户回合、步骤授权必须与冻结计划一致、
系统/WebUI 永远不能自授权。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.integrations.minecraft.agent import (
    BRIDGE_KEY,
    INTENT_KEY,
    TASK_TOKEN_KEY,
    TURN_ORIGIN_KEY,
    MinecraftAgentBridge,
)
from app.integrations.minecraft.confirmation import ConfirmationStore
from app.tasks.models import StepState, TaskState
from app.tasks.runtime import TaskAuthorizationError, TaskConfig, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_task_runtime import FakeConfirmation, Recorder, simple_plan


def make_task_runtime(invoke: Any = None) -> tuple[TaskRuntime, Recorder, FakeConfirmation]:
    invoke = invoke or Recorder()
    confirmations = FakeConfirmation()
    runtime = TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=confirmations,
        config=TaskConfig(),
        risk_of=lambda tool: {
            "minecraft_inventory": "SAFE",
            "minecraft_find_blocks": "SAFE",
            "minecraft_move_to": "LOW",
            "minecraft_dig": "MEDIUM",
        }.get(tool, ""),
        is_registered=lambda tool: (
            tool
            in {
                "minecraft_inventory",
                "minecraft_find_blocks",
                "minecraft_move_to",
                "minecraft_dig",
            }
        ),
    )
    return runtime, invoke, confirmations


async def make_confirmed(runtime: TaskRuntime) -> Any:
    record = await runtime.create_task(
        "去附近找一棵橡木，挖一块原木并捡回来",
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=simple_plan(),
    )
    return await runtime.confirm_and_start(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )


# ------------------------------------------------------------------ 1：确认 Plan A 不能执行 Plan B


async def test_1_confirmed_plan_cannot_execute_a_different_plan() -> None:
    runtime, _, _ = make_task_runtime()
    record = await make_confirmed(runtime)
    other = simple_plan(steps=[("minecraft_move_to", {"x": 999, "y": 64, "z": 999})])
    # 另一个计划的 hash 不匹配 → 一律不放行
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_1",
            tool="minecraft_move_to",
            arguments={"x": 999, "y": 64, "z": 999},
            plan_hash=other.plan_hash,
        )
        is False
    )
    assert other.plan_hash != record.plan_hash


# ------------------------------------------------------------------ 2：步骤参数被改


async def test_2_changed_step_arguments_are_not_authorized() -> None:
    runtime, _, _ = make_task_runtime()
    record = await make_confirmed(runtime)
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_2",
            tool="minecraft_move_to",
            arguments={"x": 1, "y": 2, "z": 3},
            plan_hash=record.plan_hash,
        )
        is False
    ), "参数变了就不再是用户确认过的那一步"
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_2",
            tool="minecraft_dig",  # 工具也换了
            arguments={"x": 10, "y": 64, "z": 10, "expected_block": "oak_log"},
            plan_hash=record.plan_hash,
        )
        is False
    )


# ------------------------------------------------------------------ 3：计划 hash 变了


async def test_3_plan_change_invalidates_the_confirmation() -> None:
    runtime, _, confirmations = make_task_runtime()
    record = await runtime.create_task(
        "去附近找一棵橡木，挖一块原木并捡回来",
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=simple_plan(),
    )
    pending = confirmations.created[-1]
    # 用户确认的是这一份计划；计划被换成别的 → 那条确认不再匹配
    new_plan = simple_plan(steps=[("minecraft_move_to", {"x": 5, "y": 64, "z": 5})])
    ok, code = await confirmations.consume(
        record.confirmation_id,
        task_id=record.task_id,
        session_id="private:10001",
        user_id="10001",
        plan_hash=new_plan.plan_hash,
        arguments={"plan_hash": new_plan.plan_hash, "plan": new_plan.hash_payload()},
        origin="user",
    )
    assert ok is False and code == "minecraft.confirmation_mismatch"
    assert pending["plan_hash"] == record.plan_hash


# -------------------------------------------- 4/5：INITIATIVE / BACKGROUND 不能消费


async def test_4_initiative_turn_cannot_consume_the_task_authorization() -> None:
    runtime, _, _ = make_task_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    with pytest.raises(TaskAuthorizationError) as excinfo:
        await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="initiative"
        )
    assert excinfo.value.code == "task.confirmation_not_user_turn"


async def test_5_background_turn_cannot_consume_the_task_authorization() -> None:
    runtime, _, _ = make_task_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    with pytest.raises(TaskAuthorizationError) as excinfo:
        await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="background"
        )
    assert excinfo.value.code == "task.confirmation_not_user_turn"


# ------------------------------------------------------------------ 6：SYSTEM / WebUI 不能自授权


async def test_6_system_or_webui_cannot_self_authorize() -> None:
    runtime, _, _ = make_task_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    for origin in ("system", "task"):
        with pytest.raises(TaskAuthorizationError):
            await runtime.confirm_and_start(
                record.task_id, user_id="u", session_id="s", origin=origin
            )

    # 反过来：即使绕开 TaskRuntime，直接给 bridge 一个"自称已授权"的上下文也不放行 ——
    # 因为没有 TaskRuntime 装的校验器（fail-closed），任务步骤一律拒绝。
    service = FakeMinecraftService()
    bridge = MinecraftAgentBridge(service)
    from app.tools.models import ToolContext

    context = ToolContext(
        user_id="u",
        session_id="s",
        metadata={
            BRIDGE_KEY: bridge,
            TURN_ORIGIN_KEY: "task",
            INTENT_KEY: False,
            TASK_TOKEN_KEY: "forged-token",  # 伪造的凭据：bridge 表里没有 → 一律不放行
        },
    )
    tool_result = await bridge.invoke(
        "minecraft_move_to", {"x": 1, "y": 2, "z": 3}, lambda _svc: _never(), context=context
    )
    assert tool_result.success is False
    assert tool_result.error_type == "minecraft.task_authorization_missing"


async def _never() -> Mapping[str, Any]:  # pragma: no cover - 不该被调用
    raise AssertionError("没有授权的任务步骤绝不该执行到 Service")


async def test_task_turn_is_not_a_user_turn() -> None:
    """§十六：TASK 来源**不是**用户回合（is_user=False），所以不能拿它冒充 USER。"""
    from app.character.turn import TurnOrigin

    assert TurnOrigin.TASK.value == "task"
    assert TurnOrigin.TASK.is_user is False


async def test_bridge_denies_task_steps_without_a_verifier() -> None:
    """bridge 默认没有校验器 → 任务步骤一律拒绝（fail-closed）。"""
    service = FakeMinecraftService()
    bridge = MinecraftAgentBridge(service)
    result = await bridge.invoke_task_step(
        "minecraft_move_to",
        {"x": 1, "y": 2, "z": 3},
        task_id="task_x",
        step_id="step_1",
        plan_hash="h",
        risk="LOW",
        authorization=None,
        call=lambda _svc: _never(),
    )
    assert result.success is False
    assert result.error_type == "minecraft.task_authorization_missing"


async def test_bridge_accepts_a_step_the_runtime_authorizes() -> None:
    # 用"停在等待动作"的任务（非终态）来验放行；终态任务不该再授权任何步骤
    runtime, _, _ = make_task_runtime(Recorder(minecraft_move_to="detached"))
    record = await make_confirmed(runtime)
    service = FakeMinecraftService()
    bridge = MinecraftAgentBridge(service)
    bridge.set_task_authorizer(runtime.authorize_step)
    assert record.state is TaskState.WAITING_ACTION
    step = record.steps[1]
    result = await bridge.invoke_task_step(
        step.tool,
        step.effective_arguments,
        task_id=record.task_id,
        step_id=step.step_id,
        plan_hash=record.plan_hash,
        risk=step.risk,
        authorization=None,
        call=lambda svc: svc.move_to(step.arguments["x"], step.arguments["y"], step.arguments["z"]),
    )
    assert result.success is True, result.error
    assert result.data["status"] == "RUNNING"
    # 同样的调用，参数换成别的 → 立刻被拒
    denied = await bridge.invoke_task_step(
        step.tool,
        {"x": 1, "y": 2, "z": 3},
        task_id=record.task_id,
        step_id=step.step_id,
        plan_hash=record.plan_hash,
        risk=step.risk,
        authorization=None,
        call=lambda svc: svc.move_to(1, 2, 3),
    )
    assert denied.success is False
    assert denied.error_type == "minecraft.task_authorization_missing"


def test_confirmation_store_carries_task_binding_without_changing_direct_calls() -> None:
    """§四十/§四十一：同一个 ConfirmationStore，直接工具调用的载荷一个字节不变。"""

    store = ConfirmationStore(ttl_seconds=60, max_pending=8)
    direct = store.create(
        session_id="s", user_id="u", tool="minecraft_dig", risk="MEDIUM", arguments={"x": 1}
    )
    assert set(direct.to_payload()) == {
        "confirmation_id",
        "tool",
        "risk",
        "summary",
        "expires_at",
        "status",
    }
    task_scoped = store.create(
        session_id="s",
        user_id="u",
        tool="minecraft_task",
        risk="MEDIUM",
        arguments={"plan_hash": "abc"},
        summary="计划",
        task_id="task_1",
        plan_hash="abc",
    )
    payload = task_scoped.to_payload()
    assert payload["task_id"] == "task_1" and payload["plan_hash"] == "abc"


def test_task_state_enum_is_explicit() -> None:
    """§六：状态是显式枚举，没有 None / "running" / "done" 这种散落字符串。"""
    from app.tasks.models import StepState as SStep
    from app.tasks.models import TaskState as TState

    assert {state.value for state in TState} == {
        "PLANNING",
        "PENDING_CONFIRMATION",
        "RUNNING",
        "WAITING_ACTION",
        "WAITING_USER",
        "PAUSED",
        "REPLANNING",
        "SUCCEEDED",
        "FAILED",
        "CANCELLED",
        "EXPIRED",
    }
    assert {state.value for state in SStep} == {
        "PENDING",
        "RUNNING",
        "WAITING_ACTION",
        "WAITING_CONFIRMATION",
        "SUCCEEDED",
        "FAILED",
        "SKIPPED",
        "CANCELLED",
    }


async def test_webui_cannot_confirm_or_start_a_task() -> None:
    """WebUI 是 SYSTEM 回合：能看、能暂停/取消，但**不能**确认计划（§八十七 case 6）。"""
    runtime, _, _ = make_task_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    with pytest.raises(TaskAuthorizationError):
        await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="system"
        )
    snapshot = runtime.snapshot_payload(record)
    assert snapshot["state"] == TaskState.PENDING_CONFIRMATION.value
    assert snapshot["confirmation_required"] is True
    assert all(step["state"] == StepState.PENDING.value for step in snapshot["plan"]["steps"])
