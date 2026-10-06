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
        "查一个目标物品能做的配方：每个配方给出 recipe_id、产物数量、材料清单，"
        "以及材料现在够不够。不给 crafting_table 就只查**罐头自己 2×2 背包**能做的；"
        "给出明确的工作台坐标就查那张工作台的 **3×3**（不会自己去找工作台）。"
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
        "切石机/锻造台/织布机/制图台这类「工作台」本阶段都不支持（只认 crafting_table）；"
        "熔炉、酿造、村民交易也不支持。"
    ),
    limitations=(
        "2×2 = 玩家自身背包；3×3 = 必须由调用方给出**明确的工作台坐标**；"
        "只回报配方与材料是否够，不会自动准备材料、不会做中间材料（recipe chain）、"
        "不会去找/走过去/放一个工作台；一次只查一个物品。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "item": {
                "type": "string",
                "minLength": 1,
                "description": "目标物品名（如 stick；也接受 minecraft:stick）",
            },
            "crafting_table": {
                "type": "object",
                "description": (
                    "工作台方块的整数坐标；**不给** = 用罐头自己的 2×2 背包合成，"
                    "**给了** = 用你明确指定的这一张工作台的 3×3（不接受 nearest/auto，"
                    "也不会自己去找/走过去/放一个工作台）"
                ),
                "properties": {
                    "x": {"type": "integer", "description": "工作台的整数 X 坐标"},
                    "y": {"type": "integer", "description": "工作台的整数 Y 坐标"},
                    "z": {"type": "integer", "description": "工作台的整数 Z 坐标"},
                },
                "required": ["x", "y", "z"],
                "additionalProperties": False,
            },
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
        return await service.recipe_lookup(arguments.get("item"), arguments.get("crafting_table"))
