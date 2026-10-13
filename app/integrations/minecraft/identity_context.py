"""跨平台身份上下文解析（Phase 8B §四/§五）。

本模块是**只读适配器**：把「这一轮说话的人是谁」解析成一个可审计的结构化事实，
交给角色对话上下文使用。它复用既有身份桥（:class:`app.integrations.minecraft.identity.IdentityStore`，
经 ``bot.minecraft_memory.identities`` 暴露）与既有 ``MinecraftIdentity``，
**不新建第二套 Store、不加表、不加迁移、不缓存绑定**。

三条硬边界（任务书 §七）：

* 身份核验（``VERIFIED``）**只**证明「两个平台账号是同一个主体」，**不等于动作授权**。
  游戏内 LOW/MEDIUM 是否可执行仍由既有 Policy / ``trusted_players`` / ``TurnOrigin`` /
  确认门 / ``TaskAuthorization`` 决定 —— 本模块**不碰**它们，也**不写** ``origin_metadata``
  （那是给策略读的运输层事实；往里塞身份事实等于变相扩权）。
* 解析失败 / 未绑定 / 已解除 / 冲突 / 服务器不匹配**一律 fail-closed 为未知身份**：
  绝不按昵称猜人、绝不跨服务器复用绑定、绝不把 MC username 当成 QQ 身份。
* 不记录聊天原文、Prompt、思维链、凭据、完整 UUID 或不必要的 QQ 号。给模型的最小事实
  （:meth:`IdentityContext.facts`）里没有完整 UUID、没有 QQ 号、没有绑定备注。

Minecraft 入站的**权威 UUID 来自运行时**：``minecraft.chat`` 事件本身只有 username
（见 ``minecraft_runtime/runtime.js`` 的三处 chat 上报），而在线玩家视图带着 mineflayer
给的稳定 UUID。因此 MC 侧解析顺序固定为：

    username → 在线玩家视图（权威 UUID）→ IdentityStore.by_player(server_id, uuid) → 跨平台关联

拿不到权威 UUID（玩家不在线 / 感知未就绪 / 未连接）时**不建立任何关联**，
只按游戏内名字认识他（``UNBOUND`` + 稳定原因码）。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from app.integrations.minecraft.identity import (
    CONFLICT,
    REVOKED,
    VERIFIED,
    canonical_uuid,
)

log = logging.getLogger("CatooBot.Minecraft.IdentityContext")

#: Minecraft 会话前缀（与 ``chat_bridge.chat_session_id`` 同口径）
MINECRAFT_SESSION_PREFIX = "minecraft:"
#: QQ 平台名（与 ``identity.PLATFORMS`` / 绑定命令同口径）
PLATFORM_QQ = "qq"
#: 本模块对权限的**唯一**影响（写死成常量，测试可断言）
PERMISSION_EFFECT = "none"


class IdentityResolution(StrEnum):
    """一次入站回合的身份解析结论（任务书 §四 的有限枚举）。"""

    VERIFIED = "VERIFIED"
    UNBOUND = "UNBOUND"
    REVOKED = "REVOKED"
    CONFLICT = "CONFLICT"
    SERVER_MISMATCH = "SERVER_MISMATCH"
    DEGRADED = "DEGRADED"


class IdentityChannel(StrEnum):
    """回合来自哪个通道（会话/历史隔离按它判断，§四 2 / §六）。"""

    QQ_PRIVATE = "qq_private"
    QQ_GROUP = "qq_group"
    MINECRAFT_CHAT = "minecraft_chat"
    WEBUI = "webui"


def channel_of(
    *, session_id: str, is_group: bool = False, origin_metadata: Mapping[str, Any] | None = None
) -> IdentityChannel:
    """会话 + 运输层事实 → 通道。**只看来源事实，绝不看用户说了什么。**"""
    source = str((origin_metadata or {}).get("source") or "").strip().lower()
    if str(session_id or "").startswith(MINECRAFT_SESSION_PREFIX) or source.startswith("minecraft"):
        return IdentityChannel.MINECRAFT_CHAT
    if source == "webui":
        return IdentityChannel.WEBUI
    return IdentityChannel.QQ_GROUP if is_group else IdentityChannel.QQ_PRIVATE


@dataclass(frozen=True)
class IdentityContext:
    """一轮的身份事实（只读；**没有任何授权能力**）。

    ``person_ref`` / ``platform_user_id`` 是**内部**稳定引用：只用于关系键、记忆检索与
    结构化日志，绝不进 Prompt、不进普通用户可见输出（§四 6）。
    """

    resolution: IdentityResolution
    channel: IdentityChannel
    #: 内部稳定引用：VERIFIED 时是已核验对手（``qq:12345``），否则是本通道本地身份
    person_ref: str = ""
    #: 已核验对手的平台/平台用户 ID（仅 VERIFIED 有意义；内部使用）
    platform: str = ""
    platform_user_id: str = ""
    #: 当前服务器（身份解析都是"这台服务器上"的事实）
    server_id: str = ""
    #: 显示名（游戏内玩家名 / 昵称），只用于显示
    username: str = ""
    #: UUID 后四位（确认/诊断用；绝不暴露完整 UUID）
    uuid_suffix: str = ""
    #: 稳定原因码（诊断用；不放用户原文）
    reason: str = ""
    #: 关系/连续性键：VERIFIED → 对手平台 ID（与 QQ 侧同键）；否则 → 本通道本地 ID
    relationship_key: str = ""
    #: 只读描述（**不是**授权凭据；权限永远由 Policy/确认门决定）
    action_trust: str = "identity_verified_only"
    #: 解析过程中观察到的存储降级原因（空 = 没观察到降级）
    degraded_reason: str = ""

    @property
    def verified(self) -> bool:
        return self.resolution is IdentityResolution.VERIFIED

    @property
    def permission_effect(self) -> str:
        """写死 "none"：身份上下文对权限的影响恒为空（§七 可逐条证明）。"""
        return PERMISSION_EFFECT

    def memory_identity(self, *, platform: str, user_id: str) -> tuple[str, str]:
        """记忆检索用的 ``(platform, user_id)``。

        VERIFIED → 对手的 ``(platform, user_id)``（于是游戏内说话也能检索到
        "这个人在 QQ 这边"的既有记忆）；否则 → 调用方原本的本地键（行为不变）。
        """
        if self.verified and self.platform and self.platform_user_id:
            return self.platform, self.platform_user_id
        return platform, user_id

    def facts(self) -> str:
        """给模型的**最小**身份事实（没有 UUID、没有 QQ 号、没有备注）。

        ``UNBOUND`` 返回空串：什么都不说比说废话好（绝大多数回合都是这种）。
        其余结论都给一句**明确、只读、无权限效果**的说明。
        """
        name = self.username or "这个人"
        if self.resolution is IdentityResolution.VERIFIED:
            return (
                f"身份（只读事实）：游戏内玩家 {name} 与你在 QQ 上认识的是**同一个已核验的人**，"
                "不是两个人；关系与共同经历是同一条。身份核验**不等于**动作授权——"
                "能不能在游戏里做某个动作，仍然只看当时的信任配置与确认结果。"
            )
        if self.resolution is IdentityResolution.REVOKED:
            return (
                f"身份（只读事实）：{name} 的跨平台身份关联**已被解除**，"
                "不要再沿用旧的跨平台关系或称呼。"
            )
        if self.resolution is IdentityResolution.CONFLICT:
            return (
                f"身份（只读事实）：{name} 的身份记录存在**冲突**，"
                "本轮按未知身份处理，不建立跨平台关联。"
            )
        if self.resolution is IdentityResolution.SERVER_MISMATCH:
            return (
                "身份（只读事实）：当前服务器的身份信息不可用或不匹配，"
                "本轮按未知身份处理（绑定绝不跨服务器复用）。"
            )
        if self.resolution is IdentityResolution.DEGRADED:
            return "身份（只读事实）：身份模块暂时不可用（降级），本轮按未知身份处理。"
        return ""

    def log_fields(self) -> dict[str, Any]:
        """结构化日志字段（稳定原因码 + 枚举；不含原文、不含完整 UUID/QQ 号）。"""
        return {
            "resolution": str(self.resolution),
            "channel": str(self.channel),
            "reason": self.reason,
            "server_matched": bool(self.verified and self.server_id),
            "uuid_suffix": self.uuid_suffix,
            "platform": self.platform if self.verified else "",
            "degraded": self.degraded_reason,
            "permission_effect": PERMISSION_EFFECT,
        }


def _context(
    *, channel: IdentityChannel, username: str, relationship_key: str, **extra: Any
) -> IdentityContext:
    return IdentityContext(
        channel=channel, username=username, relationship_key=relationship_key, **extra
    )


class IdentityResolver:
    """只读身份解析器（复用既有 ``IdentityStore``；**没有**自己的绑定缓存）。

    ``bridge_getter`` 是一个**延迟**取用 minecraft 记忆桥（``bot.minecraft_memory``）的
    可调用对象：装配顺序上角色运行时可能先于记忆桥存在，这里不做假设。
    """

    def __init__(self, bridge_getter: Any = None) -> None:
        self._bridge_getter = bridge_getter

    def _bridge(self) -> Any:
        if self._bridge_getter is None:
            return None
        try:
            return self._bridge_getter()
        except Exception:  # noqa: BLE001 - 取桥失败就是"没有身份层"
            return None

    async def resolve(
        self,
        *,
        session_id: str,
        user_id: int | str,
        user_name: str = "",
        is_group: bool = False,
        origin_metadata: Mapping[str, Any] | None = None,
    ) -> IdentityContext:
        """解析一轮的身份。**永不抛异常**：任何失败都降级为 ``DEGRADED``。"""
        channel = channel_of(
            session_id=session_id, is_group=is_group, origin_metadata=origin_metadata
        )
        local = str(user_name or user_id or "")
        try:
            return await self._resolve(
                channel=channel,
                session_id=session_id,
                user_id=user_id,
                user_name=user_name,
                origin_metadata=origin_metadata,
            )
        except Exception as exc:  # noqa: BLE001 - 身份解析绝不影响聊天
            log.warning("[Identity] resolve failed (degraded): %s", exc)
            return _context(
                channel=channel,
                username=local,
                relationship_key=str(user_id),
                resolution=IdentityResolution.DEGRADED,
                reason="resolve_error",
                degraded_reason=type(exc).__name__,
                action_trust="identity_unavailable",
            )

    async def _resolve(
        self,
        *,
        channel: IdentityChannel,
        session_id: str,
        user_id: int | str,
        user_name: str,
        origin_metadata: Mapping[str, Any] | None,
    ) -> IdentityContext:
        local = str(user_name or user_id or "")
        local_key = str(user_id or local)
        bridge = self._bridge()
        identities = getattr(bridge, "identities", None) if bridge is not None else None
        if identities is None:
            return _context(
                channel=channel,
                username=local,
                relationship_key=local_key,
                resolution=IdentityResolution.DEGRADED,
                reason="identity_layer_absent",
                action_trust="identity_unavailable",
            )
        server_id = str(bridge.server_id() or "")
        # Phase 8B Follow-up §三：降级判定**只看本次读取的逐次结果**，
        # 不再比较 degraded_reason 是否变化 —— 同类异常连续发生时字符串不变，
        # 那种判据会把「读取失败」误判成「没有绑定」（返回 UNBOUND）。
        if channel is IdentityChannel.MINECRAFT_CHAT:
            return await self._resolve_minecraft(
                bridge=bridge,
                identities=identities,
                channel=channel,
                username=local,
                server_id=server_id,
            )
        return await self._resolve_platform(
            identities=identities,
            channel=channel,
            user_id=str(user_id or ""),
            username=local,
            server_id=server_id,
        )

    async def _read(
        self,
        store: Any,
        *,
        read_name: str,
        legacy_name: str,
        kwargs: dict[str, Any],
        many: bool = False,
    ) -> tuple[Any, str]:
        """**单次**存储读取 → ``(值, error)``（error 非空 == 这次读取失败）。

        优先用 ``IdentityStore`` 的 ``*_read`` 接口：它逐次返回成功/失败，因此
        「同类异常连续发生」时每一次都能被识别（§三 2/3），且**没有任何共享可变
        状态** → 并发解析天然互不干扰（§三 4）。没有该接口的旧替身回退到旧方法 +
        ``degraded_reason`` 变化判据（兼容路径；真实 Store 始终提供 ``*_read``）。
        """
        reader = getattr(store, read_name, None)
        if callable(reader):
            result = await reader(**kwargs)
            value = getattr(result, "links" if many else "link", None)
            return value, str(getattr(result, "error", "") or "")
        before = str(getattr(store, "degraded_reason", "") or "")
        value = await getattr(store, legacy_name)(**kwargs)
        after = str(getattr(store, "degraded_reason", "") or "")
        error = after if (after and after != before) else ""
        return (tuple(value or ()) if many else value), error

    def _degraded(
        self,
        *,
        channel: IdentityChannel,
        username: str,
        relationship_key: str,
        server_id: str,
        error: str,
    ) -> IdentityContext:
        """读取失败 → 明确 ``DEGRADED``（绝不假装「没有绑定」，也不授予任何权限）。"""
        return IdentityContext(
            resolution=IdentityResolution.DEGRADED,
            channel=channel,
            username=username,
            relationship_key=relationship_key,
            server_id=server_id,
            reason="identity_store_error",
            degraded_reason=error,
            action_trust="identity_unavailable",
        )

    async def _resolve_minecraft(
        self,
        *,
        bridge: Any,
        identities: Any,
        channel: IdentityChannel,
        username: str,
        server_id: str,
    ) -> IdentityContext:
        """MC 入站：只有 username 时必须先拿运行时给的权威 UUID（任务书 §五）。"""
        base: dict[str, Any] = {
            "channel": channel,
            "username": username,
            "server_id": server_id,
            "relationship_key": username,
        }
        if not username:
            return IdentityContext(
                resolution=IdentityResolution.DEGRADED, reason="no_player_name", **base
            )
        finder = getattr(bridge, "player_named", None)
        player = finder(username) if callable(finder) else None
        raw_uuid = getattr(player, "player_uuid", "") if player is not None else ""
        player_uuid = canonical_uuid(raw_uuid)
        if not player_uuid:
            # 只有名字、拿不到权威 UUID（不在线/感知未就绪）→ 不建立任何关联。
            return IdentityContext(
                resolution=IdentityResolution.UNBOUND,
                reason="no_authoritative_uuid",
                **base,
            )
        if not server_id:
            return IdentityContext(
                resolution=IdentityResolution.UNBOUND,
                reason="server_identity_unavailable",
                **base,
            )
        link, error = await self._read(
            identities,
            read_name="by_player_read",
            legacy_name="by_player",
            kwargs={"server_id": server_id, "player_uuid": player_uuid},
        )
        if error:
            return self._degraded(
                channel=channel,
                username=username,
                relationship_key=username,
                server_id=server_id,
                error=error,
            )
        if link is not None and str(link.status) == VERIFIED:
            return self._verified_context(
                channel=channel,
                username=username,
                server_id=server_id,
                link=link,
                uuid_suffix=player_uuid[-4:],
            )
        rows, error = await self._read(
            identities,
            read_name="all_links_read",
            legacy_name="all_links",
            kwargs={"server_id": server_id, "limit": 200},
            many=True,
        )
        if error:
            return self._degraded(
                channel=channel,
                username=username,
                relationship_key=username,
                server_id=server_id,
                error=error,
            )
        mine = [row for row in rows if row.canonical_uuid == player_uuid]
        if any(str(row.status) == CONFLICT for row in mine):
            return IdentityContext(
                resolution=IdentityResolution.CONFLICT, reason="conflict_record", **base
            )
        if any(str(row.status) == REVOKED for row in mine):
            return IdentityContext(
                resolution=IdentityResolution.REVOKED, reason="link_revoked", **base
            )
        return IdentityContext(
            resolution=IdentityResolution.UNBOUND, reason="no_verified_link", **base
        )

    async def _resolve_platform(
        self,
        *,
        identities: Any,
        channel: IdentityChannel,
        user_id: str,
        username: str,
        server_id: str,
    ) -> IdentityContext:
        """QQ/WebUI 入站：只用平台稳定 user id（绝不用昵称）。"""
        base: dict[str, Any] = {
            "channel": channel,
            "username": username,
            "server_id": server_id,
            "relationship_key": user_id,
        }
        if not user_id:
            return IdentityContext(
                resolution=IdentityResolution.DEGRADED, reason="no_platform_user_id", **base
            )
        link = None
        if server_id:
            link, error = await self._read(
                identities,
                read_name="verified_read",
                legacy_name="verified",
                kwargs={"platform": PLATFORM_QQ, "user_id": user_id, "server_id": server_id},
            )
            if error:
                return self._degraded(
                    channel=channel,
                    username=username,
                    relationship_key=user_id,
                    server_id=server_id,
                    error=error,
                )
        if link is not None and str(link.status) == VERIFIED:
            return self._verified_context(
                channel=channel,
                username=username,
                server_id=server_id,
                link=link,
                uuid_suffix=canonical_uuid(link.player_uuid)[-4:],
            )
        rows, error = await self._read(
            identities,
            read_name="links_for_read",
            legacy_name="links_for",
            kwargs={"platform": PLATFORM_QQ, "user_id": user_id, "include_revoked": True},
            many=True,
        )
        if error:
            return self._degraded(
                channel=channel,
                username=username,
                relationship_key=user_id,
                server_id=server_id,
                error=error,
            )
        if any(str(row.status) == CONFLICT for row in rows):
            return IdentityContext(
                resolution=IdentityResolution.CONFLICT, reason="conflict_record", **base
            )
        if any(str(row.status) == VERIFIED for row in rows):
            # 有生效绑定但不在当前服务器（或当前服务器身份不可用）→ 绝不跨服复用。
            return IdentityContext(
                resolution=IdentityResolution.SERVER_MISMATCH,
                reason="server_identity_mismatch",
                **base,
            )
        if any(str(row.status) == REVOKED for row in rows):
            return IdentityContext(
                resolution=IdentityResolution.REVOKED, reason="link_revoked", **base
            )
        return IdentityContext(
            resolution=IdentityResolution.UNBOUND, reason="no_verified_link", **base
        )

    @staticmethod
    def _verified_context(
        *,
        channel: IdentityChannel,
        username: str,
        server_id: str,
        link: Any,
        uuid_suffix: str,
    ) -> IdentityContext:
        platform = str(link.platform or "")
        platform_user_id = str(link.user_id or "")
        return IdentityContext(
            resolution=IdentityResolution.VERIFIED,
            channel=channel,
            person_ref=f"{platform}:{platform_user_id}",
            platform=platform,
            platform_user_id=platform_user_id,
            server_id=server_id,
            username=str(link.username or username),
            uuid_suffix=uuid_suffix,
            reason="verified_link",
            # §四 3：同一个人在 QQ 与游戏内共用同一条关系（键就是对手平台 ID）。
            relationship_key=platform_user_id,
        )
