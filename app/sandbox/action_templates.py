"""System-level action templates (§15/§20/§21).

Templates carry *generic* mechanics only: durations, needs physics, effect
shapes, and ``{placeholder}`` slots. Every character-specific value (pet name,
anchor items, project id, which spaces exist) is injected by
:func:`resolve_action_template` from the world seed — a template is legal
system knowledge; a hardcoded character is not (§10).
"""

from __future__ import annotations

from typing import Any

ACTION_TEMPLATES: dict[str, dict[str, Any]] = {
    "play_minecraft": {
        "activity": "gaming",
        "name": "玩{game}",
        "spaces": ["livingroom"],
        "required_objects": ["computer"],
        "min_minutes": 30,
        "typical_minutes": 120,
        "max_minutes": 360,
        "interruptibility": 0.35,
        "priority": 0.7,
        "tags": ["game", "favorite"],
        "need_relief": {"entertainment": 0.85, "social_need": 0.15},
        "need_cost": {"sleepiness": 0.05, "hunger": 0.04, "thirst": 0.04},
        "effects": {"project:{project}": 0.03},
        "modes": ["home", "gaming"],
        "detail_pool": ["在建城", "在搭红石", "在挖矿", "在扩建建筑"],
        "social_space": "minecraft_server",
    },
    "play_singleplayer": {
        "activity": "gaming",
        "name": "玩单机游戏",
        "spaces": ["livingroom"],
        "required_objects": ["computer"],
        "min_minutes": 30,
        "typical_minutes": 90,
        "max_minutes": 240,
        "interruptibility": 0.4,
        "priority": 0.6,
        "tags": ["game", "favorite"],
        "need_relief": {"entertainment": 0.8},
        "need_cost": {"sleepiness": 0.05, "hunger": 0.04, "thirst": 0.04},
        "modes": ["home", "gaming"],
        "detail_pool": ["在打Boss", "在刷成就", "在读档重来"],
    },
    "watch_animation": {
        "activity": "reading",
        "name": "看动画",
        "spaces": ["livingroom"],
        "required_objects": ["tv"],
        "min_minutes": 25,
        "typical_minutes": 60,
        "max_minutes": 180,
        "interruptibility": 0.6,
        "priority": 0.5,
        "tags": ["entertainment"],
        "need_relief": {"entertainment": 0.75},
        "need_cost": {"sleepiness": 0.03},
        "modes": ["home"],
        "detail_pool": ["在看新番", "在补旧番", "在看动画电影"],
    },
    "browse_social": {
        "activity": "online",
        "name": "刷群/论坛",
        "spaces": ["livingroom", "bedroom"],
        "required_objects": ["phone"],
        "min_minutes": 10,
        "typical_minutes": 30,
        "max_minutes": 90,
        "interruptibility": 0.8,
        "priority": 0.5,
        "tags": ["social"],
        "need_relief": {"social_need": 0.8, "entertainment": 0.2},
        "modes": ["home", "online_social"],
        "detail_pool": ["在群里吹水", "在刷论坛", "在回消息"],
        "social_space": "game_group",
    },
    "chat_group": {
        "activity": "online",
        "name": "在群里聊天",
        "spaces": ["livingroom", "bedroom"],
        "required_objects": ["phone"],
        "min_minutes": 5,
        "typical_minutes": 20,
        "max_minutes": 60,
        "interruptibility": 0.9,
        "priority": 0.45,
        "tags": ["social"],
        "need_relief": {"social_need": 0.6},
        "modes": ["home", "online_social"],
        "detail_pool": ["在接梗", "在发表情包", "在跟群友聊天"],
        "social_space": "game_group",
    },
    "browse_forum": {
        "activity": "online",
        "name": "刷论坛",
        "spaces": ["livingroom", "bedroom"],
        "required_objects": ["phone"],
        "min_minutes": 10,
        "typical_minutes": 25,
        "max_minutes": 60,
        "interruptibility": 0.85,
        "priority": 0.4,
        "tags": ["social"],
        "need_relief": {"social_need": 0.5, "entertainment": 0.25},
        "modes": ["home", "online_social"],
        "detail_pool": ["在刷论坛", "在回帖"],
        "social_space": "game_forum",
    },
    "eat_pudding": {
        "activity": "eating",
        "name": "吃{snack}",
        "spaces": ["kitchen"],
        "required_objects": ["fridge"],
        "min_minutes": 3,
        "typical_minutes": 8,
        "max_minutes": 15,
        "interruptibility": 0.95,
        "priority": 0.5,
        "tags": ["food", "favorite"],
        "need_relief": {"hunger": 0.5},
        "consumes": {"fridge": {"{snack}": 1}},
        "modes": ["home"],
        "detail_pool": ["在吃{snack}", "挖了一勺{snack}"],
        "requires_anchor": "snack",
    },
    "eat_cake": {
        "activity": "eating",
        "name": "吃{dessert}",
        "spaces": ["kitchen"],
        "required_objects": ["fridge"],
        "min_minutes": 5,
        "typical_minutes": 12,
        "max_minutes": 25,
        "interruptibility": 0.95,
        "priority": 0.5,
        "tags": ["food", "favorite"],
        "need_relief": {"hunger": 0.65},
        "consumes": {"fridge": {"{dessert}": 1}},
        "modes": ["home"],
        "detail_pool": ["在吃{dessert}"],
        "requires_anchor": "dessert",
    },
    "eat_fruit": {
        "activity": "eating",
        "name": "吃水果",
        "spaces": ["kitchen"],
        "required_objects": ["fridge"],
        "min_minutes": 5,
        "typical_minutes": 10,
        "max_minutes": 20,
        "interruptibility": 0.95,
        "priority": 0.4,
        "tags": ["food"],
        "need_relief": {"hunger": 0.45, "thirst": 0.2},
        "consumes": {"fridge": {"{fruit}": 1}},
        "modes": ["home"],
        "detail_pool": ["在吃{fruit}"],
        "requires_anchor": "fruit",
    },
    "drink_cola": {
        "activity": "eating",
        "name": "喝{drink}",
        "spaces": ["kitchen"],
        "required_objects": ["fridge"],
        "min_minutes": 2,
        "typical_minutes": 5,
        "max_minutes": 10,
        "interruptibility": 0.98,
        "priority": 0.5,
        "tags": ["drink", "favorite"],
        "need_relief": {"thirst": 0.75},
        "consumes": {"fridge": {"{drink}": 1}},
        "modes": ["home"],
        "detail_pool": ["在喝冰{drink}"],
        "requires_anchor": "drink",
    },
    "feed_cat": {
        "activity": "pet_care",
        "name": "给{pet}添粮",
        "spaces": ["kitchen"],
        "required_objects": ["cat_food_bowl"],
        "min_minutes": 2,
        "typical_minutes": 4,
        "max_minutes": 8,
        "interruptibility": 1.0,
        "priority": 0.6,
        "tags": ["pet", "care"],
        "need_relief": {"pet_care": 0.95},
        "consumes": {"cat_food_bowl": {"{pet_food}": -1}},
        "effects": {"pet:feed": 1.0},
        "modes": ["home"],
        "detail_pool": ["在给{pet}添粮"],
        "requires_pet": True,
        "requires_inventory": "cat_food_bowl",
    },
    "talk_to_cat": {
        "activity": "pet_care",
        "name": "跟{pet}说话",
        "spaces": ["livingroom", "bedroom"],
        "min_minutes": 1,
        "typical_minutes": 5,
        "max_minutes": 12,
        "interruptibility": 1.0,
        "priority": 0.3,
        "tags": ["pet"],
        "need_relief": {"pet_care": 0.35, "social_need": 0.1},
        "modes": ["home"],
        "detail_pool": ["在跟{pet}说话", "被{pet}蹭了"],
        "requires_pet": True,
    },
    "film_cat": {
        "activity": "pet_care",
        "name": "拍{pet}发群",
        "spaces": ["livingroom"],
        "required_objects": ["phone"],
        "min_minutes": 2,
        "typical_minutes": 5,
        "max_minutes": 10,
        "interruptibility": 1.0,
        "priority": 0.35,
        "tags": ["pet", "social"],
        "need_relief": {"social_need": 0.3, "pet_care": 0.1},
        "modes": ["home", "online_social"],
        "detail_pool": ["在拍{pet}", "发了一张宠物图"],
        "social_space": "cat_group",
        "requires_pet": True,
    },
    "take_out_trash": {
        "activity": "out",
        "name": "扔垃圾",
        "spaces": ["entrance", "neighborhood"],
        "required_objects": ["trash_bag"],
        "min_minutes": 5,
        "typical_minutes": 10,
        "max_minutes": 20,
        "interruptibility": 0.2,
        "priority": 0.55,
        "tags": ["chore", "outdoor"],
        "need_relief": {"household_maintenance": 0.95},
        "need_cost": {"sleepiness": 0.02},
        "effects": {"object:trash_bag.level": -1.0},
        "modes": ["outdoor"],
        "detail_pool": ["去扔垃圾"],
    },
    "pick_up_package": {
        "activity": "out",
        "name": "取快递",
        "spaces": ["entrance", "neighborhood"],
        "required_objects": ["door"],
        "min_minutes": 5,
        "typical_minutes": 8,
        "max_minutes": 15,
        "interruptibility": 0.3,
        "priority": 0.5,
        "tags": ["chore", "outdoor"],
        "need_relief": {"household_maintenance": 0.2},
        "effects": {"object:package_box.present": 0.0},
        "modes": ["outdoor"],
        "detail_pool": ["去取快递"],
    },
    "buy_cola": {
        "activity": "out",
        "name": "买{drink}",
        "spaces": ["convenience_store"],
        "min_minutes": 8,
        "typical_minutes": 15,
        "max_minutes": 30,
        "interruptibility": 0.2,
        "priority": 0.6,
        "tags": ["shopping", "outdoor"],
        "need_relief": {"thirst": 0.2},
        "effects": {"inventory:fridge:{drink}": 6, "object:trash_bag.level": 0.05},
        "modes": ["outdoor"],
        "detail_pool": ["在便利店买{drink}"],
        "requires_anchor": "drink",
    },
    "buy_snacks": {
        "activity": "out",
        "name": "买零食",
        "spaces": ["convenience_store"],
        "min_minutes": 8,
        "typical_minutes": 15,
        "max_minutes": 30,
        "interruptibility": 0.2,
        "priority": 0.5,
        "tags": ["shopping", "outdoor"],
        "effects": {"inventory:fridge:{drink}": 2},
        "modes": ["outdoor"],
        "detail_pool": ["在便利店补货"],
        "requires_anchor": "drink",
    },
    "buy_sweets": {
        "activity": "out",
        "name": "买甜食",
        "spaces": ["dessert_shop"],
        "min_minutes": 10,
        "typical_minutes": 20,
        "max_minutes": 35,
        "interruptibility": 0.2,
        "priority": 0.65,
        "tags": ["shopping", "outdoor", "favorite"],
        "need_relief": {"hunger": 0.1},
        "effects": {"inventory:fridge:{snack}": 2, "inventory:fridge:{dessert}": 1},
        "modes": ["outdoor"],
        "detail_pool": ["在甜品店挑蛋糕", "在甜品店买布丁"],
        "requires_anchor": "snack",
    },
    "go_shopping_cola": {
        "activity": "out",
        "name": "出门买{drink}",
        "spaces": ["*"],
        "min_minutes": 25,
        "typical_minutes": 40,
        "max_minutes": 70,
        "interruptibility": 0.25,
        "priority": 0.7,
        "tags": ["shopping", "outdoor", "favorite"],
        "need_relief": {"thirst": 0.8},
        "effects": {"inventory:fridge:{drink}": 6, "object:trash_bag.level": 0.05},
        "modes": ["outdoor"],
        "destination": "convenience_store",
        "requires_absent": {"fridge": ["{drink}"]},
        "detail_pool": ["去便利店抱了一批{drink}回来", "在便利店补{drink}"],
        "requires_anchor": "drink",
    },
    "go_shopping_sweets": {
        "activity": "out",
        "name": "出门买甜食",
        "spaces": ["*"],
        "min_minutes": 30,
        "typical_minutes": 50,
        "max_minutes": 90,
        "interruptibility": 0.2,
        "priority": 0.7,
        "tags": ["shopping", "outdoor", "favorite"],
        "need_relief": {"hunger": 0.4, "entertainment": 0.2},
        "effects": {"inventory:fridge:{snack}": 2, "inventory:fridge:{dessert}": 1},
        "modes": ["outdoor"],
        "destination": "dessert_shop",
        "requires_absent": {"fridge": ["{snack}", "{dessert}"]},
        "detail_pool": ["去甜品店挑了半天", "在甜品店买了草莓蛋糕和布丁"],
        "requires_anchor": "snack",
    },
    "sleep": {
        "activity": "sleeping",
        "name": "睡觉",
        "spaces": ["bedroom"],
        "required_objects": ["bed"],
        "min_minutes": 240,
        "typical_minutes": 540,
        "max_minutes": 720,
        "interruptibility": 0.05,
        "priority": 0.95,
        "tags": ["rest", "core"],
        "need_relief": {"sleepiness": 1.0, "energy": 1.0},
        "modes": ["home"],
        "detail_pool": ["在睡觉"],
    },
    "nap": {
        "activity": "napping",
        "name": "补觉",
        "spaces": ["bedroom", "livingroom"],
        "required_objects": ["bed"],
        "min_minutes": 40,
        "typical_minutes": 120,
        "max_minutes": 240,
        "interruptibility": 0.25,
        "priority": 0.6,
        "tags": ["rest"],
        "need_relief": {"sleepiness": 0.6, "energy": 0.5},
        "modes": ["home"],
        "detail_pool": ["在补觉"],
    },
    "shower": {
        "activity": "self_care",
        "name": "洗澡",
        "spaces": ["bathroom"],
        "min_minutes": 10,
        "typical_minutes": 20,
        "max_minutes": 40,
        "interruptibility": 0.1,
        "priority": 0.5,
        "tags": ["self-care"],
        "need_relief": {"hygiene": 0.95},
        "modes": ["home"],
        "detail_pool": ["在洗澡"],
    },
    "tidy_room": {
        "activity": "household",
        "name": "整理房间",
        "spaces": ["livingroom", "bedroom"],
        "min_minutes": 15,
        "typical_minutes": 40,
        "max_minutes": 90,
        "interruptibility": 0.5,
        "priority": 0.4,
        "tags": ["chore"],
        "need_relief": {"household_maintenance": 0.5},
        "effects": {"object:trash_bag.level": 0.15},
        "modes": ["home"],
        "detail_pool": ["在收拾屋子"],
    },
    "work_commission": {
        "activity": "working",
        "name": "做零工",
        "spaces": ["livingroom"],
        "required_objects": ["computer"],
        "min_minutes": 30,
        "typical_minutes": 75,
        "max_minutes": 180,
        "interruptibility": 0.5,
        "priority": 0.55,
        "tags": ["work"],
        "need_relief": {"work_need": 0.9},
        "need_cost": {"sleepiness": 0.04},
        "effects": {"commission:progress": 0.25},
        "modes": ["home"],
        "detail_pool": ["在做委托", "在写稿", "在画稿"],
    },
    "idle_on_sofa": {
        "activity": "idle",
        "name": "瘫在沙发上",
        "spaces": ["livingroom"],
        "required_objects": ["sofa"],
        "min_minutes": 10,
        "typical_minutes": 30,
        "max_minutes": 90,
        "interruptibility": 1.0,
        "priority": 0.2,
        "tags": ["idle"],
        "need_relief": {},
        "modes": ["home"],
        "detail_pool": ["瘫在沙发上玩手机", "窝着发呆"],
    },
    "change_to_homewear": {
        "activity": "idle",
        "name": "换睡衣",
        "spaces": ["bedroom", "entrance"],
        "required_objects": ["wardrobe"],
        "min_minutes": 2,
        "typical_minutes": 3,
        "max_minutes": 6,
        "interruptibility": 1.0,
        "priority": 0.7,
        "tags": ["ritual"],
        "need_relief": {},
        "modes": ["home"],
        "detail_pool": ["换上了睡衣"],
    },
    "change_to_outdoor": {
        "activity": "idle",
        "name": "换外出装",
        "spaces": ["bedroom", "entrance"],
        "required_objects": ["wardrobe"],
        "min_minutes": 3,
        "typical_minutes": 5,
        "max_minutes": 10,
        "interruptibility": 1.0,
        "priority": 0.7,
        "tags": ["ritual", "outdoor"],
        "need_relief": {},
        "modes": ["outdoor"],
        "detail_pool": ["打理好外表换上外出装"],
    },
    "think": {
        "activity": "idle",
        "name": "发呆/想事情",
        "spaces": ["*"],
        "min_minutes": 5,
        "typical_minutes": 15,
        "max_minutes": 45,
        "interruptibility": 1.0,
        "priority": 0.15,
        "tags": ["idle"],
        "need_relief": {},
        "modes": ["home"],
        "detail_pool": ["在发呆", "在想事情"],
    },
    "walk": {
        "activity": "out",
        "name": "在外面走",
        "spaces": ["neighborhood"],
        "min_minutes": 5,
        "typical_minutes": 15,
        "max_minutes": 40,
        "interruptibility": 0.6,
        "priority": 0.3,
        "tags": ["outdoor"],
        "need_relief": {"entertainment": 0.1},
        "modes": ["outdoor"],
        "detail_pool": ["在外面走"],
    },
    "idle": {
        "activity": "idle",
        "name": "闲着",
        "spaces": ["*"],
        "min_minutes": 5,
        "typical_minutes": 15,
        "max_minutes": 40,
        "interruptibility": 1.0,
        "priority": 0.1,
        "tags": ["idle"],
        "modes": ["home"],
        "detail_pool": ["闲着呢"],
    },
}


