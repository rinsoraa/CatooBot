"""Phase 8G G1/G2：工作台感知 + 工作台合成的规划期行为（六态、接近步、零多余读取）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.craft_budget import CraftBudget
from app.tasks.planner import ObservationFailed, plan_craft_task
from tests.test_phase8f_craft_plan import PLANKS, PLANKS_RECIPE, TABLE_RECIPE, _items

OAK_LOG = "oak_log"
TABLE_POS = (1, 64, 0)


def _world(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "available": True,
        "online": True,
        "age_seconds": 0.5,
        "semantic": {
            "self_state": {"dimension": "overworld", "position": {"x": 0.0, "y": 64.0, "z": 0.0}}
        },
    }
    payload.update(overrides)
    return payload


def _blocks(*positions: tuple[int, int, int], name: str = "crafting_table") -> dict[str, Any]:
    return {
        "matches": [
            {"block": {"name": name}, "position": {"x": px, "y": py, "z": pz}}
            for px, py, pz in positions
        ]
    }


class FakeObserve:
    """脚本化真实读出：背包 / 配方 / 世界 / 找方块，并记录**每一次**调用。"""

    def __init__(
        self,
        *,
        items: list[dict[str, Any]],
        recipes: dict[str, list[dict[str, Any]]],
        world: dict[str, Any] | None = None,
        blocks: dict[str, Any] | None = None,
        blocks_error: Exception | None = None,
    ) -> None:
        self.items = list(items)
        self.recipes = dict(recipes)
        self.world = world
        self.blocks = blocks
        self.blocks_error = blocks_error
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: Any) -> Any:
        args = dict(arguments)
        self.calls.append((tool, args))
        payload: dict[str, Any]
        if tool == "minecraft_inventory":
            payload = {"ok": True, "items": list(self.items)}
        elif tool == "minecraft_recipe_lookup":
            rows = self.recipes.get(str(args.get("item") or ""))
            payload = {"ok": True, "recipes": list(rows or []), "total": len(rows or [])}
        elif tool == "minecraft_world":
            payload = dict(self.world or {})
        elif tool == "minecraft_find_blocks":
            if self.blocks_error is not None:
                raise self.blocks_error
            payload = dict(self.blocks or {"matches": []})
        else:
            payload = {}
        return type("R", (), {"result": payload, "summary": tool})()

    @property
    def tools(self) -> list[str]:
        return [tool for tool, _args in self.calls]


def _observe(
    *,
    world: dict[str, Any] | None,
    blocks: dict[str, Any] | None,
    **kwargs: Any,
) -> FakeObserve:
    return FakeObserve(
        items=_items(oak_log=8),
        recipes={PLANKS: [TABLE_RECIPE]},
        world=world,
        blocks=blocks,
        **kwargs,
    )


async def test_available_workbench_is_frozen_into_every_table_step() -> None:
    observe = _observe(world=_world(), blocks=_blocks(TABLE_POS))
    planned = await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    crafts = [step for step in planned.plan.steps if step.tool == "minecraft_craft"]
    assert [step.arguments["recipe_id"] for step in crafts] == [TABLE_RECIPE["recipe_id"]]
    assert crafts[0].arguments["crafting_table"] == {"x": 1, "y": 64, "z": 0}
    assert all(isinstance(value, int) for value in crafts[0].arguments["crafting_table"].values())
    assert planned.plan.steps[-1].tool == "minecraft_inventory"
    # 只读观察，且只在**需要工作台时**才读世界与找方块
    assert observe.tools.count("minecraft_find_blocks") == 1


async def test_workbench_is_not_probed_when_the_recipe_does_not_need_it() -> None:
    observe = FakeObserve(
        items=_items(oak_log=8),
        recipes={PLANKS: [PLANKS_RECIPE]},
        world=_world(),
        blocks=_blocks(TABLE_POS),
    )
    planned = await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    assert "minecraft_find_blocks" not in observe.tools
    assert "minecraft_world" not in observe.tools
    assert "crafting_table" not in planned.plan.steps[0].arguments


async def test_unreachable_workbench_gets_one_explicit_approach_step() -> None:
    observe = _observe(world=_world(), blocks=_blocks((9, 64, 0)))
    planned = await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    steps = list(planned.plan.steps)
    assert steps[0].tool == "minecraft_move_to" and steps[0].risk == "LOW"
    assert steps[0].arguments == {"x": 9, "y": 64, "z": 0}
    assert steps[0].expected_state is not None
    assert steps[0].expected_state.position_within == {
        "x": 9.0,
        "y": 64.0,
        "z": 0.0,
        "radius": 2.0,
    }
    craft = steps[1]
    assert craft.tool == "minecraft_craft"
    assert craft.depends_on == ("step_1",), "合成必须排在到位之后"


async def test_approach_can_be_switched_off_by_the_budget() -> None:
    observe = _observe(world=_world(), blocks=_blocks((9, 64, 0)))
    with pytest.raises(ObservationFailed, match="crafting_table_required"):
        await plan_craft_task(
            "做一块橡木木板",
            observe=observe,
            item=PLANKS,
            count=4,
            budget=CraftBudget(allow_approach=False),
        )


async def test_workbench_not_found_is_reported_honestly() -> None:
    observe = _observe(world=_world(), blocks={"matches": []})
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    message = str(excinfo.value)
    assert message.startswith("crafting_table_required"), message
    assert "NOT_FOUND" in message and "workbench_not_found_in_range" in message
    assert "工作台" in message


async def test_non_table_block_is_never_accepted_as_a_workbench() -> None:
    observe = _observe(world=_world(), blocks=_blocks(TABLE_POS, name="furnace"))
    with pytest.raises(ObservationFailed, match="NOT_FOUND"):
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)


async def test_stale_world_view_refuses_instead_of_using_an_old_position() -> None:
    observe = _observe(world=_world(age_seconds=99.0), blocks=_blocks(TABLE_POS))
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    assert "STALE" in str(excinfo.value) and "world_stale" in str(excinfo.value)
    assert "minecraft_find_blocks" not in observe.tools, "过期快照不拿去搜方块"


async def test_unknown_world_view_refuses_without_claiming_capability() -> None:
    observe = _observe(world={}, blocks=_blocks(TABLE_POS))
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    assert "UNKNOWN" in str(excinfo.value)


async def test_workbench_read_error_refuses_instead_of_faking_a_table() -> None:
    observe = _observe(world=_world(), blocks=None, blocks_error=RuntimeError("runtime down"))
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("做一块橡木木板", observe=observe, item=PLANKS, count=4)
    assert "ERROR" in str(excinfo.value) and "find_blocks_failed" in str(excinfo.value)


def test_workbench_reason_codes_are_stable() -> None:
    from app.tasks import craft_plan, workbench

    assert craft_plan.CRAFT_PLAN_TABLE == "crafting_table_required"
    assert workbench.WORKBENCH_MAX_DISTANCE == 5.0
    assert workbench.WORKBENCH_BLOCK == "crafting_table"
