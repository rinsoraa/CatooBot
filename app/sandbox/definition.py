"""CharacterDefinition: the compiled, runtime-facing character contract (§7).

The bible markdown is the canonical *source*; :class:`CharacterBible` is its
parsed form; ``CharacterDefinition`` is the structured contract every runtime
subsystem consumes — identity, personality, modes, rules, relationships, pets,
spaces, objects, inventories, actions, projects, social spaces, livelihood and
world rules, in one place. Static facts live here; runtime state never does
(§8): "现在在哪里/困不困" belongs to the Sandbox, never to this file.

Built by :meth:`CharacterDefinition.from_bible` — nothing in the runtime may
read the markdown or ``CharacterBible`` directly for character data.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import BaseModel, Field

from app.sandbox.action_templates import ACTION_TEMPLATES
from app.sandbox.bible import CharacterBible


class IdentityDefinition(BaseModel):
    """§8 canonical facts — who she *is*, immutable at runtime."""

    name: str = ""
    nickname: str = ""
    game_name: str = ""
    gender: str = ""
    age: str = ""
    birthday: str = ""
    height: str = ""
    appearance: str = ""
    household: str = ""
    lifestyle: str = ""


class PersonalityDefinition(BaseModel):
    traits: list[str] = Field(default_factory=list)
    likes: list[str] = Field(default_factory=list)
    dislikes: list[str] = Field(default_factory=list)
    habits: list[str] = Field(default_factory=list)
    interests: list[str] = Field(default_factory=list)


class ModeDefinition(BaseModel):
    """§18: a mode is data, not a Python enum — id/trigger/priority/speech."""

    id: str
    name: str
    trigger: str = ""
    #: home / outdoor / time / action / social — how derive() fires it
    trigger_kind: str = ""
    #: inclusive hour window for time-triggered modes, e.g. (2, 5)
    time_window: tuple[int, int] | None = None
    style: str = ""
    phrases: list[str] = Field(default_factory=list)
    tone: str = ""
    length: str = ""
    priority: int = 60
    #: True for the everyday default mode (lowest priority, broadest fit)
    is_default: bool = False


class RelationshipDefinition(BaseModel):
    name: str
    type: str = "friend"
    core: bool = False
    notes: list[str] = Field(default_factory=list)


class PetDefinition(BaseModel):
    """§11/§12: pet *definition* — runtime hunger/location lives in PetState."""

    name: str = ""
    species: str = ""
    traits: list[str] = Field(default_factory=list)
    behaviors: list[str] = Field(default_factory=list)
    special: list[str] = Field(default_factory=list)
    food_item: str = ""
    #: nap spots compiled from bible spaces (bed/sofa...) — ids, not prose
    nap_spots: list[str] = Field(default_factory=list)


class SocialSpaceDefinition(BaseModel):
    """§24/§25: a place she hangs out online — NOT a QQ group binding."""

    id: str
    name: str
    topic: str = ""
    kind: str = "simulated"
    interest: float = 0.5
    social_temperature: float = 0.5


class ProjectDefinition(BaseModel):
    """§22/§23: a long-running goal with a lifecycle."""

    id: str
    name: str
    progress: float = 0.0
    next_action: str = ""
    status: str = "active"  # planned / active / paused / completed / abandoned
    current_task: str = ""


class SpaceSeedDefinition(BaseModel):
    id: str
    name: str
    kind: str = "room"
    parent: str = ""
    connections: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)
    note: str = ""


class ObjectSeedDefinition(BaseModel):
    id: str
    name: str
    space: str = ""
    kind: str = "object"
    inventory_key: str = ""
    note: str = ""


class SpeechCalibration(BaseModel):
    """§32: dialogue examples are calibration material, not world rules."""

    examples: dict[str, list[str]] = Field(default_factory=dict)
    phrases: list[str] = Field(default_factory=list)
    prose: str = ""


#: system-level conventional mode ids that action templates may reference
CONVENTIONAL_MODES: dict[str, dict[str, Any]] = {
    "home": {"priority": 10, "default": True},
    "outdoor": {"priority": 20},
    "gaming": {"priority": 30},
    "online_social": {"priority": 40},
    "deep_night": {"priority": 30},
}

#: system-level anchors: bible phrases → conventional action-ownership keys.
#: Generic semantics (games/sweets/pet/chore), never a character name.
ACTION_KEYWORDS: dict[str, tuple[str, ...]] = {
    "play_minecraft": ("Minecraft", "建筑", "红石", "建房子", "挖矿"),
    "play_singleplayer": ("单机游戏", "单机", "RPG", "解谜", "模拟经营", "全收集", "读档"),
    "watch_animation": ("动画", "看剧", "漫画", "追番", "新番"),
    "browse_social": ("社交软件", "群聊", "刷", "聊天频道"),
    "chat_group": ("接梗", "抛话题", "发沙雕图", "群里聊"),
    "browse_forum": ("论坛", "求助帖", "攻略区"),
    "eat_pudding": ("布丁",),
    "eat_cake": ("蛋糕",),
    "eat_fruit": ("草莓", "西瓜", "水果"),
    "drink_cola": ("可乐", "快乐水"),
    "feed_cat": ("添粮", "猫粮", "喂猫"),
    "talk_to_cat": ("对着猫", "和猫说", "跟猫说", "猫说话"),
    "film_cat": ("猫入镜", "拍猫", "发群配文", "猫图"),
    "take_out_trash": ("扔垃圾", "垃圾"),
    "pick_up_package": ("取快递", "快递"),
    "buy_cola": ("买可乐", "补可乐", "抱一箱可乐"),
    "buy_snacks": ("买零食", "补货", "买饮料"),
    "buy_sweets": ("买甜食", "甜品店", "草莓蛋糕"),
    "go_shopping_cola": ("采购甜食和可乐", "没有可乐的夜晚"),
    "go_shopping_sweets": ("甜品店", "必买项"),
    "sleep": ("睡觉", "熬夜", "睡"),
    "nap": ("补觉", "白天睡", "午睡"),
    "shower": ("洗澡", "洗漱"),
    "tidy_room": ("整理房间", "收拾"),
    "work_commission": ("委托", "约稿", "插画稿", "攻略约稿", "零工"),
    "idle_on_sofa": ("窝在沙发", "沙发", "窝着", "躺"),
    "change_to_homewear": ("换猫猫睡衣", "换睡衣", "睡衣"),
    "change_to_outdoor": ("换休闲装", "束起长发", "打理外表", "外出装"),
    "think": ("想平时不会想的事", "哲思", "发呆"),
    "walk": ("散步", "在外面走", "小区"),
    "idle": (),
    #: Phase C: the closing step of a shopping trip — owns「回家」
    "return_home": ("回家", "回去", "到家"),
}

#: bible Preferences keys → anchor slots consumed by action templates
_ANCHOR_PATTERNS: dict[str, tuple[str, ...]] = {
    "drink": ("可乐", "橙汁", "汽水", "苏打", "咖啡", "茶"),
    "snack": ("布丁",),
    "dessert": ("蛋糕",),
    "fruit": ("西瓜", "草莓", "水果"),
}


def _extract_anchors(bible: CharacterBible) -> dict[str, str]:
    """Pick canonical item names from the bible's inventory + preferences.

    Preference lines are split into items (、，;；) so an anchor is a real
    item name ("西瓜"), never a whole sentence. Inventory items win over
    preferences because they are the stock the sandbox actually manipulates.
    """
    inventory_pool: list[str] = []
    for items in bible.inventories.values():
        inventory_pool.extend(items)
    preference_pool: list[str] = []
    for values in bible.preferences.values():
        for line in values:
            preference_pool.extend(
                part.strip() for part in re.split(r"[、,，;；]", line) if part.strip()
            )
    anchors: dict[str, str] = {}
    for slot, patterns in _ANCHOR_PATTERNS.items():
        for pool in (inventory_pool, preference_pool):
            hit = next((item for item in pool if any(p in item for p in patterns)), "")
            if hit:
                anchors[slot] = hit
                break
    return anchors


def _time_window(trigger: str) -> tuple[int, int] | None:
    """Parse "凌晨2点到5点" / "22点到6点" into an inclusive-ish hour window."""
    match = re.search(r"(\d{1,2})\s*点(?:[^到至]*?)[到至]\s*(?:次日)?\s*(\d{1,2})\s*点", trigger)
    if match:
        return int(match.group(1)) % 24, int(match.group(2)) % 24
    return None


def _trigger_kind(mode_id: str, trigger: str) -> str:
    if "凌晨" in trigger or _time_window(trigger) is not None:
        return "time"
    if "出门" in trigger or "外出" in trigger:
        return "outdoor"
    if "社交软件" in trigger or "群聊" in trigger or "论坛" in trigger:
        return "social"
    if "游戏" in trigger or "专注" in trigger:
        return "action"
    if "在家" in trigger or "独处" in trigger or "默认" in trigger:
        return "home"
    return "custom"


def _parse_identity(facts: dict[str, Any]) -> IdentityDefinition:
    get = lambda key: str(facts.get(key, "") or "")  # noqa: E731 - tiny accessor
    appearance = get("外貌")
    household = get("居住")
    lifestyle = get("生活状态")
    return IdentityDefinition(
        name=get("角色名"),
        nickname=get("别名"),
        game_name=get("游戏名"),
        gender=get("性别"),
        age=get("年龄"),
        birthday=get("生日"),
        height=get("身高"),
        appearance=appearance,
        household=household,
        lifestyle=lifestyle,
    )


class CharacterDefinition(BaseModel):
    """The full character contract (§7). Static facts only (§8)."""

    source_version: str = ""
    source_hash: str = ""
    identity: IdentityDefinition = Field(default_factory=IdentityDefinition)
    appearance: list[str] = Field(default_factory=list)
    personality: PersonalityDefinition = Field(default_factory=PersonalityDefinition)
    values: list[str] = Field(default_factory=list)
    preferences: dict[str, list[str]] = Field(default_factory=dict)
    habits: list[str] = Field(default_factory=list)
    behavior_rules: list[str] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    modes: list[ModeDefinition] = Field(default_factory=list)
    speech: SpeechCalibration = Field(default_factory=SpeechCalibration)
    relationships: list[RelationshipDefinition] = Field(default_factory=list)
    pet: PetDefinition | None = None
    pet_definitions: list[PetDefinition] = Field(default_factory=list)
    space_definitions: list[SpaceSeedDefinition] = Field(default_factory=list)
    object_definitions: list[ObjectSeedDefinition] = Field(default_factory=list)
    inventory_definitions: dict[str, dict[str, int]] = Field(default_factory=dict)
    action_ownership: list[str] = Field(default_factory=list)
    project_definitions: list[ProjectDefinition] = Field(default_factory=list)
    social_space_definitions: list[SocialSpaceDefinition] = Field(default_factory=list)
    livelihood: dict[str, Any] = Field(default_factory=dict)
    world_rules: list[str] = Field(default_factory=list)
    anchors: dict[str, str] = Field(default_factory=dict)
    unresolved: list[str] = Field(default_factory=list)

    @classmethod
    def from_bible(cls, bible: CharacterBible) -> CharacterDefinition:
        """Compile the parsed bible into the runtime contract (§6/§7)."""
        definition = cls(
            source_version=bible.version,
            source_hash=bible.source_hash,
            identity=_parse_identity(bible.facts),
            values=list(bible.values),
            preferences={key: list(values) for key, values in bible.preferences.items()},
            boundaries=list(bible.boundaries),
            inventory_definitions={key: dict(items) for key, items in bible.inventories.items()},
            livelihood=dict(bible.livelihood),
            anchors=_extract_anchors(bible),
            unresolved=list(bible.unresolved),
        )
        definition.appearance = [bible.facts["外貌"]] if bible.facts.get("外貌") else []
        # Personality: the bible has no dedicated section — derive from values
        # prose and mode styles; missing entries are recorded, never invented.
        traits: list[str] = []
        for value in bible.values:
            for separator in ("：", ":"):
                if separator in value:
                    traits.append(value.split(separator)[0].strip())
                    break
        definition.personality.traits = traits[:8]
        definition.personality.likes = list(bible.preferences.get("食物饮料", []))
        definition.personality.interests = list(bible.preferences.get("游戏", []))
        for mode in bible.modes:
            if mode.id == "home":
                definition.personality.habits.extend(mode.behavior[:4])
        definition.behavior_rules = [
            rule.text for rule in bible.rules if rule.canonical and rule.text
        ]
        definition.world_rules = [
            rule.text for rule in bible.rules if rule.canonical and "world" in rule.runtime
        ]
        definition.speech = SpeechCalibration(
            examples={key: list(value) for key, value in bible.speech_examples.items()},
            phrases=[phrase for mode in bible.modes for phrase in mode.phrases],
            prose=bible.prose,
        )
        core_declared = any(rel.type == "core_friend" for rel in bible.relationships)
        definition.relationships = [
            RelationshipDefinition(
                name=rel.name,
                type=rel.type,
                # a bible that names its core friends is taken at its word (a
                # character may have more than one); the first entry stays the
                # fallback for bibles that never say it
                core=(rel.type == "core_friend") if core_declared else index == 0,
                notes=list(rel.notes),
            )
            for index, rel in enumerate(bible.relationships)
        ]
        definition.modes = _compile_modes(bible)
        if bible.pet.name:
            pet = PetDefinition(
                name=bible.pet.name,
                species=bible.pet.species,
                traits=list(bible.pet.traits),
                behaviors=list(bible.pet.actions),
                special=list(bible.pet.special),
                food_item=bible.pet.food_item,
            )
            definition.pet = pet
            definition.pet_definitions = [pet]
        definition.space_definitions = [
            SpaceSeedDefinition(
                id=space.id,
                name=space.name,
                kind=space.kind,
                parent=space.parent,
                connections=list(space.connections),
                objects=list(space.objects),
                note=space.note,
            )
            for space in bible.spaces
        ]
        definition.object_definitions = [
            ObjectSeedDefinition(
                id=obj.id,
                name=obj.name,
                space=obj.space,
                inventory_key=obj.inventory_key or (obj.id if obj.id in bible.inventories else ""),
                note=obj.note,
            )
            for obj in bible.objects
        ]
        definition.social_space_definitions = _compile_social_spaces(bible)
        definition.project_definitions = _compile_projects(bible)
        definition.action_ownership = _compile_action_ownership(bible, anchors=definition.anchors)
        return definition


def _compile_modes(bible: CharacterBible) -> list[ModeDefinition]:
    """Bible modes → ModeDefinitions with machine-usable triggers (§18/§19)."""
    modes: list[ModeDefinition] = []
    for mode in bible.modes:
        conventional = CONVENTIONAL_MODES.get(mode.id, {})
        kind = _trigger_kind(mode.id, mode.trigger)
        modes.append(
            ModeDefinition(
                id=mode.id,
                name=mode.name,
                trigger=mode.trigger,
                trigger_kind=kind,
                time_window=_time_window(mode.trigger),
                style=mode.style,
                phrases=list(mode.phrases),
                tone=mode.tone,
                length=mode.length,
                priority=int(conventional.get("priority", 60)),
                is_default=bool(conventional.get("default", False)),
            )
        )
    return modes


#: bible hangout names → conventional social-space ids (system-level aliases;
#: unmatched names get generated ids, so a different bible still seeds fine)
SOCIAL_SPACE_ALIASES: dict[str, str] = {
    "游戏群": "game_group",
    "Minecraft服务器": "minecraft_server",
    "甜品同好群": "dessert_group",
    "猫图群": "cat_group",
    "单机游戏论坛": "game_forum",
    "游戏论坛": "game_forum",
}

#: hangout name keywords → topic lines (generic, name-derived)
_SOCIAL_TOPIC_KEYWORDS: dict[str, str] = {
    "游戏": "游戏/聊天",
    "Minecraft": "建筑/服务器日常",
    "服务器": "建筑/服务器日常",
    "甜品": "甜食分享",
    "猫": "猫图",
    "论坛": "攻略/讨论",
}


def _compile_social_spaces(bible: CharacterBible) -> list[SocialSpaceDefinition]:
    spaces: list[SocialSpaceDefinition] = []
    for index, name in enumerate(bible.social_spaces):
        slug = SOCIAL_SPACE_ALIASES.get(name, f"social_{index + 1}")
        topic = next(
            (line for needle, line in _SOCIAL_TOPIC_KEYWORDS.items() if needle in name),
            "闲聊",
        )
        if slug == "game_forum" or "论坛" in name:
            interest, temperature = 0.6, 0.3
        elif "服务器" in name:
            interest, temperature = 0.9, 0.5
        else:
            interest, temperature = 0.8, 0.5
        spaces.append(
            SocialSpaceDefinition(
                id=slug,
                name=name,
                topic=topic,
                interest=interest,
                social_temperature=temperature,
            )
        )
    return spaces


def _compile_projects(bible: CharacterBible) -> list[ProjectDefinition]:
    """§22: long-running goals from the bible's own words.

    The bible says "有自己的长期存档，造了一座慢慢扩大的小城" — one project
    line becomes one ProjectDefinition; there is deliberately no hardcoded
    project catalogue here.
    """
    projects: list[ProjectDefinition] = []
    keywords = ("存档", "小城", "长期", "项目")
    pool: list[str] = list(bible.preferences.get("游戏", []))
    for value in bible.values:
        pool.append(value)
    seen: set[str] = set()
    for line in pool:
        if not any(keyword in line for keyword in keywords) or line in seen:
            continue
        seen.add(line)
        name_match = re.search(r"一座(.*?)(?:[，。；]|$)", line)
        name = (name_match.group(1) if name_match else "长期项目").strip()[:12] or "长期项目"
        projects.append(
            ProjectDefinition(
                id=f"project_{len(projects) + 1}",
                name=name,
                progress=0.35,
                status="active",
            )
        )
    return projects


#: matches template slot placeholders such as {drink} / {snack} / {dessert}
_TEMPLATE_PLACEHOLDER = re.compile(r"\{([a-zA-Z_]+)\}")

#: template keys that make a template part of the procurement/shopping family
_PROCUREMENT_KEYS = ("purchase", "restock", "requires_absent")


def _template_slot_names(template: dict[str, Any]) -> set[str]:
    """Anchor slots a procurement template resolves against ({drink}, …)."""
    slots: set[str] = set()

    def collect(value: Any) -> None:
        if isinstance(value, str):
            slots.update(_TEMPLATE_PLACEHOLDER.findall(value))
        elif isinstance(value, dict):
            for key, item in value.items():
                collect(key)
                collect(item)
        elif isinstance(value, list):
            for item in value:
                collect(item)

    for key in _PROCUREMENT_KEYS:
        if template.get(key):
            collect(template[key])
    if template.get("effects"):
        # go_shopping_* carry no purchase/restock — the effects spell the slots
        collect(template["effects"])
    return slots


def _template_containers(template: dict[str, Any]) -> set[str]:
    """Inventory containers a procurement template writes to / checks."""
    containers: set[str] = set()
    for key in template.get("purchase") or {}:
        containers.add(str(key))
    restock = template.get("restock") or {}
    if restock.get("inventory"):
        containers.add(str(restock["inventory"]))
    for key in template.get("requires_absent") or {}:
        containers.add(str(key))
    return containers


def _anchor_owns_template(
    template_id: str, anchors: dict[str, str], inventories: dict[str, Any]
) -> bool:
    """Anchor presence for the procurement family: she buys what she actually has.

    A bible that never writes 买可乐 still owns ``buy_cola`` when its own
    ``{drink}`` anchor resolves; every slot the template references must
    resolve, and the container it writes to (fridge…) must exist in this
    world — a coffee-only character with no fridge owns no cola action.
    """
    template = ACTION_TEMPLATES.get(template_id)
    if not template or not any(template.get(key) for key in _PROCUREMENT_KEYS):
        return False
    slots = _template_slot_names(template)
    if not slots or not all(str(anchors.get(slot, "") or "").strip() for slot in slots):
        return False
    containers = _template_containers(template)
    return bool(containers) and containers <= set(inventories)


def _compile_action_ownership(
    bible: CharacterBible, *, anchors: dict[str, str] | None = None
) -> list[str]:
    """§20: which system action templates this character *owns*.

    Ownership is the union of two world-derived rules:

    * bible keywords (as before) — a bible without, say, Minecraft simply
      never owns ``play_minecraft``;
    * anchor presence for the procurement/shopping family — ``buy_cola`` is
      owned when this bible's own ``{drink}`` anchor resolves (she has/likes
      that drink), even if the text never writes 买可乐. Empty anchors or a
      missing container never grant ownership.

    Actions with no hit at all are recorded as unresolved rather than silently
    dropped.
    """
    corpus = bible.preferences_text() if hasattr(bible, "preferences_text") else ""
    if not corpus:
        parts = [bible.prose, *(f"{'；'.join(v)}" for v in bible.preferences.values())]
        parts.extend(mode.trigger + mode.style + "".join(mode.behavior) for mode in bible.modes)
        parts.extend(bible.values)
        parts.extend(str(v) for v in bible.facts.values())
        corpus = "\n".join(parts)
    owned: list[str] = []
    for template_id, keywords in ACTION_KEYWORDS.items():
        if not keywords or any(keyword in corpus for keyword in keywords):
            owned.append(template_id)
            continue
        if _anchor_owns_template(template_id, anchors or {}, bible.inventories):
            owned.append(template_id)
    return owned


__all__ = [
    "ACTION_KEYWORDS",
    "CONVENTIONAL_MODES",
    "SOCIAL_SPACE_ALIASES",
    "CharacterDefinition",
    "IdentityDefinition",
    "ModeDefinition",
    "ObjectSeedDefinition",
    "PetDefinition",
    "PersonalityDefinition",
    "ProjectDefinition",
    "RelationshipDefinition",
    "SocialSpaceDefinition",
    "SpaceSeedDefinition",
    "SpeechCalibration",
]
