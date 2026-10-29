"""Phase 7F.5 §七（Runtime 生命周期）：故障注入测试。

断言对象与 7F.4 一致：**外部世界动作真实启动了几次**、任务状态、**授权状态**、
**派发代次**、从 store 读回的**持久化状态**，以及**事件到底匹配到了谁**。

十个场景（任务书 §七.1）：派发前 Runtime 未启动 / 意图已落盘而请求未发出 / 已接受但响应丢失 /
Node 在动作中重启而 Python 不重启 / 重启后新动作重用了旧序号 / 旧实例迟到事件 / 未决任务在最近
50 条之外 / 多条记录冲突关联同一 action_id / 健康检查暂时失败后恢复 / Runtime 已恢复但旧动作
不可查询。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.service import MinecraftService
from app.tasks.models import (
    DispatchState,
    StepState,
    TaskFailure,
    TaskState,
)
from app.tasks.runtime import TaskAuthorizationError
from app.tasks.store import InMemoryTaskStore
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    armed_record,
    create,
    make_runtime,
    single_action_plan,
    start,
)
from tests.test_bridge_auth_boundary import _Stub

INSTANCE_A = "rt_aaaa_instance"
INSTANCE_B = "rt_bbbb_instance"
INSTANCE_C = "rt_cccc_instance"


async def _waiting_task(store: Any, *, instance: str = INSTANCE_A) -> tuple[Any, Any, Any]:
    """造一个"外部已接受、正在等终态"的任务（动作由 `instance` 接受）。"""
    invoke = DispatchDouble(mode="detached")
    runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: instance)
    record = await create(runtime, single_action_plan())
    started = await start(runtime, record.task_id)
    return runtime, invoke, started


class TestRuntimeInstanceIdentity:
    """§三.1：实例身份 —— 不同实例的动作必须可区分。"""

    async def test_accepted_action_records_the_accepting_instance(self) -> None:
        store = InMemoryTaskStore()
        _, invoke, started = await _waiting_task(store)
        assert started.state is TaskState.WAITING_ACTION
        step = started.steps[0]
        assert step.dispatch_state == DispatchState.ACCEPTED.value
        assert step.dispatch_runtime_instance == INSTANCE_A, "必须记下接受它的实例"
        assert started.pending_action_id == "act_srv_1"
        assert invoke.action_count == 1

    async def test_matching_instance_event_settles_normally(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting_task(store)
        settled = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert settled is not None
        assert settled.state is TaskState.SUCCEEDED
        assert invoke.action_count == 1, "结算不得再派发任何动作"


class TestDispatchWindows:
    """§七.1（1）（2）（3）：派发前/中途的三个崩溃窗口。"""

    async def test_dispatch_before_runtime_started_is_fail_closed(self) -> None:
        # "runtime 没起来" 在传输层就是"没有可用的答复" → 歧义，绝不猜"没执行"。
        invoke = DispatchDouble(mode="lost_response")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: "")
        record = await create(runtime, single_action_plan())
        started = await start(runtime, record.task_id)
        assert invoke.action_count == 1, "请求确实发出去过一次"
        assert started.state is TaskState.PAUSED
        assert started.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert started.authorization is None and started.replan_required is True
        stored = await store.load(record.task_id)
        step = stored.steps[0]
        assert step.dispatch_state == DispatchState.AMBIGUOUS.value
        assert step.action_id == ""
        assert step.dispatch_runtime_instance == "", "从未确认过实例"
        assert step.dispatch_generation == 1 and step.dispatch_key
        await runtime.drive(record.task_id)
        assert invoke.action_count == 1, "歧义之后绝不允许第二次世界动作"

    async def test_persisted_intent_keeps_instance_evidence_and_is_not_retried(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: INSTANCE_A)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.INTENT.value,
            generation=1,
        )
        armed = await store.load(record.task_id)
        armed.steps[0].dispatch_runtime_instance = INSTANCE_A
        await store.save(armed)
        recovered = await runtime.recover(record.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert recovered.recovery["detail"]["runtime_instance"] == INSTANCE_A
        assert recovered.recovery["detail"]["dispatch_key"]
        assert invoke.action_count == 0

    async def test_ambiguous_dispatch_evidence_is_complete(self) -> None:
        # §五.2：状态未知时必须保存 原因 / 实例 / action_id / dispatch_key / 代次 / 时间戳。
        invoke = DispatchDouble(mode="lost_response")
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: INSTANCE_A)
        record = await create(runtime, single_action_plan())
        started = await start(runtime, record.task_id)
        detail = started.recovery["detail"]
        assert detail["dispatch_reason"] == "ambiguous_dispatch"
        assert detail["runtime_instance"] == INSTANCE_A
        assert detail["step_id"] == "step_1"
        assert detail["dispatch_generation"] == 1
        assert detail["dispatch_key"].startswith("dsp_")
        assert isinstance(detail["at"], float) and detail["at"] > 0


class TestRuntimeRestartReconciliation:
    """§七.1（4）（6）（10）：Node 重启而 Python 不重启。"""

    async def test_runtime_restart_mid_action_enters_explicit_ambiguity(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting_task(store)
        affected = await runtime.on_runtime_instance_changed(INSTANCE_B)
        assert [item.task_id for item in affected] == [started.task_id]
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.PAUSED
        assert stored.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert stored.authorization is None, "旧实例的未决动作必须清授权"
        assert stored.replan_required is True
        assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        detail = stored.recovery["detail"]
        assert detail["dispatch_reason"] == "runtime_instance_changed"
        assert detail["runtime_instance"] == INSTANCE_A
        assert detail["dispatch_key"] == stored.steps[0].dispatch_key
        assert detail["dispatch_generation"] == 1
        with pytest.raises(TaskAuthorizationError):
            await runtime.resume(started.task_id, user_id="u", session_id="s", origin="user")
        await runtime.drive(started.task_id)
        assert invoke.action_count == 1, "绝不因为新 runtime 可用就重新派发旧动作"

    async def test_stale_instance_event_does_not_settle_the_new_action(self) -> None:
        # §七.1（5）（6）：新实例产生了**同样的局部序号**（act_srv_1），旧实例的事件迟到。
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting_task(store)
        result = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_B,
        )
        assert result is None, "旧实例事件不得结算这个任务"
        stored = await store.load(started.task_id)
        assert stored.steps[0].state is not StepState.SUCCEEDED
        assert stored.state is TaskState.PAUSED
        assert stored.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert stored.recovery["detail"]["dispatch_reason"] == "stale_runtime_instance_event"
        assert stored.authorization is None
        assert invoke.action_count == 1

    async def test_runtime_back_but_old_action_unqueryable_stays_ambiguous(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting_task(store)
        await runtime.on_runtime_instance_changed(INSTANCE_B)
        # Runtime 又"活"了（第三个实例）—— 但旧动作的真实状态依然无法查询。
        runtime._runtime_instance_of = lambda: INSTANCE_C
        recovered = await runtime.recover(started.task_id)
        assert recovered.state is TaskState.PAUSED
        assert recovered.failure == TaskFailure.DISPATCH_AMBIGUOUS.value, (
            "不得因为新 Runtime 可用就把歧义洗掉"
        )
        await runtime.tick()
        stored = await store.load(started.task_id)
        assert stored.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        with pytest.raises(TaskAuthorizationError):
            await runtime.resume(started.task_id, user_id="u", session_id="s", origin="user")
        await runtime.drive(started.task_id)
        assert invoke.action_count == 1


class TestPendingActionIndex:
    """§四：未决动作查询必须完整、可重建，不受"最近 50 条"限制。"""

    async def test_pending_task_beyond_the_recent_window_is_still_found(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        # 先造最老的未决任务，再造 60 条更新的 —— 旧实现只看最近 50 条，会漏掉它。
        pending = await armed_record(
            runtime,
            store,
            plan=single_action_plan(x=100),
            action_id="act_old_1",
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            step_state=StepState.WAITING_ACTION,
            session="old-pending",
        )
        for index in range(60):
            await armed_record(
                runtime,
                store,
                plan=single_action_plan(x=400 + index),
                session=f"filler-{index}",
            )
        result = await runtime.on_action_event(
            action_id="act_old_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert result is not None, "窗口之外的未决任务也必须能被事件找到"
        assert result.task_id == pending.task_id
        stored = await store.load(pending.task_id)
        assert stored.steps[0].state is StepState.SUCCEEDED
        assert stored.pending_action_id == "", "消费后的关联必须持久化清理"

    async def test_conflicting_action_correlation_is_ambiguous(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        first = await armed_record(
            runtime,
            store,
            plan=single_action_plan(x=100),
            action_id="act_dup_1",
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            step_state=StepState.WAITING_ACTION,
            session="dup-a",
        )
        second = await armed_record(
            runtime,
            store,
            plan=single_action_plan(x=300),
            action_id="act_dup_1",
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            step_state=StepState.WAITING_ACTION,
            session="dup-b",
        )
        result = await runtime.on_action_event(
            action_id="act_dup_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert result is None, "冲突关联绝不能猜归属（不许取第一个匹配项）"
        for item in (first, second):
            stored = await store.load(item.task_id)
            assert stored.state is TaskState.PAUSED
            assert stored.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
            assert stored.steps[0].state is not StepState.SUCCEEDED
            assert stored.authorization is None
            assert stored.recovery["detail"]["dispatch_reason"] == "action_id_ambiguous"
        assert invoke.action_count == 0

    async def test_unknown_action_event_changes_nothing(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        pending = await armed_record(
            runtime,
            store,
            plan=single_action_plan(x=100),
            action_id="act_known_1",
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            step_state=StepState.WAITING_ACTION,
            session="known",
        )
        before = await store.load(pending.task_id)
        result = await runtime.on_action_event(
            action_id="act_never_seen",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert result is None
        after = await store.load(pending.task_id)
        assert after.state is before.state
        assert after.pending_action_id == "act_known_1"
        assert after.authorization is not None, "未知事件不得改变授权"
        assert after.steps[0].dispatch_generation == 1, "未知事件不得改变派发代次"

    async def test_duplicate_terminal_event_is_consumed_once(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting_task(store)
        first = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert first is not None
        after = invoke.action_count
        second = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert second is None, "重复终态事件只被接受一次"
        assert invoke.action_count == after
        stored = await store.load(started.task_id)
        assert stored.pending_action_id == ""
        assert stored.pending_step_id == ""

    async def test_recovery_rebuilds_correlated_pending_tasks_beyond_window(self) -> None:
        invoke = DispatchDouble()
        store = InMemoryTaskStore()
        runtime = make_runtime(invoke, store=store)
        pending = await armed_record(
            runtime,
            store,
            plan=single_action_plan(x=100),
            action_id="act_rec_1",
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            step_state=StepState.WAITING_ACTION,
            session="rec-pending",
        )
        for index in range(60):
            await armed_record(
                runtime,
                store,
                plan=single_action_plan(x=500 + index),
                session=f"rec-filler-{index}",
            )
        recovered = await runtime.recover_persisted_tasks()
        ids = [item.task_id for item in recovered]
        assert pending.task_id in ids, "恢复必须重建所有相关联的未决任务，而不是固定条数"
        stored = await store.load(pending.task_id)
        assert stored.state is TaskState.PAUSED
        assert stored.failure == TaskFailure.RUNTIME_RESTART.value
        assert stored.authorization is None and stored.replan_required is True
        assert stored.pending_action_id == ""


class TestInstanceChangeDetection:
    """§七.1（9）：单次健康检查失败**不足以**推断实例已经换人。"""

    def test_transient_failure_does_not_change_instance(self) -> None:
        stub = _Stub()
        seen: list[str] = []
        stub.on_runtime_instance_changed = seen.append
        MinecraftService._note_runtime_instance(stub, {"runtime_instance": INSTANCE_A})
        assert stub._runtime_instance == INSTANCE_A
        assert seen == [], "首次见到实例只记录、不通知（还没有旧动作要分类）"
        # 失败 / 没有身份的响应：绝不改变实例、绝不触发对账。
        MinecraftService._note_runtime_instance(
            stub, {"ok": False, "error": {"code": "runtime.unreachable"}}
        )
        MinecraftService._note_runtime_instance(stub, {"ok": True})
        MinecraftService._note_runtime_instance(stub, None)
        MinecraftService._note_runtime_instance(stub, {"runtime_instance": ""})
        assert stub._runtime_instance == INSTANCE_A
        assert seen == []
        # 同一个实例重复成功：不重复告警。
        MinecraftService._note_runtime_instance(stub, {"runtime_instance": INSTANCE_A})
        assert seen == []
        # 真的换人（成功的身份响应）→ 恰好通知一次。
        MinecraftService._note_runtime_instance(stub, {"runtime_instance": INSTANCE_B})
        assert stub._runtime_instance == INSTANCE_B
        assert seen == [INSTANCE_B]
