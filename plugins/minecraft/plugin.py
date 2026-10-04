"""Minecraft plugin: QQ 侧的连接控制面（Phase 1，任务书 §QQ 第一阶段）。

只做三件事，刻意保持克制：

1. 识别「加入/离开 Minecraft」的自然语言指令 → MinecraftService（host:port
   直接写在消息里，或复用上一次成功的服务器）；
2. 把指令执行结果用罐头的口吻回在哪问的哪（群问群回、私聊私回）；
3. 把 Minecraft 聊天作为外部事件送进沙盒处理链（不是 Minecraft 对话 Agent，
   Phase 1 只保证事件能进来）。

不发主动播报（被踢/断线不上 QQ 骚扰），那是 Phase 2 的事。
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.integrations.minecraft.service import (
    MinecraftBridgeError,
    MinecraftDisabled,
    MinecraftRuntimeDown,
    MinecraftService,
)
from app.plugins.base import Plugin
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.message.event import MessageEvent

_MC_WORD = r"(?:minecraft|我的世界|mc)"

_JOIN_RE = re.compile(
    rf"(?:加入|进一下?|进服|连接|联机|来玩|一起玩|去玩|玩|开黑|组队|登入|登录|上线)\s*{_MC_WORD}"
    rf"|{_MC_WORD}\s*(?:加入|联机|开黑)",
    re.IGNORECASE,
)
_LEAVE_RE = re.compile(
    rf"离开\s*{_MC_WORD}|{_MC_WORD}\s*离开|退出\s*{_MC_WORD}|退出服务器|下线\s*{_MC_WORD}"
    rf"|回来吧|从\s*{_MC_WORD}\s*回来",
    re.IGNORECASE,
)
#: IPv4 / 带点的域名 / localhost，可带端口（: 或中文冒号）
_ADDR_RE = re.compile(
    r"(?:\d{1,3}(?:\.\d{1,3}){3})"
    r"|(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?(?:\.[a-zA-Z0-9](?:[a-zA-Z0-9-]*[a-zA-Z0-9])?)+)"
    r"|localhost"
    r"(?:[:：](\d{1,5}))?",
    re.IGNORECASE,
)

#: 请求加入后等待 spawned 的目标（"group:123" / "private:456"），只保留一个
_PENDING_JOIN: dict[str, str] = {}  # module-level: 插件实例可能被重载


def parse_server_address(text: str) -> tuple[str, int] | None:
    """从一句话里解析 ``host`` 或 ``host:port``；解析不出返回 None。"""
    match = _ADDR_RE.search(text)
    if match is None:
        return None
    host = match.group(0).split(":", 1)[0].split("：", 1)[0]
    port_raw = match.group(1)
    port = int(port_raw) if port_raw else None
    if port is not None and not 1 <= port <= 65535:
        return None
    return host, (port or 25565)


def chat_target(event: MessageEvent) -> str:
    return f"group:{event.group_id}" if event.is_group else f"private:{event.user_id}"


class MinecraftPlugin(Plugin):
    name = "minecraft"
    version = "1.0.0"
    description = "QQ 侧 Minecraft join/leave 触发 + Minecraft 聊天入链"

    async def on_load(self, bot: Any) -> None:
        self._pending_join: dict[str, str] = _PENDING_JOIN
        bot.event_bus.on("message.private", self._on_private)
        bot.event_bus.on("message.group", self._on_group)
        service = getattr(bot, "minecraft", None)
        if service is not None:
            service.add_listener(self._on_minecraft_event)

    async def on_unload(self) -> None:
        service = getattr(self.bot, "minecraft", None)
        if service is not None:
            service.remove_listener(self._on_minecraft_event)

    # ------------------------------------------------------------- QQ triggers

    async def _on_private(self, event: MessageEvent) -> None:
        await self._on_message(event)

    async def _on_group(self, event: MessageEvent) -> None:
        await self._on_message(event)

    async def _on_message(self, event: MessageEvent) -> None:
        text = event.message.text.strip()
        if not text:
            return
        if _LEAVE_RE.search(text):
            await self._handle_leave(event)
            return
        if _JOIN_RE.search(text):
            await self._handle_join(event, text)

    async def _handle_join(self, event: MessageEvent, text: str) -> None:
        service = self._service()
        if service is None:
            await self._reply(event, "我现在还进不了 Minecraft 世界呢——连接层还没开启。")
            return
        if not service.enabled:
            await self._reply(event, "我现在还进不了 Minecraft 世界呢——连接层没开启。")
            return
        address = parse_server_address(text)
        if address is None:
            last = service.snapshot()["connection"]
            if last.get("host") and last.get("port"):
                address = (str(last["host"]), int(last["port"]))
            else:
                await self._reply(
                    event, "好呀，把服务器地址发我就行，比如 `127.0.0.1:25565` 或者 `hypixel.net`。"
                )
                return
        host, port = address
        target = chat_target(event)
        self._pending_join[target] = target
        narrate().sense(
            f"⛏ 想让我进 {host}:{port}",
            detail="群 " + str(event.group_id) if event.is_group else "私聊",
        )
        try:
            await service.join(host, port)
        except (MinecraftDisabled, MinecraftRuntimeDown) as exc:
            self._pending_join.pop(target, None)
            await self._reply(event, f"我现在进不去 Minecraft……原因是 {exc}")
        except MinecraftBridgeError as exc:
            self._pending_join.pop(target, None)
            await self._reply(event, f"我进不去这个服务器……原因是 {exc}")

    async def _handle_leave(self, event: MessageEvent) -> None:
        service = self._service()
        if service is None or not service.enabled:
            return  # 连接层没开时，「离开」这句话不值得回
        snapshot = service.snapshot()["connection"]
        if snapshot.get("status") in ("DISCONNECTED", "ERROR"):
            await self._reply(event, "咦，我现在本来就不在服务器里呀。")
            return
        try:
            await service.leave()
        except MinecraftBridgeError as exc:
            await self._reply(event, f"呜，没能从服务器出来……原因是 {exc}")
            return
        await self._reply(event, "好啦，我回来啦。")

    # ------------------------------------------------------- minecraft events

    async def _on_minecraft_event(self, event: MinecraftBridgeEvent) -> None:
        if event.type_name == "minecraft.spawned":
            await self._announce_join()
        elif event.type_name == "minecraft.chat":
            await self._forward_chat(event)
        elif event.type_name == "minecraft.disconnected":
            await self._report_failed_join(event)

    async def _announce_join(self) -> None:
        """谁在 QQ 里喊了「加入」，就回给谁：成功进世界才算数。"""
        for target in list(self._pending_join):
            self._pending_join.pop(target, None)
            await self._send_to_target(target, "我进来啦！")

    async def _report_failed_join(self, event: MinecraftBridgeEvent) -> None:
        """挂着等待名单却没进世界就断开（被踢/连不上）：把原因回给请求者。

        没有等待名单的断开（玩完离开、WebUI 操作）不播报——不主动骚扰。
        """
        if not self._pending_join:
            return
        reason = str(event.data.get("reason") or "连接失败")
        if len(reason) > 120:
            reason = reason[:120] + "……"
        for target in list(self._pending_join):
            self._pending_join.pop(target, None)
            await self._send_to_target(target, f"我进不去这个服务器……原因是 {reason}")

    async def _send_to_target(self, target: str, text: str) -> None:
        kind, _, key = target.partition(":")
        try:
            if kind == "group":
                await self.bot.api.send_group_msg(int(key), text)
            else:
                await self.bot.api.send_private_msg(int(key), text)
        except Exception:  # noqa: BLE001 - 播报失败不影响连接
            self.bot.log.exception("[Minecraft] 无法发送 QQ 播报")

    async def _forward_chat(self, event: MinecraftBridgeEvent) -> None:
        """Minecraft 聊天 → 沙盒外部事件链（Phase 1 的唯一「处理」）。"""
        username = str(event.data.get("username") or "").strip()
        message = str(event.data.get("message") or "").strip()
        if not username or not message:
            return
        sandbox = getattr(self.bot, "sandbox", None)
        if sandbox is None or not getattr(sandbox, "enabled", False):
            self.bot.log.info("[Minecraft] chat（沙盒未启用，仅记录）：%s: %s", username, message)
            return
        try:
            from app.sandbox.external import ExternalSource, ExternalWorldEvent

            world_event = ExternalWorldEvent(
                event_id=f"mc_{event.session_id}_{int(event.timestamp * 1000)}",
                source=ExternalSource.other,
                actor_id=username,
                event_type="message",
                content=message,
                metadata={"channel": "minecraft", "session_id": event.session_id or ""},
                actor_relationship="known",
            )
            if await sandbox.submit_external(world_event):
                await sandbox.wakeup()
        except Exception:  # noqa: BLE001 - 沙盒故障不拖垮事件通道
            self.bot.log.exception("[Minecraft] 聊天转发沙盒失败")

    # ----------------------------------------------------------------- helpers

    def _service(self) -> MinecraftService | None:
        service = getattr(self.bot, "minecraft", None)
        return service if isinstance(service, MinecraftService) else None

    async def _reply(self, event: MessageEvent, text: str) -> None:
        try:
            if event.is_group:
                await self.bot.api.send_group_msg(int(event.group_id), text)
            else:
                await self.bot.api.send_private_msg(int(event.user_id), text)
        except Exception:  # noqa: BLE001
            self.bot.log.exception("[Minecraft] 回复发送失败")
