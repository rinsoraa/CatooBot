"""Builtin tool: minecraft_pickup_item —— 走过去捡起**一个**掉落物实体（Phase 4H）。

边界（任务书 §二/§十三/§十四/§十八/§二十/§二十六）：
* 只捡**明确指定的那一个**实体（entity_id + expected_item 双重约束）：
  不捡附近所有掉落物、不自动 loot、不自动扫地、不自动挖、不自动杀怪、不自动开箱；
* MEDIUM → 必须用户确认（USER 回合 + 可信玩家 + allow_medium + 一次性确认）；
* 有限导航：最多 `pickup.max_distance` 格（默认 16），目标被拉远就直接失败，
  **绝不**为了捡东西挖墙/垫方块/搭桥/开门（与非破坏导航同一套 Movement）；
* 成功判据是硬的：目标实体真的被收集 **且** 背包对应物品数量增加
  （只看"实体消失"不算成功 —— 可能被别人捡走或掉进未加载区块）。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

PICKUP_ITEM_METADATA = ToolMetadata(
    name="minecraft_pickup_item",
    display_name="Minecraft Pickup Item",
    description=(
        "让罐头走过去，把**一个明确指定**的地上掉落物捡起来"
        "（entity_id + expected_item 两个参数都必填；MEDIUM 风险，需要用户确认）。"
        "只捡这一个实体：不会顺手捡别的、不会挖、不会自动清理地面。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "pickup", "entity", "inventory"],
    keywords=["捡起来", "捡一下", "把地上那个捡", "过去捡", "拾取", "拿走地上的", "minecraft"],
    when_to_use=(
        "用户明确要求捡起地上的某个掉落物时：先用 minecraft_dropped_items 看清 entity_id "
        "与物品名，再把这两个值一起交给本工具。"
    ),
    when_not_to_use=(
        "还没看清地上有什么 → 先用 minecraft_dropped_items；"
        "「把附近都捡了 / 自动扫地 / 自动收割」本阶段不支持（一次只捡一个明确实体）；"
        "要挖方块用 minecraft_dig。"
    ),
    limitations=(
        "一次只捡一个明确实体（entity_id + expected_item 双重约束）；"
        "目标超过 max_distance 就直接失败，不会追太远；不会为了到达目标挖/放方块或开门；"
        "只有「真的进了背包」才算成功（实体消失但背包没增加会报 pickup_unconfirmed）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {
                "type": "integer",
                "minimum": 0,
                "description": "minecraft_dropped_items 给出的 entity_id（地上的那一个）",
            },
            "expected_item": {
                "type": "string",
                "minLength": 1,
                "description": "第二层身份校验：那个掉落物必须真的是这个物品（如 dirt）",
            },
        },
        "required": ["entity_id", "expected_item"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftPickupItemTool(ActionTool):
    """捡起一个明确实体：判定（含 MEDIUM 确认门）→ Service → 结构化结果。"""

    metadata = PICKUP_ITEM_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.pickup_item(arguments.get("entity_id"), arguments.get("expected_item"))
