"""Phase 5C §七/§八/§三十五：QQ 侧的 Minecraft 身份绑定命令。

用户显式验证是**唯一**优先级最高的绑定来源（§六 1）。流程刻意做成两步：

```
「把我和 Minecraft 里的 Rinsora 绑定」
    → 她先报出 服务器 + 在线玩家名 + UUID 后四位，请对方回「确认绑定」
「确认绑定」
    → 真的建立 IdentityLink（VERIFIED）；对方不确认就什么都不会发生
「解除绑定」
    → 旧 link 变 REVOKED（历史记忆不删，§九）
```

安全要点：

* 只有**在线**玩家才能绑定 —— 要拿到服务器发的真实 UUID 才允许写入（§四十三）；
* 二次确认时重新解析同名玩家，UUID 对不上就拒绝（防"确认期间换人"）；
* 已 VERIFIED 的 UUID 属于别人 → 冲突拒绝，**绝不覆盖**（§三十五）；
* 身份只用来"认人"：绑定**不**授予任何权限，也不降低 MEDIUM 确认要求（§十/§五十二）。
"""

from __future__ import annotations

import re
import time
from typing import Any

from app.integrations.minecraft.identity import CODE_NOT_FOUND
from app.tasks.qq_entry import (
    QQIdentity,
    _strip_mention,
    qq_addressed,
    qq_text_of,
    send_qq,
)

#: 一次待确认绑定的有效期（秒）；过期就当没问过
PENDING_TTL_SECONDS = 300.0
#: 绑定/解绑回复的重试（与任务入口同口径）
REPLY_RETRY_DELAYS = (1.5,)

#: 玩家名（Java 版是 ASCII，但离线模式/代理服允许中文名 —— 只要不是空白或标点）
_NOT_A_NAME = frozenset({"minecraft", "mc", "我的世界"})

_CONFIRM = re.compile(r"^确认绑定[。！!．.]?$")
_UNBIND = re.compile(r"^(解除绑定|解绑|取消绑定)[。！!．.]?$")
#: 「把我和 Minecraft 里的 X 绑定」/「绑定 X」/「bind X」（名字在前或在后都认）
_WORD = r"[^\s，。！？、,.!?]{1,24}"
_BIND = re.compile(
    r"^(?:请)?(?:把)?(?:我)?(?:和|跟|与)?\s*"
    r"(?:minecraft|mc|我的世界)?\s*(?:里的|中的|里面(?:的)?)?\s*"
    r"(?:(?P<after>" + _WORD + r")\s*(?:绑定|绑起来|绑一下|绑定起来|bind)"
    r"|(?:绑定|绑起来|bind)\s*(?P<before>" + _WORD + r"))"
    r"\s*$",
    re.IGNORECASE,
)


def _name_of(match: re.Match[str]) -> str:
    """两种语序里取出玩家名（「把我和 X 绑定」/「绑定 X」）。"""
    return str(match.group("after") or match.group("before") or "").strip()


_NO_LINK = "我这边还没有记录过我们的身份绑定。"


