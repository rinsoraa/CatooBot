"""Phase 8H Follow-up F2：候选搜索的记忆化范围、回滚完整性与预算计数（与文档一致）。"""

from __future__ import annotations

import pytest

from app.tasks.craft_budget import CraftBudget
from app.tasks.craft_plan import (
    CRAFT_PLAN_DEPTH,
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
PLANKS = _spec("oak_planks*4=oak_log*1", 4, {"oak_log": 1})
STICK_A = _spec("stick*4=oak_planks*2", 4, {"oak_planks": 2})
STICK_B = _spec("stick*4=birch_planks*2", 4, {"birch_planks": 2})
PICKAXE_ALL = (PICKAXE_ACACIA, PICKAXE_BAMBOO, PICKAXE_OAK)


def _lookup(calls: list[str], table: dict[str, tuple[RecipeSpec, ...]]):
    async def lookup(name: str) -> tuple[RecipeSpec, ...]:
        calls.append(name)
        return tuple(table.get(name, ()))

    return lookup


async def test_no_recipe_subproblem_is_queried_once_and_fails_stably() -> None:
    calls: list[str] = []
    lookup = _lookup(calls, {"stick": (STICK_A, STICK_B), "oak_planks": (PLANKS,)})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick", requested=8, inventory={"oak_planks": 2}, lookup=lookup, budget=CraftBudget()
        )
    assert excinfo.value.reason == CRAFT_PLAN_MATERIAL
    assert calls.count("birch_planks") == 1


async def test_cycle_failures_do_not_consume_the_search_budget() -> None:
    cycle_a = _spec("aaa*1=bbb*1", 1, {"bbb": 1})
    cycle_b = _spec("bbb*1=aaa*1", 1, {"aaa": 1})
    lookup = _lookup([], {"aaa": (cycle_a,), "bbb": (cycle_b,)})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "aaa",
            requested=1,
            inventory={},
            lookup=lookup,
            budget=CraftBudget(max_recipe_search_states=4),
        )
    assert excinfo.value.reason != CRAFT_PLAN_SEARCH


async def test_depth_failures_do_not_consume_the_search_budget() -> None:
    chain = {f"i{i}": (_spec(f"i{i}*1=i{i + 1}*1", 1, {f"i{i + 1}": 1}),) for i in range(6)}
    lookup = _lookup([], chain)
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "i0",
            requested=1,
            inventory={},
            lookup=lookup,
            budget=CraftBudget(max_recipe_search_states=4),
        )
    assert excinfo.value.reason == CRAFT_PLAN_DEPTH


async def test_material_verdicts_are_not_cached_across_inventory_states() -> None:
    table = {"wooden_pickaxe": (PICKAXE_OAK,), "oak_planks": (PLANKS,)}
    lookup = _lookup([], table)
    with pytest.raises(CraftPlanError):
        await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"stick": 2},
            lookup=lookup,
            budget=CraftBudget(),
        )
    planned = await build_craft_plan(
        "wooden_pickaxe",
        requested=1,
        inventory={"oak_log": 1, "stick": 2},
        lookup=lookup,
        budget=CraftBudget(),
    )
    assert planned.recipe_ids == (
        "oak_planks*4=oak_log*1",
        "!wooden_pickaxe*1=oak_planks*3+stick*2",
    )


async def test_failed_candidate_rollback_leaves_no_trace() -> None:
    with_failure = await build_craft_plan(
        "wooden_pickaxe",
        requested=1,
        inventory={"oak_log": 1, "stick": 2},
        lookup=_lookup(
            [], {"wooden_pickaxe": (PICKAXE_ACACIA, PICKAXE_OAK), "oak_planks": (PLANKS,)}
        ),
        budget=CraftBudget(),
    )
    clean = await build_craft_plan(
        "wooden_pickaxe",
        requested=1,
        inventory={"oak_log": 1, "stick": 2},
        lookup=_lookup([], {"wooden_pickaxe": (PICKAXE_OAK,), "oak_planks": (PLANKS,)}),
        budget=CraftBudget(),
    )
    assert with_failure.recipe_ids == clean.recipe_ids
    assert with_failure.nodes == clean.nodes
    assert with_failure.inventory_delta == clean.inventory_delta
    assert with_failure.inventory_consumed == clean.inventory_consumed
    assert with_failure.total_inputs == clean.total_inputs


async def test_search_budget_counts_every_candidate_attempt() -> None:
    table = {"wooden_pickaxe": PICKAXE_ALL, "oak_planks": (PLANKS,)}
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"oak_log": 1, "stick": 2},
            lookup=_lookup([], table),
            budget=CraftBudget(max_recipe_search_states=2),
        )
    assert excinfo.value.reason == CRAFT_PLAN_SEARCH
    planned = await build_craft_plan(
        "wooden_pickaxe",
        requested=1,
        inventory={"oak_log": 1, "stick": 2},
        lookup=_lookup([], table),
        budget=CraftBudget(max_recipe_search_states=8),
    )
    assert planned.recipe_ids[-1] == PICKAXE_OAK.recipe_id


async def test_budget_exhaustion_never_returns_a_partial_plan() -> None:
    table = {"wooden_pickaxe": PICKAXE_ALL, "oak_planks": (PLANKS,)}
    result = None
    with pytest.raises(CraftPlanError) as excinfo:
        result = await build_craft_plan(
            "wooden_pickaxe",
            requested=1,
            inventory={"oak_log": 1, "stick": 2},
            lookup=_lookup([], table),
            budget=CraftBudget(max_recipe_search_states=1),
        )
    assert excinfo.value.reason == CRAFT_PLAN_SEARCH
    assert result is None
