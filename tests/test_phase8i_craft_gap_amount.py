"""Phase 8I：缺料 detail 必须保留真实数量，否则资源交接提案永远不会触发。

真实真机门禁（scripts/phase8i_handoff_smoke_real.py）暴露的缺陷：craft_plan 的
记忆化缓存只存原因码，复述时丢掉 (缺 N)，于是 craft_gap_from_detail 解析不出缺什么缺多少，
H1/H2 的用户可见路径在真实服务器上**永远不会**走到。这里把修复固定在测试里。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.craft_budget import CraftBudget
from app.tasks.craft_plan import (
    CRAFT_PLAN_MATERIAL,
    CraftPlanError,
    RecipeSpec,
    build_craft_plan,
)
from app.tasks.resource_handoff import craft_gap_from_detail, propose_handoff


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


PLANKS = _spec("oak_planks*4=oak_log*1", 4, {"oak_log": 1})
STICK_A = _spec("stick*4=oak_planks*2", 4, {"oak_planks": 2})
STICK_B = _spec("stick*4=birch_planks*2", 4, {"birch_planks": 2})


def _lookup(calls: list[str], table: dict[str, tuple[RecipeSpec, ...]]):
    async def lookup(name: str) -> tuple[RecipeSpec, ...]:
        calls.append(name)
        return tuple(table.get(name, ()))

    return lookup


async def test_memoized_material_failure_keeps_the_missing_amount() -> None:
    """记忆化剪枝复述失败时，必须把 (缺 N) 一起带出来（否则缺料诊断解析不到数量）。"""
    calls: list[str] = []
    lookup = _lookup(calls, {"stick": (STICK_A, STICK_B), "oak_planks": (PLANKS,)})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick", requested=8, inventory={"oak_planks": 2}, lookup=lookup, budget=CraftBudget()
        )
    error = excinfo.value
    assert error.reason == CRAFT_PLAN_MATERIAL
    # 该子问题确实只查过一次（走的是记忆化复述路径，不是首次求解路径）。
    assert calls.count("birch_planks") == 1
    assert "(缺 " in error.detail, "复述路径丢了真实缺失数量：" + repr(error.detail)
    gap = craft_gap_from_detail(error.reason + ":" + error.detail)
    assert gap.is_material_gap, "真实缺料 detail 必须能解析成可交接的缺料"
    assert gap.missing and gap.missing[0][1] >= 1


async def test_real_planner_detail_becomes_a_bounded_proposal() -> None:
    """真实形态的缺料 detail → 有界提案（数量按既有 WOOD_MAX_TARGETS 夹取）。"""
    lookup = _lookup([], {"stick": (STICK_A,), "oak_planks": (PLANKS,)})
    with pytest.raises(CraftPlanError) as excinfo:
        await build_craft_plan(
            "stick", requested=8, inventory={"oak_planks": 2}, lookup=lookup, budget=CraftBudget()
        )
    detail = excinfo.value.reason + ":" + excinfo.value.detail
    # 真实叶子是**原木**（既有木材能力支持矩阵里就有它），这才是可交接的缺料。
    assert craft_gap_from_detail(detail).missing[0][0] == "oak_log"
    observed: list[tuple[str, dict[str, Any]]] = []

    async def observe(tool: str, args: Any) -> Any:
        observed.append((tool, dict(args)))
        return type(
            "Invocation",
            (),
            {
                "result": {
                    "ok": True,
                    "matches": [
                        {"position": {"x": 1, "y": 2, "z": 3}, "distance": {"goal_near": 2.5}}
                    ],
                }
            },
        )()

    proposal = await propose_handoff(detail=detail, observe=observe, craft_goal="做木棍")
    assert proposal is not None
    assert observed and observed[0][0] == "minecraft_find_blocks"
    assert proposal.amount <= 2 and proposal.amount >= 1
    gap_item = craft_gap_from_detail(detail).missing[0][0]
    assert proposal.item == gap_item
    assert proposal.block == gap_item


async def test_bare_material_gap_without_amount_is_not_a_handoff() -> None:
    """只有原因码、没有真实数量时**不**交接（不猜数量、不观察、不提案）。"""
    called: list[str] = []

    async def observe(tool: str, args: Any) -> Any:
        called.append(tool)
        return None

    gap = craft_gap_from_detail("insufficient_material:acacia_log")
    assert gap.reason == CRAFT_PLAN_MATERIAL
    assert not gap.is_material_gap
    assert await propose_handoff(detail="insufficient_material:acacia_log", observe=observe) is None
    assert called == [], "没有真实数量就不该发起任何观察"
