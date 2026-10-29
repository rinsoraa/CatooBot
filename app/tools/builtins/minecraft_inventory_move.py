"""Builtin tool: minecraft_inventory_move —— 单物品、单槽位的背包搬运（Phase 4D）。

MEDIUM 风险 → 必须用户确认。第一版严格限定：**一个物品、一个来源槽、一个目标槽、一个数量**；
目标被别的物品占用时**拒绝**（绝不隐式交换），不批量整理、不自动找"最优"槽位、不自动补货。

槽位是玩家窗口的绝对槽位（主背包 9–35 + 快捷栏 36–44，与 ``minecraft_inventory``
的聚合视图无关——只读工具**不会**暴露原始槽位）。所以这个工具通常用于：用户明确报出槽位
（例如「把 37 格的东西挪到 9 格」）或 WebUI 调试面板里照着槽位表操作；不确定槽位时不要猜。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import (
    PLAYER_SLOT_MAX,
    PLAYER_SLOT_MIN,
    MinecraftService,
)
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

INVENTORY_MOVE_METADATA = ToolMetadata(
    name="minecraft_inventory_move",
    display_name="Minecraft Inventory Move",
    description=(
        "把罐头背包里**某一个明确槽位**上的指定物品，移动指定数量到**另一个明确槽位**"
        f"（槽位范围 {PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX}：主背包 + 快捷栏；"
        "MEDIUM 风险，需要用户确认）。"
        "目标槽位被别的物品占用时会拒绝，不会交换。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "inventory", "move"],
    keywords=["移到", "挪到", "放到第", "换到别的格", "整理一格", "背包格", "槽位", "minecraft"],
    when_to_use=(
        "用户明确要求把某个槽位上的东西挪到另一个槽位时（例如「把第 37 格的泥土挪到第 9 格」）。"
        "槽位号必须明确；不知道槽位时不要猜。"
    ),
    when_not_to_use=(
        "只是想把物品拿到手上——用 minecraft_equip（按名字，不需要槽位）；"
        "整理整个背包 / 自动排序 / 搬多个 stack——本阶段没有这些能力。"
    ),
    limitations=(
        "一次只搬一个物品、一个来源槽、一个目标槽、一个数量；"
        "目标槽位非空且不是同名可堆叠的物品时会被拒绝（绝不隐式交换）；"
        "不批量整理、不自动找最优槽位、不操作容器（箱子/熔炉等属于后续阶段）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "source_slot": {
                "type": "integer",
                "minimum": PLAYER_SLOT_MIN,
                "maximum": PLAYER_SLOT_MAX,
                "description": f"来源槽位（{PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX}）",
            },
            "destination_slot": {
                "type": "integer",
                "minimum": PLAYER_SLOT_MIN,
                "maximum": PLAYER_SLOT_MAX,
                "description": f"目标槽位（{PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX}）",
            },
            "item": {
                "type": "string",
                "minLength": 1,
                "description": "来源槽位上的物品名（必须与之一致，如 dirt）",
            },
            "count": {"type": "integer", "minimum": 1, "description": "要移动的数量（>= 1）"},
        },
        "required": ["source_slot", "destination_slot", "item", "count"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftInventoryMoveTool(ActionTool):
    """单物品单槽位搬运：判定（含确认门）→ Service → 结构化结果。"""

    metadata = INVENTORY_MOVE_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.inventory_move(
            arguments.get("source_slot"),
            arguments.get("destination_slot"),
            arguments.get("item"),
            arguments.get("count"),
        )
