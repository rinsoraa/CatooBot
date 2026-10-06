"""Builtin tool: minecraft_dig_capability —— 只读回答「这个方块现在能不能挖、大概多久」（4J）。

边界（任务书 §二/§五/§十七/§十八）：
* **只读 SAFE**：绝不改世界、不改背包、不装备、不切槽、**不移动、不导航、不挖**；
* 事实来源是 Minecraft 运行时的 ``bot.blockAt`` / ``bot.heldItem`` / ``bot.canDigBlock`` /
  ``bot.digTime`` —— 工具本身不碰 Mineflayer，路径仍然是 Tool → Agent Bridge → Policy → Service；
* 只查**一个明确方块**（整数坐标），**不接收** block name（查的就是那个位置真实的东西），
  也**不接收** expected_tool（问的是「我现在真实拿着的东西」能不能挖，不是「假设我有某把工具」）；
* **不给任何推荐**（没有 recommended_tool / best_tool）—— 要不要换工具由模型/用户决定，
  换工具走独立的 ``minecraft_equip``。

它是 ``minecraft_dig`` 的前置：先问「现在挖得动吗、大概多久」，再决定要不要先 equip。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

DIG_CAPABILITY_METADATA = ToolMetadata(
    name="minecraft_dig_capability",
    display_name="Minecraft Dig Capability",
    description=(
        "只读查询：罐头**现在站着的位置、现在的主手**，对指定方块（整数坐标）能不能挖、"
        "预计需要多少毫秒。返回 block / held_item / 两种口径的距离 / can_dig / "
        "dig_time_ms / reason。"
        "不会换工具、不会导航、不会挖——只回答事实。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "dig", "capability", "tool", "read-only"],
    keywords=[
        "能不能挖",
        "挖得动吗",
        "挖不动",
        "要挖多久",
        "挖这个要多久",
        "工具够不够",
        "minecraft",
    ],
    when_to_use=(
        "准备挖一个方块之前（尤其是用户点名了某个方块或矿物时）：先问一次"
        "「现在这样挖得动吗、大概多久」，再决定直接 minecraft_dig，还是先 minecraft_equip 换工具。"
    ),
    when_not_to_use=(
        "要真的挖用 minecraft_dig；要看背包里有什么用 minecraft_inventory；"
        "本工具**不推荐**该用哪把工具，也不替你装备。"
    ),
    limitations=(
        "只读一次当前状态（不缓存、不预测）；只回答能不能挖与预计耗时，不判断「哪种工具更好」、"
        "不管耐久、不做资源规划、不会自动换工具/导航/挖掘；"
        "目标必须是加载范围内真实存在的方块（没有就是 block_unavailable）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "目标方块 X 坐标（整数）"},
            "y": {"type": "integer", "description": "目标方块 Y 坐标（整数）"},
            "z": {"type": "integer", "description": "目标方块 Z 坐标（整数）"},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftDigCapabilityTool(ActionTool):
    """只读查挖掘能力：判定（SAFE / 非独占）→ Service → 语义投影。"""

    metadata = DIG_CAPABILITY_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.dig_capability(
            arguments.get("x"), arguments.get("y"), arguments.get("z")
        )
