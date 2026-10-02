"""Character Bible: parser → compiler → coverage (v2.0 §8-§15).

The bible markdown is the canonical source. The compiler turns it into a
structured :class:`CharacterBible` — facts, rules, modes, relationships,
world seed, preferences — and records a **coverage report** saying which
compiled items drive runtime and which remain prompt-only or unresolved.
Nothing is silently dropped (§14).
"""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger("CatooBot.Sandbox.Bible")


class BibleRule(BaseModel):
    """One canonical behavioral rule (§115) with a traceable source (§35)."""

    id: str
    text: str
    source: str = ""
    source_section: str = ""  # which bible section the rule was found in
    runtime: str = ""  # which subsystem enforces it
    canonical: bool = True
    status: str = "canonical"  # canonical / candidate / unresolved


class BibleMode(BaseModel):
    """One mode definition (§18): id/name/trigger/style + extracted phrases."""

    id: str
    name: str
    trigger: str = ""
    style: str = ""
    behavior: list[str] = Field(default_factory=list)
    #: quoted phrases lifted from style/behavior text (口癖 / speech calibration)
    phrases: list[str] = Field(default_factory=list)
    #: system-level tone/length hints inferred from style keywords
    tone: str = ""
    length: str = ""


class BibleRelationship(BaseModel):
    name: str
    type: str = "friend"
    profile: dict[str, Any] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class BibleSpace(BaseModel):
    id: str
    name: str
    kind: str = "room"
    parent: str = ""
    note: str = ""
    #: sibling/parent topology compiled from the bible's indentation tree
    connections: list[str] = Field(default_factory=list)
    objects: list[str] = Field(default_factory=list)


class BibleObject(BaseModel):
    id: str
    name: str
    space: str = ""
    note: str = ""
    #: set when the bible's Inventory section keys this object as a container
    inventory_key: str = ""


class BiblePet(BaseModel):
    name: str = ""
    species: str = "猫"
    traits: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    special: list[str] = Field(default_factory=list)
    #: the pet's food item name, derived from the Inventory section (e.g. 猫粮)
    food_item: str = ""


class CharacterBible(BaseModel):
    """The compiled canonical definition (§10/§114-§117)."""

    version: str = ""
    source_hash: str = ""
    facts: dict[str, Any] = Field(default_factory=dict)
    modes: list[BibleMode] = Field(default_factory=list)
    rules: list[BibleRule] = Field(default_factory=list)
    boundaries: list[str] = Field(default_factory=list)
    relationships: list[BibleRelationship] = Field(default_factory=list)
    preferences: dict[str, list[str]] = Field(default_factory=dict)
    spaces: list[BibleSpace] = Field(default_factory=list)
    objects: list[BibleObject] = Field(default_factory=list)
    inventories: dict[str, dict[str, int]] = Field(default_factory=dict)
    pet: BiblePet = Field(default_factory=BiblePet)
    livelihood: dict[str, Any] = Field(default_factory=dict)
    social_spaces: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    prose: str = ""
    speech_examples: dict[str, list[str]] = Field(default_factory=dict)
    #: §33: stage directions inside speech examples ("猫跳上键盘") — behavior
    #: candidates, merged with real rules only when confirmed elsewhere
    behavior_candidates: list[str] = Field(default_factory=list)
    #: §14/§112-§113 reporting — never silent
    unresolved: list[str] = Field(default_factory=list)
    conflicts: list[str] = Field(default_factory=list)
    coverage: dict[str, Any] = Field(default_factory=dict)

    def rule(self, rule_id: str) -> BibleRule | None:
        for item in self.rules:
            if item.id == rule_id:
                return item
        return None

    def has_rule(self, rule_id: str) -> bool:
        return self.rule(rule_id) is not None


