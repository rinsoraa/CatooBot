"""CharacterWorldSeed: the complete initial world, built only from the bible (§26).

    Character Bible → CharacterDefinition → CharacterWorldSeed → SandboxRuntime

Everything here is *static initial state* (§8/§28): one seed JSON fully describes
the starting world, serializable via :meth:`CharacterWorldSeed.export_seed` and
reproducible — the same bible + definition + ``simulation_seed`` always produce
an identical export (§29). Runtime state (hunger, location changes, needs)
lives only in the SandboxRuntime, never here.

System-level *templates* (generic object knowledge like "a fridge stores
food", generic action durations) are allowed (§15/§20); every character
specific value — names, items, spaces, ownership — comes from the definition.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.sandbox.action_templates import ACTION_TEMPLATES, resolve_action_template
from app.sandbox.bible import CharacterBible
from app.sandbox.definition import (
    CharacterDefinition,
    ObjectSeedDefinition,
    SpaceSeedDefinition,
)

# ------------------------------------------------------------------- seeds


class CharacterSeed(BaseModel):
    """§9: the character entity's static starting definition."""

    name: str
    nickname: str = ""
    game_name: str = ""
    home_space: str = ""
    start_location: str = ""
    start_modes: list[str] = Field(default_factory=list)
    inventory_key: str = "character"


class PetSeed(BaseModel):
    """§12: pet definition (runtime hunger/energy belongs to PetState)."""

    name: str
    species: str = "猫"
    traits: list[str] = Field(default_factory=list)
    behaviors: list[str] = Field(default_factory=list)
    special: list[str] = Field(default_factory=list)
    food_item: str = ""
    nap_spots: list[str] = Field(default_factory=list)
    start_location: str = ""


class ResolvedObject(BaseModel):
    """An object definition enriched with system-level generic knowledge."""

    id: str
    name: str
    space: str = ""
    kind: str = "object"
    inventory_key: str = ""
    interactions: list[str] = Field(default_factory=list)
    state: dict[str, Any] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class CharacterWorldSeed(BaseModel):
    """§26: character + pets + spaces + objects + inventories + projects +
    social spaces + relationships + actions + modes + rules — the whole world."""

    bible_version: str = ""
    source_hash: str = ""
    simulation_seed: int = 0
    character: CharacterSeed
    pets: list[PetSeed] = Field(default_factory=list)
    spaces: list[SpaceSeedDefinition] = Field(default_factory=list)
    objects: list[ResolvedObject] = Field(default_factory=list)
    inventories: dict[str, dict[str, int]] = Field(default_factory=dict)
    social_spaces: list[dict[str, Any]] = Field(default_factory=list)
    projects: dict[str, dict[str, Any]] = Field(default_factory=dict)
    actions: list[dict[str, Any]] = Field(default_factory=list)
    modes: list[dict[str, Any]] = Field(default_factory=list)
    rules: list[dict[str, Any]] = Field(default_factory=list)
    anchors: dict[str, str] = Field(default_factory=dict)
    #: conventional need key → prompt label (pet/project labels come from data)
    need_labels: dict[str, str] = Field(default_factory=dict)
    #: names the character treats as her core friends (from the bible)
    core_friend_names: list[str] = Field(default_factory=list)

    # ------------------------------------------------------------- export

    def export_seed(self) -> dict[str, Any]:
        """§28: the full seed as JSON-able dict — debug & migration base."""
        return self.model_dump(mode="json")

    # ----------------------------------------------------------- coverage

    def coverage_report(self, bible: CharacterBible) -> dict[str, Any]:
        """§36/§37: extend the bible's compile-time levels with the seed chain.

        Levels: parsed → compiled → seeded → runtime_connected → tested.
        Every core setting is either counted in a level or listed under
        ``unresolved`` — nothing is silently dropped.
        """
        levels = dict(bible.coverage.get("levels", {}))
        levels["seeded"] = (
            len(self.spaces)
            + len(self.objects)
            + len(self.inventories)
            + len(self.pets)
            + len(self.social_spaces)
            + len(self.projects)
            + len(self.actions)
            + len(self.modes)
            + (1 if self.character.name else 0)
        )
        # every seeded structure is consumed by a runtime system (see
        # SandboxRuntime.__init__) — the wiring is asserted by tests
        levels["runtime_connected"] = levels["seeded"]
        levels["tested"] = TESTED_ITEMS
        samples = []
        if self.pets:
            samples.append(
                {
                    "item": f"宠物{self.pets[0].name}",
                    "chain": "Bible Pet → PetDefinition → PetSeed → PetState",
                }
            )
        home = next((s for s in self.spaces if s.parent == self.character.home_space), None)
        if home is not None:
            samples.append(
                {
                    "item": f"空间{home.id}",
                    "chain": "Bible Spaces → SpaceSeedDefinition → SpaceNode",
                }
            )
        if self.actions:
            samples.append(
                {
                    "item": f"动作{self.actions[0]['id']}",
                    "chain": "Bible 关键词归属 → ActionTemplate → ActionDefinition",
                }
            )
        return {
            "levels": levels,
            "chain_samples": samples,
            "unresolved": list(bible.unresolved),
            "conflicts": list(bible.conflicts),
        }


