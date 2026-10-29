"""Builtin tool: minecraft_container_inspect —— 只读查看 Chest / Barrel 的内容（Phase 4E）。

边界（任务书 §二/§三/§六）：
* 只支持**单方块** Chest / Barrel（trapped chest / 双箱 / 潜影盒 / 熔炉… 一律拒绝）；
* 只读：内部 open → read → close，绝不把"开着的窗口"留给模型；
* SAFE 只代表"对支持的单方块容器做只读 inspection"，它仍然**独占**（打开真实窗口是
  有生命周期的客户端状态，不能与其他前台动作并发）；
* 不导航（距离上限之外直接拒绝）、不自动开未知容器。

它是 ``minecraft_container_transfer`` 的前置：模型必须先看清"第几格有什么"，
才能给出精确的 container_slot。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

CONTAINER_INSPECT_METADATA = ToolMetadata(
    name="minecraft_container_inspect",
    display_name="Minecraft Container Inspect",
    description=(
        "只读查看一个 Chest（箱子）或 Barrel（木桶）里有什么：每个非空格子的物品名与数量"
        "（含格子编号）。只支持单方块箱子/桶，罐头必须站在 5 格内；只读，不拿也不放。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "container", "chest", "perception"],
    keywords=[
        "箱子里",
        "箱子有什么",
        "看看箱子",
        "打开箱子",
        "桶里",
        "仓库",
        "存货",
        "容器",
        "minecraft",
    ],
    when_to_use=(
        "用户问「箱子里有什么 / 仓库里还有多少」，或者准备从箱子里拿东西 / 往箱子里放东西之前"
        "（必须先看清 container_slot 上有哪个物品，再决定 transfer 的参数）。"
    ),
    when_not_to_use=(
        "只是看看自己背包用 minecraft_inventory；要拿/放东西用 minecraft_container_transfer；"
        "双箱、潜影盒、熔炉、漏斗等容器本阶段不支持。"
    ),
    limitations=(
        "只支持单方块 Chest / Barrel；只显示容器里的非空格子（不含原始 window/NBT/cursor）；"
        "罐头必须已经在 5 格内，本工具不会自己走过去；打开窗口是独占的，忙时会被拒绝。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "integer", "description": "容器方块的整数 X 坐标"},
            "y": {"type": "integer", "description": "容器方块的整数 Y 坐标"},
            "z": {"type": "integer", "description": "容器方块的整数 Z 坐标"},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftContainerInspectTool(ActionTool):
    """读容器内容：判定（SAFE，但独占）→ Service → 结构化结果。"""

    metadata = CONTAINER_INSPECT_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.container_inspect(
            arguments.get("x"), arguments.get("y"), arguments.get("z")
        )