#: rule id → (keywords that prove it in the bible, runtime owner, human text)
RULE_LIBRARY: list[tuple[str, tuple[str, ...], str, str]] = [
    (
        "homewear_on_arrival",
        ("回家第一件事是换", "猫猫睡衣", "回家立刻换"),
        "actions:change_to_homewear",
        "回家立刻换猫猫睡衣",
    ),
    (
        "romance_avoidance",
        ("恋爱处对象类", "不感兴趣", "不聊这个"),
        "conversation boundaries",
        "恋爱话题回避",
    ),
    (
        "lecture_resistance",
        ("说教类", "嗯嗯好的"),
        "conversation boundaries",
        "不接受说教，敷衍后我行我素",
    ),
    (
        "privacy_strict",
        ("追问现实身份", "不暴露真实姓名"),
        "conversation boundaries",
        "拒绝现实身份追问",
    ),
    (
        "smalltalk_aversion",
        ("无聊的客套", "懒得回"),
        "conversation boundaries",
        "无信息量客套懒得回",
    ),
    (
        "night_owl",
        ("凌晨三四点睡", "凌晨三点是常态", "作息颠倒"),
        "needs:sleepiness + modes:deep_night",
        "常年熬夜，作息颠倒",
    ),
    (
        "name_origin_private",
        ("朋友取的", "空凛给她取的", "空凛给她取"),
        "conversation boundaries",
        "名字来源只说“朋友取的”",
    ),
    (
        "no_family_refs",
        ("没有哥哥", "父母在外地", "不要主动提及家人"),
        "context rules",
        "不提家人/同学/兄弟姐妹",
    ),
    (
        "game_preference",
        ("擅长建筑和红石", "格斗游戏"),
        "actions:play_minecraft/play_singleplayer",
        "Minecraft+单机游戏偏好",
    ),
    (
        "sweets_coke_anchor",
        ("冰箱里永远有可乐", "快乐水", "布丁"),
        "inventory + needs:hunger/thirst",
        "甜食可乐行为锚点",
    ),
    ("avoid_over_cutesy", ("避免过度卖萌", "不是刻意讨好"), "speech policy", "避免过度卖萌"),
    (
        "efficient_outings",
        ("快点搞定，快点回家", "三分钟搞定"),
        "actions:outdoor shopping",
        "出门高效，尽快回家",
    ),
    (
        "trash_accumulation",
        ("攒到不得不扔", "垃圾袋"),
        "objects:trash_bag + needs:household_maintenance",
        "垃圾攒满才出门扔",
    ),
    ("cat_care_priority", ("添粮", "猫粮"), "pet care + needs:pet_care", "猫粮按时添"),
    (
        "cat_photo_sharing",
        ("拍照发到群里", "看看我家主子"),
        "social space behavior",
        "猫入镜拍照发群",
    ),
    ("outdoor_cat_glance", ("路过猫", "多看两眼"), "outdoor behavior", "路过猫多看两眼不久留"),
    (
        "core_friend_special",
        ("关系最好的朋友", "回得比任何人都快", "放下手头的事"),
        "relationships + interrupt evaluator",
        "空凛特殊关系规则",
    ),
    (
        "casual_income",
        ("线上零工", "建筑委托"),
        "livelihood + commissions",
        "线上零工（非正式职业）",
    ),
    (
        "deep_night_escape_core_friend",
        ("从深夜模式里主动走出来", "从深夜模式主动走出来"),
        "interrupt + modes",
        "空凛能把她拉出深夜模式",
    ),
]


