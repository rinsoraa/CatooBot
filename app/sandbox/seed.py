"""SandboxSeed: the bible's world becomes real state (v2.0 §117-§122).

Seed = "what the initial world looks like"; runtime = "what it has become".
Randomness is only allowed on non-canonical details and is seeded (§122/§123).
"""

from __future__ import annotations

import random
from typing import Any

from app.sandbox.bible import CharacterBible
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

DEFAULT_NEEDS: list[tuple[str, float, float]] = [
    # key, initial level, growth per hour
    ("hunger", 0.35, 0.09),
    ("thirst", 0.30, 0.12),
    ("sleepiness", 0.20, 0.055),
    ("energy", 0.30, 0.06),          # low energy pressure grows while awake
    ("hygiene", 0.20, 0.02),
    ("social_need", 0.35, 0.10),
    ("entertainment", 0.30, 0.11),
    ("pet_care", 0.10, 0.05),
    ("household_maintenance", 0.10, 0.03),
    ("work_need", 0.05, 0.01),
    ("project_progress", 0.10, 0.02),
]


def build_spaces() -> list[SpaceNode]:
    """The apartment + the outside (straight from the bible's world seed)."""
    return [
        SpaceNode(
            id="apartment", name="公寓", kind=SpaceKind.apartment,
            connects=["hallway"],
            allowed_actions=["change_to_homewear", "idle", "think"],
            tags=["家", "公寓", "房间", "家里", "回家"],
        ),
        SpaceNode(
            id="bedroom", name="卧室", kind=SpaceKind.room, parent_id="apartment",
            connects=["livingroom", "bathroom"],
            allowed_actions=["sleep", "nap", "browse_social", "think", "change_to_outdoor"],
            objects=["bed", "wardrobe"],
            tags=["卧室", "睡觉", "休息", "床", "换衣服", "睡衣"],
        ),
        SpaceNode(
            id="livingroom", name="客厅", kind=SpaceKind.room, parent_id="apartment",
            connects=["bedroom", "kitchen", "entrance"],
            allowed_actions=[
                "play_minecraft", "play_singleplayer", "watch_animation", "idle_on_sofa",
                "browse_social", "talk_to_cat", "film_cat", "think", "tidy_room",
            ],
            objects=["computer", "sofa", "tv", "phone"],
            tags=["客厅", "沙发", "电视", "玩", "打游戏", "看剧", "窝着", "上网"],
        ),
        SpaceNode(
            id="kitchen", name="厨房", kind=SpaceKind.room, parent_id="apartment",
            connects=["livingroom"],
            allowed_actions=["eat_pudding", "eat_cake", "drink_cola", "eat_fruit",
                             "feed_cat", "refill_water"],
            objects=["fridge", "cat_food_bowl", "cat_water_bowl"],
            tags=["厨房", "做饭", "吃", "喝", "拿饮料", "喂猫"],
        ),
        SpaceNode(
            id="bathroom", name="卫生间", kind=SpaceKind.room, parent_id="apartment",
            connects=["bedroom"],
            allowed_actions=["shower"],
            tags=["卫生间", "洗澡", "洗漱"],
        ),
        SpaceNode(
            id="entrance", name="玄关", kind=SpaceKind.room, parent_id="apartment",
            connects=["livingroom", "hallway"],
            allowed_actions=["take_out_trash", "pick_up_package", "change_to_outdoor",
                             "change_to_homewear"],
            objects=["trash_bag", "door", "package_box"],
            tags=["玄关", "门口", "出门", "回家", "换鞋"],
        ),
        SpaceNode(
            id="hallway", name="走廊", kind=SpaceKind.transit, connects=["entrance", "elevator"],
            allowed_actions=[], tags=["走廊", "出门"],
        ),
        SpaceNode(
            id="elevator", name="电梯", kind=SpaceKind.transit,
            connects=["hallway", "neighborhood"], allowed_actions=[], tags=["电梯", "出门"],
        ),
        SpaceNode(
            id="neighborhood", name="小区", kind=SpaceKind.outdoor,
            connects=["elevator", "convenience_store", "dessert_shop"],
            allowed_actions=["take_out_trash", "pick_up_package", "walk"],
            tags=["小区", "楼下", "外面", "散步", "扔垃圾", "取快递"],
        ),
        SpaceNode(
            id="convenience_store", name="便利店", kind=SpaceKind.shop,
            connects=["neighborhood", "dessert_shop"],
            allowed_actions=["buy_cola", "buy_snacks"],
            private=False,
            tags=["便利店", "买东西", "买可乐", "饮料", "零食", "出门"],
        ),
        SpaceNode(
            id="dessert_shop", name="甜品店", kind=SpaceKind.shop,
            connects=["neighborhood", "convenience_store"],
            allowed_actions=["buy_sweets"],
            private=False,
            tags=["甜品店", "甜食", "蛋糕", "布丁", "出门"],
        ),
    ]


