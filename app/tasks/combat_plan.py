"""R5: deterministic combat request parsing and bounded attack planning.

Planning only observes SAFE facts. Every hit is a separate MEDIUM action and
the final verify step re-reads the live entity list; no mobile chase exists.
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
)

MAX_ATTACK_STEPS = 4
MAX_ATTACK_RANGE = 3.5
MIN_HEALTH = 10.0
MIN_FOOD = 6
SWORD_NAMES = frozenset(
    {
        "wooden_sword",
        "stone_sword",
        "iron_sword",
        "golden_sword",
        "diamond_sword",
        "netherite_sword",
    }
)

#: 白名单别名 → 规范实体类型（最长优先，避免“洞穴蜘蛛”被拆成“蜘蛛”）。
COMBAT_ALIASES: tuple[tuple[str, str], ...] = tuple(
    sorted(
        (
            ("洞穴蜘蛛", "cave_spider"),
            ("僵尸", "zombie"),
            ("骷髅", "skeleton"),
            ("蜘蛛", "spider"),
            ("cave_spider", "cave_spider"),
            ("zombie", "zombie"),
            ("skeleton", "skeleton"),
            ("spider", "spider"),
        ),
        key=lambda item: (-len(item[0]), item[0]),
    )
)
#: 明确拒绝的“战斗”对象：不稳定映射、爆炸物或不应攻击的对象。
COMBAT_FORBIDDEN: tuple[tuple[str, str], ...] = (
    ("苦力怕", "explosive_not_supported"),
    ("爬行者", "explosive_not_supported"),
    ("creeper", "explosive_not_supported"),
    ("玩家", "player_forbidden"),
    ("村民", "villager_forbidden"),
    ("流浪商人", "villager_forbidden"),
    ("宠物", "pet_forbidden"),
    ("猫", "pet_forbidden"),
    ("狗", "pet_forbidden"),
    ("马", "pet_forbidden"),
)
COMBAT_VERB = re.compile(r"(攻击|打|击杀|杀死|干掉|解决|清理)")


@dataclass(frozen=True)
class CombatRequest:
    target_type: str = ""
    problem: str = ""
    detail: str = ""


def parse_combat_request(text: str) -> CombatRequest | None:
    """Parse an explicit bounded combat request; None means not combat."""
    message = str(text or "").strip()
    if not message or not COMBAT_VERB.search(message):
        return None
    lowered = message.lower()
    for alias, problem in COMBAT_FORBIDDEN:
        if alias in lowered:
            return CombatRequest(problem=problem, detail=alias)
    for alias, entity_type in COMBAT_ALIASES:
        if alias in lowered:
            return CombatRequest(target_type=entity_type)
    return CombatRequest(problem="target_required")


def _self_stat(world: Mapping[str, Any], key: str) -> float | None:
    semantic = world.get("semantic")
    self_state = semantic.get("self") if isinstance(semantic, Mapping) else None
    if isinstance(self_state, Mapping):
        value = self_state.get(key)
        if isinstance(value, int | float) and math.isfinite(float(value)):
            return float(value)
    value = world.get(key)
    if isinstance(value, int | float) and math.isfinite(float(value)):
        return float(value)
    return None


def _held_item(inventory: Mapping[str, Any]) -> Mapping[str, Any]:
    held = inventory.get("held_item")
    return held if isinstance(held, Mapping) else {}


async def plan_combat_task(objective: str, request: CombatRequest, *, observe: Any) -> PlannedTask:
    if request.problem:
        raise ObservationFailed(f"combat.{request.problem}")
    if request.target_type not in {item for _, item in COMBAT_ALIASES}:
        raise ObservationFailed("combat.unsupported_target")

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
    candidates: list[Mapping[str, Any]] = []
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("type") != request.target_type:
                continue
            if row.get("is_player") is True:
                continue
            distance = row.get("distance")
            if not isinstance(distance, int | float) or float(distance) > MAX_ATTACK_RANGE:
                continue
            candidates.append(row)
    if not candidates:
        raise ObservationFailed("combat.no_target")
    target = min(candidates, key=lambda row: float(row.get("distance") or 999))
    entity_id = target.get("entity_id")
    entity_uuid = str(target.get("entity_uuid") or "")
    if not isinstance(entity_id, int) or isinstance(entity_id, bool) or entity_id < 0:
        raise ObservationFailed("combat.target_identity_unknown")
    if not entity_uuid:
        raise ObservationFailed("combat.target_identity_unknown")

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
    health = _self_stat(world_result, "health")
    food = _self_stat(world_result, "food")
    if health is None or health < MIN_HEALTH:
        raise ObservationFailed("combat.low_health")
    if food is not None and food < MIN_FOOD:
        raise ObservationFailed("combat.low_food")

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
    held = _held_item(inventory_result)
    held_name = canonical_item_name(held.get("name")) if held else ""
    if held_name not in SWORD_NAMES:
        raise ObservationFailed("combat.weapon_required")

    target_health = target.get("health")
    hits = MAX_ATTACK_STEPS
    if isinstance(target_health, int | float) and float(target_health) > 0:
        hits = min(MAX_ATTACK_STEPS, max(1, math.ceil(float(target_health) / 6.0)))
    steps: list[TaskStep] = []
    for index in range(hits):
        steps.append(
            TaskStep(
                step_id=f"step_{index + 1}",
                tool="minecraft_attack",
                risk="MEDIUM",
                arguments={
                    "entity_id": entity_id,
                    "expected_type": request.target_type,
                    "expected_uuid": entity_uuid,
                    "max_distance": MAX_ATTACK_RANGE,
                },
            )
        )
    steps.append(
        TaskStep(
            step_id=f"step_{hits + 1}",
            tool="minecraft_combat_verify",
            risk="SAFE",
            arguments={
                "entity_id": entity_id,
                "expected_type": request.target_type,
                "expected_uuid": entity_uuid,
            },
        )
    )
    return PlannedTask(
        objective=objective,
        plan=TaskPlan(objective=objective, steps=steps, expected_final_state=ExpectedFinalState()),
        observations=observations,
    )
