"""World facts: tag-driven entity selection + false-claim audit (v2.0).

The character must never invent world state ("冰箱空了" while 9 cans sit in
the fridge — a real incident). Two guards live here:

* :class:`FactSelector` — every entity carries semantic **tags**; when the
  topic matches tags (吃喝/猫/游戏/快递…), the *real* numbers and states are
  injected into the prompt with a "must not invent" constraint.
* :meth:`FactSelector.audit_claims` — an outbound guard that strips sentences
  claiming an entity is gone/empty when the sandbox says otherwise.

Both narrate what they did, so the operator can watch the injection in the
PowerShell log.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any

from app.utils.narrator import narrate

logger = logging.getLogger("CatooBot.Sandbox.Facts")

#: 标题语：注入时告诉模型这些数字是唯一可信来源
FACTS_HEADER = (
    "世界事实（唯一可信来源；提到这些实体的数量/状态时必须以此为准，"
    "不要编造“没了/喝完/最后一罐/空了”这类说法）："
)


@dataclass
class FactEntry:
    """One injectable entity: what to say, and which words should trigger it."""

    id: str
    label: str
    line: str
    keywords: list[str] = field(default_factory=list)


@dataclass
class FactsSelection:
    lines: list[str] = field(default_factory=list)
    hits: dict[str, list[str]] = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.lines

    def block(self) -> str:
        if self.empty:
            return ""
        return FACTS_HEADER + "\n" + "\n".join(f"- {line}" for line in self.lines)


class FactSelector:
    """Selects the world facts worth injecting for a given text."""

    def __init__(self, runtime: Any, *, max_entities: int = 4) -> None:
        self._rt = runtime
        self._max = max_entities

    # ------------------------------------------------------------ entries

    def entries(self) -> list[FactEntry]:
        rt = self._rt
        entries: list[FactEntry] = []

        for obj in rt.objects.all():
            keywords = list(getattr(obj, "tags", []) or []) + [obj.name]
            if obj.inventory_key:
                inventory = rt.inventories.get(obj.inventory_key)
                items = "、".join(f"{name}×{count}" for name, count in inventory.items.items())
                item_names = list(inventory.items.keys())
                entries.append(
                    FactEntry(
                        id=obj.id,
                        label=obj.name,
                        line=f"{obj.name}：{items or '空的'}",
                        keywords=keywords + item_names,
                    )
                )
                continue
            line = self._object_line(obj)
            if line:
                entries.append(FactEntry(id=obj.id, label=obj.name, line=line, keywords=keywords))

        pet_system = getattr(rt, "pet_system", None)
        if pet_system is not None:
            pet = pet_system.pet
            entries.append(
                FactEntry(
                    id="pet",
                    label=pet.name,
                    line=(
                        f"{pet.name}：{pet_system.activity_label()}"
                        f"（{self._space_name(pet.location)}），"
                        f"饥饿度 {pet.hunger:.2f}（0=不饿，1=很饿）"
                    ),
                    keywords=list(getattr(pet, "tags", []) or []) + [pet.name],
                )
            )

        character_inventory = rt.inventories.get("character")
        if character_inventory.items:
            items = "、".join(
                f"{name}×{count}" for name, count in character_inventory.items.items()
            )
            entries.append(
                FactEntry(
                    id="inventory:character",
                    label="随身物品",
                    line=f"她随身：{items}",
                    keywords=list(character_inventory.items.keys()) + ["随身", "身上"],
                )
            )

        for space in rt.spaces.all():
            can_do = "、".join(space.allowed_actions[:3]) or "休息"
            entries.append(
                FactEntry(
                    id=f"space:{space.id}",
                    label=space.name,
                    line=f"{space.name}：在公寓里（可做：{can_do}）",
                    keywords=list(getattr(space, "tags", []) or []) + [space.name],
                )
            )
        return [entry for entry in entries if entry.keywords]

    def _space_name(self, space_id: str) -> str:
        try:
            return self._rt.spaces.name(space_id)
        except Exception:  # noqa: BLE001 - cosmetic only
            return space_id

    def _object_line(self, obj: Any) -> str:
        state = getattr(obj, "state", {}) or {}
        if "level" in state:
            level = float(state.get("level") or 0.0)
            if level >= 0.7:
                return f"{getattr(obj, 'name', '')}：快满了（{level:.1f}/1.0）"
            return f"{getattr(obj, 'name', '')}：还不到半满（{level:.1f}/1.0）"
        if "present" in state:
            present = bool(state.get("present"))
            return f"{getattr(obj, 'name', '')}：{'门口有一个' if present else '目前没有'}"
        return f"{getattr(obj, 'name', '')}：在{self._space_name(getattr(obj, 'space_id', ''))}"

    # ------------------------------------------------------------ selection

    def select(self, text: str) -> FactsSelection:
        query = (text or "").strip()
        if not query:
            return FactsSelection()
        scored: list[tuple[int, FactEntry, list[str]]] = []
        for entry in self.entries():
            matched = [kw for kw in entry.keywords if kw and kw in query]
            if matched:
                scored.append((len(matched), entry, matched))
        scored.sort(key=lambda item: item[0], reverse=True)
        selection = FactsSelection()
        for _score, entry, matched in scored[: self._max]:
            selection.lines.append(entry.line)
            selection.hits[entry.id] = matched
        return selection

    # --------------------------------------------------------------- audit

    #: 子句切分（保留标点；违规片段与子句重叠时整句删除）
    _CLAUSE = re.compile(r"[^，,。！？!?…；;、\n]+[，,。！？!?…；;、\n]?")

    def audit_claims(self, text: str) -> tuple[str, list[str]]:
        """Remove claims that an entity is gone while the sandbox says otherwise.

        Clause-level with span overlap: any clause intersecting a false claim
        is dropped ("刚喝完最后一罐可乐，冰箱空了……不想出门补货" →
        "不想出门补货"); true clauses survive untouched. An all-false reply
        degrades to "……" rather than a lie.
        """
        if not text:
            return text, []
        rules = self._claim_rules()
        if not rules:
            return text, []
        spans: list[tuple[int, int]] = []
        violations: list[str] = []
        for pattern in rules:
            for match in pattern.finditer(text):
                fragment = match.group(0).strip()
                if not fragment:
                    continue
                spans.append((match.start(), match.end()))
                if fragment not in violations:
                    violations.append(fragment)
        if not spans:
            return text, []

        kept: list[str] = []
        for clause in self._CLAUSE.finditer(text):
            if any(not (clause.end() <= start or clause.start() >= end) for start, end in spans):
                continue
            kept.append(clause.group(0))
        cleaned = self._tidy("".join(kept))
        if not cleaned:
            cleaned = "……"
        narrate().say(
            "audit",
            f"拦下与沙盒不符的说法：{violations[0]}",
            detail="含该说法的句子已删除（真实状态见 事实 行）",
        )
        logger.warning("[Facts] claim mismatch removed: %s", "；".join(violations[:3]))
        return cleaned, violations

    @staticmethod
    def _tidy(text: str) -> str:
        """Collapse the punctuation/ellipsis left behind by removed clauses."""
        cleaned = re.sub(r"[ \t]+", " ", text)
        cleaned = re.sub(r"…{2,}", "……", cleaned)
        cleaned = re.sub(r"[，,、；;]{2,}", "，", cleaned)
        cleaned = re.sub(r"[。.!！?？；;、]+…{2,}", "……", cleaned)
        cleaned = re.sub(r"^[，,、；;。.!！?？…\s]+", "", cleaned)
        cleaned = re.sub(r"[，,、；;。.!！?？…\s]+$", "", cleaned)
        return cleaned.strip()

    def _claim_rules(self) -> list[re.Pattern[str]]:
        rt = self._rt
        rules: list[re.Pattern[str]] = []
        gone = r"(?:没了|没有了|没啦|喝完了|喝光|吃完了|吃光|用完了|空(?:了|的)|见底)"
        for obj in rt.objects.all():
            if not obj.inventory_key:
                continue
            inventory = rt.inventories.get(obj.inventory_key)
            total = sum(inventory.items.values())
            if total > 0:
                # container is NOT empty: any "冰箱空了/没东西" claim is false
                rules.append(re.compile(rf"{re.escape(obj.name)}(?:里|里都|都)?{gone}"))
                rules.append(
                    re.compile(rf"{re.escape(obj.name)}[^。！？!?\n]{{0,4}}没(?:东西|啥|什么)")
                )
            for item, count in inventory.items.items():
                if count <= 0:
                    continue
                escaped = re.escape(item)
                rules.append(re.compile(rf"{escaped}[^。！？!?\n]{{0,6}}{gone}"))
                if count > 1:
                    # "刚喝完最后一罐可乐" — only false while more than one is left
                    rules.append(
                        re.compile(
                            rf"(?:[喝吃用拿买](?:完|光|掉|没)?(?:了)?)?"
                            rf"最后一(?:罐|瓶|个|块|份|袋|杯)?{escaped}"
                        )
                    )
                rules.append(
                    re.compile(rf"(?:最后|only)?一(?:罐|瓶|个|块|份|袋|杯)?{escaped}(?:也)?没")
                )
                rules.append(
                    re.compile(rf"{escaped}[^。！？!?\n]{{0,4}}最后一(?:罐|瓶|个|块|份|袋|杯)")
                )
                rules.append(re.compile(rf"没(?:有)?{escaped}(?:了)?"))
        # pet-bowl claim rules: any inventory that backs a pet bowl object
        pet_bowl_keys = {
            obj.inventory_key
            for obj in rt.objects.all()
            if obj.inventory_key and ("bowl" in obj.id or "food" in obj.id)
        }
        for key in pet_bowl_keys:
            for name, count in rt.inventories.get(key).items.items():
                if count > 0:
                    escaped = re.escape(name)
                    rules.append(re.compile(rf"{escaped}[^。！？!?\n]{{0,6}}{gone}"))
        return rules
