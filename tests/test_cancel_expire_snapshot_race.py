"""Phase 7F.5.3 Follow-up：**cancel/expire 与动作响应结算的快照竞态**收口测试。

根因（审计实测）：旧顺序是「读快照 → await stop → 按旧快照算在途 → 整份保存」。stop 是长
await，期间 _settle() 会把**真实 action_id / pending 关联 / ACCEPTED** 写进 store，而取消流程
随后用**旧快照整份覆盖**（Sqlite 侧是 payload 整列 ON CONFLICT DO UPDATE）→ 真实动作关联被抹掉，
迟到的真实终态事件再也匹配不到任何任务，动作于是脱离生命周期管理。

本文件把交错写成**确定性**的（没有计时 sleep、没有随机）：
* **窗口 2**「底层已接受、调用方还没拿到响应」：SnapshotRaceInvoker 在 super().__call__()
  **之后**才等门闩 —— 真实 action_id 已经生成，但 TaskInvocation 还没交回调用方；
* **窗口 1**「请求进了调用链、底层还没接受」：同一替身，但在 super().__call__() **之前**等；
* 「stop 仍在等待、响应先完成结算」：**每一次** minecraft_stop 都有独立门闩、可分别放行，
  因此能精确复现"取消那次 stop 还没回来、结算先写完了真实关联"这一被修的次序。

每个用例都断言：task state、step state、dispatch_state、dispatch_generation、action_id、
pending_action_id / pending_step_id、replan_required、recovery 停止与派发证据、
**世界动作启动次数**、**stop 调用次数**，以及从 store **重新读回**的最终状态。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import DispatchState, StepState, TaskState
from app.tasks.store import InMemoryTaskStore, SqliteTaskStore
from tests.test_action_dispatch_safety import WORLD_ACTION_TOOLS, DispatchDouble, make_runtime
from tests.test_predispatch_pause_race import armed

ACTION_ID = "act_srv_1"

MOVE_ARGUMENTS = {"x": 100, "y": 64, "z": 200}


class SnapshotRaceInvoker(DispatchDouble):
    """可控替身：能精确停在「已接受但未交付」「stop 未返回」两个窗口。

    before_accept=True 时退化成窗口 1（请求已进调用链、底层尚未接受），用来证明本文件的
    主用例（窗口 2）**不是同一个场景换了个名字**。
    """

    def __init__(self, *, mode: str = "detached", before_accept: bool = False) -> None:
        super().__init__(mode=mode)
        self.before_accept = before_accept
        self.entered = asyncio.Event()
        self.accepted = asyncio.Event()
        self.deliver = asyncio.Event()
        self.stop_reached = asyncio.Event()
        self.stop_gates: list[asyncio.Event] = []
        self.stop_calls = 0
        self.stops_after_accept = 0

    def release_stop(self, index: int) -> None:
        self.stop_gates[index].set()

    async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> Any:
        if tool == "minecraft_stop":
            self.stop_calls += 1
            if self.accepted.is_set():
                self.stops_after_accept += 1
            gate = asyncio.Event()
            self.stop_gates.append(gate)
            self.stop_reached.set()
            await gate.wait()
            return await super().__call__(tool, arguments, **kwargs)
        if tool in WORLD_ACTION_TOOLS:
            self.entered.set()
            if self.before_accept:
                # 窗口 1：先被取消，**稍后**才被底层接受。
                await self.deliver.wait()
                invocation = await super().__call__(tool, arguments, **kwargs)
                self.accepted.set()
                return invocation
            # 窗口 2：底层已经接受（action_id 已生成），但响应还没交回调用方。
            invocation = await super().__call__(tool, arguments, **kwargs)
            self.accepted.set()
            await self.deliver.wait()
            return invocation
        return await super().__call__(tool, arguments, **kwargs)


class SignalStore:
    """包一层 store：每次真实落盘后打信号 + 记快照（其余方法全权委托）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.snapshots: list[dict[str, Any]] = []
        self.link_saved = asyncio.Event()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    async def save(
        self, record: Any, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        step = record.steps[0] if record.steps else None
        snapshot = {
            "event": event,
            "state": record.state.value,
            "dispatch_state": str(getattr(step, "dispatch_state", "") or ""),
            "action_id": str(getattr(step, "action_id", "") or ""),
            "pending_action_id": str(record.pending_action_id or ""),
        }
        self.snapshots.append(snapshot)
        await self._inner.save(record, event=event, step_id=step_id, detail=detail)
        if snapshot["action_id"] and snapshot["pending_action_id"]:
            self.link_saved.set()


async def _wait_for_stops(double: SnapshotRaceInvoker, count: int) -> None:
    """等到第 count 次 stop **已经进入**控制面（有界让出，不依赖任何计时）。"""
    for _ in range(20000):
        if len(double.stop_gates) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError(f"stop 调用没有达到 {count} 次（实际 {len(double.stop_gates)}）")


async def _start_stop_pending_race(
    engine: Any, double: SnapshotRaceInvoker, record: Any, *, action: str
) -> tuple[asyncio.Task[Any], asyncio.Task[Any]]:
    """把交错推进到「终态已落盘 + 阻塞在 stop 里」，返回 (driver, 终态任务)。"""
    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.accepted.wait(), timeout=5)
    assert double.action_count == 1
    terminal = asyncio.create_task(
        engine.cancel(record.task_id, reason="user cancel")
        if action == "cancel"
        else engine.expire(record.task_id, reason="task ttl")
    )
    await asyncio.wait_for(double.stop_reached.wait(), timeout=5)
    return driver, terminal


