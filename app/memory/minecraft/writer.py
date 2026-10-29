"""Phase 5C：把"值得记住的事"写成 Minecraft 记忆（§三十-§三十二）。

**不是每个事件都写记忆**（§三十）：只写任务结果、玩家出现、重要地点/资源观察、
用户明确说过的事、关系变化。`bot moved 1 block` / `look_at` / `world tick` / `inventory poll`
这种东西永远不进长期记忆。

句子只有一句（§二十五），并且**不带任何内部细节**：没有 action_id、没有 timeout、
没有 Node 事件、没有 pathfinder 调试信息（§三十一/§二）。
"""

from __future__ import annotations

import time
from typing import Any

from app.memory.minecraft.model import (
    DIRECTIVE_CONFIDENCE,
    FactSource,
    Freshness,
    MinecraftMemoryFact,
    MinecraftMemoryKind,
    cluster_position,
    looks_like_directive,
    number_or,
)
from app.memory.minecraft.store import MinecraftMemoryStore

#: 任务结果 → 一句人话（不暴露内部状态）
TASK_OUTCOME_TEXT: dict[str, str] = {
    "SUCCEEDED": "成功完成过",
    "FAILED": "那一次没做成",
    "CANCELLED": "那一次被取消了",
    "EXPIRED": "那一次放着过期了",
}


def _place(position: Any) -> str:
    source = position if isinstance(position, dict) else None
    if source is None or any(source.get(key) is None for key in ("x", "y", "z")):
        return ""
    x = int(number_or(source.get("x")))
    y = int(number_or(source.get("y")))
    z = int(number_or(source.get("z")))
    return f"({x},{y},{z}) 附近"


