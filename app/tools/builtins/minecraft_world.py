"""Builtin tool: Minecraft 世界只读视图（Phase 2 · World Perception；Phase 3E 升级）。

只读：返回 Semantic World Model（位置/环境/附近玩家/实体/地形/POI），让罐头在普通对话里
能回答「你附近有什么」，也是「罐头你过来」这类请求的第一步——模型先看附近有谁、
在什么坐标，再决定要不要 :mod:`minecraft_move_to`（任务书 §四十一）。

返回是**结构化结果 + 可选 summary**（§五）：data 里带完整语义模型（含附近玩家坐标），
summary 是一句人话；Raw Snapshot（45KB+）永远留在感知层，绝不进 LLM（§四十五）。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.agent import bridge_from
from app.tools.base import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

METADATA = ToolMetadata(
    name="minecraft_world",
    display_name="Minecraft World",
    description=(
        "只读查询罐头当前所在的 Minecraft 世界：维度、坐标、环境、附近玩家/生物/方块（不会移动，"
        "不会修改世界）。"
    ),
    version="0.2.0",
    category="information",
    tags=["minecraft", "world", "perception"],
    keywords=[
        "minecraft",
        "世界",
        "附近",
        "周围",
        "坐标",
        "生物群系",
        "服务器里",
        "谁在",
        "有谁",
        "在哪",
    ],
    when_to_use=(
        "用户问罐头在 Minecraft 里的位置、周围环境、附近玩家/实体/方块时；"
        "或者需要知道某个玩家在哪（准备 move_to/follow 之前）时。"
    ),
    when_not_to_use=("要求罐头移动/跟随/说话时用对应的动作工具；本工具只回答现状，不做任何动作。"),
    limitations=(
        "只读感知，不能执行任何 Minecraft 动作；罐头不在服务器内时只能如实说不在；"
        "数据有最长几秒的感知延迟。"
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    cache_ttl_seconds=2.0,
    timeout=5.0,
    risk_level="low",
)


class MinecraftWorldTool(ToolBase):
    metadata = METADATA

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        bridge = bridge_from(context)
        if bridge is None:
            return self.failure(
                self.metadata.name, "Minecraft 连接层不可用", error_type="minecraft.disabled"
            )
        # §十二：只读工具也过同一道策略门（未启用/未允许时如实拒绝，不去碰 runtime）
        decision = bridge.check(self.metadata.name, arguments, context=context)
        if not decision.allowed:
            return bridge.denial(self.metadata.name, decision)

        try:
            view = bridge.world_view()
        except Exception as exc:  # noqa: BLE001 - surface as tool failure, never raise
            return self.failure(
                self.metadata.name, f"读取世界状态失败：{exc}", error_type="minecraft.action_failed"
            )
        if not view.get("available") or not view.get("semantic"):
            return self.failure(
                self.metadata.name,
                "罐头现在不在 Minecraft 世界里",
                error_type="minecraft.offline",
            )

        semantic = view["semantic"]
        self_state = semantic.get("self") or {}
        environment = semantic.get("environment") or {}
        players = semantic.get("players") or []
        entities = semantic.get("entities") or []
        terrain = semantic.get("terrain") or []
        pois = semantic.get("points_of_interest") or []

        data: dict[str, Any] = {
            "ok": True,
            "online": True,
            "dimension": self_state.get("dimension"),
            "position": self_state.get("position"),
            "biome": self_state.get("location"),
            "time_of_day": environment.get("time_phase"),
            "weather": environment.get("weather"),
            "health": self_state.get("health"),
            "nearby_players": [
                {
                    "name": player.get("name"),
                    "distance": player.get("distance"),
                    "relative_direction": player.get("direction"),
                    "compass": player.get("compass"),
                    "position": player.get("position"),
                }
                for player in players[:5]
            ],
            "nearby_entities": [
                {
                    "type": entity.get("type"),
                    "count": entity.get("count"),
                    "direction": entity.get("direction"),
                    "distance": entity.get("distance"),
                }
                for entity in entities[:5]
            ],
            "points_of_interest": pois[:5],
            "terrain": terrain[:4],
            "age_seconds": view.get("age_seconds"),
        }
        return ToolResult(
            tool_name=self.metadata.name,
            success=True,
            data=data,
            summary=self._summary(self_state, environment, players, entities, pois, terrain),
            metadata={"source_type": "minecraft", "confidence": 0.9, "online": view.get("online")},
        )

    @staticmethod
    def _summary(
        self_state: dict[str, Any],
        environment: dict[str, Any],
        players: list[dict[str, Any]],
        entities: list[dict[str, Any]],
        pois: list[dict[str, Any]],
        terrain: list[dict[str, Any]],
    ) -> str:
        location = self_state.get("location") or "未知生物群系"
        parts = [f"罐头在 {location}（{self_state.get('dimension')}）"]
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
        return "，".join(parts) + "。"
