"""Builtin tool: minecraft_container_transfer —— 单物品在容器槽 ↔ 背包槽之间搬一次（Phase 4E）。

边界（任务书 §十六-§二十五）：
* MEDIUM → 必须用户确认（与 dig/place/equip/inventory_move 同一套确认门）；
* **一个**方向、**一个** container_slot、**一个** inventory_slot、**一个** item、**一个** count；
* 目标槽被别的物品占用 → 拒绝（绝不交换、绝不换槽、绝不"帮你找个空位"）；
* 只支持单方块 Chest / Barrel；不批量整理、不自动补货、不箱对箱搬运；
* 参数必须精确：先 ``minecraft_container_inspect`` 看清第几格有什么，再给出参数——
  绝不允许"拿一些泥土出来"这种模糊语义。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import (
    CONTAINER_DIRECTIONS,
    PLAYER_SLOT_MAX,
    PLAYER_SLOT_MIN,
    MinecraftService,
)
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

CONTAINER_TRANSFER_METADATA = ToolMetadata(
    name="minecraft_container_transfer",
    display_name="Minecraft Container Transfer",
    description=(
        "把一个明确的物品，在一个明确的箱子/桶格子与一个明确的自己背包格子之间移动一次"
        f"（inventory_slot 范围 {PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX}；"
        "direction=withdraw 是拿出来，deposit 是放进去）。MEDIUM 风险，需要用户确认。"
        "只支持单方块 Chest / Barrel。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "container", "chest", "transfer"],
    keywords=[
        "从箱子里拿",
        "拿点东西出来",
        "放进箱子",
        "存进箱子",
        "把…放回去",
        "取出来",
        "箱子拿",
        "minecraft",
    ],
    when_to_use=(
        "用户明确要求把某个箱子/桶里**某一格**的东西拿到背包的**某一格**（withdraw），"
        "或者把背包**某一格**的东西放进箱子的**某一格**（deposit）时。"
        "先用 minecraft_container_inspect 看清 container_slot，再给精确参数。"
    ),
    when_not_to_use=(
        "只是想把物品拿到手上——用 minecraft_equip；想在自己背包里换格——用 "
        "minecraft_inventory_move；双箱、潜影盒、熔炉、漏斗、箱子对箱子搬运本阶段都不支持；"
        "「拿一些泥土出来」「整理一下箱子」这种模糊要求不要猜，先 inspect 或问清槽位。"
    ),
    limitations=(
        "一次只搬一个物品、一个容器槽、一个背包槽、一个数量；目标槽非空且不同名时直接拒绝；"
        "只支持单方块 Chest / Barrel；不批量整理、不自动补货、不操作未知容器。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "容器方块的整数 X 坐标"},
            "y": {"type": "integer", "description": "容器方块的整数 Y 坐标"},
            "z": {"type": "integer", "description": "容器方块的整数 Z 坐标"},
            "direction": {
                "type": "string",
                "enum": list(CONTAINER_DIRECTIONS),
                "description": "withdraw = 从箱子拿到背包；deposit = 从背包放进箱子",
            },
            "container_slot": {
                "type": "integer",
                "minimum": 0,
                "description": "容器里的格子编号（单方块箱子是 0~26；见 inspect 的 slot）",
            },
            "inventory_slot": {
                "type": "integer",
                "minimum": PLAYER_SLOT_MIN,
                "maximum": PLAYER_SLOT_MAX,
                "description": f"自己背包的格子编号（{PLAYER_SLOT_MIN}~{PLAYER_SLOT_MAX}）",
            },
            "item": {
                "type": "string",
                "minLength": 1,
                "description": "这个格子上的物品名（必须与之一致，如 dirt）",
            },
            "count": {"type": "integer", "minimum": 1, "description": "要移动的数量（>= 1）"},
        },
        "required": [
            "x",
            "y",
            "z",
            "direction",
            "container_slot",
            "inventory_slot",
            "item",
            "count",
        ],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftContainerTransferTool(ActionTool):
    """单物品单项存取：判定（含确认门）→ Service → 结构化结果。"""

    metadata = CONTAINER_TRANSFER_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.container_transfer(
            arguments.get("x"),
            arguments.get("y"),
            arguments.get("z"),
            arguments.get("direction"),
            arguments.get("container_slot"),
            arguments.get("inventory_slot"),
            arguments.get("item"),
            arguments.get("count"),
        )