def build_objects() -> list[WorldObjectItem]:
    return [
        WorldObjectItem(id="fridge", name="冰箱", space_id="kitchen", kind="appliance",
                        interactions=["open", "take", "store"], inventory_key="fridge",
                        tags=["冰箱", "吃的", "喝的", "饮料", "可乐", "布丁", "蛋糕", "橙汁",
                              "甜食", "外卖", "点外卖", "饿", "渴", "吃", "喝"]),
        WorldObjectItem(id="computer", name="电脑", space_id="livingroom", kind="appliance",
                        interactions=["play", "work"],
                        tags=["电脑", "Minecraft", "MC", "游戏", "打游戏", "单机", "零工",
                              "委托", "攻略", "红石", "建城"]),
        WorldObjectItem(id="phone", name="手机", space_id="livingroom", kind="device",
                        interactions=["browse", "chat"],
                        tags=["手机", "群", "群里", "论坛", "聊天", "发消息", "上网", "刷"]),
        WorldObjectItem(id="bed", name="床", space_id="bedroom", kind="furniture",
                        interactions=["sleep"], tags=["床", "睡觉", "睡", "困", "休息"]),
        WorldObjectItem(id="sofa", name="沙发", space_id="livingroom", kind="furniture",
                        interactions=["lie", "sit"], tags=["沙发", "瘫", "窝着", "躺"]),
        WorldObjectItem(id="tv", name="电视", space_id="livingroom", kind="appliance",
                        interactions=["watch"], tags=["电视", "动画", "看剧", "追番", "电影"]),
        WorldObjectItem(id="wardrobe", name="衣柜", space_id="bedroom", kind="furniture",
                        interactions=["change"], inventory_key="wardrobe",
                        tags=["衣柜", "衣服", "睡衣", "猫猫睡衣", "换衣服", "外出装"]),
        WorldObjectItem(id="trash_bag", name="垃圾袋", space_id="entrance", kind="container",
                        interactions=["fill", "take_out"], state={"level": 0.25},
                        tags=["垃圾", "垃圾袋", "扔垃圾", "家务", "收拾"]),
        WorldObjectItem(id="cat_food_bowl", name="猫粮碗", space_id="kitchen", kind="container",
                        interactions=["feed"], inventory_key="cat_food_bowl",
                        tags=["猫粮", "喂猫", "小喵", "猫", "添粮"]),
        WorldObjectItem(id="cat_water_bowl", name="水碗", space_id="kitchen", kind="container",
                        interactions=["refill"], tags=["水碗", "猫喝水", "小喵", "猫"]),
        WorldObjectItem(id="door", name="门", space_id="entrance", kind="fixture",
                        interactions=["leave", "enter"], tags=["门", "出门", "回家", "锁门"]),
        WorldObjectItem(id="package_box", name="快递盒", space_id="entrance", kind="container",
                        interactions=["pick_up"], state={"present": False},
                        tags=["快递", "快递盒", "取件", "包裹", "门口"]),
    ]


def build_inventories(bible: CharacterBible) -> dict[str, Inventory]:
    """Bible inventory seed + code defaults (fridge always has cola & pudding)."""
    inventories = {
        "fridge": Inventory(key="fridge", items={
            "可乐": 10, "布丁": 3, "蛋糕": 1, "橙汁": 2,
        }),
        "cat_food_bowl": Inventory(key="cat_food_bowl", items={"猫粮": 5}),
        "wardrobe": Inventory(key="wardrobe", items={
            "猫猫睡衣": 2, "外出休闲装": 3, "帽子": 1,
        }),
        "character": Inventory(key="character", items={"手机": 1, "猫猫睡衣": 1}),
    }
    # Bible overrides (items mentioned in the bible win).
    for key, items in bible.inventories.items():
        target = inventories.setdefault(key, Inventory(key=key))
        for name, count in items.items():
            target.items[name] = count
    return inventories


