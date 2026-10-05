"""Minecraft 动作类 LLM Tool（Phase 3E）：chat / look_at / move_to / follow_player / stop。

边界（任务书 §三）：这些 Tool **只**是「LLM 的嘴和手」的说明书，本身不做任何 Minecraft
操作——每个 handler 的路径都是：

    validate（ToolRuntime 的 schema 校验）→ MinecraftActionPolicy → MinecraftService
    → Action Runtime → 结构化结果

绝不自己访问 Mineflayer / Pathfinder / Bridge HTTP，也绝不 sleep 等动作完成：
move_to / follow_player 启动即返回 ``RUNNING`` + ``action_id``（§三十三/§三十七），
终点由 ``minecraft.action.*`` 事件回到 CatooBot 的 Minecraft Context（§二十）。

风险等级（§十三）在这里由 :mod:`app.integrations.minecraft.agent` 的
``ACTION_RISK`` 权威定义；ToolMetadata.risk_level 只是通用 Tool Runtime 的
风险词表（low/medium/high），两者不是一回事：LOW 动作 = 非破坏性导航（会移动，
但绝不挖/放/搭），SAFE 动作 = 不改世界也不移动。
"""

from __future__ import annotations

from typing import Any

from app.integrations.minecraft.agent import bridge_from
from app.integrations.minecraft.service import MinecraftService
from app.tools.base import Tool as ToolBase
from app.tools.models import ToolContext, ToolMetadata, ToolResult

#: 动作类 Tool 的工具超时：动作本身在后台跑（30s/120s 是它的生命周期），
#: 这里只等「启动 + 校验」，唯一可能的等待是 runtime 冷启动（< 30s 健康等待）。
ACTION_TOOL_TIMEOUT = 15.0


class ActionTool(ToolBase):
    """Minecraft 动作工具的公共骨架：桥 → 判定（含确认门）→ Service → 结构化结果。

    ``minecraft_dig``（Phase 4B）也复用这个骨架——它和这五个工具的唯一区别是风险等级。
    """

    #: 传给 MinecraftService 的调用（在子类里用 lambda 绑定参数）
    async def _call(
        self, service: MinecraftService, arguments: dict[str, Any]
    ) -> dict[str, Any]:  # pragma: no cover - 抽象
        raise NotImplementedError

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        bridge = bridge_from(context)
        if bridge is None:
            # 连接层没装配（Minecraft 未启用 / 未启动）：如实说，不假装能动手
            return _unavailable(self.metadata.name)
        return await bridge.invoke(
            self.metadata.name,
            arguments,
            lambda service: self._call(service, arguments),
            context=context,
        )


def _unavailable(tool: str) -> ToolResult:
    message = "Minecraft 连接层现在不可用"
    return ToolResult(
        tool_name=tool,
        success=False,
        error=message,
        error_type="minecraft.disabled",
        data={"ok": False, "error": {"code": "minecraft.disabled", "message": message}},
        metadata={"source_type": "minecraft", "confidence": 0.0},
    )


def _coords_schema(*, unit: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "number", "description": f"目标 {unit} 的 X 坐标"},
            "y": {"type": "number", "description": f"目标 {unit} 的 Y 坐标"},
            "z": {"type": "number", "description": f"目标 {unit} 的 Z 坐标"},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
    }


# ------------------------------------------------------------------- chat


