"""Minecraft 动作确认门（Phase 4A）：MEDIUM/HIGH 动作的用户授权。

设计要点（任务书 §五-§十五）：

* **确认只回答「有没有用户授权」**，执行永远还是 `MinecraftService` → Action Runtime；
* **绑定** `user_id` + `session_id` + `arguments_hash`：别的用户/别的会话/改过的参数
  都不能拿同一个 `confirmation_id` 执行动作（§六/§七）；
* **只能由新的 USER 回合消费**：`INITIATIVE / BACKGROUND / SYSTEM` 一律拒绝（§十一/§十二）；
* **一次性**：PENDING → CONSUMED，再用同一个 id 就是 `confirmation_invalid`（§十四）；
* **只活在内存里**：TTL + 有界 + 重启全部失效，绝不让旧授权复活（§三十六）。

MEDIUM 已经有真实动作（`minecraft_dig`，Phase 4B），它走的正是这套确认流程；
HIGH/DESTRUCTIVE 仍没有实现（风险开关默认关闭）。store 自身的单元测试在
`tests/test_minecraft_confirmation.py`，工具链路上的确认流程在
`tests/test_minecraft_agent_confirm_gate.py`。
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

from app.character.turn import TurnOrigin

log = logging.getLogger("CatooBot.Minecraft.Confirmation")

#: 确认状态机
PENDING = "PENDING"
CONFIRMED = "CONFIRMED"
EXPIRED = "EXPIRED"
CANCELLED = "CANCELLED"
CONSUMED = "CONSUMED"

STATUSES = (PENDING, CONFIRMED, EXPIRED, CANCELLED, CONSUMED)

#: 需要确认的风险等级（§四）：SAFE 直接允许、LOW 只要 USER 回合、MEDIUM 及以上要确认
CONFIRMATION_RISKS = frozenset({"MEDIUM", "HIGH", "DESTRUCTIVE"})

#: 稳定错误码（§三十四）
CODE_REQUIRED = "minecraft.confirmation_required"
CODE_INVALID = "minecraft.confirmation_invalid"
CODE_EXPIRED = "minecraft.confirmation_expired"
CODE_MISMATCH = "minecraft.confirmation_mismatch"
CODE_NOT_USER_TURN = "minecraft.confirmation_not_user_turn"
CODE_NOT_TRUSTED = "minecraft.user_not_trusted"


def arguments_hash(arguments: Mapping[str, Any] | None) -> str:
    """参数指纹：确认的是**哪个具体动作**必须不可变（§七）。"""
    blob = json.dumps(dict(arguments or {}), ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


@dataclass
class MinecraftActionConfirmation:
    """一条待确认/已确认的 Minecraft 动作授权。"""

    confirmation_id: str
    session_id: str
    user_id: str
    tool: str
    risk: str
    arguments_hash: str
    arguments: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0
    expires_at: float = 0.0
    status: str = PENDING
    #: 只给模型/界面的中文摘要（不含机密，不含对话正文）
    summary: str = ""

    @property
    def pending(self) -> bool:
        return self.status == PENDING

    def to_payload(self) -> dict[str, Any]:
        """给 LLM / WebUI 的安全投影（id + 工具 + 风险 + 摘要 + 有效期）。"""
        return {
            "confirmation_id": self.confirmation_id,
            "tool": self.tool,
            "risk": self.risk,
            "summary": self.summary,
            "expires_at": self.expires_at,
            "status": self.status,
        }

    def to_dict(self) -> dict[str, Any]:
        """完整视图（WebUI Debug 用；不含任何机密——本来就只存动作参数）。"""
        return asdict(self)


@dataclass(frozen=True)
class ConfirmationOutcome:
    """消费结果：``ok`` 为真时 ``confirmation`` 有效且已置 CONSUMED。"""

    ok: bool
    confirmation: MinecraftActionConfirmation | None = None
    code: str = ""
    message: str = ""


class ConfirmationStore:
    """有界、TTL、内存态的确认存储（§三十六：重启即全部失效）。"""

    def __init__(
        self,
        *,
        ttl_seconds: float = 60.0,
        max_pending: int = 32,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.ttl_seconds = float(ttl_seconds)
        self.max_pending = int(max_pending)
        self._clock = clock
        self._items: dict[str, MinecraftActionConfirmation] = {}

    # --------------------------------------------------------------- 写

    def create(
        self,
        *,
        session_id: str,
        user_id: str,
        tool: str,
        risk: str,
        arguments: Mapping[str, Any] | None = None,
        summary: str = "",
    ) -> MinecraftActionConfirmation:
        """创建一条 PENDING 确认（同一 (session,user,tool,args) 已有 PENDING 时复用它）。"""
        self.sweep()
        digest = arguments_hash(arguments)
        existing = self.find_pending(
            session_id=session_id, user_id=user_id, tool=tool, arguments=arguments
        )
        if existing is not None:
            return existing
        now = self._clock()
        confirmation = MinecraftActionConfirmation(
            confirmation_id=f"cfm_{secrets.token_hex(6)}",
            session_id=str(session_id),
            user_id=str(user_id),
            tool=tool,
            risk=risk,
            arguments_hash=digest,
            arguments=dict(arguments or {}),
            created_at=now,
            expires_at=now + self.ttl_seconds,
            summary=summary or f"{tool}（{risk}）",
        )
        self._items[confirmation.confirmation_id] = confirmation
        self._trim()
        log.info(
            "[MC Confirmation] created id=%s tool=%s risk=%s user=%s session=%s ttl=%ss",
            confirmation.confirmation_id,
            tool,
            risk,
            user_id,
            session_id,
            int(self.ttl_seconds),
        )
        return confirmation

    def cancel(self, confirmation_id: str) -> bool:
        """作废一条确认（只能缩小授权，不能扩大——管理台/用户取消都走这里）。"""
        item = self._items.get(confirmation_id)
        if item is None or item.status != PENDING:
            return False
        item.status = CANCELLED
        log.info("[MC Confirmation] cancelled id=%s tool=%s", item.confirmation_id, item.tool)
        return True

    def expire(self, confirmation_id: str) -> bool:
        """手动置为 EXPIRED（管理台 Debug / 测试用；真实过期由 TTL 自动判定）。"""
        item = self._items.get(confirmation_id)
        if item is None or item.status != PENDING:
            return False
        item.status = EXPIRED
        log.info("[MC Confirmation] expired id=%s tool=%s", item.confirmation_id, item.tool)
        return True

    # --------------------------------------------------------------- 读

    def get(self, confirmation_id: str) -> MinecraftActionConfirmation | None:
        self.sweep()
        return self._items.get(str(confirmation_id))

    def pending(self) -> list[MinecraftActionConfirmation]:
        self.sweep()
        items = [item for item in self._items.values() if item.status == PENDING]
        return sorted(items, key=lambda item: item.created_at)

    def find_pending(
        self,
        *,
        session_id: str,
        user_id: str,
        tool: str,
        arguments: Mapping[str, Any] | None = None,
    ) -> MinecraftActionConfirmation | None:
        self.sweep()
        digest = arguments_hash(arguments)
        for item in self._items.values():
            if (
                item.status == PENDING
                and item.session_id == str(session_id)
                and item.user_id == str(user_id)
                and item.tool == tool
                and item.arguments_hash == digest
            ):
                return item
        return None

    def latest_for(
        self, *, session_id: str, user_id: str, tool: str
    ) -> MinecraftActionConfirmation | None:
        """同一 (会话, 用户, 工具) 下**最新**的 PENDING/EXPIRED 记录。

        确认流程用它：PENDING → 可消费；EXPIRED → 如实告诉用户「过期了」并重新发起；
        CONSUMED/CANCELLED 视同不存在（一次性语义：要执行就得重新确认）。
        """
        self.sweep()
        candidates = [
            item
            for item in self._items.values()
            if item.session_id == str(session_id)
            and item.user_id == str(user_id)
            and item.tool == tool
            and item.status in (PENDING, EXPIRED)
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item.created_at)

    # ------------------------------------------------------------- 消费

    def consume(
        self,
        confirmation_id: str,
        *,
        session_id: str,
        user_id: str,
        arguments: Mapping[str, Any] | None,
        turn_origin: TurnOrigin,
    ) -> ConfirmationOutcome:
        """用一条确认换一次执行许可（§十一-§十四）。

        顺序固定：**来源 → 存在/状态 → 归属 → 参数指纹 → 置 CONSUMED**。
        任何一步失败都只返回结构化结论，绝不执行、绝不改状态（除了 TTL 落定）。
        """
        self.sweep()
        if not turn_origin.is_user:
            log.info(
                "[MC Confirmation] rejected id=%s reason=not_user_turn origin=%s",
                confirmation_id,
                turn_origin.value,
            )
            return ConfirmationOutcome(
                ok=False,
                code=CODE_NOT_USER_TURN,
                message="只有用户回合的确认有效（本轮不是用户回合）",
            )
        item = self._items.get(str(confirmation_id))
        if item is None:
            return ConfirmationOutcome(ok=False, code=CODE_INVALID, message="确认不存在或已失效")
        if item.status == EXPIRED:
            return ConfirmationOutcome(
                ok=False, code=CODE_EXPIRED, message="确认已过期，请重新发起这个动作"
            )
        if item.status != PENDING:
            # CONSUMED / CANCELLED：一次性 + 不可复活
            return ConfirmationOutcome(ok=False, code=CODE_INVALID, message="确认已使用或已取消")
        if item.session_id != str(session_id) or item.user_id != str(user_id):
            log.info(
                "[MC Confirmation] rejected id=%s reason=hijack user=%s/%s session=%s/%s",
                item.confirmation_id,
                item.user_id,
                user_id,
                item.session_id,
                session_id,
            )
            return ConfirmationOutcome(
                ok=False, code=CODE_MISMATCH, message="这条确认不属于当前用户或会话"
            )
        if item.arguments_hash != arguments_hash(arguments):
            log.info(
                "[MC Confirmation] rejected id=%s reason=arguments_changed tool=%s",
                item.confirmation_id,
                item.tool,
            )
            return ConfirmationOutcome(
                ok=False, code=CODE_MISMATCH, message="动作参数与用户确认时不一致"
            )
        item.status = CONSUMED
        log.info(
            "[MC Confirmation] consumed id=%s tool=%s risk=%s user=%s",
            item.confirmation_id,
            item.tool,
            item.risk,
            item.user_id,
        )
        return ConfirmationOutcome(ok=True, confirmation=item)

    # ------------------------------------------------------------- 维护

    def sweep(self) -> int:
        """把过期的 PENDING 落定为 EXPIRED（TTL 到点即失效）。"""
        now = self._clock()
        expired = 0
        for item in self._items.values():
            if item.status == PENDING and item.expires_at <= now:
                item.status = EXPIRED
                expired += 1
                log.info("[MC Confirmation] expired id=%s tool=%s", item.confirmation_id, item.tool)
        return expired

    def _trim(self) -> None:
        """有界内存：超出上限时把最旧的先作废（绝不静默丢失一条仍有效的授权）。"""
        if len(self._items) <= self.max_pending:
            return
        ordered = sorted(self._items.values(), key=lambda item: item.created_at)
        for item in ordered[: len(self._items) - self.max_pending]:
            if item.status == PENDING:
                item.status = CANCELLED
                log.info("[MC Confirmation] cancelled id=%s reason=bounded", item.confirmation_id)
            self._items.pop(item.confirmation_id, None)

    def snapshot(self) -> dict[str, Any]:
        return {
            "ttl_seconds": self.ttl_seconds,
            "max_pending": self.max_pending,
            "pending": [item.to_dict() for item in self.pending()],
            "total": len(self._items),
        }
