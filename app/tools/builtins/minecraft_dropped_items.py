"""Builtin tool: minecraft_dropped_items —— 看附近有哪些掉落物实体（Phase 4H）。

边界（任务书 §四/§六/§八）：
* **只读**：只列 Item Entity（玩家/动物/怪物/箭/经验球/载具/投射物一律不进列表）；
* 只给语义字段：`entity_id` / `item{name,count}` / `position` / `distance`
  —— 不泄露 raw metadata / packet / 内部数字 id / entity 对象 / UUID / velocity；
* SAFE 且**非独占**（可与导航、挖、放、合成并行），但仍需在线；
* 按距离升序（其次 entity_id 升序）稳定排序，最多 32 条 + `truncated` 标记。

它是 `minecraft_pickup_item` 的前置：模型要先拿到 `entity_id` 与物品名，才能给出
"捡**哪一个**"的明确参数。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

DROPPED_ITEMS_METADATA = ToolMetadata(
    name="minecraft_dropped_items",
    display_name="Minecraft Dropped Items",
    description=(
        "只读查看罐头**附近地上的掉落物实体**：每一条给出 entity_id、物品名与数量、坐标与距离。"
        "只会列掉落物（不列玩家/怪物/动物/箭/经验球），按距离从近到远排序，最多 32 条。"
        "不会捡任何东西。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "entity", "item", "perception"],
    keywords=[
        "地上的东西",
        "掉落物",
        "掉了什么",
        "地上有",
        "捡的东西在哪",
        "附近有东西",
        "minecraft",
    ],
    when_to_use=(
        "用户问「地上有什么 / 刚才挖的东西在哪」，或者要捡东西之前"
        "（先用它拿到 entity_id 与物品名，再决定要不要 minecraft_pickup_item）。"
    ),
    when_not_to_use=(
        "要真正捡起来用 minecraft_pickup_item；要看背包用 minecraft_inventory；"
        "要看玩家/怪物/动物这些**非掉落物**实体本阶段不提供。"
    ),
    limitations=(
        "只列掉落物实体；只给 entity_id/物品/坐标/距离四个语义字段；"
        "最多 32 条（超出会标 truncated）；不会自动捡、不会自动挖、不会自动带货。"
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftDroppedItemsTool(ActionTool):
    """只读看地上的掉落物：判定（SAFE / 非独占）→ Service → 语义投影。"""

    metadata = DROPPED_ITEMS_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.dropped_items()
