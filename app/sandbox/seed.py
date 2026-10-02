"""SandboxSeed: the world seed becomes runtime state (v2.0 §117-§122).

Every ``build_*`` function takes a :class:`CharacterWorldSeed` — nothing in
this module holds character data anymore (§27). What remains here are
*system-level templates*: generic object/action knowledge (an action has
durations; a fridge stores food) whose character-specific values — names,
items, spaces, ownership — arrive exclusively through the seed. Placeholders
(``{pet}``/``{drink}``/...) are resolved against the seed's anchors.

Randomness is only allowed on non-canonical details and is seeded (§122/§123).
"""

from __future__ import annotations

import random
from typing import Any

from app.sandbox.models import (
    ActionDefinition,
    Inventory,
    NeedState,
    NeedThresholds,
    SocialSpace,
    SpaceKind,
    SpaceNode,
    WorldObjectItem,
)
from app.sandbox.world_seed import CharacterWorldSeed

DEFAULT_NEEDS: list[tuple[str, float, float]] = [
    # key, initial level, growth per hour — generic physiology, not character
    ("hunger", 0.35, 0.09),
    ("thirst", 0.30, 0.12),
    ("sleepiness", 0.20, 0.055),
    ("energy", 0.30, 0.06),  # low energy pressure grows while awake
    ("hygiene", 0.20, 0.02),
    ("social_need", 0.35, 0.10),
    ("entertainment", 0.30, 0.11),
    ("pet_care", 0.10, 0.05),
    ("household_maintenance", 0.10, 0.03),
    ("work_need", 0.05, 0.01),
    ("project_progress", 0.10, 0.02),
]


def build_spaces(seed: CharacterWorldSeed) -> list[SpaceNode]:
    """Seed spaces → SpaceNodes (§27: the bible's tree becomes real state)."""
    valid_kinds = {kind.value for kind in SpaceKind}
    nodes: list[SpaceNode] = []
    for space in seed.spaces:
        tags = [space.id, space.name]
        if space.parent:
            tags.append(space.parent)
        tags.extend(space.objects)
        deduped: list[str] = []
        for tag in tags:
            if tag and tag not in deduped:
                deduped.append(tag)
        nodes.append(
            SpaceNode(
                id=space.id,
                name=space.name,
                kind=SpaceKind(space.kind) if space.kind in valid_kinds else SpaceKind.room,
                parent_id=space.parent,
                connects=list(space.connections),
                objects=list(space.objects),
                private=space.kind not in ("outdoor", "shop", "transit"),
                tags=deduped,
            )
        )
    return nodes


def build_objects(seed: CharacterWorldSeed) -> list[WorldObjectItem]:
    """Seed objects → WorldObjectItems (tags already compiled in the seed)."""
    return [
        WorldObjectItem(
            id=obj.id,
            name=obj.name,
            space_id=obj.space,
            kind=obj.kind,
            interactions=list(obj.interactions),
            state=dict(obj.state),
            inventory_key=obj.inventory_key,
            tags=list(obj.tags),
        )
        for obj in seed.objects
    ]


def build_inventories(seed: CharacterWorldSeed) -> dict[str, Inventory]:
    """§16/§17: inventories come *only* from the seed — no code defaults."""
    return {key: Inventory(key=key, items=dict(items)) for key, items in seed.inventories.items()}


def build_needs(seed: CharacterWorldSeed) -> dict[str, NeedState]:
    """Generic need physics; character flavor lives in labels (seed.need_labels)."""
    needs: dict[str, NeedState] = {}
    for key, level, growth in DEFAULT_NEEDS:
        thresholds = NeedThresholds()
        if key in ("social_need", "entertainment"):
            thresholds = NeedThresholds(soft=0.45, strong=0.65, critical=0.85)
        if key in ("pet_care", "household_maintenance"):
            thresholds = NeedThresholds(soft=0.55, strong=0.75, critical=0.9)
        needs[key] = NeedState(
            key=key,
            level=level,
            growth_per_hour=growth,
            thresholds=thresholds,
        )
    return needs


def build_action_definitions(seed: CharacterWorldSeed) -> dict[str, ActionDefinition]:
    """Seed-resolved action payloads → ActionDefinitions (§20/§21)."""
    return {payload["id"]: ActionDefinition.model_validate(payload) for payload in seed.actions}


def build_social_spaces(seed: CharacterWorldSeed) -> dict[str, SocialSpace]:
    """Seed social spaces → SocialSpace entities (§24/§25)."""
    return {payload["id"]: SocialSpace.model_validate(payload) for payload in seed.social_spaces}


def build_projects(seed: CharacterWorldSeed) -> dict[str, dict[str, Any]]:
    """§22/§23: seed projects (from the bible) → runtime project dicts."""
    return {
        project_id: {
            "name": payload.get("name", project_id),
            "progress": float(payload.get("progress", 0.0)),
            "next_action": str(payload.get("next_action", "")),
            "status": str(payload.get("status", "active")),
            "started_at": float(payload.get("started_at", 0.0)),
        }
        for project_id, payload in seed.projects.items()
    }


def seeded_rng(seed: int) -> random.Random:
    """§123: simulation randomness always runs on an explicit seed."""
    return random.Random(seed)
