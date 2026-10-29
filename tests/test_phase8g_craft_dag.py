"""Phase 8G G3：受限多步合成 DAG（构建、合并、预算、拓扑序、净值）。"""

from __future__ import annotations

import pytest

from app.tasks.craft_budget import CraftBudget
from app.tasks.craft_plan import (
    CRAFT_PLAN_CYCLE,
    CRAFT_PLAN_DEPTH,
    CRAFT_PLAN_INPUTS,
    CRAFT_PLAN_MATERIAL,
    CRAFT_PLAN_NO_RECIPE,
    CRAFT_PLAN_STEPS,
    CRAFT_PLAN_TABLE,
    CraftPlanError,
    RecipeSpec,
    build_craft_plan,
)

STICK = RecipeSpec("stick", "stick*4=oak_planks*2", 4, (("oak_planks", 2),))
PLANKS = RecipeSpec("oak_planks", "oak_planks*4=oak_log*1", 4, (("oak_log", 1),))
LOG_PLANKS = RecipeSpec("oak_planks", "!oak_planks*2=oak_log*1", 2, (("oak_log", 1),), True)
TABLE = RecipeSpec(
    "crafting_table", "!crafting_table*1=oak_planks*4", 1, (("oak_planks", 4),), True
)


def _lookup(recipes: dict[str, RecipeSpec]):
    async def lookup(item: str) -> RecipeSpec | None:
        return recipes.get(item)

    return lookup


async def test_single_step_plan_from_existing_materials() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=1,
        inventory={"oak_planks": 2},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    assert [node.item for node in plan.nodes] == ["stick"]
    assert plan.craft_steps == 1 and plan.depth == 1
    assert plan.inventory_delta == {"stick": 4}
    assert plan.inventory_consumed == {"oak_planks": 2}
    assert plan.requires_table is False
    assert plan.total_inputs == 2
    assert plan.projected["oak_planks"] == 0


async def test_multi_step_dag_is_dependency_first() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=1,
        inventory={"oak_log": 1},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    # 依赖优先：先做木板，再做木棍
    assert [node.item for node in plan.nodes] == ["oak_planks", "stick"]
    assert [node.depth for node in plan.nodes] == [2, 1]
    assert plan.craft_steps == 2
    # 净值：原木 -1，木板 +4（真的多出来了），木棍 +4
    assert plan.inventory_consumed == {"oak_log": 1}
    assert plan.inventory_delta == {"oak_planks": 2, "stick": 4}
    assert "multi_step" in plan.notes


async def test_existing_stock_is_merged_not_double_consumed() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=1,
        inventory={"oak_planks": 4, "oak_log": 9},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    # 库存里的木板直接抵扣 → 不需要木板节点、也不动原木
    assert [node.item for node in plan.nodes] == ["stick"]
    assert plan.inventory_consumed == {"oak_planks": 2}
    assert plan.inventory_delta == {"stick": 4}
    assert plan.inventory_before["oak_log"] == 9
    assert plan.projected["oak_log"] == 9


async def test_two_batches_when_more_is_requested() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=8,
        inventory={"oak_planks": 4},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    assert plan.craft_steps == 2
    assert plan.inventory_delta == {"stick": 8}
    assert plan.inventory_consumed == {"oak_planks": 4}


async def test_default_batch_when_no_count_given() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=0,
        inventory={"oak_planks": 2},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    assert plan.requested == 0
    assert plan.craft_steps == 1 and plan.inventory_delta == {"stick": 4}


async def test_missing_base_material_is_reported() -> None:
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick",
            requested=1,
            inventory={},
            lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
            budget=CraftBudget(),
        )
    assert excinfo.value.reason == CRAFT_PLAN_MATERIAL
    assert "oak_log" in excinfo.value.detail


async def test_unknown_target_is_no_recipe() -> None:
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "diamond_pickaxe",
            requested=1,
            inventory={},
            lookup=_lookup({"stick": STICK}),
            budget=CraftBudget(),
        )
    assert excinfo.value.reason == CRAFT_PLAN_NO_RECIPE


async def test_recipe_cycle_is_detected() -> None:
    a = RecipeSpec("a", "a*1=b*1", 1, (("b", 1),))
    b = RecipeSpec("b", "b*1=a*1", 1, (("a", 1),))
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "a",
            requested=1,
            inventory={},
            lookup=_lookup({"a": a, "b": b}),
            budget=CraftBudget(),
        )
    assert excinfo.value.reason == CRAFT_PLAN_CYCLE


