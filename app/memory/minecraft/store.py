"""Phase 5C：Minecraft 记忆域在**现有记忆引擎**上的适配（不建第二套存储，§四十七）。

* 复用同一张 ``memories`` 表、同一套 dedupe/conflict/embedding/quota 逻辑；
* 只用**一个专用 scope** ``character:<角色>:minecraft`` 把 MC 记忆与普通聊天记忆隔开
  （§十/§四十八：普通人格检索不会顺手捞到 Minecraft 世界细节）；
* 每条记忆的 ``source="minecraft"``、``provenance`` 里带 ``domain=minecraft`` 与
  ``server_id``，检索端据此做**服务器隔离**（§三十六）；
* 任何一次读写失败都只**降级 Minecraft 记忆**（``degraded_reason``），
  绝不影响聊天、任务执行（§三十九/§四十）。
"""

from __future__ import annotations

import time
from collections.abc import Sequence
from typing import Any

from app.memory.minecraft.model import (
    DIRECTIVE_CONFIDENCE,
    DOMAIN,
    ENGINE_STATUS_BY_FRESHNESS,
    FRESHNESS_BY_ENGINE_STATUS,
    FactSource,
    Freshness,
    MinecraftMemoryFact,
    MinecraftMemoryKind,
)

#: 这些 kind 的同一 subject 出现不同内容时要**保留冲突**而不是覆盖（§三十四）
CONFLICT_KINDS = frozenset(
    {
        MinecraftMemoryKind.LOCATION,
        MinecraftMemoryKind.PREFERENCE,
        MinecraftMemoryKind.RELATIONSHIP,
    }
)


#: 角色名拿不到时的兜底 key —— **故意不叫 "default"**：`character:default:minecraft`
#: 是 Phase 5C 装配点缺陷期间写下的历史 scope（见 LEGACY_SCOPE_KEYS），
#: 兜底值绝不能落进那个审计抽屉里。
FALLBACK_CHARACTER_KEY = "unscoped"

#: **历史（legacy）scope：只作审计，不删、不迁移、默认排除**（2026-10-08 决定）。
#:
#: 成因：Phase 5C 第一版把记忆桥装配在 `__init__`（角色名那时还没从库里读出来），
#: 于是 scope 落成了 `character:default:minecraft`。修好装配点之后，新数据走
#: `character:<角色名>:minecraft`；旧数据**原样保留**在这两个 scope 里当证据，
#: 默认读路径（`facts` / 检索 / 对账 / 状态计数）一律不含它们 ——
#: 只有显式调用 :meth:`MinecraftMemoryStore.legacy_facts` 才读得到，且每条都带
#: ``extra={"legacy_scope": True}`` 标记，绝不混进正常检索。
LEGACY_SCOPE_KEYS: tuple[str, ...] = ("character:default:minecraft",)


def is_legacy_scope(scope_key: str) -> bool:
    """这个 scope 是不是"缺陷期间留下的审计抽屉"。"""
    return str(scope_key or "") in LEGACY_SCOPE_KEYS


#: ``provenance`` 里"结构化字段"的名字（其余键都属于域内附加标记 ``extra``）
_PROVENANCE_RESERVED = frozenset(
    {
        "domain",
        "kind",
        "server_id",
        "subject",
        "fact_source",
        "freshness",
        "observed_at",
        "last_verified_at",
        "observation_count",
        "world_revision",
        "player_uuid",
        "username",
        "position",
        "radius",
        "cell",
        "task_id",
        "plan_version",
        "initiator",
        "outcome",
    }
)


def minecraft_scope_key(character_key: str) -> str:
    """Minecraft 记忆的专用 scope（与普通聊天记忆彻底分开）。"""
    key = str(character_key or FALLBACK_CHARACTER_KEY).strip() or FALLBACK_CHARACTER_KEY
    return f"character:{key}:minecraft"


