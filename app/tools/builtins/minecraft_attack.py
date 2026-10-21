"""Builtin tool: minecraft_attack (R5) — exactly one hit on one frozen entity (MEDIUM)."""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

ATTACK_METADATA = ToolMetadata(
    name="minecraft_attack",
    display_name="Minecraft Attack",
    description=(
        "对**一个已冻结**的敌对实体攻击一次（MEDIUM，需要用户确认）。"
        "必须给出 entity_id、expected_type 与可选 expected_uuid；不导航、不追远、不自动换武器。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "attack", "combat", "medium"],
    keywords=["攻击", "打", "击杀", "怪物", "僵尸", "蜘蛛", "minecraft"],
    when_to_use="用户明确要求攻击白名单敌对实体，且已用 minecraft_entities 确认目标身份时。",
    when_not_to_use="玩家/村民/宠物/未知实体禁止攻击；不要用本工具无限连击或自动寻找目标。",
    limitations="一次只打一下；目标超出 4 格或身份变化立即失败；不自动装备/补货/追击。",
    input_schema={
        "type": "object",
        "properties": {
            "entity_id": {"type": "integer", "minimum": 0},
            "expected_type": {"type": "string", "minLength": 1},
            "expected_uuid": {"type": "string", "minLength": 1},
            "max_distance": {"type": "number", "minimum": 0.1, "maximum": 4},
        },
        "required": ["entity_id", "expected_type"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="medium",
)


class MinecraftAttackTool(ActionTool):
    metadata = ATTACK_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.attack(
            arguments.get("entity_id"),
            arguments.get("expected_type"),
            expected_uuid=arguments.get("expected_uuid"),
            max_distance=arguments.get("max_distance"),
        )
