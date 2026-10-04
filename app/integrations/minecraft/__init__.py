"""Minecraft 连接层（Phase 1 · Minecraft Connection / Bridge Layer）。

结构（与任务书架构一致，Mineflayer 不进 CatooBot 核心语言）::

    CatooBot Core
      ↓ MinecraftService（本包：状态镜像 + 事件分发 + runtime 进程托管）
      ↓ MinecraftRuntimeClient（HTTP）
    Minecraft Runtime（独立 Node.js 进程，minecraft_runtime/runtime.js）
      ↓ Mineflayer
    Minecraft Server

本阶段只有连接/聊天/事件；感知、寻路、Agent 属于 Phase 2+，一律不做。
"""

from __future__ import annotations

from app.integrations.minecraft.events import (
    MinecraftBridgeEvent,
    MinecraftEventType,
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
    "MinecraftService",
    "MinecraftBridgeError",
    "MinecraftDisabled",
    "MinecraftRuntimeDown",
    "MinecraftInvalidTarget",
    "MinecraftNotConnected",
    "MinecraftBusy",
    "parse_bridge_event",
]
