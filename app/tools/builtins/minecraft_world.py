"""Builtin tool: Minecraft 世界只读视图（Phase 2 · World Perception）。

只读：返回 Semantic World Model（位置/环境/附近玩家/实体/地形/POI），
让罐头在普通对话里能回答「你附近有什么」。本工具没有任何 Minecraft
行动能力——移动/挖掘/建造属于 Phase 3+，任务书明确禁止在本阶段实现。
"""

from __future__ import annotations

from typing import Any

from app.tools.base import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

METADATA = ToolMetadata(
    name="minecraft_world",
    display_name="Minecraft World",
    description=(
        "查看罐头当前所在的 Minecraft 世界状态：位置、环境、附近玩家/实体/方块兴趣点（只读）。"
    ),
    version="0.1.0",
    category="information",
    tags=["minecraft", "world", "perception"],
    keywords=["minecraft", "世界", "附近", "周围", "坐标", "生物群系", "服务器里"],
    when_to_use="用户询问罐头在 Minecraft 里的位置、周围环境、附近玩家/实体/方块时。",
    when_not_to_use=(
        "用户要求罐头移动、挖掘、建造、跟随等 Minecraft 行动时——那些能力尚未实现，"
        "工具只能如实回答现状。"
    ),
    limitations=(
        "只读感知，不能执行任何 Minecraft 动作；罐头不在服务器内时不可用；"
        "数据有最长几秒的感知延迟。"
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    cache_ttl_seconds=2.0,
    timeout=5.0,
)


class MinecraftWorldTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        service = context.metadata.get("minecraft_world")
        if service is None:
            return self.failure(
                self.metadata.name, "Minecraft 连接层不可用", error_type="unavailable"
            )
        try:
            view = service.world_view()
        except Exception as exc:  # noqa: BLE001 - surface as tool failure, never raise
            return self.failure(
                self.metadata.name, f"读取世界状态失败：{exc}", error_type="tool_error"
            )
        if not view.get("available") or not view.get("semantic"):
            return self.failure(
                self.metadata.name,
                "罐头现在不在 Minecraft 世界里",
                error_type="not_in_world",
            )

        semantic = view["semantic"]
        self_state = semantic.get("self") or {}
        environment = semantic.get("environment") or {}
        players = semantic.get("players") or []
        entities = semantic.get("entities") or []
        terrain = semantic.get("terrain") or []
        pois = semantic.get("points_of_interest") or []

        location = self_state.get("location") or "未知生物群系"
        dimension = self_state.get("dimension")
        parts = [f"罐头在 {location}（{dimension}）"]
        if self_state.get("position"):
            pos = self_state["position"]
            parts.append(f"坐标 ({pos.get('x')}, {pos.get('y')}, {pos.get('z')})")
        if environment.get("time_phase"):
            parts.append(f"{environment['time_phase']}")
        if environment.get("weather") and environment["weather"] != "clear":
            parts.append(f"天气 {environment['weather']}")
        if players:
            parts.append(
                "附近玩家："
                + "、".join(
                    f"{player['name']}（{player['direction']}，{player['distance']}格）"
                    for player in players[:5]
                )
            )
        if entities:
            parts.append(
                "附近生物："
                + "、".join(
                    f"{entity['type']}×{entity['count']}（{entity.get('direction') or '未知方位'}）"
                    for entity in entities[:5]
                )
            )
        if pois:
            parts.append(
                "附近值得注意："
                + "、".join(
                    f"{poi['type']}（{poi['direction']}，{poi['distance']}格）" for poi in pois[:5]
                )
            )
        if terrain:
            parts.append(
                "地形："
                + "、".join(f"{item['type']}（{item['direction']}）" for item in terrain[:4])
            )

        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data={"semantic": semantic, "age_seconds": view.get("age_seconds")},
            summary="，".join(parts) + "。",
            metadata={"source_type": "minecraft", "online": view.get("online")},
        )
