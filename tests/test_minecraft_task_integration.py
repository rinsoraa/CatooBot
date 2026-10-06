"""Phase 5A §八十八：TaskRuntime ↔ Minecraft 工具链的**集成**测试（不碰 Mineflayer）。

这一层要证明的是"接缝"是对的，而不是各部件单独能跑：

* 任务步骤走的是既有的 Tool → Agent Bridge → Policy → Service 通道（TASK 回合 +
  一次性凭据），**不是**绕过策略直接调 runtime；
* action 终态事件（``minecraft.action.*``）经 :class:`MinecraftTaskCoordinator` 回到
  TaskRuntime，任务从"等动作"推进到下一步；
* MEDIUM 步骤靠**已确认的冻结计划 + 步骤授权**放行（TASK ≠ USER）；
* 全部步骤完成后用 SAFE 的 ``minecraft_inventory`` 重新读背包做最终校验。

真 Java 服务器的行为由 ``scripts/task_smoke_real.py`` 负责；这里全用假 Service，
所以断言可以精确到"第几步、什么状态、有没有偷偷换目标"。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.config.settings import MinecraftAgentToolsConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.integrations.minecraft.task_adapter import build_minecraft_task_runtime
from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
from app.tasks.models import ExpectedFinalState, StepState, TaskPlan, TaskState, TaskStep
from app.tasks.planner import Observation, plan_resource_task
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore

SESSION = "minecraft:integration:空凛"
USER = "空凛"


class FakeService:
    """与 MinecraftService 同形的假 Service：可编排返回 + 能像回调一样"发事件"。"""

    def __init__(self, *, allow_medium: bool = True) -> None:
        self.enabled = True
        config = MinecraftConfig(enabled=True, auto_start_runtime=False)
        config.agent.tools = MinecraftAgentToolsConfig(enabled=True, allow_medium=allow_medium)
        self.config = config
        self.username = "Catodayo"
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._listeners: list[Any] = []
        self._queue: dict[str, list[Any]] = {}
        self._positions: dict[str, dict[str, Any]] = {
            "position": {"x": 100.0, "y": 64.0, "z": 140.0},
            "dimension": "minecraft:overworld",
        }
        self.inventory_items: list[dict[str, Any]] = [{"name": "dirt", "count": 3}]
        self.held: dict[str, Any] | None = {"name": "stone_pickaxe", "count": 1}
        self.drops: list[dict[str, Any]] = []

    # --- 编排面
    def enqueue(self, action: str, outcome: Any) -> None:
        self._queue.setdefault(action, []).append(outcome)

    def _next(self, action: str, default: Any) -> Any:
        queue = self._queue.get(action) or []
        return queue.pop(0) if queue else default

    def _record(self, action: str, **payload: Any) -> None:
        self.calls.append((action, payload))

    def add_listener(self, listener: Any) -> None:
        if listener not in self._listeners:
            self._listeners.append(listener)

    async def emit(self, event: str, **data: Any) -> None:
        """像 runtime 回调那样发一条 Bridge 事件（走与生产相同的分发路径）。"""
        payload = MinecraftBridgeEvent(event=event, session_id=SESSION, timestamp=1.0, data=data)
        for listener in list(self._listeners):
            outcome = listener(payload)
            if asyncio.iscoroutine(outcome):
                await outcome

    # --- Service 公开面
    def world_view(self) -> dict[str, Any]:
        return {
            "available": True,
            "online": True,
            "age_seconds": 0.2,
            "semantic": {
                "self": {
                    "dimension": "minecraft:overworld",
                    "location": "plains",
                    "position": dict(self._positions["position"]),
                    "health": 20,
                },
                "environment": {"time_phase": "白天", "weather": "clear"},
                "players": [],
                "entities": [],
                "terrain": [],
                "points_of_interest": [],
            },
        }

    def snapshot(self) -> dict[str, Any]:
        return {"connection": {"status": "ONLINE", "position": dict(self._positions["position"])}}

    async def inventory(self) -> dict[str, Any]:
        self._record("inventory")
        outcome = self._next("inventory", None)
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "ok": True,
            "online": True,
            "selected_hotbar_slot": 0,
            "held_item": dict(self.held) if self.held else None,
            "items": [dict(row) for row in self.inventory_items],
            **(outcome or {}),
        }

    async def find_blocks(
        self, block_names: Any = None, max_distance: Any = None, max_results: Any = None
    ) -> dict[str, Any]:
        self._record("find_blocks", block_names=block_names, max_distance=max_distance)
        outcome = self._next("find_blocks", None)
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "status": "SUCCEEDED",
            "result": {
                "ok": True,
                "truncated": False,
                "matches": [
                    {
                        "block": {"name": "oak_log"},
                        "position": {"x": 103, "y": 64, "z": 141},
                        "distance": {"goal_near": 3.0, "raw": 3.1},
                    }
                ],
            },
            **(outcome or {}),
        }

    async def dig_capability(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        self._record("dig_capability", x=x, y=y, z=z)
        outcome = self._next("dig_capability", None)
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "status": "SUCCEEDED",
            "result": {
                "ok": True,
                "position": {"x": x, "y": y, "z": z},
                "block": {"name": "oak_log"},
                "held_item": dict(self.held) if self.held else None,
                "can_dig": False,
                "dig_time_ms": 900,
                "reason": "too_far",
            },
            **(outcome or {}),
        }

    async def dropped_items(self) -> dict[str, Any]:
        self._record("dropped_items")
        outcome = self._next("dropped_items", None)
        if isinstance(outcome, Exception):
            raise outcome
        return {
            "status": "SUCCEEDED",
            "result": {
                "ok": True,
                "online": True,
                "total": len(self.drops),
                "items": list(self.drops),
            },
            **(outcome or {}),
        }

    async def move_to(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        self._record("move_to", x=x, y=y, z=z)
        outcome = self._next(
            "move_to", {"status": "RUNNING", "action_id": "act_move_1", "action": "move_to"}
        )
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    async def dig(
        self, x: Any, y: Any, z: Any, expected_block: Any = None, expected_tool: Any = None
    ) -> dict[str, Any]:
        self._record("dig", x=x, y=y, z=z, expected_block=expected_block)
        outcome = self._next(
            "dig",
            {
                "status": "RUNNING",
                "action_id": "act_dig_1",
                "action": "dig",
                "message": f"挖 {expected_block}",
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    async def equip(self, item: Any) -> dict[str, Any]:
        self._record("equip", item=item)
        outcome = self._next("equip", {"status": "SUCCEEDED", "action_id": "act_equip_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    async def pickup_item(self, entity_id: Any, expected_item: Any = None) -> dict[str, Any]:
        self._record("pickup_item", entity_id=entity_id, expected_item=expected_item)
        outcome = self._next(
            "pickup_item",
            {"status": "RUNNING", "action_id": "act_pick_1", "action": "pickup_item"},
        )
        if isinstance(outcome, Exception):
            raise outcome
        return dict(outcome)

    async def stop_action(self) -> dict[str, Any]:
        self._record("stop_action")
        return {"ok": True, "status": "IDLE", "cancelled": []}


def _tools_config() -> ToolsConfig:
    return ToolsConfig(enabled=True)


async def _build(
    service: FakeService, *, store: InMemoryTaskStore | None = None
) -> tuple[TaskRuntime, MinecraftTaskCoordinator, FakeService]:
    service.agent = MinecraftAgentBridge(service)  # type: ignore[attr-defined]
    runtime = await build_minecraft_task_runtime(
        service,
        tools_config=_tools_config(),
        store=store if store is not None else InMemoryTaskStore(),
        config=TaskConfig(ttl_seconds=600.0, max_steps=16, max_replans=2, no_progress_limit=3),
    )
    coordinator = MinecraftTaskCoordinator(runtime, service, poll_seconds=3600.0)
    coordinator.start()
    return runtime, coordinator, service


async def _settle(times: int = 6) -> None:
    """让 ``create_task`` 出来的协程跑完（协调器用 create_task 转交事件）。"""
    for _ in range(times):
        await asyncio.sleep(0)


def _observe(service: FakeService) -> Any:
    """规划阶段的 SAFE 观察：直接走 Service（与生产同一条只读通道的返回值）。"""

    async def observe(tool: str, arguments: Any) -> TaskInvocation:
        if tool == "minecraft_world":
            return TaskInvocation(ok=True, result=service.world_view())
        if tool == "minecraft_find_blocks":
            payload = await service.find_blocks(**dict(arguments))
            return TaskInvocation(ok=True, result=dict(payload["result"]))
        if tool == "minecraft_inventory":
            return TaskInvocation(ok=True, result=await service.inventory())
        if tool == "minecraft_dig_capability":
            payload = await service.dig_capability(**dict(arguments))
            return TaskInvocation(ok=True, result=dict(payload["result"]))
        raise AssertionError(f"观察阶段不允许调用 {tool}")

    return observe


# ------------------------------------------------------------------ 协调器


async def test_coordinator_forwards_action_terminals_and_ignores_the_rest() -> None:
    """协调器只认 action 终态，且必须读**事件名属性**（enum 直接 str 化会永远匹配不上）。"""
    service = FakeService()
    runtime, coordinator, _ = await _build(service)
    try:
        seen: list[dict[str, Any]] = []

        async def spy(**kwargs: Any) -> None:
            seen.append(kwargs)

        runtime.on_action_event = spy  # type: ignore[method-assign]

        await service.emit("minecraft.action.started", action_id="act_x", action="move_to")
        await service.emit("minecraft.spawned", username="Catodayo")
        await service.emit("minecraft.action.completed", action_id="act_x", status="SUCCEEDED")
        await _settle()

        assert [row["action_id"] for row in seen] == ["act_x"]
        assert seen[0]["event"] == "minecraft.action.completed"

        # 没有 action_id 的终态事件不该打扰任何任务
        await service.emit("minecraft.action.failed", action="dig")
        await _settle()
        assert len(seen) == 1
    finally:
        await coordinator.stop()


# ------------------------------------------------------------------ 正向链路


async def test_task_drives_find_move_dig_pickup_and_verifies_inventory() -> None:
    """find → move → dig → dropped → pickup → inventory：由真 TaskRuntime 驱动到 SUCCEEDED。"""
    service = FakeService()
    runtime, coordinator, _ = await _build(service)
    try:
        observe = _observe(service)
        planned = await plan_resource_task(
            "去附近找一棵橡木，挖一块原木并捡回来",
            observe=observe,
            block_name="minecraft:oak_log",
            inventory={"items": [{"name": "dirt", "count": 3}], "held_item": None},
        )
        assert [step.tool for step in planned.plan.steps] == [
            "minecraft_move_to",
            "minecraft_dig",
            "minecraft_dropped_items",
            "minecraft_pickup_item",
            "minecraft_inventory",
        ]

        record = await runtime.create_task(
            "去附近找一棵橡木，挖一块原木并捡回来",
            session_id=SESSION,
            user_id=USER,
            origin="user",
            plan=planned.plan,
        )
        assert record.state is TaskState.PENDING_CONFIRMATION
        record = await runtime.confirm_and_start(
            record.task_id, user_id=USER, session_id=SESSION, origin="user"
        )
        assert record.state is TaskState.WAITING_ACTION
        assert record.steps[0].state is StepState.WAITING_ACTION
        assert "move_to" in [row[0] for row in service.calls]

        # 走到目标 → move_to 成功
        await service.emit(
            "minecraft.action.completed",
            action_id="act_move_1",
            status="SUCCEEDED",
            result={"distance_to_target": 1.4},
        )
        await _settle()
        record = await runtime.get(record.task_id)
        assert record is not None
        assert record.steps[0].state is StepState.SUCCEEDED
        # MEDIUM 的 dig 由"已确认的冻结计划 + 步骤授权"放行（不弹新确认）
        assert record.steps[1].state is StepState.WAITING_ACTION
        assert ("dig", {"x": 103, "y": 64, "z": 141, "expected_block": "oak_log"}) in [
            (name, args) for name, args in service.calls if name == "dig"
        ]

        # 挖完 → 掉落物里出现 oak_log
        service.drops = [
            {
                "entity_id": 4242,
                "item": {"name": "oak_log", "count": 1},
                "position": {"x": 103.2, "y": 64.3, "z": 141.4},
                "distance": 1.1,
            }
        ]
        await service.emit(
            "minecraft.action.completed",
            action_id="act_dig_1",
            status="SUCCEEDED",
            result={"block_after": "air"},
        )
        await _settle()
        record = await runtime.get(record.task_id)
        assert record is not None
        assert record.steps[1].state is StepState.SUCCEEDED
        assert record.steps[2].state is StepState.SUCCEEDED, "SAFE 的掉落物查询同步完成"
        assert record.steps[3].state is StepState.WAITING_ACTION
        pickup_args = next(args for name, args in service.calls if name == "pickup_item")
        assert pickup_args == {"entity_id": 4242, "expected_item": "oak_log"}, (
            "引用参数在执行前解析成**树里那一份**具体值"
        )

        # 捡起来 → 背包真的多了一块
        service.inventory_items = [{"name": "dirt", "count": 3}, {"name": "oak_log", "count": 1}]
        await service.emit(
            "minecraft.action.completed",
            action_id="act_pick_1",
            status="SUCCEEDED",
            result={"collected": True},
        )
        await _settle()

        finished = await runtime.get(record.task_id)
        assert finished is not None
        assert finished.state is TaskState.SUCCEEDED
        assert [step.state for step in finished.steps] == [StepState.SUCCEEDED] * 5
        assert finished.verification["ok"] is True
        assert finished.verification["inventory_delta"] == {"oak_log": 1}
        assert finished.result["summary"]
        # 最终校验是"重新读一次背包"得来的，不是从历史 action 结果抄的
        assert [name for name, _ in service.calls].count("inventory") >= 2
    finally:
        await coordinator.stop()


async def test_medium_step_arguments_changed_after_confirmation_are_refused() -> None:
    """计划确认之后谁改了参数都不行：授权对的是"冻结的那一份"（§十七/§十八）。"""
    service = FakeService()
    runtime, coordinator, _ = await _build(service)
    try:
        plan = TaskPlan(
            objective="挖一块木头",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_dig",
                    arguments={"x": 1, "y": 64, "z": 1, "expected_block": "oak_log"},
                    risk="MEDIUM",
                )
            ],
            expected_final_state=ExpectedFinalState(),
        )
        record = await runtime.create_task(
            "挖一块木头", session_id=SESSION, user_id=USER, origin="user", plan=plan
        )
        record = await runtime.confirm_and_start(
            record.task_id, user_id=USER, session_id=SESSION, origin="user"
        )
        assert record.state is TaskState.WAITING_ACTION

        # 偷换目标：改成另一个方块（模拟"runtime 临时换目标"）
        tampered = await runtime.get(record.task_id)
        assert tampered is not None
        tampered.steps[0].arguments["x"] = 2

        # 1) bridge 侧的授权校验必须直接拒绝（这一个函数就是所有任务步骤的放行口）
        allowed = await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments={"x": 2, "y": 64, "z": 1, "expected_block": "oak_log"},
            plan_hash=tampered.plan_hash,
        )
        assert allowed is False, "被改过的计划绝不能放行"

        # 2) 真的往下推也不行：计划 hash 与授权记录对不上 → FAILED（需要重新确认）
        tampered.state = TaskState.RUNNING
        tampered.pending_action_id = ""
        tampered.pending_step_id = ""
        await runtime._save(tampered)  # noqa: SLF001 - 故意在存储层做手脚
        digs_before = [name for name, _ in service.calls].count("dig")
        driven = await runtime.drive(record.task_id)
        assert driven.state is TaskState.FAILED
        assert driven.failure == "AUTHORIZATION"
        assert [name for name, _ in service.calls].count("dig") == digs_before, "拒绝在调用之前"
    finally:
        await coordinator.stop()


async def test_failed_medium_step_pauses_instead_of_retrying() -> None:
    """MEDIUM 失败 → 暂停等用户，绝不偷偷再挖一次（§二十三）。"""
    service = FakeService()
    runtime, coordinator, _ = await _build(service)
    try:
        plan = TaskPlan(
            objective="挖一块木头",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_dig",
                    arguments={"x": 1, "y": 64, "z": 1, "expected_block": "oak_log"},
                    risk="MEDIUM",
                )
            ],
            expected_final_state=ExpectedFinalState(),
        )
        record = await runtime.create_task(
            "挖一块木头", session_id=SESSION, user_id=USER, origin="user", plan=plan
        )
        record = await runtime.confirm_and_start(
            record.task_id, user_id=USER, session_id=SESSION, origin="user"
        )
        digs_before = [name for name, _ in service.calls].count("dig")
        await service.emit(
            "minecraft.action.failed",
            action_id="act_dig_1",
            status="FAILED",
            error="方块已经不见了",
            code="block.changed",
        )
        await _settle()
        paused = await runtime.get(record.task_id)
        assert paused is not None
        assert paused.state is TaskState.PAUSED
        assert paused.failure == "WORLD_CHANGED", "代码要映射成稳定的失败分类"
        assert [name for name, _ in service.calls].count("dig") == digs_before, "绝不自动重试"
    finally:
        await coordinator.stop()


async def test_medium_tools_stay_closed_when_the_policy_disallows_them() -> None:
    """任务也不能凭空打开 MEDIUM 门（allow_medium=false 时 dig 步骤直接停下）。"""
    service = FakeService(allow_medium=False)
    runtime, coordinator, _ = await _build(service)
    try:
        plan = TaskPlan(
            objective="挖一块木头",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_dig",
                    arguments={"x": 1, "y": 64, "z": 1, "expected_block": "oak_log"},
                    risk="MEDIUM",
                )
            ],
            expected_final_state=ExpectedFinalState(),
        )
        record = await runtime.create_task(
            "挖一块木头", session_id=SESSION, user_id=USER, origin="user", plan=plan
        )
        record = await runtime.confirm_and_start(
            record.task_id, user_id=USER, session_id=SESSION, origin="user"
        )
        assert record.state is TaskState.PAUSED
        assert record.failure == "AUTHORIZATION"
        assert "dig" not in [name for name, _ in service.calls], "策略拒绝时不该碰 runtime"
    finally:
        await coordinator.stop()


def test_observations_are_recorded_for_audit() -> None:
    """计划里必须留一份"当时看到了什么"（审计与复现用）。"""
    observation = Observation(
        tool="minecraft_find_blocks",
        arguments={"block_names": ["oak_log"], "max_distance": 16},
        result={"ok": True, "matches": [{"position": {"x": 1, "y": 2, "z": 3}}]},
    )
    plan = TaskPlan(
        objective="找木头",
        steps=[TaskStep(step_id="step_1", tool="minecraft_inventory", arguments={}, risk="SAFE")],
        expected_final_state=ExpectedFinalState(),
        observations=[observation.to_payload()],
    )
    assert plan.observations[0]["tool"] == "minecraft_find_blocks"
    assert plan.hash_payload()["objective"] == "找木头"


async def test_runtime_is_built_with_the_authorizer_installed() -> None:
    """装配出来的 bridge 必须持有授权校验器（None = 任务步骤一律不放行，fail-closed）。"""
    service = FakeService()
    runtime, coordinator, _ = await _build(service)
    try:
        bridge = service.agent  # type: ignore[attr-defined]
        assert bridge._task_authorizer is not None  # noqa: SLF001
        assert bridge._task_authorizer == runtime.authorize_step
    finally:
        await coordinator.stop()
