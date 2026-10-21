"""Builtin tool: minecraft_combat_verify (R5) — verify frozen entity is gone."""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

COMBAT_VERIFY_METADATA = ToolMetadata(
    name="minecraft_combat_verify",
    display_name="Minecraft Combat Verify",
    description=(
        "在有限攻击后重新读取附近实体，确认冻结目标真的消失。"
        "目标仍存在则本步失败；不执行任何攻击或世界修改。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "combat", "verify", "safe"],
    keywords=["确认击杀", "目标还在吗", "战斗核验", "minecraft"],
    when_to_use="战斗计划最后一步，用于把动作回执与真实实体消失分开验证。",
    when_not_to_use="不要用它代替攻击；不要用它宣告未观察到的死亡。",
    limitations="只按 entity_id + UUID/类型核对附近实体；超时仍存在就失败。",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {"type": "integer", "minimum": 0},
            "expected_type": {"type": "string", "minLength": 1},
            "expected_uuid": {"type": "string", "minLength": 1},
        },
        "required": ["entity_id", "expected_type"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftCombatVerifyTool(ActionTool):
    metadata = COMBAT_VERIFY_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.combat_verify(
            arguments.get("entity_id"),
            arguments.get("expected_type"),
            expected_uuid=arguments.get("expected_uuid"),
        )
