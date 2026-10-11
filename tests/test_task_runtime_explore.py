"""Phase 7F.1：有界自主探索的**真实执行 + 后置条件验证**测试（TaskRuntime 层）。

用替身 invoke 覆盖：到达成功 / 未到达 / 目标不可达 / 世界读不到（离线）/ 用户取消。
真实 Java 服务器的端到端门禁在真机 smoke 里。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.tasks.models import (
    ExpectedFinalState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from tests.test_task_runtime import FakeConfirmation

REGISTRY = {
    "minecraft_world": "SAFE",
    "minecraft_look_at": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_stop": "SAFE",
    "minecraft_inventory": "SAFE",
}


def explore_plan(*, x: int = 108, y: int = 64, z: int = 208, radius: float = 2.0) -> TaskPlan:
    return TaskPlan(
        objective="有点想去外面转转 想起：上次说要探索地图",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={"x": x, "y": y, "z": z},
                risk="LOW",
            ),
            TaskStep(
                step_id="step_2",
                tool="minecraft_look_at",
                arguments={"x": float(x), "y": float(y) + 1.5, "z": float(z)},
                risk="SAFE",
            ),
            # Phase 7F.2：到达后再做一次 SAFE 观察（执行期新事实来源）。
            TaskStep(step_id="step_3", tool="minecraft_world", arguments={}, risk="SAFE"),
        ],
        expected_final_state=ExpectedFinalState(
            position_within={"x": float(x), "y": float(y), "z": float(z), "radius": radius}
        ),
    )


class WorldRecorder:
    """可编排的 invoke：按工具名返回世界视图 / 移动结果。

    ``move_mode`` ∈ {``ok``, ``fail``, ``detached``}：直接成功 / 找不到路 / 持续型动作。
    """

    def __init__(
        self,
        *,
        self_position: Mapping[str, float] | None,
        move_mode: str = "ok",
        positions: list[Mapping[str, float] | None] | None = None,
    ) -> None:
        self.self_position = self_position
        self.move_mode = move_mode
        #: 可选的**读数序列**（模拟感知层滞后：前几次读到旧位置，随后收敛到真实位置）。
        self.positions = list(positions) if positions is not None else None
        self.calls: list[str] = []

    async def __call__(
        self,
        tool: str,
        arguments: Mapping[str, Any],
        *,
        task_id: str = "",
        step_id: str = "",
        plan_hash: str = "",
        risk: str = "",
        authorization: Any = None,
    ) -> TaskInvocation:
        self.calls.append(tool)
        if tool == "minecraft_world":
            position = self.self_position
            if self.positions is not None:
                # 每次读都取序列里的下一个；读完就停在最后一个（收敛后保持真实位置）。
                position = self.positions.pop(0) if len(self.positions) > 1 else self.positions[0]
            if position is None:
                return TaskInvocation(
                    ok=True, status="SUCCEEDED", result={"available": False, "online": False}
                )
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={
                    "available": True,
                    "online": True,
                    "semantic": {"self": {"position": dict(position)}},
                },
            )
        if tool == "minecraft_move_to":
            if self.move_mode == "fail":
                return TaskInvocation(
                    ok=False,
                    status="FAILED",
                    error="找不到路",
                    code="minecraft.path_not_found",
                )
            if self.move_mode == "detached":
                return TaskInvocation(
                    ok=True, status="RUNNING", action_id="act_1", summary="正在过去"
                )
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})
        if tool == "minecraft_look_at":
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})
        if tool == "minecraft_inventory":
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True, "items": []})
        if tool == "minecraft_stop":
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})
        raise AssertionError(f"意外工具 {tool}")


def make_runtime(invoke: WorldRecorder, **overrides: Any) -> TaskRuntime:
    return TaskRuntime(
        store=overrides.pop("store", None) or InMemoryTaskStore(),
        invoke=invoke,
        confirmations=overrides.pop("confirmations", None) or FakeConfirmation(),
        config=overrides.pop("config", None) or TaskConfig(),
        publish=lambda event, payload: None,
        risk_of=lambda tool: REGISTRY.get(tool, ""),
        is_registered=lambda tool: tool in REGISTRY,
        **overrides,
    )


async def confirm(runtime: TaskRuntime, plan: TaskPlan) -> Any:
    record = await runtime.create_task(
        plan.objective,
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=plan,
    )
    return await runtime.confirm_and_start(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )


class TestExploreRuntime:
    async def test_arrival_verified_by_fresh_world_read(self) -> None:
        invoke = WorldRecorder(self_position={"x": 108.0, "y": 64.0, "z": 208.0})
        runtime = make_runtime(invoke)
        record = await confirm(runtime, explore_plan())
        assert record.state is TaskState.SUCCEEDED
        assert record.verification["ok"] is True
        assert record.verification["position_within"]["distance"] == 0.0
        # 后置条件是重新读世界得到的，不是动作返回值自述
        assert "minecraft_world" in invoke.calls

    async def test_not_arrived_fails_verification(self) -> None:
        invoke = WorldRecorder(self_position={"x": 100.0, "y": 64.0, "z": 200.0})
        # 单次读即判失败（重试预算 = 1），验证 fail-closed 不依赖等待。
        runtime = make_runtime(invoke, config=TaskConfig(position_verify_attempts=1))
        record = await confirm(runtime, explore_plan())
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value
        assert record.verification["ok"] is False
        assert record.verification["position_within"]["distance"] > 2.0

    async def test_perception_lag_recovers_and_succeeds(self) -> None:
        # 感知层 near 轮询约 1s 一次：move_to 已到达，但缓存还会短时读到**中途**旧坐标。
        # 有界重试必须等到真实坐标收敛后判成功——不是放宽半径，而是容忍传播延迟。
        invoke = WorldRecorder(
            self_position={"x": 108.0, "y": 64.0, "z": 208.0},
            positions=[
                {"x": 104.0, "y": 64.0, "z": 204.0},  # 滞后：还在路上
                {"x": 108.0, "y": 64.0, "z": 208.0},  # 收敛：已到达
            ],
        )
        runtime = make_runtime(
            invoke,
            config=TaskConfig(position_verify_attempts=4, position_verify_interval_seconds=0.0),
        )
        record = await confirm(runtime, explore_plan())
        assert record.state is TaskState.SUCCEEDED
        assert record.verification["ok"] is True
        assert record.verification["position_within"]["distance"] == 0.0

    async def test_offline_at_verification_fails_closed(self) -> None:
        invoke = WorldRecorder(self_position=None)
        runtime = make_runtime(invoke)
        record = await confirm(runtime, explore_plan())
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value

    async def test_unreachable_path_safely_stops_before_verification(self) -> None:
        # 路径找不到 → 既有规则下的"安全停止"（PAUSED），绝不是验证失败冒充成功；
        # FAILED 只留给 VALIDATION/INTERNAL/VERIFICATION，这里不得改动既有状态语义。
        invoke = WorldRecorder(self_position={"x": 108.0, "y": 64.0, "z": 208.0}, move_mode="fail")
        runtime = make_runtime(invoke)
        record = await confirm(runtime, explore_plan())
        assert record.state is TaskState.PAUSED
        assert record.failure == TaskFailure.ACTION_FAILED.value
        assert record.failure != TaskFailure.VERIFICATION.value
        assert not record.verification, "未到达验证阶段就不该有 verification 结论"

    async def test_cancel_stops_and_produces_no_new_actions(self) -> None:
        invoke = WorldRecorder(
            self_position={"x": 108.0, "y": 64.0, "z": 208.0}, move_mode="detached"
        )
        runtime = make_runtime(invoke)
        record = await runtime.create_task(
            "去探索", session_id="s", user_id="u", origin="user", plan=explore_plan()
        )
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert started.state is TaskState.WAITING_ACTION
        cancelled = await runtime.cancel(started.task_id, reason="stop")
        assert cancelled.state is TaskState.CANCELLED
        assert "minecraft_move_to" in invoke.calls
        assert "minecraft_stop" in invoke.calls
        # 取消之后驱动终态任务：不再产生任何新动作
        before = len(invoke.calls)
        await runtime.drive(started.task_id)
        assert len(invoke.calls) == before
