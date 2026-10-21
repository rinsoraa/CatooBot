"""R4: bounded placement request parsing and deterministic build planning.

This module only produces a frozen plan. It never places a block and never
authorizes a world change; TaskRuntime still requires the normal USER
confirmation and executes each step through the existing minecraft_place tool.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.tasks.models import ExpectedFinalState, TaskPlan, TaskStep, canonical_item_name
from app.tasks.planner import (
    Observation,
    ObservationFailed,
    PlannedTask,
    _result_of,
    _safe_observe,
    _self_position,
)

MAX_BUILD_CELLS = 4
BUILD_REGION_RADIUS = 1
BUILD_MAX_DISTANCE = 5.0

BUILD_ITEM_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("工作台", "minecraft:crafting_table"),
            ("crafting_table", "minecraft:crafting_table"),
            ("crafting table", "minecraft:crafting_table"),
            ("火把", "minecraft:torch"),
            ("torch", "minecraft:torch"),
            ("泥土", "minecraft:dirt"),
            ("dirt", "minecraft:dirt"),
            ("圆石", "minecraft:cobblestone"),
            ("cobblestone", "minecraft:cobblestone"),
            ("石头", "minecraft:stone"),
            ("stone", "minecraft:stone"),
            ("橡木木板", "minecraft:oak_planks"),
            ("木板", "minecraft:oak_planks"),
            ("oak_planks", "minecraft:oak_planks"),
        ),
        key=lambda item: (-len(item[0]), item[0]),
    )
)
SUPPORTED_BUILD_ITEMS = frozenset(item for _, item in BUILD_ITEM_ALIASES)

FACE_VECTORS: dict[str, tuple[int, int, int]] = {
    "up": (0, 1, 0),
    "down": (0, -1, 0),
    "north": (0, 0, -1),
    "south": (0, 0, 1),
    "east": (1, 0, 0),
    "west": (-1, 0, 0),
}


@dataclass(frozen=True)
class BuildCell:
    x: int
    y: int
    z: int
    face: str = "up"

    def to_payload(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "z": self.z, "face": self.face}


@dataclass(frozen=True)
class BuildRequest:
    """A deterministic build request; non-empty problem means refuse/clarify."""

    item: str = ""
    cells: tuple[BuildCell, ...] = ()
    problem: str = ""
    detail: str = ""

    @property
    def count(self) -> int:
        return len(self.cells)

    def region(self) -> tuple[dict[str, int], dict[str, int]]:
        xs = [cell.x for cell in self.cells]
        ys = [cell.y for cell in self.cells]
        zs = [cell.z for cell in self.cells]
        return (
            {"x": min(xs), "y": min(ys), "z": min(zs)},
            {"x": max(xs), "y": max(ys), "z": max(zs)},
        )


_COORD = r"(-?\d+)\s*[,，\s]\s*(-?\d+)\s*[,，\s]\s*(-?\d+)"
_SINGLE = re.compile(
    r"在\s*[（(]?\s*"
    + _COORD
    + r"\s*[)）]?\s*(?:位置)?\s*(?:放|摆|放置|建|搭建)\s*"
    + r"(?:一|1)?\s*(?:个|块)?\s*([\u4e00-\u9fffa-zA-Z_ ]+)"
)
_RANGE = re.compile(
    r"在\s*[（(]?\s*"
    + _COORD
    + r"\s*[)）]?\s*到\s*[（(]?\s*"
    + _COORD
    + r"\s*[)）]?\s*(?:放|摆|放置|建|搭建)\s*"
    + r"(\d+|[一二两三四五六七八九十])?\s*(?:个|块)?\s*([\u4e00-\u9fffa-zA-Z_ ]+)"
)


_CN_NUMBERS = {
    "一": 1,
    "二": 2,
    "两": 2,
    "三": 3,
    "四": 4,
    "五": 5,
    "六": 6,
    "七": 7,
    "八": 8,
    "九": 9,
    "十": 10,
}


def _count_value(text: str) -> int | None:
    token = str(text or "").strip()
    if not token:
        return None
    if token.isdigit():
        return int(token)
    return _CN_NUMBERS.get(token)


def _item_from_text(text: str) -> str:
    lowered = text.strip().lower()
    for alias, item in BUILD_ITEM_ALIASES:
        if lowered == alias or lowered.endswith(alias):
            return item
    return ""


def _cells_for_range(
    start: tuple[int, int, int], end: tuple[int, int, int]
) -> tuple[BuildCell, ...] | str:
    deltas = [end[i] - start[i] for i in range(3)]
    changed = [i for i, delta in enumerate(deltas) if delta != 0]
    if len(changed) > 1:
        return "region_not_axis_aligned"
    if not changed:
        return (BuildCell(*start, face="up"),)
    axis = changed[0]
    step = 1 if deltas[axis] > 0 else -1
    distance = abs(deltas[axis]) + 1
    if distance > MAX_BUILD_CELLS:
        return "too_many_cells"
    cells: list[BuildCell] = []
    for offset in range(distance):
        point = list(start)
        point[axis] += step * offset
        cells.append(BuildCell(x=point[0], y=point[1], z=point[2], face="up"))
    return tuple(cells)


def parse_build_request(text: str) -> BuildRequest | None:
    """Parse a small explicit build request; None means this is not a build request."""
    message = str(text or "").strip()
    if not message:
        return None
    match = _RANGE.search(message)
    if match:
        groups = match.groups()
        start = (int(groups[0]), int(groups[1]), int(groups[2]))
        end = (int(groups[3]), int(groups[4]), int(groups[5]))
        item = _item_from_text(groups[7] if len(groups) > 7 else "")
        if not item:
            return BuildRequest(problem="unsupported_item", detail=str(groups[-1]).strip())
        cells = _cells_for_range(start, end)
        if isinstance(cells, str):
            return BuildRequest(item=item, problem=cells, detail=f"{start}->{end}")
        count_text = groups[6] if len(groups) > 6 else ""
        requested_count = _count_value(count_text)
        if requested_count is not None and requested_count != len(cells):
            return BuildRequest(
                item=item, problem="count_mismatch", detail=f"{count_text}!={len(cells)}"
            )
        return BuildRequest(item=item, cells=cells)
    match = _SINGLE.search(message)
    if not match:
        return None
    groups = match.groups()
    start = (int(groups[0]), int(groups[1]), int(groups[2]))
    item = _item_from_text(groups[3] if len(groups) > 3 else "")
    if not item:
        return BuildRequest(problem="unsupported_item", detail=str(groups[-1]).strip())
    return BuildRequest(item=item, cells=(BuildCell(*start, face="up"),))


def _canonical(value: Any) -> str:
    return canonical_item_name(value) if value else ""


def _is_air(name: Any) -> bool:
    return _canonical(name) in {"air", "cave_air", "void_air"}


def _cell_distance(probe: Mapping[str, Any], cell: BuildCell) -> float | None:
    distance = probe.get("distance")
    if isinstance(distance, Mapping):
        raw = distance.get("raw")
        goal = distance.get("goal_near")
        value = raw if isinstance(raw, int | float) else goal
        if isinstance(value, int | float) and math.isfinite(float(value)):
            return float(value)
    return None


async def plan_build_task(objective: str, request: BuildRequest, *, observe: Any) -> PlannedTask:
    """Build a deterministic multi-step placement plan from a parsed request."""
    if request.problem:
        raise ObservationFailed(f"build.{request.problem}")
    if not request.cells:
        raise ObservationFailed("build.no_targets")
    if request.count > MAX_BUILD_CELLS:
        raise ObservationFailed("build.too_many_cells")
    if request.item not in SUPPORTED_BUILD_ITEMS:
        raise ObservationFailed("build.unsupported_item")

    observations: list[Observation] = []
    world = await _safe_observe(observe, "minecraft_world", {})
    world_result = _result_of(world)
    observations.append(
        Observation(
            tool="minecraft_world",
            arguments={},
            result=world_result,
            summary=getattr(world, "summary", ""),
        )
    )
    self_position = _self_position(world_result)
    if not all(key in self_position for key in ("x", "y", "z")):
        raise ObservationFailed("build.position_unknown")

    region_min, region_max = request.region()
    if any(
        region_max[axis] - region_min[axis] > BUILD_REGION_RADIUS * 2 for axis in ("x", "y", "z")
    ):
        raise ObservationFailed("build.region_too_large")

    inventory = await _safe_observe(observe, "minecraft_inventory", {})
    inventory_result = _result_of(inventory)
    observations.append(
        Observation(
            tool="minecraft_inventory",
            arguments={},
            result=inventory_result,
            summary=getattr(inventory, "summary", ""),
        )
    )
    held = inventory_result.get("held_item")
    held_name = _canonical(held.get("name")) if isinstance(held, Mapping) else ""
    held_count = int(held.get("count") or 0) if isinstance(held, Mapping) else 0
    item_name = _canonical(request.item)
    if held_name != item_name or held_count < request.count:
        raise ObservationFailed("build.material_missing_or_insufficient")

    center = {axis: math.floor(float(self_position[axis])) for axis in ("x", "y", "z")}
    for cell in request.cells:
        if any(
            abs(cell_value - center[axis]) > BUILD_REGION_RADIUS
            for axis, cell_value in (("x", cell.x), ("y", cell.y), ("z", cell.z))
        ):
            raise ObservationFailed("build.cell_outside_region")
        target = await _safe_observe(
            observe, "minecraft_dig_capability", {"x": cell.x, "y": cell.y, "z": cell.z}
        )
        target_result = _result_of(target)
        observations.append(
            Observation(
                tool="minecraft_dig_capability",
                arguments={"x": cell.x, "y": cell.y, "z": cell.z},
                result=target_result,
                summary=getattr(target, "summary", ""),
            )
        )
        if not _is_air((target_result.get("block") or {}).get("name")):
            raise ObservationFailed("build.target_occupied")
        distance = _cell_distance(target_result, cell)
        if distance is not None and distance > BUILD_MAX_DISTANCE:
            raise ObservationFailed("build.cell_too_far")
        vector = FACE_VECTORS[cell.face]
        reference = (cell.x - vector[0], cell.y - vector[1], cell.z - vector[2])
        probe = await _safe_observe(
            observe,
            "minecraft_dig_capability",
            {"x": reference[0], "y": reference[1], "z": reference[2]},
        )
        probe_result = _result_of(probe)
        observations.append(
            Observation(
                tool="minecraft_dig_capability",
                arguments={"x": reference[0], "y": reference[1], "z": reference[2]},
                result=probe_result,
                summary=getattr(probe, "summary", ""),
            )
        )
        if _is_air((probe_result.get("block") or {}).get("name")):
            raise ObservationFailed("build.reference_missing")

    steps: list[TaskStep] = []
    for index, cell in enumerate(request.cells, start=1):
        steps.append(
            TaskStep(
                step_id=f"step_{index}",
                tool="minecraft_place",
                risk="MEDIUM",
                arguments={
                    "x": cell.x,
                    "y": cell.y,
                    "z": cell.z,
                    "face": cell.face,
                    "expected_item": request.item,
                },
            )
        )
    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=ExpectedFinalState(),
        observations=[item.to_payload() for item in observations],
    )
    return PlannedTask(objective=objective, plan=plan, observations=observations)
