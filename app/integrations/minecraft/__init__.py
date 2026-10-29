"""Minecraft 连接层（Phase 1）+ 世界感知（Phase 2）。

结构（与任务书架构一致，Mineflayer 不进 CatooBot 核心语言）::

    CatooBot Core
      ↓ MinecraftService（本包：状态镜像 + 事件分发 + runtime 进程托管 + 感知）
      ↓ MinecraftRuntimeClient（HTTP）
    Minecraft Runtime（独立 Node.js 进程，minecraft_runtime/runtime.js）
      ↓ Mineflayer
    Minecraft Server

Phase 1：连接/聊天/事件；Phase 2：`world.py` 的 Raw Snapshot → Semantic World Model
（只读「眼睛」）。行动能力（移动/挖掘/建造）属于 Phase 3+，一律不做。
"""

from __future__ import annotations

from app.integrations.minecraft.events import (
    MinecraftBridgeEvent,
    MinecraftEventType,
    MinecraftWorldEvent,
    parse_bridge_event,
)
from app.integrations.minecraft.service import (
    MinecraftBridgeError,
    MinecraftBusy,
    MinecraftDisabled,
    MinecraftInvalidTarget,
    MinecraftNotConnected,
    MinecraftRuntimeDown,
    MinecraftService,
)

__all__ = [
    "MinecraftBridgeEvent",
    "MinecraftEventType",
    "MinecraftWorldEvent",
    "MinecraftService",
    "MinecraftBridgeError",
    "MinecraftDisabled",
    "MinecraftRuntimeDown",
    "MinecraftInvalidTarget",
    "MinecraftNotConnected",
    "MinecraftBusy",
    "parse_bridge_event",
]
