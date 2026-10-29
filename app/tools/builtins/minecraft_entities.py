"""Builtin tool: minecraft_entities (R5) — read nearby living entities (SAFE)."""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

ENTITIES_METADATA = ToolMetadata(
    name="minecraft_entities",
    display_name="Minecraft Entities",
    description=(
        "只读附近 16 格内的活体实体（entity_id/UUID/类型/位置/距离/生命/是否玩家）。"
        "不移动、不攻击、不改变世界；只用于确认目标身份。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "entity", "observe", "safe"],
    keywords=["附近有什么", "看看怪物", "实体", "僵尸", "蜘蛛", "minecraft"],
    when_to_use="需要确认附近活体实体的身份、距离和 UUID 时。",
    when_not_to_use="不要用它代替攻击；攻击前仍要经过独立确认与 runtime 复检。",
    limitations="只返回 16 格内、最多 32 个实体；不返回玩家以外的隐藏/未加载实体。",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftEntitiesTool(ActionTool):
    metadata = ENTITIES_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.entities()
