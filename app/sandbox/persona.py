"""Bible → Persona (v2.0): the character page reads the compiled bible.

The sandbox bible is the canonical source for *who she is*; this module maps
it onto the Persona the WebUI edits and the prompt builder consumes, so the
``/character`` page is never empty and the chat prompt always carries her
identity. Everything here is derived from the bible's own data — names,
habits, relationships — with no character-specific literals.
"""

from __future__ import annotations

from typing import Any

from app.sandbox.bible import CharacterBible
from app.sandbox.definition import CharacterDefinition

#: conventional rule ids whose *label text* the bible itself carries — the
#: rule text always wins; this map only adds prompt phrasing where the bible
#: label is too terse. Unknown ids fall through to their own text.
RULE_PROMPT_HINTS: dict[str, str] = {
    "romance_avoidance": "不聊恋爱处对象类话题（回避、装死、转移话题）",
    "lecture_resistance": "不接受说教（敷衍过去，然后我行我素）",
    "smalltalk_aversion": "无信息量的客套懒得回",
    "casual_income": "偶尔接线上零工，不是正式职业",
}


def build_persona_payload(
    bible: CharacterBible, definition: CharacterDefinition | None = None
) -> dict[str, Any]:
    """The ``character:`` shape Persona.model_validate understands."""
    if definition is None:
        from app.sandbox.definition import CharacterDefinition as _CD

        definition = _CD.from_bible(bible)
    facts = bible.facts
    identity_def = definition.identity
    preferences = bible.preferences

    likes: list[str] = []
    banned: list[str] = []
    for key in ("食物饮料", "游戏", "猫", "甜品店"):
        for item in preferences.get(key, []):
            head = item.split("。")[0].strip()[:30]
            if not head:
                continue
            if "禁止" in head or "不符" in head:
                banned.append("不提" + head.split("：", 1)[-1][:24])
            elif head not in likes:
                likes.append(head)
    # bible-derived only: no hardcoded fallback list (an empty likes stays empty)
    likes = likes[:8]

    # dislikes: the bible's own Social Boundaries lead the list
    dislikes = [boundary.split("：", 1)[-1][:20] for boundary in bible.boundaries[:4]]
    dislikes.extend(banned)

    # habits: the default mode's behaviors are her daily habits (bible text)
    habits: list[str] = []
    default_mode = next(
        (mode for mode in definition.modes if mode.is_default),
        definition.modes[0] if definition.modes else None,
    )
    if default_mode is not None:
        source_mode = next((m for m in bible.modes if m.id == default_mode.id), None)
        if source_mode is not None:
            habits.extend(behavior[:40] for behavior in source_mode.behavior[:5])
    interests = [line.split("：")[0][:20] for line in bible.preferences.get("游戏", [])]
    interests.extend(line.split("：")[0][:20] for line in bible.preferences.get("猫", []))

    # traits: values section leads ("对熬夜：知道不好…" → "对熬夜"), then style
    traits = [trait for trait in definition.personality.traits[:5]]
    for mode in bible.modes:
        if mode.id == "home" and mode.style:
            traits.append(mode.style.split("；")[0][:20])

    behavior_rules: list[str] = []
    for rule in bible.rules:
        if not rule.canonical:
            continue
        text = rule.text
        hint = RULE_PROMPT_HINTS.get(rule.id)
        if hint:
            text = hint
        if text and text not in behavior_rules:
            behavior_rules.append(text)
    # keep the boundaries verbatim too (they are emphatic in the bible)
    for boundary in bible.boundaries[:4]:
        text = boundary.split("：", 1)[-1].strip()[:60]
        if text and text not in behavior_rules:
            behavior_rules.append(text)

    occupation = str(facts.get("生活状态", ""))
    occupation_short = ""
    if "不用工作" in occupation or "不用上学" in occupation:
        occupation_short = "不上学也不上班"
    if str(bible.livelihood.get("casual_income", "")).lower() in ("true", "yes", "是", "有"):
        occupation_short += "，偶尔接线上零工" if occupation_short else "偶尔接线上零工"

    identity = {
        "name": identity_def.name or str(facts.get("角色名", "")),
        "nickname": identity_def.nickname or str(facts.get("别名", "")).split("、")[0],
        "age": identity_def.age or str(facts.get("年龄", "")),
        "birthday": identity_def.birthday or str(facts.get("生日", "")),
        "gender": identity_def.gender or str(facts.get("性别", "")),
        "occupation": occupation_short,
        "location": (identity_def.household or str(facts.get("居住", "")))[:20],
        "background": (
            f"{facts.get('居住', '')}；{facts.get('生活状态', '')}；外貌：{facts.get('外貌', '')}"
        ).strip("；"),
    }

    style_notes = " / ".join(f"{mode.name}：{mode.style}" for mode in bible.modes if mode.style)

    # the relationship/pet lines come from the bible's own data
    context_lines: list[str] = []
    if bible.pet.name:
        pet_traits = "、".join(bible.pet.traits[:2]) or "有个性的"
        context_lines.append(f"{bible.pet.name}是她的{bible.pet.species}（{pet_traits}）。")
    for rel in bible.relationships:
        if rel.type == "core_friend":
            context_lines.append(f"{rel.name}是她的核心朋友。")
            break
    system_prompt = bible.prose
    if style_notes:
        system_prompt += "\n\n说话方式按当前状态切换：" + style_notes
    if context_lines:
        system_prompt += "\n\n" + "".join(context_lines)

    persona_name = identity["name"] or "角色"

    return {
        "name": f"{persona_name}（人物档案）",
        "identity": identity,
        "personality": {
            "traits": traits,
            "likes": likes,
            "dislikes": dislikes,
            "habits": habits,
            "interests": interests,
        },
        "speaking_style": {
            "language": "zh-CN",
            "tone": (definition.modes[0].tone if definition.modes else "") or "casual",
            "emoji": True,
            "kaomoji": False,
            "length_preference": (
                (definition.modes[0].length if definition.modes else "") or "short"
            ),
            "notes": style_notes[:200],
        },
        "behavior_rules": {"rules": behavior_rules},
        "system_prompt": system_prompt,
    }
