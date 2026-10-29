"""R4 deterministic build plan tests; observation only, never placement."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.build_plan import BuildCell, BuildRequest, plan_build_task
from app.tasks.planner import ObservationFailed


class BuildObservations:
    def __init__(
        self, *, item="minecraft:dirt", held=1, target_block="air", reference_block="smooth_stone"
    ):
        self.item = item
        self.held = held
        self.target_block = target_block
        self.reference_block = reference_block
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, dict(arguments)))
        if tool == "minecraft_world":
            return SimpleNamespace(result={"position": {"x": 10.0, "y": 64.0, "z": 10.0}})
        if tool == "minecraft_inventory":
            return SimpleNamespace(result={"held_item": {"name": self.item, "count": self.held}})
        if tool == "minecraft_dig_capability":
            x, y, z = arguments["x"], arguments["y"], arguments["z"]
            is_target = (x, y, z) == (10, 64, 10) or (x, y, z) in set(getattr(self, "cells", ()))
            name = self.target_block if is_target else self.reference_block
            return SimpleNamespace(
                result={
                    "block": {"name": name},
                    "distance": {"raw": 3.2, "goal_near": 3},
                }
            )
        raise AssertionError("unexpected observation: " + tool)


def request(item="minecraft:dirt", cells=None):
    return BuildRequest(item=item, cells=tuple(cells or [BuildCell(10, 64, 10)]))


async def test_single_placement_plan_has_one_place_step() -> None:
    observe = BuildObservations()
    observe.cells = [(10, 64, 10)]
    planned = await plan_build_task("在 (10,64,10) 放一块泥土", request(), observe=observe)
    assert [step.tool for step in planned.plan.steps] == ["minecraft_place"]
    assert planned.plan.steps[0].arguments == {
        "x": 10,
        "y": 64,
        "z": 10,
        "face": "up",
        "expected_item": "minecraft:dirt",
    }
    assert planned.plan.expected_final_state.inventory_consumed == {}
    assert not any(call[0] == "minecraft_place" for call in observe.calls)


async def test_multi_cell_plan_is_ordered_and_frozen() -> None:
    observe = BuildObservations(held=3)
    cells = [BuildCell(9, 64, 10), BuildCell(10, 64, 10), BuildCell(11, 64, 10)]
    observe.cells = [(c.x, c.y, c.z) for c in cells]
    planned = await plan_build_task("放三块泥土", request(cells=cells), observe=observe)
    assert [step.step_id for step in planned.plan.steps] == ["step_1", "step_2", "step_3"]
    assert [step.arguments["x"] for step in planned.plan.steps] == [9, 10, 11]
    assert planned.plan.expected_final_state.inventory_consumed == {}


async def test_material_missing_or_insufficient_is_rejected() -> None:
    observe = BuildObservations(held=0)
    with pytest.raises(ObservationFailed, match="material"):
        await plan_build_task("放一块泥土", request(), observe=observe)


async def test_occupied_target_is_rejected() -> None:
    observe = BuildObservations(target_block="stone")
    observe.cells = [(10, 64, 10)]
    with pytest.raises(ObservationFailed, match="target_occupied"):
        await plan_build_task("放一块泥土", request(), observe=observe)


async def test_missing_reference_is_rejected() -> None:
    observe = BuildObservations(reference_block="air")
    observe.cells = [(10, 64, 10)]
    with pytest.raises(ObservationFailed, match="reference_missing"):
        await plan_build_task("放一块泥土", request(), observe=observe)


async def test_cell_outside_player_region_is_rejected() -> None:
    observe = BuildObservations()
    cell = BuildCell(13, 64, 10)
    observe.cells = [(cell.x, cell.y, cell.z)]
    with pytest.raises(ObservationFailed, match="outside_region"):
        await plan_build_task("放一块泥土", request(cells=[cell]), observe=observe)


async def test_over_four_cells_is_rejected() -> None:
    cells = [BuildCell(10, 64, 10 + i) for i in range(5)]
    with pytest.raises(ObservationFailed, match="too_many_cells"):
        await plan_build_task("放五块泥土", request(cells=cells), observe=BuildObservations())


async def test_plan_hash_covers_cell_and_item() -> None:
    observe = BuildObservations()
    observe.cells = [(10, 64, 10), (11, 64, 10)]
    first = await plan_build_task(
        "放一块泥土", request(cells=[BuildCell(10, 64, 10)]), observe=observe
    )
    second = await plan_build_task(
        "放一块泥土", request(cells=[BuildCell(11, 64, 10)]), observe=observe
    )
    assert first.plan.plan_hash != second.plan.plan_hash


def test_task_adapter_registers_place_route() -> None:
    from app.integrations.minecraft.task_adapter import _SERVICE_ROUTES

    assert "minecraft_place" in _SERVICE_ROUTES
