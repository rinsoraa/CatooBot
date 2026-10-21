"""R6 delivery parsing/planning tests; observation only, no drop."""

from types import SimpleNamespace
from typing import Any

import pytest

from app.tasks.delivery_plan import DeliveryRequest, parse_delivery_request, plan_delivery_task
from app.tasks.planner import ObservationFailed


def test_parse_give_form() -> None:
    request = parse_delivery_request("给 GuanTou 一个泥土")
    assert request == DeliveryRequest(target="GuanTou", item="minecraft:dirt", count=1)


def test_parse_take_form() -> None:
    request = parse_delivery_request("把两块铁锭给 GuanTou")
    assert (
        request.target == "GuanTou"
        and request.item == "minecraft:iron_ingot"
        and request.count == 2
    )


def test_parse_unknown_item() -> None:
    request = parse_delivery_request("给 GuanTou 一个钻石块")
    assert request.problem == "unsupported_item"


def test_non_delivery_text_is_not_claimed() -> None:
    assert parse_delivery_request("攻击僵尸") is None


class DeliveryObservations:
    def __init__(self, *, player=True, held="dirt", count=1, uuid="uuid-guantou"):
        self.player = player
        self.held = held
        self.count = count
        self.uuid = uuid
        self.calls: list[str] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append(tool)
        if tool == "minecraft_entities":
            rows = []
            if self.player:
                rows.append(
                    {
                        "entity_id": 77,
                        "entity_uuid": self.uuid,
                        "type": "player",
                        "username": "GuanTou",
                        "is_player": True,
                        "distance": 2.0,
                    }
                )
            return SimpleNamespace(result={"entities": rows})
        if tool == "minecraft_inventory":
            return SimpleNamespace(result={"held_item": {"name": self.held, "count": self.count}})
        raise AssertionError(tool)


async def test_delivery_plan_has_drop_and_dropped_verify() -> None:
    planned = await plan_delivery_task(
        "给 GuanTou 一个泥土",
        DeliveryRequest(target="GuanTou", item="minecraft:dirt", count=1),
        observe=DeliveryObservations(),
    )
    assert [step.tool for step in planned.plan.steps] == [
        "minecraft_drop_item",
        "minecraft_delivery_verify",
    ]
    assert planned.plan.steps[1].arguments["entity_id"] == {
        "from_step": "step_1",
        "path": "entity_id",
    }


async def test_delivery_target_missing_is_rejected() -> None:
    with pytest.raises(ObservationFailed, match="target_not_found"):
        await plan_delivery_task(
            "给 GuanTou 一个泥土",
            DeliveryRequest(target="GuanTou", item="minecraft:dirt", count=1),
            observe=DeliveryObservations(player=False),
        )


async def test_delivery_item_not_held_is_rejected() -> None:
    with pytest.raises(ObservationFailed, match="item_not_held"):
        await plan_delivery_task(
            "给 GuanTou 一个泥土",
            DeliveryRequest(target="GuanTou", item="minecraft:dirt", count=1),
            observe=DeliveryObservations(held="bread"),
        )
