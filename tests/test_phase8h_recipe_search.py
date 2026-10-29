"""Phase 8H H1：库存感知的有界配方候选搜索（修掉 available=false 时按字母序选变体的缺陷）。"""

from __future__ import annotations

import pytest

from app.tasks.craft_budget import CraftBudget
from app.tasks.craft_plan import (
    CRAFT_PLAN_CYCLE,
    CRAFT_PLAN_MATERIAL,
    CRAFT_PLAN_SEARCH,
    CraftPlanError,
    RecipeSpec,
    build_craft_plan,
)


def _spec(
    recipe_id: str, per_craft: int, ingredients: dict[str, int], *, table: bool = False
) -> RecipeSpec:
    first = recipe_id.split("=", 1)[0].lstrip("!").split("*", 1)[0]
    return RecipeSpec(
        item=first,
        recipe_id=recipe_id,
        per_craft=per_craft,
        ingredients=tuple(sorted(ingredients.items())),
        requires_table=table,
    )


PICKAXE_ACACIA = _spec(
    "!wooden_pickaxe*1=acacia_planks*3+stick*2", 1, {"acacia_planks": 3, "stick": 2}, table=True
)
PICKAXE_BAMBOO = _spec(
    "!wooden_pickaxe*1=bamboo_planks*3+stick*2", 1, {"bamboo_planks": 3, "stick": 2}, table=True
)
PICKAXE_OAK = _spec(
    "!wooden_pickaxe*1=oak_planks*3+stick*2", 1, {"oak_planks": 3, "stick": 2}, table=True
)
PLANKS_OAK = _spec("oak_planks*4=oak_log*1", 4, {"oak_log": 1})
STICK_FROM_PLANKS = _spec("stick*4=oak_planks*2", 4, {"oak_planks": 2})
STICK_FROM_BAMBOO = _spec("stick*4=bamboo_planks*2", 4, {"bamboo_planks": 2})


def _lookup(table: dict[str, list[RecipeSpec]]):
    async def lookup(name: str) -> tuple[RecipeSpec, ...]:
        return tuple(table.get(name, ()))

    return lookup


async def test_inventory_aware_search_picks_the_craftable_variant() -> None:
    """真实缺陷回归：所有变体 available=false 时，必须选**前置材料能由库存做出来**的那个。

    字母序会先试金合欢 -> 竹子 -> 橡木；前两个在受限 DAG 内不可行，必须回滚并成功选到橡木。
    """
    lookup = _lookup(
        {
            "wooden_pickaxe": [PICKAXE_ACACIA, PICKAXE_BAMBOO, PICKAXE_OAK],
            "oak_planks": [PLANKS_OAK],
        }
    )
    plan = await build_craft_plan(
        "wooden_pickaxe",
        requested=1,
        inventory={"oak_log": 3, "stick": 2},
        lookup=lookup,
        budget=CraftBudget(),
    )
    assert [node.recipe.recipe_id for node in plan.nodes] == [
        "oak_planks*4=oak_log*1",
        "!wooden_pickaxe*1=oak_planks*3+stick*2",
    ]
    assert plan.requires_table is True
    # 1 根原木做出 4 块木板：木镐吃 3 块，剩 1 块留在背包里（整数刀数的余量）。
    assert plan.inventory_consumed == {"oak_log": 1, "stick": 2}
    assert plan.inventory_delta == {"wooden_pickaxe": 1, "oak_planks": 1}


async def test_no_feasible_candidate_reports_real_missing_material() -> None:
    """没有任何可行路径时：明确报**缺什么**，绝不用字母序第一个候选伪造可执行计划。"""
    lookup = _lookup({"wooden_pickaxe": [PICKAXE_ACACIA, PICKAXE_OAK]})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"stick": 2},
            lookup=lookup,
            budget=CraftBudget(),
        )
    assert excinfo.value.reason == CRAFT_PLAN_MATERIAL
    assert "缺" in excinfo.value.detail


async def test_search_budget_exhaustion_is_an_explicit_refusal() -> None:
    """集中搜索预算超限 -> 稳定原因码，绝不静默退化成字母序选择后继续制作。"""
    lookup = _lookup(
        {
            "wooden_pickaxe": [PICKAXE_ACACIA, PICKAXE_BAMBOO, PICKAXE_OAK],
            "oak_planks": [PLANKS_OAK],
        }
    )
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"oak_log": 3, "stick": 2},
            lookup=lookup,
            budget=CraftBudget(max_recipe_search_states=1),
        )
    assert excinfo.value.reason == CRAFT_PLAN_SEARCH


async def test_recipe_cycle_is_rejected() -> None:
    cycle_a = _spec("a*1=b*1", 1, {"b": 1})
    cycle_b = _spec("b*1=a*1", 1, {"a": 1})
    lookup = _lookup({"a": [cycle_a], "b": [cycle_b]})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan("a", requested=1, inventory={}, lookup=lookup, budget=CraftBudget())
    assert excinfo.value.reason in {CRAFT_PLAN_CYCLE, CRAFT_PLAN_MATERIAL}


async def test_duplicate_candidates_are_deduplicated() -> None:
    """同一 recipe_id 出现两次只算一份候选（不去重会重复消耗库存）。"""
    lookup = _lookup({"stick": [STICK_FROM_PLANKS, STICK_FROM_PLANKS]})
    plan = await build_craft_plan(
        "stick",
        requested=4,
        inventory={"oak_planks": 2},
        lookup=lookup,
        budget=CraftBudget(),
    )
    assert [node.recipe.recipe_id for node in plan.nodes] == ["stick*4=oak_planks*2"]
    assert plan.inventory_consumed == {"oak_planks": 2}


async def test_identical_inputs_produce_identical_plans() -> None:
    """确定性：同输入 + 同候选 + 同库存 -> 同选择、同 recipe_id 序列。"""
    lookup = _lookup(
        {
            "wooden_pickaxe": [PICKAXE_ACACIA, PICKAXE_OAK],
            "oak_planks": [PLANKS_OAK],
        }
    )
    plans = [
        await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"oak_log": 3, "stick": 2},
            lookup=lookup,
            budget=CraftBudget(),
        )
        for _ in range(3)
    ]
    assert len({plan.recipe_ids for plan in plans}) == 1


async def test_fewer_craft_steps_wins_between_two_feasible_plans() -> None:
    """两份候选都可行时：额外合成步骤更少的那一份优先（§2.5 第 3 条）。"""
    lookup = _lookup(
        {
            "stick": [STICK_FROM_PLANKS, STICK_FROM_BAMBOO],
            "oak_planks": [PLANKS_OAK],
        }
    )
    plan = await build_craft_plan(
        "stick",
        requested=4,
        inventory={"oak_log": 2, "bamboo_planks": 2},
        lookup=lookup,
        budget=CraftBudget(),
    )
    assert [node.recipe.recipe_id for node in plan.nodes] == ["stick*4=bamboo_planks*2"]
    assert plan.craft_steps == 1


async def test_equivalent_candidates_prefer_the_one_without_a_table() -> None:
    """步骤数与输入量相同时，优先不需要工作台的 2x2（§2.5 第 5 条）。"""
    table_stick = _spec("!stick*4=oak_planks*2", 4, {"oak_planks": 2}, table=True)
    lookup = _lookup({"stick": [table_stick, STICK_FROM_PLANKS]})
    plan = await build_craft_plan(
        "stick",
        requested=4,
        inventory={"oak_planks": 2},
        lookup=lookup,
        budget=CraftBudget(),
    )
    assert plan.requires_table is False
    assert plan.recipe_ids == ("stick*4=oak_planks*2",)
