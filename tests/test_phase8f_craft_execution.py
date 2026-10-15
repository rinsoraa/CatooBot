"""Phase 8F §9.2：合成执行的**真实哨兵**闭环（真实计划 → 真实 TaskRuntime → 真实后置验证）。

这里的 ``invoke`` 是一张可编排的迷你世界（真实背包 + 配方效果）：合成果真会改背包。
断言全部落在真实哨兵上：工具调用轨迹（recipe_id / risk / task_id）、任务与步骤状态、
执行前后的真实背包、运行时的独立后置验证结果 —— 不看回复文本，也不信工具自述。
"""

from __future__ import annotations

from typing import Any

from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome
from app.tasks.models import TaskPlan, TaskState
from app.tasks.runtime import TaskInvocation
from tests import test_task_runtime as harness
from tests.test_phase8f_craft_plan import (
    PLANKS_RECIPE,
    STICK_RECIPE,
    FakeObserve,
    _items,
)
from tests.test_task_runtime import create_confirmed, make_runtime

OAK_LOG = "oak_log"
PLANKS = "oak_planks"
STICK = "stick"
PLANKS_ID = "oak_planks*4=oak_log*1"
STICK_ID = "stick*4=oak_planks*2"

#: 配方效果（与 tests/test_phase8f_craft_plan.py 里的真实投影一一对应）。
EFFECTS: dict[str, tuple[str, int, dict[str, int]]] = {
    PLANKS_ID: (PLANKS, 4, {OAK_LOG: 1}),
    STICK_ID: (STICK, 4, {PLANKS: 2}),
}


class CraftWorld:
    """可编排的迷你世界：合成**真的**改这张背包；可注入"没生效/不消耗/读取失败"。"""

    def __init__(
        self,
        counts: dict[str, int],
        *,
        apply_effect: bool = True,
        consume_materials: bool = True,
        fail_craft_at: int = 0,
        detached_craft_at: int = 0,
        inventory_fail_from: int = 0,
    ) -> None:
        self.counts = dict(counts)
        self.apply_effect = apply_effect
        self.consume_materials = consume_materials
        self.fail_craft_at = fail_craft_at
        self.detached_craft_at = detached_craft_at
        self.inventory_fail_from = inventory_fail_from
        self.calls: list[dict[str, Any]] = []
        self.craft_count = 0
        self.inventory_count = 0
        self.task_id = ""

    def snapshot(self) -> dict[str, Any]:
        return {
            "ok": True,
            "items": [
                {"name": name, "count": count}
                for name, count in sorted(self.counts.items())
                if count
            ],
        }

    def crafts(self) -> list[dict[str, Any]]:
        return [call for call in self.calls if call["tool"] == "minecraft_craft"]

    async def __call__(
        self,
        tool: str,
        arguments: Any,
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
        if task_id:
            self.task_id = task_id
        if tool == "minecraft_inventory":
            self.inventory_count += 1
            if self.inventory_fail_from and self.inventory_count >= self.inventory_fail_from:
                return TaskInvocation(
                    ok=False, status="FAILED", error="背包读取超时", code="minecraft.timeout"
                )
            return TaskInvocation(ok=True, status="SUCCEEDED", result=self.snapshot())
        if tool == "minecraft_craft":
            self.craft_count += 1
            if self.fail_craft_at and self.craft_count == self.fail_craft_at:
                return TaskInvocation(
                    ok=False, status="FAILED", error="合成失败", code="minecraft.craft_failed"
                )
            effect = EFFECTS.get(str(dict(arguments).get("recipe_id") or ""))
            if effect is None:
                return TaskInvocation(
                    ok=False, status="FAILED", error="配方不存在", code="minecraft.recipe_not_found"
                )
            if self.detached_craft_at and self.craft_count == self.detached_craft_at:
                return TaskInvocation(
                    ok=True,
                    status="RUNNING",
                    action_id=f"act_craft_{self.craft_count}",
                    summary="已开始",
                )
            self._apply(effect)
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={"ok": True, "result": {"crafted": {effect[0]: effect[1]}}},
            )
        return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})

    def _apply(self, effect: tuple[str, int, dict[str, int]]) -> None:
        name, produced, cost = effect
        if not self.apply_effect:
            return  # 工具**自称**成功，但背包没有变化
        if self.consume_materials:
            for material, need in cost.items():
                remaining = self.counts.get(material, 0) - need
                if remaining:
                    self.counts[material] = remaining
                else:
                    # 真实背包里不存在 0 个的堆叠：消耗干净就移除这个键
                    self.counts.pop(material, None)
        self.counts[name] = self.counts.get(name, 0) + produced


async def _craft_plan(counts: dict[str, int], *, count: int = 4) -> TaskPlan:
    """用**真实**规划器 + 真实观察（脚本化读出）造一份冻结合成计划。"""
    observe = FakeObserve(
        items=_items(**counts),
        recipes={STICK: [STICK_RECIPE], PLANKS: [PLANKS_RECIPE]},
    )
    result = await BoundedAgentPlanner(allow_medium=True).plan(
        f"做 {count} 根木棍", observe=observe, shape_hint="craft"
    )
    assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL, result.reason
    assert result.plan is not None
    return result.plan.plan


