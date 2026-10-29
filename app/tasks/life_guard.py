"""Read-only LIFE admission. An allowed result never grants execution authority."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.initiative.candidates import REST_ENERGY_THRESHOLD
from app.initiative.gate import RECOVERY_GRACE_SECONDS, SOCIAL_FATIGUE_THRESHOLD

LifeGuard = Callable[..., Awaitable[dict[str, Any]]]


async def check_life_guard(
    provider: LifeGuard | None, *, ignore_task_id: str = "", intent_id: str = ""
) -> dict[str, Any]:
    """Bounded, fail-closed hook shared by planning, approval and step dispatch."""
    if provider is None:
        return {"allowed": False, "reason": "life_context_unavailable"}
    try:
        result = await asyncio.wait_for(
            provider(ignore_task_id=ignore_task_id, intent_id=intent_id), timeout=2.0
        )
    except Exception:  # noqa: BLE001 - exception text may contain private context
        return {"allowed": False, "reason": "life_context_unavailable"}
    if not isinstance(result, Mapping) or result.get("allowed") is not True:
        reason = str(result.get("reason", "")) if isinstance(result, Mapping) else ""
        return {
            "allowed": False,
            "reason": reason if reason in LIFE_BLOCK_REASONS else "life_context_unavailable",
        }
    return {"allowed": True, "reason": "life_preconditions_checked"}


LIFE_BLOCK_REASONS = frozenset(
    {
        "life_context_unavailable",
        "life_origin_unavailable",
        "life_intent_expired",
        "life_intent_not_open",
        "role_state_unavailable",
        "role_recovering",
        "role_degraded",
        "role_sleeping",
        "role_quiet_hours",
        "role_low_energy",
        "role_social_fatigue",
        "world_unavailable",
        "task_state_unavailable",
        "active_task_conflict",
    }
)

LIFE_WORLD_NEAR_MAX_AGE_SECONDS = 5.0


def assess_life_facts(
    context: Any, *, energy: Any, world: Any, blockers: list[Any], now: float
) -> dict[str, Any]:
    """Evaluate fresh trusted facts; no model text, tools, stores or authorization."""
    reason = ""
    if (
        isinstance(energy, bool)
        or not isinstance(energy, int | float)
        or not math.isfinite(energy)
        or not 0 <= energy <= 1
    ):
        reason = "role_state_unavailable"
    elif energy < REST_ENERGY_THRESHOLD:
        reason = "role_low_energy"
    elif context is None:
        reason = "life_context_unavailable"
    elif str(getattr(context, "degraded", "") or ""):
        reason = "role_degraded"
    elif getattr(context, "sleeping", False):
        reason = "role_sleeping"
    elif getattr(context, "quiet_hours", False):
        reason = "role_quiet_hours"
    elif getattr(context, "social_fatigue", 0.0) >= SOCIAL_FATIGUE_THRESHOLD:
        reason = "role_social_fatigue"
    elif getattr(context, "recovered_at", 0.0) and (
        now - float(context.recovered_at) < RECOVERY_GRACE_SECONDS
    ):
        reason = "role_recovering"
    elif blockers:
        reason = "active_task_conflict"
    elif (
        not isinstance(world, Mapping)
        or world.get("online") is not True
        or world.get("available") is not True
        or not isinstance(world.get("semantic"), Mapping)
        or not isinstance(world["semantic"].get("self"), Mapping)
        or not world["semantic"]["self"].get("dimension")
        or not isinstance(world.get("layers"), Mapping)
        or not isinstance(world["layers"].get("near"), Mapping)
        or isinstance(world["layers"]["near"].get("age_seconds"), bool)
        or not isinstance(world["layers"]["near"].get("age_seconds"), int | float)
        or not math.isfinite(world["layers"]["near"]["age_seconds"])
        or not 0 <= world["layers"]["near"]["age_seconds"] <= LIFE_WORLD_NEAR_MAX_AGE_SECONDS
    ):
        reason = "world_unavailable"
    return {"allowed": not reason, "reason": reason or "life_preconditions_checked"}
