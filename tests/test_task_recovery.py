"""Phase 5A.1 §三十五：Task Recovery（重启恢复 / 只读对账 / 授权到期）。

这一层的核心不是"能跑完"，而是**面对世界已经变化时仍然不会偷偷做错事**：

* 进程重启后旧 Mineflayer action 一定失效 → 先判失效，再只读对账；
* 对账只回答"目标还在不在、还是不是那个东西"，绝不挑替代目标、绝不产生世界动作；
* 授权到期 → 停在安全边界回"等重新确认"，旧确认条目一律作废；
* 每一步都要留下审计（checkpoint + 事件 + 计划版本历史）。

时钟全部注入（``clock``），所以"t0+ttl-1 有效 / t0+ttl 过期"是确定性的，绝不 sleep。
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from app.tasks.models import (
    ExpectedFinalState,
    PlanStatus,
    StepState,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.runtime import (
    ReconcileOutcome,
    ReplanReason,
    TaskAuthorizationError,
    TaskConfig,
    TaskInvocation,
    TaskRuntime,
)
from app.tasks.store import InMemoryTaskStore
from tests.test_task_runtime import FakeConfirmation

SESSION = "minecraft:127.0.0.1:25565:空凛"
USER = "空凛"


# ------------------------------------------------------------------ 替身

RISK = {
    "minecraft_dig": "MEDIUM",
    "minecraft_pickup_item": "MEDIUM",
    "minecraft_move_to": "LOW",
    "minecraft_inventory": "SAFE",
    "minecraft_dropped_items": "SAFE",
    "minecraft_find_blocks": "SAFE",
    "minecraft_dig_capability": "SAFE",
    "minecraft_world": "SAFE",
    "minecraft_stop": "SAFE",
}


class FakeInvoke:
    """假工具通道：SAFE 读给出**可编排的世界事实**，世界动作单独记账。"""

    def __init__(
        self,
        *,
        online: bool = True,
        dig_block: str | None = "oak_log",
        dig_reason: str | None = "too_far",
        dig_missing: bool = False,
        drops: list[dict[str, Any]] | None = None,
        inventory: list[dict[str, Any]] | None = None,
        world_code: str = "",
    ) -> None:
        self.online = online
        self.dig_block = dig_block
        self.dig_reason = dig_reason
        self.dig_missing = dig_missing
        self.drops = list(drops or [])
        self.inventory = list(inventory or [])
        self.world_code = world_code
        self.calls: list[str] = []
        #: 真正会改世界的调用（恢复/对账期间必须保持为空）
        self.world_actions: list[str] = []
        self._detached = 0

    async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
        self.calls.append(tool)
        if tool == "minecraft_world":
            if self.world_code:
                return TaskInvocation(ok=False, error="world read failed", code=self.world_code)
            return TaskInvocation(ok=True, result={"ok": True, "online": self.online})
        if tool == "minecraft_dig_capability":
            if self.dig_missing:
                return TaskInvocation(
                    ok=False, error="那里没有方块", code="minecraft.block_unavailable"
                )
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "block": {"name": self.dig_block} if self.dig_block else None,
                    "can_dig": self.dig_reason != "too_far",
                    "reason": self.dig_reason,
                    "dig_time_ms": 900,
                },
            )
        if tool == "minecraft_dropped_items":
            return TaskInvocation(
                ok=True,
                result={
                    "ok": True,
                    "online": True,
                    "total": len(self.drops),
                    "items": list(self.drops),
                },
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(ok=True, result={"ok": True, "items": list(self.inventory)})
        if tool in {"minecraft_dig", "minecraft_pickup_item", "minecraft_move_to"}:
            # 持续型动作只回 "RUNNING + action_id"：世界有没有变由测试自己显式声明
            # （``dig_reason`` / ``drops`` / ``inventory``），绝不隐式改世界 ——
            # 否则"对账看到的世界"就不是测试以为的那个世界了。
            self.world_actions.append(tool)
            self._detached += 1
            return TaskInvocation(
                ok=True,
                status="RUNNING",
                action_id=f"act_{tool}_{self._detached}",
            )
        self.world_actions.append(tool)
        return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})


class Events:
    def __init__(self) -> None:
        self.rows: list[tuple[str, dict[str, Any]]] = []

    def __call__(self, event: str, payload: dict[str, Any]) -> None:
        self.rows.append((event, dict(payload)))

    def names(self) -> list[str]:
        return [name for name, _ in self.rows]

    def payloads(self, name: str) -> list[dict[str, Any]]:
        return [payload for event, payload in self.rows if event == name]


class Clock:
    """可注入的假时钟（§十一：禁止 sleep）。"""

    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


# ------------------------------------------------------------------ 夹具


def dig_plan(*, x: int = 10, y: int = 64, z: int = 10, expected: str = "oak_log") -> TaskPlan:
    return TaskPlan(
        objective="挖一块橡木原木并捡回来",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_dig",
                arguments={"x": x, "y": y, "z": z, "expected_block": expected},
                risk="MEDIUM",
            ),
            TaskStep(step_id="step_2", tool="minecraft_dropped_items", arguments={}, risk="SAFE"),
            TaskStep(
                step_id="step_3",
                tool="minecraft_pickup_item",
                arguments={
                    "entity_id": {"from_step": "step_2", "path": "items.0.entity_id"},
                    "expected_item": expected,
                },
                risk="MEDIUM",
            ),
            TaskStep(step_id="step_4", tool="minecraft_inventory", arguments={}, risk="SAFE"),
        ],
        expected_final_state=ExpectedFinalState(inventory_delta={expected: 1}),
    )


def make_runtime(
    *,
    invoke: FakeInvoke | None = None,
    store: Any = None,
    clock: Any = None,
    config: TaskConfig | None = None,
    confirmations: Any = None,
    events: Events | None = None,
) -> tuple[TaskRuntime, FakeInvoke, Any, Events]:
    invoke = invoke or FakeInvoke()
    confirmations = confirmations or FakeConfirmation()
    events = events or Events()
    runtime = TaskRuntime(
        store=store if store is not None else InMemoryTaskStore(),
        invoke=invoke,
        confirmations=confirmations,
        config=config or TaskConfig(),
        publish=events,
        clock=clock or Clock(),
        risk_of=lambda tool: RISK.get(tool, ""),
        is_registered=lambda tool: tool in RISK,
        schema_of=lambda tool: None,
    )
    return runtime, invoke, confirmations, events


async def create_confirmed(runtime: TaskRuntime, *, plan: TaskPlan | None = None) -> Any:
    record = await runtime.create_task(
        "挖一块橡木原木并捡回来",
        session_id=SESSION,
        user_id=USER,
        origin="user",
        plan=plan or dig_plan(),
    )
    assert record.state is TaskState.PENDING_CONFIRMATION, record.message
    record = await runtime.confirm_and_start(
        record.task_id, user_id=USER, session_id=SESSION, origin="user"
    )
    return record


def drops_for(entity_id: int = 4242, name: str = "oak_log") -> list[dict[str, Any]]:
    return [{"entity_id": entity_id, "item": {"name": name, "count": 1}, "distance": 1.0}]


# ------------------------------------------------------------------ A/B/C：重启后的旧动作


async def test_a_waiting_action_is_persisted_with_its_plan_version() -> None:
    runtime, _, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    assert record.state is TaskState.WAITING_ACTION
    assert record.pending_action_id.startswith("act_minecraft_dig")
    assert record.plan_version == 1
    assert record.plan_status == PlanStatus.ACTIVE.value
    assert record.plan_history[-1].confirmed_at > 0


async def test_b_c_recovery_marks_the_old_action_invalid_and_never_retries_it() -> None:
    store = InMemoryTaskStore()
    invoke = FakeInvoke(dig_reason="air", dig_block=None)  # 世界已经变了：方块没了
    clock = Clock()
    runtime, _, _, events = make_runtime(invoke=invoke, store=store, clock=clock)
    await create_confirmed(runtime)
    actions_before = list(invoke.world_actions)

    # 进程重启：新 runtime 读同一份存储（模拟真正的 SQLite）
    fresh, _, _, fresh_events = make_runtime(
        invoke=invoke, store=store, clock=clock, events=Events()
    )
    recovered = await fresh.recover_persisted_tasks()

    assert len(recovered) == 1
    after = recovered[0]
    assert after.state is TaskState.REPLANNING, "对账发现目标已被处理掉 → 重规划"
    assert after.recovery["reason"] == ReplanReason.RUNTIME_RESTART.value
    assert after.recovery["outcome"] == ReconcileOutcome.TARGET_ALREADY_DONE.value
    stale = after.step("step_1")
    assert stale is not None
    assert stale.state is StepState.FAILED
    assert stale.failure == "RUNTIME_RESTART"
    assert after.pending_action_id == ""
    assert invoke.world_actions == actions_before, "恢复期绝不能重复任何世界动作"
    assert "task.recovered" in fresh_events.names()


async def test_recovery_is_idempotent() -> None:
    store = InMemoryTaskStore()
    invoke = FakeInvoke(dig_reason="too_far")
    clock = Clock()
    runtime, _, _, _ = make_runtime(invoke=invoke, store=store, clock=clock)
    record = await create_confirmed(runtime)
    fresh, _, _, events = make_runtime(invoke=invoke, store=store, clock=clock, events=Events())
    first = await fresh.recover_persisted_tasks()
    assert first[0].state is TaskState.PAUSED
    calls_after_first = list(invoke.calls)
    second = await fresh.recover_persisted_tasks()
    assert second[0].state is TaskState.PAUSED
    assert invoke.calls == calls_after_first, "已经恢复过的不再重复对账"
    assert events.names().count("task.recovered") == 1
    assert record.task_id == first[0].task_id


# ------------------------------------------------------------------ D/E/F：只读对账的四种事实


async def test_d_reconcile_says_reconciled_when_the_target_is_still_there() -> None:
    runtime, invoke, _, _ = make_runtime(invoke=FakeInvoke(dig_reason="too_far"))
    record = await create_confirmed(runtime)
    actions_before = list(invoke.world_actions)
    outcome = await runtime.reconcile_task(record)
    assert outcome["outcome"] == ReconcileOutcome.RECONCILED.value
    assert invoke.world_actions == actions_before, "对账只有 SAFE 读"
    assert "minecraft_world" in invoke.calls
    assert "minecraft_dig_capability" in invoke.calls


async def test_e_reconcile_detects_a_changed_block() -> None:
    runtime, invoke, _, _ = make_runtime(invoke=FakeInvoke(dig_block="stone"))
    record = await create_confirmed(runtime)
    actions_before = list(invoke.world_actions)
    outcome = await runtime.reconcile_task(record)
    assert invoke.world_actions == actions_before
    assert outcome["outcome"] == ReconcileOutcome.WORLD_CHANGED.value
    assert outcome["detail"]["expected"] == "oak_log"
    assert outcome["detail"]["actual"] == "stone"


async def test_f_reconcile_detects_a_lost_target() -> None:
    runtime, _, _, _ = make_runtime(invoke=FakeInvoke(dig_missing=True))
    record = await create_confirmed(runtime)
    outcome = await runtime.reconcile_task(record)
    assert outcome["outcome"] == ReconcileOutcome.TARGET_LOST.value


async def test_reconcile_reports_offline_and_unknown() -> None:
    offline, _, _, _ = make_runtime(invoke=FakeInvoke(online=False))
    record = await create_confirmed(offline)
    assert (await offline.reconcile_task(record))["outcome"] == ReconcileOutcome.OFFLINE.value

    broken, _, _, _ = make_runtime(invoke=FakeInvoke(world_code="minecraft.offline"))
    record2 = await create_confirmed(broken)
    assert (await broken.reconcile_task(record2))["outcome"] == ReconcileOutcome.OFFLINE.value

    weird, _, _, _ = make_runtime(invoke=FakeInvoke(world_code="minecraft.action_failed"))
    record3 = await create_confirmed(weird)
    assert (await weird.reconcile_task(record3))["outcome"] == ReconcileOutcome.UNKNOWN.value


async def test_g_recovery_never_touches_the_world_even_when_offline() -> None:
    store = InMemoryTaskStore()
    invoke = FakeInvoke(online=False)
    clock = Clock()
    runtime, _, _, _ = make_runtime(invoke=invoke, store=store, clock=clock)
    record = await create_confirmed(runtime)
    actions_before = list(invoke.world_actions)
    fresh, _, _, events = make_runtime(invoke=invoke, store=store, clock=clock, events=Events())
    recovered = (await fresh.recover_persisted_tasks())[0]
    assert recovered.state is TaskState.PAUSED
    assert recovered.failure == "RUNTIME_RESTART"
    assert recovered.recovery["outcome"] == ReconcileOutcome.OFFLINE.value
    assert invoke.world_actions == actions_before, "离线也只读，绝不重做"
    assert record.task_id == recovered.task_id
    assert "task.recovered" in events.names()


# ------------------------------------------------------------------ H/I：授权到期与重新确认


async def test_h_clock_boundary_valid_before_ttl_expired_at_ttl() -> None:
    clock = Clock(1000.0)
    runtime, _, _, _ = make_runtime(clock=clock, config=TaskConfig(authorization_ttl_seconds=10.0))
    record = await create_confirmed(runtime)
    assert record.authorization is not None
    assert record.authorization.expires_at == 1010.0

    clock.advance(9.0)  # t0 + ttl - 1
    still_valid = await runtime.get(record.task_id)
    assert still_valid is not None
    assert still_valid.authorization is not None
    assert still_valid.authorization.valid(now=clock()) is True

    clock.advance(1.0)  # t0 + ttl
    expired = await runtime.get(record.task_id)
    assert expired is not None
    assert expired.authorization is not None
    assert expired.authorization.valid(now=clock()) is False


async def test_i_expiry_requires_a_brand_new_confirmation() -> None:
    clock = Clock(1000.0)
    runtime, invoke, confirmations, events = make_runtime(
        clock=clock, config=TaskConfig(authorization_ttl_seconds=5.0)
    )
    record = await create_confirmed(runtime)
    old_confirmation = record.confirmation_id
    old_hash = record.plan_hash
    clock.advance(6.0)

    # 把任务推回"下一步还没开始"的位置，再推进
    current = await runtime.get(record.task_id)
    assert current is not None
    current.pending_action_id = ""
    current.pending_step_id = ""
    current.steps[0].state = StepState.PENDING
    current.state = TaskState.RUNNING
    await runtime._save(current)  # noqa: SLF001
    paused = await runtime.drive(record.task_id)

    assert paused.state is TaskState.PENDING_CONFIRMATION
    assert paused.failure == "AUTHORIZATION"
    assert paused.authorization is None
    assert paused.authorization_expired_at > 0
    assert "task.authorization_expired" in events.names()
    # 计划没变（这不是重规划），但确认必须是**新的**那一条
    assert paused.plan_hash == old_hash
    assert paused.replan_required is False
    assert paused.confirmation_id != old_confirmation
    # 旧确认条目不能被复用：它早就被消费掉了
    ok, code = await confirmations.consume(
        old_confirmation,
        task_id=paused.task_id,
        session_id=SESSION,
        user_id=USER,
        plan_hash=paused.plan_hash,
        arguments={"plan_hash": paused.plan_hash, "plan": paused.plan.hash_payload()},
        origin="user",
    )
    assert ok is False and code, "一次性确认条目绝不允许二次消费"
    assert len(invoke.world_actions) == 1, "授权过期后没有新的世界动作"
    assert "minecraft_dig" in invoke.world_actions

    # 用**新**确认重新走真实用户回合 → 继续执行
    resumed = await runtime.confirm_and_start(
        paused.task_id, user_id=USER, session_id=SESSION, origin="user"
    )
    assert resumed.state is TaskState.WAITING_ACTION
    assert resumed.authorization is not None
    assert resumed.authorization_expired_at == 0.0
    assert resumed.plan_version == 1


# ------------------------------------------------------------------ J/K/L/M/N/O：重规划与审计


async def test_j_k_l_replan_versions_hash_and_checkpoint() -> None:
    runtime, invoke, confirmations, events = make_runtime(
        invoke=FakeInvoke(dig_reason="air", dig_block=None)
    )
    record = await create_confirmed(runtime)
    v1_hash = record.plan_hash
    actions_before = list(invoke.world_actions)
    await runtime.recover(record.task_id)  # 世界已经变了 → REPLANNING
    handed_off = await runtime.get(record.task_id)
    assert handed_off is not None
    assert handed_off.state is TaskState.REPLANNING

    new_plan = dig_plan(x=99, z=99)
    replanned = await runtime.replan(
        record.task_id, new_plan, reason=ReplanReason.WORLD_CHANGED.value
    )
    assert replanned.state is TaskState.PENDING_CONFIRMATION
    assert replanned.plan_version == 2
    assert [item.version for item in replanned.plan_history] == [1, 2]
    assert replanned.plan_history[0].status == PlanStatus.SUPERSEDED.value
    assert replanned.plan_history[0].reason == ReplanReason.WORLD_CHANGED.value
    assert replanned.plan_history[0].plan_hash == v1_hash
    assert replanned.plan_history[1].plan_hash == replanned.plan_hash
    assert replanned.plan_hash != v1_hash, "重新规划必须换 hash（否则旧授权会盖到新动作上）"
    assert replanned.replans == 1
    assert replanned.replan_required is False
    assert invoke.world_actions == actions_before, "恢复 + 重规划全程零世界动作"

    # 审计：checkpoint 里有重规划与恢复两条转移
    rows = await runtime.checkpoints(record.task_id)
    events_in_log = [row["event"] for row in rows]
    assert "task.replanning" in events_in_log
    assert "task.recovered" in events_in_log
    recovered_rows = [row for row in rows if row["event"] == "task.recovered"]
    assert "RUNTIME_RESTART" in recovered_rows[-1]["detail"]
    replan_rows = [row for row in rows if row["event"] == "task.replanning"]
    assert "replan v2" in replan_rows[-1]["detail"]
    assert "task.replanning" in events.names()
    assert events.payloads("task.replanning")[-1]["replan_reason"] == "WORLD_CHANGED"
    assert events.payloads("task.replanning")[-1]["plan_version"] == 2
    assert confirmations.created[-1]["plan_hash"] == replanned.plan_hash
    assert "新的计划（第 2 版）" in confirmations.created[-1]["summary"]
    assert "需要重新确认" in confirmations.created[-1]["summary"]


async def test_o_resume_after_reconfirmation_runs_the_new_plan_to_success() -> None:
    invoke = FakeInvoke(dig_reason="air", dig_block=None)
    clock = Clock()
    runtime, _, confirmations, events = make_runtime(invoke=invoke, clock=clock)
    record = await create_confirmed(runtime)
    await runtime.recover(record.task_id)

    # 用户确认新计划（真实用户回合）
    replanned = await runtime.replan(
        record.task_id, dig_plan(x=42, z=42), reason=ReplanReason.WORLD_CHANGED.value
    )
    started = await runtime.confirm_and_start(
        replanned.task_id, user_id=USER, session_id=SESSION, origin="user"
    )
    assert started.state is TaskState.WAITING_ACTION
    # 新计划的第一步真的动了世界（这次是**授权后**的动作）
    assert [call for call in invoke.world_actions if call == "minecraft_dig"], "确认后才会真挖"

    # 挖完了：世界现在"多了一块原木在地上"，背包也真的多了一块
    invoke.dig_reason = "air"
    invoke.dig_block = None
    invoke.drops = drops_for()
    invoke.inventory = [{"name": "oak_log", "count": 1}]
    await runtime.on_action_event(
        action_id=started.pending_action_id, event="minecraft.action.completed", status="SUCCEEDED"
    )
    after_dig = await runtime.get(record.task_id)
    assert after_dig is not None
    # 掉落物查询是同步 SAFE，捡取是持续型动作
    await runtime.on_action_event(
        action_id=after_dig.pending_action_id,
        event="minecraft.action.completed",
        status="SUCCEEDED",
    )
    finished = await runtime.get(record.task_id)
    assert finished is not None
    assert finished.state is TaskState.SUCCEEDED, finished.message
    assert finished.plan_version == 2
    assert finished.plan_history[0].status == PlanStatus.SUPERSEDED.value
    assert finished.plan_history[1].status == PlanStatus.COMPLETED.value
    assert finished.verification["inventory_delta"] == {"oak_log": 1}
    assert "task.succeeded" in events.names()


# ------------------------------------------------------------------ SQLite（§三十七：不是只测内存）


async def test_sqlite_store_persists_versions_and_recovery(tmp_path: Any) -> None:
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.tasks.store import SqliteTaskStore

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'tasks.db'}"))
    await database.connect()
    try:
        store = SqliteTaskStore(database)
        clock = Clock()
        invoke = FakeInvoke(dig_reason="air", dig_block=None)
        runtime, _, _, events = make_runtime(invoke=invoke, store=store, clock=clock)
        record = await create_confirmed(runtime)

        # 进程重启（同一个 SQLite，新的 runtime 实例）
        fresh, _, _, fresh_events = make_runtime(
            invoke=invoke, store=SqliteTaskStore(database), clock=clock, events=Events()
        )
        loaded = await fresh.get(record.task_id)
        assert loaded is not None
        assert loaded.plan_hash == record.plan_hash
        assert loaded.plan_version == 1
        assert loaded.state is TaskState.WAITING_ACTION
        assert loaded.pending_action_id == record.pending_action_id

        recovered = await fresh.recover_persisted_tasks()
        assert recovered[0].state is TaskState.REPLANNING
        reloaded = await fresh.get(record.task_id)
        assert reloaded is not None
        assert reloaded.recovery["reason"] == "RUNTIME_RESTART"
        # 数据库里的 checkpoint 也是可审计的
        rows = await fresh.checkpoints(record.task_id)
        assert any(row["event"] == "task.recovered" for row in rows)
        assert "task.recovered" in fresh_events.names()
        assert events.names()  # 第一次运行也有事件
    finally:
        await database.close()


async def test_failed_world_change_hands_the_task_to_replanning() -> None:
    """§四：MEDIUM 步骤因为世界变了失败 → REPLANNING（不是"暂停一下接着跑"）。"""
    runtime, invoke, _, events = make_runtime()
    record = await create_confirmed(runtime)
    await runtime.on_action_event(
        action_id=record.pending_action_id,
        event="minecraft.action.failed",
        status="FAILED",
        error="目标方块已经不是原来那个了",
        code="minecraft.block_changed",
    )
    handed_off = await runtime.get(record.task_id)
    assert handed_off is not None
    assert handed_off.state is TaskState.REPLANNING
    assert handed_off.failure == "WORLD_CHANGED"
    assert handed_off.replan_required is True
    assert handed_off.replan_reason == "WORLD_CHANGED"
    assert handed_off.plan_status == PlanStatus.SUPERSEDED.value
    assert handed_off.message == "目标方块已经不是原来那个了"
    assert len([call for call in invoke.world_actions]) == 1, "失败之后绝不再自动动世界"
    assert events.names().count("task.replanning") == 1


async def test_replan_requires_a_live_task_and_respects_max_replans() -> None:
    runtime, _, _, _ = make_runtime(config=TaskConfig(max_replans=1))
    record = await create_confirmed(runtime)
    await runtime.recover(record.task_id)
    await runtime.replan(record.task_id, dig_plan(x=7), reason="WORLD_CHANGED")
    again = await runtime.replan(record.task_id, dig_plan(x=8), reason="WORLD_CHANGED")
    assert again.state is TaskState.FAILED
    assert "重规划次数超过上限" in again.message


async def test_replan_rejects_an_invalid_plan_without_touching_the_old_one() -> None:
    runtime, _, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    await runtime.recover(record.task_id)
    broken = TaskPlan(
        objective="坏的",
        steps=[TaskStep(step_id="s1", tool="minecraft_gather_resource", arguments={}, risk="LOW")],
        expected_final_state=ExpectedFinalState(),
    )
    failed = await runtime.replan(record.task_id, broken, reason="WORLD_CHANGED")
    assert failed.state is TaskState.FAILED
    assert failed.failure == "VALIDATION"

    with pytest.raises(TaskAuthorizationError):
        await runtime.replan("task_missing", dig_plan(), reason="WORLD_CHANGED")


async def test_recovery_after_a_race_between_completion_and_checkpoint() -> None:
    """§二十：动作完成了但 checkpoint 还没记 → 重启后靠**世界事实**对账，绝不重做。"""
    store = InMemoryTaskStore()
    invoke = FakeInvoke(dig_reason="air", dig_block=None)  # 世界说：那块木头已经没了
    clock = Clock()
    runtime, _, _, events = make_runtime(invoke=invoke, store=store, clock=clock)
    await create_confirmed(runtime)
    actions = list(invoke.world_actions)

    fresh, _, _, fresh_events = make_runtime(
        invoke=invoke, store=store, clock=clock, events=Events()
    )
    recovered = (await fresh.recover_persisted_tasks())[0]
    assert recovered.recovery["outcome"] == ReconcileOutcome.TARGET_ALREADY_DONE.value
    assert recovered.state is TaskState.REPLANNING
    assert invoke.world_actions == actions, "竞态下也绝不重复挖"
    assert "task.recovered" in fresh_events.names()
    assert events.payloads("task.recovered") == []


async def test_recovery_of_a_running_task_without_pending_action() -> None:
    """重启时任务停在 RUNNING（下一步还没派出去）→ 只对账，不动世界。"""
    store = InMemoryTaskStore()
    invoke = FakeInvoke(dig_reason="too_far")
    clock = Clock()
    runtime, _, _, _ = make_runtime(invoke=invoke, store=store, clock=clock)
    record = await create_confirmed(runtime)
    actions_before = list(invoke.world_actions)
    current = await runtime.get(record.task_id)
    assert current is not None
    current.pending_action_id = ""
    current.pending_step_id = ""
    current.steps[0].state = StepState.PENDING
    current.state = TaskState.RUNNING
    await runtime._save(current)  # noqa: SLF001

    fresh, _, _, _ = make_runtime(invoke=invoke, store=store, clock=clock, events=Events())
    recovered = (await fresh.recover_persisted_tasks())[0]
    assert recovered.state is TaskState.PAUSED
    assert recovered.failure == "RUNTIME_RESTART"
    assert recovered.recovery["outcome"] == ReconcileOutcome.RECONCILED.value
    assert invoke.world_actions == actions_before


def test_reconcile_outcomes_are_a_closed_set() -> None:
    """对账结论必须是有限枚举（UI/模型按它分支）。"""
    assert {item.value for item in ReconcileOutcome} == {
        "RECONCILED",
        "WORLD_CHANGED",
        "TARGET_LOST",
        "TARGET_ALREADY_DONE",
        "OFFLINE",
        "UNKNOWN",
    }
    assert {item.value for item in ReplanReason} >= {
        "TARGET_LOST",
        "WORLD_CHANGED",
        "RUNTIME_RESTART",
        "AUTHORIZATION_EXPIRED",
    }
    assert Mapping is not None


# ------------------------------------------------- 5B：真机踩到的"重启恢复" 两个坑


async def test_recovery_of_a_task_waiting_for_confirmation_does_not_crash() -> None:
    """真机 bug：持久化的任务停在 PENDING_CONFIRMATION 时，恢复**不能**切成 PAUSED
    （状态机里那是非法转移，会把整个任务能力带下去）。它应该继续等用户确认，
    只是内存里的确认条目没了 → 重新挂一条。"""
    store = InMemoryTaskStore()
    invoke = FakeInvoke(online=False)  # 重启后还没进世界：对账读不到世界
    clock = Clock()
    runtime, _, confirmations, events = make_runtime(invoke=invoke, store=store, clock=clock)
    record = await runtime.create_task(
        "挖一块橡木原木并捡回来",
        session_id=SESSION,
        user_id=USER,
        origin="user",
        plan=dig_plan(),
    )
    assert record.state is TaskState.PENDING_CONFIRMATION
    before = len(confirmations.created)

    fresh, _, fresh_confirmations, fresh_events = make_runtime(
        invoke=invoke, store=store, clock=clock, events=Events()
    )
    recovered = await fresh.recover_persisted_tasks()
    assert len(recovered) == 1
    after = recovered[0]
    assert after.state is TaskState.PENDING_CONFIRMATION, "等确认的任务重启后仍然等确认"
    assert after.recovery["reason"] == ReplanReason.RUNTIME_RESTART.value
    assert after.recovery["outcome"] == ReconcileOutcome.OFFLINE.value
    # 内存里的确认条目没了 → 新进程必须**重新挂一条**（旧条目不可能跨进程存活）
    assert len(fresh_confirmations.created) == 1, "重启后要重新挂一条确认"
    assert fresh_confirmations.created[0]["plan_hash"] == after.plan_hash
    assert before == 1  # 创建时本来就挂过一条（旧进程里的那条已经随进程没了）
    assert "task.confirmation_required" in fresh_events.names()
    assert "task.recovered" in fresh_events.names()
    assert invoke.world_actions == []


async def test_one_broken_task_does_not_stop_the_others() -> None:
    """逐条隔离：一条任务恢复失败，其它的照常恢复，启动恢复本身绝不整体抛异常。"""
    store = InMemoryTaskStore()
    invoke = FakeInvoke(dig_reason="too_far")
    clock = Clock()
    runtime, _, _, _ = make_runtime(invoke=invoke, store=store, clock=clock)
    good = await create_confirmed(runtime)
    other = await runtime.create_task(
        "挖一块橡木原木并捡回来",
        session_id=SESSION + "-b",
        user_id=USER,
        origin="user",
        plan=dig_plan(x=77, z=77),
    )
    broken = await runtime.confirm_and_start(
        other.task_id, user_id=USER, session_id=SESSION + "-b", origin="user"
    )

    class Exploding:
        def __init__(self, store: Any) -> None:
            self._store = store

        async def list_recent(self, limit: int = 20) -> list[Any]:
            return await self._store.list_recent(limit)

        async def load(self, task_id: str) -> Any:
            if task_id == broken.task_id:
                raise RuntimeError("bad row")
            return await self._store.load(task_id)

        async def save(self, *args: Any, **kwargs: Any) -> None:
            return await self._store.save(*args, **kwargs)

        async def active(self, session_id: str) -> Any:
            return await self._store.active(session_id)

        async def checkpoints(self, task_id: str, limit: int = 50) -> list[Any]:
            return await self._store.checkpoints(task_id, limit)

    fresh, _, _, _ = make_runtime(invoke=invoke, store=Exploding(store), clock=clock)
    recovered = await fresh.recover_persisted_tasks()
    ids = [item.task_id for item in recovered]
    assert good.task_id in ids, "坏的那条不能挡住好的那条"
    assert broken.task_id not in ids


def test_recovery_outcome_names_are_stable() -> None:
    assert ReconcileOutcome.OFFLINE.value == "OFFLINE"
    assert ReplanReason.RUNTIME_RESTART.value == "RUNTIME_RESTART"