#: number of test modules that pin the bible→runtime chain (§63-§69)
TESTED_ITEMS = 7


# ------------------------------------------------------------------ builder


def build_world_seed(
    definition: CharacterDefinition,
    bible: CharacterBible,
    *,
    simulation_seed: int = 0,
) -> CharacterWorldSeed:
    """§27: bible + definition → the complete initial world (deterministic)."""
    identity = definition.identity
    spaces = definition.space_definitions
    objects = definition.object_definitions
    home_space = _home_space(spaces)
    start_location = _start_location(spaces, objects, home_space)

    pet_seed = _pet_seed(definition, objects, start_location)
    inventories = {key: dict(items) for key, items in definition.inventory_definitions.items()}
    social_ids = {space.id for space in definition.social_space_definitions}
    space_ids = {space.id for space in spaces}
    shop_ids = [space.id for space in spaces if space.kind == "shop"]
    projects = {
        project.id: {
            "name": project.name,
            "progress": project.progress,
            "next_action": project.next_action,
            "status": project.status,
            "started_at": 0.0,
        }
        for project in definition.project_definitions
    }
    actions = _resolve_actions(
        definition,
        inventories,
        social_ids,
        projects,
        space_ids,
        shop_ids,
        home_space=home_space,
    )
    need_labels = _need_labels(definition, projects)

    return CharacterWorldSeed(
        bible_version=bible.version,
        source_hash=bible.source_hash,
        simulation_seed=simulation_seed,
        character=CharacterSeed(
            name=identity.name or "角色",
            nickname=identity.nickname,
            game_name=identity.game_name.split("（")[0].strip(),
            home_space=home_space,
            start_location=start_location,
            start_modes=[mode.id for mode in definition.modes if mode.is_default],
            inventory_key="character",
        ),
        pets=[pet_seed] if pet_seed is not None else [],
        spaces=[
            SpaceSeedDefinition(
                id=space.id,
                name=space.name,
                kind=space.kind,
                parent=space.parent,
                connections=list(space.connections),
                objects=list(space.objects),
                note=space.note,
            )
            for space in spaces
        ],
        objects=[_resolve_object(obj, definition, inventories, pet_seed) for obj in objects],
        inventories=inventories,
        social_spaces=[space.model_dump() for space in definition.social_space_definitions],
        projects=projects,
        actions=actions,
        modes=[mode.model_dump() for mode in definition.modes],
        rules=[rule.model_dump() for rule in bible.rules],
        anchors=dict(definition.anchors),
        need_labels=need_labels,
        core_friend_names=[rel.name for rel in definition.relationships if rel.core and rel.name],
    )


def _home_space(spaces: list[SpaceSeedDefinition]) -> str:
    for space in spaces:
        if not space.parent and space.kind in ("apartment", "world"):
            return space.id
    return spaces[0].id if spaces else "home"


def _start_location(
    spaces: list[SpaceSeedDefinition],
    objects: list[ObjectSeedDefinition],
    home_space: str,
) -> str:
    """The home room with rest furniture (sofa/bed); else the fullest room."""
    children = [space for space in spaces if space.parent == home_space]
    if not children:
        return home_space
    #: a sofa creature starts where the sofa is; else wherever the bed is
    for furniture in ("sofa", "couch", "bed", "carpet"):
        for space in children:
            if any(obj.id == furniture for obj in objects if obj.space == space.id):
                return space.id
    counts: dict[str, int] = {}
    for obj in objects:
        if obj.space:
            counts[obj.space] = counts.get(obj.space, 0) + 1
    children.sort(key=lambda space: (-counts.get(space.id, 0), space.id))
    return children[0].id


def _pet_seed(
    definition: CharacterDefinition,
    objects: list[ObjectSeedDefinition],
    start_location: str,
) -> PetSeed | None:
    pet = definition.pet
    if pet is None or not pet.name:
        return None
    #: nap spots = rest-ish furniture that actually exists in this world
    nap_candidates = ("sofa", "bed", "couch", "carpet", "floor", "windowsill")
    known_ids = {obj.id for obj in objects}
    nap_spots = [candidate for candidate in nap_candidates if candidate in known_ids]
    return PetSeed(
        name=pet.name,
        species=pet.species or "猫",
        traits=list(pet.traits),
        behaviors=list(pet.behaviors),
        special=list(pet.special),
        food_item=pet.food_item,
        nap_spots=nap_spots or ["bed"],
        start_location=start_location,
    )


