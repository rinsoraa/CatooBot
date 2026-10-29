"""Phase 7F.5.2：**派发前暂停竞态**的确定性故障注入测试。

核心命题（任务书 §二）：用户暂停与派发交错时，

* 外部动作**一次都不能发出**（动作计数为零）；
* 持久化的 PAUSED **绝不被**旧内存快照覆盖回 RUNNING；
* 派发被拦下之后执行流程**立即停止**，不会在下一轮重新推进同一步、也不会再 +1 代次；
* 已经发出的外部调用**必须有记录**（action_id / pending），绝不出现「任务说已暂停、
  动作却在外面跑且没有记录」的假象；未知就如实进入受控暂停 + 人工核对。

交错注入用**可控 store 钩子**（在「派发意图刚刚落盘」那个 await 点触发），
以及**可控执行替身**（用 asyncio.Event 门闩精确停在调用中间）—— 不依赖 sleep 碰运气。
所有断言对象都是：外部动作启动次数、任务状态、authorization、dispatch_state、
dispatch_generation、pending_action_id、replan_required，以及**从 store 回读**的持久化结果。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import pytest

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import (
    DispatchState,
    StepState,
    TaskAuthorization,
    TaskPlan,
    TaskState,
)
from app.tasks.runtime import (
    TaskAuthorizationError,
    TaskInvocation,
    TaskRuntime,
)
from app.tasks.store import InMemoryTaskStore, SqliteTaskStore
from tests.test_action_dispatch_safety import (
    WORLD_ACTION_TOOLS,
    DispatchDouble,
    make_runtime,
    single_action_plan,
)


class IntentInjectMixin:
    """在「派发意图**刚刚落盘**」这个精确窗口注入一个并发动作（任务书 §三）。

    注入点在 super().save() **之后**：此时 store 里已经是「INTENT 已落盘、外部请求还没发出」
    的真实中间态 —— 也就是任务书要求覆盖的那个窗口。只在**首次**引入 INTENT 的那次 save 上
    触发一次，所以暂停/取消本身引起的 save 不会递归触发。
    """

    def _init_inject(self) -> None:
        self.on_intent: Any = None
        self.fire_once = True
        self.fired = 0

    async def save(
        self, record: Any, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        previous = await self.load(record.task_id)
        prev_intent = {
            item.step_id
            for item in (previous.steps if previous is not None else [])
            if item.dispatch_state == DispatchState.INTENT.value
        }
        await super().save(record, event=event, step_id=step_id, detail=detail)
        now_intent = {
            item.step_id
            for item in record.steps
            if item.dispatch_state == DispatchState.INTENT.value
        }
        if not (now_intent - prev_intent) or self.on_intent is None:
            return
        if self.fire_once and self.fired:
            return
        self.fired += 1
        await self.on_intent(record)


class InjectOnIntentStore(IntentInjectMixin, InMemoryTaskStore):
    def __init__(self) -> None:
        super().__init__()
        self._init_inject()


class InjectOnIntentSqliteStore(IntentInjectMixin, SqliteTaskStore):
    """同一套注入逻辑接在**真实 SQLite** store 上（用于数据库回读验证）。"""

    def __init__(self, database: Any) -> None:
        super().__init__(database)
        self._init_inject()

    async def admit_dispatch_intent(
        self,
        record: Any,
        *,
        expected_generation: int,
        event: str = "",
        step_id: str = "",
        now: float | None = None,
    ) -> bool:
        """Keep the post-commit pause hook when INTENT uses atomic admission."""
        admitted = await super().admit_dispatch_intent(
            record,
            expected_generation=expected_generation,
            event=event,
            step_id=step_id,
            now=now,
        )
        if admitted and self.on_intent is not None and (not self.fire_once or not self.fired):
            self.fired += 1
            await self.on_intent(record)
        return admitted


async def armed(
    engine: TaskRuntime,
    store: Any,
    *,
    plan: TaskPlan | None = None,
    expires_in: float = 300.0,
) -> Any:
    """造一份「已确认 + 正在执行」的持久化记录（RUNNING + 有效授权），还没走任何一步。"""
    plan = plan or single_action_plan()
    record = await engine.create_task(
        plan.objective, session_id="s", user_id="u", origin="user", plan=plan
    )
    now = time.time()
    record.authorization = TaskAuthorization(
        task_id=record.task_id,
        user_id="u",
        session_id="s",
        plan_hash=record.plan_hash,
        approved_at=now,
        expires_at=now + float(expires_in),
        plan_version=record.plan_version,
    )
    record.mark_plan_confirmed(now=now)
    record.state = TaskState.RUNNING
    await store.save(record, event="task.started")
    return record


# --------------------------------------------------------------------- §三.1


async def test_pause_between_intent_and_dispatch_check_never_dispatches() -> None:
    """暂停发生在 INTENT 落盘后、派发复核前：动作计数为零，任务保持 PAUSED。"""
    double = DispatchDouble(mode="detached")
    store = InjectOnIntentStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    async def pause_now(_record: Any) -> None:
        await engine.pause(record.task_id, reason="user")

    store.on_intent = pause_now
    result = await engine.drive(record.task_id)

    # ① 外部动作一次都没发出：这个窗口的计数必须是零
    assert double.action_count == 0
    # ② 持久化真相：PAUSED；不是被旧快照覆盖回 RUNNING
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.PAUSED
    step = stored.steps[0]
    assert step.dispatch_state == DispatchState.AMBIGUOUS.value, "受控歧义暂停（7F.4 语义）"
    assert step.dispatch_generation == 1, "只开了一代，没有重复 +1"
    assert step.action_id == "", "从未真正派发过，就没有 action_id"
    assert stored.authorization is None, "歧义路径必须清授权"
    assert stored.replan_required is True
    assert stored.recovery["detail"]["dispatch_reason"] == "paused_during_dispatch"
    # ③ 返回给调用方的状态与持久化真相一致
    assert result.state is TaskState.PAUSED
    # ④ 之后不会自动恢复、也不会再次派发
    assert (await engine.drive(record.task_id)).state is TaskState.PAUSED
    assert double.action_count == 0
    with pytest.raises(TaskAuthorizationError):
        await engine.resume(record.task_id, user_id="u", session_id="s", origin="user")


# --------------------------------------------------------------------- §三.2


async def test_authorize_step_refuses_a_paused_task() -> None:
    """暂停发生在授权复核前：authorize_step 不得仅凭尚未过期的授权放行。"""
    double = DispatchDouble(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)
    step = record.steps[0]

    # 正对照：RUNNING + 有效授权 → 放行（证明拒绝的原因是状态，不是参数）
    assert (
        await engine.authorize_step(
            task_id=record.task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments=step.arguments,
            plan_hash=record.plan_hash,
        )
        is True
    )
    paused = await engine.pause(record.task_id, reason="user")
    assert paused.state is TaskState.PAUSED
    # 授权对象本身仍然有效 —— 拒绝只能来自"任务已经暂停"
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.authorization is not None
    assert stored.authorization.valid(now=time.time())
    assert (
        await engine.authorize_step(
            task_id=record.task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments=step.arguments,
            plan_hash=record.plan_hash,
        )
        is False
    ), "已暂停的任务绝不能放行步骤授权"
    assert double.action_count == 0


# --------------------------------------------------------------------- §三.3


async def test_stale_in_memory_record_cannot_override_persisted_state() -> None:
    """暂停与派发交错、原执行协程持有旧对象：旧对象不能覆盖持久化的新状态。"""
    double = DispatchDouble(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    stale = await store.load(record.task_id)
    assert stale is not None and stale.state is TaskState.RUNNING
    await engine.pause(record.task_id, reason="user")
    assert stale.state is TaskState.RUNNING, "旧对象确实还停在 RUNNING（这正是竞态的来源）"

    result = await engine.drive(record.task_id)
    assert result.state is TaskState.PAUSED, "drive 必须以持久化真相为准，而不是旧对象"
    assert double.action_count == 0
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.PAUSED, "持久化的 PAUSED 没有被覆盖回 RUNNING"
    assert stored.steps[0].dispatch_generation == 0, "没有凭空开一代"
    assert stored.steps[0].dispatch_state == "", "也没有留下悬空的派发意图"


# --------------------------------------------------------------------- §三.4


async def test_cancel_between_intent_and_real_call_never_dispatches() -> None:
    """取消发生在派发意图与真实调用之间：没有外部副作用时绝不派发。"""
    double = DispatchDouble(mode="detached")
    store = InjectOnIntentStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    async def cancel_now(_record: Any) -> None:
        await engine.cancel(record.task_id, reason="user cancel")

    store.on_intent = cancel_now
    result = await engine.drive(record.task_id)

    assert double.action_count == 0
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.CANCELLED
    # 复核是在真实调用**之前**拒绝的 ⇒ 这次派发确认没发出 ⇒ 意图被如实 retire
    assert stored.steps[0].dispatch_state == ""
    assert result.state is TaskState.CANCELLED
    assert (await engine.drive(record.task_id)).state is TaskState.CANCELLED
    assert double.action_count == 0


async def test_expire_between_intent_and_real_call_never_dispatches() -> None:
    """过期发生在派发意图与真实调用之间：同样绝不派发。"""
    double = DispatchDouble(mode="detached")
    store = InjectOnIntentStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    async def expire_now(_record: Any) -> None:
        await engine.expire(record.task_id, reason="task ttl")

    store.on_intent = expire_now
    result = await engine.drive(record.task_id)

    assert double.action_count == 0
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.EXPIRED
    assert stored.steps[0].dispatch_state == ""
    assert result.state is TaskState.EXPIRED
    assert double.action_count == 0


async def test_lost_response_after_intent_stays_ambiguous_and_never_retries() -> None:
    """请求**已经发出**但拿不到答复：保持既有歧义语义，绝不盲目重试。"""
    double = DispatchDouble(mode="lost_response")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    result = await engine.drive(record.task_id)

    assert double.action_count == 1, "请求确实发出去了一次"
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.PAUSED
    assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
    assert stored.authorization is None
    assert stored.replan_required is True
    assert result.state is TaskState.PAUSED
    # 绝不盲目重试：再 drive 一次也不会多出一次世界动作
    assert (await engine.drive(record.task_id)).state is TaskState.PAUSED
    assert double.action_count == 1


# --------------------------------------------------------------------- §三.5


async def test_instance_change_and_user_pause_do_not_double_dispatch() -> None:
    """Runtime 实例变化与用户暂停同时发生：无死锁、无二次派发、无未确认的新动作。"""
    double = DispatchDouble(mode="detached")
    store = InjectOnIntentStore()
    engine = make_runtime(double, store=store, runtime_instance_of=lambda: "rt_B")
    record = await armed(engine, store)

    async def pause_and_change_instance(_record: Any) -> None:
        await engine.pause(record.task_id, reason="user")
        # 同一个窗口里，这次派发被记成"属于旧实例 rt_A"
        stale = await store.load(record.task_id)
        assert stale is not None
        stale.steps[0].dispatch_runtime_instance = "rt_A"
        await store.save(stale)
        # 真实的身份对账入口（来自一次成功的身份响应）
        await engine.on_runtime_instance_changed("rt_B")

    store.on_intent = pause_and_change_instance
    result = await engine.drive(record.task_id)

    assert double.action_count == 0
    assert result.state is TaskState.PAUSED
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.PAUSED
    assert stored.authorization is None
    assert stored.replan_required is True
    assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
    # 先到的那次分类**胜出且不被覆盖**（幂等）：暂停先把它判成受控歧义，之后实例对账不会再
    # 改这条已经分类好的记录（它只匹配 INTENT/ACCEPTED）。
    assert stored.recovery["detail"]["dispatch_reason"] == "paused_during_dispatch"
    # 不因为 Runtime 重新可用就重发
    assert (await engine.drive(record.task_id)).state is TaskState.PAUSED
    assert double.action_count == 0


async def test_instance_change_before_pause_in_the_same_window() -> None:
    """同一个窗口的另一种到达顺序：实例变化先到、用户暂停随后到 —— 同样零派发、零重发。"""
    double = DispatchDouble(mode="detached")
    store = InjectOnIntentStore()
    engine = make_runtime(double, store=store, runtime_instance_of=lambda: "rt_B")
    record = await armed(engine, store)

    async def change_instance_then_pause(_record: Any) -> None:
        stale = await store.load(record.task_id)
        assert stale is not None
        # 这次派发被记成"属于旧实例 rt_A"（真实进程里就是旧实例留下的记录）
        stale.steps[0].dispatch_runtime_instance = "rt_A"
        await store.save(stale)
        affected = await engine.on_runtime_instance_changed("rt_B")
        assert len(affected) == 1, "未决动作属于旧实例 → 必须进入受控歧义对账"
        await engine.pause(record.task_id, reason="user")

    store.on_intent = change_instance_then_pause
    result = await engine.drive(record.task_id)

    assert double.action_count == 0
    assert result.state is TaskState.PAUSED
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.state is TaskState.PAUSED
    assert stored.authorization is None
    assert stored.replan_required is True
    assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
    assert stored.recovery["detail"]["dispatch_reason"] == "runtime_instance_changed"
    assert (await engine.drive(record.task_id)).state is TaskState.PAUSED
    assert double.action_count == 0


async def test_identity_reconcile_reentrancy_does_not_deadlock() -> None:
    """对账内部的 SAFE 控制面又会观测到身份变化：只能立即返回，不能递归等待。"""
    nested: list[list[Any]] = []
    holder: dict[str, Any] = {}

    class ReentrantStop(DispatchDouble):
        async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
            if tool == "minecraft_stop":
                engine = holder["engine"]
                nested.append(await engine.on_runtime_instance_changed("rt_B"))
            return await super().__call__(tool, arguments, **kwargs)

    double = ReentrantStop(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store, runtime_instance_of=lambda: "rt_B")
    holder["engine"] = engine
    record = await armed(engine, store)
    record.steps[0].dispatch_state = DispatchState.INTENT.value
    record.steps[0].dispatch_generation = 1
    record.steps[0].dispatch_runtime_instance = "rt_A"
    await store.save(record)

    affected = await asyncio.wait_for(engine.on_runtime_instance_changed("rt_B"), timeout=5)
    assert len(affected) == 1
    assert double.stops, "受控暂停必须走既有 SAFE 控制面尽力真停"
    assert nested == [[]], "再入必须立即返回空：不等待、不递归（结构上无死锁）"
    stored = await store.load(record.task_id)
    assert stored is not None
    assert stored.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value


# --------------------------------------------------------------------- §三.6


async def test_inflight_pause_records_the_action_and_never_redispatches() -> None:
    """真实派发已经进行中的暂停：动作必须有记录，绝不出现「已暂停但动作在跑」的假象。"""
    gate = asyncio.Event()
    entered = asyncio.Event()

    class Gated(DispatchDouble):
        async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
            if tool in WORLD_ACTION_TOOLS:
                entered.set()
                await gate.wait()
            return await super().__call__(tool, arguments, **kwargs)

    double = Gated(mode="detached")
    store = InMemoryTaskStore()
    engine = make_runtime(double, store=store)
    record = await armed(engine, store)

    driver = asyncio.create_task(engine.drive(record.task_id))
    await asyncio.wait_for(entered.wait(), timeout=5)
    paused = await engine.pause(record.task_id, reason="user")
    assert paused.state is TaskState.PAUSED
    gate.set()
    result = await asyncio.wait_for(driver, timeout=5)

    assert double.action_count == 1, "动作确实发出过一次"
    assert double.stops, "受控暂停必须尽力真停"
    stored = await store.load(record.task_id)
    assert stored is not None
    # 任务状态以持久化真相为准：绝不因为调用返回就把 PAUSED 推回 RUNNING/WAITING_ACTION
    assert stored.state is TaskState.PAUSED
    # 但动作**必须有记录**（action_id + pending）：这才是"不制造假象"
    assert stored.pending_action_id == "act_srv_1"
    assert stored.pending_step_id == stored.steps[0].step_id
    assert stored.pause_requested is True, "等这个动作真正结束后再停"
    step = stored.steps[0]
    assert step.state is StepState.WAITING_ACTION
    assert step.dispatch_state == DispatchState.ACCEPTED.value
    assert step.action_id == "act_srv_1"
    assert result.state is TaskState.PAUSED
    assert (await engine.drive(record.task_id)).state is TaskState.PAUSED
    assert double.action_count == 1, "绝不重发"


# --------------------------------------------------- 数据库（SQLite）回读验证


async def test_pause_race_survives_a_real_sqlite_round_trip(tmp_path: Any) -> None:
    """同一场竞态在**真实 SQLite** 上跑一遍，并用一个全新 store 从数据库回读。"""
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'race.db'}"))
    await database.connect()
    try:
        double = DispatchDouble(mode="detached")
        store = InjectOnIntentSqliteStore(database)
        engine = make_runtime(double, store=store)
        record = await armed(engine, store)

        async def pause_now(_record: Any) -> None:
            await engine.pause(record.task_id, reason="user")

        store.on_intent = pause_now
        result = await engine.drive(record.task_id)
        assert double.action_count == 0
        assert result.state is TaskState.PAUSED

        # 全新 store（模拟"换个进程读同一份数据库"）
        reader = SqliteTaskStore(database)
        reloaded = await reader.load(record.task_id)
        assert reloaded is not None
        assert reloaded.state is TaskState.PAUSED
        assert reloaded.steps[0].dispatch_state == DispatchState.AMBIGUOUS.value
        assert reloaded.steps[0].dispatch_generation == 1
        assert reloaded.authorization is None
        assert reloaded.replan_required is True
        assert double.action_count == 0
    finally:
        await database.close()
