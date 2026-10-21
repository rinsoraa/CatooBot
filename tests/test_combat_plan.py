"""R5 combat parser/planner tests; observation only, no attack."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.combat_plan import CombatRequest, parse_combat_request, plan_combat_task
from app.tasks.planner import ObservationFailed


def test_parse_zombie_request() -> None:
    request = parse_combat_request("攻击附近的一只僵尸")
    assert request == CombatRequest(target_type="zombie")


def test_cave_spider_is_longest_match() -> None:
    assert parse_combat_request("打洞穴蜘蛛").target_type == "cave_spider"


def test_player_is_forbidden() -> None:
    request = parse_combat_request("攻击那个玩家")
    assert request.problem == "player_forbidden"


def test_creeper_is_explicitly_unsupported() -> None:
    request = parse_combat_request("打苦力怕")
    assert request.problem == "explosive_not_supported"


def test_non_combat_text_is_not_claimed() -> None:
    assert parse_combat_request("今天天气不错") is None
    assert parse_combat_request("采集一块泥土") is None


class CombatObservations:
    def __init__(
        self, *, target=True, health=20.0, food=20.0, held="iron_sword", entity_uuid="uuid-zombie"
    ):
        self.target = target
        self.health = health
        self.food = food
        self.held = held
        self.entity_uuid = entity_uuid
        self.calls: list[str] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(tool)
        if tool == "minecraft_entities":
            rows = []
            if self.target:
                rows.append(
                    {
                        "entity_id": 42,
                        "entity_uuid": self.entity_uuid,
                        "type": "zombie",
                        "distance": 2.0,
                        "health": 20,
                        "is_player": False,
                    }
                )
            return SimpleNamespace(result={"entities": rows})
        if tool == "minecraft_world":
            return SimpleNamespace(
                result={"semantic": {"self": {"health": self.health, "food": self.food}}}
            )
        if tool == "minecraft_inventory":
            return SimpleNamespace(result={"held_item": {"name": self.held, "count": 1}})
        raise AssertionError(tool)


async def test_combat_plan_has_four_hits_and_live_verify() -> None:
    observe = CombatObservations()
    planned = await plan_combat_task(
        "攻击僵尸", CombatRequest(target_type="zombie"), observe=observe
    )
    assert [step.tool for step in planned.plan.steps] == ["minecraft_attack"] * 4 + [
        "minecraft_combat_verify"
    ]
    assert planned.plan.steps[0].arguments["entity_id"] == 42
    assert planned.plan.steps[0].arguments["expected_uuid"] == "uuid-zombie"
    assert planned.plan.steps[-1].risk == "SAFE"


async def test_no_target_is_rejected() -> None:
    with pytest.raises(ObservationFailed, match="no_target"):
        await plan_combat_task(
            "攻击僵尸",
            CombatRequest(target_type="zombie"),
            observe=CombatObservations(target=False),
        )


async def test_low_health_is_rejected() -> None:
    with pytest.raises(ObservationFailed, match="low_health"):
        await plan_combat_task(
            "攻击僵尸", CombatRequest(target_type="zombie"), observe=CombatObservations(health=6)
        )


async def test_missing_sword_is_rejected() -> None:
    with pytest.raises(ObservationFailed, match="weapon_required"):
        await plan_combat_task(
            "攻击僵尸", CombatRequest(target_type="zombie"), observe=CombatObservations(held="dirt")
        )