async def _assert_link_survives(
    engine: Any,
    store: Any,
    double: SnapshotRaceInvoker,
    record: Any,
    *,
    terminal_state: TaskState,
    stops_after_accept: int = 2,
) -> Any:
    """两次 save 之后，真实动作关联必须仍然完整，且终态任务不能再放行任何步骤。

    stops_after_accept 取决于交错的形状：动作**在接受之后**才被取消时，取消那次 stop 与
    结算那次 stop 都落在接受之后（2）；只有"接受之前就取消"的窗口 1 才是 1。
    """
    final = await store.load(record.task_id)
    assert final is not None
    assert final.state is terminal_state
    assert final.steps[0].action_id == ACTION_ID
    assert final.steps[0].dispatch_state == DispatchState.ACCEPTED.value
    assert final.steps[0].dispatch_generation == 1
    assert final.pending_action_id == ACTION_ID
    assert final.pending_step_id == final.steps[0].step_id
    assert final.replan_required is False, "终态任务的在途动作不得被改写成要求重规划"
    assert double.action_count == 1, "世界动作只启动过一次：绝不重发"
    assert double.stop_calls == 2, "接受之后必须再停一次（请求过 stop != 动作已停止）"
    assert double.stops_after_accept == stops_after_accept, (
        "『接受之后再停一次』的次数必须与交错形状一致"
    )
    detail = final.recovery["detail"]
    assert detail["action_id"] == ACTION_ID
    assert detail["pending_action_id"] == ACTION_ID
    assert detail["stop_requested"] is True
    assert detail["stop_confirmed"] is False
    assert detail["stop_after_accept"] is True
    assert int(detail["stop_after_accept_requests"]) == 1
    assert not await engine.authorize_step(
        task_id=record.task_id,
        step_id=final.steps[0].step_id,
        tool="minecraft_move_to",
        arguments=dict(MOVE_ARGUMENTS),
        plan_hash=final.plan_hash,
    ), "终态任务的授权绝不可再放行任何步骤"
    linked_at = next(
        index for index, item in enumerate(store.snapshots) if item["action_id"] == ACTION_ID
    )
    for later in store.snapshots[linked_at:]:
        assert later["action_id"] == ACTION_ID, f"动作关联被后来的落盘抹掉了：{later}"
        assert later["pending_action_id"] == ACTION_ID
    return final


