"""Builtin tool: minecraft_recipe_lookup —— 查玩家 2×2 背包能做的配方（Phase 4F）。

边界（任务书 §二/§四/§五/§九/§十）：
* 只查**玩家自身 2×2**（`craftingTable = null`）能执行的配方——工作台配方只回报
  「需要工作台」，**绝不自动去找 / 走过去 / 放一个工作台**；
* 只读语义投影：`recipe_id` / `result` / `requires_table` / `available` / `ingredients`，
  绝不返回 raw Recipe / 数字 id / metadata；
* SAFE 且**非独占**（可与导航、背包读并行），但仍需在线；
* `available` 是按**当前真实背包**算出来的，所以它只是建议；真正执行时 `minecraft_craft`
  还会用当时的背包重算一遍。

`recipe_id` 是稳定的可读签名（例如 `stick*4=oak_planks*2`）——`minecraft_craft` 只接受它。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

RECIPE_LOOKUP_METADATA = ToolMetadata(
    name="minecraft_recipe_lookup",
    display_name="Minecraft Recipe Lookup",
    description=(
        "查一个目标物品在**罐头自己 2×2 背包**里能做的配方：每个配方给出 recipe_id、"
        "产物数量、材料清单，以及材料现在够不够。只支持玩家 2×2 合成（不需要工作台的配方）；"
        "只读，不消耗任何东西。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "craft", "recipe", "perception"],
    keywords=[
        "能做什么",
        "怎么做",
        "配方",
        "合成表",
        "需要什么材料",
        "能做几个",
        "crafting",
        "minecraft",
    ],
    when_to_use=(
        "用户问「这个能不能做 / 需要什么材料」，或者准备合成之前（先用它拿到 recipe_id 和材料清单，"
        "看清楚材料够不够，再决定要不要 craft）。"
    ),
    when_not_to_use=(
        "要真正合成用 minecraft_craft（一次一个配方）；要看背包里有什么用 minecraft_inventory；"
        "工作台配方、熔炉、酿造、村民交易本阶段都不支持。"
    ),
    limitations=(
        "只支持玩家自身 2×2 背包合成；只回报配方与材料是否够，不会自动准备材料、"
        "不会做中间材料（recipe chain）、不会去找工作台；一次只查一个物品。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "item": {
                "type": "string",
                "minLength": 1,
                "description": "目标物品名（如 stick；也接受 minecraft:stick）",
            }
        },
        "required": ["item"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftRecipeLookupTool(ActionTool):
    """只读查配方：判定（SAFE / 非独占）→ Service → 语义投影。"""

    metadata = RECIPE_LOOKUP_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.recipe_lookup(arguments.get("item"))
