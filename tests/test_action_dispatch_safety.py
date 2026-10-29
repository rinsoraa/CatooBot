"""Phase 7F.4：动作派发与恢复安全的**故障注入**测试。

核心命题（任务书 §二）：恢复时**不得**把「动作状态未知」当成「动作没有执行」，
然后无条件重新派发。因此本文件每个用例断言的都不是"函数返回了什么"，而是：

* **外部动作真实启动了几次**（替身记录每一次真实派发 —— 这是最关键的原始证据）；
* **真实 action_id / 关联键 / 派发代次**是否落盘且可追溯；
* **持久化状态**（从 store load 回来的对象，不是内存里的那个）；
* **最终世界动作数**（恢复 / 重放之后有没有多出一次世界动作）。

故障注入方式：可控的 store 替身（在指定的持久化点"断电"）+ 可编排的执行替身
（模拟"runtime 已接受动作但响应丢失"）。不通过杀生产进程来模拟崩溃。
"""

from __future__ import annotations

import time
from collections.abc import Mapping
from typing import Any

import pytest

from app.tasks.models import (
    DispatchState,
    ExpectedFinalState,
    StepState,
    TaskAuthorization,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
    dispatch_key_of,
)
from app.tasks.runtime import (
    TaskAuthorizationError,
    TaskConfig,
    TaskInvocation,
    TaskRuntime,
)
from app.tasks.store import InMemoryTaskStore
from tests.test_multi_leg_explore import LegWorld, build_route
from tests.test_task_runtime import FakeConfirmation

REGISTRY = {
    "minecraft_world": "SAFE",
    "minecraft_look_at": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_stop": "SAFE",
    "minecraft_inventory": "SAFE",
}

#: 真正会**动世界**的工具（SAFE 读单独统计，不算"外部世界动作"）。
WORLD_ACTION_TOOLS = frozenset({"minecraft_move_to"})


def single_action_plan(*, x: int = 100, y: int = 64, z: int = 200, radius: float = 2.0) -> TaskPlan:
    """一个两步计划：LOW 移动 + 一次 SAFE 世界读（末尾复核用）。"""

    return TaskPlan(
        objective="走到目标点",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={"x": x, "y": y, "z": z},
                risk="LOW",
            ),
            TaskStep(step_id="step_2", tool="minecraft_world", arguments={}, risk="SAFE"),
        ],
        expected_final_state=ExpectedFinalState(
            position_within={"x": float(x), "y": float(y), "z": float(z), "radius": radius}
        ),
    )


class DispatchDouble:
    """可编排的执行替身 —— 记录**每一次真实派发**。

    ``mode``：

    * ``sync``：同步成功（终态立刻已知）；
    * ``detached``：持续型动作（返回 RUNNING + action_id，终态等事件）；
    * ``fail``：runtime 明确回复失败（终态**已知**，不是歧义）；
    * ``lost_response``：外部**可能已经接受**，但调用方拿不到答复（抛异常）。
    """

    def __init__(
        self,
        *,
        mode: str = "sync",
        start: tuple[float, float, float] = (0.0, 64.0, 0.0),
    ) -> None:
        self.mode = mode
        self.position = {"x": start[0], "y": start[1], "z": start[2]}
        self.actions: list[dict[str, Any]] = []
        self.reads: list[str] = []
        #: 控制面调用（minecraft_stop）—— 受控暂停必须真的尝试停下来。
        self.stops: list[str] = []
        self._seq = 0

    @property
    def action_count(self) -> int:
        """**真实发起过的世界动作次数** —— 本文件最核心的断言对象。"""
        return len(self.actions)

    def action_targets(self) -> list[tuple[float, float, float]]:
        return [
            (
                float(item["arguments"]["x"]),
                float(item["arguments"]["y"]),
                float(item["arguments"]["z"]),
            )
            for item in self.actions
        ]

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
        if tool == "minecraft_world":
            self.reads.append(tool)
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={
                    "available": True,
                    "online": True,
                    "semantic": {"self": {"position": dict(self.position)}},
                },
            )
        if tool in {"minecraft_look_at", "minecraft_stop", "minecraft_inventory"}:
            if tool == "minecraft_stop":
                self.stops.append(tool)
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})
        if tool not in WORLD_ACTION_TOOLS:
            raise AssertionError(f"意外工具 {tool}")
        self._seq += 1
        self.actions.append({"tool": tool, "arguments": dict(arguments), "seq": self._seq})
        if self.mode == "lost_response":
            # 请求已经发出去了（外面可能已经接受），但调用方永远等不到答复。
            raise RuntimeError("connection reset while awaiting response")
        if self.mode == "detached":
            self.position = {
                "x": float(arguments["x"]),
                "y": float(arguments["y"]),
                "z": float(arguments["z"]),
            }
            return TaskInvocation(
                ok=True, status="RUNNING", action_id=f"act_srv_{self._seq}", summary="正在过去"
            )
        if self.mode == "fail":
            return TaskInvocation(
                ok=False, status="FAILED", error="找不到路", code="minecraft.path_not_found"
            )
        self.position = {
            "x": float(arguments["x"]),
            "y": float(arguments["y"]),
            "z": float(arguments["z"]),
        }
        return TaskInvocation(
            ok=True, status="SUCCEEDED", result={"ok": True}, action_id=f"act_srv_{self._seq}"
        )


