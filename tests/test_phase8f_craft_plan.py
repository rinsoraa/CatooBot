"""Phase 8F：有界合成计划模板 + 规划期策略闸（§4/§5/§9.1；不调用模型、不用 sleep）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome
from app.tasks.models import ExpectedFinalState
from app.tasks.planner import ObservationFailed, plan_craft_task

OAK_LOG = "oak_log"
PLANKS = "oak_planks"
STICK = "stick"

PLANKS_RECIPE: dict[str, Any] = {
    "recipe_id": "oak_planks*4=oak_log*1",
    "result": {"name": PLANKS, "count_per_craft": 4},
    "requires_table": False,
    "available": True,
    "ingredients": [{"name": OAK_LOG, "count": 1}],
}
STICK_RECIPE: dict[str, Any] = {
    "recipe_id": "stick*4=oak_planks*2",
    "result": {"name": STICK, "count_per_craft": 4},
    "requires_table": False,
    "available": True,
    "ingredients": [{"name": PLANKS, "count": 2}],
}
TABLE_RECIPE: dict[str, Any] = {
    "recipe_id": "!oak_planks*4=oak_log*1",
    "result": {"name": PLANKS, "count_per_craft": 4},
    "requires_table": True,
    "available": True,
    "ingredients": [{"name": OAK_LOG, "count": 1}],
}


def _items(**counts: int) -> list[dict[str, Any]]:
    return [{"name": name, "count": count} for name, count in counts.items()]


class FakeObserve:
    """SAFE 观察替身：按工具名返回预设的真实读出结果（形状与真实 invocation 一致）。"""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]],
        recipes: dict[str, list[dict[str, Any]]],
        inventory_ok: bool = True,
    ) -> None:
        self.items = list(items)
        self.recipes = dict(recipes)
        self.inventory_ok = inventory_ok
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: Any) -> Any:
        self.calls.append((tool, dict(arguments)))
        payload: dict[str, Any]
        if tool == "minecraft_inventory":
            payload = (
                {"ok": True, "items": list(self.items)} if self.inventory_ok else {"ok": False}
            )
        elif tool == "minecraft_recipe_lookup":
            item = str(dict(arguments).get("item") or "")
            rows = self.recipes.get(item)
            payload = {
                "ok": True,
                "item": item,
                "status": "available" if rows else "recipe_not_found",
                "total": len(rows or []),
                "recipes": list(rows or []),
            }
        else:
            payload = {}
        return type("R", (), {"result": payload, "summary": tool})()


async def test_sticks_from_logs_are_a_frozen_two_stage_chain() -> None:
    """§5.1 第 3 项：必要的前置准备（原木→木板）可以进计划；产物与消耗都进后置条件。"""
    observe = FakeObserve(
        items=_items(oak_log=1),
        recipes={STICK: [STICK_RECIPE], PLANKS: [PLANKS_RECIPE]},
    )
    planned = await plan_craft_task("做一根木棍", observe=observe, item=STICK, count=4)
    crafts = [step for step in planned.plan.steps if step.tool == "minecraft_craft"]
    assert [step.arguments["recipe_id"] for step in crafts] == [
        "oak_planks*4=oak_log*1",
        "stick*4=oak_planks*2",
    ]
    assert all(step.risk == "MEDIUM" for step in crafts)
    assert planned.plan.steps[-1].tool == "minecraft_inventory"
    state = planned.plan.expected_final_state
    # 木板既是产物又是材料 → 只写**净变化**：+2（§7.1 第 5 条）
    assert state.inventory_delta == {STICK: 4, PLANKS: 2}
    assert state.inventory_consumed == {OAK_LOG: 1}
    actions = [step for step in planned.plan.steps if step.risk != "SAFE"]
    assert len(actions) <= 8


async def test_batches_are_ceil_and_extra_output_is_only_whole_crafts() -> None:
    """§4.2：批次数 = ceil(N/q)，多出来的只能是整数刀（绝不静默多耗材料）。"""
    observe = FakeObserve(
        items=_items(oak_log=1),
        recipes={STICK: [STICK_RECIPE], PLANKS: [PLANKS_RECIPE]},
    )
    planned = await plan_craft_task("做 6 根木棍", observe=observe, item=STICK, count=6)
    crafts = [step for step in planned.plan.steps if step.tool == "minecraft_craft"]
    assert len(crafts) == 3, "2 刀木棍（ceil(6/4)）+ 1 刀木板（4 个够 2 刀木棍用）"
    state = planned.plan.expected_final_state
    assert state.inventory_delta == {STICK: 8}
    assert state.inventory_consumed == {OAK_LOG: 1}


async def test_unspecified_count_defaults_to_exactly_one_batch() -> None:
    """§9.1 用例 2：缺数量时用**已定义的默认值**（一次真实合成），不猜、不放大。"""
    observe = FakeObserve(
        items=_items(oak_log=1),
        recipes={STICK: [STICK_RECIPE], PLANKS: [PLANKS_RECIPE]},
    )
    planned = await plan_craft_task("做点木棍", observe=observe, item=STICK, count=0)
    crafts = [step for step in planned.plan.steps if step.tool == "minecraft_craft"]
    assert len(crafts) == 2
    assert planned.plan.expected_final_state.inventory_delta == {STICK: 4, PLANKS: 2}


async def test_insufficient_material_reports_exactly_what_is_missing() -> None:
    """§9.1 用例 5：材料不足 → 明确缺什么、缺多少；不自动补料、不丢物品、不开容器。"""
    observe = FakeObserve(items=[], recipes={PLANKS: [PLANKS_RECIPE]})
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    message = str(excinfo.value)
    assert message.startswith("insufficient_material"), message
    assert OAK_LOG in message
    # 决策只用了 SAFE 观察（背包 + 配方），没有任何动作步
    assert {tool for tool, _args in observe.calls} <= {
        "minecraft_inventory",
        "minecraft_recipe_lookup",
    }


async def test_table_only_recipe_is_refused_instead_of_being_faked() -> None:
    """§9.1 用例 6：需要工作台的配方如实拒绝（本阶段不自动找/走/放工作台）。"""
    observe = FakeObserve(items=_items(oak_log=1), recipes={PLANKS: [TABLE_RECIPE]})
    with pytest.raises(ObservationFailed, match="crafting_table_required"):
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)


async def test_inventory_read_failure_is_fail_closed() -> None:
    """§9.1 用例 7：背包读不回来 ≠ 背包为空 —— 绝不继续。"""
    observe = FakeObserve(items=[], recipes={PLANKS: [PLANKS_RECIPE]}, inventory_ok=False)
    with pytest.raises(ObservationFailed, match="inventory_unavailable"):
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)


async def test_missing_recipe_is_refused() -> None:
    """§9.1 用例 4：没有支持的配方 → 如实拒绝。"""
    observe = FakeObserve(items=_items(oak_log=1), recipes={})
    with pytest.raises(ObservationFailed, match="no_recipe"):
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)


async def test_count_over_cap_is_disclosed_before_confirmation() -> None:
    """§9.1 用例 12：一次任务最多 CRAFT_MAX_BATCHES 刀 —— 截断必须**可见**。"""
    observe = FakeObserve(items=_items(oak_log=64), recipes={PLANKS: [PLANKS_RECIPE]})
    result = await _planner(allow_medium=True).plan(
        "做 100 块橡木木板", observe=observe, shape_hint="craft"
    )
    assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
    detail = result.checks[-1]["detail"]
    assert detail["requested"] == 100
    assert detail["batches"] == 4
    assert detail["produced"] == 16
    assert detail["capped"] is True


async def test_two_stage_count_beyond_the_batch_budget_is_refused() -> None:
    """两级加起来超过批次预算时**不硬凑**：如实报告越界（稳定原因码）。"""
    observe = FakeObserve(
        items=_items(oak_log=64),
        recipes={STICK: [STICK_RECIPE], PLANKS: [PLANKS_RECIPE]},
    )
    with pytest.raises(ObservationFailed, match="count_out_of_bounds"):
        await plan_craft_task("做 100 根木棍", observe=observe, item=STICK, count=100)


async def test_medium_craft_is_blocked_by_policy_when_allow_medium_is_false() -> None:
    """§5.3/§6：合成是 MEDIUM —— 生产默认配置下**规划期**就 BLOCKED，不创建可执行任务。"""
    observe = FakeObserve(items=_items(oak_log=1), recipes={PLANKS: [PLANKS_RECIPE]})
    result = await _planner(allow_medium=False).plan(
        "做一块橡木木板", observe=observe, shape_hint="craft"
    )
    assert result.outcome is PlanningOutcome.BLOCKED_BY_POLICY
    assert result.reason == "minecraft_craft:MEDIUM"
    assert result.plan is not None, "计划被如实展示，但不可执行"


async def test_allow_medium_makes_the_plan_ready_for_approval() -> None:
    """允许 MEDIUM 时：计划 READY_FOR_APPROVAL，且 checks 里如实带出数量与材料。"""
    observe = FakeObserve(items=_items(oak_log=2), recipes={PLANKS: [PLANKS_RECIPE]})
    result = await _planner(allow_medium=True).plan(
        "做 6 块橡木木板", observe=observe, shape_hint="craft"
    )
    assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
    detail = result.checks[-1]["detail"]
    assert detail["item"] == PLANKS
    assert detail["requested"] == 6
    assert detail["batches"] == 2, "ceil(6/4) = 2 刀"
    assert detail["produced"] == 8
    assert detail["capped"] is False
    assert detail["default_batch"] is False
    assert detail["materials"] == {OAK_LOG: 2}


async def test_unparseable_craft_message_never_becomes_an_executable_plan() -> None:
    """§9.1 用例 3：目标物品未知 → 计划层不猜（NEEDS_MORE_INFORMATION，零计划）。"""
    observe = FakeObserve(items=_items(oak_log=1), recipes={PLANKS: [PLANKS_RECIPE]})
    result = await _planner(allow_medium=True).plan(
        "帮我做点东西", observe=observe, shape_hint="craft"
    )
    assert result.plan is None
    assert result.outcome is PlanningOutcome.NEEDS_MORE_INFORMATION


async def test_offline_observation_is_blocked_before_any_plan() -> None:
    """§9.1 用例 8：世界服务不可用（观察通道缺失）→ 规划期 BLOCKED。"""
    result = await _planner(allow_medium=True).plan(
        "做一块橡木木板", observe=None, shape_hint="craft"
    )
    assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION
    assert result.reason == "observe_unavailable"
    assert result.plan is None


def test_expected_final_state_payload_is_backward_compatible() -> None:
    """新增的材料消耗字段**空则省略** —— 8F 之前冻结计划的 plan_hash 逐字节不变。"""
    legacy = ExpectedFinalState(inventory_delta={"oak_log": 1})
    assert legacy.to_payload() == {"inventory_delta": {"oak_log": 1}}
    assert legacy.empty is False
    assert ExpectedFinalState().empty is True
    rebuilt = ExpectedFinalState.from_payload(
        {"inventory_delta": {"oak_log": 1}, "inventory_consumed": {"oak_planks": 2}}
    )
    assert rebuilt.inventory_delta == {"oak_log": 1}
    assert rebuilt.inventory_consumed == {"oak_planks": 2}
    assert rebuilt.to_payload()["inventory_consumed"] == {"oak_planks": 2}
    assert ExpectedFinalState.from_payload({"inventory_delta": {}}).empty is True


def _planner(*, allow_medium: bool) -> BoundedAgentPlanner:
    return BoundedAgentPlanner(allow_medium=allow_medium)