def build_needs() -> dict[str, NeedState]:
    needs: dict[str, NeedState] = {}
    for key, level, growth in DEFAULT_NEEDS:
        thresholds = NeedThresholds()
        if key in ("social_need", "entertainment"):
            thresholds = NeedThresholds(soft=0.45, strong=0.65, critical=0.85)
        if key in ("pet_care", "household_maintenance"):
            thresholds = NeedThresholds(soft=0.55, strong=0.75, critical=0.9)
        needs[key] = NeedState(
            key=key, level=level, growth_per_hour=growth, thresholds=thresholds,
        )
    return needs


def build_action_definitions() -> dict[str, ActionDefinition]:
    """The action catalogue (bible-anchored). Deterministic durations (§41)."""
    defs: list[ActionDefinition] = [
        ActionDefinition(
            id="play_minecraft", name="玩Minecraft", spaces=["livingroom"],
            required_objects=["computer"], min_minutes=30, typical_minutes=120, max_minutes=360,
            interruptibility=0.35, priority=0.7, tags=["game", "favorite"],
            need_relief={"entertainment": 0.85, "social_need": 0.15},
            need_cost={"sleepiness": 0.05, "hunger": 0.04, "thirst": 0.04},
            effects={"project:mc_city": 0.03},
            modes=["home", "gaming"],
            detail_pool=["在建城", "在搭红石", "在挖矿", "在扩建图书馆"],
            social_space="minecraft_server",
        ),
        ActionDefinition(
            id="play_singleplayer", name="玩单机游戏", spaces=["livingroom"],
            required_objects=["computer"], min_minutes=30, typical_minutes=90, max_minutes=240,
            interruptibility=0.4, priority=0.6, tags=["game", "favorite"],
            need_relief={"entertainment": 0.8},
            need_cost={"sleepiness": 0.05, "hunger": 0.04, "thirst": 0.04},
            modes=["home", "gaming"],
            detail_pool=["在打Boss", "在刷成就", "在读档重来"],
        ),
        ActionDefinition(
            id="watch_animation", name="看动画", spaces=["livingroom"],
            required_objects=["tv"], min_minutes=25, typical_minutes=60, max_minutes=180,
            interruptibility=0.6, priority=0.5, tags=["entertainment"],
            need_relief={"entertainment": 0.75},
            need_cost={"sleepiness": 0.03},
            modes=["home"],
            detail_pool=["在看新番", "在补旧番", "在看动画电影"],
        ),
        ActionDefinition(
            id="browse_social", name="刷群/论坛", spaces=["livingroom", "bedroom"],
            required_objects=["phone"], min_minutes=10, typical_minutes=30, max_minutes=90,
            interruptibility=0.8, priority=0.5, tags=["social"],
            need_relief={"social_need": 0.8, "entertainment": 0.2},
            modes=["home", "online_social"],
            detail_pool=["在群里吹水", "在刷论坛", "在回消息"],
            social_space="game_group",
        ),
        ActionDefinition(
            id="chat_group", name="在群里聊天", spaces=["livingroom", "bedroom"],
            required_objects=["phone"], min_minutes=5, typical_minutes=20, max_minutes=60,
            interruptibility=0.9, priority=0.45, tags=["social"],
            need_relief={"social_need": 0.6},
            modes=["home", "online_social"],
            detail_pool=["在接梗", "在发表情包", "在跟群友对线"],
            social_space="game_group",
        ),
        ActionDefinition(
            id="eat_pudding", name="吃布丁", spaces=["kitchen"],
            required_objects=["fridge"], min_minutes=3, typical_minutes=8, max_minutes=15,
            interruptibility=0.95, priority=0.5, tags=["food", "favorite"],
            need_relief={"hunger": 0.5}, need_cost={},
            consumes={"fridge": {"布丁": 1}},
            modes=["home"],
            detail_pool=["在吃布丁", "挖了一勺布丁"],
        ),
        ActionDefinition(
            id="eat_cake", name="吃蛋糕", spaces=["kitchen"],
            required_objects=["fridge"], min_minutes=5, typical_minutes=12, max_minutes=25,
            interruptibility=0.95, priority=0.5, tags=["food", "favorite"],
            need_relief={"hunger": 0.65}, consumes={"fridge": {"蛋糕": 1}},
            modes=["home"], detail_pool=["在吃蛋糕"],
        ),
        ActionDefinition(
            id="eat_fruit", name="吃水果", spaces=["kitchen"],
            required_objects=["fridge"], min_minutes=5, typical_minutes=10, max_minutes=20,
            interruptibility=0.95, priority=0.4, tags=["food"],
            need_relief={"hunger": 0.45, "thirst": 0.2},
            consumes={"fridge": {"西瓜": 1}},
            modes=["home"], detail_pool=["在挖西瓜吃", "在吃草莓"],
        ),
        ActionDefinition(
            id="drink_cola", name="喝可乐", spaces=["kitchen"],
            required_objects=["fridge"], min_minutes=2, typical_minutes=5, max_minutes=10,
            interruptibility=0.98, priority=0.5, tags=["drink", "favorite"],
            need_relief={"thirst": 0.75}, consumes={"fridge": {"可乐": 1}},
            modes=["home"], detail_pool=["在喝冰可乐"],
        ),
        ActionDefinition(
            id="feed_cat", name="给小喵添粮", spaces=["kitchen"],
            required_objects=["cat_food_bowl"], min_minutes=2, typical_minutes=4, max_minutes=8,
            interruptibility=1.0, priority=0.6, tags=["pet", "care"],
            need_relief={"pet_care": 0.95}, consumes={"cat_food_bowl": {"猫粮": -1}},
            modes=["home"], detail_pool=["在给小喵添粮"],
        ),
        ActionDefinition(
            id="talk_to_cat", name="跟小喵说话", spaces=["livingroom", "bedroom"],
            min_minutes=1, typical_minutes=5, max_minutes=12,
            interruptibility=1.0, priority=0.3, tags=["pet"],
            need_relief={"pet_care": 0.35, "social_need": 0.1},
            modes=["home"], detail_pool=["在跟小喵说话", "被小喵蹭了"],
        ),
        ActionDefinition(
            id="film_cat", name="拍小喵发群", spaces=["livingroom"],
            required_objects=["phone"], min_minutes=2, typical_minutes=5, max_minutes=10,
            interruptibility=1.0, priority=0.35, tags=["pet", "social"],
            need_relief={"social_need": 0.3, "pet_care": 0.1},
            modes=["home", "online_social"],
            detail_pool=["在拍小喵", "发了一张猫图"],
            social_space="cat_group",
        ),
        ActionDefinition(
            id="take_out_trash", name="扔垃圾", spaces=["entrance", "neighborhood"],
            required_objects=["trash_bag"], min_minutes=5, typical_minutes=10, max_minutes=20,
            interruptibility=0.2, priority=0.55, tags=["chore", "outdoor"],
            need_relief={"household_maintenance": 0.95},
            need_cost={"sleepiness": 0.02},
            effects={"object:trash_bag.level": -1.0},
            modes=["outdoor"], detail_pool=["去扔垃圾"],
        ),
        ActionDefinition(
            id="pick_up_package", name="取快递", spaces=["entrance", "neighborhood"],
            required_objects=["door"], min_minutes=5, typical_minutes=8, max_minutes=15,
            interruptibility=0.3, priority=0.5, tags=["chore", "outdoor"],
            need_relief={"household_maintenance": 0.2},
            effects={"object:package_box.present": 0.0},
            modes=["outdoor"], detail_pool=["去取快递"],
        ),
        ActionDefinition(
            id="buy_cola", name="买可乐", spaces=["convenience_store"],
            min_minutes=8, typical_minutes=15, max_minutes=30,
            interruptibility=0.2, priority=0.6, tags=["shopping", "outdoor"],
            need_relief={"thirst": 0.2},
            effects={"inventory:fridge:可乐": 6, "object:trash_bag.level": 0.05},
            modes=["outdoor"], detail_pool=["在便利店买可乐"],
        ),
        ActionDefinition(
            id="buy_snacks", name="买零食", spaces=["convenience_store"],
            min_minutes=8, typical_minutes=15, max_minutes=30,
            interruptibility=0.2, priority=0.5, tags=["shopping", "outdoor"],
            effects={"inventory:fridge:橙汁": 2},
            modes=["outdoor"], detail_pool=["在便利店补货"],
        ),
        ActionDefinition(
            id="buy_sweets", name="买甜食", spaces=["dessert_shop"],
            min_minutes=10, typical_minutes=20, max_minutes=35,
            interruptibility=0.2, priority=0.65, tags=["shopping", "outdoor", "favorite"],
            need_relief={"hunger": 0.1},
            effects={"inventory:fridge:布丁": 2, "inventory:fridge:蛋糕": 1},
            modes=["outdoor"], detail_pool=["在甜品店挑蛋糕", "在甜品店买布丁"],
        ),
        ActionDefinition(
            id="go_shopping_cola", name="出门买可乐", spaces=["*"],
            min_minutes=25, typical_minutes=40, max_minutes=70,
            interruptibility=0.25, priority=0.7, tags=["shopping", "outdoor", "favorite"],
            need_relief={"thirst": 0.8},
            effects={"inventory:fridge:可乐": 6, "object:trash_bag.level": 0.05},
            modes=["outdoor"], destination="convenience_store",
            requires_absent={"fridge": ["可乐"]},
            detail_pool=["去便利店抱了一箱可乐回来", "在便利店补可乐"],
        ),
        ActionDefinition(
            id="go_shopping_sweets", name="出门买甜食", spaces=["*"],
            min_minutes=30, typical_minutes=50, max_minutes=90,
            interruptibility=0.2, priority=0.7, tags=["shopping", "outdoor", "favorite"],
            need_relief={"hunger": 0.4, "entertainment": 0.2},
            effects={"inventory:fridge:布丁": 2, "inventory:fridge:蛋糕": 1},
            modes=["outdoor"], destination="dessert_shop",
            requires_absent={"fridge": ["布丁", "蛋糕"]},
            detail_pool=["去甜品店挑了半天", "在甜品店买了草莓蛋糕和布丁"],
        ),
        ActionDefinition(
            id="sleep", name="睡觉", spaces=["bedroom"],
            required_objects=["bed"], min_minutes=240, typical_minutes=540, max_minutes=720,
            interruptibility=0.05, priority=0.95, tags=["rest", "core"],
            need_relief={"sleepiness": 1.0, "energy": 1.0},
            modes=["home"], detail_pool=["在睡觉"],
        ),
        ActionDefinition(
            id="nap", name="午睡/补觉", spaces=["bedroom", "livingroom"],
            required_objects=["bed"], min_minutes=40, typical_minutes=120, max_minutes=240,
            interruptibility=0.25, priority=0.6, tags=["rest"],
            need_relief={"sleepiness": 0.6, "energy": 0.5},
            modes=["home"], detail_pool=["在补觉"],
        ),
        ActionDefinition(
            id="shower", name="洗澡", spaces=["bathroom"],
            min_minutes=10, typical_minutes=20, max_minutes=40,
            interruptibility=0.1, priority=0.5, tags=["self-care"],
            need_relief={"hygiene": 0.95},
            modes=["home"], detail_pool=["在洗澡"],
        ),
        ActionDefinition(
            id="tidy_room", name="整理房间", spaces=["livingroom", "bedroom"],
            min_minutes=15, typical_minutes=40, max_minutes=90,
            interruptibility=0.5, priority=0.4, tags=["chore"],
            need_relief={"household_maintenance": 0.5},
            effects={"object:trash_bag.level": 0.15},
            modes=["home"], detail_pool=["在收拾屋子"],
        ),
        ActionDefinition(
            id="work_commission", name="做零工", spaces=["livingroom"],
            required_objects=["computer"], min_minutes=30, typical_minutes=75, max_minutes=180,
            interruptibility=0.5, priority=0.55, tags=["work"],
            need_relief={"work_need": 0.9},
            need_cost={"sleepiness": 0.04},
            effects={"commission:progress": 0.25},
            modes=["home"],
            detail_pool=["在搭委托建筑", "在写攻略稿", "在画插画稿"],
        ),
        ActionDefinition(
            id="idle_on_sofa", name="瘫在沙发上", spaces=["livingroom"],
            required_objects=["sofa"], min_minutes=10, typical_minutes=30, max_minutes=90,
            interruptibility=1.0, priority=0.2, tags=["idle"],
            need_relief={},
            modes=["home"], detail_pool=["瘫在沙发上玩手机", "窝着发呆"],
        ),
        ActionDefinition(
            id="change_to_homewear", name="换猫猫睡衣", spaces=["bedroom", "entrance"],
            required_objects=["wardrobe"], min_minutes=2, typical_minutes=3, max_minutes=6,
            interruptibility=1.0, priority=0.7, tags=["ritual"],
            need_relief={},
            modes=["home"], detail_pool=["换上了猫猫睡衣"],
        ),
        ActionDefinition(
            id="change_to_outdoor", name="换外出装", spaces=["bedroom", "entrance"],
            required_objects=["wardrobe"], min_minutes=3, typical_minutes=5, max_minutes=10,
            interruptibility=1.0, priority=0.7, tags=["ritual", "outdoor"],
            need_relief={},
            modes=["outdoor"], detail_pool=["梳好头发换上休闲装"],
        ),
        ActionDefinition(
            id="think", name="发呆/想事情", spaces=["*"],
            min_minutes=5, typical_minutes=15, max_minutes=45,
            interruptibility=1.0, priority=0.15, tags=["idle"],
            need_relief={},
            modes=["home"], detail_pool=["在发呆", "在想事情"],
        ),
        ActionDefinition(
            id="walk", name="在外面走", spaces=["neighborhood"],
            min_minutes=5, typical_minutes=15, max_minutes=40,
            interruptibility=0.6, priority=0.3, tags=["outdoor"],
            need_relief={"entertainment": 0.1},
            modes=["outdoor"], detail_pool=["在小区里走"],
        ),
        ActionDefinition(
            id="browse_forum", name="刷游戏论坛", spaces=["livingroom", "bedroom"],
            required_objects=["phone"], min_minutes=10, typical_minutes=25, max_minutes=60,
            interruptibility=0.85, priority=0.4, tags=["social"],
            need_relief={"social_need": 0.5, "entertainment": 0.25},
            modes=["home", "online_social"],
            detail_pool=["在刷论坛", "在回求助帖"],
            social_space="game_forum",
        ),
        ActionDefinition(
            id="idle", name="闲着", spaces=["*"],
            min_minutes=5, typical_minutes=15, max_minutes=40,
            interruptibility=1.0, priority=0.1, tags=["idle"],
            modes=["home"], detail_pool=["闲着呢"],
        ),
    ]
    return {item.id: item for item in defs}


