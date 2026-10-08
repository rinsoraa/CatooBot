"""Phase 7A §九-§十五/§二十-§二十三/§二十八/§三十三：InitiativeGate。

结构（§九）：

```text
Candidate Intent → Hard Guards → Cooldown → Current Activity → Current Task
                 → Social / User Context → Goal / Routine → Intent Decision
```

这一层是**纯函数**：输入是候选意图 + 只读 context + 配置 + （bounded 的）最近意图，
输出是"哪一条被允许、其余为什么不行"。它**不写盘、不发消息、不调模型** ——
所有副作用都在 :mod:`app.initiative.service` 里，而那里也只有 store 与事件。

两条不能忘的语义（§二十五/§二十六）：

* ``SUPPRESSED != FAILED``：被抑制是**状态**，不是错误 —— 意图本身没有失败；
* 抑制**不封死未来**：条件重新满足时会产生**新的一条**意图（新 id、新指纹）。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.initiative.candidates import InitiativeContext
from app.initiative.model import LifeIntent, LifeIntentStatus, time_bucket

# ---------------------------------------------------------------- 抑制原因（§十）


class SuppressionReason(str, Enum):  # noqa: UP042
    """`§十` 的 11 条 hard guard + 两条本层的策略原因。

    **SUPPRESSED 不是失败**（§二十五）：它是"这条想法本身没问题，但现在不是时候"。
    """

    # §十 的 hard guard（顺序与任务书一致）
    ACTIVE_USER_TASK = "ACTIVE_USER_TASK"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    RECENT_USER_INTERACTION = "RECENT_USER_INTERACTION"
    QUIET_HOURS = "QUIET_HOURS"
    SLEEPING = "SLEEPING"
    HIGH_SOCIAL_FATIGUE = "HIGH_SOCIAL_FATIGUE"
    MINECRAFT_OFFLINE = "MINECRAFT_OFFLINE"
    RECENT_INITIATIVE = "RECENT_INITIATIVE"
    DUPLICATE_INTENT = "DUPLICATE_INTENT"
    CHARACTER_RECOVERY = "CHARACTER_RECOVERY"
    SYSTEM_DEGRADED = "SYSTEM_DEGRADED"
    # 本层的策略原因（不是 hard guard）
    ENGINE_DISABLED = "ENGINE_DISABLED"
    BURST_PROTECTION = "BURST_PROTECTION"
    LOWER_PRIORITY = "LOWER_PRIORITY"


#: §十 的 hard guard 词表（顺序**就是**评估顺序，可审计）
HARD_GUARD_ORDER: tuple[str, ...] = (
    SuppressionReason.CHARACTER_RECOVERY.value,
    SuppressionReason.SYSTEM_DEGRADED.value,
    SuppressionReason.ACTIVE_USER_TASK.value,
    SuppressionReason.PENDING_CONFIRMATION.value,
    SuppressionReason.SLEEPING.value,
    SuppressionReason.QUIET_HOURS.value,
    SuppressionReason.HIGH_SOCIAL_FATIGUE.value,
    SuppressionReason.MINECRAFT_OFFLINE.value,
    SuppressionReason.RECENT_USER_INTERACTION.value,
    SuppressionReason.RECENT_INITIATIVE.value,
    SuppressionReason.DUPLICATE_INTENT.value,
)

#: 任务状态里"用户任务正占着她"的那几个（§十一）
ACTIVE_TASK_STATES = ("RUNNING", "WAITING_ACTION", "PAUSED")
#: 需要确认的任务状态（§十二）—— 绝不允许自动确认，也不允许旁边冒出新方向
CONFIRMATION_TASK_STATES = ("PENDING_CONFIRMATION",)

#: 重启后的静默期（§四十五：恢复后不得出现 initiative burst）
RECOVERY_GRACE_SECONDS = 300.0
#: §三十三：10 分钟内 ≥ 5 条就进入 INITIATIVE_COOLDOWN（不再继续产生）
BURST_WINDOW_SECONDS = 600.0
BURST_MAX_IN_WINDOW = 5
#: 一轮最多放行几条（§六十"低噪声，不是越多越好"）
MAX_PROPOSALS_PER_PASS = 1
#: 社交疲劳阈值（§十 HIGH_SOCIAL_FATIGUE）
SOCIAL_FATIGUE_THRESHOLD = 0.8
#: 只有带这个 tag 的候选才需要真实世界（§三十七/§三十八）—— 7A 的候选都不带
WORLD_REQUIRED_TAG = "requires_world"


# ---------------------------------------------------------------- 结果


@dataclass(frozen=True)
class CandidateVerdict:
    """一条候选的裁决（写进审计 / WebUI，**不含**任何思维链或 prompt）。"""

    intent_type: str
    title: str
    fingerprint: str
    priority: float
    allowed: bool
    reason: str = ""
    checks: tuple[dict[str, Any], ...] = ()

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent_type": self.intent_type,
            "title": self.title,
            "fingerprint": self.fingerprint,
            "priority": float(self.priority),
            "allowed": bool(self.allowed),
            "reason": self.reason,
            "checks": [dict(item) for item in self.checks],
        }


@dataclass(frozen=True)
class GateDecision:
    """一次门禁的完整结论（§九 的 "Intent Decision"）。"""

    allowed: bool
    reason: str = ""
    selected: LifeIntent | None = None
    suppressed: tuple[LifeIntent, ...] = ()
    verdicts: tuple[CandidateVerdict, ...] = ()
    guards: dict[str, Any] = field(default_factory=dict)
    cooldown_remaining_seconds: float = 0.0
    proposals_last_hour: int = 0

    def to_payload(self) -> dict[str, Any]:
        reason_by_fp = {item.fingerprint: item.reason for item in self.verdicts}
        return {
            "allowed": bool(self.allowed),
            "reason": self.reason,
            "selected": self.selected.to_payload() if self.selected is not None else None,
            "suppressed": [
                {**item.to_payload(), "suppression_reason": reason_by_fp.get(item.fingerprint, "")}
                for item in self.suppressed
            ],
            "verdicts": [item.to_payload() for item in self.verdicts],
            "guards": dict(self.guards),
            "cooldown_remaining_seconds": float(self.cooldown_remaining_seconds),
            "proposals_last_hour": int(self.proposals_last_hour),
        }


# ---------------------------------------------------------------- 门禁


class InitiativeGate:
    """§九 的门禁本体。**只读、纯函数、确定**（§六十一）。"""

    def evaluate(
        self,
        candidates: Sequence[LifeIntent],
        *,
        context: InitiativeContext,
        config: Any,
        recent_intents: Sequence[LifeIntent] = (),
    ) -> GateDecision:
        """评估一批候选，返回"放行哪一条 / 其余为什么被抑制"。"""
        now = float(context.now)
        recent = list(recent_intents)
        guards: dict[str, Any] = {
            "evaluated_at": now,
            "candidate_count": len(candidates),
            "period": str(context.period or ""),
        }

        if not bool(getattr(config, "enabled", True)):
            # §六十五：enabled 只表示"允许产生 LifeIntent"，关掉就是完全不产
            disabled = SuppressionReason.ENGINE_DISABLED.value
            return GateDecision(
                allowed=False,
                reason=disabled,
                suppressed=tuple(candidates),
                verdicts=tuple(self._verdict(item, disabled) for item in candidates),
                guards={**guards, "engine_enabled": False},
            )

        cooldown_seconds = float(getattr(config, "cooldown_minutes", 20.0) or 0.0) * 60.0
        interaction_window = (
            float(getattr(config, "recent_interaction_suppress_minutes", 10.0) or 0.0) * 60.0
        )
        max_per_hour = int(getattr(config, "max_proposals_per_hour", 3) or 0)

        # ---- hard guards（顺序 = HARD_GUARD_ORDER，全部如实记录）
        hard = self._hard_guards(
            context, now=now, interaction_window=interaction_window, guards=guards
        )
        # ---- 冷却 / 去重 / 防爆（依赖 bounded 的最近意图）
        # §二十五/§二十六：**被抑制不算"产生过"**（SUPPRESSED != FAILED），
        # 所以冷却与防爆只看 PROPOSED；而去重看**所有**状态（同一个指纹绝不重复建行）。
        proposed = [item for item in recent if item.status is LifeIntentStatus.PROPOSED]
        proposals_last_hour = self._count_since(proposed, now=now, window=3600.0)
        burst = self._burst_state(proposed, now=now, max_per_hour=max_per_hour, guards=guards)
        last_by_type = self._last_by_type(proposed)
        fingerprints = {item.fingerprint for item in recent if item.fingerprint}
        # §六十"低噪声"：同一个**想法**只要还挂着（open），就不重复提。
        # 指纹去掉时间桶就是"词干"（character|type|goal|activity|semantic_key），
        # 所以这里判定的是"同一个念头"，而不是"同一个小时的同一个念头"。
        # 只把**PROPOSED**（真正提出来过的）算作"还挂着"：被抑制的想法**不算**
        # —— 否则睡一觉醒来，同一条想法会被自己昨天的抑制行挡死（§二十六 要求能再出现）。
        open_stems = {
            self._stem(item.fingerprint)
            for item in proposed
            if item.fingerprint and item.status is LifeIntentStatus.PROPOSED
        }
        bucket = time_bucket(now)

        verdict_by_fp: dict[str, CandidateVerdict] = {}
        survivors: list[LifeIntent] = []
        suppressed: list[LifeIntent] = []
        for candidate in candidates:
            checks: list[dict[str, Any]] = []
            reason = ""
            for name in HARD_GUARD_ORDER:
                blocked = self._guard_blocks(name, hard, candidate)
                checks.append(
                    {
                        "guard": name,
                        "ok": not blocked,
                        "detail": dict(hard.get(f"_{name}") or {}),
                    }
                )
                if blocked and not reason:
                    reason = name
            if not reason and burst:
                reason = SuppressionReason.BURST_PROTECTION.value
                checks.append(
                    {"guard": SuppressionReason.BURST_PROTECTION.value, "ok": False, "detail": {}}
                )
            key = str(candidate.intent_type.value)
            last = last_by_type.get(key)
            if not reason and last is not None and cooldown_seconds > 0:
                elapsed = now - float(last.created_at)
                if elapsed < cooldown_seconds:
                    reason = SuppressionReason.RECENT_INITIATIVE.value
                    checks.append(
                        {
                            "guard": SuppressionReason.RECENT_INITIATIVE.value,
                            "ok": False,
                            "detail": {
                                "intent_type": key,
                                "elapsed_seconds": round(elapsed, 1),
                                "cooldown_seconds": cooldown_seconds,
                            },
                        }
                    )
            if not reason and (
                candidate.fingerprint in fingerprints
                or self._stem(candidate.fingerprint) in open_stems
            ):
                reason = SuppressionReason.DUPLICATE_INTENT.value
                checks.append(
                    {
                        "guard": SuppressionReason.DUPLICATE_INTENT.value,
                        "ok": False,
                        "detail": {
                            "fingerprint": candidate.fingerprint,
                            "stem": self._stem(candidate.fingerprint),
                            "bucket": int(bucket),
                            "already_open": self._stem(candidate.fingerprint) in open_stems,
                        },
                    }
                )
            verdict_by_fp[candidate.fingerprint] = self._verdict(
                candidate, reason, checks=tuple(checks)
            )
            if reason:
                suppressed.append(candidate)
            else:
                survivors.append(candidate)

        # ---- 选择（§六十：低噪声 —— 一轮最多放行 MAX_PROPOSALS_PER_PASS 条）
        survivors.sort(
            key=lambda item: (
                -float(item.priority),
                item.intent_type.value,
                item.fingerprint,
            )
        )
        selected: LifeIntent | None = None
        if survivors:
            selected = survivors[0]
            for extra in survivors[MAX_PROPOSALS_PER_PASS:]:
                suppressed.append(extra)
                previous = verdict_by_fp.get(extra.fingerprint)
                kept = tuple(previous.checks) if previous is not None else ()
                # 一个候选**恰好**一条裁决：剪枝时替换它原本的裁决（不是再追加一条）
                verdict_by_fp[extra.fingerprint] = self._verdict(
                    extra,
                    SuppressionReason.LOWER_PRIORITY.value,
                    checks=kept
                    + (
                        {
                            "guard": SuppressionReason.LOWER_PRIORITY.value,
                            "ok": False,
                            "detail": {"selected": selected.intent_type.value},
                        },
                    ),
                )
        # 按候选原本的顺序给裁决（一个候选一条）
        verdicts: list[CandidateVerdict] = [
            verdict_by_fp[item.fingerprint]
            for item in candidates
            if item.fingerprint in verdict_by_fp
        ]
        reason = "" if selected is not None else self._first_reason(verdicts, candidates)
        guards["proposals_last_hour"] = proposals_last_hour
        guards["burst"] = bool(burst)
        guards["cooldown_seconds"] = cooldown_seconds
        return GateDecision(
            allowed=selected is not None,
            reason=reason,
            selected=selected,
            suppressed=tuple(suppressed),
            verdicts=tuple(verdicts),
            guards=guards,
            cooldown_remaining_seconds=self._cooldown_remaining(
                last_by_type, now=now, cooldown_seconds=cooldown_seconds
            ),
            proposals_last_hour=proposals_last_hour,
        )

    # ------------------------------------------------------------ hard guards

    def _hard_guards(
        self,
        context: InitiativeContext,
        *,
        now: float,
        interaction_window: float,
        guards: dict[str, Any],
    ) -> dict[str, Any]:
        """§十 的 11 条：返回 ``{name: blocked}``（+ ``_name`` 里的细节）。"""
        states = {str(state).upper() for state in context.task_states}
        blocked_active_task = bool(states & set(ACTIVE_TASK_STATES))
        blocked_confirmation = bool(context.pending_confirmation) or bool(
            states & set(CONFIRMATION_TASK_STATES)
        )
        recovered_ago = now - float(context.recovered_at or 0.0)
        blocked_recovery = bool(context.recovered_at) and recovered_ago < RECOVERY_GRACE_SECONDS
        blocked_degraded = bool(str(context.degraded or "").strip())
        blocked_interaction = (
            bool(context.user_interaction_at)
            and interaction_window > 0
            and (now - float(context.user_interaction_at)) < interaction_window
        )
        fatigue = float(getattr(context, "social_fatigue", 0.0) or 0.0)

        guards["_CHARACTER_RECOVERY"] = {
            "recovered_ago_seconds": round(recovered_ago, 1),
            "grace_seconds": RECOVERY_GRACE_SECONDS,
        }
        guards["_SYSTEM_DEGRADED"] = {"degraded": str(context.degraded or "")}
        guards["_ACTIVE_USER_TASK"] = {"task_states": sorted(states)}
        guards["_PENDING_CONFIRMATION"] = {"pending": bool(blocked_confirmation)}
        guards["_SLEEPING"] = {"sleeping": bool(context.sleeping)}
        guards["_QUIET_HOURS"] = {
            "quiet_hours": bool(context.quiet_hours),
            "period": context.period,
        }
        guards["_HIGH_SOCIAL_FATIGUE"] = {
            "fatigue": fatigue,
            "threshold": SOCIAL_FATIGUE_THRESHOLD,
        }
        guards["_MINECRAFT_OFFLINE"] = {"online": bool(context.minecraft_online)}
        guards["_RECENT_USER_INTERACTION"] = {
            "seconds_ago": round(now - float(context.user_interaction_at), 1)
            if context.user_interaction_at
            else None,
            "window_seconds": interaction_window,
        }
        guards["_RECENT_INITIATIVE"] = {"cooldown_checked": True}
        guards["_DUPLICATE_INTENT"] = {"bucket": int(time_bucket(now))}

        return {
            SuppressionReason.CHARACTER_RECOVERY.value: blocked_recovery,
            SuppressionReason.SYSTEM_DEGRADED.value: blocked_degraded,
            SuppressionReason.ACTIVE_USER_TASK.value: blocked_active_task,
            SuppressionReason.PENDING_CONFIRMATION.value: blocked_confirmation,
            SuppressionReason.SLEEPING.value: bool(context.sleeping),
            SuppressionReason.QUIET_HOURS.value: bool(context.quiet_hours),
            SuppressionReason.HIGH_SOCIAL_FATIGUE.value: fatigue >= SOCIAL_FATIGUE_THRESHOLD,
            # §三十七：Minecraft 离线**不**封杀"想去看看"这种虚拟兴趣；
            # 只有明确需要真实世界的候选（tag=requires_world）才被它挡下。
            SuppressionReason.MINECRAFT_OFFLINE.value: not bool(context.minecraft_online),
            SuppressionReason.RECENT_USER_INTERACTION.value: blocked_interaction,
            SuppressionReason.RECENT_INITIATIVE.value: False,
            SuppressionReason.DUPLICATE_INTENT.value: False,
        }

    def _world_required(self, candidate: LifeIntent) -> bool:
        return WORLD_REQUIRED_TAG in tuple(candidate.tags or ())

    def _guard_blocks(self, name: str, hard: dict[str, Any], candidate: LifeIntent) -> bool:
        """某条 hard guard 是否挡下**这一条**候选。

        §三十七/§三十八：Minecraft 离线只挡"明确需要真实世界"的候选（``requires_world``）——
        "有点想回 Minecraft 看看"这种虚拟兴趣在离线时**照样可以提**。
        """
        if not hard.get(name):
            return False
        if name == SuppressionReason.MINECRAFT_OFFLINE.value:
            return self._world_required(candidate)
        return True

    # ------------------------------------------------------------ 冷却 / 防爆

    @staticmethod
    def _count_since(intents: Sequence[LifeIntent], *, now: float, window: float) -> int:
        return sum(1 for item in intents if 0 <= now - float(item.created_at) < window)

    def _burst_state(
        self,
        intents: Sequence[LifeIntent],
        *,
        now: float,
        max_per_hour: int,
        guards: dict[str, Any],
    ) -> bool:
        in_window = self._count_since(intents, now=now, window=BURST_WINDOW_SECONDS)
        last_hour = self._count_since(intents, now=now, window=3600.0)
        burst = in_window >= BURST_MAX_IN_WINDOW or (max_per_hour > 0 and last_hour >= max_per_hour)
        guards["_BURST"] = {
            "in_window": in_window,
            "window_seconds": BURST_WINDOW_SECONDS,
            "max_in_window": BURST_MAX_IN_WINDOW,
            "last_hour": last_hour,
            "max_per_hour": max_per_hour,
        }
        return burst

    @staticmethod
    def _stem(fingerprint: str) -> str:
        """指纹去掉时间桶 = "同一个想法"（§二十 的指纹前六段）。"""
        return str(fingerprint or "").rsplit("|", 1)[0]

    @staticmethod
    def _last_by_type(intents: Sequence[LifeIntent]) -> dict[str, LifeIntent]:
        out: dict[str, LifeIntent] = {}
        for item in intents:
            key = str(item.intent_type.value)
            current = out.get(key)
            if current is None or float(item.created_at) > float(current.created_at):
                out[key] = item
        return out

    @staticmethod
    def _cooldown_remaining(
        last_by_type: dict[str, LifeIntent], *, now: float, cooldown_seconds: float
    ) -> float:
        if cooldown_seconds <= 0 or not last_by_type:
            return 0.0
        newest = max(last_by_type.values(), key=lambda item: float(item.created_at))
        remaining = cooldown_seconds - (now - float(newest.created_at))
        return round(max(0.0, remaining), 1)

    # ------------------------------------------------------------ 小工具

    def _verdict(
        self,
        candidate: LifeIntent,
        reason: str,
        *,
        checks: tuple[dict[str, Any], ...] = (),
    ) -> CandidateVerdict:
        return CandidateVerdict(
            intent_type=str(candidate.intent_type.value),
            title=str(candidate.title),
            fingerprint=str(candidate.fingerprint),
            priority=float(candidate.priority),
            allowed=not reason,
            reason=reason,
            checks=checks,
        )

    @staticmethod
    def _first_reason(
        verdicts: Sequence[CandidateVerdict], candidates: Sequence[LifeIntent]
    ) -> str:
        if not candidates:
            return ""
        for verdict in verdicts:
            if verdict.reason:
                return verdict.reason
        return ""


def suppression_reasons() -> tuple[str, ...]:
    """词表（测试与文档共用）：11 条 hard guard + 策略原因。"""
    return tuple(item.value for item in SuppressionReason)


def is_guard_reason(reason: str) -> bool:
    return str(reason) in HARD_GUARD_ORDER


__all__ = [
    "ACTIVE_TASK_STATES",
    "BURST_MAX_IN_WINDOW",
    "BURST_WINDOW_SECONDS",
    "CandidateVerdict",
    "CONFIRMATION_TASK_STATES",
    "GateDecision",
    "HARD_GUARD_ORDER",
    "InitiativeGate",
    "MAX_PROPOSALS_PER_PASS",
    "RECOVERY_GRACE_SECONDS",
    "SOCIAL_FATIGUE_THRESHOLD",
    "SuppressionReason",
    "WORLD_REQUIRED_TAG",
    "is_guard_reason",
    "suppression_reasons",
]
