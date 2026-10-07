"""Phase 5C：世界变化 → 记忆对账（§二十-§二十二）。

方向**只能是**：

```
WorldPerception  →  Memory Reconciliation  →  Memory(freshness/status)
```

绝不允许反向：记忆说那里有棵树、`find_blocks` 没找到 → **不许**去改世界或改感知
（唯一真实来源永远是运行时/世界感知，§二十三）。

* 位置类事实（RESOURCE / LOCATION 带坐标）用**只读**复核：世界说"不在了" → `INVALIDATED`；
  说"还在" → 刷新 `last_verified_at`；说"读不到" → 不动，交给时间。
* 其它事实（PLAYER / TASK / RELATIONSHIP / PREFERENCE）只按时间变 `STALE`。
* **永远不删除**（§二十一）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from app.memory.minecraft.model import Freshness, MinecraftMemoryFact, MinecraftMemoryKind
from app.memory.minecraft.store import MinecraftMemoryStore

#: 复核结果：还在 / 不在了 / 读不到（读不到 ≠ 不在了）
PRESENT = "present"
ABSENT = "absent"
UNKNOWN = "unknown"

#: 带坐标、可以被世界事实推翻的 kind
VERIFIABLE_KINDS = frozenset({MinecraftMemoryKind.RESOURCE, MinecraftMemoryKind.LOCATION})

#: 默认"多久没复核就算过时"
DEFAULT_STALE_AFTER_SECONDS = 24 * 3600.0

#: 复核函数（生产上由任务/工具层用 SAFE 只读实现；记忆域自己不认识 Minecraft）
Verifier = Callable[[MinecraftMemoryFact], Awaitable[tuple[str, dict[str, Any]]]]


@dataclass
class ReconcileReport:
    """一次对账的结果（审计/测试用）。"""

    checked: int = 0
    confirmed: int = 0
    invalidated: int = 0
    staled: int = 0
    skipped: int = 0
    degraded: bool = False
    details: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "checked": self.checked,
            "confirmed": self.confirmed,
            "invalidated": self.invalidated,
            "staled": self.staled,
            "skipped": self.skipped,
            "degraded": self.degraded,
        }


class MinecraftMemoryReconciler:
    """把记忆和当前世界对一遍（只读、可降级）。"""

    def __init__(
        self,
        store: MinecraftMemoryStore,
        *,
        verify: Verifier | None = None,
        clock: Any = time.time,
        stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS,
        logger: Any = None,
    ) -> None:
        self._store = store
        self._verify = verify
        self._clock = clock
        self._stale_after = float(stale_after_seconds)
        self._log = logger

    async def reconcile(
        self, *, server_id: str, facts: list[MinecraftMemoryFact] | None = None, limit: int = 50
    ) -> ReconcileReport:
        report = ReconcileReport()
        if not server_id:
            return report
        if facts is None:
            facts = await self._store.facts(server_id=server_id, limit=limit)
            if self._store.degraded_reason:
                # §四十：读不到就如实说"记忆降级"，绝不假装已经对过账
                report.degraded = True
                return report
        now = float(self._clock())
        for fact in facts:
            report.checked += 1
            if fact.fresh is Freshness.INVALIDATED:
                report.skipped += 1
                continue
            if self._verify is not None and fact.kind in VERIFIABLE_KINDS and fact.position:
                outcome, detail = await self._safe_verify(fact)
                if outcome == ABSENT:
                    fact.fresh = Freshness.INVALIDATED
                    await self._store.set_freshness(fact, Freshness.INVALIDATED)
                    report.invalidated += 1
                    report.details.append(
                        {"subject": fact.subject, "outcome": ABSENT, "detail": detail}
                    )
                    continue
                if outcome == PRESENT:
                    await self._store.touch(fact, now=now)
                    report.confirmed += 1
                    report.details.append({"subject": fact.subject, "outcome": PRESENT})
                    continue
                # UNKNOWN：读不到世界 ≠ 世界变了，什么都不做（绝不猜）
                report.skipped += 1
                continue
            if self._age(fact, now) > self._stale_after and fact.fresh is Freshness.ACTIVE:
                fact.fresh = Freshness.STALE
                await self._store.set_freshness(fact, Freshness.STALE)
                report.staled += 1
                report.details.append({"subject": fact.subject, "outcome": "stale_by_age"})
            else:
                report.skipped += 1
        if self._store.degraded_reason:
            report.degraded = True
        return report

    # ------------------------------------------------------------ 内部

    async def _safe_verify(self, fact: MinecraftMemoryFact) -> tuple[str, dict[str, Any]]:
        try:
            return await self._verify(fact)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001 - 复核失败 = 读不到，不是"世界变了"
            if self._log is not None:
                self._log.warning("[Minecraft.Memory] verify failed (unknown): %s", exc)
            return UNKNOWN, {"error": str(exc)[:80]}

    def _age(self, fact: MinecraftMemoryFact, now: float) -> float:
        anchor = float(fact.last_verified_at or fact.observed_at or 0.0)
        return (now - anchor) if anchor else 0.0
