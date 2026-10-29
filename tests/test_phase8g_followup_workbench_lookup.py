"""Phase 8G Follow-up F3：需要工作台的配方必须**先观察工作台、再用它重查**（真实运行时语义）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.planner import (
    CRAFT_REASON_TABLE,
    ObservationFailed,
    craft_recipe_of,
    plan_craft_task,
)

PLANKS = "oak_planks"
STICK = "stick"

#: 运行时的真实语义：**不带工作台**时 3x3 配方只回 crafting_table_required 且不带配方。
NO_TABLE = {
    "ok": True,
    "item": STICK,
    "status": "crafting_table_required",
    "total": 0,
    "recipes": [],
}
#: 带上真实观察到的工作台之后，才返回真正的 3x3 配方（容器 + recipes 列表）。
TABLE_PAYLOAD = {
    "ok": True,
    "item": STICK,
    "status": "available",
    "total": 1,
    "recipes": [
        {
            "recipe_id": "!stick*4=oak_planks*2",
            "result": {"name": STICK, "count_per_craft": 4},
            "requires_table": True,
            "available": True,
            "ingredients": [{"name": PLANKS, "count": 2}],
        }
    ],
}
PLANKS_PAYLOAD = {
    "ok": True,
    "item": PLANKS,
    "status": "available",
    "total": 1,
    "recipes": [
        {
            "recipe_id": "oak_planks*4=oak_log*1",
            "result": {"name": PLANKS, "count_per_craft": 4},
            "requires_table": False,
            "available": True,
            "ingredients": [{"name": "oak_log", "count": 1}],
        }
    ],
}


class _TableOnlyObserve:
    """脚本化真实读出：只有**带工作台参数**的那次查询才拿得到配方。"""

    def __init__(self, *, workbench: dict[str, Any] | None, inventory: dict[str, int]) -> None:
        self.workbench = workbench
        self.inventory = dict(inventory)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: Any) -> Any:
        args = dict(arguments)
        self.calls.append((tool, args))
        payload: dict[str, Any]
        if tool == "minecraft_inventory":
            payload = {
                "ok": True,
                "items": [{"name": k, "count": v} for k, v in self.inventory.items()],
            }
        elif tool == "minecraft_recipe_lookup":
            item = str(args.get("item") or "")
            if item == STICK:
                payload = dict(TABLE_PAYLOAD if args.get("crafting_table") else NO_TABLE)
            elif item == PLANKS:
                payload = dict(PLANKS_PAYLOAD)
            else:
                payload = {"ok": True, "item": item, "status": "recipe_not_found", "recipes": []}
        elif tool == "minecraft_world":
            if self.workbench is None:
                payload = {}
            else:
                payload = {
                    "available": True,
                    "online": True,
                    "age_seconds": 0.1,
                    "semantic": {
                        "self": {
                            "dimension": "overworld",
                            "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                        }
                    },
                }
        elif tool == "minecraft_find_blocks":
            rows = []
            if self.workbench is not None:
                rows = [
                    {
                        "block": {"name": "crafting_table"},
                        "position": dict(self.workbench),
                    }
                ]
            payload = {"ok": True, "matches": rows}
        else:
            payload = {}
        return type("R", (), {"result": payload, "summary": tool})()


def test_runtime_table_status_maps_to_the_table_reason() -> None:
    """行为回归：运行时用 status=crafting_table_required 表达「需要 3x3」，绝不是「没有配方」。"""
    recipe, reason = craft_recipe_of(dict(NO_TABLE), STICK, allow_table=True)
    assert recipe is None
    assert reason == CRAFT_REASON_TABLE


async def test_table_recipe_is_recalled_with_the_observed_workbench() -> None:
    observe = _TableOnlyObserve(
        workbench={"x": 2, "y": 64, "z": 1}, inventory={PLANKS: 4, STICK: 8}
    )
    planned = await plan_craft_task("在工作台上做梯子", observe=observe, item=STICK, count=4)
    crafts = [step for step in planned.plan.steps if step.tool == "minecraft_craft"]
    assert [step.arguments["recipe_id"] for step in crafts] == ["!stick*4=oak_planks*2"]
    assert crafts[0].arguments["crafting_table"] == {"x": 2, "y": 64, "z": 1}
    lookups = [args for tool, args in observe.calls if tool == "minecraft_recipe_lookup"]
    assert any("crafting_table" in args for args in lookups), "必须用观察到的工作台重查一次"
    assert any("crafting_table" not in args for args in lookups), "第一次必须是不带桌子的真实查询"


async def test_missing_workbench_reports_the_workbench_state_not_no_recipe() -> None:
    observe = _TableOnlyObserve(workbench=None, inventory={PLANKS: 4})
    with pytest.raises(ObservationFailed) as excinfo:
        await plan_craft_task("在工作台上做梯子", observe=observe, item=STICK, count=4)
    message = str(excinfo.value)
    assert message.startswith(CRAFT_REASON_TABLE), message
    assert "no_recipe" not in message