async def test_craft_closed_loop_really_changes_the_inventory_and_passes_verification(
    monkeypatch: Any,
) -> None:
    """§9.2：计划生成后确认前零世界修改；确认后真的改背包，并由独立后置验证判定成功。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1})
    runtime, _, _, _ = make_runtime(invoke=world)
    assert [step.tool for step in plan.steps] == [
        "minecraft_craft",
        "minecraft_craft",
        "minecraft_inventory",
    ]
    record = await runtime.create_task(
        plan.objective,
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=plan,
    )
    assert record.state is TaskState.PENDING_CONFIRMATION
    assert world.crafts() == [], "确认前不得有任何合成调用"
    assert world.counts == {OAK_LOG: 1}, "确认前不得修改任何背包状态"
    started = await runtime.confirm_and_start(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )
    assert started.state is TaskState.SUCCEEDED, started.state
    assert world.counts == {STICK: 4, PLANKS: 2}, "产物增加、材料按配方消耗"
    crafts = world.crafts()
    assert [call["arguments"]["recipe_id"] for call in crafts] == [PLANKS_ID, STICK_ID]
    assert all(call["risk"] == "MEDIUM" for call in crafts)
    assert all(call["task_id"] == record.task_id for call in crafts)
    assert all(call["step_id"] for call in crafts), "每一步都有 step_id（审计证据）"
    verification = started.verification
    assert verification["ok"] is True
    assert verification["inventory_delta"] == {STICK: 4, PLANKS: 2}
    assert verification["inventory_consumed"] == {OAK_LOG: 1}
    assert verification["expected_consumed"] == {OAK_LOG: 1}
    assert world.inventory_count >= 2, "执行前与执行后都真实重读了背包"


async def test_tool_success_without_an_inventory_change_is_never_a_success(
    monkeypatch: Any,
) -> None:
    """§7.2/§9.2：工具自称成功但背包没有相符增量 → **不得**宣告成功。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1}, apply_effect=False)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is not TaskState.SUCCEEDED
    assert record.verification.get("ok") is False
    assert record.verification["inventory_delta"][STICK] == 0
    assert world.counts == {OAK_LOG: 1}, "世界确实没变（所以不能报成功）"


async def test_material_consumption_is_part_of_the_postcondition(monkeypatch: Any) -> None:
    """§7.1：产物增加但材料没按配方消耗 → 后置验证必须失败（净耗真在判定里）。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1}, consume_materials=False)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is not TaskState.SUCCEEDED
    assert record.verification.get("ok") is False
    assert record.verification["inventory_consumed"][OAK_LOG] == 0
    assert record.verification["expected_consumed"] == {OAK_LOG: 1}


async def test_partial_completion_is_reported_honestly_and_never_retried(
    monkeypatch: Any,
) -> None:
    """§7.2/§9.2：第二刀失败 → 如实报告，且**绝不**重试（避免重复制作）。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1}, fail_craft_at=2)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is not TaskState.SUCCEEDED
    assert len(world.crafts()) == 2, "失败的那一刀不得被自动重试"
    assert world.counts.get(STICK, 0) == 0, "整体没成功：没有产物"
    assert world.counts.get(OAK_LOG, 0) == 0, "材料确实按第一刀消耗掉了"
    assert world.counts.get(PLANKS) == 4, "只完成了第一刀（部分完成）"
    assert world.counts.get(STICK, 0) == 0


async def test_cancel_between_crafts_stops_the_next_dispatch(monkeypatch: Any) -> None:
    """§8：步骤之间取消 → 停止派发后续合成步骤，绝不重复制作。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})

    class CancellingWorld(CraftWorld):
        runtime: Any = None

        async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> TaskInvocation:
            result = await super().__call__(tool, arguments, **kwargs)
            if tool == "minecraft_craft" and self.craft_count == 1 and self.runtime is not None:
                await self.runtime.cancel(self.task_id, reason="用户取消")
            return result

    world = CancellingWorld({OAK_LOG: 1})
    runtime, _, _, _ = make_runtime(invoke=world)
    world.runtime = runtime
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is TaskState.CANCELLED, record.state
    assert len(world.crafts()) == 1, "取消后不得再派发下一刀"
    assert world.counts.get(STICK, 0) == 0


async def test_late_terminal_event_never_causes_a_second_craft(monkeypatch: Any) -> None:
    """§8：已取消任务收到旧动作的迟到终态事件 → 收敛但绝不重新派发/重复制作。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1}, detached_craft_at=1)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    pending = record.pending_action_id
    assert pending == "act_craft_1", pending
    cancelled = await runtime.cancel(record.task_id, reason="用户取消")
    assert cancelled.state is TaskState.CANCELLED
    before = len(world.crafts())
    late = await runtime.on_action_event(
        action_id=pending, event="minecraft.action.completed", status="SUCCEEDED"
    )
    assert late is not None and late.state is TaskState.CANCELLED
    assert len(world.crafts()) == before, "迟到事件绝不能再派发一次合成"
    assert world.counts.get(STICK, 0) == 0


async def test_inventory_readback_failure_after_crafting_is_not_success(
    monkeypatch: Any,
) -> None:
    """§7.2/§8：合成后库存读回失败 → 状态不确定，**绝不**宣告成功。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1}, inventory_fail_from=2)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is not TaskState.SUCCEEDED
    assert world.crafts(), "合成确实派发过（但结果无法确认）"
