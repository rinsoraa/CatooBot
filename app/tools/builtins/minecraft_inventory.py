"""Builtin tool: minecraft_inventory —— 只读背包切片（Phase 4C）。

结构完全对称于 ``minecraft_world``：``bridge_from(context)`` → 策略门 → Service（只读）
→ ToolResult。
只暴露"选中的 hotbar 槽 / 手持物品 / 按物品名聚合的物品清单"这三样；raw slot / NBT / window /
容器状态 / 盔甲 / cursor 一律不进 LLM（§四）。

它是 ``minecraft_place`` 的前置：expected_item 必须与**当前主手**一致，所以模型要先看清手里有什么。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.agent import bridge_from
from app.integrations.minecraft.service import MinecraftBridgeError
from app.tools.base import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

METADATA = ToolMetadata(
    name="minecraft_inventory",
    display_name="Minecraft Inventory",
    description=(
        "只读查看罐头在 Minecraft 里的背包：当前选中的快捷栏槽、主手拿着什么、"
        "以及背包里各物品的数量（按物品名聚合）。不会移动、不会换手、不会修改任何东西。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "inventory", "perception"],
    keywords=[
        "背包",
        "身上有什么",
        "手里拿",
        "拿了什么",
        "带了什么",
        "有多少",
        "库存",
        "minecraft",
    ],
    when_to_use=(
        "用户问「你身上有什么 / 手里拿着什么」，或者准备让罐头放方块之前"
        "（先看清主手物品，再决定 expected_item）。"
    ),
    when_not_to_use=(
        "要移动/挖方块/放方块时用对应的动作工具；本工具只回答现状，不做任何动作，"
        "也不会替你换手或装备。"
    ),
    limitations=(
        "只读投影：不显示原始 slot/NBT/容器内容，也不显示盔甲槽；"
        "罐头不在服务器内时只能如实说不在；数据以最近一次服务器状态为准。"
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    cache_ttl_seconds=1.0,
    timeout=5.0,
    risk_level="low",
)


class MinecraftInventoryTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        bridge = bridge_from(context)
        if bridge is None:
            return self.failure(
                self.metadata.name, "Minecraft 连接层不可用", error_type="minecraft.disabled"
            )
        # §五：与 minecraft_world 同一道策略门（未启用/未允许时如实拒绝，不碰 runtime）
        decision = bridge.check(self.metadata.name, arguments, context=context)
        if not decision.allowed:
            return bridge.denial(self.metadata.name, decision)

        try:
            slice_ = await bridge.inventory()
        except MinecraftBridgeError as exc:
            code = str(getattr(exc, "code", "") or "minecraft.action_failed")
            return self.failure(self.metadata.name, str(exc), error_type=code)
        except Exception as exc:  # noqa: BLE001 - surface as tool failure, never raise
            return self.failure(
                self.metadata.name,
                f"读取背包失败：{exc}",
                error_type="minecraft.action_failed",
            )
        if not slice_.get("online"):
            return self.failure(
                self.metadata.name,
                "罐头现在不在 Minecraft 世界里",
                error_type="minecraft.offline",
            )

        held = slice_.get("held_item")
        items = slice_.get("items") or []
        parts: list[str] = []
        if held:
            parts.append(f"主手拿着 {held['name']}×{held['count']}")
        else:
            parts.append("主手空着")
        if slice_.get("selected_hotbar_slot") is not None:
            parts.append(f"快捷栏第 {int(slice_['selected_hotbar_slot']) + 1} 格")
        if items:
            parts.append(
                "背包："
                + "、".join(f"{item['name']}×{item['count']}" for item in items[:8])
                + (f"（共 {len(items)} 种）" if len(items) > 8 else "")
            )
        else:
            parts.append("背包是空的")
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={
                "ok": True,
                "online": True,
                "selected_hotbar_slot": slice_.get("selected_hotbar_slot"),
                "held_item": held,
                "items": items,
            },
            summary="；".join(parts) + "。",
            metadata={"source_type": "minecraft", "confidence": 0.9, "online": True},
        )
