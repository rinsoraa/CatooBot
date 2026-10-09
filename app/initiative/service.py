"""Phase 7A §一/§三十一/§四十五/§四十六/§四十九/§五十：LifeIntent 的编排层。

```text
Context(read-only) → Candidate Intents → InitiativeGate → SUPPRESS | PROPOSE → LifeIntent
```

这一层是 7A 唯一的"有副作用"的地方，而它的副作用只有三种：

1. 写 ``life_intents`` / ``behavior_events``（状态与审计）；
2. 发 ``initiative.*`` 事件（状态更新，绝不允许因此调模型或发消息，§四十八）；
3. 提供**只读**视图（WebUI §四十九）与**只读**对话上下文块（§五十）。

**没有执行入口**：这里既没有 TaskRuntime / ActionRuntime / MinecraftService / Policy /
ConfirmationStore，也没有 QQ 发送器 —— 执行层恒为 ``NONE``（§四十一/§六十八）。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from typing import Any

from app.initiative.candidates import InitiativeContext, propose_intents
from app.initiative.events import (
    INITIATIVE_CANCELLED,
    INITIATIVE_CREATED,
    INITIATIVE_EXPIRED,
    INITIATIVE_RESOLVED,
    INITIATIVE_SUPPRESSED,
    InitiativeEventPublisher,
)
from app.initiative.gate import (
    GateDecision,
    InitiativeGate,
    SuppressionReason,
)
from app.initiative.model import LifeIntent, LifeIntentStatus

DUPLICATE_INTENT = SuppressionReason.DUPLICATE_INTENT.value

#: 每次 check 读回多少条历史意图（bounded，§六十三）
RECENT_INTENT_LIMIT = 40
#: 给 Planner 的建议条数上限（bounded，§三.3；与 activity 侧的 MAX_HINTS 同宽）
#: ★真机发现：上限 3 会把同时挂着的 4 条念头里最弱的那条整条挤出去 → 放宽到 5（仍有界）
HINT_LIMIT = 5

#: §九：**"她此刻不方便"**的守卫 —— 命中这些时连建议都不给 Planner（旧想法也不算数）。
#: 刻意**不含** MINECRAFT_OFFLINE（§三十七：离线时虚拟兴趣照样有效）、
#: 也不含 RECENT_USER_INTERACTION / RECENT_INITIATIVE / DUPLICATE_INTENT（那些只是"现在别再提"，
#: 不是"她不想做"）。
HINT_BLOCKING_REASONS: frozenset[str] = frozenset(
    {
        "CHARACTER_RECOVERY",
        "SYSTEM_DEGRADED",
        "ACTIVE_USER_TASK",
        "PENDING_CONFIRMATION",
        "SLEEPING",
        "QUIET_HOURS",
        "HIGH_SOCIAL_FATIGUE",
    }
)
#: 展示用（WebUI / 对话上下文）
VIEW_RECENT_LIMIT = 10
CONTEXT_RECENT_LIMIT = 3


class LifeIntentService:
    """意图编排器（确定性；不 import 任何执行层，见 ``tests/test_initiative_security.py``）。"""

    def __init__(
        self,
        *,
        store: Any,
        config: Any,
        context_provider: Callable[[float], Awaitable[InitiativeContext | None]] | None = None,
        character_id: str = "",
        gate: Any = None,
        publisher: Any = None,
        clock: Callable[[], float] = time.time,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.config = config
        self.character_id = str(character_id or "")
        self.context_provider = context_provider
        self.gate = gate or InitiativeGate()
        self.publisher = publisher or InitiativeEventPublisher(logger=logger)
        self._clock = clock
        self._log = logger
        #: 启动时刻：§四十五 的重启静默期（恢复后不得出现 initiative burst）
        self.started_at = float(self._clock())
        self.recovered_at = self.started_at
        self.last_decision: GateDecision | None = None
        #: Phase 7B §三：给 ActivityPlanner 的**只读建议**（bounded；只在 check/recover 刷新，
        #: 于是"每 30 秒重算整个 horizon"这件事**不会**发生，§十一）
        self._open_hints: tuple[dict[str, Any], ...] = ()
        self.last_check_at = 0.0
        self.checks = 0
        self.degraded_reason = ""

    # ------------------------------------------------------------ 只读

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.config, "enabled", True))

    def _now(self, now: float | None = None) -> float:
        return float(now if now is not None else self._clock())

    async def current(self) -> LifeIntent | None:
        """最近一条**还没被抑制**的意图（"她最近想干嘛"）。"""
        for intent in await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT):
            if intent.status is LifeIntentStatus.PROPOSED:
                return intent
        return None

    async def recent(self, limit: int = CONTEXT_RECENT_LIMIT) -> list[LifeIntent]:
        return await self.store.recent(self.character_id, limit=max(1, int(limit)))

    async def suppressed(self, limit: int = VIEW_RECENT_LIMIT) -> list[LifeIntent]:
        rows = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        return [item for item in rows if item.status is LifeIntentStatus.SUPPRESSED][
            : max(1, int(limit))
        ]

    async def cooldown_view(self, now: float | None = None) -> dict[str, Any]:
        moment = self._now(now)
        minutes = float(getattr(self.config, "cooldown_minutes", 20.0) or 0.0)
        window = minutes * 60.0
        rows = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        proposed = [item for item in rows if item.status is LifeIntentStatus.PROPOSED]
        remaining = 0.0
        if proposed and window > 0:
            newest = max(proposed, key=lambda item: float(item.created_at))
            remaining = round(max(0.0, window - (moment - float(newest.created_at))), 1)
        return {
            "minutes": minutes,
            "seconds_remaining": remaining,
            "max_proposals_per_hour": int(getattr(self.config, "max_proposals_per_hour", 3) or 0),
            "proposals_last_hour": sum(
                1 for item in proposed if 0 <= moment - float(item.created_at) < 3600.0
            ),
        }

    async def view(self, now: float | None = None) -> dict[str, Any]:
        """§四十九：只读快照（**没有** Execute / Send / Confirm / Run / Force）。"""
        moment = self._now(now)
        rows = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        decision = self.last_decision
        current = await self.current()
        history = (
            await self.store.recent_events(self.character_id, limit=VIEW_RECENT_LIMIT)
            if hasattr(self.store, "recent_events")
            else []
        )
        return {
            "enabled": bool(self.enabled),
            "character_id": self.character_id,
            "execution_layer": "NONE",  # §四十一：7A 的执行层
            "degraded": self.degraded_reason,
            "last_check_at": float(self.last_check_at),
            "checks": int(self.checks),
            "current": current.to_payload() if current is not None else None,
            "candidates": [item.to_payload() for item in (decision.verdicts if decision else ())],
            "recent": [item.to_payload() for item in rows[:VIEW_RECENT_LIMIT]],
            "suppressed": [
                item.to_payload() for item in rows if item.status is LifeIntentStatus.SUPPRESSED
            ][:VIEW_RECENT_LIMIT],
            "cooldown": await self.cooldown_view(moment),
            "guards": dict(decision.guards) if decision else {},
            "history": [dict(item) for item in history],
        }

    async def context_block(self, now: float | None = None) -> str:
        """§五十：给对话的 **bounded** 上下文块（当前 1 条 + 最近 ≤3 条）。

        措辞上明确"只是想法"：它既不是现状（§七），也不是计划（6C），
        更不是"已经被允许做什么"（§五十五）。
        """
        rows = await self.store.recent(self.character_id, limit=CONTEXT_RECENT_LIMIT + 4)
        if not rows:
            return ""
        current = next((item for item in rows if item.status is LifeIntentStatus.PROPOSED), None)
        lines = [
            "她最近冒出来的念头（**只是念头**：既不是在做的事，也不是已经答应谁的计划；"
            "别人问起可以如实说，但绝不能说成「正在做」或「已经确认要做」）："
        ]
        if current is not None:
            lines.append(f"- 现在挂着：{current.title}（{current.intent_type.value}）")
        others = [item for item in rows if item is not current][:CONTEXT_RECENT_LIMIT]
        for item in others:
            mark = item.status.value
            line = f"- 之前：{item.title}（{mark}"
            if item.suppression_reason:
                line += f"，{item.suppression_reason}"
            lines.append(line + "）")
        return "\n".join(lines)

    #: 建议的加成公式（确定性的；§三：只交数据，Planner 自己乘权重）
    @staticmethod
    def _hint_bonus(priority: float) -> float:
        return round(0.6 + 0.4 * max(0.0, min(1.0, float(priority))), 4)

    def hints(self, *, limit: int = 3, now: float | None = None) -> tuple[dict[str, Any], ...]:
        """Phase 7B §三：给 ActivityPlanner 的**只读结构化建议**（纯数据）。

        * 只有 **PROPOSED 且未过期**的意图参与（§三.1/§三.2）——被抑制/取消/解决/过期的一律不交；
        * bounded（默认 ≤3 条，§三.3）、确定性（同样的输入同样顺序）、**不写任何东西**（§三.5）；
        * 每条意图只会出现一次（§三.4：重复 check 不会重复加分）；
        * 名字到活动的翻译**不在这里做**（那是 activity 侧的 Registry 知识，§六 分层）。

        拿不到（没装配 / 没跑过 check）就是空表 —— Planner 逐字退回原有行为（§五）。
        """
        moment = self._now(now)
        bound = max(0, int(limit))
        out: list[dict[str, Any]] = []
        for item in self._open_hints:
            if len(out) >= bound:
                break
            expires = float(item.get("expires_at") or 0.0)
            if expires and moment >= expires:
                continue  # §二十二：过期就是过期，绝不当成"还想做"
            out.append(dict(item))
        return tuple(out)

    def _refresh_hints(self, *, extra: Sequence[Any] = ()) -> None:
        """刷新建议缓存（§三.1：只留 PROPOSED 且未过期的；bounded）。"""
        rows = [
            item
            for item in extra
            if getattr(item, "status", LifeIntentStatus.PROPOSED) is LifeIntentStatus.PROPOSED
        ]
        hints: list[dict[str, Any]] = []
        seen: set[str] = set()
        for item in rows:
            intent_id = str(getattr(item, "intent_id", "") or "")
            if not intent_id or intent_id in seen:
                continue
            seen.add(intent_id)
            hints.append(
                {
                    "intent_id": intent_id,
                    "intent_type": str(getattr(item, "intent_type", "") and item.intent_type.value),
                    "source": str(getattr(item, "source", "") and item.source.value),
                    "related_activity": str(getattr(item, "related_activity", "") or ""),
                    "score_bonus": self._hint_bonus(float(getattr(item, "priority", 0.5) or 0.5)),
                    "expires_at": float(getattr(item, "expires_at", 0.0) or 0.0),
                }
            )
            if len(hints) >= HINT_LIMIT:
                break
        hints.sort(key=lambda row: (-float(row["score_bonus"]), str(row["intent_id"])))
        self._open_hints = tuple(hints)

    def suggested_activities(self) -> dict[str, float]:
        """§三十五/§五四：**只读**建议给 ActivityPlanner 的候选加权（没有就空表）。

        这里只交出"活动名 → 加成"的字典；要不要采纳、怎么算分、会不会换活动，
        全在 6B/6C —— Initiative **永远**没有决定权（§五十五）。
        """
        # 这里只报"意图自己带活动名"的那些（例如长闲置建议的 reading）；
        # 类型 → 已注册活动的翻译在 activity 侧（§六 分层），所以 MINECRAFT_INTEREST /
        # REST / SOCIAL 这类意向在这里是空表 —— Planner 走 hints()。
        suggestions: dict[str, float] = {}
        for item in self.hints():
            activity = str(item.get("related_activity") or "")
            if not activity:
                continue
            suggestions[activity] = float(item.get("score_bonus") or 0.0)
        return suggestions

    # ------------------------------------------------------------ 主流程

    async def check(
        self, *, now: float | None = None, trigger: str = "scheduled"
    ) -> dict[str, Any]:
        """跑一次 initiative check（§三十一：只在合理 trigger 上，不做"每分钟评分"）。"""
        moment = self._now(now)
        self.last_check_at = moment
        self.checks += 1
        if not self.enabled:
            return {"action": "disabled", "now": moment, "trigger": str(trigger)}
        if self.context_provider is None:
            self.degraded_reason = "no_context_provider"
            return {"action": "no_context", "now": moment, "trigger": str(trigger)}

        await self.expire_due(now=moment)
        try:
            context = await self.context_provider(moment)
        except Exception:  # noqa: BLE001 - 读不到就什么都不产生（绝不猜）
            self.degraded_reason = "context_error"
            if self._log is not None:
                self._log.debug("[Initiative] context 读取失败（忽略）", exc_info=True)
            return {
                "action": "no_context",
                "degraded": self.degraded_reason,
                "now": moment,
                "trigger": str(trigger),
            }
        if context is None:
            self.degraded_reason = "no_context"
            return {
                "action": "no_context",
                "degraded": self.degraded_reason,
                "now": moment,
                "trigger": str(trigger),
            }
        if not context.recovered_at:
            context = _replace_recovered(context, self.recovered_at)

        recent = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        self._refresh_hints(
            extra=tuple(item for item in recent if item.status is LifeIntentStatus.PROPOSED)
        )
        candidates = propose_intents(context)
        decision = self.gate.evaluate(
            candidates, context=context, config=self.config, recent_intents=recent
        )
        self.last_decision = decision

        created: list[str] = []
        created_intents: list[LifeIntent] = []
        suppressed: list[str] = []
        ignored: list[str] = []
        verdict_by_fp = {item.fingerprint: item for item in decision.verdicts}
        for candidate in candidates:
            verdict = verdict_by_fp.get(candidate.fingerprint)
            if verdict is None:
                continue
            if not verdict.allowed and verdict.reason == DUPLICATE_INTENT:
                # §二十：相同 Intent 被"合并 / 忽略" —— 已经在案了，**不建新行**
                ignored.append(candidate.fingerprint)
                continue
            if verdict.allowed:
                payload = candidate
            else:
                payload = candidate.with_status(
                    LifeIntentStatus.SUPPRESSED, suppression_reason=verdict.reason
                )
            stored, fresh = await self.store.create(payload)
            if not fresh:
                continue  # §四十六：同一个指纹只写一次
            if verdict.allowed:
                created.append(stored.intent_id)
                created_intents.append(stored)
                await self.publisher.publish(
                    INITIATIVE_CREATED,
                    stored,
                    store=self.store,
                    timestamp=moment,
                    reason=f"trigger={trigger}",
                )
            else:
                suppressed.append(stored.intent_id)
                await self.publisher.publish(
                    INITIATIVE_SUPPRESSED,
                    stored,
                    store=self.store,
                    timestamp=moment,
                    reason=verdict.reason,
                )
        self.degraded_reason = ""
        # §九：**硬 guard 命中时连建议都不给**（任务占位 / 待确认 / 睡着 / 刚重启 …）——
        # 已经提过的旧想法也不能在这个时刻继续给 Planner 加权（那不是"自由时的想法"了）。
        if str(decision.reason or "") in HINT_BLOCKING_REASONS or (
            not decision.allowed and str(decision.reason or "") in HINT_BLOCKING_REASONS
        ):
            self._open_hints = ()
        else:
            self._refresh_hints(
                extra=tuple(
                    item
                    for item in (*created_intents, *recent)
                    if item.status is LifeIntentStatus.PROPOSED
                )
            )
        action = "proposed" if created else ("suppressed" if suppressed else "idle")
        if not created and not suppressed and ignored:
            action = "ignored"
        return {
            "action": action,
            "now": moment,
            "trigger": str(trigger),
            "created": created,
            "suppressed": suppressed,
            "ignored": ignored,
            "reason": decision.reason,
            "candidate_count": len(candidates),
            "decision": decision.to_payload(),
        }

    async def expire_due(self, *, now: float | None = None) -> int:
        """§二十二：过期的意图必须收尾 —— 绝不无限挂着 PROPOSED。"""
        moment = self._now(now)
        expired = 0
        rows = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        for intent in rows:
            if not intent.open or not intent.expired_at(moment):
                continue
            updated = await self.store.update_status(
                intent.intent_id,
                LifeIntentStatus.EXPIRED,
                reason="expired",
                now=moment,
            )
            if updated is None:
                continue
            expired += 1
            await self.publisher.publish(
                INITIATIVE_EXPIRED,
                updated,
                store=self.store,
                timestamp=moment,
                reason="expired_at",
            )
        return expired

    async def recover(self) -> dict[str, Any]:
        """§四十五：重启对账 —— 载入既有意图、按 ``expires_at`` 收尾，**不**批量新造。"""
        moment = self._now()
        self.recovered_at = moment
        rows = await self.store.recent(self.character_id, limit=RECENT_INTENT_LIMIT)
        expired = await self.expire_due(now=moment)
        proposed = [item for item in rows if item.status is LifeIntentStatus.PROPOSED]
        self._refresh_hints(extra=tuple(proposed))
        return {
            "action": "recovered",
            "loaded": len(rows),
            "proposed": len(proposed),
            "expired": expired,
            "skipped_generation": True,  # 恢复路径绝不产生新意图（§四十五）
        }

    # ------------------------------------------------------------ 状态推进（§二十七）

    async def resolve(self, intent_id: str, *, reason: str = "resolved") -> LifeIntent | None:
        """§二十七：``RESOLVED`` = 这个意图已经被**更高层逻辑**处理（**不是** EXECUTED）。"""
        updated = await self.store.update_status(
            intent_id, LifeIntentStatus.RESOLVED, reason=reason, now=self._now()
        )
        if updated is not None:
            await self.publisher.publish(
                INITIATIVE_RESOLVED,
                updated,
                store=self.store,
                timestamp=self._now(),
                reason=reason,
            )
        return updated

    async def cancel(self, intent_id: str, *, reason: str = "cancelled") -> LifeIntent | None:
        updated = await self.store.update_status(
            intent_id, LifeIntentStatus.CANCELLED, reason=reason, now=self._now()
        )
        if updated is not None:
            await self.publisher.publish(
                INITIATIVE_CANCELLED,
                updated,
                store=self.store,
                timestamp=self._now(),
                reason=reason,
            )
        return updated

    async def expire_one(self, intent_id: str, *, reason: str = "expired") -> LifeIntent | None:
        updated = await self.store.update_status(
            intent_id, LifeIntentStatus.EXPIRED, reason=reason, now=self._now()
        )
        if updated is not None:
            await self.publisher.publish(
                INITIATIVE_EXPIRED,
                updated,
                store=self.store,
                timestamp=self._now(),
                reason=reason,
            )
        return updated


def _replace_recovered(context: InitiativeContext, recovered_at: float) -> InitiativeContext:
    """补上"进程是刚起来的"这件事（§四十五 的静默期）——已经带了就不动。"""
    if context.recovered_at:
        return context
    return replace(context, recovered_at=float(recovered_at))
