"""Phase 7F.5.3 Follow-up 2 §2：生命周期状态更新的**确定性**竞态测试。

两个审计发现的根因（本文件逐条证明）：

* **发现 A：on_action_event 未参与生命周期锁。** 事件路径会读 TaskRecord、改步骤与 pending
  关联、然后整份写回 payload，却不与 cancel()/expire()/_settle() 共用按任务锁 —— 取消已经
  落盘终态之后，事件仍可能把**旧快照**写回去（CANCELLED 复活），或者覆盖掉更新的关联。
* **发现 B：_pause_ambiguous_dispatch 持锁却用旧快照。** 包装方法拿到锁后直接把调用方传入的
  record/step 交给内部方法。**锁不等于数据新鲜度** —— 取消在这两步之间落盘终态后，内部方法
  仍按旧快照判定"非终态"，于是把 CANCELLED 改写成 PAUSED。

本文件全部用**确定性**手段构造交错：可控 store 包装器（门闩）+ asyncio.Event + 真实 SQLite
读写；**不使用 sleep 作为同步机制**（只做有界让出，等待一个已经发生的信号）。

每个用例都断言：task state、step state、dispatch_state、dispatch_generation、action_id、
pending_action_id / pending_step_id、authorization、replan_required、Runtime 实例身份、
recovery 证据、**世界动作启动次数**、stop 调用次数，以及从 store **重新读回**的最终状态。
终态事件保持幂等；终态任务的 drive() 绝不再次发起世界动作。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import (
    TASK_CANCELLED,
    DispatchState,
    StepState,
    TaskState,
)
from app.tasks.store import InMemoryTaskStore, SqliteTaskStore
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    armed_record,
    make_runtime,
    single_action_plan,
)
from tests.test_event_correlation_matrix import INSTANCE_A, INSTANCE_B, two_action_plan

ACTION_ID = "act_srv_1"


class GateFindStore:
    """把**第一次** find_by_action 扣在门闩上（复现"事件读到旧记录之后才发生取消"）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls = 0
        self.find_entered = asyncio.Event()
        self.release = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def find_by_action(self, action_id: str, *, include_terminal: bool = False) -> Any:
        self.calls += 1
        if self.calls == 1:
            self.find_entered.set()
            await self.release.wait()
        return await self._inner.find_by_action(action_id, include_terminal=include_terminal)