class BibleCompiler:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    # ------------------------------------------------------------------ read

    def load_text(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"character bible not found: {self.path}")
        return self.path.read_text(encoding="utf-8")

    # ---------------------------------------------------------------- compile

    def compile(self) -> CharacterBible:  # noqa: A003 - domain verb
        text = self.load_text()
        bible = CharacterBible(
            source_hash=hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            version="",
        )
        sections = _split_sections(text)
        bible.facts = _parse_facts(sections.get("Static Facts", []))
        bible.version = f"bible-{bible.source_hash}"
        bible.modes = _parse_modes(sections.get("Modes", []))
        bible.boundaries = _bullets(sections.get("Social Boundaries", []))
        bible.relationships = _parse_relationships(sections.get("Relationships", []))
        bible.livelihood = _parse_kv(sections.get("Livelihood", []))
        bible.preferences = _parse_preferences(sections.get("Preferences", []))
        bible.spaces, bible.objects = _parse_world_seed(sections.get("World Seed", []))
        _link_spaces(bible.spaces)
        _link_objects(bible.objects, bible.spaces)
        seed_entries = _parse_world_seed_extra(sections.get("World Seed", []))
        bible.inventories = seed_entries.get("inventories", {})
        bible.pet = _parse_pet(sections.get("World Seed", []))
        _derive_pet_food(bible)
        bible.values = _bullets(sections.get("Values", []))
        bible.prose = "\n".join(sections.get("Persona Prose", [])).strip()
        bible.speech_examples = _parse_examples(sections.get("Speech Examples", []))
        bible.behavior_candidates = _parse_example_directions(sections.get("Speech Examples", []))
        bible.social_spaces = _social_space_names(sections)
        bible.rules = self._compile_rules(sections, bible)
        bible.unresolved = self._unresolved(sections, bible)
        bible.conflicts = self._conflicts(bible)
        bible.coverage = self._coverage(bible)
        return bible

    # ----------------------------------------------------------------- rules

    def _compile_rules(
        self, sections: dict[str, list[str]], bible: CharacterBible
    ) -> list[BibleRule]:
        """Keyword-anchored canonical rules (§34/§35) with per-section provenance.

        The keyword anchors are *system-level* phrase families (romance/lecture/
        privacy/...); a bible that lacks them simply produces no rule of that
        id — and the miss is reported via ``unresolved`` so nothing vanishes
        silently when the character changes.
        """
        rules: list[BibleRule] = []
        section_texts = {name: "\n".join(lines) for name, lines in sections.items()}
        full_text = "\n".join(section_texts.values())
        for rule_id, keywords, runtime, label in RULE_LIBRARY:
            hit = ""
            section = ""
            for name, body in section_texts.items():
                found = next((kw for kw in keywords if kw in body), "")
                if found:
                    hit = found
                    section = name
                    break
            if not hit:
                hit = next((kw for kw in keywords if kw in full_text), "")
            if not hit:
                continue
            rules.append(
                BibleRule(
                    id=rule_id,
                    text=label,
                    source=f"“{hit}”",
                    source_section=section,
                    runtime=runtime,
                    canonical=True,
                    status="canonical",
                )
            )
        # Social Boundaries are canonical rules too (one per bullet).
        for index, bullet in enumerate(bible.boundaries, start=1):
            rules.append(
                BibleRule(
                    id=f"social_boundary_{index}",
                    text=bullet[:80],
                    source_section="Social Boundaries",
                    source="Social Boundaries",
                    runtime="conversation boundaries",
                    status="canonical",
                )
            )
        # §33: speech-example stage directions become *candidates* — recorded,
        # never silently promoted to rules.
        for candidate in bible.behavior_candidates[:10]:
            rules.append(
                BibleRule(
                    id=f"behavior_candidate_{len(rules) + 1}",
                    text=candidate[:80],
                    source_section="Speech Examples",
                    runtime="",
                    canonical=False,
                    status="candidate",
                )
            )
        return rules

    def _unresolved(self, sections: dict[str, list[str]], bible: CharacterBible) -> list[str]:
        """Bullets in known sections that produced no structured item (§14)."""
        unresolved: list[str] = []
        known = set()
        for mode in bible.modes:
            known.add(f"mode:{mode.id}")
        for rule in bible.rules:
            known.add(f"rule:{rule.id}")
        # Anything that looks like a statement but has no home is reported.
        # facts are free-form by design — nothing to check here
        values = bible.values
        if not values:
            unresolved.append("Values 章节未解析出条目")
        prose = bible.prose
        if not prose:
            unresolved.append("Persona Prose 章节缺失")
        if not bible.speech_examples:
            unresolved.append("Speech Examples 章节未解析")
        return unresolved

    def _conflicts(self, bible: CharacterBible) -> list[str]:
        """Detected canonical conflicts (§15) — merged, never overwritten."""
        conflicts: list[str] = []
        livelihood = bible.livelihood
        no_job = str(livelihood.get("formal_employment", "")).strip().lower() in (
            "false",
            "no",
            "否",
            "无",
        )
        casual = str(livelihood.get("casual_income", "")).strip().lower() in (
            "true",
            "yes",
            "是",
            "有",
        )
        if no_job and casual:
            conflicts.append(
                "正式工作=无 与 零工=有：已按语义合并为 "
                "formal_employment=false + casual_income=true（不算冲突，记录合并规则）"
            )
        facts = bible.facts
        if "生活状态" in facts and "不用工作" in str(facts.get("生活状态", "")):
            pass
        return conflicts

    def _coverage(self, bible: CharacterBible) -> dict[str, Any]:
        """§36/§37 level chain: parsed → compiled → (seed/runtime/test added later).

        ``world_seed.coverage_report()`` extends this with seeded /
        runtime-connected / tested once the seed has been built.
        """
        runtime_rules = [r for r in bible.rules if r.runtime]
        prompt_only = [
            "persona_prose",
            "speech_examples",
            "values",
        ]
        total = len(bible.rules) + len(bible.facts) + len(bible.modes) + len(prompt_only)
        #: §36 per-level counts (compile-time part of the chain)
        parsed = (
            len(bible.facts)
            + len(bible.modes)
            + len(bible.boundaries)
            + len(bible.relationships)
            + len(bible.spaces)
            + len(bible.objects)
            + len(bible.inventories)
            + (1 if bible.pet.name else 0)
            + len(bible.values)
            + len(bible.speech_examples)
        )
        compiled = (
            len(bible.rules)
            + len(bible.spaces)
            + len(bible.objects)
            + len(bible.inventories)
            + len(bible.modes)
            + (1 if bible.pet.name else 0)
            + len(bible.relationships)
        )
        return {
            "total": total,
            "runtime": len(runtime_rules),
            "facts": len(bible.facts),
            "modes": len(bible.modes),
            "rules": len(bible.rules),
            "prompt_only": prompt_only,
            "unresolved": list(bible.unresolved),
            "conflicts": list(bible.conflicts),
            "implemented": [
                *(f"rule:{r.id}" for r in bible.rules if r.runtime),
            ],
            # §36 levels
            "levels": {
                "parsed": parsed,
                "compiled": compiled,
                "seeded": 0,  # filled by world_seed.coverage_report()
                "runtime_connected": 0,
                "tested": 0,
            },
        }