class SimulatedCrash(RuntimeError):
    """模拟"进程在某个持久化点断电"（不用 KeyboardInterrupt：那是 pytest 的会话级信号）。"""


class CrashOnEventStore(InMemoryTaskStore):
    """在第 ``skip + 1`` 次带指定事件的 save 上"断电"（**写入之前**抛出，只崩一次）。

    只崩一次是有意的：崩溃之后就是**新进程 + 新 store**，恢复流程必须能正常跑完。
    """

    def __init__(self, *, crash_event: str, skip: int = 0) -> None:
        super().__init__()
        self.crash_event = crash_event
        self.skip = skip
        self.fired = False
        self.saves: list[str] = []

    async def save(
        self, record: Any, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        self.saves.append(event)
        if not self.fired and event == self.crash_event and self.saves.count(event) > self.skip:
            self.fired = True
            raise SimulatedCrash("simulated crash before persisting")
        await super().save(record, event=event, step_id=step_id, detail=detail)


def make_runtime(invoke: Any, *, store: Any = None, **overrides: Any) -> TaskRuntime:
    return TaskRuntime(
        store=store if store is not None else InMemoryTaskStore(),
        invoke=invoke,
        confirmations=overrides.pop("confirmations", None) or FakeConfirmation(),
        config=overrides.pop("config", None)
        or TaskConfig(position_verify_attempts=1, position_verify_interval_seconds=0.0),
        publish=lambda event, payload: None,
        risk_of=lambda tool: REGISTRY.get(tool, ""),
        is_registered=lambda tool: tool in REGISTRY,
        **overrides,
    )


async def create(engine: TaskRuntime, plan: TaskPlan, *, session: str = "s") -> Any:
    return await engine.create_task(
        plan.objective, session_id=session, user_id="u", origin="user", plan=plan
    )


async def start(engine: TaskRuntime, task_id: str, *, session: str = "s") -> Any:
    return await engine.confirm_and_start(task_id, user_id="u", session_id=session, origin="user")


async def armed_record(
    engine: TaskRuntime,
    store: Any,
    *,
    plan: TaskPlan,
    dispatch_state: str = "",
    generation: int = 0,
    action_id: str = "",
    step_state: StepState = StepState.PENDING,
    step_index: int = 0,
    expires_in: float = 60.0,
    session: str = "s",
) -> Any:
    """造一份「崩溃之后」的**持久化**记录：RUNNING + 有效授权 + 指定的派发痕迹。

    这是可重复的故障注入：直接构造崩溃那一刻磁盘上应有的状态（而不是杀进程）。
    """

    record = await create(engine, plan, session=session)
    now = time.time()
    record.authorization = TaskAuthorization(
        task_id=record.task_id,
        user_id="u",
        session_id=session,
        plan_hash=record.plan_hash,
        approved_at=now,
        expires_at=now + float(expires_in),
        plan_version=record.plan_version,
    )
    record.mark_plan_confirmed(now=now)
    record.state = TaskState.RUNNING
    step = record.steps[step_index]
    step.state = step_state
    step.dispatch_state = dispatch_state
    step.dispatch_generation = generation
    if generation:
        step.dispatch_key = dispatch_key_of(
            record.task_id, record.plan_hash, step.step_id, generation
        )
        step.dispatch_at = now
    step.action_id = action_id
    if action_id:
        record.pending_action_id = action_id
        record.pending_step_id = step.step_id
    await store.save(record)
    return record


class TestCrashBeforeDispatchIntent:
    """崩溃点 1：**写入派发意图之前**崩溃。"""

    async def test_crash_before_intent_persisted_proves_no_action_was_sent(self) -> None:
        # 意图是在**调用工具之前**落盘的（app/tasks/runtime.py 的 _run_step）。
        # 所以"磁盘上连意图都没有"就等于"外部一步都没走" -> 这里重试是**安全**的。
        invoke = DispatchDouble()
        store = CrashOnEventStore(crash_event="task.step_started")
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        with pytest.raises(SimulatedCrash):
            await start(runtime, record.task_id)
        assert invoke.action_count == 0, "崩在意图落盘之前 -> 外部一次都不该被调用"
        stored = await store.load(record.task_id)
        assert stored is not None
        assert stored.steps[0].dispatch_state == "", "没有意图痕迹"
        assert stored.steps[0].dispatch_generation == 0
        # 恢复之后**可以**安全地重新开始（没有任何需要怀疑的外部副作用）
        await runtime.recover(record.task_id)
        resumed = await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        assert resumed.state is TaskState.SUCCEEDED
        assert invoke.action_count == 1, "恢复后恰好派发一次（不会再多）"


class TestPersistedIntentWithoutActionId:
    """崩溃点 2 / 3：意图已落盘，但没有 action_id（可能已发出、也可能没发出）。"""

    async def test_intent_without_action_id_is_fail_closed(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        assert invoke.action_count == 0
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert recovered.authorization is None, "歧义必须清授权（否则 resume 就能接着跑）"
        assert recovered.replan_required is True
        assert recovered.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert recovered.steps[0].dispatch_unresolved is True
        # 绝不重发：resume() 与 drive() 都不允许产生世界动作
        with pytest.raises(TaskAuthorizationError) as excinfo:
            await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        assert excinfo.value.code == "task.replan_required"
        await runtime.drive(record.task_id)
        assert invoke.action_count == 0, "歧义状态下 drive() 绝不能重新派发"

    async def test_resume_still_refuses_when_replan_flag_is_cleared(self) -> None:
        # 纵深防御：即使有人把 replan_required 抹掉，未决派发本身也必须拦住 resume。
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        recovered = await runtime.recover(record.task_id)
        recovered.replan_required = False
        recovered.state = TaskState.RUNNING
        await store.save(recovered)
        with pytest.raises(TaskAuthorizationError) as excinfo:
            await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        assert excinfo.value.code == "task.dispatch_unresolved"
        assert invoke.action_count == 0

    async def test_lost_response_after_acceptance_never_re_dispatches(self) -> None:
        # 崩溃点 3 的端到端版本：runtime **可能已经接受**了动作，但调用方拿不到答复。
        invoke = DispatchDouble(mode="lost_response")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        assert invoke.action_count == 1, "外部确实被调用过一次"
        assert started.state is TaskState.PAUSED
        assert started.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert started.authorization is None
        assert started.replan_required is True
        stored = await store.load(record.task_id)
        assert stored is not None
        step = stored.steps[0]
        assert step.dispatch_state == DispatchState.AMBIGUOUS.value
        assert step.dispatch_generation == 1, "代次已落盘（可审计的新一次派发）"
        assert step.dispatch_key, "关联键已落盘（供人工核对）"
        assert step.action_id == "", "响应丢了 -> 没有 action_id（这正是歧义来源）"
        assert invoke.stops == ["minecraft_stop"], "受控暂停必须真的尝试停掉可能还在跑的动作"
        await runtime.recover(record.task_id)
        with pytest.raises(TaskAuthorizationError):
            await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        await runtime.drive(record.task_id)
        assert invoke.action_count == 1, "歧义之后绝不允许第二次世界动作"


class TestAcceptedActionRecovery:
    """崩溃点 4 / 5：action_id 已落盘（等终态 / 终态没落盘）。"""

    async def test_action_id_persisted_but_not_waiting_is_invalidated_not_retried(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime,
            store,
            plan=plan,
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id="act_srv_1",
            step_state=StepState.WAITING_ACTION,
        )
        assert invoke.action_count == 0
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.RUNTIME_RESTART.value
        assert recovered.authorization is None
        assert recovered.replan_required is True
        assert recovered.steps[0].state is StepState.FAILED
        assert recovered.pending_action_id == "", "旧的 action 关联必须失效"
        await runtime.drive(record.task_id)
        assert invoke.action_count == 0, "旧动作一律不重做"

    async def test_action_finished_but_terminal_unsaved_never_re_dispatches(self) -> None:
        # 持续型动作：外部动作其实已经结束，但终态事件永远没送达 / 没落盘。
        invoke = DispatchDouble(mode="detached")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        assert started.state is TaskState.WAITING_ACTION
        assert started.pending_action_id == "act_srv_1"
        assert invoke.action_count == 1
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.RUNTIME_RESTART.value
        assert recovered.authorization is None
        assert recovered.replan_required is True
        assert invoke.action_count == 1, "恢复不得再发一次世界动作"
        await runtime.drive(record.task_id)
        assert invoke.action_count == 1


class TestReplayAndExplicitResume:
    """崩溃点 6 / 7：重复恢复、重复事件、以及"动作明确终止后用户显式恢复"。"""

    async def test_repeated_recovery_is_idempotent_and_keeps_classification(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        first = await runtime.recover(record.task_id)
        assert first.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        second = await runtime.recover(record.task_id)
        assert second.failure == TaskFailure.DISPATCH_AMBIGUOUS.value, "分类不被覆盖"
        assert second.recovery.get("reason") == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert second.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert invoke.action_count == 0

    async def test_duplicate_terminal_event_is_ignored(self) -> None:
        invoke = DispatchDouble(mode="detached")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        await start(runtime, record.task_id)
        first = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert first is not None
        after = invoke.action_count
        second = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert second is None, "同一个终态事件重复投递不得再推进任务"
        assert invoke.action_count == after

    async def test_unknown_action_event_changes_nothing(self) -> None:
        invoke = DispatchDouble(mode="detached")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        result = await runtime.on_action_event(
            action_id="act_other",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert result is None, "关联不上的事件必须被丢弃，绝不猜"
        stored = await store.load(record.task_id)
        assert stored is not None
        assert stored.state is TaskState.WAITING_ACTION
        assert stored.pending_action_id == started.pending_action_id
        assert invoke.action_count == 1

    async def test_explicit_resume_after_clear_failure_uses_a_new_generation(self) -> None:
        # 动作**明确失败**（runtime 给了答复 -> 终态已知）之后，用户显式恢复仍然允许；
        # 但必须是可审计的**新代次 + 新关联键**，绝不能看起来像同一次派发。
        invoke = DispatchDouble(mode="fail")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        assert invoke.action_count == 1
        assert started.state is TaskState.PAUSED
        assert started.failure == TaskFailure.ACTION_FAILED.value
        first = started.steps[0]
        assert first.dispatch_state == DispatchState.SETTLED.value, "终态已知 -> 不是歧义"
        first_key, first_generation = first.dispatch_key, first.dispatch_generation
        invoke.mode = "sync"
        resumed = await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        assert resumed.state is TaskState.SUCCEEDED
        assert invoke.action_count == 2, "显式恢复 -> 恰好再来一次"
        assert resumed.steps[0].dispatch_generation == first_generation + 1
        assert resumed.steps[0].dispatch_key != first_key, "新一次派发必须是新键"


class TestAuthorizationDuringCrash:
    """崩溃点 8：崩溃期间任务被取消 / 过期。"""

    async def test_cancelled_task_with_intent_is_not_resurrected(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        cancelled = await runtime.cancel(record.task_id, reason="user cancel")
        assert cancelled.state is TaskState.CANCELLED
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.CANCELLED, "终态任务绝不因为恢复被复活"
        assert invoke.action_count == 0
        with pytest.raises(TaskAuthorizationError):
            await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")

    async def test_expired_task_with_intent_is_not_resurrected(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        expired = await runtime.expire(record.task_id, reason="ttl")
        assert expired.state is TaskState.EXPIRED
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.EXPIRED
        assert invoke.action_count == 0


class TestMultiLegRecovery:
    """崩溃点 9：多段路线 — 未决段拦住后续段；前序路点仍先验证。"""

    async def test_unresolved_current_leg_blocks_every_later_leg(self) -> None:
        world = LegWorld()
        store = InMemoryTaskStore()
        runtime = make_runtime(world, store=store)
        plan = await build_route(legs=3)
        assert len(plan.expected_final_state.legs()) == 3
        record = await armed_record(
            runtime, store, plan=plan, dispatch_state=DispatchState.INTENT.value, generation=1
        )
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert recovered.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert world.move_plan() == [], "第 1 段未决 -> 一步都不许走"
        assert recovered.steps[2].state is StepState.PENDING, "第 2 段从未执行"
        assert recovered.steps[4].state is StepState.PENDING, "第 3 段从未执行"

    async def test_recovery_still_verifies_previous_leg_before_the_next_move(self) -> None:
        # 第 1 段移动已成功、但还没做逐段验证（7F.3 的崩溃窗口）-> 恢复后再次驱动，
        # 必须**先补验第 1 段**才允许第 2 段移动（绝不能因为恢复而跳过逐段验证）。
        route_plan = await build_route(legs=3)
        first_target = route_plan.expected_final_state.legs()[0]["position_within"]
        world = LegWorld(start=(first_target["x"], first_target["y"], first_target["z"]))
        store = InMemoryTaskStore()
        runtime = make_runtime(world, store=store)
        record = await armed_record(
            runtime, store, plan=route_plan, step_state=StepState.SUCCEEDED, step_index=0
        )
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.authorization is not None, "没有未决派发 -> 授权不动"
        resumed = await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        assert resumed.state is TaskState.SUCCEEDED
        calls = world.calls
        verify_index = calls.index("minecraft_world")
        move_indices = [i for i, tool in enumerate(calls) if tool == "minecraft_move_to"]
        assert move_indices, "第 2/3 段应当继续执行"
        assert verify_index < move_indices[0], "第 1 段的补验必须先于第 2 段的移动"
        assert [entry["index"] for entry in resumed.leg_verifications] == [1, 2, 3]


class TestDispatchKeyStability:
    """关联键与代次的定义（任务书 §三.2）：同代次同键、新代次新键、重启不改键。"""

    async def test_key_is_stable_across_runtime_instances_and_generations(self) -> None:
        invoke = DispatchDouble(mode="fail")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        step = started.steps[0]
        assert step.dispatch_generation == 1
        expected_key = dispatch_key_of(record.task_id, started.plan_hash, "step_1", 1)
        assert step.dispatch_key == expected_key
        # "重启"：换一个 runtime 实例读同一份持久化记录 -> 键与代次都不变
        fresh = make_runtime(DispatchDouble(), store=store)
        reloaded = await fresh.get(record.task_id)
        assert reloaded is not None
        assert reloaded.steps[0].dispatch_generation == 1
        assert reloaded.steps[0].dispatch_key == expected_key
        # 同一代次重复计算得到同一个键；新代次必然是新键
        assert dispatch_key_of(record.task_id, started.plan_hash, "step_1", 1) == expected_key
        assert dispatch_key_of(record.task_id, started.plan_hash, "step_1", 2) != expected_key

    async def test_every_new_dispatch_attempt_persists_its_intent_first(self) -> None:
        # 每次真实派发之前都先落盘意图：所以"意图的条数"= "外部动作尝试的次数"。
        invoke = DispatchDouble()
        store = CrashOnEventStore(crash_event="__never__")
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        await start(runtime, record.task_id)
        assert invoke.action_count == 1
        assert store.saves.count("task.step_started") >= 1, "派发前必须有一次意图落盘"
        stored = await store.load(record.task_id)
        assert stored is not None
        assert stored.steps[0].dispatch_state == DispatchState.SETTLED.value
        assert stored.steps[0].dispatch_generation == 1


class TestTransportFailureAmbiguity:
    """传输层失败必须被当成**歧义**，而不是"终态已知的失败"。"""

    def test_transport_codes_are_classified_ambiguous(self) -> None:
        # 工具层保证不抛异常（agent.py 的"工具层永不抛给模型"），所以
        # runtime.unreachable / runtime.bad_response 会被转成**普通失败结果** ——
        # 如果不在转换点按 code 判定，它们就会被当成"失败过、可以重试"。
        from app.integrations.minecraft.task_adapter import (
            AMBIGUOUS_TRANSPORT_CODES,
            _to_invocation,
        )

        class _Result:
            success = False
            data: dict[str, Any] = {"status": "FAILED"}
            error = "桥接没有响应"
            summary = ""

        def make(code: str) -> Any:
            return type("R", (_Result,), {"error_type": code})()

        unreachable = _to_invocation("minecraft_move_to", make("runtime.unreachable"))
        assert unreachable.ok is False and unreachable.ambiguous is True
        bad = _to_invocation("minecraft_move_to", make("runtime.bad_response"))
        assert bad.ambiguous is True
        # runtime 给出的**明确答复**（终态已知）不算歧义
        assert (
            _to_invocation("minecraft_move_to", make("minecraft.path_not_found")).ambiguous is False
        )
        assert _to_invocation("minecraft_move_to", make("runtime.error")).ambiguous is False
        assert {
            "runtime.unreachable",
            "runtime.bad_response",
            "minecraft.runtime_unreachable",
        } == AMBIGUOUS_TRANSPORT_CODES

    async def test_transport_failure_end_to_end_pauses_without_re_dispatch(self) -> None:
        class UnreachableDouble(DispatchDouble):
            async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
                if tool == "minecraft_move_to":
                    self._seq += 1
                    self.actions.append(
                        {"tool": tool, "arguments": dict(arguments), "seq": self._seq}
                    )
                    # 与 task_adapter 现在的行为一致：传输失败 -> 普通失败结果 + 歧义
                    return TaskInvocation(
                        ok=False,
                        status="FAILED",
                        error="桥接没有响应",
                        code="runtime.unreachable",
                        ambiguous=True,
                    )
                return await super().__call__(tool, arguments, **kwargs)

        invoke = UnreachableDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        assert invoke.action_count == 1
        assert started.state is TaskState.PAUSED
        assert started.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert started.authorization is None
        assert started.replan_required is True
        with pytest.raises(TaskAuthorizationError):
            await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        await runtime.drive(record.task_id)
        assert invoke.action_count == 1, "传输失败之后绝不允许第二次世界动作"


class TestActionLinkageFidelity:
    """审计保真：落盘的 action_id 必须**真的**属于当前这一次派发。"""

    async def test_new_dispatch_attempt_clears_the_previous_action_linkage(self) -> None:
        # 第 1 次派发是持续型（action_id=act_srv_1）-> 事件说明它**明确失败**
        # -> 用户显式恢复 -> 第 2 次派发失败且**没有** action_id。
        # 落盘的 action_id 必须是空，绝不能残留上一次的 act_srv_1。
        invoke = DispatchDouble(mode="detached")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        plan = single_action_plan()
        record = await create(runtime, plan)
        started = await start(runtime, record.task_id)
        assert started.steps[0].action_id == "act_srv_1"
        assert started.steps[0].dispatch_state == DispatchState.ACCEPTED.value
        failed = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.failed",
            status="FAILED",
            error="找不到路",
            code="minecraft.path_not_found",
        )
        assert failed is not None and failed.state is TaskState.PAUSED
        assert failed.steps[0].action_id == "act_srv_1", "这一次的关联要如实保留"
        assert failed.steps[0].dispatch_state == DispatchState.SETTLED.value
        invoke.mode = "fail"
        resumed = await runtime.resume(record.task_id, user_id="u", session_id="s", origin="user")
        step = resumed.steps[0]
        assert step.dispatch_generation == 2
        assert step.action_id == "", "不得残留上一次派发的 action_id"
        assert step.dispatch_state == DispatchState.SETTLED.value
