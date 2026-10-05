"""Builtin tool: minecraft_dig —— 破坏**一个**明确指定的方块（Phase 4B）。

第一个真正修改 Minecraft 世界的动作，因此比其它工具多两道门：

1. **MEDIUM 风险 → 必须用户确认**（Phase 4A 的确认门，绑 user/session/参数指纹、一次性、TTL）；
2. **expected_block 必填**：用户确认的是「这个位置的这个方块」，不是「这里现在的东西」——
   执行前 runtime 会重新读一次方块，变了就 `minecraft.block_changed` 拒绝。

本阶段只做单块：不找矿、不换目标、不连续挖、不导航、不换工具、不捡掉落物。
工具自己只做「判定 → Service → 结构化结果」，挖的动作在 Action Runtime 里（Mineflayer dig）。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.service import MinecraftService
from app.tools.builtins.minecraft_actions import ACTION_TOOL_TIMEOUT, ActionTool
from app.tools.models import ToolMetadata

DIG_METADATA = ToolMetadata(
    name="minecraft_dig",
    display_name="Minecraft Dig",
    description=(
        "破坏一个指定的 Minecraft 方块（真实修改世界；MEDIUM 风险，需要用户确认）。"
        "必须先调用 minecraft_world 看清目标方块与坐标，再用它的坐标和方块名调用本工具。"
        "每次最多破坏一个方块；目标方块已经变了、不存在、挖不动或太远都会失败。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "dig", "world-change"],
    keywords=["挖掉", "挖了", "破坏", "挖", "拆掉", "敲掉", "弄掉", "minecraft"],
    when_to_use=(
        "用户明确要求打掉某个具体方块时（例如「把我面前这个石头挖掉」）。"
        "先调用 minecraft_world 确认目标和坐标，再调用本工具；"
        "第一次调用会返回需要确认，用户明确确认后用**完全相同的参数**再调用一次。"
    ),
    when_not_to_use=(
        "用户只是问「这里有什么」时（那是 minecraft_world）；"
        "要移动位置用 minecraft_move_to；要连续挖矿/自动找矿——本阶段没有这种能力。"
    ),
    limitations=(
        "一次只破坏一个方块；不会自动寻找其他方块、不会连续挖掘、不会导航过去、"
        "不会自动换工具或捡掉落物；只能用**当前手持**的工具（挖不动就失败）；"
        "目标必须在身边几格内；破坏结果以真实回执为准（未确认破坏会报 block_break_unconfirmed）。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "x": {"type": "number", "description": "目标方块 X 坐标"},
            "y": {"type": "number", "description": "目标方块 Y 坐标"},
            "z": {"type": "number", "description": "目标方块 Z 坐标"},
            "expected_block": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "目标方块名（必须与 minecraft_world 里看到的一致，如 minecraft:stone）"
                ),
            },
        },
        "required": ["x", "y", "z", "expected_block"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftDigTool(ActionTool):
    """挖一个方块：判定（含确认门）→ Service → 结构化结果。"""

    metadata = DIG_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.dig(
            arguments.get("x"),
            arguments.get("y"),
            arguments.get("z"),
            arguments.get("expected_block"),
        )
