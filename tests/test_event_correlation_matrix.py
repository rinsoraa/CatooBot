"""Phase 7F.5.1 §五.B（事件关联矩阵）：记录实例 × 事件实例的**全部**组合。

矩阵（任务书 §四 / §五.B）：

| 记录 | 事件 | 期望 |
| --- | --- | --- |
| A（带身份追踪） | A | 正常结算 |
| A | B | 歧义，**不得**结算 |
| A | 缺失 | 歧义，**不得**结算（来源无法可靠验证） |
| 旧记录（无追踪） | 缺失 / 任意 | 旧格式回退路径：按 action_id 兼容结算 + 持久化标记 |
| 未知 action_id | — | 什么都不改 |
| 重复终态事件 | — | 第二次不结算 |
| 两条记录关联同一 action_id | — | 全部歧义 |

每一行都断言：最终任务状态、授权状态、dispatch_state、replan_required、
**持久化证据**（从 store 读回）以及**外部真实动作启动次数**。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.models import (
    DispatchState,
    ExpectedFinalState,
    StepState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.store import InMemoryTaskStore
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    armed_record,
    create,
    make_runtime,
    single_action_plan,
    start,
)

INSTANCE_A = "rt_aaaa_matrix"
INSTANCE_B = "rt_bbbb_matrix"


def two_action_plan() -> TaskPlan:
    """两步**都会动世界**的计划（用来验证"身份变化时绝不继续派发"）。"""
    return TaskPlan(
        objective="走两步",
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


async def _waiting(
    store: Any,
    *,
    instance: str = INSTANCE_A,
    plan: TaskPlan | None = None,
    session: str = "s",
) -> tuple[Any, Any, Any]:
    """造一条"外部已接受、正在等终态"的任务（动作由 instance 接受）。"""
    invoke = DispatchDouble(mode="detached")
    runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: instance)
    record = await create(runtime, plan or single_action_plan(), session=session)
    started = await start(runtime, record.task_id, session=session)
    return runtime, invoke, started


class TestEventCorrelationMatrix:
    async def test_same_instance_settles_normally(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        assert started.steps[0].dispatch_identity_tracked is True
        settled = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert settled is not None
        assert settled.state is TaskState.SUCCEEDED
        assert invoke.action_count == 1, "结算绝不产生新的世界动作"

    async def test_different_instance_is_ambiguous_and_never_settles(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        result = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_B,
        )
        assert result is None or result.state is TaskState.PAUSED
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.PAUSED
        assert stored.failure == TaskFailure.DISPATCH_AMBIGUOUS.value
        assert stored.recovery["detail"]["dispatch_reason"] == "stale_runtime_instance_event"
        assert stored.authorization is None, "歧义必须清掉授权"
        assert stored.replan_required is True
        assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert invoke.action_count == 1, "绝不因为一条迟到事件再派发"

    async def test_missing_event_instance_must_not_settle(self) -> None:
        """§四的核心修正：记录有实例、事件**缺**实例 -> 不得自动结算。"""
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        result = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance="",
        )
        assert result is None, "未结算的事件不返回记录（与既有歧义路径一致）"
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.PAUSED, "缺实例的事件绝不能把任务判成成功"
        assert stored.steps[0].state is StepState.FAILED
        assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert stored.recovery["detail"]["dispatch_reason"] == "missing_runtime_instance_event"
        assert stored.authorization is None and stored.replan_required is True
        assert invoke.action_count == 1

    @pytest.mark.parametrize("incoming", ["", INSTANCE_B])
    async def test_legacy_record_uses_documented_fallback(self, incoming: str) -> None:
        """旧持久化记录（7F.5.1 之前）：只保留明确界定的兼容行为 + 持久化标记。"""
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        legacy = await store.load(started.task_id)
        step = legacy.steps[0]
        step.dispatch_identity_tracked = False
        step.dispatch_runtime_instance = ""
        await store.save(legacy)
        result = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=incoming,
        )
        assert result is not None
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.SUCCEEDED, "旧格式行按 action_id 兼容结算"
        assert stored.steps[0].dispatch_legacy_fallback is True, "必须留下旧格式回退标记"
        assert stored.steps[0].dispatch_runtime_instance == incoming
        # 事件带了实例 -> 升级成"已追踪"；事件也没带 -> 这一行只能保持"无身份能力"。
        assert stored.steps[0].dispatch_identity_tracked is bool(incoming)
        assert invoke.action_count == 1

    async def test_unknown_action_id_changes_nothing(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        before = await store.load(started.task_id)
        result = await runtime.on_action_event(
            action_id="act_srv_unknown",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            runtime_instance=INSTANCE_A,
        )
        assert result is None
        after = await store.load(started.task_id)
        assert after.state is TaskState.WAITING_ACTION
        assert after.pending_action_id == before.pending_action_id
        assert after.authorization is not None, "未知事件不得动授权"
        assert after.steps[0].dispatch_state == DispatchState.ACCEPTED.value
        assert invoke.action_count == 1

    async def test_duplicate_terminal_event_does_not_settle_twice(self) -> None:
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        first = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert first is not None and first.state is TaskState.SUCCEEDED
        second = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert second is None, "重复终态事件必须被丢弃"
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.SUCCEEDED
        assert invoke.action_count == 1

    async def test_conflicting_multi_task_association_is_ambiguous(self) -> None:
        store = InMemoryTaskStore()
        runtime_a, invoke_a, task_a = await _waiting(store)
        # 第二个替身也发出 act_srv_1（真实世界里 action_id 重启后会重用序号）。
        # 注意换一个 session：同一 session 只允许一个前台任务（§二十六）。
        runtime_b, invoke_b, task_b = await _waiting(store, session="s2")
        await runtime_a.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        for task_id in (task_a.task_id, task_b.task_id):
            stored = await store.load(task_id)
            assert stored.state is TaskState.PAUSED, "冲突关联一律歧义，绝不挑一条结算"
            assert stored.recovery["detail"]["dispatch_reason"] == "action_id_ambiguous"
            assert stored.authorization is None
        assert invoke_a.action_count == 1 and invoke_b.action_count == 1

    async def test_late_event_from_old_instance_still_correlates(self) -> None:
        """旧实例迟到的终态：记录与事件都是 A -> 关联可验证 -> 正常结算且不再派发。"""
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store)
        runtime._runtime_instance_of = lambda: INSTANCE_B  # Runtime 已经换成 B
        settled = await runtime.on_action_event(
            action_id="act_srv_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
            runtime_instance=INSTANCE_A,
        )
        assert settled is not None
        stored = await store.load(started.task_id)
        assert stored.steps[0].state is StepState.SUCCEEDED
        assert invoke.action_count == 1, "结算本身绝不重新派发"


class TestDispatchIdentityGuard:
    """§三（派发顺序/并发）：身份不可信时**绝不**把动作发出去。"""

    async def test_changed_instance_blocks_dispatch_before_the_call(self) -> None:
        store = InMemoryTaskStore()
        invoke = DispatchDouble()
        runtime = make_runtime(invoke, store=store, runtime_instance_of=lambda: INSTANCE_A)
        record = await armed_record(
            runtime,
            store,
            plan=two_action_plan(),
            dispatch_state=DispatchState.SETTLED.value,
            generation=1,
        )
        armed = await store.load(record.task_id)
        step = armed.steps[0]
        step.state = StepState.PENDING
        step.dispatch_runtime_instance = INSTANCE_A
        step.dispatch_identity_tracked = True
        await store.save(armed)
        runtime._runtime_instance_of = lambda: INSTANCE_B
        driven = await runtime.drive(record.task_id)
        assert driven.state is TaskState.PAUSED
        assert invoke.action_count == 0, "实例变了就绝不派发"
        stored = await store.load(record.task_id)
        assert stored.state is TaskState.PAUSED
        detail = stored.recovery["detail"]
        assert detail["dispatch_reason"] == "runtime_instance_changed_before_dispatch"
        assert stored.authorization is None

    async def test_runtime_reachable_without_identity_is_fail_closed(self) -> None:
        async def probe() -> str:
            return ""  # Runtime 可达，但不报告身份

        store = InMemoryTaskStore()
        invoke = DispatchDouble()
        runtime = make_runtime(
            invoke,
            store=store,
            runtime_instance_of=lambda: "",
            identity_probe=probe,
        )
        record = await create(runtime, two_action_plan())
        blocked = await start(runtime, record.task_id)
        assert blocked.state is TaskState.PAUSED
        assert invoke.action_count == 0, "拿不到可信身份就不许发动作"
        stored = await store.load(record.task_id)
        assert stored.state is TaskState.PAUSED
        assert stored.recovery["detail"]["dispatch_reason"] == "runtime_instance_unavailable"
        assert stored.authorization is None and stored.replan_required is True

    async def test_unwired_runtime_keeps_legacy_behaviour(self) -> None:
        """没装配身份提供者 = 纯单测替身场景：沿用旧行为（不静默降级**生产**任务）。"""
        store = InMemoryTaskStore()
        invoke = DispatchDouble(mode="detached")
        runtime = make_runtime(invoke, store=store)
        record = await create(runtime, single_action_plan())
        started = await start(runtime, record.task_id)
        assert invoke.action_count == 1
        assert started.state is TaskState.WAITING_ACTION
        assert started.steps[0].dispatch_identity_tracked is False, (
            "没装 provider 就不假装有身份能力"
        )
        assert started.steps[0].dispatch_runtime_instance == ""

    async def test_instance_change_pauses_pending_action_without_redispatch(self) -> None:
        """§五.B「Runtime 已恢复但旧动作不可查询」：对账把旧动作判歧义，且此后不再派发。"""
        store = InMemoryTaskStore()
        runtime, invoke, started = await _waiting(store, plan=two_action_plan())
        assert invoke.action_count == 1
        affected = await runtime.on_runtime_instance_changed(INSTANCE_B)
        assert [item.task_id for item in affected] == [started.task_id]
        stored = await store.load(started.task_id)
        assert stored.state is TaskState.PAUSED
        detail = stored.recovery["detail"]
        assert detail["dispatch_reason"] == "runtime_instance_changed"
        assert detail["runtime_instance"] == INSTANCE_A
        assert detail["dispatch_key"] and detail["dispatch_generation"] == 1
        await runtime.drive(started.task_id)
        assert invoke.action_count == 1, "绝不允许因为 Runtime 又活了而重发旧动作"
        assert invoke.stops, "受控暂停必须真的尝试停掉前台动作"