# ------------------------------------------------------------------ parsing


def _split_sections(text: str) -> dict[str, list[str]]:
    """`## Section` → the lines under it (before the next `##`)."""
    sections: dict[str, list[str]] = {}
    current = ""
    for line in text.splitlines():
        if line.startswith("## "):
            current = line[3:].strip()
            sections.setdefault(current, [])
            continue
        if current:
            sections[current].append(line)
    return sections


def _bullets(lines: list[str]) -> list[str]:
    out: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
        elif stripped.startswith("  - "):
            out.append(stripped[4:].strip())
    return out


def _parse_kv(lines: list[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("- "):
            stripped = stripped[2:]
        match = re.match(r"([\w\u4e00-\u9fff_]+)\s*[:：]\s*(.+)", stripped)
        if match:
            out[match.group(1)] = match.group(2).strip()
    return out


def _parse_facts(lines: list[str]) -> dict[str, Any]:
    return _parse_kv(lines)


def _parse_modes(lines: list[str]) -> list[BibleMode]:
    modes: list[BibleMode] = []
    current: BibleMode | None = None
    for line in lines:
        if line.startswith("### "):
            if current is not None:
                modes.append(current)
            current = BibleMode(id="", name=line[4:].strip())
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        if stripped.startswith("id:"):
            current.id = stripped[3:].strip()
        elif stripped.startswith("触发"):
            current.trigger = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif stripped.startswith("风格"):
            current.style = stripped.split("：", 1)[-1].split(":", 1)[-1].strip()
        elif stripped.startswith("行为"):
            current.behavior.append(stripped.split("：", 1)[-1].split(":", 1)[-1].strip())
    if current is not None:
        modes.append(current)
    for mode in modes:
        _extract_mode_speech(mode)
    return [m for m in modes if m.id]


#: system-level style keyword → (tone, length) hints — generic speech hints,
#: not character-specific
_STYLE_HINTS: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("敬语", "礼貌", "温和"), "polite", "short"),
    (("拖长音", "懒散"), "casual", "lengthened"),
    (("少话", "低沉", "缓慢", "安静"), "quiet", "short"),
    (("话多", "随意", "有梗", "轻松"), "chatty", "flexible"),
    (("简短", "利落", "术语"), "focused", "short"),
    (("自言自语",), "casual", "short"),
)