def build_social_spaces(bible: CharacterBible) -> dict[str, SocialSpace]:
    """Bible's online hangouts (§58-§62): QQ groups map onto real ids later."""
    spaces: dict[str, SocialSpace] = {
        "game_group": SocialSpace(
            id="game_group", name="游戏群", kind="simulated", topic="游戏/聊天",
            interest=0.8, social_temperature=0.6,
        ),
        "minecraft_server": SocialSpace(
            id="minecraft_server", name="Minecraft服务器", kind="simulated",
            topic="建筑/服务器日常", interest=0.9, social_temperature=0.5,
        ),
        "dessert_group": SocialSpace(
            id="dessert_group", name="甜品同好群", kind="simulated", topic="甜食分享",
            interest=0.6, social_temperature=0.4,
        ),
        "cat_group": SocialSpace(
            id="cat_group", name="猫图群", kind="simulated", topic="猫图",
            interest=0.8, social_temperature=0.5,
        ),
        "game_forum": SocialSpace(
            id="game_forum", name="单机游戏论坛", kind="simulated", topic="攻略/讨论",
            interest=0.6, social_temperature=0.3,
        ),
    }
    return spaces


def seeded_rng(seed: int) -> random.Random:
    """§123: simulation randomness always runs on an explicit seed."""
    return random.Random(seed)


def build_projects() -> dict[str, dict[str, Any]]:
    """Long-running projects from the bible (MC city)."""
    return {
        "mc_city": {
            "name": "Minecraft小城",
            "progress": 0.35,
            "next_action": "把图书馆的屋顶搭完",
            "started_at": 0.0,
        }
    }
