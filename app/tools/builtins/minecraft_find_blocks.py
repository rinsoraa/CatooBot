"""Builtin tool: minecraft_find_blocks —— 找附近有哪些指定方块（Phase 4K）。

边界（任务书 §三/§五/§六/§十五/§十七）：
* **只读 SAFE**：不移动、不装备、不挖、不拾取、不改世界、不改背包；
* 只回答"目标方块在哪里"，**绝不回答"哪一个最适合挖"**（没有 recommended / best / optimal）——
  下一步该问 `minecraft_dig_capability`；
* 起点永远是**罐头当前所在位置**（不接受坐标起点，避免执行任意远程扫描）；
* 只接受**明确的方块名**（`minecraft:iron_ore` / `iron_ore` 都行，会规范成裸名）；
  第一版**不支持** `#ores` 这种 block tag；
* 范围与条数都有硬上限（默认 16 格 / 8 条，最大 32 格 / 16 条）；
* 只反映**当前已加载**的范围：不会为了搜索去移动、也不会声称"整个世界都没有"。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

FIND_BLOCKS_METADATA = ToolMetadata(
    name="minecraft_find_blocks",
    display_name="Minecraft Find Blocks",
    description=(
        "只读查询：在罐头**当前所在位置**附近（默认 16 格内）找指定名称的方块，"
        "返回最多 8 条匹配（方块名 + 坐标 + 两种口径的距离），按距离稳定排序。"
        "只定位，不移动、不装备、不挖、不拾取；也不判断哪一个最适合挖。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "block", "search", "read-only"],
    keywords=[
        "附近有什么",
        "哪里有",
        "找一下",
        "附近有没有",
        "周围有",
        "矿在哪",
        "树在哪",
        "minecraft",
    ],
    when_to_use=(
        "用户问「附近有没有/哪里有某个方块」时（例如「附近有橡木吗」），"
        "或者准备挖某个资源、需要先知道它在哪时：先 find_blocks 拿到候选坐标，"
        "再用 minecraft_dig_capability 确认能不能挖，然后 move_to / dig / pickup。"
    ),
    when_not_to_use=(
        "不要用它代替能力判断（那是 minecraft_dig_capability）；"
        "不要指望它自己移动或挖掘（它什么都不会做）；"
        "第一版也不支持方块标签（#ores 这种）。"
    ),
    limitations=(
        "只查当前**已加载**的范围（不会为了搜索去移动，也不代表整个世界）；"
        "最多 8 个方块名、最多 16 条结果、半径最大 32 格；"
        "只给位置与距离，不给推荐、不管可达性与工具是否合适；"
        "不认识的方块名会报 minecraft.block_name_unknown（不会假装「附近没有」）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "block_names": {
                "type": "array",
                "minItems": 1,
                "maxItems": 8,
                "items": {"type": "string", "minLength": 1},
                "description": ("要找的方块名（1~8 个；minecraft:oak_log 与 oak_log 都可以）"),
            },
            "max_distance": {
                "type": "integer",
                "minimum": 1,
                "maximum": 32,
                "description": "搜索半径（格），默认 16，最大 32",
            },
            "max_results": {
                "type": "integer",
                "minimum": 1,
                "maximum": 16,
                "description": "最多返回几条，默认 8，最大 16",
            },
        },
        "required": ["block_names"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftFindBlocksTool(ActionTool):
    """只读找方块：判定（SAFE / 非独占）→ Service → 语义投影。"""

    metadata = FIND_BLOCKS_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.find_blocks(
            arguments.get("block_names"),
            arguments.get("max_distance"),
            arguments.get("max_results"),
        )
