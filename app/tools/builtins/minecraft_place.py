"""Builtin tool: minecraft_place —— 放置**一个**明确指定的方块（Phase 4C）。

Phase 4B `minecraft_dig` 的对称实现：MEDIUM 风险 → 必须用户确认；参数里
``expected_item`` 是**当前主手物品的硬约束**（不是"我希望最后出现什么"），
执行前 runtime 会用实时状态复检（主手物品 / 目标是否空气 / 参考方块 / 距离）。

本阶段只放一块：不导航、不找放置面、不自动找 reference block、不换 hotbar、不 equip、
不补货、不连续建造。工具自己只做「判定 → Service → 结构化结果」。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

PLACE_METADATA = ToolMetadata(
    name="minecraft_place",
    display_name="Minecraft Place",
    description=(
        "在指定坐标放置**一个**方块（真实修改世界；MEDIUM 风险，需要用户确认）。"
        "expected_item 必须是罐头**当前主手**拿着的物品名，且目标位置必须是空气、"
        "相邻的参考方块必须存在。每次最多放一个方块。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "place", "world-change"],
    keywords=["放一个", "放个", "放块", "放下", "放上", "摆一个", "贴上", "放置", "minecraft"],
    when_to_use=(
        "用户明确要求在某个位置放一个方块时（例如「在脚下放一块泥土」）。"
        "先用 minecraft_world 确认目标坐标与参考方块、用 minecraft_inventory 确认主手物品，"
        "再调用本工具；第一次调用会返回需要确认，用户明确确认后用**完全相同的参数**再调用一次。"
    ),
    when_not_to_use=(
        "要挖方块用 minecraft_dig；要移动用 minecraft_move_to；"
        "连续建造/自动补货/自动找位置——本阶段没有这些能力。"
    ),
    limitations=(
        "一次只放一个方块；只往**空气**里放（不会覆盖草/水/雪等）；"
        "只用当前主手的物品（不会自动装备或切换快捷栏，手里没有就失败）；"
        "参考方块必须存在（就是 target 沿 face 反方向那一格）；目标必须在身边几格内；"
        "放置结果以真实回执为准（未确认放置会报 block_place_unconfirmed）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "目标方块 X 坐标（整数）"},
            "y": {"type": "integer", "description": "目标方块 Y 坐标（整数）"},
            "z": {"type": "integer", "description": "目标方块 Z 坐标（整数）"},
            "face": {
                "type": "string",
                "enum": ["up", "down", "north", "south", "east", "west"],
                "description": (
                    "贴哪个面放：参考方块 = 目标沿该方向的反方向一格（如 up → 放在参考方块上方）"
                ),
            },
            "expected_item": {
                "type": "string",
                "minLength": 1,
                "description": "主手必须拿着的物品名（与 minecraft_inventory 看到的一致，如 dirt）",
            },
        },
        "required": ["x", "y", "z", "face", "expected_item"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftPlaceTool(ActionTool):
    """放一个方块：判定（含确认门）→ Service → 结构化结果。"""

    metadata = PLACE_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.place(
            arguments.get("x"),
            arguments.get("y"),
            arguments.get("z"),
            arguments.get("face"),
            arguments.get("expected_item"),
        )