class MinecraftIdentityCommands:
    """把 QQ 消息里的绑定意图接进身份桥（认领式订阅，绝不抢普通对话）。"""

    def __init__(
        self,
        bot: Any,
        bridge: Any = None,
        *,
        clock: Any = time.time,
        logger: Any = None,
    ) -> None:
        self.bot = bot
        self.bridge = bridge
        self._clock = clock
        self._log = logger
        #: 会话 → (player_uuid, username, server_id, expires_at)
        self._pending: dict[str, tuple[str, str, str, float]] = {}

    # ------------------------------------------------------------ 入口

    async def on_message(self, event: Any) -> bool:
        """事件总线订阅入口；返回 True = 这条消息归身份命令管。"""
        try:
            return await self._handle(event)
        except Exception:  # noqa: BLE001 - 绝不把异常抛给聊天管线
            if self._log is not None:
                self._log.exception("[Minecraft.Identity] 处理消息失败（忽略）")
            return False

    async def _handle(self, event: Any) -> bool:
        if self.bridge is None or getattr(self.bridge, "identities", None) is None:
            return False
        text = self._text_of(event)
        if not text:
            return False
        identity = QQIdentity.from_event(event)
        if not identity.user_id:
            return False
        if identity.is_group and not self._addressed(event):
            return False
        if identity.is_group:
            text = _strip_mention(text, str(getattr(event, "self_id", "") or ""))

        if _UNBIND.match(text):
            await self._say(identity, await self._unbind(identity))
            return True
        if _CONFIRM.match(text):
            reply = await self._confirm(identity)
            if reply:
                await self._say(identity, reply)
                return True
            return False
        match = _BIND.match(text)
        if match is None:
            return False
        await self._say(identity, await self._request(identity, _name_of(match)))
        return True

    # ------------------------------------------------------------ 三个动作

    async def _request(self, identity: QQIdentity, username: str) -> str:
        """第一步：只**提出**绑定，拿到真实 UUID 后请对方明确确认。"""
        bridge = self.bridge
        name = str(username or "").strip()
        if not name or name.lower() in _NOT_A_NAME:
            return (
                "要绑的是 Minecraft 里的哪个玩家名？说「把我和 Minecraft 里的 <玩家名> 绑定」就行。"
            )
        player = bridge.player_named(name)
        if player is None:
            return f"我在这个服务器里没看到「{name}」在线。先让他（你）进服务器，我见到人了再绑。"
        server = bridge.server()
        self._pending[identity.session_id] = (
            player.player_uuid,
            player.username,
            player.server_id,
            self._clock() + PENDING_TTL_SECONDS,
        )
        return (
            f"我看到的服务器是 {server.display()}，在线玩家是 {player.username}"
            f"（UUID 尾号 {player.short_uuid()}）。\n"
            "确认是你本人在玩的话，回我一句「确认绑定」。"
        )

    async def _confirm(self, identity: QQIdentity) -> str:
        pending = self._pending.get(identity.session_id)
        if pending is None:
            return ""
        player_uuid, username, server_id, expires_at = pending
        if self._clock() > expires_at:
            self._pending.pop(identity.session_id, None)
            return "刚才那次绑定请求已经过期了，再说一次「把我和 Minecraft 里的 X 绑定」吧。"
        # 重新解析：确认期间玩家可能已经下线 / 服务器可能换了人
        player = self.bridge.player_named(username)
        if player is None or player.player_uuid != player_uuid:
            self._pending.pop(identity.session_id, None)
            return f"「{username}」现在不在线（或者已经不是刚才那个人了），我这边就什么都没改。"
        self._pending.pop(identity.session_id, None)
        outcome = await self.bridge.bind(
            platform=identity.platform, user_id=identity.user_id, username=player.username
        )
        if outcome is None:  # 存储器不可用 / 玩家消失：如实说，绝不假装成功
            return "我的身份记录现在读不到，这次没有绑定成功。"
        if not outcome.ok:
            return outcome.message or "这次绑定没有成功。"
        return f"记住了：这里是 {username}，以后我会认得你。"

    async def _unbind(self, identity: QQIdentity) -> str:
        self._pending.pop(identity.session_id, None)
        outcome = await self.bridge.unbind(platform=identity.platform, user_id=identity.user_id)
        if outcome is None:
            return "我的身份记录现在读不到，这次没有解除成功。"
        if not outcome.ok:
            # 没有绑定 = 目标状态已经达成，不算失败
            if outcome.code == CODE_NOT_FOUND:
                return _NO_LINK
            return outcome.message or "这次解除没有成功。"
        return "好，我这边先不认这个身份了（以前记住的事情我不会删掉）。"

    # ------------------------------------------------------------ 内部

    async def _say(self, identity: QQIdentity, text: str) -> None:
        await send_qq(self.bot, identity, text, delays=REPLY_RETRY_DELAYS, logger=self._log)

    def _text_of(self, event: Any) -> str:
        return qq_text_of(event)

    def _addressed(self, event: Any) -> bool:
        """群里只有 @她 / 回复她那条消息才进身份命令（普通群聊照旧）。"""
        return qq_addressed(self.bot, event)