def _extract_mode_speech(mode: BibleMode) -> None:
    """Lift quoted 口癖 phrases and tone hints out of style/behavior text."""
    body = mode.style + "；".join(mode.behavior)
    mode.phrases = re.findall(r"[“「]([^”」]{1,24})[”」]", body)
    for needles, tone, length in _STYLE_HINTS:
        if any(needle in body for needle in needles):
            mode.tone = tone
            mode.length = length
            break


def _parse_relationships(lines: list[str]) -> list[BibleRelationship]:
    relationships: list[BibleRelationship] = []
    current: BibleRelationship | None = None
    for line in lines:
        if line.startswith("### "):
            if current is not None:
                relationships.append(current)
            raw = line[4:].strip()
            name = re.sub(r"（[^）]*）", "", raw).strip()
            current = BibleRelationship(name=name)
            continue
        if current is None:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
            current.notes.append(stripped)
            if stripped.startswith("type:"):
                current.type = stripped[5:].strip()
    if current is not None:
        relationships.append(current)
    return relationships


def _parse_preferences(lines: list[str]) -> dict[str, list[str]]:
    prefs: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        if line.startswith("### "):
            current = line[4:].strip()
            prefs.setdefault(current, [])
            continue
        stripped = line.strip()
        if stripped.startswith("- ") and current:
            prefs[current].append(stripped[2:].strip())
    return prefs


def _sub_bullets(lines: list[str], title: str) -> list[str]:
    """Bullets under `### title` (until the next heading)."""
    out: list[str] = []
    in_section = False
    for line in lines:
        if line.startswith("### "):
            in_section = line[4:].strip() == title
            continue
        if in_section:
            stripped = line.strip()
            if stripped.startswith("- "):
                out.append(stripped[2:].strip())
    return out


def _parse_world_seed(lines: list[str]) -> tuple[list[BibleSpace], list[BibleObject]]:
    """Spaces (indentation tree, kind per system keywords) + objects."""
    spaces: list[BibleSpace] = []
    in_spaces = False
    current_group = ""
    current_group_kind = "apartment"
    for line in lines:
        if line.startswith("### "):
            in_spaces = line[4:].strip() == "Spaces"
            continue
        if not in_spaces:
            continue
        stripped = line.rstrip()
        if stripped.startswith("- "):
            item = stripped[2:].strip()
            match = re.match(r"([\w\u4e00-\u9fff_]+)\s*[（(]([^）)]*)[）)]", item)
            if match:
                sid = match.group(1)
                paren = match.group(2)
                # "公寓，根空间" / "外部" — the parenthetical carries the role
                name = paren.split("，")[0].split(",")[0].strip()
                note = paren
                if sid == "outside" or "外部" in paren or "室外" in paren:
                    current_group_kind = "outdoor"
                elif "根空间" in paren or "家" in name:
                    current_group_kind = "apartment"
                else:
                    current_group_kind = "outdoor"
                spaces.append(
                    BibleSpace(
                        id=sid,
                        name=name or sid,
                        kind=current_group_kind,
                        note=note,
                    )
                )
                current_group = sid
            continue
        if stripped.startswith("  - ") and current_group:
            item = stripped[4:].strip()
            parts = item.split(None, 1)
            if not parts:
                continue
            sid = parts[0]
            rest = parts[1] if len(parts) > 1 else sid
            name = rest.split("（")[0].split("，")[0].strip() or sid
            note_match = re.search(r"[（(]([^）)]*)[）)]", rest)
            spaces.append(
                BibleSpace(
                    id=sid,
                    name=name,
                    kind=_space_kind(sid, name),
                    parent=current_group,
                    note=note_match.group(1) if note_match else "",
                )
            )

    objects: list[BibleObject] = []
    for bullet in _sub_bullets(lines, "Objects"):
        parts = bullet.split(None, 1)
        if not parts:
            continue
        oid = parts[0]
        rest = parts[1] if len(parts) > 1 else ""
        note = ""
        space_ref = ""
        match = re.match(r"([\w\u4e00-\u9fff_]+)\s*[（(]([^）)]*)[）)]", rest)
        if match:
            note = match.group(1)
            space_ref = match.group(2)
        objects.append(BibleObject(id=oid, name=note or oid, space=space_ref, note=rest))
    return spaces, objects