def resolve_action_template(
    template: dict[str, Any],
    *,
    template_id: str,
    anchors: dict[str, str],
    inventories: dict[str, dict[str, int]],
    pet_name: str,
    project_id: str,
    social_ids: set[str],
    space_ids: set[str] | None = None,
    shop_ids: list[str] | None = None,
) -> dict[str, Any] | None:
    """Template + seed → one concrete action payload (§20/§21).

    Returns None when the world cannot support the action (missing anchor
    item / required pet / missing inventory / no valid space) — the character
    simply does not own it, which the coverage report shows instead of hiding.
    """
    anchor_slot = template.get("requires_anchor", "")
    if anchor_slot and not anchors.get(anchor_slot):
        return None
    if template.get("requires_pet") and not pet_name:
        return None
    required_inventory = template.get("requires_inventory", "")
    if required_inventory and required_inventory not in inventories:
        return None
    # feeding needs a food item to require
    if "{pet_food}" in str(template.get("consumes", {})) and not any(
        "bowl" in key or "food" in key for key in inventories
    ):
        return None

    values: dict[str, str] = {
        "pet": pet_name or "宠物",
        "pet_food": next(
            (
                item
                for key, items in inventories.items()
                if ("bowl" in key or "food" in key)
                for item in items
            ),
            "粮",
        ),
        "drink": anchors.get("drink", ""),
        "snack": anchors.get("snack", ""),
        "dessert": anchors.get("dessert", ""),
        "fruit": anchors.get("fruit", ""),
        "project": project_id,
    }
    payload: dict[str, Any] = {"id": template_id}
    for key, value in template.items():
        if key in ("requires_anchor", "requires_pet", "requires_inventory"):
            continue
        payload[key] = _substitute(value, values)
    # spaces / destination must exist in this world; else fall back sensibly
    if space_ids is not None:
        mapped = _map_spaces(payload.get("spaces", []), space_ids)
        if mapped is None:
            return None
        payload["spaces"] = mapped
        destination = str(payload.get("destination", ""))
        if destination:
            if destination in space_ids:
                pass
            elif shop_ids:
                payload["destination"] = shop_ids[0]
            else:
                payload.pop("destination", None)
    # social_space must exist; else fall back to any seeded social space
    social_ref = str(payload.get("social_space", ""))
    if social_ref and social_ids:
        if social_ref not in social_ids:
            payload["social_space"] = sorted(social_ids)[0]
    elif social_ref and not social_ids:
        payload.pop("social_space", None)
    return payload


def _substitute(value: Any, values: dict[str, str]) -> Any:
    if isinstance(value, str):
        out = value
        for key, replacement in values.items():
            out = out.replace("{" + key + "}", replacement)
        return out
    if isinstance(value, list):
        return [_substitute(item, values) for item in value]
    if isinstance(value, dict):
        return {_substitute(key, values): _substitute(item, values) for key, item in value.items()}
    return value


def _map_spaces(template_spaces: list[str], space_ids: set[str]) -> list[str] | None:
    """Map conventional template space ids onto this world's actual spaces.

    ``*`` passes through. A conventional id that exists is kept; missing ids
    are dropped, and if nothing remains the action is not ownable (None).
    """
    if "*" in template_spaces:
        return ["*"]
    mapped = [space for space in template_spaces if space in space_ids]
    return mapped or None
