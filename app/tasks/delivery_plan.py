"""R6: deterministic player delivery parsing and planning (DROPPED only)."""

from __future__ import annotations

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
)
from app.tasks.validation import collect_references

MAX_DELIVERY_COUNT = 64
MAX_DELIVERY_DISTANCE = 3.5
PLAYER_NAME = re.compile(r"([A-Za-z0-9_]{3,16})")
COUNT_TOKEN = r"(\d+|[一二两三四五六七八九十]+)"
CN_NUMBERS = {
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
ITEM_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("泥土", "minecraft:dirt"),
            ("dirt", "minecraft:dirt"),
            ("铁锭", "minecraft:iron_ingot"),
            ("iron_ingot", "minecraft:iron_ingot"),
            ("面包", "minecraft:bread"),
            ("bread", "minecraft:bread"),
            ("苹果", "minecraft:apple"),
            ("apple", "minecraft:apple"),
            ("圆石", "minecraft:cobblestone"),
            ("cobblestone", "minecraft:cobblestone"),
            ("火把", "minecraft:torch"),
            ("torch", "minecraft:torch"),
            ("橡木原木", "minecraft:oak_log"),
            ("oak_log", "minecraft:oak_log"),
        ),
        key=lambda item: (-len(item[0]), item[0]),
    )
)
PASS1 = re.compile(
    r"给\s*"
    + PLAYER_NAME.pattern
    + r"\s*"
    + COUNT_TOKEN
    + r"\s*(?:个|件|块)?\s*([\u4e00-\u9fffa-zA-Z_]+)"
)
PASS2 = re.compile(
    r"把\s*"
    + COUNT_TOKEN
    + r"\s*(?:个|件|块)?\s*([\u4e00-\u9fffa-zA-Z_]+)\s*给\s*"
    + PLAYER_NAME.pattern
)


@dataclass(frozen=True)
class DeliveryRequest:
    target: str = ""
    item: str = ""
    count: int = 0
    problem: str = ""
    detail: str = ""


def _count_value(text: str) -> int | None:
    token = str(text or "").strip()
    if token.isdigit():
        return int(token)
    total = 0
    for char in token:
        value = CN_NUMBERS.get(char)
        if value is None:
            return None
        total += value
    return total or None


def _item_from_text(text: str) -> str:
    lowered = text.strip().lower()
    for alias, item in ITEM_ALIASES:
        if lowered == alias or lowered.endswith(alias):
            return item
    return ""


def parse_delivery_request(text: str) -> DeliveryRequest | None:
    message = str(text or "").strip()
    if not message:
        return None
    match = PASS1.search(message) or PASS2.search(message)
    if not match:
        return None
    groups = match.groups()
    if match.re is PASS1:
        target, count_text, item_text = groups[0], groups[1], groups[2]
    else:
        count_text, item_text, target = groups[0], groups[1], groups[2]
    count = _count_value(count_text)
    item = _item_from_text(item_text)
    if not item:
        return DeliveryRequest(target=target, problem="unsupported_item", detail=item_text)
    if count is None or count < 1 or count > MAX_DELIVERY_COUNT:
        return DeliveryRequest(target=target, item=item, problem="count_invalid", detail=count_text)
    return DeliveryRequest(target=target, item=item, count=count)


async def plan_delivery_task(
    objective: str, request: DeliveryRequest, *, observe: Any
) -> PlannedTask:
    if request.problem:
        raise ObservationFailed(f"delivery.{request.problem}")
    if not request.target:
        raise ObservationFailed("delivery.target_missing")
    if request.count > MAX_DELIVERY_COUNT:
        raise ObservationFailed("delivery.count_invalid")

    observations: list[Observation] = []
    entities_call = await _safe_observe(observe, "minecraft_entities", {})
    entities_result = _result_of(entities_call)
    observations.append(
        Observation(
            tool="minecraft_entities",
            arguments={},
            result=entities_result,
            summary=getattr(entities_call, "summary", ""),
        )
    )
    rows = entities_result.get("entities")
    target: Mapping[str, Any] | None = None
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping) or row.get("is_player") is not True:
                continue
            username = str(row.get("username") or "")
            if username.lower() != request.target.lower():
                continue
            distance = row.get("distance")
            if isinstance(distance, int | float) and float(distance) <= MAX_DELIVERY_DISTANCE:
                target = row
                break
    if target is None:
        raise ObservationFailed("delivery.target_not_found")
    target_uuid = str(target.get("entity_uuid") or "")
    if not target_uuid:
        raise ObservationFailed("delivery.target_identity_unknown")

    inventory_call = await _safe_observe(observe, "minecraft_inventory", {})
    inventory_result = _result_of(inventory_call)
    observations.append(
        Observation(
            tool="minecraft_inventory",
            arguments={},
            result=inventory_result,
            summary=getattr(inventory_call, "summary", ""),
        )
    )
    held = inventory_result.get("held_item")
    held_name = canonical_item_name(held.get("name")) if isinstance(held, Mapping) else ""
    held_count = int(held.get("count") or 0) if isinstance(held, Mapping) else 0
    item_name = canonical_item_name(request.item)
    if held_name != item_name:
        raise ObservationFailed("delivery.item_not_held")
    if held_count < request.count:
        raise ObservationFailed("delivery.item_insufficient")

    drop_step = TaskStep(
        step_id="step_1",
        tool="minecraft_drop_item",
        risk="MEDIUM",
        arguments={"item": request.item, "count": request.count},
    )
    verify_arguments = {
        "item": request.item,
        "count": request.count,
        "entity_id": {"from_step": "step_1", "path": "entity_id"},
        "expected_uuid": {"from_step": "step_1", "path": "entity_uuid"},
    }
    verify_step = TaskStep(
        step_id="step_2",
        tool="minecraft_delivery_verify",
        risk="SAFE",
        arguments=verify_arguments,
        references=collect_references(verify_arguments),
    )
    return PlannedTask(
        objective=objective,
        plan=TaskPlan(
            objective=objective,
            steps=[drop_step, verify_step],
            expected_final_state=ExpectedFinalState(),
        ),
        observations=observations,
    )
