"""Phase 5C：Minecraft 身份桥（Canonical Identity + QQ ↔ Minecraft IdentityLink）。

**身份只认稳定标识**（§三/§四/§六）：

* 服务器：``server_id`` 由 ``edition|host|port|world_key`` 确定性派生 —— 同一台服务器重启
  之后不变、不同服务器不同（绝不把 ``127.0.0.1:25565`` 当永久身份）。
* 玩家：``player_uuid`` 是 **canonical identity**（来自运行时/mineflayer），
  ``username`` 只用于显示与说话 —— 改名不该换人，重名不该认成同一个人。
* 绑定必须有**明确来源**（§六 优先级）：用户显式验证 > 既有 trusted/显式配置 > 无绑定。
  **绝不**因为"QQ 昵称 == 玩家名"或名字相似就自动绑定；LLM 永远不能推测身份。

身份关系只是"谁是谁"，**不携带任何权限**（§九）：权限仍然只由 Policy / Confirmation /
TaskAuthorization 决定，Memory 与 IdentityLink 都不能授予 trusted。
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("CatooBot.Minecraft.Identity")

#: 身份桥认识哪些平台（platform 只用于身份键，不参与权限）
PLATFORMS = ("qq", "minecraft_chat", "webui", "system")

#: 绑定状态（§八：解除绑定只是失效，历史不删）
VERIFIED = "VERIFIED"
REVOKED = "REVOKED"
CONFLICT = "CONFLICT"

#: 绑定来源（§六 的优先级；审计要看得见"凭什么绑上的"）
SOURCE_EXPLICIT = "explicit_verification"  # 用户明确说"绑定"并确认（最高优先级）
SOURCE_CONFIGURED = "configured"  # 项目既有显式配置（例如 core_friend_identities 的 QQ↔玩家名）
SOURCE_TRUSTED = "trusted_player"  # 既有 trusted 玩家配置（仍需与显式身份配成一对）
SOURCE_SERVER_UUID = "server_uuid"  # 服务器侧已有的 UUID 绑定

SOURCE_PRIORITY: dict[str, int] = {
    SOURCE_EXPLICIT: 0,
    SOURCE_CONFIGURED: 1,
    SOURCE_TRUSTED: 2,
    SOURCE_SERVER_UUID: 3,
}

#: 稳定错误码（QQ/WebUI 直接用它们翻译成人话）
CODE_CONFLICT = "identity.conflict"
CODE_NOT_FOUND = "identity.not_found"
CODE_UNAVAILABLE = "identity.store_unavailable"
CODE_INVALID = "identity.invalid"


def canonical_uuid(value: Any) -> str:
    """把 UUID 规范成小写无连字符（``RinsoraNeko`` 的 uuid 可能带连字符/大写）。"""
    raw = str(value or "").strip().lower().replace("-", "")
    return raw if len(raw) == 32 and all(ch in "0123456789abcdef" for ch in raw) else ""


@dataclass(frozen=True)
class MinecraftServerIdentity:
    """一台 Minecraft 服务器的稳定身份（§四）。"""

    server_id: str
    host: str = ""
    port: int = 0
    edition: str = "java"
    world_key: str = ""
    label: str = ""

    def display(self) -> str:
        if self.label:
            return self.label
        if self.host:
            return f"{self.host}:{self.port}" if self.port else self.host
        return self.server_id


def server_identity(
    host: str | None,
    port: int | str | None,
    *,
    edition: str = "java",
    world_key: str = "",
    label: str = "",
) -> MinecraftServerIdentity:
    """确定性 server_id：``edition|host|port|world_key`` 的稳定摘要。

    同一台服务器重启 → 同一个 id；换了 host/port/世界 → 不同 id。
    拿不到 host（还没连过）时返回空 id —— 绝不编一个"看起来像"的 id。
    """
    clean_host = str(host or "").strip().lower()
    try:
        clean_port = int(port) if port is not None and str(port) != "" else 0
    except (TypeError, ValueError):
        clean_port = 0
    if not clean_host:
        return MinecraftServerIdentity(
            server_id="", edition=edition, world_key=world_key, label=label
        )
    raw = f"{edition}|{clean_host}|{clean_port}|{str(world_key or '').strip().lower()}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
    return MinecraftServerIdentity(
        server_id=f"mc-{digest}",
        host=clean_host,
        port=clean_port,
        edition=edition,
        world_key=world_key,
        label=label,
    )


@dataclass(frozen=True)
class MinecraftIdentity:
    """一个玩家在**某台服务器**上的身份（§三）：uuid 是 identity，username 只是显示名。"""

    server_id: str
    player_uuid: str = ""
    username: str = ""

    @property
    def key(self) -> str:
        return f"{self.server_id}:{canonical_uuid(self.player_uuid)}"

    def short_uuid(self) -> str:
        """给用户看后四位（§七 的确认摘要）—— 绝不暴露完整 UUID。"""
        clean = canonical_uuid(self.player_uuid)
        return clean[-4:] if clean else ""

    def display(self) -> str:
        suffix = self.short_uuid()
        return f"{self.username}（……{suffix}）" if suffix else (self.username or "未知玩家")


@dataclass
class IdentityLink:
    """一条 QQ ↔ Minecraft 绑定（§五）。"""

    link_id: str
    platform: str
    user_id: str
    server_id: str
    player_uuid: str
    username: str = ""
    status: str = VERIFIED
    source: str = SOURCE_EXPLICIT
    created_at: float = 0.0
    verified_at: float = 0.0
    revoked_at: float = 0.0
    note: str = ""

    @property
    def canonical_uuid(self) -> str:
        return canonical_uuid(self.player_uuid)

    @property
    def active(self) -> bool:
        return self.status == VERIFIED

    def identity(self) -> MinecraftIdentity:
        return MinecraftIdentity(
            server_id=self.server_id, player_uuid=self.canonical_uuid, username=self.username
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "link_id": self.link_id,
            "platform": self.platform,
            "user_id": self.user_id,
            "server_id": self.server_id,
            "player_uuid": self.player_uuid,
            "username": self.username,
            "status": self.status,
            "source": self.source,
            "created_at": self.created_at,
            "verified_at": self.verified_at,
            "revoked_at": self.revoked_at,
            "note": self.note,
        }

    def user_facing(self) -> dict[str, Any]:
        """给 QQ/WebUI 的只读投影（不暴露完整 UUID）。"""
        return {
            "platform": self.platform,
            "user_id": self.user_id,
            "server_id": self.server_id,
            "username": self.username,
            "uuid_suffix": self.identity().short_uuid(),
            "status": self.status,
            "source": self.source,
            "verified_at": self.verified_at,
        }

    @classmethod
    def from_row(cls, row: Mapping[str, Any]) -> IdentityLink:
        return cls(
            link_id=str(row["link_id"]),
            platform=str(row["platform"]),
            user_id=str(row["user_id"]),
            server_id=str(row["server_id"]),
            player_uuid=str(row["player_uuid"]),
            username=str(row["username"] or ""),
            status=str(row["status"]),
            source=str(row["source"] or SOURCE_EXPLICIT),
            created_at=float(row["created_at"] or 0.0),
            verified_at=float(row["verified_at"] or 0.0),
            revoked_at=float(row["revoked_at"] or 0.0),
            note=str(row["note"] or ""),
        )


@dataclass
class LinkOutcome:
    """一次绑定/解绑的结果（QQ 层据此说人话，绝不泄漏内部细节）。"""

    ok: bool
    code: str = ""
    link: IdentityLink | None = None
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def message(self) -> str:
        return LINK_MESSAGES.get(self.code, "这件事现在做不了。" if not self.ok else "")


#: 稳定错误码 → 人话（QQ/WebUI 复用）
LINK_MESSAGES: dict[str, str] = {
    CODE_CONFLICT: "这个 Minecraft 玩家已经绑定到别人了，我不能抢过来。",
    CODE_NOT_FOUND: "我这边没有这个绑定。",
    CODE_UNAVAILABLE: "我的身份记录现在读不到，先记着这件事，等会儿再试。",
    CODE_INVALID: "这个绑定请求我看不明白（需要服务器 + 玩家 UUID）。",
}


class IdentityStore:
    """身份桥的持久化（复用同一个 SQLite；失败只降级，绝不影响任务执行，§三十九/§四十）。"""

    def __init__(self, database: Any, *, clock: Any = time.time, logger: Any = None) -> None:
        self._db = database
        self._clock = clock
        self._log = logger or log
        #: 最近一次失败的原因（WebUI/日志里如实显示"身份层降级"）
        self.degraded_reason = ""

    # ------------------------------------------------------------ 写

    async def verify(
        self,
        *,
        platform: str,
        user_id: str,
        server_id: str,
        player_uuid: str,
        username: str = "",
        source: str = SOURCE_EXPLICIT,
        note: str = "",
    ) -> LinkOutcome:
        """建立（或刷新）一条绑定：**已经有别人的 VERIFIED 绑定就拒绝**（§三十五）。"""
        clean = canonical_uuid(player_uuid)
        if (
            str(platform) not in PLATFORMS
            or not str(user_id).strip()
            or not str(server_id).strip()
            or not clean
        ):
            return LinkOutcome(False, CODE_INVALID, detail={"player_uuid": str(player_uuid)[:36]})
        now = float(self._clock())
        try:
            holder = await self._verified_row("", "", server_id, clean)
            if holder is not None and not (
                str(holder["platform"]) == str(platform) and str(holder["user_id"]) == str(user_id)
            ):
                # 已经有别人绑上了同一个 UUID → 冲突，拒绝新绑定（绝不自动覆盖）
                existing = IdentityLink.from_row(holder)
                await self._record_conflict(platform, user_id, server_id, clean, username, now)
                return LinkOutcome(
                    False,
                    CODE_CONFLICT,
                    detail={
                        "held_by": {"platform": existing.platform, "user_id": existing.user_id}
                    },
                )
            mine = await self._verified_row(platform, user_id, server_id, "")
            if mine is not None:
                # 同一个人同一台服务器：只刷新显示名/来源（改名不换人）
                await self._db.execute(
                    "UPDATE minecraft_identity_links"
                    " SET username = ?, source = ?, note = ?, verified_at = ?"
                    " WHERE link_id = ?",
                    (str(username or ""), str(source), str(note or ""), now, str(mine["link_id"])),
                )
                return LinkOutcome(
                    True,
                    link=IdentityLink.from_row(
                        {**dict(mine), "username": username, "source": source}
                    ),
                )
            link = IdentityLink(
                link_id=f"mclink_{uuid.uuid4().hex[:12]}",
                platform=str(platform),
                user_id=str(user_id),
                server_id=str(server_id),
                player_uuid=clean,
                username=str(username or ""),
                status=VERIFIED,
                source=str(source),
                created_at=now,
                verified_at=now,
                note=str(note or ""),
            )
            await self._db.execute(
                "INSERT INTO minecraft_identity_links"
                " (link_id, platform, user_id, server_id, player_uuid, username, status, source,"
                "  created_at, verified_at, revoked_at, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    link.link_id,
                    link.platform,
                    link.user_id,
                    link.server_id,
                    link.player_uuid,
                    link.username,
                    link.status,
                    link.source,
                    link.created_at,
                    link.verified_at,
                    link.revoked_at,
                    link.note,
                ),
            )
            self.degraded_reason = ""
            self._log.info(
                "[Minecraft.Identity] verified platform=%s user=%s server=%s uuid=…%s",
                link.platform,
                link.user_id,
                link.server_id,
                link.identity().short_uuid(),
            )
            return LinkOutcome(True, link=link)
        except Exception as exc:  # noqa: BLE001 - 身份层故障只降级（§三十九）
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] verify failed (degraded): %s", exc)
            return LinkOutcome(False, CODE_UNAVAILABLE, detail={"error": str(exc)[:120]})

    async def revoke(self, *, platform: str, user_id: str, server_id: str = "") -> LinkOutcome:
        """解除绑定（§八）：状态置 REVOKED，**历史保留**（不删行）。"""
        now = float(self._clock())
        try:
            rows = await self._rows(
                "SELECT * FROM minecraft_identity_links WHERE platform = ? AND user_id = ?"
                " AND status = ? AND (? = '' OR server_id = ?)",
                (str(platform), str(user_id), VERIFIED, str(server_id), str(server_id)),
            )
            if not rows:
                return LinkOutcome(False, CODE_NOT_FOUND)
            for row in rows:
                await self._db.execute(
                    "UPDATE minecraft_identity_links"
                    " SET status = ?, revoked_at = ? WHERE link_id = ?",
                    (REVOKED, now, str(row["link_id"])),
                )
            self._log.info(
                "[Minecraft.Identity] revoked platform=%s user=%s (%d link(s))",
                platform,
                user_id,
                len(rows),
            )
            return LinkOutcome(True, link=IdentityLink.from_row(rows[0]))
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] revoke failed (degraded): %s", exc)
            return LinkOutcome(False, CODE_UNAVAILABLE)

    # ------------------------------------------------------------ 读

    async def verified(self, *, platform: str, user_id: str, server_id: str) -> IdentityLink | None:
        """这个人在这台服务器上绑定的玩家（没有就是 None —— 不做任何名字猜测）。"""
        try:
            row = await self._verified_row(platform, user_id, server_id, "")
            return IdentityLink.from_row(row) if row is not None else None
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] read failed (degraded): %s", exc)
            return None

    async def by_player(self, *, server_id: str, player_uuid: str) -> IdentityLink | None:
        """反过来：这个玩家绑给了谁。"""
        clean = canonical_uuid(player_uuid)
        if not clean:
            return None
        try:
            row = await self._verified_row("", "", server_id, clean)
            return IdentityLink.from_row(row) if row is not None else None
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] read failed (degraded): %s", exc)
            return None

    async def links_for(
        self, *, platform: str, user_id: str, include_revoked: bool = True
    ) -> list[IdentityLink]:
        try:
            sql = "SELECT * FROM minecraft_identity_links WHERE platform = ? AND user_id = ?"
            if not include_revoked:
                sql += " AND status = 'VERIFIED'"
            sql += " ORDER BY verified_at DESC"
            rows = await self._rows(sql, (str(platform), str(user_id)))
            return [IdentityLink.from_row(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] list failed (degraded): %s", exc)
            return []

    async def counts(self) -> dict[str, int]:
        try:
            rows = await self._rows(
                "SELECT status, COUNT(*) AS n FROM minecraft_identity_links GROUP BY status", ()
            )
            return {str(row["status"]): int(row["n"]) for row in rows}
        except Exception:  # noqa: BLE001
            return {}

    async def all_links(self, *, server_id: str = "", limit: int = 100) -> list[IdentityLink]:
        """这台服务器上（或全部）的绑定，最新在前（**含已解除的**，§九 历史不删）。"""
        try:
            sql = "SELECT * FROM minecraft_identity_links"
            params: list[Any] = []
            if server_id:
                sql += " WHERE server_id = ?"
                params.append(str(server_id))
            sql += " ORDER BY verified_at DESC LIMIT ?"
            params.append(max(1, min(int(limit), 500)))
            rows = await self._rows(sql, tuple(params))
            return [IdentityLink.from_row(row) for row in rows]
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._log.warning("[Minecraft.Identity] list failed (degraded): %s", exc)
            return []

    # ------------------------------------------------------------ 内部

    async def _verified_row(
        self, platform: str, user_id: str, server_id: str, player_uuid: str
    ) -> Mapping[str, Any] | None:
        clauses = ["status = ?", "server_id = ?"]
        params: list[Any] = [VERIFIED, str(server_id)]
        if platform:
            clauses.append("platform = ?")
            params.append(str(platform))
        if user_id:
            clauses.append("user_id = ?")
            params.append(str(user_id))
        if player_uuid:
            clauses.append("player_uuid = ?")
            params.append(str(player_uuid))
        rows = await self._rows(
            f"SELECT * FROM minecraft_identity_links WHERE {' AND '.join(clauses)}"
            " ORDER BY verified_at DESC LIMIT 1",
            tuple(params),
        )
        return dict(rows[0]) if rows else None

    async def _rows(self, sql: str, params: tuple[Any, ...]) -> list[Mapping[str, Any]]:
        rows = await self._db.fetchall(sql, params)
        return [dict(row) for row in rows]

    async def _record_conflict(
        self,
        platform: str,
        user_id: str,
        server_id: str,
        player_uuid: str,
        username: str,
        now: float,
    ) -> None:
        """把冲突也留档（§三十四：冲突不覆盖，要看得见）。"""
        try:
            await self._db.execute(
                "INSERT INTO minecraft_identity_links"
                " (link_id, platform, user_id, server_id, player_uuid, username, status, source,"
                "  created_at, verified_at, revoked_at, note)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    f"mclink_{uuid.uuid4().hex[:12]}",
                    str(platform),
                    str(user_id),
                    str(server_id),
                    canonical_uuid(player_uuid),
                    str(username or ""),
                    CONFLICT,
                    SOURCE_EXPLICIT,
                    now,
                    0.0,
                    0.0,
                    "已有人绑定：拒绝新绑定",
                ),
            )
        except Exception as exc:  # noqa: BLE001 - 冲突留档失败也不能影响"拒绝"本身
            self._log.warning("[Minecraft.Identity] conflict record failed: %s", exc)
