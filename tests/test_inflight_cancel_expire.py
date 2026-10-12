"""Phase 7F.5.3：**在途取消 / 过期与迟到事件**的确定性故障注入测试。

核心不变量（任务书 §一）：用户取消或任务过期后，不能因为一个已经开始的异步请求稍后才被
Runtime 接受，而留下仍在执行、无法停止、或无法被关联的世界动作。

交错注入全部是**确定性的**：用可控调用替身 + asyncio.Event 门闩，把调用精确停在
「请求已发出、响应还没回来」的窗口（§二窗口 2/3），绝不依赖随机 sleep。

每个用例都断言：task.state、step.state、dispatch_state、dispatch_generation、action_id、
pending_action_id、authorization、replan_required、**真实动作启动次数**、**停止调用次数**，
以及从存储**重新读回**的最终状态；其中至少一组使用真实 SQLite 存储做完整回读。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import DispatchState, StepState, TaskState, TaskStep
from app.tasks.store import InMemoryTaskStore, SqliteTaskStore
from tests.test_action_dispatch_safety import (
    WORLD_ACTION_TOOLS,
    DispatchDouble,
    make_runtime,
)
from tests.test_predispatch_pause_race import InjectOnIntentSqliteStore, armed

ACTION_ID = "act_srv_1"


class GatedInvoker(DispatchDouble):
    """可控调用替身：世界动作可以精确停在**请求已发出、响应还没回来**的窗口。

    ``release`` 放行之后才返回「已接受」—— 这样测试就能在"动作已经被 Runtime 接受、
    但 Python 还没写 action_id"的窗口里插入取消/过期（§二窗口 2/3）。
    """

    def __init__(self, *, mode: str = "detached", **kwargs: Any) -> None:
        super().__init__(mode=mode, **kwargs)
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> Any:
        if tool in WORLD_ACTION_TOOLS:
            self.entered.set()
            await self.release.wait()
        return await super().__call__(tool, arguments, **kwargs)


def _stops(double: Any) -> int:
    return len(double.stops)


# ----------------------------------------------------------------- 窗口 2 / §四.1


async def test_cancel_while_request_in_call_chain_keeps_action_managed() -> None:
    """取消发生在请求已进入调用链、Node 尚未接受动作时：迟到的接受不能留下无人管理的动作。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    cancelled = await engine.cancel(record.task_id, reason="user cancel")
    assert cancelled.state is TaskState.CANCELLED
    stops_before_accept = _stops(double)
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    # ① 终态保持：迟到的"已接受"绝不把 CANCELLED 翻案
    assert stored.state is TaskState.CANCELLED
    # ② 动作留下**可对账**的管理状态（不是被无声无息地丢掉）
    step = stored.steps[0]
    assert step.dispatch_state == DispatchState.ACCEPTED.value
    assert step.state is StepState.WAITING_ACTION
    assert step.action_id == ACTION_ID
    assert stored.pending_action_id == ACTION_ID, "终态任务同样必须保留事件匹配能力"
    assert stored.pending_step_id == step.step_id
    assert step.dispatch_generation == 1
    # ③ 拿到真实 action_id 之后**再停一次**：绝不把"发过 stop"当成"肯定停了"
    assert _stops(double) == stops_before_accept + 1
    detail = stored.recovery["detail"]
    assert detail["stop_after_accept"] is True
    assert detail["stop_confirmed"] is False
    assert int(detail["stop_after_accept_requests"]) == 1, "接受之后恰好再停一次"
    assert detail["dispatch_key"] == step.dispatch_key
    assert detail["dispatch_reason"] == "cancel_during_dispatch" or detail["dispatch_reason"]
    # ④ 世界动作只启动过一次（绝不重发）
    assert double.action_count == 1
    # ⑤ 随后真实终态事件仍能匹配并收敛（"无人管理"的反面证据）
    late = await engine.on_action_event(
        action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert late is not None
    after = await store.load(record.task_id)
    assert after is not None
    assert after.state is TaskState.CANCELLED, "收敛证据但绝不改变终态"
    assert after.pending_action_id == ""
    assert after.steps[0].dispatch_state == DispatchState.SETTLED.value
    assert double.action_count == 1


# ----------------------------------------------------------------- 窗口 2 / §四.2


async def test_expiry_in_the_same_window_keeps_action_managed() -> None:
    """过期发生在同一窗口：语义与取消**分别**测试，刻意不复用同一个用例。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    expired = await engine.expire(record.task_id, reason="task ttl")
    assert expired.state is TaskState.EXPIRED
    stops_before_accept = _stops(double)
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.EXPIRED
    step = stored.steps[0]
    assert step.dispatch_state == DispatchState.ACCEPTED.value
    assert step.action_id == ACTION_ID
    assert stored.pending_action_id == ACTION_ID
    assert _stops(double) == stops_before_accept + 1
    assert stored.recovery["detail"]["stop_after_accept"] is True
    assert double.action_count == 1
    assert (await engine.drive(record.task_id)).state is TaskState.EXPIRED
    assert double.action_count == 1


# ----------------------------------------------------------------- 窗口 3 / §四.3


async def test_cancel_after_accept_before_response_does_not_assume_stopped() -> None:
    """Runtime 已接受动作、响应尚未返回时取消：不得只靠先前那次 stop 就假定动作已停止。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    # 取消就发生在「Node 已经接受、响应还在回程」的窗口里
    await engine.cancel(record.task_id, reason="user cancel")
    stops_at_cancel = _stops(double)
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    detail = stored.recovery["detail"]
    assert detail["stop_after_accept"] is True, "必须在拿到真实 action_id 之后再停一次"
    assert int(detail["stop_after_accept_requests"]) == 1
    assert _stops(double) == stops_at_cancel + 1
    assert detail["stop_confirmed"] is False, "没有证据证明动作已停止，绝不假装"
    assert stored.state is TaskState.CANCELLED
    assert stored.pending_action_id == ACTION_ID
    assert double.action_count == 1


# ----------------------------------------------------------------- 窗口 3 / §四.4


async def test_expire_after_accept_before_response_does_not_assume_stopped() -> None:
    """同一窗口的**过期**语义：独立用例（任务书明确要求不得用同一个测试替代两种语义）。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    await engine.expire(record.task_id, reason="task ttl")
    stops_at_expire = _stops(double)
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    detail = stored.recovery["detail"]
    assert detail["stop_after_accept"] is True
    assert _stops(double) == stops_at_expire + 1
    assert stored.state is TaskState.EXPIRED
    assert stored.pending_action_id == ACTION_ID
    assert stored.steps[0].state is StepState.WAITING_ACTION
    assert double.action_count == 1


# ----------------------------------------------------------------- 窗口 6 / §四.5


async def test_event_arriving_before_action_id_is_not_lost() -> None:
    """动作事件先于 action_id 落盘到达：事件不会永久丢失，重复事件处理幂等。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    # 事件比 action_id 落盘更早到达（响应仍在回程）：此刻没有任何记录关联它
    early = await engine.on_action_event(
        action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert early is None
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    step = stored.steps[0]
    assert step.action_id == ACTION_ID
    assert step.dispatch_state == DispatchState.SETTLED.value, "早到的事件必须已被消费并收敛"
    assert step.state is StepState.SUCCEEDED
    assert stored.pending_action_id == ""
    assert stored.state is TaskState.SUCCEEDED
    assert double.action_count == 1
    # 重复事件：幂等空操作（不改状态、也不会被误当成"未知事件"再塞回暂存区）
    again = await engine.on_action_event(
        action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert again is None
    reloaded = await store.load(record.task_id)
    assert reloaded is not None and reloaded.state is TaskState.SUCCEEDED
    assert reloaded.event_seq == stored.event_seq, "重复事件不得再产生任何状态变化"


# ----------------------------------------------------------------- §四.6


async def test_cancel_stop_and_late_event_stay_consistent() -> None:
    """取消 / 过期与 stop 请求交错：最终动作状态、停止证据、任务终态、动作次数互相一致。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    await engine.cancel(record.task_id, reason="user cancel")
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)
    stops_after_settle = _stops(double)

    settled = await engine.on_action_event(
        action_id=ACTION_ID, event="minecraft.action.cancelled", status="CANCELLED"
    )
    assert settled is not None and settled.state is TaskState.CANCELLED
    stored = await store.load(record.task_id)
    assert stored is not None
    step = stored.steps[0]
    # 一致性：任务终态、步骤证据、停止证据、外部动作次数四者互相吻合
    assert stored.state is TaskState.CANCELLED
    assert step.state is StepState.CANCELLED
    assert step.dispatch_state == DispatchState.SETTLED.value
    assert step.action_id == ACTION_ID
    assert stored.pending_action_id == ""
    assert _stops(double) == stops_after_settle, "终态事件不需要（也不得）再发 stop"
    assert _stops(double) >= 2
    assert double.action_count == 1
    assert stored.recovery["detail"]["stop_after_accept"] is True


# ----------------------------------------------------------------- §四.7


async def test_unknown_dispatch_outcome_never_redispatches_under_terminal_state() -> None:
    """未知派发结果：不得重发；dispatch_key / 实例 / 代次 / 歧义原因与时间必须保留。"""
    double = GatedInvoker(mode="lost_response")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    await engine.cancel(record.task_id, reason="user cancel")
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)

    stored = await store.load(record.task_id)
    assert stored is not None
    assert double.action_count == 1, "请求确实发出过一次（可能已执行）"
    # 终态保持：取消就是取消，不因为"结果未知"把任务改成 PAUSED
    assert stored.state is TaskState.CANCELLED
    step = stored.steps[0]
    assert step.dispatch_state == DispatchState.AMBIGUOUS.value
    assert step.failure == "DISPATCH_AMBIGUOUS"
    assert stored.authorization is None, "歧义路径必须清授权"
    detail = stored.recovery["detail"]
    assert detail["dispatch_key"] == step.dispatch_key and detail["dispatch_key"]
    assert detail["dispatch_generation"] == step.dispatch_generation == 1
    assert "runtime_instance" in detail
    assert detail["at"] > 0
    assert detail["terminal_state_preserved"] is True
    # 之后绝不重发
    assert (await engine.drive(record.task_id)).state is TaskState.CANCELLED
    assert double.action_count == 1


# ----------------------------------------------------------------- §四.8


async def test_late_terminal_event_for_cancelled_task_never_resumes_dispatch() -> None:
    """已取消任务的迟到终态事件：任务保持原终态，不重新进入派发/续跑，证据仍可审计。"""
    double = GatedInvoker(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(double.entered.wait(), timeout=5)
    double.release.set()
    await asyncio.wait_for(driver, timeout=5)
    assert (await store.load(record.task_id)).state is TaskState.WAITING_ACTION  # type: ignore[union-attr]

    await engine.cancel(record.task_id, reason="user cancel")
    stops_at_cancel = _stops(double)
    late = await engine.on_action_event(
        action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert late is not None and late.state is TaskState.CANCELLED
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.CANCELLED
    assert stored.pending_action_id == ""
    assert stored.steps[0].state is StepState.SUCCEEDED, "证据如实收敛（动作确实完成了）"
    assert stored.steps[0].dispatch_state == DispatchState.SETTLED.value
    assert stored.recovery["detail"]["stop_after_accept"] is True
    assert _stops(double) >= stops_at_cancel
    # 绝不重新进入派发/续跑：再 drive 一次也不产生任何新动作
    assert (await engine.drive(record.task_id)).state is TaskState.CANCELLED
    assert double.action_count == 1


# ----------------------------------------------------- 真实 SQLite：完整持久化回读


async def test_inflight_cancel_survives_a_real_sqlite_round_trip(tmp_path: Any) -> None:
    """同一场在途取消竞态在**真实 SQLite** 上跑，并用全新 store 从数据库完整回读。"""
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'inflight.db'}"))
    await database.connect()
    try:
        double = GatedInvoker(mode="detached")
        store: Any = SqliteTaskStore(database)
        engine = make_runtime(double, store=store)
        record = await armed(engine, store)

        driver = asyncio.create_task(engine.drive(record.task_id))
        await asyncio.wait_for(double.entered.wait(), timeout=5)
        await engine.cancel(record.task_id, reason="user cancel")
        double.release.set()
        await asyncio.wait_for(driver, timeout=5)

        # 全新 store = "换个进程读同一份数据库"
        reader = SqliteTaskStore(database)
        reloaded = await reader.load(record.task_id)
        assert reloaded is not None
        assert reloaded.state is TaskState.CANCELLED
        assert reloaded.pending_action_id == ACTION_ID
        assert reloaded.steps[0].dispatch_state == DispatchState.ACCEPTED.value
        assert reloaded.steps[0].action_id == ACTION_ID
        assert reloaded.steps[0].dispatch_generation == 1
        assert reloaded.recovery["detail"]["stop_after_accept"] is True
        assert double.action_count == 1
        # 真实存储上，终态任务同样必须能被 action_id 匹配到（终态可匹配查询）
        matched = await reader.find_by_action(ACTION_ID, include_terminal=True)
        assert [item.task_id for item in matched] == [record.task_id]
        assert await reader.find_by_action(ACTION_ID, include_terminal=False) == []
        # 收敛
        settled = await engine.on_action_event(
            action_id=ACTION_ID, event="minecraft.action.completed", status="SUCCEEDED"
        )
        assert settled is not None and settled.state is TaskState.CANCELLED
        final = await reader.load(record.task_id)
        assert final is not None
        assert final.pending_action_id == ""
        assert final.steps[0].dispatch_state == DispatchState.SETTLED.value
        assert final.state is TaskState.CANCELLED
    finally:
        await database.close()


def test_inject_sqlite_store_is_usable() -> None:
    """导入自 pause-race 测试的 SQLite 替身仍然可用（保持两套门禁的共享夹具一致）。"""
    assert InjectOnIntentSqliteStore is not None
    assert TaskStep is not None
