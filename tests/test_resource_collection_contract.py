"""R3 counterexamples: planner admission only, never real-server evidence."""

import time
from types import SimpleNamespace

import pytest

from app.tasks.planner import ObservationFailed, plan_resource_task
from app.tasks.validation import resolve_arguments


class ResourceObservations:
    def __init__(self, block="stone", *, can_harvest=True, age=0.1, remaining=59):
        self.block = block
        self.can_harvest = can_harvest
        self.age = age
        self.remaining = remaining
        self.calls = []

    async def __call__(self, tool, arguments):
        self.calls.append((tool, dict(arguments)))
        position = {"x": 2, "y": 64, "z": 1}
        tool_name = "wooden_pickaxe" if self.can_harvest else "wooden_shovel"
        if tool == "minecraft_find_blocks":
            result = {
                "ok": True,
                "matches": [{"block": {"name": self.block}, "position": position}],
                "observation": {
                    "coverage": "COMPLETE",
                    "dimension": "minecraft:overworld",
                    "captured_at": time.time() - self.age,
                    "age_seconds": self.age,
                },
            }
        elif tool == "minecraft_world":
            result = {
                "online": True,
                "available": True,
                "dimension": "minecraft:overworld",
                "position": {"x": 1.0, "y": 64.0, "z": 1.0},
                "age_seconds": self.age,
            }
        elif tool == "minecraft_inventory":
            held = {"name": tool_name, "count": 1, "durability_remaining": 59}
            result = {"ok": True, "online": True, "held_item": held, "items": [held]}
        elif tool == "minecraft_dig_capability":
            result = {
                "ok": True,
                "position": position,
                "block": {"name": self.block},
                "held_item": {"name": tool_name, "count": 1},
                "can_dig": True,
                "dig_time_ms": 1000,
                "reason": None,
                "safety": {"known": True, "allowed": True},
                "harvest": {
                    "known": True,
                    "can_harvest": self.can_harvest,
                    "tool": {
                        "known": True,
                        "name": tool_name,
                        "damageable": True,
                        "durability_known": True,
                        "remaining_uses": self.remaining,
                        "enchantments_known": True,
                        "has_enchantments": False,
                        "components_known": True,
                        "unsupported_components": [],
                    },
                },
            }
        else:
            raise AssertionError("unexpected observation: " + tool)
        return SimpleNamespace(ok=True, result=result, summary="controlled unit observation")


async def test_unknown_resource_never_becomes_a_dig_plan():
    observe = ResourceObservations("bedrock")
    with pytest.raises(ObservationFailed, match="unsupported|不支持"):
        await plan_resource_task("挖基岩并捡回来", observe=observe, block_name="bedrock")
    assert not observe.calls


async def test_breakable_stone_with_nonharvesting_tool_is_rejected():
    observe = ResourceObservations(can_harvest=False)
    with pytest.raises(ObservationFailed, match="harvest|tool|采收|工具"):
        await plan_resource_task(
            "挖一块石头并捡回来", observe=observe, block_name="stone", drop_item="cobblestone"
        )


async def test_caller_cannot_invent_a_resource_drop_mapping():
    observe = ResourceObservations()
    with pytest.raises(ObservationFailed, match="drop|掉落"):
        await plan_resource_task(
            "挖一块石头", observe=observe, block_name="stone", drop_item="diamond"
        )
    assert not observe.calls


async def test_stale_resource_observation_does_not_freeze_an_executable_plan():
    observe = ResourceObservations(age=60)
    with pytest.raises(ObservationFailed, match="stale|过期|新鲜"):
        await plan_resource_task(
            "挖一块石头并捡回来", observe=observe, block_name="stone", drop_item="cobblestone"
        )


@pytest.mark.parametrize("remaining", [0, None])
async def test_exhausted_or_unknown_tool_durability_never_becomes_a_plan(remaining):
    with pytest.raises(ObservationFailed, match="durability"):
        await plan_resource_task(
            "挖一块石头并捡回来",
            observe=ResourceObservations(remaining=remaining),
            block_name="stone",
        )


async def test_supported_stone_uses_declared_drop_and_freezes_the_observed_tool():
    planned = await plan_resource_task(
        "挖一块石头并捡回来", observe=ResourceObservations(), block_name="stone"
    )
    assert planned.plan.expected_final_state.inventory_delta == {"cobblestone": 1}
    dig = next(step for step in planned.plan.steps if step.tool == "minecraft_dig")
    assert dig.arguments.get("expected_tool") == "wooden_pickaxe"


async def test_collection_selects_this_break_even_with_an_older_nearby_identical_drop():
    planned = await plan_resource_task(
        "挖一块石头", observe=ResourceObservations(), block_name="stone"
    )
    dig = next(step for step in planned.plan.steps if step.tool == "minecraft_dig")
    scan = next(step for step in planned.plan.steps if step.tool == "minecraft_dropped_items")
    pickup = next(step for step in planned.plan.steps if step.tool == "minecraft_pickup_item")
    results = {
        dig.step_id: {
            "attribution": {
                "action_id": "this-break",
                "server_collection": {
                    "drops": [
                        {"entity_id": 101, "uuid": "this-drop", "item": "minecraft:cobblestone"}
                    ]
                },
            }
        },
        scan.step_id: {"items": [{"entity_id": 99, "entity_uuid": "old-drop"}]},
    }
    assert resolve_arguments(pickup, results) == {
        "entity_id": 101,
        "entity_uuid": "this-drop",
        "expected_item": "cobblestone",
        "expected_break_action": "this-break",
    }
    results[dig.step_id] = {"block_after": "air"}
    with pytest.raises(KeyError):
        resolve_arguments(pickup, results)


@pytest.mark.parametrize("safety", [None, {"known": False}, {"known": True, "allowed": False}])
async def test_unknown_or_dangerous_local_terrain_never_becomes_an_executable_plan(safety):
    base = ResourceObservations()

    async def observe(tool, arguments):
        response = await base(tool, arguments)
        if tool == "minecraft_dig_capability":
            response.result["safety"] = safety
        return response

    with pytest.raises(ObservationFailed, match="safety"):
        await plan_resource_task("挖一块石头", observe=observe, block_name="stone")