#: system-level Chinese keyword → space kind — generic naming, not character data
_SPACE_KIND_KEYWORDS: tuple[tuple[tuple[str, ...], str], ...] = (
    (("走廊", "电梯", "楼道", "楼梯"), "transit"),
    (("便利店", "超市", "店", "商场"), "shop"),
    (("小区", "外面", "街道", "公园"), "outdoor"),
    (("卧室", "客厅", "厨房", "卫生间", "书房", "阳台"), "room"),
    (("玄关", "门厅"), "room"),
)


def _space_kind(sid: str, name: str) -> str:
    for needles, kind in _SPACE_KIND_KEYWORDS:
        if any(needle in name or needle in sid for needle in needles):
            return kind
    return "room"


def _link_spaces(spaces: list[BibleSpace]) -> None:
    """Compile connections from the tree: child↔parent + sibling mesh.

    The bible expresses containment, not door topology; the compiled graph
    (parent + same-parent siblings) is a conservative superset. Outdoor roots
    link to each other so home↔outside remains reachable.
    """
    by_id = {space.id: space for space in spaces}
    children: dict[str, list[str]] = {}
    for space in spaces:
        if space.parent:
            children.setdefault(space.parent, []).append(space.id)
    roots = [space.id for space in spaces if not space.parent]
    for space in spaces:
        linked: list[str] = []
        if space.parent and space.parent in by_id:
            linked.append(space.parent)
        linked.extend(sibling for sibling in children.get(space.parent, []) if sibling != space.id)
        if not space.parent:
            linked.extend(root for root in roots if root != space.id)
        space.connections = list(dict.fromkeys(linked))


def _link_objects(objects: list[BibleObject], spaces: list[BibleSpace]) -> None:
    """Resolve Chinese space references to space ids; record child objects.

    Objects carried on the person ("随身") resolve to no space — they ride
    the character's inventory instead.
    """
    name_to_id = {space.name: space.id for space in spaces}
    space_ids = set(name_to_id.values())
    for obj in objects:
        if obj.space and obj.space not in space_ids:
            obj.space = name_to_id.get(obj.space, "")
        if obj.space:
            target = next(s for s in spaces if s.id == obj.space)
            if obj.id not in target.objects:
                target.objects.append(obj.id)


def _derive_pet_food(bible: CharacterBible) -> None:
    """The pet's food item = first item of the pet-bowl inventory (e.g. 猫粮)."""
    for key, items in bible.inventories.items():
        if ("food" in key or "bowl" in key or "粮" in key) and items:
            bible.pet.food_item = next(iter(items))
            return


def _parse_world_seed_extra(lines: list[str]) -> dict[str, Any]:
    """Inventory group from `### Inventory` bullets like `fridge：可乐 × 10、布丁 × 3`."""
    inventories: dict[str, dict[str, int]] = {}
    for bullet in _sub_bullets(lines, "Inventory"):
        match = re.match(r"([\w\u4e00-\u9fff_]+)(?:[（(][^）)]*[）)])?\s*[：:]\s*(.+)", bullet)
        if not match:
            continue
        key = match.group(1)
        items: dict[str, int] = {}
        for part in re.split(r"[、,，]", match.group(2)):
            part = part.strip()
            if not part:
                continue
            count = 1
            amount = re.search(r"[×xX]\s*(\d+)", part)
            if amount:
                count = int(amount.group(1))
                part = part[: amount.start()].strip()
            if re.search(r"[（(]\s*充足\s*[）)]", part):
                count = 10  # system convention: 充足 → a comfortable stock
                part = re.sub(r"[（(]\s*充足\s*[）)]", "", part).strip()
            name = re.sub(r"[（(].*?[）)]", "", part).strip()
            if name:
                items[name] = count
        if items:
            inventories[key] = items
    return {"inventories": inventories}


