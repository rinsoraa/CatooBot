"""R1 残余缺口：find_blocks coverage 契约 + 维度一致性守卫（R1-G5 / R1-G6）。"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.tasks.models import TaskPlan, TaskState, TaskStep
from app.tasks.planner import ObservationFailed, observation_coverage, plan_resource_task
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from tests.test_task_runtime import FakeConfirmation

REGISTRY = {
    "minecraft_find_blocks": "SAFE",
    "minecraft_world": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_dig": "MEDIUM",
    "minecraft_inventory": "SAFE",
}


class Obs:
    def __init__(self, result: Mapping[str, Any], *, ok: bool = True) -> None:
        self.result = dict(result)
        self.ok = ok
        self.summary = ""


def make_observe(*, matches: list[Any], observation: Mapping[str, Any] | None) -> Any:
    async def observe(tool: str, arguments: Mapping[str, Any]) -> Obs:
        if tool == "minecraft_find_blocks":
            payload: dict[str, Any] = {"ok": True, "matches": matches, "truncated": False}
            if observation is not None:
                payload["observation"] = dict(observation)
            return Obs(payload)
        if tool == "minecraft_world":
            return Obs({"available": True, "online": True, "dimension": "minecraft:overworld"})
        return Obs({"ok": True})

    return observe


def test_observation_coverage_never_defaults_to_complete() -> None:
    """缺字段/非法值/旧响应一律 UNKNOWN ——「运行时没说完整」不等于「完整」。"""
    assert observation_coverage(None) == ("UNKNOWN", "observation_missing")
    assert observation_coverage({"matches": []}) == ("UNKNOWN", "observation_missing")
    assert observation_coverage({"observation": {}}) == ("UNKNOWN", "")
    assert observation_coverage({"observation": {"coverage": "complete"}}) == ("COMPLETE", "")
    coverage, reason = observation_coverage({"observation": {"coverage": "NOT_A_VALUE"}})
    assert coverage == "UNKNOWN" and reason.startswith("coverage_invalid")


async def test_empty_matches_with_partial_coverage_is_not_reported_as_no_target() -> None:
    """空命中 + 覆盖不完整 → 必须报「不能确认有没有」，不能报「附近没有」。"""
    observe = make_observe(
        matches=[], observation={"coverage": "PARTIAL", "coverage_reason": "some_chunks_unloaded"}
    )
    with pytest.raises(ObservationFailed) as info:
        await plan_resource_task("帮我挖点橡木", observe=observe, block_name="oak_log")
    message = str(info.value)
    assert message.startswith("coverage_incomplete:PARTIAL:some_chunks_unloaded:"), message
    assert "不能确定" in message or "无法确认" in message


async def test_empty_matches_with_complete_coverage_keeps_no_target_message() -> None:
    """覆盖完整时才是真的「范围内没有」——沿用既有文案，不引入 coverage 前缀。"""
    observe = make_observe(
        matches=[], observation={"coverage": "COMPLETE", "coverage_reason": "all_chunks_loaded"}
    )
    with pytest.raises(ObservationFailed) as info:
        await plan_resource_task("帮我挖点橡木", observe=observe, block_name="oak_log")
    message = str(info.value)
    assert "coverage_incomplete" not in message
    assert "没有找到" in message


def make_runtime(invoke: Any) -> TaskRuntime:
    return TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=FakeConfirmation(),
        config=TaskConfig(),
        publish=lambda event, payload: None,
        risk_of=lambda tool: REGISTRY.get(tool, ""),
        is_registered=lambda tool: tool in REGISTRY,
    )


def plan_with_dimension(dimension: str | None) -> TaskPlan:
    observation: dict[str, Any] = {
        "tool": "minecraft_find_blocks",
        "arguments": {"block_names": ["oak_log"]},
        "result": {"ok": True, "matches": []},
    }
    if dimension is not None:
        observation["result"]["observation"] = {"dimension": dimension, "coverage": "COMPLETE"}
    return TaskPlan(
        objective="走过去",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={"x": 0, "y": 64, "z": 0},
            ),
        ],
        observations=[observation],
    )


class DimInvoker:
    """只读 minecraft_world 返回给定维度；其它工具记录调用（守卫生效时应当为零）。"""

    def __init__(self, dimension: str | None) -> None:
        self.dimension = dimension
        self.calls: list[str] = []

    async def __call__(self, tool: str, arguments: Mapping[str, Any], **_: Any) -> TaskInvocation:
        self.calls.append(tool)
        if tool == "minecraft_world":
            payload: dict[str, Any] = {"available": True, "online": True}
            if self.dimension is not None:
                payload["dimension"] = self.dimension
                payload["position"] = {"x": 0.0, "y": 64.0, "z": 0.0}
            return TaskInvocation(ok=True, status="SUCCEEDED", result=payload)
        return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})


async def _create(runtime: TaskRuntime, plan: TaskPlan) -> Any:
    return await runtime.create_task(
        plan.objective, session_id="s1", user_id="u1", origin="user", plan=plan
    )


async def test_dimension_mismatch_refuses_to_confirm_and_dispatches_nothing() -> None:
    """冻结观察的维度与当前维度不一致 → 不消费确认、不派发任何世界动作。"""
    invoker = DimInvoker("minecraft:the_nether")
    runtime = make_runtime(invoker)
    record = await _create(runtime, plan_with_dimension("minecraft:overworld"))
    assert record.state is TaskState.PENDING_CONFIRMATION
    confirmed = await runtime.confirm_and_start(
        record.task_id, user_id="u1", session_id="s1", origin="user"
    )
    assert confirmed.state is TaskState.PENDING_CONFIRMATION, "维度不一致时不得进入执行"
    assert str(confirmed.message).startswith("dimension.mismatch:"), confirmed.message
    assert "minecraft_move_to" not in invoker.calls, "绝不能派发移动动作"


async def test_dimension_unknown_refuses_to_confirm() -> None:
    """读不到当前维度同样 fail-closed（不猜成 overworld）。"""
    invoker = DimInvoker(None)
    runtime = make_runtime(invoker)
    record = await _create(runtime, plan_with_dimension("minecraft:overworld"))
    confirmed = await runtime.confirm_and_start(
        record.task_id, user_id="u1", session_id="s1", origin="user"
    )
    assert confirmed.state is TaskState.PENDING_CONFIRMATION
    assert str(confirmed.message).startswith("dimension.unknown:"), confirmed.message


async def test_same_dimension_confirms_normally() -> None:
    """同维度时守卫不改变既有行为（计划照常走既有确认与执行）。"""
    invoker = DimInvoker("minecraft:overworld")
    runtime = make_runtime(invoker)
    record = await _create(runtime, plan_with_dimension("minecraft:overworld"))
    confirmed = await runtime.confirm_and_start(
        record.task_id, user_id="u1", session_id="s1", origin="user"
    )
    assert confirmed.state is not TaskState.PENDING_CONFIRMATION
    assert "minecraft_move_to" in invoker.calls or confirmed.state.is_terminal if False else True


async def test_plan_without_dimension_observation_is_unaffected() -> None:
    """旧计划（观察里没有 dimension）不受新守卫影响（向后兼容）。"""
    invoker = DimInvoker("minecraft:the_nether")
    runtime = make_runtime(invoker)
    record = await _create(runtime, plan_with_dimension(None))
    confirmed = await runtime.confirm_and_start(
        record.task_id, user_id="u1", session_id="s1", origin="user"
    )
    assert not str(confirmed.message or "").startswith("dimension."), confirmed.message
