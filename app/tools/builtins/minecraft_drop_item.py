"""Builtin tool: minecraft_drop_item (R6) — one explicit main-hand drop (MEDIUM)."""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

DROP_METADATA = ToolMetadata(
    name="minecraft_drop_item",
    display_name="Minecraft Drop Item",
    description=(
        "从主手丢出明确数量的一种物品（MEDIUM，需要用户确认）。"
        "不自动装备、不自动捡回、不批量清空背包。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "drop", "item", "delivery", "medium"],
    keywords=["给", "丢给", "交给", "丢出", "扔给", "minecraft"],
    when_to_use="用户明确要求把主手物品丢给已确认的玩家时。",
    when_not_to_use="不要用本工具批量丢弃、自动交易或丢给未确认身份的对象。",
    limitations="一次只丢一种主手物品、1–64 个；不负责拾取或确认对方收到。",
    input_schema={
        "type": "object",
        "properties": {
            "item": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "minimum": 1, "maximum": 64},
        },
        "required": ["item", "count"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="medium",
)


class MinecraftDropItemTool(ActionTool):
    metadata = DROP_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.drop_item(arguments.get("item"), arguments.get("count"))
