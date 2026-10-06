"""Builtin tool: minecraft_equip —— 把背包里指定的物品拿到主手（Phase 4D）。

MEDIUM 风险 → 必须用户确认（与 dig/place 同一套确认门）。第一版只支持 `destination=hand`：
不碰盔甲/副手、不自动换槽、不挑"更方便"的 stack（runtime 按**槽位稳定顺序**取第一个匹配的物品）、
不自动补货。它是 `minecraft_place` 的前置：`expected_item` 必须与当前主手一致。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

EQUIP_METADATA = ToolMetadata(
    name="minecraft_equip",
    display_name="Minecraft Equip",
    description=(
        "把罐头背包里**已经存在**的指定物品拿到主手"
        "（会改变手持状态；MEDIUM 风险，需要用户确认）。"
        "只按物品名查找，不指定槽位；同名有多个 stack 时按槽位顺序取第一个。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "inventory", "equip"],
    keywords=["拿到手里", "拿在手上", "换个手", "换成", "手持", "拿一下", "装备", "minecraft"],
    when_to_use=(
        "用户要求罐头把某个物品拿到手上时（例如「把泥土拿到手里」），"
        "或者准备放方块前需要先确认主手物品时。先调用 minecraft_inventory 看清背包里有什么。"
    ),
    when_not_to_use=(
        "背包里没有这个物品时（会失败，先如实告诉用户）；"
        "要整理/搬运槽位用 minecraft_inventory_move；要放方块用 minecraft_place。"
    ),
    limitations=(
        "只能拿到**主手**（不支持盔甲/副手）；不会自动装备、不会切换快捷栏、"
        "不会挑「更合适」的 stack；"
        "一次只拿一个物品；背包里没有该物品就失败。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "item": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "物品名（与 minecraft_inventory 里看到的一致，如 dirt；也接受 minecraft:dirt）"
                ),
            }
        },
        "required": ["item"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftEquipTool(ActionTool):
    """把物品拿到主手：判定（含确认门）→ Service → 结构化结果。"""

    metadata = EQUIP_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.equip(arguments.get("item"))