async def test_cancel_keeps_action_link_written_while_its_stop_was_pending() -> None:
    """取消的 stop 还挂着时结算先写入真实 action_id：取消**不得**用旧快照覆盖它。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver, cancelling = await _start_stop_pending_race(engine, double, record, action="cancel")
    mid = await store.load(record.task_id)
    assert mid is not None
    assert mid.state is TaskState.CANCELLED, "终态真相必须先落盘"
    assert mid.steps[0].action_id == "", "取消落盘时确实还没有 action_id（这就是窗口 2）"

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(store.link_saved.wait(), timeout=5)

    linked = await store.load(record.task_id)
    assert linked is not None
    assert linked.steps[0].action_id == ACTION_ID
    assert linked.pending_action_id == ACTION_ID
    assert linked.state is TaskState.CANCELLED, "迟到的『已接受』绝不把终态翻案"
    assert not cancelling.done(), "取消流程此刻仍阻塞在它自己那次 stop 上"

    double.release_stop(0)
    cancelled = await asyncio.wait_for(cancelling, timeout=5)
    await asyncio.wait_for(driver, timeout=5)
    assert cancelled.state is TaskState.CANCELLED
    await _assert_link_survives(engine, store, double, record, terminal_state=TaskState.CANCELLED)
    assert (await engine.drive(record.task_id)).state is TaskState.CANCELLED
    assert double.action_count == 1, "再 drive 一次也绝不产生新的世界动作"


async def test_expire_keeps_action_link_written_while_its_stop_was_pending() -> None:
    """过期走同一交错：独立用例，绝不用取消用例代替过期。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver, expiring = await _start_stop_pending_race(engine, double, record, action="expire")
    mid = await store.load(record.task_id)
    assert mid is not None
    assert mid.state is TaskState.EXPIRED
    assert mid.steps[0].action_id == ""

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(store.link_saved.wait(), timeout=5)
    linked = await store.load(record.task_id)
    assert linked is not None
    assert linked.steps[0].action_id == ACTION_ID
    assert linked.state is TaskState.EXPIRED

    double.release_stop(0)
    expired = await asyncio.wait_for(expiring, timeout=5)
    await asyncio.wait_for(driver, timeout=5)
    assert expired.state is TaskState.EXPIRED
    await _assert_link_survives(engine, store, double, record, terminal_state=TaskState.EXPIRED)


async def test_cancel_in_accepted_but_undelivered_window() -> None:
    """底层**已经接受**（action_id 已生成）、调用方还没拿到响应时取消。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.accepted.wait(), timeout=5)
    assert double.action_count == 1
    assert not double.deliver.is_set(), "响应还没交回调用方 —— 这正是与窗口 1 不同的地方"

    cancelling = asyncio.create_task(engine.cancel(record.task_id, reason="user cancel"))
    await asyncio.wait_for(double.stop_reached.wait(), timeout=5)
    double.release_stop(0)
    cancelled = await asyncio.wait_for(cancelling, timeout=5)
    assert cancelled.state is TaskState.CANCELLED
    at_cancel = await store.load(record.task_id)
    assert at_cancel is not None
    assert at_cancel.steps[0].dispatch_state == DispatchState.INTENT.value
    assert at_cancel.pending_action_id == "", "取消时确实还没有真实 action_id"

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(driver, timeout=5)
    final = await _assert_link_survives(
        engine, store, double, record, terminal_state=TaskState.CANCELLED
    )
    assert final.steps[0].state is StepState.WAITING_ACTION
    assert (await engine.drive(record.task_id)).state is TaskState.CANCELLED
    assert double.action_count == 1


async def test_expire_in_accepted_but_undelivered_window() -> None:
    """过期在同窗口的独立用例（绝不用取消用例代替过期）。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.accepted.wait(), timeout=5)
    assert not double.deliver.is_set()
    expiring = asyncio.create_task(engine.expire(record.task_id, reason="task ttl"))
    await asyncio.wait_for(double.stop_reached.wait(), timeout=5)
    double.release_stop(0)
    expired = await asyncio.wait_for(expiring, timeout=5)
    assert expired.state is TaskState.EXPIRED

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(driver, timeout=5)
    await _assert_link_survives(engine, store, double, record, terminal_state=TaskState.EXPIRED)


async def test_before_accept_window_is_a_distinct_scenario() -> None:
    """窗口 1（接受之前取消）：世界动作计数为 0、accepted 未置位 —— 与窗口 2 判然不同。"""
    double = SnapshotRaceInvoker(mode="detached", before_accept=True)
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    assert not double.accepted.is_set(), "窗口 1：底层接受还没发生"
    assert double.action_count == 0, "接受之前没有任何真实世界动作"

    cancelling = asyncio.create_task(engine.cancel(record.task_id, reason="user cancel"))
    await asyncio.wait_for(double.stop_reached.wait(), timeout=5)
    double.release_stop(0)
    await asyncio.wait_for(cancelling, timeout=5)

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(driver, timeout=5)
    final = await _assert_link_survives(
        engine,
        store,
        double,
        record,
        terminal_state=TaskState.CANCELLED,
        stops_after_accept=1,
    )
    assert final.steps[0].action_id == ACTION_ID
    assert final.recovery["detail"]["stop_after_accept"] is True
    assert final.recovery["detail"]["stop_confirmed"] is False