class MinecraftMemoryWriter:
    """按写入门槛把观察/结果变成记忆（§三十）。"""

    def __init__(
        self,
        store: MinecraftMemoryStore,
        *,
        clock: Any = time.time,
        character_label: str = "罐头",
        logger: Any = None,
    ) -> None:
        self._store = store
        self._clock = clock
        self._label = str(character_label or "罐头")
        self._log = logger

    # ------------------------------------------------------------ 任务结果（§三十一/§三十二）

    async def task_finished(
        self,
        *,
        server_id: str,
        objective: str,
        outcome: str,
        task_id: str = "",
        plan_version: int = 0,
        initiator: str = "",
        position: Any = None,
        gained: dict[str, Any] | None = None,
        reason: str = "",
        title: str = "",
        extra: dict[str, Any] | None = None,
    ) -> MinecraftMemoryFact | None:
        """任务收尾 → 一条经验（只留语义摘要，绝不留审计细节）。"""
        clean_objective = str(objective or "").strip()[:80]
        if not server_id or not clean_objective:
            return None
        clean_title = str(title or "").strip()[:80]
        state = str(outcome or "").upper()
        verb = TASK_OUTCOME_TEXT.get(state, "做过")
        place = _place(position)
        if state == "SUCCEEDED":
            gains = "、".join(
                f"{name} ×{count}" for name, count in (gained or {}).items() if int(count or 0) > 0
            )
            content = f"{self._label}{verb}“{clean_objective}”这个任务" + (
                f"（{place}）" if place else ""
            )
            if gains:
                content += f"，拿到了 {gains}"
            content += "。"
            fact = MinecraftMemoryFact(
                kind=MinecraftMemoryKind.TASK,
                server_id=str(server_id),
                subject=f"task:{clean_objective}",
                content=content,
                source=FactSource.TASK_RESULT,
                task_id=str(task_id),
                plan_version=int(plan_version or 0),
                initiator=str(initiator or ""),
                outcome=state,
                title=clean_title,
                world_revision=str(self._world_revision()),
                extra=dict(extra or {}),
            )
            return await self._store.remember(fact)
        if state in {"FAILED", "EXPIRED", "CANCELLED"}:
            # §三十二：失败也能记，但**谨慎** —— 只当"当时的情况"，不当"永远如此"
            detail = str(reason or "").strip()[:60]
            content = (
                f"{self._label}{verb}“{clean_objective}”这个任务"
                + (f"（{place}）" if place else "")
                + (f"：{detail}" if detail else "")
                + "（当时的情况，未必一直如此）。"
            )
            fact = MinecraftMemoryFact(
                kind=MinecraftMemoryKind.TASK,
                server_id=str(server_id),
                subject=f"task:{clean_objective}",
                content=content,
                source=FactSource.TASK_RESULT,
                task_id=str(task_id),
                plan_version=int(plan_version or 0),
                initiator=str(initiator or ""),
                outcome=state,
                title=clean_title,
                fresh=Freshness.STALE,
                extra={
                    "temporary": True,
                    "temporal_scope": "short_term",
                    **dict(extra or {}),
                },
            )
            return await self._store.remember(fact)
        return None

    # ------------------------------------------------------------ 玩家（§十二）

    async def player_seen(
        self,
        *,
        server_id: str,
        player_uuid: str,
        username: str,
        position: Any = None,
    ) -> MinecraftMemoryFact | None:
        """在某台服务器上见过这个玩家（uuid 是身份；username 只是显示名）。"""
        if not server_id or not player_uuid:
            return None
        name = str(username or "").strip() or "一个玩家"
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.PLAYER,
                server_id=str(server_id),
                subject=f"player:{player_uuid}",
                content=f"{name} 在这个服务器里活动过。",
                source=FactSource.OBSERVED,
                player_uuid=str(player_uuid),
                username=name,
                position=position if isinstance(position, dict) else None,
            )
        )

    # ------------------------------------------------------------ 地点 / 资源（§十三）

    async def location_seen(
        self,
        *,
        server_id: str,
        kind_label: str,
        position: Any,
        content: str,
        radius: float = 0.0,
        source: FactSource = FactSource.OBSERVED,
        world_revision: str = "",
    ) -> MinecraftMemoryFact | None:
        place = _place(position)
        if not server_id or not place:
            return None
        cell = cluster_position(position)  # 按 16 格聚簇：同格 = 同一件事（§十三 允许误差）
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.LOCATION,
                server_id=str(server_id),
                subject=f"location:{kind_label}@{cell}" if cell else f"location:{kind_label}",
                content=str(content or f"{place} 有{kind_label}"),
                source=source,
                position=dict(position),
                radius=float(radius or 0.0),
                world_revision=str(world_revision or self._world_revision()),
            )
        )

    async def resource_seen(
        self,
        *,
        server_id: str,
        block_name: str,
        position: Any,
        world_revision: str = "",
        source: FactSource = FactSource.OBSERVED,
    ) -> MinecraftMemoryFact | None:
        """某处有一块什么方块（位置按 16 格聚簇，允许误差，§十三）。"""
        place = _place(position)
        name = str(block_name or "").strip()
        if not server_id or not place or not name:
            return None
        cell = cluster_position(position)  # 同格算同一处资源（§十三 允许误差）
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.RESOURCE,
                server_id=str(server_id),
                subject=f"resource:{name}@{cell}" if cell else f"resource:{name}@{place}",
                content=f"{place} 有一块 {name}。",
                source=source,
                position=dict(position),
                world_revision=str(world_revision or self._world_revision()),
            )
        )

    # ------------------------------------------------------------ 关系 / 事件 / 偏好 / 用户陈述

    async def relationship(
        self,
        *,
        server_id: str,
        player_uuid: str,
        username: str,
        label: str = "trusted_companion",
        source: FactSource = FactSource.SYSTEM,
    ) -> MinecraftMemoryFact | None:
        """关系事实（§十六）——**不是权限**：Policy 永远不会读它。"""
        if not server_id or not player_uuid:
            return None
        name = str(username or "").strip() or "一个玩家"
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.RELATIONSHIP,
                server_id=str(server_id),
                subject=f"player:{player_uuid}",
                content=f"{name} 是{self._label}在 Minecraft 里熟悉的人（{label}）。",
                source=source,
                player_uuid=str(player_uuid),
                username=name,
                extra={"relationship": str(label), "grants_permission": False},
            )
        )

    async def event(
        self, *, server_id: str, subject: str, content: str
    ) -> MinecraftMemoryFact | None:
        """有长期价值的事件（§十五）。"""
        if not server_id or not str(content or "").strip():
            return None
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.EVENT,
                server_id=str(server_id),
                subject=str(subject),
                content=str(content).strip()[:160],
                source=FactSource.OBSERVED,
            )
        )

    async def preference(
        self,
        *,
        server_id: str,
        subject: str,
        content: str,
        source: FactSource = FactSource.USER_STATED,
    ) -> MinecraftMemoryFact | None:
        """Minecraft 相关偏好（§十七）：必须有来源（用户说过 / 重复行为 / 任务结果）。"""
        if not server_id or not str(content or "").strip():
            return None
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.PREFERENCE,
                server_id=str(server_id),
                subject=str(subject),
                content=str(content).strip()[:160],
                source=source,
            )
        )

    async def user_stated(
        self, *, server_id: str, content: str, subject: str = ""
    ) -> MinecraftMemoryFact | None:
        """用户明确说过的事实（§二十三）。

        试图改规则的语句（"以后不要确认就直接挖"）**照记**，但只是"用户说过这样一句话"：
        provenance 会被标成 `untrusted_directive`，importance 压到最低，
        而且它**永远不可能**影响 Policy（§四十一/§四十二）。
        """
        text = str(content or "").strip()
        if not server_id or not text:
            return None
        # §四十一/§四十二：试图改规则的语句**照样记**，但置信度压到 0.40、并标成不可信语境 ——
        # 它永远不可能变成「给我的指令」，更不可能改 Policy（Policy 从不读记忆）。
        directive = looks_like_directive(text)
        return await self._store.remember(
            MinecraftMemoryFact(
                kind=MinecraftMemoryKind.PREFERENCE,
                server_id=str(server_id),
                subject=str(subject or f"user:{text[:24]}"),
                content=text[:160],
                source=FactSource.USER_STATED,
                # 0 = 用来源默认（USER_STATED 0.95）
                confidence=DIRECTIVE_CONFIDENCE if directive else 0.0,
                extra={"untrusted_directive": True} if directive else {},
            )
        )

    # ------------------------------------------------------------ 内部

    def _world_revision(self) -> str:
        """世界版本由感知层给（这里只是占位；调用方应显式传入得到的 revision）。"""
        return ""
