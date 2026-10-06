"""Builtin tools shipped with CatooBot.

Phase 3E 起：Minecraft 工具（只读 world/chat 之外的动作类）——动作类 Tool 只经
MinecraftService/Action Runtime 执行，判定与错误结构化在
:mod:`app.integrations.minecraft.agent`。
"""

from app.tools.builtins.calculator import CalculatorTool
from app.tools.builtins.minecraft_actions import (
    MinecraftChatTool,
    MinecraftFollowPlayerTool,
    MinecraftLookAtTool,
    MinecraftMoveToTool,
    MinecraftStopTool,
)
from app.tools.builtins.minecraft_dig import MinecraftDigTool
from app.tools.builtins.minecraft_equip import MinecraftEquipTool
from app.tools.builtins.minecraft_inventory import MinecraftInventoryTool
from app.tools.builtins.minecraft_inventory_move import MinecraftInventoryMoveTool
from app.tools.builtins.minecraft_place import MinecraftPlaceTool
from app.tools.builtins.minecraft_world import MinecraftWorldTool
from app.tools.builtins.providers import WeatherTool, WebSearchTool
from app.tools.builtins.query_image_memory import QueryImageMemoryTool
from app.tools.builtins.time import TimeTool

__all__ = [
    "CalculatorTool",
    "MinecraftChatTool",
    "MinecraftDigTool",
    "MinecraftEquipTool",
    "MinecraftFollowPlayerTool",
    "MinecraftInventoryMoveTool",
    "MinecraftInventoryTool",
    "MinecraftLookAtTool",
    "MinecraftMoveToTool",
    "MinecraftPlaceTool",
    "MinecraftStopTool",
    "MinecraftWorldTool",
    "QueryImageMemoryTool",
    "TimeTool",
    "WeatherTool",
    "WebSearchTool",
]
