"""R3 resource declarations and read-only admission; never execute or authorize."""

from __future__ import annotations

import math
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from app.tasks.models import canonical_item_name

RESOURCE_OBSERVATION_MAX_AGE_SECONDS = 5.0


class ResourceContractError(ValueError):
    """A required collection fact is unknown or contradicts the frozen contract."""


@dataclass(frozen=True)
class ResourceSpec:
    block: str
    drop: str
    family: str
    max_count: int
    hand_harvestable: bool


_LOGS = ("oak", "spruce", "birch", "jungle", "acacia", "dark_oak")
RESOURCE_SPECS = MappingProxyType(
    {
        **{
            f"{kind}_log": ResourceSpec(f"{kind}_log", f"{kind}_log", "log", 2, True)
            for kind in _LOGS
        },
        "stone": ResourceSpec("stone", "cobblestone", "stone", 1, False),
        "cobblestone": ResourceSpec("cobblestone", "cobblestone", "stone", 1, False),
        "coal_ore": ResourceSpec("coal_ore", "coal", "ore", 1, False),
        "iron_ore": ResourceSpec("iron_ore", "raw_iron", "ore", 1, False),
        "dirt": ResourceSpec("dirt", "dirt", "soil", 1, True),
        "sand": ResourceSpec("sand", "sand", "gravity", 1, True),
    }
)


def resource_spec(block: Any, *, drop_item: Any = None) -> ResourceSpec:
    name = canonical_item_name(block)
    spec = RESOURCE_SPECS.get(name)
    if spec is None:
        raise ResourceContractError(f"resource.unsupported:{name}")
    if drop_item is not None and canonical_item_name(drop_item) != spec.drop:
        raise ResourceContractError(f"resource.drop_mismatch:{name}:{spec.drop}")
    return spec


def require_resource_observation(result: Mapping[str, Any], *, now: float | None = None) -> None:
    observation = result.get("observation")
    if not isinstance(observation, Mapping) or observation.get("coverage") != "COMPLETE":
        raise ResourceContractError("resource.coverage_unknown")
    if not str(observation.get("dimension") or "").strip():
        raise ResourceContractError("resource.dimension_unknown")
    captured = observation.get("captured_at")
    moment = time.time() if now is None else now
    if (
        isinstance(captured, bool)
        or not isinstance(captured, int | float)
        or not math.isfinite(captured)
        or not -0.5 <= moment - captured <= RESOURCE_OBSERVATION_MAX_AGE_SECONDS
    ):
        raise ResourceContractError("resource.observation_stale_or_unknown")
    age = observation.get("age_seconds")
    if age is not None and (
        isinstance(age, bool)
        or not isinstance(age, int | float)
        or not math.isfinite(age)
        or not 0 <= age <= RESOURCE_OBSERVATION_MAX_AGE_SECONDS
    ):
        raise ResourceContractError("resource.observation_stale_or_unknown")


def require_harvest_probe(result: Mapping[str, Any], *, required_uses: int = 1) -> None:
    harvest = result.get("harvest")
    if not isinstance(harvest, Mapping) or harvest.get("known") is not True:
        raise ResourceContractError("resource.harvest_unknown")
    if harvest.get("can_harvest") is not True:
        raise ResourceContractError("resource.tool_cannot_harvest")
    tool = harvest.get("tool")
    if not isinstance(tool, Mapping) or tool.get("known") is not True:
        raise ResourceContractError("resource.tool_facts_unknown")
    held = result.get("held_item")
    held_name = canonical_item_name(held.get("name")) if isinstance(held, Mapping) else ""
    if canonical_item_name(tool.get("name")) != held_name:
        raise ResourceContractError("resource.tool_identity_mismatch")
    if (
        tool.get("enchantments_known") is not True
        or tool.get("has_enchantments") is not False
        or tool.get("components_known") is not True
        or tool.get("unsupported_components") != []
    ):
        raise ResourceContractError("resource.tool_profile_unknown_or_unsupported")
    if tool.get("damageable") is True:
        remaining = tool.get("remaining_uses")
        if (
            tool.get("durability_known") is not True
            or isinstance(remaining, bool)
            or not isinstance(remaining, int)
            or remaining < required_uses
        ):
            raise ResourceContractError("resource.tool_durability_insufficient_or_unknown")
    elif tool.get("damageable") is not False:
        raise ResourceContractError("resource.tool_durability_unknown")
    safety = result.get("safety")
    if not isinstance(safety, Mapping) or safety.get("known") is not True:
        raise ResourceContractError("resource.safety_unknown")
    if safety.get("allowed") is not True:
        raise ResourceContractError("resource.safety_precondition_failed")