async def test_late_true_terminal_event_converges_without_reviving_terminal_task() -> None:
    """终态之后真实终态事件迟到：只收敛证据，绝不复活任务、绝不重发。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver, cancelling = await _start_stop_pending_race(engine, double, record, action="cancel")
    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(store.link_saved.wait(), timeout=5)
    double.release_stop(0)
    await asyncio.wait_for(cancelling, timeout=5)
    await asyncio.wait_for(driver, timeout=5)

    converged = await engine.on_action_event(
        action_id=ACTION_ID,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
    )
    assert converged is not None
    final = await store.load(record.task_id)
    assert final is not None
    assert final.state is TaskState.CANCELLED, "终态任务保持终态"
    assert final.steps[0].dispatch_state == DispatchState.SETTLED.value
    assert final.steps[0].state is StepState.SUCCEEDED
    assert final.pending_action_id == ""
    assert final.pending_step_id == ""
    assert double.action_count == 1

    before = await store.load(record.task_id)
    again = await engine.on_action_event(
        action_id=ACTION_ID,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
    )
    after = await store.load(record.task_id)
    assert again is None or again.state is TaskState.CANCELLED
    assert after is not None and before is not None
    assert after.state is before.state, "重复终态事件必须是幂等空操作"
    assert after.steps[0].dispatch_state == before.steps[0].dispatch_state
    assert after.pending_action_id == before.pending_action_id
    assert double.action_count == 1


async def test_terminal_event_before_action_id_is_deferred_then_consumed() -> None:
    """终态事件比 action_id 落盘更早到达：先暂存，拿到真实关联后立刻消费，绝不静默丢弃。"""
    double = SnapshotRaceInvoker(mode="detached")
    store = SignalStore(InMemoryTaskStore())
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.accepted.wait(), timeout=5)
    early = await engine.on_action_event(
        action_id=ACTION_ID,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
    )
    assert early is None, "此刻还没有落盘的真实 action_id -> 匹配不到，进暂存区"

    expiring = asyncio.create_task(engine.expire(record.task_id, reason="task ttl"))
    await asyncio.wait_for(double.stop_reached.wait(), timeout=5)
    double.release_stop(0)
    await asyncio.wait_for(expiring, timeout=5)

    double.deliver.set()
    await _wait_for_stops(double, 2)
    double.release_stop(1)
    await asyncio.wait_for(driver, timeout=5)
    final = await store.load(record.task_id)
    assert final is not None
    assert final.state is TaskState.EXPIRED, "终态保持"
    assert final.steps[0].dispatch_state == DispatchState.SETTLED.value, "暂存事件已被消费"
    assert final.steps[0].action_id == ACTION_ID
    assert final.pending_action_id == "", "真实终态已确认 -> 未决关联收敛"
    assert double.action_count == 1


async def test_cancel_snapshot_race_survives_real_sqlite_round_trip(tmp_path: Any) -> None:
    """同一场竞态在**真实 SQLite** 上跑，并用全新 store 从数据库完整回读。"""
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'snapshot_race.db'}"))
    await database.connect()
    try:
        double = SnapshotRaceInvoker(mode="detached")
        store: Any = SignalStore(SqliteTaskStore(database))
        engine = make_runtime(double, store=store)
        record = await armed(engine, store)

        driver, cancelling = await _start_stop_pending_race(engine, double, record, action="cancel")
        double.deliver.set()
        await _wait_for_stops(double, 2)
        double.release_stop(1)
        await asyncio.wait_for(store.link_saved.wait(), timeout=5)
        double.release_stop(0)
        await asyncio.wait_for(cancelling, timeout=5)
        await asyncio.wait_for(driver, timeout=5)

        reader = SqliteTaskStore(database)
        reloaded = await reader.load(record.task_id)
        assert reloaded is not None
        assert reloaded.state is TaskState.CANCELLED
        assert reloaded.steps[0].action_id == ACTION_ID
        assert reloaded.steps[0].dispatch_state == DispatchState.ACCEPTED.value
        assert reloaded.steps[0].dispatch_generation == 1
        assert reloaded.pending_action_id == ACTION_ID
        assert reloaded.pending_step_id == reloaded.steps[0].step_id
        assert reloaded.recovery["detail"]["action_id"] == ACTION_ID
        assert reloaded.recovery["detail"]["stop_confirmed"] is False
        assert double.action_count == 1
        matched = await reader.find_by_action(ACTION_ID, include_terminal=True)
        assert [item.task_id for item in matched] == [record.task_id]
    finally:
        await database.close()