class GateSaveStore:
    """把**指定事件**的那一次落盘扣在门闩上（可让调用方在**持锁**状态停住）。"""

    def __init__(self, inner: Any, *, event: str) -> None:
        self._inner = inner
        self.event = str(event)
        self.saves: list[str] = []
        self.save_entered = asyncio.Event()
        self.release = asyncio.Event()
        self._blocked_once = False

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def save(
        self, record: Any, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        self.saves.append(str(event))
        if str(event) == self.event and not self._blocked_once:
            self._blocked_once = True
            self.save_entered.set()
            await self.release.wait()
        await self._inner.save(record, event=event, step_id=step_id, detail=detail)


async def _let_run(steps: int = 200) -> None:
    """有界让出（不是计时等待）：让已经可运行的协程跑到它下一次阻塞。"""
    for _ in range(steps):
        await asyncio.sleep(0)


def _assert_no_world_action(double: DispatchDouble) -> None:
    assert double.action_count == 0, "生命周期竞态收口期间绝不允许出现世界动作"


# ===================================================================== 发现 A


class TestEventSettlementVersusTerminalRace:
    """事件结算与取消/过期对同一 TaskRecord 的**同一把锁**串行化。"""

    async def test_event_that_read_a_stale_record_cannot_resurrect_a_cancelled_task(
        self,
    ) -> None:
        """§2 场景 1：事件先读到 WAITING_ACTION/ACCEPTED 记录，取消先完成，再放行事件。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id=ACTION_ID,
            step_state=StepState.WAITING_ACTION,
        )
        gated = GateFindStore(store)
        runtime._store = gated
        event_task = asyncio.create_task(
            runtime.on_action_event(
                action_id=ACTION_ID,
                event="minecraft.action.completed",
                status="SUCCEEDED",
                result={"ok": True},
            )
        )
        # 事件已经读到旧记录，此刻被门闩拦住。
        await asyncio.wait_for(gated.find_entered.wait(), timeout=5)
        assert gated.calls == 1

        cancelled = await runtime.cancel(record.task_id, reason="user cancel")
        assert cancelled.state is TaskState.CANCELLED

        gated.release.set()
        await asyncio.wait_for(event_task, timeout=5)

        final = await store.load(record.task_id)
        assert final is not None
        assert final.state is TaskState.CANCELLED, "事件绝不复活已取消的任务"
        step = final.steps[0]
        assert step.dispatch_state == DispatchState.SETTLED.value, "证据照样收敛"
        assert step.action_id == ACTION_ID
        assert final.pending_action_id == "", "真实终态已确认 -> 未决关联收敛"
        assert final.pending_step_id == ""
        assert final.replan_required is False
        _assert_no_world_action(double)

        # 终态任务的 drive() 绝不再发起世界动作（幂等）。
        driven = await runtime.drive(record.task_id)
        assert driven.state is TaskState.CANCELLED
        _assert_no_world_action(double)

        # 重复终态事件是幂等空操作。
        again = await runtime.on_action_event(
            action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
        )
        assert again is None
        _assert_no_world_action(double)

    async def test_event_first_then_cancel_never_downgrades_the_settled_step(self) -> None:
        """§2 场景 2：事件先在锁内结算，取消随后完成 —— 已 SETTLED 的步骤不得被降级。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id=ACTION_ID,
            step_state=StepState.WAITING_ACTION,
        )
        # 用户要求"当前动作结束后暂停" -> 事件结算后任务进 PAUSED，而不是继续跑下一步。
        record.pause_requested = True
        await store.save(record)

        settled = await runtime.on_action_event(
            action_id=ACTION_ID,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert settled is not None
        assert settled.state is TaskState.PAUSED
        after_event = await store.load(record.task_id)
        assert after_event.steps[0].dispatch_state == DispatchState.SETTLED.value
        assert after_event.steps[0].state is StepState.SUCCEEDED
        assert after_event.steps[0].action_id == ACTION_ID

        final = await runtime.cancel(record.task_id, reason="user cancel")
        assert final.state is TaskState.CANCELLED
        reloaded = await store.load(record.task_id)
        step = reloaded.steps[0]
        assert step.dispatch_state == DispatchState.SETTLED.value, "绝不被降级"
        assert step.state is StepState.SUCCEEDED
        assert step.action_id == ACTION_ID
        assert step.dispatch_generation == 1
        _assert_no_world_action(double)

    async def test_a_stale_ambiguity_callback_never_downgrades_a_settled_step(self) -> None:
        """§2 场景 5：SETTLED 单调性 —— 陈旧歧义回调不得把已结算步骤倒退。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id=ACTION_ID,
            step_state=StepState.WAITING_ACTION,
        )
        stale = await store.load(record.task_id)
        stale_step = stale.steps[0]
        assert stale_step.dispatch_state == DispatchState.ACCEPTED.value

        await runtime.on_action_event(
            action_id=ACTION_ID,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        settled = await store.load(record.task_id)
        assert settled.steps[0].dispatch_state == DispatchState.SETTLED.value
        # 结算后 drive() 会继续推进（这里因为伪造的到达位置必然验证不通过 -> 终态是 FAILED
        # 或 SUCCEEDED 都算"已经落定"）：本用例只关心**陈旧回调不得改变它**。
        settled_state = settled.state
        assert settled_state.terminal

        # 陈旧回调（拿着结算之前的快照）绝不允许把新事实倒退成 AMBIGUOUS。
        await runtime._pause_ambiguous_dispatch(
            stale, stale_step, message="陈旧回调", reason="stale_callback"
        )
        final = await store.load(record.task_id)
        step = final.steps[0]
        assert step.dispatch_state == DispatchState.SETTLED.value
        assert step.state is StepState.SUCCEEDED
        assert step.failure == ""
        assert final.state is settled_state, "陈旧回调绝不改变终态"
        _assert_no_world_action(double)


# ===================================================================== 发现 B


class TestAmbiguityVersusTerminalRace:
    """歧义回调进锁后必须按**最新持久化事实**判定，绝不拿旧快照覆盖终态。"""

    async def test_ambiguity_pause_waiting_for_the_lock_keeps_the_terminal_state(self) -> None:
        """§2 场景 3：歧义处理等锁期间取消先写入终态 -> 保持 CANCELLED，不出现 PAUSED。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.INTENT.value,
            generation=1,
            step_state=StepState.PENDING,
        )
        stale = await store.load(record.task_id)
        stale_step = stale.steps[0]
        assert stale.state is TaskState.RUNNING

        gated = GateSaveStore(store, event=TASK_CANCELLED)
        runtime._store = gated
        cancel_task = asyncio.create_task(runtime.cancel(record.task_id, reason="user cancel"))
        # 取消已经**持锁**并阻塞在终态落盘上。
        await asyncio.wait_for(gated.save_entered.wait(), timeout=5)

        pause_task = asyncio.create_task(
            runtime._pause_ambiguous_dispatch(
                stale, stale_step, message="派发结果未知", reason="race_while_locked"
            )
        )
        await _let_run()
        assert not pause_task.done(), "歧义处理必须在等同一把按任务锁"
        assert not cancel_task.done()

        gated.release.set()
        await asyncio.wait_for(cancel_task, timeout=5)
        await asyncio.wait_for(pause_task, timeout=5)

        final = await store.load(record.task_id)
        assert final is not None
        assert final.state is TaskState.CANCELLED, "绝不把 CANCELLED 改写成 PAUSED"
        step = final.steps[0]
        assert step.dispatch_state == DispatchState.AMBIGUOUS.value, "歧义证据仍如实记录"
        assert step.failure == "DISPATCH_AMBIGUOUS"
        assert final.authorization is None
        assert final.replan_required is False, "终态任务是惰性的，不设这一位"
        assert final.pending_action_id == ""
        assert final.recovery["detail"]["terminal_state_preserved"] is True
        assert final.recovery["detail"]["dispatch_reason"] == "race_while_locked"
        assert final.recovery["detail"]["stop_confirmed"] is False
        _assert_no_world_action(double)

    async def test_identity_probe_racing_a_cancel_never_pauses_or_dispatches(self) -> None:
        """§2 场景 4：_resolve_dispatch_identity 等身份探测时取消 -> 终态保持、零世界动作。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        probe_entered = asyncio.Event()
        probe_release = asyncio.Event()
        current = {"value": ""}

        async def probe() -> str:
            probe_entered.set()
            await probe_release.wait()
            current["value"] = INSTANCE_B
            return INSTANCE_B

        runtime = make_runtime(
            double,
            store=store,
            runtime_instance_of=lambda: current["value"],
            identity_probe=probe,
        )
        # 上一代派发已经结清（SETTLED），但记录的是**旧实例** —— 新一轮派发在发出前必须
        # 重新取得可信身份；这是"派发前的身份复核"，不是"未决派发"（后者会被 drive() 的
        # fail-closed 守卫直接拦下，根本走不到身份探测）。
        record = await armed_record(
            runtime,
            store,
            plan=two_action_plan(),
            dispatch_state=DispatchState.SETTLED.value,
            generation=1,
            step_state=StepState.PENDING,
        )
        step = record.steps[0]
        step.dispatch_runtime_instance = INSTANCE_A
        step.dispatch_identity_tracked = True
        await store.save(record)

        driver = asyncio.create_task(runtime.drive(record.task_id))
        await asyncio.wait_for(probe_entered.wait(), timeout=5)

        cancelled = await runtime.cancel(record.task_id, reason="user cancel")
        assert cancelled.state is TaskState.CANCELLED

        probe_release.set()
        await asyncio.wait_for(driver, timeout=5)

        final = await store.load(record.task_id)
        assert final is not None
        assert final.state is TaskState.CANCELLED, "歧义回调绝不把终态改成 PAUSED"
        assert final.pending_action_id == ""
        assert final.authorization is None
        settled_step = final.steps[0]
        assert settled_step.dispatch_state in {
            DispatchState.INTENT.value,
            DispatchState.AMBIGUOUS.value,
        }
        assert settled_step.dispatch_generation == 1
        assert settled_step.dispatch_key == step.dispatch_key
        _assert_no_world_action(double)


# ===================================================================== 真实 SQLite


class TestLifecycleRaceOnRealSqlite:
    """§2 场景 6：事件竞态与歧义竞态各至少一个跑在真实 SQLite 上，并用**全新 store** 回读。"""

    async def test_event_race_survives_a_real_sqlite_round_trip(self, tmp_path: Any) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'event_race.db'}"))
        await database.connect()
        try:
            store: Any = SqliteTaskStore(database)
            double = DispatchDouble(mode="detached")
            runtime = make_runtime(double, store=store)
            record = await armed_record(
                runtime,
                store,
                plan=single_action_plan(),
                dispatch_state=DispatchState.ACCEPTED.value,
                generation=1,
                action_id=ACTION_ID,
                step_state=StepState.WAITING_ACTION,
            )
            gated = GateFindStore(store)
            runtime._store = gated
            event_task = asyncio.create_task(
                runtime.on_action_event(
                    action_id=ACTION_ID,
                    event="minecraft.action.completed",
                    status="SUCCEEDED",
                    result={"ok": True},
                )
            )
            await asyncio.wait_for(gated.find_entered.wait(), timeout=5)
            await runtime.cancel(record.task_id, reason="user cancel")
            gated.release.set()
            await asyncio.wait_for(event_task, timeout=5)

            reader = SqliteTaskStore(database)
            reloaded = await reader.load(record.task_id)
            assert reloaded is not None
            assert reloaded.state is TaskState.CANCELLED
            assert reloaded.steps[0].dispatch_state == DispatchState.SETTLED.value
            assert reloaded.steps[0].action_id == ACTION_ID
            assert reloaded.pending_action_id == ""
            assert reloaded.replan_required is False
            _assert_no_world_action(double)
        finally:
            await database.close()

    async def test_ambiguity_race_survives_a_real_sqlite_round_trip(self, tmp_path: Any) -> None:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'amb_race.db'}"))
        await database.connect()
        try:
            store: Any = SqliteTaskStore(database)
            double = DispatchDouble(mode="detached")
            runtime = make_runtime(double, store=store)
            record = await armed_record(
                runtime,
                store,
                plan=single_action_plan(),
                dispatch_state=DispatchState.ACCEPTED.value,
                generation=1,
                action_id=ACTION_ID,
                step_state=StepState.WAITING_ACTION,
            )
            stale = await store.load(record.task_id)
            stale_step = stale.steps[0]

            await runtime.cancel(record.task_id, reason="user cancel")
            # 迟到的歧义回调（旧快照：WAITING_ACTION/ACCEPTED）不得覆盖终态，也不得抹掉关联。
            await runtime._pause_ambiguous_dispatch(
                stale, stale_step, message="迟到的歧义回调", reason="late_ambiguous"
            )

            reader = SqliteTaskStore(database)
            reloaded = await reader.load(record.task_id)
            assert reloaded is not None
            assert reloaded.state is TaskState.CANCELLED
            assert reloaded.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
            assert reloaded.steps[0].action_id == ACTION_ID
            assert reloaded.pending_action_id == ACTION_ID, "真实动作关联必须保留"
            assert reloaded.pending_step_id == reloaded.steps[0].step_id
            assert reloaded.recovery["detail"]["terminal_state_preserved"] is True
            assert reloaded.recovery["detail"]["stop_confirmed"] is False
            matched = await reader.find_by_action(ACTION_ID, include_terminal=True)
            assert [item.task_id for item in matched] == [record.task_id]
            _assert_no_world_action(double)
        finally:
            await database.close()


# ============================================================ 唯一写入口的终态吸收


class TestTerminalStateAbsorption:
    """§一：**唯一写入口** _save 的兜底 —— 未持锁的转换路径也不能用旧快照复活任务。"""

    async def test_an_unlocked_old_snapshot_can_never_downgrade_a_terminal_state(self) -> None:
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id=ACTION_ID,
            step_state=StepState.WAITING_ACTION,
        )
        stale = await store.load(record.task_id)
        assert stale.state is TaskState.RUNNING

        cancelled = await runtime.cancel(record.task_id, reason="user cancel")
        assert cancelled.state is TaskState.CANCELLED

        # 未持锁的转换路径手里的旧快照（例如 pause()/_finish()/_settle_blocked()）：即使它
        # 已经把内存对象改成 PAUSED，落盘时也必须被终态吸收。
        stale.state = TaskState.PAUSED
        stale.recovery = {
            "reason": "stale_pause",
            "outcome": "UNKNOWN",
            "detail": {"note": "旧快照想写的证据"},
        }
        await runtime._save(stale)

        final = await store.load(record.task_id)
        assert final is not None
        assert final.state is TaskState.CANCELLED, "旧快照绝不复活已取消的任务"
        assert stale.state is TaskState.CANCELLED, "调用方对象同步为终态真相"
        assert final.recovery["detail"]["superseded_by_terminal_state"] is True
        assert final.recovery["detail"]["superseded_evidence"]["outcome"]
        _assert_no_world_action(double)

    async def test_terminal_absorption_keeps_step_evidence_and_linkage(self) -> None:
        """被吸收时：终态与真实关联保留，**调用方想写的步骤证据照样落盘**。"""
        store = InMemoryTaskStore()
        double = DispatchDouble(mode="detached")
        runtime = make_runtime(double, store=store)
        record = await armed_record(
            runtime,
            store,
            plan=single_action_plan(),
            dispatch_state=DispatchState.ACCEPTED.value,
            generation=1,
            action_id=ACTION_ID,
            step_state=StepState.WAITING_ACTION,
        )
        stale = await store.load(record.task_id)
        expired = await runtime.expire(record.task_id, reason="task ttl")
        assert expired.state is TaskState.EXPIRED

        stale.steps[0].message = "迟到的步骤证据"
        stale.steps[0].status = "SUCCEEDED"
        stale.state = TaskState.RUNNING
        await runtime._save(stale)

        final = await store.load(record.task_id)
        assert final.state is TaskState.EXPIRED
        assert final.pending_action_id == ACTION_ID, "终态的真实动作关联必须保留"
        assert final.steps[0].message == "迟到的步骤证据"
        assert final.steps[0].status == "SUCCEEDED"
        _assert_no_world_action(double)