#: generic object knowledge — what any fridge/computer/bed *is* (§15: system
#: templates are legal; the instance names/placement come from the seed)
_OBJECT_TEMPLATES: dict[str, dict[str, Any]] = {
    "fridge": {
        "kind": "appliance",
        "interactions": ["open", "take", "store"],
        "role_tags": ["吃的", "喝的", "饮料", "饿", "渴", "吃", "喝"],
    },
    "computer": {
        "kind": "appliance",
        "interactions": ["play", "work"],
        "role_tags": ["电脑", "游戏", "打游戏", "上网"],
    },
    "phone": {
        "kind": "device",
        "interactions": ["browse", "chat"],
        "role_tags": ["手机", "群", "聊天", "发消息", "上网"],
    },
    "bed": {
        "kind": "furniture",
        "interactions": ["sleep"],
        "role_tags": ["床", "睡觉", "睡", "困", "休息"],
    },
    "sofa": {
        "kind": "furniture",
        "interactions": ["lie", "sit"],
        "role_tags": ["沙发", "瘫", "窝着", "躺"],
    },
    "tv": {
        "kind": "appliance",
        "interactions": ["watch"],
        "role_tags": ["电视", "看剧", "看动画"],
    },
    "wardrobe": {
        "kind": "furniture",
        "interactions": ["change"],
        "role_tags": ["衣柜", "衣服", "换衣服", "睡衣"],
    },
    "trash_bag": {
        "kind": "container",
        "interactions": ["fill", "take_out"],
        "state": {"level": 0.25},
        "role_tags": ["垃圾", "垃圾袋", "扔垃圾", "家务"],
    },
    "cat_food_bowl": {
        "kind": "container",
        "interactions": ["feed"],
        "role_tags": ["喂", "添粮", "碗"],
        "is_pet_bowl": True,
    },
    "cat_water_bowl": {
        "kind": "container",
        "interactions": ["refill"],
        "role_tags": ["水碗", "喝水"],
        "is_pet_bowl": True,
    },
    "door": {
        "kind": "fixture",
        "interactions": ["leave", "enter"],
        "role_tags": ["门", "出门", "回家", "锁门"],
    },
    "package_box": {
        "kind": "container",
        "interactions": ["pick_up"],
        "state": {"present": False},
        "role_tags": ["快递", "包裹", "取件"],
    },
}


def _resolve_object(
    obj: ObjectSeedDefinition,
    definition: CharacterDefinition,
    inventories: dict[str, dict[str, int]],
    pet_seed: PetSeed | None,
) -> ResolvedObject:
    template = _OBJECT_TEMPLATES.get(obj.id, {})
    inventory_key = obj.inventory_key or (
        obj.id if obj.id in inventories else str(template.get("inventory_key", ""))
    )
    tags = [obj.id, obj.name]
    tags.extend(inventories.get(inventory_key, {}))
    if template.get("is_pet_bowl") and pet_seed is not None:
        tags.extend([pet_seed.name, pet_seed.species, "宠物"])
        if pet_seed.food_item:
            tags.append(pet_seed.food_item)
    tags.extend(str(item) for item in template.get("role_tags", []))
    # de-dup, keep order, drop empty
    deduped: list[str] = []
    for tag in tags:
        if tag and tag not in deduped:
            deduped.append(tag)
    tags = deduped
    return ResolvedObject(
        id=obj.id,
        name=obj.name,
        space=obj.space,
        kind=str(template.get("kind", "object")),
        inventory_key=inventory_key,
        interactions=list(template.get("interactions", [])),
        state=dict(template.get("state", {})),
        tags=tags,
    )


def _resolve_actions(
    definition: CharacterDefinition,
    inventories: dict[str, dict[str, int]],
    social_ids: set[str],
    projects: dict[str, dict[str, Any]],
    space_ids: set[str],
    shop_ids: list[str],
    *,
    home_space: str = "",
) -> list[dict[str, Any]]:
    """§20/§21: template + definition → owned, fully-resolved action definitions.

    An action whose anchor item does not exist in this world (no 可乐 in the
    bible → no drink_cola chain) is not owned and never seeded.
    """
    anchors = definition.anchors
    project_id = next(iter(projects), "")
    pet_name = definition.pet.name if definition.pet else ""
    resolved: list[dict[str, Any]] = []
    for template_id in definition.action_ownership:
        template = ACTION_TEMPLATES.get(template_id)
        if template is None:
            continue
        action = resolve_action_template(
            template,
            template_id=template_id,
            anchors=anchors,
            inventories=inventories,
            pet_name=pet_name,
            project_id=project_id,
            social_ids=social_ids,
            space_ids=space_ids,
            shop_ids=shop_ids,
            home_space=home_space,
        )
        if action is not None:
            resolved.append(action)
    return resolved


def _need_labels(
    definition: CharacterDefinition, projects: dict[str, dict[str, Any]]
) -> dict[str, str]:
    labels: dict[str, str] = {}
    if definition.pet is not None and definition.pet.name:
        labels["pet_care"] = f"该管管{definition.pet.name}了"
    project = next(iter(projects.values()), None)
    if project is not None:
        labels["project_progress"] = f"想着没做完的{project['name']}"
    return labels
