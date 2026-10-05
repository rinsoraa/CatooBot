"""Minecraft 玩家聊天 → 角色对话（Phase 4A §十六-§二十三）。

游戏里玩家说话（``minecraft.chat``）此前只进沙盒外部事件链（世界认知/行为活动）。
本模块是**第二个消费者**：把它当成一次真正的 **USER 回合**交给
:class:`~app.character.runtime.CharacterRuntime` —— 于是游戏内玩家可以用自然语言
触发已有的 Minecraft Tools（SAFE 不限，LOW 需要可信玩家 + USER 回合）。

两条消费者**并存、互不替代**（§十六/§二十九）：

    minecraft.chat
      ├── sandbox.submit_external（世界认知 / 行为活动，插件里做）
      └── MinecraftChatBridge → CharacterRuntime.respond(turn_origin=USER) → 游戏内回复

硬门禁（§三十）：**罐头自己的发言绝不进自己的 LLM 回合**，否则「她说话 → 收到自己的
聊天 → 模型 → 再说话」会无限循环。判定用 runtime 上报的 username 与镜像里的
``connection.username`` 双比对（拿不到自己的名字时，宁可不回）。

本模块不碰 Mineflayer / Bridge HTTP：发言走 ``MinecraftService.send_chat``（Action Runtime）。
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from app.character.turn import TurnOrigin
from app.integrations.minecraft.agent import PLAYER_KEY

if TYPE_CHECKING:  # pragma: no cover
    from app.core.bot import Bot
    from app.integrations.minecraft.events import MinecraftBridgeEvent
    from app.integrations.minecraft.service import MinecraftService

log = logging.getLogger("CatooBot.Minecraft.Chat")

#: 会话命名（§十八）：Minecraft 世界有自己的会话，绝不塞进 QQ 的 private:/group:
SESSION_PREFIX = "minecraft"
#: 排队上限：短时间内大量聊天不会无限堆积（每条都在独立任务里跑）
MAX_INFLIGHT = 4


def chat_session_id(host: str | None, port: int | None, username: str) -> str:
    """会话名：``minecraft:{host}:{port}:{username}``（缺服务器信息时只用用户名）。"""
    if host and port:
        return f"{SESSION_PREFIX}:{host}:{port}:{username}"
    return f"{SESSION_PREFIX}:{username}"


class MinecraftChatBridge:
    """``minecraft.chat`` 事件 → USER 回合 → 游戏内回复。"""

    def __init__(self, bot: Bot, service: MinecraftService) -> None:
        self.bot = bot
        self.service = service
        self._inflight = 0

    # ------------------------------------------------------------ 配置/门禁

    @property
    def enabled(self) -> bool:
        return bool(self.service.enabled and self.service.config.agent.chat.enabled)

    def self_username(self) -> str:
        """罐头自己的 MC 名字（镜像里的 connection.username）。"""
        try:
            return str(self.service.snapshot()["connection"].get("username") or "").strip()
        except Exception:  # noqa: BLE001 - 镜像坏了也不该让聊天崩
            return ""

    def is_self_speech(self, username: str) -> bool:
        """§三十：她自己发的聊天绝不能回到她自己的 LLM 回合。"""
        mine = self.self_username()
        if not username:
            return True
        if not mine:
            # 拿不到自己的名字时保守处理：宁可不回，也不冒无限循环的风险
            log.info("[MC Chat] 跳过（镜像里还没有自己的 username）：%s", username)
            return True
        return username == mine

    # ------------------------------------------------------------- 事件入口

    def apply_event(self, event: MinecraftBridgeEvent) -> asyncio.Task[None] | None:
        """MinecraftService 的监听器（只处理 ``minecraft.chat``）。

        **不阻塞事件通道**：真正的回合在后台任务里跑（返回该任务，测试可以 await；
        事件分发方不会等它 —— WorldPerception 绝不能被 LLM 拖住）。
        """
        if event.type_name != "minecraft.chat":
            return None
        if not self.enabled:
            return None
        username = str(event.data.get("username") or "").strip()
        message = str(event.data.get("message") or "").strip()
        if not username or not message:
            return None
        if self.is_self_speech(username):
            return None
        if event.data.get("private"):
            # 悄悄话不公开回：本阶段只有公共聊天发送能力（见文档「已知限制」）
            log.info("[MC Chat] 跳过一条悄悄话（%s）", username)
            return None
        if self._inflight >= MAX_INFLIGHT:
            log.info("[MC Chat] 忙不过来，跳过一条（%s）：%.40s", username, message)
            return None
        self._inflight += 1
        task = asyncio.create_task(self._reply(username, message))
        task.add_done_callback(lambda _task: self._done())
        return task

    def _done(self) -> None:
        self._inflight = max(0, self._inflight - 1)

    # --------------------------------------------------------------- 回合

    async def _reply(self, username: str, message: str) -> None:
        """一次完整的 USER 回合：历史 → 角色回复 → 回游戏内 + 记入会话。"""
        bot = self.bot
        try:
            session_id = self._session_id(username)
            history = await bot.ai.conversations.get_context(session_id)
            log.info(
                "[MC Chat] %s: %.80s（session=%s, history=%d）",
                username,
                message,
                session_id,
                len(history),
            )
            reply = await bot.character.respond(
                session_id,
                username,
                message,
                # §十九：游戏内玩家说话就是 USER 回合（可信门在策略层，不在这里）
                turn_origin=TurnOrigin.USER,
                # §二十/§二十一：把「谁在说话」作为运输层事实带进工具上下文
                origin_metadata={PLAYER_KEY: username},
                history=history,
                user_name=username,
                is_group=False,
                time_context=self._time_context(),
                extra_instruction=(
                    "（这条消息来自 Minecraft 游戏内聊天，是服务器里的人在跟你说话；"
                    "回复要短，像游戏里随口说的话。）"
                ),
            )
            if not reply:
                return
            await bot.ai.conversations.append_user_message(session_id, message)
            await bot.ai.conversations.append_assistant_message(session_id, reply)
            await self._say(reply)
        except Exception:  # noqa: BLE001 - 游戏内聊天故障绝不拖垮事件通道/世界感知
            log.exception("[MC Chat] 处理 %s 的聊天失败", username)

    def _session_id(self, username: str) -> str:
        connection = self.service.snapshot().get("connection") or {}
        return chat_session_id(connection.get("host"), connection.get("port"), username)

    def _time_context(self) -> Any:
        behavior = getattr(self.bot, "behavior", None)
        if behavior is None:
            return None
        try:
            return behavior.time_context()
        except Exception:  # noqa: BLE001 - 时间只是礼貌
            return None

    async def _say(self, reply: str) -> None:
        """把回复发回游戏（走 Service → Action Runtime，绝不直接碰 runtime）。"""
        limit = self.service.config.agent.chat.max_reply_chars
        text = reply.strip()
        if len(text) > limit:
            text = text[: limit - 1].rstrip() + "…"
        try:
            await self.service.send_chat(text)
        except Exception as exc:  # noqa: BLE001 - 发不出去只记一笔（她还在世界里）
            log.warning("[MC Chat] 回复发送失败：%s", exc)
