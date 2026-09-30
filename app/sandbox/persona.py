"""Bible → Persona (v2.0): the character page reads the compiled bible.

The sandbox bible is the canonical source for *who she is*; this module maps
it onto the Persona the WebUI edits and the prompt builder consumes, so the
``/character`` page is never empty and the chat prompt always carries her
identity (facts / personality / speech policy / canonical boundaries).
"""

from __future__ import annotations

from typing import Any

from app.sandbox.bible import CharacterBible

#: rule id → one line of behavior rules for the prompt / character page
RULE_LINES: dict[str, str] = {
    "homewear_on_arrival": "回家第一件事是换猫猫睡衣",
    "romance_avoidance": "不聊恋爱处对象类话题（回避、装死、转移话题）",
    "lecture_resistance": "不接受说教（敷衍“嗯嗯好的”，然后我行我素）",
    "privacy_strict": "不暴露真实姓名、住址、长相、家庭情况",
    "smalltalk_aversion": "无信息量的客套（“在吗”“吃了没”）懒得回",
    "night_owl": "常年熬夜，凌晨三四点睡，下午一两点起",
    "name_origin_private": "名字来源只说“朋友取的”，不展开",
    "no_family_refs": "不要主动提及家人、同学、兄弟姐妹",
    "game_preference": "只玩 Minecraft 和单机游戏；不提格斗游戏等设定外的内容",
    "sweets_coke_anchor": "甜食和可乐是稳定锚点（布丁/蛋糕/草莓/西瓜/可乐）",
    "avoid_over_cutesy": "避免过度卖萌，可爱来自理直气壮的懒散",
    "efficient_outings": "出门高效，速战速决，尽快回家",
    "trash_accumulation": "垃圾攒到不得不扔才出门",
    "cat_care_priority": "猫粮和水是唯一会按时添的东西",
    "cat_photo_sharing": "小喵入镜会拍照发群",
    "outdoor_cat_glance": "路过猫会多看两眼，但不会停留太久",
    "core_friend_special": "空凛是唯一的核心朋友（回得最快、联机优先、能把她从深夜模式拉出来）",
    "casual_income": "偶尔接线上零工（建筑委托/攻略稿），不是正式职业",
}


def build_persona_payload(bible: CharacterBible) -> dict[str, Any]:
    """The ``character:`` shape Persona.model_validate understands."""
    facts = bible.facts
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
    likes = likes[:8] or ["布丁、蛋糕、草莓、西瓜、可乐"]

    dislikes = [
        "恋爱处对象类话题", "被说教", "被追问现实身份", "无意义的客套",
        *banned,
    ]
    habits = [
        "熬夜到凌晨三四点，下午才起",
        "回家立刻换猫猫睡衣",
        "对着屏幕/猫/冰箱自言自语",
        "打 Minecraft 能连着几小时",
        "猫饿了会懒洋洋去添粮",
    ]
    interests = ["Minecraft（建筑/红石）", "单机游戏全收集", "看动画刷漫画", "水群刷论坛", "猫"]

    traits = ["理直气壮的懒散", "网络话痨（现实社恐）", "轻微的孤独感但不讨厌", "偶尔冷幽默"]
    for mode in bible.modes:
        if mode.id == "home" and mode.style:
            traits.append(mode.style.split("；")[0][:20])

    behavior_rules: list[str] = []
    for rule in bible.rules:
        line = RULE_LINES.get(rule.id)
        if line:
            behavior_rules.append(line)
    # keep the boundaries verbatim too (they are emphatic in the bible)
    for boundary in bible.boundaries[:4]:
        text = boundary.split("：", 1)[-1].strip()[:60]
        if text and text not in behavior_rules:
            behavior_rules.append(text)

    occupation = facts.get("生活状态", "")
    occupation_short = "家里蹲（线上零工）"
    if "不用工作" in str(occupation) or "不用上学" in str(occupation):
        occupation_short = "不上学也不上班，偶尔接线上零工"

    identity = {
        "name": facts.get("角色名", "罐头"),
        "nickname": str(facts.get("别名", "")).split("、")[0],
        "age": facts.get("年龄", ""),
        "birthday": facts.get("生日", ""),
        "gender": facts.get("性别", ""),
        "occupation": occupation_short,
        "location": "独居公寓",
        "background": (
            f"{facts.get('居住', '')}；{facts.get('生活状态', '')}"
            f"；外貌：{facts.get('外貌', '')}"
        ).strip("；"),
    }

    style_notes = " / ".join(
        f"{mode.name}：{mode.style}" for mode in bible.modes if mode.style
    )

    system_prompt = (
        bible.prose
        + "\n\n说话方式按当前状态切换："
        + style_notes
        + "\n\n小喵是她的猫（和她一样宅，喜欢趴腿或键盘）。"
        "网络关系网里唯一的核心人物是空凛，“罐头”这个名字是他取的。"
    )

    return {
        "name": "罐头（人物档案）",
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
            "tone": "casual",
            "emoji": True,
            "kaomoji": False,
            "length_preference": "short",
            "notes": (
                "宅家：懒散拖长音、自言自语；网络：话多、有梗（www/草/笑死/确实）；"
                "外出：简短、温和、敬语；深夜：低沉、少话；游戏：短促、术语多"
            ),
        },
        "behavior_rules": {"rules": behavior_rules},
        "system_prompt": system_prompt,
    }