class MinecraftMemoryStore:
    """按域写入/读取 Minecraft 记忆（薄适配层；引擎负责 dedupe/embedding/quota）。"""

    def __init__(
        self,
        manager: Any,
        *,
        character_key: str = "default",
        clock: Any = time.time,
        logger: Any = None,
    ) -> None:
        self._manager = manager
        self._clock = clock
        self._log = logger
        self.character_key = str(character_key or "default")
        self.scope_key = minecraft_scope_key(self.character_key)
        #: 最近一次失败原因（空 = 健康）。UI/日志据此如实显示"记忆降级"。
        self.degraded_reason = ""

    # ------------------------------------------------------------ 写

    async def remember(self, fact: MinecraftMemoryFact) -> MinecraftMemoryFact | None:
        """写入一条事实：同一 dedupe_key 就**强化**，同一 subject 的矛盾内容就**留冲突**。"""
        if not fact.server_id or not fact.content.strip() or not fact.subject:
            return None
        now = float(self._clock())
        if not fact.observed_at:
            fact.observed_at = now
        if not fact.last_verified_at:
            fact.last_verified_at = now
        try:
            existing = await self._find_by_dedupe(fact.dedupe_key)
            if existing is not None:
                fact.observation_count = int(existing.provenance.get("observation_count") or 1) + 1
                if fact.directive:
                    # §四十一：试图改规则的语句说十遍也不长信心（它永远只是"他说过一句话"）
                    fact.confidence = min(
                        DIRECTIVE_CONFIDENCE,
                        float(existing.confidence),
                        float(fact.confidence),
                    )
                else:
                    fact.confidence = min(
                        0.99, max(float(existing.confidence), float(fact.confidence)) + 0.05
                    )
                fact.observed_at = float(existing.provenance.get("observed_at") or now)
                fact.last_verified_at = now
                updated = existing.model_copy(
                    update={
                        "content": fact.content,
                        "category": fact.category,
                        "summary": fact.content[:120],
                        "source": "minecraft",
                        "layer": fact.layer,
                        "confidence": round(fact.confidence, 3),
                        "status": fact.engine_status,
                        "provenance": fact.provenance(),
                        "character_id": self.character_key,
                        "dedupe_key": fact.dedupe_key,
                    }
                )
                await self._manager.repository.update(updated)
                self._invalidate_cache()
                return fact

            stored = await self._manager.remember(
                "character",
                f"{self.character_key}:minecraft",
                fact.content,
                category=fact.category,
                importance=self._importance(fact),
                confidence=fact.confidence,
                layer=fact.layer,
                summary=fact.content[:120],
                source="minecraft",
                event_at=int(fact.observed_at) if fact.layer == "episodic" else None,
                character_id=self.character_key,
                provenance=fact.provenance(),
                dedupe_key=fact.dedupe_key,
            )
            if stored is None:
                # 引擎按规则拒了（太短/敏感）→ 这是决定，不是故障
                return None
            if stored.status != fact.engine_status or stored.dedupe_key != fact.dedupe_key:
                await self._manager.repository.update(
                    stored.model_copy(
                        update={
                            "status": fact.engine_status,
                            "dedupe_key": fact.dedupe_key,
                            "provenance": fact.provenance(),
                        }
                    )
                )
            await self._flag_conflicts(fact, stored.id)
            self.degraded_reason = ""
            return fact
        except Exception as exc:  # noqa: BLE001 - 记忆写失败只降级（§三十九/§四十）
            self.degraded_reason = f"{type(exc).__name__}"
            self._warn("remember failed (degraded)", exc)
            return None

    async def touch(self, fact: MinecraftMemoryFact, *, now: float | None = None) -> bool:
        """复核一条仍成立的事实（§二十一：``last_verified_at`` 前进、置信度微增）。"""
        moment = float(now if now is not None else self._clock())
        existing = await self._find_by_dedupe(fact.dedupe_key)
        if existing is None:
            return False
        fact.observation_count = int(existing.provenance.get("observation_count") or 1) + 1
        fact.last_verified_at = moment
        fact.observed_at = float(existing.provenance.get("observed_at") or moment)
        fact.fresh = Freshness.ACTIVE
        fact.confidence = min(0.99, float(existing.confidence) + 0.05)
        try:
            await self._manager.repository.update(
                existing.model_copy(
                    update={
                        "status": ENGINE_STATUS_BY_FRESHNESS[Freshness.ACTIVE],
                        "confidence": round(fact.confidence, 3),
                        "provenance": fact.provenance(),
                    }
                )
            )
            self._invalidate_cache()
            return True
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._warn("touch failed (degraded)", exc)
            return False

    async def set_freshness(self, fact: MinecraftMemoryFact, fresh: Freshness) -> bool:
        """改新鲜度（§二十一：只改状态，**绝不删除**历史）。"""
        existing = await self._find_by_dedupe(fact.dedupe_key)
        if existing is None:
            return False
        fact.fresh = fresh
        try:
            await self._manager.repository.update(
                existing.model_copy(
                    update={
                        "status": ENGINE_STATUS_BY_FRESHNESS[fresh],
                        "provenance": fact.provenance(),
                    }
                )
            )
            self._invalidate_cache()
            return True
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            self._warn("set_freshness failed (degraded)", exc)
            return False

    # ------------------------------------------------------------ 读

    async def facts(
        self,
        *,
        server_id: str = "",
        kinds: Sequence[MinecraftMemoryKind] | None = None,
        player_uuid: str = "",
        include_inactive: bool = False,
        limit: int = 50,
    ) -> list[MinecraftMemoryFact]:
        """读本域事实（**按 server_id 隔离**；默认只看还成立的那些，§三十六）。"""
        try:
            rows = await self._manager.repository.search(
                scope_key=self.scope_key, source="minecraft", limit=max(1, int(limit)) * 4
            )
        except Exception as exc:  # noqa: BLE001 - 读失败 = 明确降级，绝不假装检索成功（§四十）
            self.degraded_reason = f"{type(exc).__name__}"
            self._warn("read failed (degraded)", exc)
            return []
        wanted_kinds = {kind.value for kind in kinds} if kinds else None
        out: list[MinecraftMemoryFact] = []
        for memory in rows:
            fact = self.to_fact(memory)
            if fact is None:
                continue  # 坏行/跨域行 → 跳过（绝不猜）
            if server_id and fact.server_id != str(server_id):
                continue  # §三十六：绝不跨服务器污染
            if player_uuid and fact.player_uuid != str(player_uuid):
                continue
            if wanted_kinds is not None and fact.kind.value not in wanted_kinds:
                continue
            if not include_inactive and fact.fresh is Freshness.INVALIDATED:
                continue
            out.append(fact)
            if len(out) >= int(limit):
                break
        self.degraded_reason = ""
        return out

    async def all_facts(
        self, *, server_id: str = "", limit: int = 200
    ) -> list[MinecraftMemoryFact]:
        return await self.facts(server_id=server_id, include_inactive=True, limit=limit)

    async def legacy_facts(self, *, limit: int = 200) -> list[MinecraftMemoryFact]:
        """**只读**盘点历史（缺限期）scope 里的事实：审计用，**绝不参与检索/对账**。

        2026-10-08 的决定：那批数据保留证据、不删除、不盲迁；这里给它们一个明确的
        读出口，并且每条都标 ``legacy_scope=True``（调用方据此展示"这是审计遗留"）。
        默认路径（`facts` / `retriever` / `reconciler` / 状态计数）看不到这些行。
        """
        out: list[MinecraftMemoryFact] = []
        for scope_key in LEGACY_SCOPE_KEYS:
            if scope_key == self.scope_key:
                continue  # 兜底 key 万一撞上也不自读（FALLBACK 已避免这种情况）
            try:
                rows = await self._manager.repository.search(
                    scope_key=scope_key, source="minecraft", limit=max(1, int(limit))
                )
            except Exception as exc:  # noqa: BLE001 - 审计读失败也只降级
                self.degraded_reason = f"{type(exc).__name__}"
                self._warn("legacy read failed (degraded)", exc)
                continue
            for memory in rows:
                fact = self.to_fact(memory)
                if fact is None:
                    continue
                fact.extra["legacy_scope"] = True
                out.append(fact)
        return out[: max(1, int(limit))]

    def to_fact(self, memory: Any) -> MinecraftMemoryFact | None:
        """把一行记忆还原成域事实（非本域/坏数据 → None，绝不猜）。"""
        provenance = dict(getattr(memory, "provenance", None) or {})
        if provenance.get("domain") != DOMAIN:
            return None
        try:
            fact = MinecraftMemoryFact(
                kind=MinecraftMemoryKind(str(provenance.get("kind"))),
                server_id=str(provenance.get("server_id") or ""),
                subject=str(provenance.get("subject") or ""),
                content=str(getattr(memory, "content", "") or ""),
                source=FactSource(str(provenance.get("fact_source") or "OBSERVED")),
                confidence=float(getattr(memory, "confidence", 0.0) or 0.0),
                observed_at=float(provenance.get("observed_at") or 0.0),
                last_verified_at=float(provenance.get("last_verified_at") or 0.0),
                fresh=FRESHNESS_BY_ENGINE_STATUS.get(
                    str(getattr(memory, "status", "active")), Freshness.ACTIVE
                ),
                world_revision=str(provenance.get("world_revision") or ""),
                player_uuid=str(provenance.get("player_uuid") or ""),
                username=str(provenance.get("username") or ""),
                position=dict(provenance.get("position") or {}) or None,
                radius=float(provenance.get("radius") or 0.0),
                task_id=str(provenance.get("task_id") or ""),
                plan_version=int(provenance.get("plan_version") or 0),
                initiator=str(provenance.get("initiator") or ""),
                outcome=str(provenance.get("outcome") or ""),
                observation_count=int(provenance.get("observation_count") or 1),
                # 域内附加标记（untrusted_directive / grants_permission / temporary …）
                # 是**语义的一部分**，重新读回来时必须一起还原（否则降级/不可信标记会丢）
                extra={
                    key: value
                    for key, value in provenance.items()
                    if key not in _PROVENANCE_RESERVED
                },
            )
        except (TypeError, ValueError):
            return None
        return fact

    # ------------------------------------------------------------ 内部

    def _importance(self, fact: MinecraftMemoryFact) -> float:
        table = {
            MinecraftMemoryKind.RELATIONSHIP: 0.75,
            MinecraftMemoryKind.PLAYER: 0.7,
            MinecraftMemoryKind.LOCATION: 0.65,
            MinecraftMemoryKind.TASK: 0.6,
            MinecraftMemoryKind.EVENT: 0.6,
            MinecraftMemoryKind.PREFERENCE: 0.6,
            MinecraftMemoryKind.RESOURCE: 0.5,
        }
        base = table.get(fact.kind, 0.5)
        if fact.directive:
            # §四十一：试图改规则的语句重要性压到最低（它只是"用户说过这样一句话"）
            base = min(base, 0.3)
        return base

    async def _find_by_dedupe(self, dedupe_key: str) -> Any:
        if not dedupe_key:
            return None
        rows = await self._manager.repository.search(
            scope_key=self.scope_key, source="minecraft", limit=200
        )
        for memory in rows:
            if str(getattr(memory, "dedupe_key", "") or "") == dedupe_key:
                return memory
        return None

    async def _flag_conflicts(self, fact: MinecraftMemoryFact, new_id: int) -> None:
        """同一 subject、不同内容的矛盾事实：**保留冲突**，不覆盖（§三十四）。"""
        if fact.kind not in CONFLICT_KINDS or not new_id:
            return
        rows = await self._manager.repository.search(
            scope_key=self.scope_key, source="minecraft", limit=200
        )
        for memory in rows:
            provenance = dict(getattr(memory, "provenance", None) or {})
            if provenance.get("domain") != DOMAIN:
                continue
            other = self.to_fact(memory)
            if other is None or other.kind is not fact.kind:
                continue
            # 同一个「语义组」（例如都叫 BASE 的地点）但内容不同 → 冲突：两条都留（§三十四）
            if other.conflict_group != fact.conflict_group:
                continue
            if int(getattr(memory, "id", 0) or 0) == int(new_id):
                continue
            if str(getattr(memory, "content", "")).strip() == fact.content.strip():
                continue
            await self._manager.repository.mark_unanswered_conflicts(new_id, int(memory.id))
            return

    def _invalidate_cache(self) -> None:
        retriever = getattr(self._manager, "retriever", None)
        if retriever is not None and hasattr(retriever, "invalidate_cache"):
            retriever.invalidate_cache()

    def _warn(self, message: str, exc: Exception) -> None:
        if self._log is not None:
            self._log.warning("[Minecraft.Memory] %s: %s", message, exc)
