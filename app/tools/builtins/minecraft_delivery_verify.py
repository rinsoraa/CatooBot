"""Builtin tool: minecraft_delivery_verify (R6) — verify DROPPED state."""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

DELIVERY_VERIFY_METADATA = ToolMetadata(
    name="minecraft_delivery_verify",
    display_name="Minecraft Delivery Verify",
    description=(
        "丢出后重新读取掉落物实体，确认 DROPPED 状态。"
        "不把动作回执当成对方收到，也不宣告 PICKED_UP。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "delivery", "verify", "safe"],
    keywords=["确认丢出", "交付核验", "掉落物", "minecraft"],
    when_to_use="交付计划的最后一步，确认掉落物实体仍存在。",
    when_not_to_use="不要用它代替目标拾取证据。",
    limitations="只确认 DROPPED；PICKED_UP 需要目标玩家的真实拾取证据。",
    input_schema={
        "type": "object",
        "properties": {
            "item": {"type": "string", "minLength": 1},
            "count": {"type": "integer", "minimum": 1, "maximum": 64},
            "entity_id": {"type": "integer", "minimum": 0},
            "expected_uuid": {"type": "string"},
        },
        "required": ["item", "count"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftDeliveryVerifyTool(ActionTool):
    metadata = DELIVERY_VERIFY_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.delivery_verify(
            arguments.get("item"),
            arguments.get("count"),
            arguments.get("entity_id"),
            arguments.get("expected_uuid"),
        )