async def test_depth_budget_is_enforced() -> None:
    chain: dict[str, RecipeSpec] = {}
    for level in range(1, 7):
        name, nxt = f"i{level}", f"i{level + 1}"
        chain[name] = RecipeSpec(name, f"{name}*1={nxt}*1", 1, ((nxt, 1),))
    chain["i7"] = RecipeSpec("i7", "i7*1=log*1", 1, (("log", 1),))
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "i1",
            requested=1,
            inventory={"log": 8},
            lookup=_lookup(chain),
            budget=CraftBudget(max_depth=4),
        )
    assert excinfo.value.reason == CRAFT_PLAN_DEPTH


async def test_step_budget_uses_the_stricter_existing_limit() -> None:
    budget = CraftBudget(max_craft_steps=1)
    # 既有执行器的动作步上限是 8，这里任务书预算 1 更严格 → 以 1 为准
    assert budget.effective_max_craft_steps == 1
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick",
            requested=8,
            inventory={"oak_planks": 4},
            lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
            budget=budget,
        )
    assert excinfo.value.reason == CRAFT_PLAN_STEPS


async def test_input_budget_is_enforced() -> None:
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick",
            requested=1,
            inventory={"oak_planks": 2},
            lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
            budget=CraftBudget(max_total_inputs=1),
        )
    assert excinfo.value.reason == CRAFT_PLAN_INPUTS


async def test_per_item_count_budget_clamps_with_disclosure() -> None:
    """§5.3：单目标数量上限 → **夹取 + 明确披露**（绝不静默少做，也不假装做了 17 个）。"""
    plan = await build_craft_plan(
        "stick",
        requested=17,
        inventory={"oak_planks": 64},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    assert plan.requested == 17
    assert plan.planned_amount == 16
    assert plan.capped is True
    assert plan.inventory_delta == {"stick": 16}
    assert plan.inventory_consumed == {"oak_planks": 8}


async def test_step_budget_is_refused_when_the_dag_cannot_fit() -> None:
    """宽配方链会撑爆动作步预算 → 明确拒绝（不硬凑、不偷偷砍步骤）。"""
    wide = RecipeSpec("widget", "widget*1=oak_planks*4", 1, (("oak_planks", 4),))
    thin = RecipeSpec("oak_planks", "oak_planks*1=oak_log*1", 1, (("oak_log", 1),))
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "widget",
            requested=4,
            inventory={"oak_log": 64},
            lookup=_lookup({"widget": wide, "oak_planks": thin}),
            budget=CraftBudget(),
        )
    assert excinfo.value.reason == CRAFT_PLAN_STEPS


async def test_table_recipes_are_flagged_and_refusable() -> None:
    allowed = await build_craft_plan(
        "crafting_table",
        requested=1,
        inventory={"oak_planks": 4},
        lookup=_lookup({"crafting_table": TABLE}),
        budget=CraftBudget(),
        allow_table=True,
    )
    assert allowed.requires_table is True
    assert "requires_workbench" in allowed.notes
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "crafting_table",
            requested=1,
            inventory={"oak_planks": 4},
            lookup=_lookup({"crafting_table": TABLE}),
            budget=CraftBudget(),
            allow_table=False,
        )
    assert excinfo.value.reason == CRAFT_PLAN_TABLE


async def test_multi_batch_table_recipe_keeps_the_flag() -> None:
    plan = await build_craft_plan(
        "oak_planks",
        requested=4,
        inventory={"oak_log": 2},
        lookup=_lookup({"oak_planks": LOG_PLANKS}),
        budget=CraftBudget(),
    )
    assert plan.requires_table is True
    assert plan.craft_steps == 2 and plan.inventory_delta == {"oak_planks": 4}
    assert plan.inventory_consumed == {"oak_log": 2}


async def test_describe_payload_is_auditable() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=1,
        inventory={"oak_log": 1},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    described = plan.describe()
    assert described["target"] == "stick"
    assert described["craft_steps"] == 2
    assert isinstance(described["nodes"], list) and len(described["nodes"]) == 2
    assert described["nodes"][0]["recipe_id"] == "oak_planks*4=oak_log*1"


async def test_projected_inventory_is_consistent_with_delta_and_consumed() -> None:
    plan = await build_craft_plan(
        "stick",
        requested=8,
        inventory={"oak_log": 2},
        lookup=_lookup({"stick": STICK, "oak_planks": PLANKS}),
        budget=CraftBudget(),
    )
    for name, after in plan.projected.items():
        before = plan.inventory_before.get(name, 0)
        gained = plan.inventory_delta.get(name, 0)
        lost = plan.inventory_consumed.get(name, 0)
        assert after == before + gained - lost
