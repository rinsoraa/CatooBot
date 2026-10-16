"""R0 §4.2/§5.1：导航失败必须留下**可诊断状态**，失败后绝不继续派发动世界的步骤。

覆盖：

* detached ``minecraft_move_to`` 失败事件里的结构化 ``detail``（目标 / 实际位置 / 距离 /
  半径 / ``reason``）必须落进 ``step.result`` —— 这是区分 ``no_path``、
  ``path_search_timeout``、``goal_reached_without_arrival``、``path_stopped_short``、
  ``goal_changed`` 的唯一字段；
* 失败分类仍是既有确定性映射（未知错误码 → ``ACTION_FAILED``，超时 → ``TIMEOUT``）；
* 移动失败后**绝不**派发后续动世界的步骤；
* 迟到 / 重复的终态事件不复活任务。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
from app.tasks.models import (
    ExpectedFinalState,
    StepState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.store import InMemoryTaskStore
from tests.test_action_dispatch_safety import DispatchDouble, create, make_runtime, start


def move_plan() -> TaskPlan:
    """两步**都会动世界**：step_1 移动失败时 step_2 绝不能被派发。"""
    return TaskPlan(
        objective="走到目标点",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={"x": 100, "y": 64, "z": 200},
                risk="LOW",
            ),
            TaskStep(
                step_id="step_2",
                tool="minecraft_move_to",
                arguments={"x": 104, "y": 64, "z": 200},
                risk="LOW",
            ),
        ],
        expected_final_state=ExpectedFinalState(
            position_within={"x": 104.0, "y": 64.0, "z": 200.0, "radius": 2.0}
        ),
    )


MOVE_FAILURE_DETAIL: dict[str, Any] = {
    "target": {"x": 100, "y": 64, "z": 200},
    "actual": {"x": 93.5, "y": 64.0, "z": 200.5},
    "distance_to_target": 6.83,
    "raw_distance_to_target": 7.11,
    "radius": 2.0,
    "goal_reached": False,
    "reason": "path_stopped_short",
}


async def _started_move() -> tuple[Any, Any, Any]:
    invoke = DispatchDouble(mode="detached")
    runtime = make_runtime(invoke, store=InMemoryTaskStore())
    record = await create(runtime, move_plan())
    started = await start(runtime, record.task_id)
    return runtime, invoke, started


async def test_move_failure_detail_is_recorded_on_the_step() -> None:
    """§4.2/§5.1：失败 detail（reason/距离/半径/实际位置）必须进步骤证据。"""
    runtime, invoke, started = await _started_move()
    action_id = str(started.steps[0].action_id)
    assert action_id
    settled = await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.failed",
        status="FAILED",
        error="导航在到达目标之前停住了（距目标 6.83 格，半径 2.0）",
        code="path.not_reached",
        detail=MOVE_FAILURE_DETAIL,
    )
    assert settled is not None
    step = settled.steps[0]
    assert step.state is StepState.FAILED
    assert step.result["detail"]["reason"] == "path_stopped_short"
    assert step.result["detail"]["distance_to_target"] == 6.83
    assert step.result["detail"]["radius"] == 2.0
    assert step.result["detail"]["goal_reached"] is False
    assert step.result["detail"]["actual"] == {"x": 93.5, "y": 64.0, "z": 200.5}
    assert step.failure == TaskFailure.ACTION_FAILED.value
    assert settled.state is TaskState.PAUSED
    assert settled.steps[1].state is StepState.PENDING
    assert settled.steps[1].action_id == ""
    assert len(invoke.actions) == 1


async def test_move_failure_suppresses_later_world_steps() -> None:
    """§4.3 N3/N5：移动失败后，后续世界动作绝不执行（不放宽任何阈值）。"""
    runtime, invoke, started = await _started_move()
    settled = await runtime.on_action_event(
        action_id=str(started.steps[0].action_id),
        event="minecraft.action.failed",
        status="FAILED",
        error="无法找到到达目标的非破坏性路径",
        code="path.not_found",
        detail={**MOVE_FAILURE_DETAIL, "reason": "no_path"},
    )
    assert settled is not None and settled.state is TaskState.PAUSED
    assert settled.steps[0].result["detail"]["reason"] == "no_path"
    assert settled.steps[1].state is StepState.PENDING
    assert len(invoke.actions) == 1
    assert str(invoke.actions[0]["tool"]) == "minecraft_move_to"


async def test_reasons_stay_distinguishable_across_failure_kinds() -> None:
    """§4.2：no_path / 路径超时 / 到达判定失败必须能按 reason 区分（不是笼统 move failed）。"""
    reasons = (
        "no_path",
        "path_search_timeout",
        "goal_reached_without_arrival",
        "path_stopped_short",
    )
    codes = ("path.not_found", "path.not_found", "path.not_reached", "path.not_reached")
    for reason, code in zip(reasons, codes, strict=True):
        runtime, _invoke, started = await _started_move()
        settled = await runtime.on_action_event(
            action_id=str(started.steps[0].action_id),
            event="minecraft.action.failed",
            status="FAILED",
            error=f"移动失败：{reason}",
            code=code,
            detail={**MOVE_FAILURE_DETAIL, "reason": reason},
        )
        assert settled is not None
        assert settled.steps[0].state is StepState.FAILED
        assert settled.steps[0].result["detail"]["reason"] == reason
        assert settled.state is TaskState.PAUSED


async def test_timeout_maps_to_timeout_failure() -> None:
    """§4.2 第 5 项：移动超时必须与路径失败区分（既有确定性映射）。"""
    runtime, _invoke, started = await _started_move()
    settled = await runtime.on_action_event(
        action_id=str(started.steps[0].action_id),
        event="minecraft.action.timeout",
        status="TIMEOUT",
        error="动作超时",
        code="action.timeout",
        detail={"elapsed_ms": 30000},
    )
    assert settled is not None
    assert settled.steps[0].failure == TaskFailure.TIMEOUT.value
    assert settled.steps[0].result["detail"]["elapsed_ms"] == 30000
    assert settled.state is TaskState.PAUSED


async def test_late_terminal_event_does_not_revive_the_task() -> None:
    """§4.2 第 9 项：迟到/重复终态事件只收敛证据，绝不复活任务或再派发。"""
    invoke = DispatchDouble(mode="detached")
    store = InMemoryTaskStore()
    runtime = make_runtime(invoke, store=store)
    record = await create(runtime, move_plan())
    started = await start(runtime, record.task_id)
    action_id = str(started.steps[0].action_id)
    failed = await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.failed",
        status="FAILED",
        error="移动失败",
        code="path.not_found",
        detail={**MOVE_FAILURE_DETAIL, "reason": "no_path"},
    )
    assert failed is not None and failed.state is TaskState.PAUSED
    late = await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"final_position": {"x": 100.0, "y": 64.0, "z": 200.0}},
    )
    assert late is None, "已结算的重复/迟到终态事件必须是幂等空操作"
    reloaded = await store.load(record.task_id)
    assert reloaded is not None
    assert reloaded.state is TaskState.PAUSED, "迟到的成功事件不得改写失败后的状态"
    assert reloaded.steps[0].state is StepState.FAILED, "已结算的步骤证据不得被覆盖"
    assert reloaded.steps[0].result["detail"]["reason"] == "no_path"
    assert reloaded.steps[1].state is StepState.PENDING
    assert len(invoke.actions) == 1


class _RecordingRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def on_action_event(self, **kwargs: Any) -> None:
        self.calls.append(kwargs)


async def test_coordinator_forwards_structured_detail() -> None:
    """§5.1：服务事件里的 detail 必须在桥接边界被转发，不能在中途丢失。"""
    recorder = _RecordingRuntime()
    coordinator = MinecraftTaskCoordinator(recorder, service=object(), logger=None)
    await coordinator._handle_terminal(
        "minecraft.action.failed",
        "act_r0_nav",
        {
            "status": "FAILED",
            "error": "导航在到达目标之前停住了",
            "code": "path.not_reached",
            "detail": {"reason": "path_stopped_short", "radius": 2.0},
            "runtime_instance": "rt_r0",
        },
    )
    assert len(recorder.calls) == 1
    assert recorder.calls[0]["detail"] == {"reason": "path_stopped_short", "radius": 2.0}
    assert recorder.calls[0]["code"] == "path.not_reached"