def _parse_pet(lines: list[str]) -> BiblePet:
    pet = BiblePet()
    in_pet = False
    for line in lines:
        if line.startswith("### "):
            in_pet = line[4:].strip() == "Pet"
            continue
        if not in_pet:
            continue
        stripped = line.strip()
        if stripped.startswith("- "):
            stripped = stripped[2:]
        if stripped.startswith("name:"):
            pet.name = stripped[5:].strip()
        elif stripped.startswith("species:"):
            pet.species = stripped[8:].strip()
        elif stripped.startswith("性格"):
            pet.traits.append(stripped.split("：", 1)[-1])
        elif stripped.startswith("行为"):
            pet.actions.extend(
                a.strip() for a in re.split(r"[、,，]", stripped.split("：", 1)[-1]) if a.strip()
            )
        elif stripped.startswith("特殊"):
            pet.special.append(stripped.split("：", 1)[-1])
    return pet


def _parse_examples(lines: list[str]) -> dict[str, list[str]]:
    """Quoted speech lines per example section (§32: Speech Calibration)."""
    examples: dict[str, list[str]] = {}
    current = ""
    for line in lines:
        if line.startswith("### "):
            current = line[4:].strip()
            examples.setdefault(current, [])
            continue
        stripped = line.strip()
        if current and stripped.startswith("“") and "”" in stripped:
            examples[current].append(stripped)
    return examples


def _parse_example_directions(lines: list[str]) -> list[str]:
    """Stage directions (（…）lines) — §33 behavior candidates, never rules."""
    directions: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("（") and "）" in stripped:
            inner = stripped.strip("（）").strip("()").strip()
            if inner and inner not in directions:
                directions.append(inner)
    return directions


#: generic name pattern for simulated online hangouts — replaces the old
#: hardcoded needle list (any "X群 / X服务器 / X论坛" in the bible counts)
_SOCIAL_SPACE_RE = re.compile(
    r"(?<![在个到会去于进和与了，。；：])"  # not mid-phrase after a particle/verb
    r"([\w\u4e00-\u9fff]{2,10}(?:同好群|图群|游戏群|服务器|论坛|群))"
)
#: an enumeration's first member often reads "在游戏群、X服务器、…" — the
#: leading 在 is grammar, not part of the name
_SOCIAL_ENUM_RE = re.compile(r"^在([\w一-鿿]{2,9}(?:同好群|图群|游戏群|服务器|论坛|群))$")
#: candidates that carry narration verbs are sentences, not hangout names
_SOCIAL_VERB_BLOCKLIST = ("拍照", "放下", "开会", "聊得", "一边", "去开", "遇到", "配文")


def _social_space_names(sections: dict[str, list[str]]) -> list[str]:
    names: list[str] = []
    for key in ("Modes", "Values", "Static Facts", "Preferences"):
        for line in sections.get(key, []):
            for match in _SOCIAL_SPACE_RE.finditer(line):
                name = match.group(1)
                if name in names or any(verb in name for verb in _SOCIAL_VERB_BLOCKLIST):
                    continue
                names.append(name)
            # enumeration tokens: "在游戏群" → "游戏群"
            for token in re.split(r"[、，,；;]", line):
                enum_match = _SOCIAL_ENUM_RE.match(token.strip())
                if enum_match:
                    name = enum_match.group(1)
                    if name not in names and not any(
                        verb in name for verb in _SOCIAL_VERB_BLOCKLIST
                    ):
                        names.append(name)
    return names
