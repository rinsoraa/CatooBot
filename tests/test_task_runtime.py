"""TaskRuntime 核心测试（Phase 5A §八十六 A–X）。

编排层是纯逻辑：这里的 ``invoke`` / 确认门 / 世界事实全是替身，
真正的 Minecraft 集成由 ``tests/test_minecraft_task_integration.py`` 与真机 smoke 覆盖。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.tasks.models import (
    StepState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
    arguments_hash,
)
from app.tasks.planner import plan_resource_task
from app.tasks.runtime import (
    TaskAuthorizationError,
    TaskBusy,
    TaskConfig,
    TaskInvocation,
    TaskRuntime,
)
from app.tasks.store import InMemoryTaskStore

# ------------------------------------------------------------------ 替身


class FakeConfirmation:
    """计划确认门（内存）：与 Minecraft ConfirmationStore 的关键点一致 ——
    一次性 + 绑 user/session/args。
    """

    def __init__(self, *, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = ttl_seconds
        self.items: dict[str, dict[str, Any]] = {}
        self.seq = 0
        self.created: list[dict[str, Any]] = []

    async def request(
        self,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        risk: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        summary: str,
    ) -> str:
        digest = arguments_hash(arguments)
        for confirmation_id, item in self.items.items():
            if (
                item["status"] == "PENDING"
                and item["task_id"] == task_id
                and item["arguments_hash"] == digest
            ):
                return confirmation_id
        self.seq += 1
        confirmation_id = f"cfm_{self.seq}"
        self.items[confirmation_id] = {
            "task_id": task_id,
            "session_id": session_id,
            "user_id": user_id,
            "risk": risk,
            "plan_hash": plan_hash,
            "arguments_hash": digest,
            "status": "PENDING",
            "summary": summary,
        }
        self.created.append(self.items[confirmation_id])
        return confirmation_id

    async def consume(
        self,
        confirmation_id: str,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        origin: str,
    ) -> tuple[bool, str]:
        item = self.items.get(confirmation_id)
        if item is None or item["status"] != "PENDING":
            return False, "minecraft.confirmation_invalid"
        if origin != "user":
            return False, "minecraft.confirmation_not_user_turn"
        if item["user_id"] != user_id or item["session_id"] != session_id:
            return False, "minecraft.confirmation_invalid"
        if item["arguments_hash"] != arguments_hash(arguments):
            return False, "minecraft.confirmation_mismatch"
        item["status"] = "CONSUMED"
        return True, ""

    async def cancel(self, confirmation_id: str) -> None:
        if confirmation_id in self.items:
            self.items[confirmation_id]["status"] = "CANCELLED"


class Recorder:
    """可编排的 invoke 替身：按工具名决定"同步成功 / 持续型 / 失败"。"""

    def __init__(self, **behaviour: Any) -> None:
        self.calls: list[dict[str, Any]] = []
        self.behaviour = behaviour
        self.actions: dict[str, str] = {}
        self.action_seq = 0

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
        self.calls.append(
            {
                "tool": tool,
                "arguments": dict(arguments),
                "task_id": task_id,
                "step_id": step_id,
                "plan_hash": plan_hash,
                "risk": risk,
            }
        )
        mode = self.behaviour.get(tool, "ok")
        if isinstance(mode, TaskInvocation):
            return mode
        if mode == "fail":
            return TaskInvocation(
                ok=False, status="FAILED", error="坏掉了", code="minecraft.action_failed"
            )
        if mode == "busy":
            return TaskInvocation(
                ok=False, status="FAILED", error="忙", code="minecraft.action_busy"
            )
        if mode == "detached":
            self.action_seq += 1
            action_id = f"act_{self.action_seq}"
            self.actions[action_id] = tool
            return TaskInvocation(ok=True, status="RUNNING", action_id=action_id, summary="已开始")
        return TaskInvocation(ok=True, status="SUCCEEDED", result=_default_result(tool))


def _default_result(tool: str) -> dict[str, Any]:
    if tool == "minecraft_find_blocks":
        return {
            "ok": True,
            "matches": [{"position": {"x": 10, "y": 64, "z": 10}, "block": {"name": "oak_log"}}],
        }
    if tool == "minecraft_dig_capability":
        return {"ok": True, "can_dig": True, "dig_time_ms": 900}
    if tool == "minecraft_inventory":
        return {"ok": True, "items": [{"name": "oak_log", "count": 0}]}
    if tool == "minecraft_dropped_items":
        return {"ok": True, "matches": [{"entity_id": 42, "item": {"name": "oak_log", "count": 1}}]}
    return {"ok": True}


REGISTRY = {
    "minecraft_find_blocks": "SAFE",
    "minecraft_dig_capability": "SAFE",
    "minecraft_inventory": "SAFE",
    "minecraft_dropped_items": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_equip": "MEDIUM",
    "minecraft_dig": "MEDIUM",
    "minecraft_pickup_item": "MEDIUM",
    "minecraft_stop": "SAFE",
}


def make_runtime(
    **overrides: Any,
) -> tuple[TaskRuntime, Recorder, FakeConfirmation, InMemoryTaskStore]:
    invoke = overrides.pop("invoke", None) or Recorder()
    confirmations = overrides.pop("confirmations", None) or FakeConfirmation()
    store = overrides.pop("store", None) or InMemoryTaskStore()
    events: list[tuple[str, dict[str, Any]]] = []
    runtime = TaskRuntime(
        store=store,
        invoke=invoke,
        confirmations=confirmations,
        config=overrides.pop("config", None) or TaskConfig(),
        publish=lambda event, payload: events.append((event, payload)),
        risk_of=lambda tool: REGISTRY.get(tool, ""),
        is_registered=lambda tool: tool in REGISTRY,
        world_facts=overrides.pop("world_facts", None),
        **overrides,
    )
    runtime.events = events  # type: ignore[attr-defined]
    return runtime, invoke, confirmations, store


def simple_plan(
    *, steps: list[tuple[str, dict[str, Any]]] | None = None, expected: dict[str, int] | None = None
) -> TaskPlan:
    steps = steps or [
        ("minecraft_find_blocks", {"block_names": ["oak_log"]}),
        ("minecraft_move_to", {"x": 10, "y": 64, "z": 10}),
        ("minecraft_dig", {"x": 10, "y": 64, "z": 10, "expected_block": "oak_log"}),
        ("minecraft_inventory", {}),
    ]
    built = [
        TaskStep(
            step_id=f"step_{index}",
            tool=tool,
            arguments=dict(args),
            risk=REGISTRY.get(tool, ""),
        )
        for index, (tool, args) in enumerate(steps, start=1)
    ]
    from app.tasks.models import ExpectedFinalState

    return TaskPlan(
        objective="去附近找一棵橡木，挖一块原木并捡回来",
        steps=built,
        expected_final_state=ExpectedFinalState(inventory_delta=expected or {}),
    )


async def create_confirmed(runtime: TaskRuntime, *, plan: TaskPlan | None = None) -> Any:
    record = await runtime.create_task(
        (plan or simple_plan()).objective,
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=plan or simple_plan(),
    )
    assert record.state is TaskState.PENDING_CONFIRMATION
    return await runtime.confirm_and_start(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )


# ------------------------------------------------------------------ A/B/C：创建与计划校验


async def test_a_task_creation_requests_one_frozen_plan_confirmation() -> None:
    runtime, _, confirmations, _ = make_runtime()
    record = await runtime.create_task(
        "去附近找一棵橡木，挖一块原木并捡回来",
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=simple_plan(),
    )
    assert record.state is TaskState.PENDING_CONFIRMATION
    assert record.plan_hash and len(confirmations.created) == 1
    assert confirmations.created[0]["task_id"] == record.task_id
    assert confirmations.created[0]["plan_hash"] == record.plan_hash
    assert "将执行" in confirmations.created[0]["summary"]
    assert record.steps[0].state is StepState.PENDING


async def test_b_invalid_plans_are_rejected_deterministically() -> None:
    runtime, _, _, _ = make_runtime()
    bad = simple_plan(steps=[("minecraft_nope", {}), ("minecraft_gather_resource", {})])
    record = await runtime.create_task(
        "随便做点什么", session_id="s", user_id="u", origin="user", plan=bad
    )
    assert record.state is TaskState.FAILED
    assert record.failure == TaskFailure.VALIDATION.value
    assert "未注册" in record.message and "复合工具" in record.message


async def test_b_too_many_steps_is_rejected() -> None:
    runtime, _, _, _ = make_runtime()
    many = simple_plan(steps=[("minecraft_inventory", {}) for _ in range(20)])
    record = await runtime.create_task(
        "太多步了", session_id="s", user_id="u", origin="user", plan=many
    )
    assert record.state is TaskState.FAILED and "超过上限" in record.message


async def test_c_plan_hash_is_stable_and_changes_with_arguments() -> None:
    first = simple_plan()
    second = simple_plan()
    assert first.plan_hash == second.plan_hash
    changed = simple_plan(
        steps=[
            ("minecraft_find_blocks", {"block_names": ["oak_log"]}),
            ("minecraft_move_to", {"x": 11, "y": 64, "z": 10}),
        ]
    )
    assert changed.plan_hash != first.plan_hash


async def test_c_plan_hash_ignores_observation_data() -> None:
    plan = simple_plan()
    before = plan.plan_hash
    plan.observations = [{"tool": "minecraft_find_blocks", "result": {"matches": []}}]
    assert plan.plan_hash == before, "观察结果不进 hash（否则再查一次世界就作废授权）"


# ------------------------------------------------------------------ D/E：确认与授权


async def test_d_confirmation_requires_the_real_user_turn_and_owner() -> None:
    runtime, invoke, _, _ = make_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="private:10001", user_id="10001", origin="user", plan=simple_plan()
    )
    with pytest.raises(TaskAuthorizationError) as excinfo:
        await runtime.confirm_and_start(
            record.task_id, user_id="10001", session_id="private:10001", origin="initiative"
        )
    assert excinfo.value.code == "task.confirmation_not_user_turn"
    with pytest.raises(TaskAuthorizationError) as excinfo2:
        await runtime.confirm_and_start(
            record.task_id, user_id="99999", session_id="private:10001", origin="user"
        )
    assert excinfo2.value.code == "task.confirmation_wrong_owner"
    assert invoke.calls == []


async def test_d_confirm_consumes_and_starts() -> None:
    runtime, invoke, confirmations, _ = make_runtime()
    record = await create_confirmed(runtime)
    assert record.state in {TaskState.SUCCEEDED, TaskState.PAUSED}
    assert record.authorization is not None and record.authorization.valid()
    assert confirmations.items[record.confirmation_id]["status"] == "CONSUMED"
    assert [call["tool"] for call in invoke.calls][:2] == [
        "minecraft_inventory",  # 任务开始时的基线读数（SAFE）
        "minecraft_find_blocks",
    ]
    assert all(call["task_id"] == record.task_id for call in invoke.calls[1:])


async def test_e_authorize_step_matrix() -> None:
    runtime, _, _, _ = make_runtime(invoke=Recorder(minecraft_move_to="detached"))
    record = await create_confirmed(runtime)
    assert record.state is TaskState.WAITING_ACTION, "停在等待动作时才能验授权矩阵"
    step = record.steps[0]
    ok = await runtime.authorize_step(
        task_id=record.task_id,
        step_id=step.step_id,
        tool=step.tool,
        arguments=step.arguments,
        plan_hash=record.plan_hash,
    )
    assert ok is True
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments={"block_names": ["stone"]},
            plan_hash=record.plan_hash,
        )
        is False
    ), "参数不一样就不放行"
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments=step.arguments,
            plan_hash="deadbeef",
        )
        is False
    ), "计划 hash 不一样就不放行"
    assert (
        await runtime.authorize_step(
            task_id="task_unknown",
            step_id=step.step_id,
            tool=step.tool,
            arguments=step.arguments,
            plan_hash=record.plan_hash,
        )
        is False
    )


async def test_e_expired_authorization_stops_the_task() -> None:
    """5A.1 §十二：授权到期 → 停在安全边界 → 回 PENDING_CONFIRMATION 等用户重新确认。"""
    runtime, _, confirmations, _ = make_runtime(invoke=Recorder(minecraft_move_to="detached"))
    record = await create_confirmed(runtime)
    assert record.authorization is not None
    record.authorization.expires_at = 0.0  # 授权过期（手动，避免时钟精度抖动）
    record.steps[1].state = StepState.PENDING
    record.pending_action_id = ""
    record.pending_step_id = ""
    record.state = TaskState.RUNNING
    await runtime._save(record)  # noqa: SLF001
    before = len(confirmations.created)
    outcome = await runtime.drive(record.task_id)
    assert outcome.state is TaskState.PENDING_CONFIRMATION
    assert outcome.failure == TaskFailure.AUTHORIZATION.value
    assert outcome.authorization is None, "过期授权必须被丢掉，绝不能留着复用"
    assert outcome.authorization_expired_at > 0
    assert len(confirmations.created) == before + 1, "过期后必须重新挂一条确认"
    # 计划本身没变：这**不是**重规划
    assert outcome.plan_hash == record.plan_hash
    assert outcome.replans == 0
    assert outcome.plan_status == "PENDING_CONFIRMATION"


# ------------------------------------------------------------------ F/G/H/I：步骤调度与异步恢复


async def test_f_steps_run_in_order_and_record_results() -> None:
    runtime, invoke, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    tools = [call["tool"] for call in invoke.calls]
    assert tools[1:] == [
        "minecraft_find_blocks",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_inventory",
    ]
    assert all(step.state is StepState.SUCCEEDED for step in record.steps)


async def test_g_h_detached_action_is_bound_by_action_id_and_resumes() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    runtime, _, _, store = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    assert record.state is TaskState.WAITING_ACTION
    assert record.pending_step_id == "step_2" and record.pending_action_id
    action_id = record.pending_action_id
    assert record.steps[1].state is StepState.WAITING_ACTION

    # 别人的 action 完成事件绝不能唤醒本任务（§二十五）
    assert (
        await runtime.on_action_event(action_id="act_other", event="minecraft.action.completed")
        is None
    )

    resumed = await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
    )
    assert resumed is not None and resumed.state is TaskState.SUCCEEDED
    assert all(step.state is StepState.SUCCEEDED for step in resumed.steps)


async def test_i_action_failure_pauses_the_task_with_a_classification() -> None:
    invoke = Recorder(minecraft_dig="fail")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    assert record.state is TaskState.PAUSED
    assert record.failure == TaskFailure.ACTION_FAILED.value
    assert record.failed_step == "step_3"


async def test_i_busy_is_classified_as_busy() -> None:
    invoke = Recorder(minecraft_move_to="busy")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    assert record.state is TaskState.PAUSED and record.failure == TaskFailure.BUSY.value


# ------------------------------------------------ J/K/L/M：暂停 / 恢复 / 取消 / 过期


async def test_j_pause_waits_for_a_running_action_then_pauses() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    assert record.state is TaskState.WAITING_ACTION

    paused = await runtime.pause(record.task_id)
    assert paused.pause_requested is True and paused.state is TaskState.WAITING_ACTION
    assert [call["tool"] for call in invoke.calls][-1] == "minecraft_stop"
    # Phase 6A §四十二：延后暂停的**请求**阶段还不算 PAUSED，这时不许谎报事件
    assert "task.paused" not in [event for event, _ in runtime.events]  # type: ignore[attr-defined]

    after = await runtime.on_action_event(
        action_id=paused.pending_action_id, event="minecraft.action.cancelled", status="CANCELLED"
    )
    assert after is not None and after.state is TaskState.PAUSED
    # 真的进入 PAUSED 了 → 必须对外说一声（世界活动靠它把"执行任务"收成 INTERRUPTED）
    assert "task.paused" in [event for event, _ in runtime.events]  # type: ignore[attr-defined]


async def test_j_pause_without_action_is_immediate_and_does_not_advance() -> None:
    runtime, invoke, _, _ = make_runtime(config=TaskConfig(no_progress_limit=1))
    plan = simple_plan(
        steps=[("minecraft_find_blocks", {"block_names": ["oak_log"]}) for _ in range(2)]
    )
    record = await runtime.create_task(
        "反复查同一个东西", session_id="s2", user_id="u", origin="user", plan=plan
    )
    record = await runtime.confirm_and_start(
        record.task_id, user_id="u", session_id="s2", origin="user"
    )
    paused = await runtime.pause(record.task_id)
    assert paused.state is TaskState.PAUSED and paused.pending_action_id == ""
    # 立即暂停：进入 PAUSED 的同一刻对外发事件（§四十二 的统一口径）
    assert "task.paused" in [event for event, _ in runtime.events]  # type: ignore[attr-defined]
    before = len(invoke.calls)
    again = await runtime.drive(record.task_id)
    assert again.state is TaskState.PAUSED and len(invoke.calls) == before, "暂停后绝不再往下走一步"


async def test_k_resume_continues_and_requires_user_turn() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    await runtime.pause(record.task_id)
    await runtime.on_action_event(
        action_id=record.pending_action_id, event="minecraft.action.cancelled", status="CANCELLED"
    )
    with pytest.raises(TaskAuthorizationError):
        await runtime.resume(
            record.task_id, user_id="u", session_id="private:10001", origin="background"
        )
    resumed = await runtime.resume(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )
    assert resumed.state in {TaskState.WAITING_ACTION, TaskState.SUCCEEDED}
    assert resumed.state is not TaskState.PAUSED, "恢复之后确实继续跑了"


async def test_k_resume_with_expired_authorization_asks_for_confirmation_again() -> None:
    runtime, _, confirmations, _ = make_runtime(invoke=Recorder(minecraft_move_to="detached"))
    record = await create_confirmed(runtime)
    record.state = TaskState.PAUSED
    record.pause_requested = False
    assert record.authorization is not None
    record.authorization.expires_at = 0.0  # 授权过期
    await runtime._save(record)  # noqa: SLF001
    again = await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
    assert again.state is TaskState.PENDING_CONFIRMATION
    assert len(confirmations.created) == 2, "过期后要重新挂一条确认（绝不偷偷继续）"


async def test_l_cancel_stops_the_action_and_ignores_late_completion() -> None:
    invoke = Recorder(minecraft_move_to="detached")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    action_id = record.pending_action_id
    cancelled = await runtime.cancel(record.task_id)
    assert cancelled.state is TaskState.CANCELLED
    assert [call["tool"] for call in invoke.calls][-1] == "minecraft_stop"
    late = await runtime.on_action_event(
        action_id=action_id, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert late is None, "已经取消的任务绝不被迟到的 completed 翻案"
    assert (await runtime.get(record.task_id)).state is TaskState.CANCELLED


async def test_m_expiration_stops_and_marks_expired() -> None:
    runtime, invoke, _, _ = make_runtime(config=TaskConfig(ttl_seconds=0.0))
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    record = await runtime.confirm_and_start(
        record.task_id, user_id="u", session_id="s", origin="user"
    )
    if record.state is not TaskState.EXPIRED:
        record = await runtime.expire(record.task_id)
    assert record.state is TaskState.EXPIRED
    assert [call["tool"] for call in invoke.calls][-1] == "minecraft_stop"


# ------------------------------------------------------------------ N/O/P：护栏


async def test_n_no_progress_pauses_the_task() -> None:
    runtime, _, _, _ = make_runtime(config=TaskConfig(no_progress_limit=3))
    plan = simple_plan(
        steps=[("minecraft_find_blocks", {"block_names": ["oak_log"]}) for _ in range(4)]
    )
    record = await runtime.create_task(
        "反复查同一个东西", session_id="s", user_id="u", origin="user", plan=plan
    )
    record = await runtime.confirm_and_start(
        record.task_id, user_id="u", session_id="s", origin="user"
    )
    assert record.state is TaskState.PAUSED
    assert record.failure == TaskFailure.STALLED.value


async def test_p_max_replans_is_enforced() -> None:
    runtime, _, _, _ = make_runtime(config=TaskConfig(max_replans=1))
    record = await create_confirmed(runtime)
    record.state = TaskState.PAUSED
    record.pause_requested = False
    await runtime._save(record)  # noqa: SLF001
    first = await runtime.replan(record.task_id, simple_plan())
    assert first.state is TaskState.PENDING_CONFIRMATION and first.replans == 1
    first.state = TaskState.PAUSED
    await runtime._save(first)  # noqa: SLF001
    second = await runtime.replan(first.task_id, simple_plan())
    assert second.state is TaskState.FAILED and "重规划次数" in second.message


async def test_q_safe_steps_may_query_the_world_mid_task() -> None:
    """SAFE 观察可以随时做（重新读世界），它不会改变计划、也不需要新确认。"""
    runtime, invoke, confirmations, _ = make_runtime()
    record = await create_confirmed(runtime)
    before_hash = record.plan_hash
    probe = await runtime._invoke_step(  # noqa: SLF001 - 直接用编排层的调用口
        record,
        TaskStep(
            step_id="probe", tool="minecraft_inventory", arguments={"item": "oak_log"}, risk="SAFE"
        ),
        record.step_authorization(record.steps[0]),
    )
    assert probe.ok is True
    assert record.plan_hash == before_hash and len(confirmations.created) == 1


# ------------------------------------------------------------------ R/S/T/U/V/W/X


async def test_r_medium_steps_run_under_the_plan_authorization() -> None:
    invoke = Recorder()
    runtime, _, confirmations, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    medium_calls = [call for call in invoke.calls if call["risk"] == "MEDIUM"]
    assert medium_calls, "MEDIUM 步骤确实执行了"
    assert len(confirmations.created) == 1, "只确认了一次（不是逐步确认）"
    assert all(call["task_id"] == record.task_id for call in medium_calls)


async def test_s_changed_resolved_arguments_are_recomputed_from_the_plan() -> None:
    """§四十七：执行用的参数由「冻结计划 + 已观察结果」确定性重算 —— 被偷改的 resolved 不作数。"""
    runtime, invoke, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    step = record.steps[2]
    assert step.tool == "minecraft_dig"
    step.resolved_arguments = {"x": 99, "y": 64, "z": 99, "expected_block": "oak_log"}
    record.steps[0].state = StepState.PENDING
    record.steps[1].state = StepState.PENDING
    record.steps[2].state = StepState.PENDING
    record.current_step = 0
    await runtime._save(record)  # noqa: SLF001
    await runtime.drive(record.task_id)
    dig_calls = [call for call in invoke.calls if call["tool"] == "minecraft_dig"]
    assert dig_calls and dig_calls[-1]["arguments"]["x"] == 10, "用的是计划里的坐标"


async def test_t_replan_requires_a_new_confirmation() -> None:
    runtime, _, confirmations, _ = make_runtime()
    record = await create_confirmed(runtime)
    record.state = TaskState.PAUSED
    await runtime._save(record)  # noqa: SLF001
    new_plan = simple_plan(steps=[("minecraft_move_to", {"x": 5, "y": 64, "z": 5})])
    old_hash = record.plan_hash
    replanned = await runtime.replan(record.task_id, new_plan, reason="目标没了")
    assert replanned.state is TaskState.PENDING_CONFIRMATION
    assert len(confirmations.created) == 2
    assert replanned.plan_hash != old_hash, "新计划 = 新 hash（旧授权因此失效）"
    assert replanned.authorization is None, "新计划的旧授权必须作废"


async def test_u_system_turn_cannot_confirm() -> None:
    runtime, _, _, _ = make_runtime()
    record = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    for origin in ("system", "background", "initiative", "task"):
        with pytest.raises(TaskAuthorizationError):
            await runtime.confirm_and_start(
                record.task_id, user_id="u", session_id="s", origin=origin
            )


async def test_v_duplicate_task_in_one_session_is_rejected() -> None:
    runtime, _, _, _ = make_runtime()
    first = await runtime.create_task(
        "去挖木头", session_id="s", user_id="u", origin="user", plan=simple_plan()
    )
    with pytest.raises(TaskBusy) as excinfo:
        await runtime.create_task(
            "再挖一次", session_id="s", user_id="u", origin="user", plan=simple_plan()
        )
    assert excinfo.value.existing.task_id == first.task_id


async def test_w_resume_after_runtime_restart_requires_replanning() -> None:
    """5A.1 §十七/§六十四：旧动作已经不在了 → 恢复必须重规划，绝不接着跑旧步骤。"""
    invoke = Recorder(minecraft_move_to="detached")
    runtime, _, _, _ = make_runtime(invoke=invoke)
    record = await create_confirmed(runtime)
    # 进程重启后的持久化形态：还写着 WAITING_ACTION + action_id，但没有任何事件会再来
    step = record.steps[1]
    step.state = StepState.WAITING_ACTION
    record.state = TaskState.PAUSED
    record.pause_requested = False
    record.pending_action_id = ""
    record.pending_step_id = ""
    await runtime._save(record)  # noqa: SLF001
    resumed = await runtime.resume(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )
    assert resumed.state is TaskState.REPLANNING
    assert resumed.steps[1].failure == TaskFailure.RUNTIME_RESTART.value
    assert resumed.replan_required is True
    assert resumed.failure == "WORLD_CHANGED"


async def test_x_final_verification_uses_a_fresh_safe_read() -> None:
    class Inventory:
        def __init__(self) -> None:
            self.counts = 0

        async def __call__(
            self, tool: str, arguments: Mapping[str, Any], **kwargs: Any
        ) -> TaskInvocation:
            if tool == "minecraft_inventory":
                self.counts += 1
                # 第一次（基线）0 个，最后一次（校验）1 个
                return TaskInvocation(
                    ok=True,
                    status="SUCCEEDED",
                    result={
                        "ok": True,
                        "items": [{"name": "oak_log", "count": 0 if self.counts == 1 else 1}],
                    },
                )
            return TaskInvocation(ok=True, status="SUCCEEDED", result=_default_result(tool))

    inventory = Inventory()
    runtime, _, _, _ = make_runtime(invoke=inventory)
    record = await runtime.confirm_and_start(
        (
            await runtime.create_task(
                "去挖木头",
                session_id="s",
                user_id="u",
                origin="user",
                plan=simple_plan(expected={"oak_log": 1}),
            )
        ).task_id,
        user_id="u",
        session_id="s",
        origin="user",
    )
    assert record.state is TaskState.SUCCEEDED
    assert record.verification["inventory_delta"] == {"oak_log": 1}
    assert "oak_log" in record.result["summary"]


async def test_x_failed_verification_fails_the_task() -> None:
    runtime, _, _, _ = make_runtime()
    record = await runtime.create_task(
        "去挖木头",
        session_id="s",
        user_id="u",
        origin="user",
        plan=simple_plan(expected={"oak_log": 3}),
    )
    record = await runtime.confirm_and_start(
        record.task_id, user_id="u", session_id="s", origin="user"
    )
    assert record.state is TaskState.FAILED
    assert record.failure == TaskFailure.VERIFICATION.value


# ------------------------------------------------------------------ 计划器（两阶段）


async def test_planner_resolves_action_arguments_before_confirmation() -> None:
    """§六十八：先用 SAFE 把目标查清楚，动作步骤写**解析后的坐标**再请用户确认。"""
    world = {"can_dig": False, "reason": "too_far"}

    async def observe(tool: str, arguments: Mapping[str, Any]) -> TaskInvocation:
        if tool == "minecraft_find_blocks":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    # 第一个候选比罐头高 6 格（树冠那一截，够不到）→ 计划应该跳过它选下面那个
                    "matches": [
                        {"position": {"x": 103, "y": 70, "z": 141}, "block": {"name": "oak_log"}},
                        {"position": {"x": 103, "y": 64, "z": 141}, "block": {"name": "oak_log"}},
                    ],
                },
            )
        if tool == "minecraft_world":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "semantic": {"self": {"position": {"x": 100, "y": 64, "z": 140}}},
                },
            )
        if tool == "minecraft_dig_capability":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "can_dig": world["can_dig"],
                    "reason": world["reason"],
                    "dig_time_ms": 800,
                },
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "held_item": {"name": "dirt", "count": 3},
                    "items": [{"name": "stone_axe", "count": 1}],
                },
            )
        raise AssertionError(f"观察阶段不允许调用 {tool}")

    planned = await plan_resource_task(
        "去附近找一棵橡木，挖一块原木并捡回来",
        observe=observe,
        block_name="minecraft:oak_log",
    )
    tools = [step.tool for step in planned.plan.steps]
    assert tools == [
        "minecraft_equip",
        "minecraft_move_to",
        "minecraft_dig",
        "minecraft_dropped_items",
        "minecraft_pickup_item",
        "minecraft_inventory",
    ]
    move = planned.plan.steps[1]
    assert move.arguments == {"x": 103, "y": 64, "z": 141}, (
        "坐标在确认前就已经解析成最终值（而且选的是够得到的那一截，不是树冠上的）"
    )
    pickup = planned.plan.steps[4]
    assert pickup.references["entity_id"].from_step == "step_4"
    assert planned.plan.expected_final_state.inventory_delta == {"oak_log": 1}
    assert [item.tool for item in planned.observations] == [
        "minecraft_find_blocks",
        "minecraft_world",
        "minecraft_inventory",
        "minecraft_dig_capability",
    ]

    # 挖不动（够不着 → 走过去就够得着）→ 计划里必须**写明**要换哪一把工具
    world.update({"can_dig": False, "reason": "too_far"})
    planned2 = await plan_resource_task(
        "去附近找一棵橡木，挖一块原木并捡回来",
        observe=observe,
        block_name="minecraft:oak_log",
    )
    assert planned2.plan.steps[0].tool == "minecraft_equip"
    assert planned2.plan.steps[0].arguments == {"item": "stone_axe"}


async def test_planner_skips_the_move_step_when_it_can_already_dig() -> None:
    """§二十：dig_capability 说"现在挖得动"就别写 move_to（不制造必然失败的一步）。"""

    async def observe(tool: str, arguments: Mapping[str, Any]) -> TaskInvocation:
        if tool == "minecraft_find_blocks":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "matches": [
                        {"position": {"x": 103, "y": 64, "z": 141}, "block": {"name": "oak_log"}}
                    ],
                },
            )
        if tool == "minecraft_dig_capability":
            return TaskInvocation(ok=True, result={"ok": True, "can_dig": True, "reason": None})
        if tool == "minecraft_world":
            return TaskInvocation(
                ok=True, result={"ok": True, "position": {"x": 100, "y": 64, "z": 140}}
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(
                ok=True, result={"ok": True, "held_item": {"name": "stone_axe", "count": 1}}
            )
        raise AssertionError(f"观察阶段不允许调用 {tool}")

    planned = await plan_resource_task("挖一块木头", observe=observe, block_name="oak_log")
    assert [step.tool for step in planned.plan.steps] == [
        "minecraft_dig",
        "minecraft_dropped_items",
        "minecraft_pickup_item",
        "minecraft_inventory",
    ], "已经够得着就不该再走一步（move_to 只会白白失败）"


async def test_planner_refuses_to_plan_a_block_it_cannot_dig() -> None:
    from app.tasks.planner import ObservationFailed

    async def observe(tool: str, arguments: Mapping[str, Any]) -> TaskInvocation:
        if tool == "minecraft_find_blocks":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "matches": [
                        {"position": {"x": 5, "y": 64, "z": 5}, "block": {"name": "oak_log"}}
                    ],
                },
            )
        if tool == "minecraft_dig_capability":
            return TaskInvocation(
                ok=True, result={"ok": True, "can_dig": False, "reason": "not_diggable"}
            )
        if tool == "minecraft_world":
            return TaskInvocation(
                ok=True, result={"ok": True, "position": {"x": 5, "y": 64, "z": 5}}
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(
                ok=True, result={"ok": True, "held_item": {"name": "stone_axe", "count": 1}}
            )
        raise AssertionError(f"不该观察到这里：{tool}")

    with pytest.raises(ObservationFailed):
        await plan_resource_task("挖一块木头", observe=observe, block_name="oak_log")


async def test_planner_fails_honestly_when_the_world_has_no_target() -> None:
    from app.tasks.planner import ObservationFailed

    async def observe(tool: str, arguments: Mapping[str, Any]) -> TaskInvocation:
        if tool == "minecraft_find_blocks":
            return TaskInvocation(ok=True, result={"ok": True, "matches": []})
        raise AssertionError("没有目标就不该继续观察")

    with pytest.raises(ObservationFailed):
        await plan_resource_task("去找木头", observe=observe, block_name="oak_log")