CHAT_METADATA = ToolMetadata(
    name="minecraft_chat",
    display_name="Minecraft Chat",
    description="让罐头在当前 Minecraft 服务器里说一句话（走服务器聊天，不是 QQ）。",
    version="0.1.0",
    category="communication",
    tags=["minecraft", "chat", "say"],
    keywords=[".minecraft", "服务器里说", "游戏里说", "在mc里", "喊一句", "打个招呼", "公屏"],
    when_to_use=("用户想让罐头在 Minecraft 服务器里说点什么（例如「跟服务器里的人打个招呼」）时。"),
    when_not_to_use=(
        "普通聊天回复一律用对话本身，不要发到 Minecraft；也不要用来假装自己做了移动/挖掘等动作。"
    ),
    limitations="只能发服务器公共聊天；不能私聊、不能带附件；消息最长 256 字符。",
    input_schema={
        "type": "object",
        "properties": {"message": {"type": "string", "description": "要发送的内容"}},
        "required": ["message"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftChatTool(ActionTool):
    metadata = CHAT_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.send_chat(str(arguments.get("message", "")))


# ---------------------------------------------------------------- look_at


LOOK_AT_METADATA = ToolMetadata(
    name="minecraft_look_at",
    display_name="Minecraft Look At",
    description="让罐头朝向 Minecraft 世界里的某个位置（只转头，不移动、不改世界）。",
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "look"],
    keywords=["看向", "转过去", "面向", "看着", "朝", ".minecraft", "视线"],
    when_to_use="用户想让罐头看看某个方向/某个位置（例如「看向那座山」）时。",
    when_not_to_use="需要走过去时用 minecraft_move_to；不要用本工具代替移动。",
    limitations="只改朝向，角色不会移动；坐标必须是世界里真实存在的位置。",
    input_schema=_coords_schema(unit="注视点"),
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftLookAtTool(ActionTool):
    metadata = LOOK_AT_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.look_at(arguments.get("x"), arguments.get("y"), arguments.get("z"))


# ---------------------------------------------------------------- move_to


MOVE_TO_METADATA = ToolMetadata(
    name="minecraft_move_to",
    display_name="Minecraft Move To",
    description=(
        "让罐头通过非破坏性寻路移动到 Minecraft 世界坐标；不能挖方块或放方块，"
        "找不到路就失败。启动后立即返回，是否到达由事件告知。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "move", "navigation"],
    keywords=["过来", "过来一下", "来我这边", "走到", "走过去", "移动", "去", ".minecraft", "坐标"],
    when_to_use=(
        "用户明确要求罐头走过去/过来/移动到某个位置时（例如「你过来」）。"
        "不知道目标位置就先调用 minecraft_world 看附近玩家和坐标。"
    ),
    when_not_to_use=(
        "用户只是聊天提到位置时不要动；不要为了「顺便」而移动；"
        "目标玩家在移动中（要一直跟着）用 minecraft_follow_player。"
    ),
    limitations=(
        "非破坏性寻路：不会挖/放/搭桥，路被堵死就失败；"
        "单次距离不能超过配置上限；同一时间只能有一个前台动作。"
    ),
    input_schema=_coords_schema(unit="目标点"),
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftMoveToTool(ActionTool):
    metadata = MOVE_TO_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.move_to(arguments.get("x"), arguments.get("y"), arguments.get("z"))


# ---------------------------------------------------------- follow_player


FOLLOW_METADATA = ToolMetadata(
    name="minecraft_follow_player",
    display_name="Minecraft Follow Player",
    description=(
        "持续跟随指定的 Minecraft 玩家：对方移动时会自动重新规划路线。"
        "这是持续动作，立刻返回 RUNNING，用 minecraft_stop 可以停止。"
    ),
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "follow"],
    keywords=["跟着我", "跟着", "跟随", "跟着你", "跟上", "一直跟着", ".minecraft", "陪我"],
    when_to_use=(
        "用户明确要求罐头跟着某个玩家时（例如「跟着我」）。"
        "对方名字不确定时先调用 minecraft_world 看附近有谁，不要猜。"
    ),
    when_not_to_use=("一次性的「过来」用 minecraft_move_to；不要在用户没要求时主动跟人。"),
    limitations=(
        "只跟随在线且在当前世界里的玩家；对方消失或跑太远会失败；"
        "跟随最多持续配置的时长（默认 120 秒），到点自动停下。"
    ),
    input_schema={
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "要跟随的玩家名（Minecraft 用户名）"},
            "distance": {
                "type": "number",
                "description": "保持的距离（格），1.5~6，默认 2.5",
                "minimum": 1.5,
                "maximum": 6,
            },
        },
        "required": ["username"],
        "additionalProperties": False,
    },
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftFollowPlayerTool(ActionTool):
    metadata = FOLLOW_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.follow_player(arguments.get("username"), arguments.get("distance"))


# ------------------------------------------------------------------- stop


STOP_METADATA = ToolMetadata(
    name="minecraft_stop",
    display_name="Minecraft Stop",
    description="立即停止罐头当前正在做的 Minecraft 行动（最高优先级，随时可用）。",
    version="0.1.0",
    category="system",
    tags=["minecraft", "action", "stop", "safety"],
    keywords=[
        "停下",
        "停下来",
        "别动了",
        "站住",
        "停止",
        "不用跟着",
        "回来",
        ".minecraft",
        "别跟了",
    ],
    when_to_use="用户说「停」「别跟了」「站住」，或需要打断当前 Minecraft 动作时——无需确认。",
    when_not_to_use="只是聊天时不要调用；没有正在进行的动作时调用也无害（会如实说没有）。",
    limitations="只停 Minecraft 里的动作，不影响聊天；没有动作在跑时不会报错。",
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    output_schema={"type": "object"},
    timeout=ACTION_TOOL_TIMEOUT,
    risk_level="low",
)


class MinecraftStopTool(ActionTool):
    metadata = STOP_METADATA

    async def _call(self, service: MinecraftService, arguments: dict[str, Any]) -> dict[str, Any]:
        return await service.stop_action()


# ------------------------------------------------------------------ exports


__all__ = [
    "ActionTool",
    "MinecraftChatTool",
    "MinecraftFollowPlayerTool",
    "MinecraftLookAtTool",
    "MinecraftMoveToTool",
    "MinecraftStopTool",
]
