"""Minecraft Bridge 事件模型（任务书 §Minecraft → CatooBot Events）。

事件由 Node runtime 推送（HTTP 回调），形状::

    {"event": "minecraft.chat", "session_id": "...", "timestamp": 1730000000.0,
     "username": "空凛", "message": "罐头过来"}

统一在这里校验，再交给 MinecraftService 更新状态镜像并分发给订阅者。
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator


class MinecraftEventType(str, Enum):  # noqa: UP042 - pydantic-friendly str enum
    """任务书规定的九种 Bridge 事件。"""

    CONNECTING = "minecraft.connecting"
    CONNECTED = "minecraft.connected"
    SPAWNED = "minecraft.spawned"
    CHAT = "minecraft.chat"
    PLAYER_JOINED = "minecraft.player_joined"
    PLAYER_LEFT = "minecraft.player_left"
    KICKED = "minecraft.kicked"
    DISCONNECTED = "minecraft.disconnected"
    ERROR = "minecraft.error"


#: 合法事件名字符串（回调载荷的 ``event`` 字段必须是其中之一）
KNOWN_EVENT_NAMES = frozenset(t.value for t in MinecraftEventType)

#: 事件模型自身的字段；其余键都归入 ``data``（username / message / reason …）
_RESERVED_KEYS = frozenset({"event", "session_id", "timestamp"})


class MinecraftBridgeEvent(BaseModel):
    """一条已校验的 Bridge 事件。原始上下文在 ``data`` 里，不解释、不丢弃。"""

    event: MinecraftEventType
    session_id: str | None = None
    timestamp: float = 0.0
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @property
    def type_name(self) -> str:
        return self.event.value


def parse_bridge_event(raw: dict[str, Any]) -> MinecraftBridgeEvent:
    """校验一条回调载荷；未知事件名/坏时间戳按 ValueError 拒绝。"""
    if not isinstance(raw, dict):
        raise ValueError("事件载荷必须是 JSON 对象")
    name = raw.get("event")
    if not isinstance(name, str) or name not in KNOWN_EVENT_NAMES:
        raise ValueError(f"未知或缺失的 Minecraft 事件类型：{name!r}")
    data = {key: value for key, value in raw.items() if key not in _RESERVED_KEYS}
    event = MinecraftBridgeEvent(
        event=name,  # type: ignore[arg-type]
        session_id=raw.get("session_id") if isinstance(raw.get("session_id"), str) else None,
        timestamp=raw.get("timestamp", 0.0),
        data=data,
    )
    return event


# ----------------------------------------------------------------- world events


class MinecraftWorldEvent(BaseModel):
    """语义级世界感知事件（Phase 2）：由感知引擎去抖后产生，再分发给订阅者。"""

    event: str
    timestamp: float = 0.0
    data: dict[str, Any] = Field(default_factory=dict)

    @field_validator("timestamp", mode="before")
    @classmethod
    def _coerce_timestamp(cls, value: Any) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return 0.0

    @property
    def type_name(self) -> str:
        return self.event


#: 语义级感知事件（任务书 §七）：方块变化聚合为 world.changed，绝无逐方块风暴
WORLD_EVENT_NAMES = frozenset(
    {
        "minecraft.world.changed",
        "minecraft.player.nearby",
        "minecraft.player.left_area",
        "minecraft.entity.discovered",
        "minecraft.entity.left_area",
        "minecraft.poi.discovered",
    }
)


def parse_world_event(event: str, data: dict[str, Any], timestamp: float) -> MinecraftWorldEvent:
    if event not in WORLD_EVENT_NAMES:
        raise ValueError(f"未知的世界感知事件类型：{event!r}")
    return MinecraftWorldEvent(event=event, timestamp=timestamp, data=data)
