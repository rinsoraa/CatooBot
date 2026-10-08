"""Phase 6B：Activity Decision Engine / Transition Guard（§三-§二十五）。

职责边界**严格**：

```
World Clock → 当前 Episode → Transition Guard → Decision Engine
                                                   ├── CONTINUE
                                                   ├── EXTEND
                                                   └── TRANSITION
                                                   ↓（只有生命周期改动）
                                              ActivityRuntime
```

* **只做规则**（§三/§一二一）：6B 是确定性规则引擎 —— 没有 LLM、没有 `random`、没有概率阈值
  （§二一/§二三）。v1.0 §22 的 ``ActivityContinuationEvaluator`` 里"模型那一半"（continue vs extend
  的偏好、具体下一个活动）留给后续阶段，这里只留一个可注入的 ``advisor`` 接口。
* **不拥有世界写权限**（§二五/§五二）：这个模块既 import 不到、也调用不到任何 Minecraft 动作、
  ActionRuntime、TaskRuntime.confirm / ConfirmationStore —— 有 AST 级 guard 测试守着。
* **硬规则优先**（§二四）：先最短时长 / 最长时长 / 撞车护栏，再谈"换不换活动"。
* **Checker 只报不修**（§二一）：``WorldConsistencyChecker`` 只 detect/report，
  绝不自己删/改 Episode；怎么修由 ActivityRuntime 的 transition / recovery 决定。
"""

from __future__ import annotations

import time
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.activity.model import (
    ActivityEpisode,
    ActivityStatus,
    TransitionReason,
    looks_like_minecraft_activity,
)

# Phase 6D：模型顾问（软判断）—— 只在这里用它，而且只在 §二十六 的第 8-14 步
from app.activity.model_advisor import (
    ActivityDecisionProposal,
    ModelFailureCode,
    advisory_cycle_key,
)

# ---------------------------------------------------------------- 枚举


class ActivityDecisionKind(str, Enum):  # noqa: UP042 - 面向 JSON 的枚举
    """决策结果（§四）。**没有** EXECUTE / ACT / DO_TOOL —— 这一层不执行任何东西。"""

    CONTINUE = "CONTINUE"
    EXTEND = "EXTEND"
    TRANSITION = "TRANSITION"


class DecisionReason(str, Enum):  # noqa: UP042
    """原因码（§二三）—— 每一次决策都必须能回答"为什么"。"""

    BEFORE_END = "BEFORE_END"
    TRANSITION_WINDOW = "TRANSITION_WINDOW"
    MAX_DURATION = "MAX_DURATION"
    MIN_DURATION_GUARD = "MIN_DURATION_GUARD"
    BOUNCE_GUARD = "BOUNCE_GUARD"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_INTERRUPTED = "TASK_INTERRUPTED"
    USER_INTERACTION = "USER_INTERACTION"
    WORLD_EVENT = "WORLD_EVENT"
    RECOVERY = "RECOVERY"
    NO_VALID_TRANSITION = "NO_VALID_TRANSITION"


class DecisionTrigger(str, Enum):  # noqa: UP042
    """什么触发了这次决策（§十二）。**绝不允许** ``RANDOM_TICK``。"""

    TIME_NEAR_END = "TIME_NEAR_END"
    TIME_EXPIRED = "TIME_EXPIRED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TASK_INTERRUPTED = "TASK_INTERRUPTED"
    USER_INTERACTION = "USER_INTERACTION"
    WORLD_EVENT = "WORLD_EVENT"
    GOAL_CHANGED = "GOAL_CHANGED"
    RECOVERY = "RECOVERY"
    MANUAL = "MANUAL"


#: **硬中断**（§十四）：这些原因可以突破 ``min_duration``（最短时长护栏）
HARD_INTERRUPT_TRIGGERS: frozenset[DecisionTrigger] = frozenset(
    {
        DecisionTrigger.TASK_STARTED,
        DecisionTrigger.TASK_COMPLETED,
        DecisionTrigger.TASK_FAILED,
        DecisionTrigger.TASK_INTERRUPTED,
        DecisionTrigger.USER_INTERACTION,
        DecisionTrigger.RECOVERY,
        DecisionTrigger.MANUAL,
    }
)

#: 触发器 → 原因码（"谁引起的"直接写进审计）
REASON_BY_TRIGGER: dict[DecisionTrigger, DecisionReason] = {
    DecisionTrigger.TASK_STARTED: DecisionReason.TASK_STARTED,
    DecisionTrigger.TASK_COMPLETED: DecisionReason.TASK_COMPLETED,
    DecisionTrigger.TASK_FAILED: DecisionReason.TASK_FAILED,
    DecisionTrigger.TASK_INTERRUPTED: DecisionReason.TASK_INTERRUPTED,
    DecisionTrigger.USER_INTERACTION: DecisionReason.USER_INTERACTION,
    DecisionTrigger.WORLD_EVENT: DecisionReason.WORLD_EVENT,
    DecisionTrigger.RECOVERY: DecisionReason.RECOVERY,
}

#: 兜底活动（§三十一）：没有合适的下一个活动时，绝不把 activity 置空
FALLBACK_ACTIVITIES: tuple[str, ...] = ("idle", "free_time", "resting")

#: "中性"活动：撞车护栏拒绝某个候选时用它（§十八：要么 CONTINUE，要么选中性活动）
NEUTRAL_BY_PERIOD: dict[str, str] = {
    "morning": "idle",
    "afternoon": "idle",
    "evening": "idle",
    "night": "napping",
}

#: 6D.1 A：候选出口的策略名（写进 ``guards["bounce_resolution"]["policy"]``，可审计）
#: 策略本身是"candidate → Bounce Guard → accepted / rejected"，**没有**任何旁路。
BOUNCE_RESOLUTION_POLICY = "candidate->bounce_guard->accept_reject"

#: 可能出现"语义冲突"的组合（§二十 Rule 5）：只报 WARNING，绝不静默吞掉
CONFLICT_PAIRS: tuple[tuple[str, str], ...] = (
    ("sleeping", "kitchen"),
    ("napping", "kitchen"),
    ("sleeping", "out"),
    ("napping", "out"),
)


# ---------------------------------------------------------------- 决策对象


@dataclass(frozen=True)
class ActivityDecision:
    """一次决策的**唯一**输出形状（§四）。"""

    decision: ActivityDecisionKind
    reason_code: DecisionReason
    next_activity_hint: str = ""
    extension_seconds: float = 0.0
    #: 规则强度（**不是**概率阈值，§二三）：硬规则 1.0、启发式兜底更低
    confidence: float = 1.0
    trace_id: str = ""

    @property
    def extend(self) -> bool:
        return self.decision is ActivityDecisionKind.EXTEND

    @property
    def transition(self) -> bool:
        return self.decision is ActivityDecisionKind.TRANSITION

    def to_payload(self) -> dict[str, Any]:
        return {
            "decision": self.decision.value,
            "reason_code": self.reason_code.value,
            "next_activity_hint": self.next_activity_hint,
            "extension_seconds": float(self.extension_seconds),
            "confidence": float(self.confidence),
            "trace_id": self.trace_id,
        }


@dataclass
class DecisionTrace:
    """可解释的结构化决策事实（§二二）—— **不保存隐藏思维链**。"""

    trace_id: str
    episode_id: str
    character_id: str
    current_activity: str
    trigger: str
    decision: str
    reason_code: str
    elapsed: float = 0.0
    planned_end_at: float = 0.0
    min_duration: float = 0.0
    typical_duration: float = 0.0
    max_duration: float = 0.0
    extension_count: int = 0
    time_period: str = ""
    transition_pending: bool = False
    next_activity_hint: str = ""
    extension_seconds: float = 0.0
    guard_results: dict[str, Any] = field(default_factory=dict)
    decided_at: float = 0.0
    # ---- Phase 6D §三十二：模型这一半的结构化事实（**绝不含 prompt / 思维链 / 凭据**）
    model_attempted: bool = False
    model_provider: str = ""
    model_latency_ms: int = 0
    model_result: dict[str, Any] = field(default_factory=dict)
    model_rejected: bool = False
    model_reject_reason: str = ""
    fallback_used: bool = False

    def to_payload(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "episode_id": self.episode_id,
            "character_id": self.character_id,
            "current_activity": self.current_activity,
            "trigger": self.trigger,
            "decision": self.decision,
            "reason_code": self.reason_code,
            "elapsed": float(self.elapsed),
            "planned_end_at": float(self.planned_end_at),
            "min_duration": float(self.min_duration),
            "typical_duration": float(self.typical_duration),
            "max_duration": float(self.max_duration),
            "extension_count": int(self.extension_count),
            "time_period": self.time_period,
            "transition_pending": bool(self.transition_pending),
            "next_activity_hint": self.next_activity_hint,
            "extension_seconds": float(self.extension_seconds),
            "guard_results": dict(self.guard_results),
            "decided_at": float(self.decided_at),
            "model_attempted": bool(self.model_attempted),
            "model_provider": self.model_provider,
            "model_latency_ms": int(self.model_latency_ms),
            "model_result": dict(self.model_result),
            "model_rejected": bool(self.model_rejected),
            "model_reject_reason": self.model_reject_reason,
            "fallback_used": bool(self.fallback_used),
        }


@dataclass(frozen=True)
class GuardVerdict:
    """一条护栏的结论（allowed=False 时带原因码与细节，方便审计）。"""

    ok: bool
    code: DecisionReason
    detail: dict[str, Any] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"ok": bool(self.ok), "code": self.code.value, "detail": dict(self.detail)}


# ---------------------------------------------------------------- 护栏


class ActivityBounceGuard:
    """撞车护栏（§十七/§十八）：防止 A → B → A 这种快速来回跳。

    判据（全部确定性）：

    * 候选活动在最近 ``cooldown`` 内**刚刚结束过** → 判定为撞车，拒绝；
    * 当前 Episode 还没稳定到 ``minimum_stable_seconds`` → 也拒绝（别把 B 也切碎）。
    """

    def __init__(
        self,
        *,
        cooldown_seconds: float,
        minimum_stable_seconds: float = 0.0,
        clock: Any = time.time,
    ) -> None:
        self.cooldown_seconds = max(0.0, float(cooldown_seconds))
        self.minimum_stable_seconds = max(0.0, float(minimum_stable_seconds))
        self._clock = clock
        self.rejections = 0

    def check(
        self,
        *,
        current: ActivityEpisode,
        candidate: str,
        history: Sequence[ActivityEpisode],
        now: float,
    ) -> GuardVerdict:
        name = str(candidate or "").strip()
        if not name:
            return GuardVerdict(False, DecisionReason.BOUNCE_GUARD, {"reason": "empty_candidate"})
        if self.minimum_stable_seconds and current.elapsed(now) < self.minimum_stable_seconds:
            self.rejections += 1
            return GuardVerdict(
                False,
                DecisionReason.BOUNCE_GUARD,
                {
                    "reason": "not_stable_yet",
                    "elapsed": round(current.elapsed(now), 1),
                    "minimum_stable_seconds": self.minimum_stable_seconds,
                },
            )
        recent = self._recent_same(name, history, now=now)
        if recent is not None:
            self.rejections += 1
            return GuardVerdict(
                False,
                DecisionReason.BOUNCE_GUARD,
                {
                    "reason": "cooldown",
                    "candidate": name,
                    "ended_at": float(recent.ended_at or recent.updated_at or 0.0),
                    "cooldown_seconds": self.cooldown_seconds,
                },
            )
        return GuardVerdict(True, DecisionReason.TRANSITION_WINDOW, {"candidate": name})

    def _recent_same(
        self, name: str, history: Sequence[ActivityEpisode], *, now: float
    ) -> ActivityEpisode | None:
        wanted = str(name).strip().lower()
        for episode in history:
            if str(episode.activity_name).strip().lower() != wanted:
                continue
            if not episode.status.terminal:
                continue  # 还活着的不是"刚才跳走过"
            ended = float(episode.ended_at or episode.updated_at or 0.0)
            if ended and (now - ended) <= self.cooldown_seconds:
                return episode
        return None

    def neutral_fallback(self, *, period: str) -> str:
        """撞车时换一个**中性**活动（§十八）—— 确定性，不随机。"""
        return NEUTRAL_BY_PERIOD.get(str(period), "idle")

    def payload(self) -> dict[str, Any]:
        return {
            "cooldown_seconds": self.cooldown_seconds,
            "minimum_stable_seconds": self.minimum_stable_seconds,
            "rejections": int(self.rejections),
        }


class TransitionGuard:
    """把 §十四/§十五/§十六 的**硬规则**集中在一处（决策引擎只问它，不自己解释）。"""

    def __init__(self, *, max_extensions: int = 2) -> None:
        self.max_extensions = max(0, int(max_extensions))

    def minimum_duration(self, episode: ActivityEpisode, *, now: float, hard: bool) -> GuardVerdict:
        """最短时长（§十四）：还没到 ``min_duration`` 就不许换 —— **除非硬中断**。"""
        elapsed = episode.elapsed(now)
        minimum = float(episode.min_duration or 0.0)
        if hard:
            return GuardVerdict(
                True, DecisionReason.MIN_DURATION_GUARD, {"bypassed_by": "hard_interruption"}
            )
        if minimum and elapsed < minimum:
            return GuardVerdict(
                False,
                DecisionReason.MIN_DURATION_GUARD,
                {
                    "elapsed": round(elapsed, 1),
                    "min_duration": minimum,
                    "remaining": round(minimum - elapsed, 1),
                },
            )
        return GuardVerdict(True, DecisionReason.MIN_DURATION_GUARD, {"elapsed": round(elapsed, 1)})

    def maximum_duration(self, episode: ActivityEpisode, *, now: float) -> GuardVerdict:
        """最长时长（§十五）：到硬上限**必须** TRANSITION，这是 hard rule。"""
        limit = episode.max_end_at
        if limit and now >= limit:
            return GuardVerdict(
                False,
                DecisionReason.MAX_DURATION,
                {"now": float(now), "max_end_at": limit, "max_duration": episode.max_duration},
            )
        return GuardVerdict(True, DecisionReason.MAX_DURATION, {"max_end_at": limit})

    def extension(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        extension_seconds: float,
    ) -> GuardVerdict:
        """延长护栏（§十六）：次数上限 + 绝不越过硬上限。"""
        allowed = self.max_extensions
        used = int(episode.extension_count or 0)
        if used >= allowed:
            return GuardVerdict(
                False,
                DecisionReason.TRANSITION_WINDOW,
                {"reason": "max_extensions", "extension_count": used, "max": allowed},
            )
        seconds = max(0.0, float(extension_seconds))
        if seconds <= 0.0:
            return GuardVerdict(
                False, DecisionReason.TRANSITION_WINDOW, {"reason": "no_extension_budget"}
            )
        limit = episode.max_end_at
        new_end = float(episode.planned_end_at or now) + seconds
        if limit and new_end > limit:
            trimmed = max(0.0, limit - float(episode.planned_end_at or now))
            if trimmed <= 0.0:
                return GuardVerdict(
                    False,
                    DecisionReason.MAX_DURATION,
                    {"reason": "would_exceed_max", "max_end_at": limit},
                )
            return GuardVerdict(
                True,
                DecisionReason.TRANSITION_WINDOW,
                {"extension_seconds": trimmed, "trimmed_to_max": True},
            )
        return GuardVerdict(True, DecisionReason.TRANSITION_WINDOW, {"extension_seconds": seconds})


# ---------------------------------------------------------------- 一致性检查器


@dataclass
class ConsistencyReport:
    """只读的一致性结论（§二十）—— ``ok=False`` 时**不许**静默吞掉。"""

    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    checked: int = 0

    @property
    def ok(self) -> bool:
        return not self.errors

    def to_payload(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "checked": int(self.checked),
            "errors": [dict(item) for item in self.errors],
            "warnings": [dict(item) for item in self.warnings],
        }


class WorldConsistencyChecker:
    """检测并报告（v1.0 §65 / 6B §二十）。

    **绝不自动修复**（§二一）：发现两条 ACTIVE 之类的异常只报 ERROR / DEGRADED，
    怎么处理交给 ActivityRuntime 的 transition / recovery —— 这里连写权限都没有。
    """

    def check(
        self,
        *,
        current: ActivityEpisode | None,
        now: float,
        live: Sequence[ActivityEpisode] = (),
        force: bool = False,
    ) -> ConsistencyReport:
        report = ConsistencyReport(checked=1)
        # Rule 3：每个角色最多一条 live primary Episode
        if len(list(live)) > 1:
            report.errors.append(
                {
                    "rule": "single_live_primary",
                    "detail": [item.episode_id for item in live],
                }
            )
        if current is None:
            return report
        # Rule 1：started_at 不能在未来
        if current.started_at and float(current.started_at) > float(now) + 1.0:
            report.errors.append(
                {
                    "rule": "started_at_in_future",
                    "episode_id": current.episode_id,
                    "started_at": float(current.started_at),
                }
            )
        # Rule 2：ended_at 不能早于 started_at
        if current.ended_at and current.started_at and current.ended_at < current.started_at:
            report.errors.append(
                {
                    "rule": "ended_before_started",
                    "episode_id": current.episode_id,
                    "started_at": float(current.started_at),
                    "ended_at": float(current.ended_at),
                }
            )
        # Rule 4：当前 Episode 必须是"活着"的状态（否则就不该被当成 current）
        if not current.status.open and not force:
            report.errors.append(
                {
                    "rule": "current_not_live",
                    "episode_id": current.episode_id,
                    "status": current.status.value,
                }
            )
        # Rule 5：位置与活动明显冲突（只 WARNING，绝不静默吞掉）
        conflict = self._conflict(current)
        if conflict is not None:
            report.warnings.append(conflict)
        # 附加（6A 以来的事实）：时长档必须 0 < min <= typical <= max
        if not (
            0
            < float(current.min_duration or 0.0)
            <= float(current.typical_duration or 0.0)
            <= float(current.max_duration or 0.0)
        ):
            report.errors.append(
                {
                    "rule": "duration_profile_invalid",
                    "episode_id": current.episode_id,
                    "min": current.min_duration,
                    "typical": current.typical_duration,
                    "max": current.max_duration,
                }
            )
        return report

    @staticmethod
    def _conflict(episode: ActivityEpisode) -> dict[str, Any] | None:
        activity = str(episode.activity_name or "").strip().lower()
        location = str(episode.location or "").strip().lower()
        if not activity or not location:
            return None
        for left, right in CONFLICT_PAIRS:
            if (left in activity and right in location) or (right in activity and left in location):
                return {
                    "rule": "activity_location_conflict",
                    "episode_id": episode.episode_id,
                    "activity": episode.activity_name,
                    "location": episode.location,
                }
        return None


# ---------------------------------------------------------------- 决策引擎


class ActivityDecisionEngine:
    """规则优先的决策引擎（§二四 的流水线，一步不漏）。"""

    def __init__(
        self,
        *,
        clock: Any,
        planner: Any,
        transition_window_seconds: float = 300.0,
        max_extensions: int = 2,
        bounce_cooldown_seconds: float = 600.0,
        guard: TransitionGuard | None = None,
        bounce: ActivityBounceGuard | None = None,
        checker: WorldConsistencyChecker | None = None,
        advisor: Any = None,
        advisory_probe: Any = None,
        advisory_note: Any = None,
        minecraft_context: Any = None,
        logger: Any = None,
    ) -> None:
        self.clock = clock
        self.planner = planner
        self.transition_window_seconds = max(0.0, float(transition_window_seconds))
        self.guard = guard or TransitionGuard(max_extensions=max_extensions)
        self.bounce = bounce or ActivityBounceGuard(
            cooldown_seconds=bounce_cooldown_seconds, clock=clock
        )
        self.checker = checker or WorldConsistencyChecker()
        #: v1.0 §22 的"模型那一半"接口（Phase 6D 起真的会说话；`None` = 纯规则模式，§四十）
        self.advisor = advisor
        #: §三十八：持久侧的"这个 cycle 问过没"探针（异步，由 runtime 注入；缺省只用内存）
        self.advisory_probe = advisory_probe
        #: §七/§三十八：把"问过了"写进既有审计（异步；缺省只记内存）
        self.advisory_note = advisory_note
        #: §十：给顾问看的**只读** minecraft 观察切片（同步可调用 → dict；缺省没有）
        self.minecraft_context = minecraft_context
        #: 最近一次顾问回执（只读展示；持久事实在审计行与 trace 里）
        self.last_receipt: dict[str, Any] = {}
        self._log = logger
        #: 最近几次决策的 trace（内存里、只读展示；持久事实在 Episode 行上）
        self.traces: list[DecisionTrace] = []
        self.max_traces = 32
        #: 上一次**对外记 INFO** 的决策签名（逐 tick 的平凡 CONTINUE 只写 DEBUG，§十/§四九：
        #: 普通 tick 不该刷日志 —— 真机上曾经一秒一条）
        self._log_signature: tuple[Any, ...] = ()

    # ------------------------------------------------------------ 只读视图

    def transition_pending(self, episode: ActivityEpisode | None, *, now: float) -> bool:
        """是否已经进入 transition window（§十一：**只是准备**，不切活动）。"""
        if episode is None or episode.status not in {
            ActivityStatus.ACTIVE,
            ActivityStatus.EXTENDED,
        }:
            return False
        planned = float(episode.planned_end_at or 0.0)
        if not planned:
            return False
        return float(now) >= planned - self.transition_window_seconds

    def last_trace(self) -> DecisionTrace | None:
        return self.traces[-1] if self.traces else None

    def view(self, episode: ActivityEpisode | None, *, now: float) -> dict[str, Any]:
        """WebUI/API 的只读决策视图（§四七/§四八）——不含任何思维链。"""
        trace = self.last_trace()
        return {
            "episode_id": episode.episode_id if episode else "",
            "current_activity": episode.activity_name if episode else "",
            "status": episode.status.value if episode else "",
            "elapsed_seconds": round(episode.elapsed(now), 1) if episode else 0.0,
            "planned_end_at": float(episode.planned_end_at or 0.0) if episode else 0.0,
            "transition_window_seconds": self.transition_window_seconds,
            "transition_pending": self.transition_pending(episode, now=now),
            "extension_count": int(episode.extension_count or 0) if episode else 0,
            "max_extensions": self.guard.max_extensions,
            "last_decision": trace.to_payload() if trace is not None else None,
            # Phase 6D §八十九：顾问回执（只读；没有就空对象）
            "model": dict(self.last_receipt),
            "guard": {
                "max_extensions": self.guard.max_extensions,
                "transition_window_seconds": self.transition_window_seconds,
                "bounce": self.bounce.payload(),
            },
        }

    # ------------------------------------------------------------ 决策

    async def decide(
        self,
        *,
        episode: ActivityEpisode,
        now: float,
        trigger: DecisionTrigger,
        history: Sequence[ActivityEpisode] = (),
        live: Sequence[ActivityEpisode] = (),
        started_today: int = 0,
    ) -> tuple[ActivityDecision, DecisionTrace]:
        """跑完 §二四 的流水线，给出 (决策, trace)。**不改任何状态**。"""
        hard = trigger in HARD_INTERRUPT_TRIGGERS
        period = self._period(now)
        guards: dict[str, Any] = {}
        # 1-2. 校验（只读；异常只报告，不修）
        report = self.checker.check(current=episode, now=now, live=live)
        guards["consistency"] = report.to_payload()
        if not report.ok:
            if self._log is not None:
                self._log.warning(
                    "[World.Activity] 一致性检查报错 episode=%s errors=%s",
                    episode.episode_id,
                    [item.get("rule") for item in report.errors],
                )
            return self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.CONTINUE,
                    DecisionReason.NO_VALID_TRANSITION,
                    confidence=0.5,
                ),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        # 4. 硬中断（可以突破最短时长）
        if hard:
            # 6D.1 A：硬中断**也**走同一个出口 —— 它可以突破最短时长（§十四），
            # 但绝不突破撞车护栏（护栏是 §十七/§十八 的硬规则，不是时长规则）。
            chosen, resolution = self._resolve_next_activity(
                episode,
                now=now,
                period=period,
                started_today=started_today,
                history=history,
                preferred=await self._next_hint(
                    episode, now=now, started_today=started_today, period=period
                ),
            )
            guards["bounce"] = self._preferred_verdict(resolution)
            guards["bounce_resolution"] = resolution
            reason_by_trigger = REASON_BY_TRIGGER.get(trigger, DecisionReason.USER_INTERACTION)
            if chosen:
                return self._finish(
                    episode,
                    ActivityDecision(
                        ActivityDecisionKind.TRANSITION,
                        reason_by_trigger,
                        next_activity_hint=chosen,
                    ),
                    now=now,
                    trigger=trigger,
                    period=period,
                    guards=guards,
                )
            # 一个合法候选都没有 → 只能留在当前活动（绝不换到被护栏拒绝的活动）
            return self._finish(
                episode,
                ActivityDecision(ActivityDecisionKind.CONTINUE, reason_by_trigger, confidence=0.5),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        # 5. 最短时长
        minimum = self.guard.minimum_duration(episode, now=now, hard=False)
        guards["min_duration"] = minimum.to_payload()
        if not minimum.ok:
            return self._finish(
                episode,
                ActivityDecision(ActivityDecisionKind.CONTINUE, minimum.code),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        # 6. 最长时长（hard rule：到上限必须换）
        maximum = self.guard.maximum_duration(episode, now=now)
        guards["max_duration"] = maximum.to_payload()
        if not maximum.ok:
            # §十五 hard rule：到硬上限**必须**换 —— 连"同名合并 → EXTEND"这条捷径也不许走
            return await self._transition(
                episode,
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
                reason=DecisionReason.MAX_DURATION,
                started_today=started_today,
                history=history,
                force_change=True,
            )
        planned = float(episode.planned_end_at or 0.0)
        # 7. 还没进 window：什么都不做（§十/§十三）
        if not planned or now < planned - self.transition_window_seconds:
            return self._finish(
                episode,
                ActivityDecision(ActivityDecisionKind.CONTINUE, DecisionReason.BEFORE_END),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        # 8. window 内但还没到期：只立 transition_pending，**不切活动**（§十一/§十六）
        if now < planned and trigger is not DecisionTrigger.TIME_EXPIRED:
            return self._finish(
                episode,
                ActivityDecision(ActivityDecisionKind.CONTINUE, DecisionReason.TRANSITION_WINDOW),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
                transition_pending=True,
            )
        # 9. 到期：先看能不能延长（§十六 延长护栏），再谈换活动
        extension_seconds = float(episode.typical_duration or 0.0)
        allowed = self.guard.extension(episode, now=now, extension_seconds=extension_seconds)
        guards["extension"] = allowed.to_payload()
        # 9b. Phase 6D：硬约束已经算完（§十九）→ 在**软空间**里问一次顾问
        # （§二十/§二十六 第 8-14 步）。
        # 顾问说不上话 / 失败 / 被规则拒 → 什么都不改，直接落到下面原样的规则路径（§二十七/§三十）。
        advised, receipt = await self._maybe_advise(
            episode,
            now=now,
            period=period,
            guards=guards,
            extension=allowed,
            started_today=started_today,
            history=history,
        )
        if advised is not None:
            return self._finish(
                episode,
                advised,
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
                transition_pending=True,
                receipt=receipt,
            )
        if allowed.ok and self._extendable(episode):
            seconds = float(allowed.detail.get("extension_seconds") or 0.0)
            decision, trace = self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.EXTEND,
                    DecisionReason.TRANSITION_WINDOW,
                    extension_seconds=seconds,
                ),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
                transition_pending=True,
            )
            self._attach_receipt(trace, receipt)
            return decision, trace
        # 10. 换活动（过撞车护栏）
        decision, trace = await self._transition(
            episode,
            now=now,
            trigger=trigger,
            period=period,
            guards=guards,
            reason=DecisionReason.TRANSITION_WINDOW,
            started_today=started_today,
            history=history,
        )
        self._attach_receipt(trace, receipt)
        return decision, trace

    # ------------------------------------------------------------ Phase 6D：模型顾问

    async def _maybe_advise(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        period: str,
        guards: dict[str, Any],
        extension: GuardVerdict,
        started_today: int,
        history: Sequence[ActivityEpisode],
    ) -> tuple[ActivityDecision | None, dict[str, Any] | None]:
        """在软空间里问一次顾问（§二十/§二十六）。返回 ``(被采纳的决策, 回执)``。

        ``决策 is None`` = **规则说了算**（顾问没装配 / 已经问过 / 失败 / 被规则拒）——
        调用方立刻走原样的规则路径，什么都不用管。
        """
        cycle_key = advisory_cycle_key(episode)
        receipt: dict[str, Any] = {
            "episode_id": episode.episode_id,
            "cycle_id": cycle_key,
            "attempted": False,
            "accepted": False,
            "fallback_used": False,
        }
        advisor = self.advisor
        if advisor is None or not getattr(advisor, "available", False):
            receipt["skipped_reason"] = "disabled"
            self.last_receipt = receipt
            return None, receipt
        # §六/§七/§三十八：一个 cycle 只问一次（内存 + 持久审计两侧都查）
        if advisor.has_attempted(cycle_key) or await self._advisory_attempted(episode, cycle_key):
            receipt["skipped_reason"] = "already_attempted"
            self.last_receipt = receipt
            return None, receipt
        receipt["provider"] = str(getattr(advisor, "provider_name", "") or "")
        receipt["model"] = str(getattr(advisor, "model", "") or "")
        context = self._advice_context(
            episode,
            now=now,
            period=period,
            extension=extension,
            started_today=started_today,
            history=history,
        )
        candidates = self._advice_candidates(
            episode, now=now, started_today=started_today, context=context
        )
        guards["model"] = {
            "attempted": True,
            "cycle": cycle_key,
            "extension_allowed": bool(extension.ok and self._extendable(episode)),
        }
        # 先记"问过"再问：超时/崩溃都不会让同一个 cycle 被问第二次（§七）
        advisor.mark_attempted(cycle_key)
        await self._advisory_note(episode, cycle_key)
        receipt["attempted"] = True
        try:
            proposal = await advisor.advise(context=context, candidates=candidates)
        except Exception as exc:  # noqa: BLE001 - 任何失败都只回退规则（§二十七/§三十九）
            code = str(getattr(exc, "code", "") or ModelFailureCode.PROVIDER_ERROR)
            receipt["failure"] = code
            # 6D.1 B：失败回执也必须带**实际**延迟 —— 顾问在 finally 里量过墙钟，
            # 所以 TIMEOUT / CONNECTION_ERROR / PROVIDER_ERROR / INVALID_JSON 都有值，
            # 绝不允许再出现 "TIMEOUT latency_ms=0"。
            receipt["latency_ms"] = int(getattr(advisor, "last_latency_ms", 0) or 0)
            receipt["fallback_used"] = True
            guards["model"].update({"failure": code, "fallback": True})
            self._log_advisory(episode, cycle_key, advisor, receipt, period=period)
            self.last_receipt = receipt
            return None, receipt
        receipt["latency_ms"] = int(getattr(advisor, "last_latency_ms", 0) or 0)
        receipt["proposal"] = proposal.to_payload()
        receipt["proposal_decision"] = proposal.decision
        receipt["proposal_extension_seconds"] = proposal.extension_seconds
        receipt["proposal_next_hint"] = proposal.next_hint or ""
        receipt["reason_code"] = proposal.reason_code
        receipt["explanation"] = proposal.state_explanation
        # §二十一-§二十四：规则**再验一遍** —— 模型只能落在软空间里，越权就整条拒绝
        rejection = self._reject_proposal(
            proposal,
            episode=episode,
            extension=extension,
            candidates=candidates,
            now=now,
            history=history,
        )
        if rejection:
            receipt["rejection_reason"] = rejection
            receipt["fallback_used"] = True
            guards["model"].update({"rejected": True, "reason": rejection, "fallback": True})
            self._log_advisory(episode, cycle_key, advisor, receipt, period=period)
            self.last_receipt = receipt
            return None, receipt
        if proposal.decision == "transition":
            # 6D.1 A：被采纳的模型提案也走**同一个候选出口** —— 于是"模型转移"与
            # "规则转移"在结构上共用一条缝，不可能有一个绕过护栏。
            # （`_reject_proposal` 已经验过一遍；这里是结构性的复核，不是第二套判断。）
            chosen, resolution = self._resolve_next_activity(
                episode,
                now=now,
                period=period,
                started_today=started_today,
                history=history,
                preferred=str(proposal.next_hint or ""),
            )
            guards["bounce"] = self._preferred_verdict(resolution)
            guards["bounce_resolution"] = resolution
            if not chosen:
                # 理论上到不了（上面已经验过）：真到了就整条拒绝 + 回退规则，绝不换到被拒的活动
                receipt["accepted"] = False
                receipt["rejection_reason"] = ModelFailureCode.RULE_REJECTED
                receipt["fallback_used"] = True
                guards["model"].update(
                    {
                        "accepted": False,
                        "rejected": True,
                        "reason": ModelFailureCode.RULE_REJECTED,
                        "fallback": True,
                    }
                )
                self._log_advisory(episode, cycle_key, advisor, receipt, period=period)
                self.last_receipt = receipt
                return None, receipt
        receipt["accepted"] = True
        guards["model"]["accepted"] = True
        self._log_advisory(episode, cycle_key, advisor, receipt, period=period)
        self.last_receipt = receipt
        return self._decision_from_proposal(proposal), receipt

    @staticmethod
    def _attach_receipt(trace: DecisionTrace, receipt: dict[str, Any] | None) -> None:
        """把回执盖到 trace 上（规则赢了也要看得见"模型说了什么、为什么没采纳"）。"""
        if not receipt:
            return
        trace.model_attempted = bool(receipt.get("attempted"))
        trace.model_provider = str(receipt.get("provider") or "")
        trace.model_latency_ms = int(receipt.get("latency_ms") or 0)
        trace.model_result = dict(receipt.get("proposal") or {})
        trace.model_reject_reason = str(receipt.get("rejection_reason") or "")
        trace.model_rejected = bool(trace.model_reject_reason)
        trace.fallback_used = bool(receipt.get("fallback_used"))

    def _advice_context(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        period: str,
        extension: GuardVerdict,
        started_today: int,
        history: Sequence[ActivityEpisode] = (),
    ) -> dict[str, Any]:
        """给顾问的只读 context（§八/§十九/§五十六/§五十七）—— 全是 bounded 的最小事实。"""
        state = self._state_fields()
        book = getattr(self.planner, "anchors", None)
        due_hard = [
            anchor.anchor_id
            for anchor in (book.all() if book is not None else ())
            if anchor.hard and anchor.is_due(self.clock, now)
        ]
        allowed = ["continue"]
        if bool(extension.ok and self._extendable(episode)):
            allowed.append("extend")
        allowed.append("transition")
        return {
            "current_activity": str(episode.activity_name or ""),
            "elapsed_minutes": int(max(0.0, episode.elapsed(now)) // 60),
            "planned_remaining_minutes": int(
                max(0.0, float(episode.planned_end_at or 0.0) - float(now)) // 60
            ),
            "time_period": period,
            "energy": float(state.get("energy") or 0.0),
            "focus": float(state.get("focus") or 0.0),
            "mood": str(state.get("mood") or ""),
            "hard_constraints": {
                "min_duration_satisfied": True,
                "max_duration_reached": False,
                "extension_count": int(episode.extension_count or 0),
                "max_extensions": int(self.guard.max_extensions),
                "extension_budget_seconds": round(
                    float(extension.detail.get("extension_seconds") or 0.0), 1
                ),
                "hard_anchor_due": due_hard,
            },
            "allowed_decisions": allowed,
            "routine_candidates": list(self._routine_for(period)),
            "goal_candidates": list(self._goal_names()),
            "recent_activities": [str(item.activity_name) for item in list(history)[-5:]],
            "memory_evidence": [],
            "minecraft": self._minecraft_context(),
            "started_today": int(started_today),
        }

    def _advice_candidates(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        started_today: int,
        context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """候选摘要（§二十五：生成/资格/排序都是 Planner 的活，顾问只拿到摘要）。"""
        plan = None
        try:
            plan = self.planner.preview_plan(
                episode=episode, now=now, clock=self.clock, started_today=started_today
            )
        except Exception:  # noqa: BLE001 - 拿不到候选就退回"没有候选"，顾问照样可以 continue
            plan = None
        if plan is not None:
            context["future_plan"] = [
                {
                    "activity": item.activity,
                    "planned_start_at": float(item.planned_start),
                    "planned_end_at": float(item.planned_end),
                    "reason": item.reason,
                }
                for item in plan.items[:6]
            ]
        return [
            {
                "activity": candidate.activity,
                "eligible": bool(candidate.eligible),
                "reason": str(candidate.reason or ""),
                "score": round(float(candidate.score), 3),
            }
            for candidate in (plan.candidates if plan is not None else ())
        ]

    def _reject_proposal(
        self,
        proposal: ActivityDecisionProposal,
        *,
        episode: ActivityEpisode,
        extension: GuardVerdict,
        candidates: list[dict[str, Any]],
        now: float,
        history: Sequence[ActivityEpisode] = (),
    ) -> str:
        """§二十一-§二十四：规则对模型提案的**再验证**。返回非空 = 拒绝原因。"""
        if proposal.decision == "continue":
            return ""
        if proposal.decision == "extend":
            if not (extension.ok and self._extendable(episode)):
                return ModelFailureCode.RULE_REJECTED
            budget = float(extension.detail.get("extension_seconds") or 0.0)
            if budget <= 0.0 or proposal.extension_seconds > budget:
                # §二十一：超出剩余延长额度 → **拒绝**（不偷偷 clamp，§三十一）
                return ModelFailureCode.RULE_REJECTED
            # §二十二：不得让延长跨过正在窗口里的**硬**锚点
            book = getattr(self.planner, "anchors", None)
            if book is not None:
                end = float(episode.planned_end_at or now) + proposal.extension_seconds
                for anchor in book.all():
                    if not anchor.hard or anchor.activity == episode.activity_name:
                        continue
                    if not (anchor.is_due(self.clock, now) or anchor.upcoming(self.clock, now)):
                        continue
                    # §二十二：延长不得**覆盖**锚点窗口（模型比规则更严 —— 只可能更安全）
                    if float(anchor.window(self.clock, now)[0]) <= end:
                        return ModelFailureCode.RULE_REJECTED
            return ""
        hint = str(proposal.next_hint or "")
        if not hint or hint == str(episode.activity_name):
            return ModelFailureCode.RULE_REJECTED
        eligible = {str(item.get("activity")): bool(item.get("eligible")) for item in candidates}
        if hint not in eligible:
            # 候选表里根本没有它 → 与"不合格"同样处理（§二十四/§五十一）
            return ModelFailureCode.RULE_REJECTED
        if not eligible.get(hint):
            return ModelFailureCode.RULE_REJECTED
        verdict = self.bounce.check(current=episode, candidate=hint, history=list(history), now=now)
        if not verdict.ok:
            # §二十三：撞车护栏是模型越不过去的
            return ModelFailureCode.RULE_REJECTED
        return ""

    @staticmethod
    def _decision_from_proposal(proposal: ActivityDecisionProposal) -> ActivityDecision:
        """把被采纳的提案翻成既有决策形状（原因码仍然走规则词表，来源在 trace 的 model_* 里）。

        ``confidence`` 刻意低于硬规则（0.6）：它是**启发式**采纳，不是规则裁决（§二三）。
        """
        if proposal.decision == "extend":
            return ActivityDecision(
                ActivityDecisionKind.EXTEND,
                DecisionReason.TRANSITION_WINDOW,
                extension_seconds=proposal.extension_seconds,
                confidence=0.6,
            )
        if proposal.decision == "transition":
            return ActivityDecision(
                ActivityDecisionKind.TRANSITION,
                DecisionReason.TRANSITION_WINDOW,
                next_activity_hint=str(proposal.next_hint or ""),
                confidence=0.6,
            )
        return ActivityDecision(
            ActivityDecisionKind.CONTINUE, DecisionReason.TRANSITION_WINDOW, confidence=0.6
        )

    async def _advisory_attempted(self, episode: ActivityEpisode, cycle_key: str) -> bool:
        """持久侧的 invocation guard（§三十八：重启也不许对同一个 cycle 再问一次）。"""
        probe = self.advisory_probe
        if probe is None:
            return False
        try:
            return bool(await probe(episode, cycle_key))
        except Exception:  # noqa: BLE001 - 探针坏了就只靠内存（绝不阻塞决策）
            return False

    async def _advisory_note(self, episode: ActivityEpisode, cycle_key: str) -> None:
        note = self.advisory_note
        if note is None:
            return
        try:
            await note(episode, cycle_key)
        except Exception:  # noqa: BLE001 - 审计写失败只降级
            if self._log is not None:
                self._log.debug("[World.Activity] 顾问审计落盘失败（忽略）", exc_info=True)

    def _log_advisory(
        self,
        episode: ActivityEpisode,
        cycle_key: str,
        advisor: Any,
        receipt: dict[str, Any],
        *,
        period: str,
    ) -> None:
        """§七十一：INFO 一行够用 —— **绝不**打印 prompt 或完整模型输出。

        6D.1 B：统一成 ``attempted= / latency_ms= / result= / fallback=`` 四件套 ——
        失败路径（TIMEOUT / CONNECTION_ERROR / PROVIDER_ERROR / INVALID_JSON）也一定带**实际**延迟，
        不再出现 "TIMEOUT latency_ms=0"。
        """
        if self._log is None:
            return
        proposal = receipt.get("proposal") or {}
        self._log.info(
            "[Activity.Model] episode=%s cycle=%s provider=%s model=%s attempted=%s latency_ms=%d"
            " result=%s accepted=%s fallback=%s reason=%s period=%s",
            episode.episode_id,
            cycle_key,
            str(receipt.get("provider") or ""),
            str(receipt.get("model") or ""),
            bool(receipt.get("attempted")),
            int(receipt.get("latency_ms") or 0),
            str(proposal.get("decision") or receipt.get("failure") or ""),
            bool(receipt.get("accepted")),
            bool(receipt.get("fallback_used")),
            str(receipt.get("rejection_reason") or receipt.get("failure") or ""),
            period,
        )

    # ---- 顾问用到的只读输入（拿不到就返回安全默认值，绝不让顾问拖垮决策） ----

    def _state_fields(self) -> dict[str, Any]:
        provider = getattr(self.planner, "state_provider", None)
        state = None
        if provider is not None:
            try:
                state = provider()
            except Exception:  # noqa: BLE001
                state = None
        if state is None:
            return {}

        def get(key: str, default: Any = None) -> Any:
            if isinstance(state, dict):
                return state.get(key, default)
            return getattr(state, key, default)

        energy = get("energy", 0.8) or 0.0
        focus_raw = get("current_focus", "")
        try:
            focus = float(focus_raw) if focus_raw not in ("", None) else 0.5
        except (TypeError, ValueError):
            focus = 0.5
        return {
            "energy": float(energy),
            "focus": max(0.0, min(1.0, float(focus))),
            "mood": str(get("mood", "") or ""),
        }

    def _routine_for(self, period: str) -> tuple[str, ...]:
        table = getattr(self.planner, "routine_table", {}) or {}
        return tuple(table.get(period) or ())

    def _goal_names(self) -> tuple[str, ...]:
        source = getattr(self.planner, "goal_source", None)
        if source is None:
            return ()
        try:
            snapshot = source.snapshot()
        except Exception:  # noqa: BLE001
            return ()
        return tuple(goal.title for goal in snapshot.open_goals[:3])

    def _minecraft_context(self) -> dict[str, Any]:
        """§十：只给**观察事实**（在线 / 当前任务 / 状态），绝不给工具 schema 或执行接口。"""
        provider = self.minecraft_context
        if provider is None:
            return {}
        try:
            payload = provider()
        except Exception:  # noqa: BLE001 - 观察拿不到就当没有
            return {}
        return dict(payload) if isinstance(payload, dict) else {}

    # ------------------------------------------------------------ 内部

    async def _transition(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        trigger: DecisionTrigger,
        period: str,
        guards: dict[str, Any],
        reason: DecisionReason,
        started_today: int,
        history: Sequence[ActivityEpisode],
        force_change: bool = False,
    ) -> tuple[ActivityDecision, DecisionTrace]:
        hint = await self._next_hint(
            episode,
            now=now,
            started_today=started_today,
            period=period,
            force_change=force_change,
        )
        if not hint:
            # §十九 相邻同活动合并：Planner 说"还是同一件事" → 与其结束再开一个一样的，
            # 不如 EXTEND（能延长就延长）。
            extension = self.guard.extension(
                episode, now=now, extension_seconds=float(episode.typical_duration or 0.0)
            )
            guards["merge"] = {
                "same_activity": True,
                "extension_ok": bool(extension.ok),
            }
            # 走到这里说明**不能再延长**（能延长的话第 9 步就已经 EXTEND 了 —— 那才是
            # §十九 的"优先延长"）；所以这个活动确实结束了：必须真的换一个，
            # 既不能同名重开（§十九），也不能一直 CONTINUE（那会让 Episode 永远不结束）。
            hint = await self._next_hint(
                episode,
                now=now,
                started_today=started_today,
                period=period,
                force_change=True,
            )
        # 6D.1 A：**唯一**产生 next activity 的出口 —— 候选 → 撞车护栏 → 接受/拒绝。
        # 首选候选（Planner 的提示）与"中性/兜底"候选走的是**同一个**出口，
        # 谁都不能绕过护栏（旧版本的中性兜底是直接返回的，那是唯一的旁路）。
        chosen, resolution = self._resolve_next_activity(
            episode,
            now=now,
            period=period,
            started_today=started_today,
            history=history,
            preferred=hint,
        )
        guards["bounce"] = self._preferred_verdict(resolution)
        guards["bounce_resolution"] = resolution
        if chosen:
            return self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.TRANSITION,
                    self._transition_reason(reason, resolution),
                    next_activity_hint=chosen,
                ),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
                transition_pending=True,
            )
        # 没有一个合法候选（或"下一个"就是当前这件事）：优先延长，否则继续当前。
        # **绝不**换到一个被护栏拒绝的活动 —— 这是 6D.1 A 的核心不变量。
        extension = self.guard.extension(
            episode, now=now, extension_seconds=float(episode.typical_duration or 0.0)
        )
        guards["bounce_extension"] = {
            "ok": bool(extension.ok),
            "extendable": self._extendable(episode),
            "same_as_current": bool(resolution.get("same_as_current")),
        }
        if extension.ok and self._extendable(episode):
            seconds = float(extension.detail.get("extension_seconds") or 0.0)
            return self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.EXTEND,
                    DecisionReason.BOUNCE_GUARD,
                    extension_seconds=seconds,
                    confidence=0.9,
                ),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        return self._finish(
            episode,
            ActivityDecision(
                ActivityDecisionKind.CONTINUE,
                DecisionReason.BOUNCE_GUARD,
                confidence=0.9,
            ),
            now=now,
            trigger=trigger,
            period=period,
            guards=guards,
        )

    async def _next_hint(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        started_today: int,
        period: str,
        force_change: bool = False,
    ) -> str:
        """下一个活动的**提示**（确定性）：Planner 说不了话就按 §三十/§三十一 兜底。

        ``force_change=True`` 是**硬规则**场景（例如到 max_duration 必须换）：这时 Planner
        说"还是同一件事"也不能接受 —— 按它自己的时段表取下一档，再不行用中性活动。
        """
        current = str(episode.activity_name).strip().lower()
        try:
            decision = self.planner.next_after(
                episode, now=now, clock=self.clock, started_today=started_today
            )
            name = str(getattr(decision, "activity_name", "") or "").strip()
            if name and name.lower() != current:
                return name
            if force_change:
                # Planner 的时段表是确定性旋转：把"今天已排数量"推一格就是"下一档"
                rotated = self.planner.initial(
                    now=now, clock=self.clock, started_today=int(started_today) + 1
                )
                candidate = str(getattr(rotated, "activity_name", "") or "").strip()
                if candidate and candidate.lower() != current:
                    return candidate
            if name and not force_change:
                # 相邻同名（§十九）：与其"结束再开一个一样的"，不如交回调用方去 EXTEND
                return ""
        except Exception:  # noqa: BLE001 - §三十/§三十一：Planner 失败绝不让 activity 变空
            if self._log is not None:
                self._log.warning("[World.Activity] Planner 失败，改用兜底活动（§三十一）")
        fallback = self.bounce.neutral_fallback(period=period)
        if str(fallback).lower() == current:  # 兜底跟当前同名 → 换一个确定性兜底
            for candidate in FALLBACK_ACTIVITIES:
                if str(candidate).lower() != current:
                    return candidate
        return fallback if fallback else FALLBACK_ACTIVITIES[0]

    # ------------------------------------------------------------ 6D.1 A：候选出口

    def _resolve_next_activity(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        period: str,
        started_today: int,
        history: Sequence[ActivityEpisode] = (),
        preferred: str = "",
    ) -> tuple[str, dict[str, Any]]:
        """**唯一**产生"下一个活动"的出口（6D.1 A）：候选 → 撞车护栏 → 接受 / 拒绝。

        候选顺序是**确定性**的：首选候选（Planner / 模型给的提示）→ 时段中性活动 →
        兜底活动（§三十一）→ Planner 的**合格**候选（懒加载：只有前面全被拒才去问它）。

        返回值 ``(activity, audit)``：

        * ``activity != ""`` = 通过护栏的合法候选（调用方按传入的 reason 换过去）；
        * ``activity == ""`` = 没有合法候选，或者"下一个"就是当前这件事
          （``audit["same_as_current"]``）—— 调用方负责 EXTEND / CONTINUE，
          **绝不**换到一个被护栏拒绝的活动。

        于是任何路径（普通转移 / Planner 转移 / 模型转移 / 回退转移 / 中性兜底）都只能
        落到"过了护栏的候选"上，没有旁路。
        """
        current = str(episode.activity_name).strip().lower()
        audit: dict[str, Any] = {
            "policy": BOUNCE_RESOLUTION_POLICY,
            "current": current,
            "preferred": str(preferred or ""),
            "cooldown_seconds": float(self.bounce.cooldown_seconds),
            "checked": [],
            "chosen": "",
            "accepted_candidate": "",
            "source": "",
            "same_as_current": False,
            "no_legal_candidate": False,
        }

        def consider(name: str, source: str) -> tuple[str, bool]:
            """过一个候选：返回 (activity, 是否已经定下来)。"""
            verdict = self.bounce.check(
                current=episode, candidate=name, history=list(history), now=now
            )
            same = name.lower() == current
            audit["checked"].append(
                {
                    "activity": name,
                    "source": source,
                    "ok": bool(verdict.ok),
                    "code": verdict.code.value,
                    "same_as_current": same,
                    "detail": dict(verdict.detail),
                }
            )
            if not verdict.ok:
                return "", False
            if same:
                # §十九：Planner/模型说的"下一个"就是当前这件事 —— 没有"下一个活动"，
                # 交回调用方去 EXTEND（而不是"结束再开一个一样的"）。
                # ``chosen`` 留空（**不会**开新 Episode），名字记在 ``accepted_candidate``。
                audit["chosen"] = ""
                audit["accepted_candidate"] = name
                audit["source"] = source
                audit["same_as_current"] = True
                return "", True
            audit["chosen"] = name
            audit["source"] = source
            return name, True

        order: list[tuple[str, str]] = []
        if str(preferred or "").strip():
            order.append((str(preferred).strip(), "preferred"))
        order.extend(self._static_candidate_pool(period=period))
        for name, source in order:
            resolved, done = consider(name, source)
            if done:
                return resolved, audit
        for name in self._planner_eligible_candidates(
            episode, now=now, started_today=started_today
        ):
            resolved, done = consider(name, "planner")
            if done:
                return resolved, audit
        audit["no_legal_candidate"] = True
        return "", audit

    def _static_candidate_pool(self, *, period: str) -> list[tuple[str, str]]:
        """时段中性活动 + 兜底活动（§十八/§三十一）—— 顺序**就是**优先级，确定性。

        刻意不用计划资格（``eligible``）过滤它们：中性/兜底是撞车后的**安全网**
        （6B 的老行为就是这样），而"能不能当计划目标"是 Planner 的判断（§二十五）——
        Planner 的候选在下一档，那里才按 ``eligible`` 过滤。
        """
        pool: list[tuple[str, str]] = []

        def push(name: Any, source: str) -> None:
            text = str(name or "").strip()
            if not text:
                return
            if any(text.lower() == item[0].lower() for item in pool):
                return
            pool.append((text, source))

        push(self.bounce.neutral_fallback(period=period), "neutral")
        for name in FALLBACK_ACTIVITIES:
            push(name, "fallback")
        return pool

    def _planner_eligible_candidates(
        self, episode: ActivityEpisode, *, now: float, started_today: int
    ) -> list[str]:
        """Planner 认为**合格**的候选（§二十五）—— 生成/资格/排序都是它的活。

        只读预览（``preview_plan`` 不落盘、无副作用）；拿不到就返回空表 ——
        前面两档（中性 / 兜底）照样管用，绝不让候选池的问题影响决策主流程。
        """
        try:
            plan = self.planner.preview_plan(
                episode=episode, now=now, clock=self.clock, started_today=started_today
            )
        except Exception:  # noqa: BLE001 - 候选拿不到就当没有（中性/兜底是兜底的兜底）
            if self._log is not None:
                self._log.debug("[World.Activity] 候选池拿不到计划（忽略）", exc_info=True)
            return []
        return [
            str(getattr(item, "activity", "") or "")
            for item in (getattr(plan, "candidates", ()) or ())
            if bool(getattr(item, "eligible", False))
        ]

    @staticmethod
    def _preferred_verdict(resolution: Mapping[str, Any]) -> dict[str, Any]:
        """``guards["bounce"]`` 仍只报**首选候选**的护栏结论（审计形状向后兼容）。"""
        checked = list(resolution.get("checked") or [])
        for item in checked:
            if str(item.get("source") or "") == "preferred":
                return {
                    "ok": bool(item.get("ok")),
                    "code": str(item.get("code") or DecisionReason.BOUNCE_GUARD.value),
                    "detail": dict(item.get("detail") or {}),
                }
        first = checked[0] if checked else {}
        return {
            "ok": bool(first.get("ok")),
            "code": str(first.get("code") or DecisionReason.BOUNCE_GUARD.value),
            "detail": dict(first.get("detail") or {}),
        }

    @staticmethod
    def _transition_reason(reason: DecisionReason, resolution: Mapping[str, Any]) -> DecisionReason:
        """最终原因码：首选候选过了护栏 → 用传入的原因；否则如实记 BOUNCE_GUARD。

        ``MAX_DURATION`` 例外：到硬上限必须换是 §十五 的硬规则（runtime 靠它判 EXPIRED），
        所以即使首选被护栏挡住、改换了别的合法活动，原因码仍然是 MAX_DURATION。
        """
        if reason is DecisionReason.MAX_DURATION:
            return reason
        if str(resolution.get("source") or "") == "preferred":
            return reason
        return DecisionReason.BOUNCE_GUARD

    @staticmethod
    def _extendable(episode: ActivityEpisode) -> bool:
        """只能延长"虚拟日常"里的可续活动；任务型活动的生命周期归任务（§十八/§十九）。"""
        if looks_like_minecraft_activity(episode.activity_name):
            return False
        return episode.source.value in {"ROUTINE", "RECOVERY"}

    def _period(self, now: float) -> str:
        try:
            return str(self.clock.period(now))
        except Exception:  # noqa: BLE001 - 时钟不认识就问不出时段，退回白天
            return "afternoon"

    def _finish(
        self,
        episode: ActivityEpisode,
        decision: ActivityDecision,
        *,
        now: float,
        trigger: DecisionTrigger,
        period: str,
        guards: dict[str, Any],
        transition_pending: bool = False,
        receipt: dict[str, Any] | None = None,
    ) -> tuple[ActivityDecision, DecisionTrace]:
        trace = DecisionTrace(
            trace_id=f"dec_{uuid.uuid4().hex[:12]}",
            episode_id=episode.episode_id,
            character_id=episode.character_id,
            current_activity=episode.activity_name,
            trigger=trigger.value,
            decision=decision.decision.value,
            reason_code=decision.reason_code.value,
            elapsed=episode.elapsed(now),
            planned_end_at=float(episode.planned_end_at or 0.0),
            min_duration=float(episode.min_duration or 0.0),
            typical_duration=float(episode.typical_duration or 0.0),
            max_duration=float(episode.max_duration or 0.0),
            extension_count=int(episode.extension_count or 0),
            time_period=period,
            transition_pending=transition_pending,
            next_activity_hint=decision.next_activity_hint,
            extension_seconds=float(decision.extension_seconds),
            guard_results=dict(guards),
            decided_at=float(now),
            model_attempted=bool((receipt or {}).get("attempted")),
            model_provider=str((receipt or {}).get("provider") or ""),
            model_latency_ms=int((receipt or {}).get("latency_ms") or 0),
            model_result=dict((receipt or {}).get("proposal") or {}),
            model_rejected=bool((receipt or {}).get("rejection_reason")),
            model_reject_reason=str((receipt or {}).get("rejection_reason") or ""),
            fallback_used=bool((receipt or {}).get("fallback_used")),
        )
        decided = ActivityDecision(
            decision.decision,
            decision.reason_code,
            decision.next_activity_hint,
            decision.extension_seconds,
            decision.confidence,
            trace.trace_id,
        )
        self.traces.append(trace)
        if len(self.traces) > self.max_traces:
            del self.traces[: len(self.traces) - self.max_traces]
        if self._log is not None:
            signature = (
                episode.episode_id,
                decided.decision.value,
                decided.reason_code.value,
                bool(trace.transition_pending),
            )
            message = (
                "[World.Activity] episode=%s decision=%s reason=%s trigger=%s"
                " elapsed=%ds planned_end=%s pending=%s hint=%s"
            )
            args = (
                episode.episode_id,
                decided.decision.value,
                decided.reason_code.value,
                trigger.value,
                int(trace.elapsed),
                int(trace.planned_end_at),
                trace.transition_pending,
                decided.next_activity_hint or "-",
            )
            # 平凡 CONTINUE **只在状态变化时**记 INFO，其余降为 DEBUG：
            # 一秒一条 INFO 会把日志淹掉（真机上就是这个现象），而"决策本身"在 trace 里查得到。
            if (
                signature != self._log_signature
                or decided.decision is not ActivityDecisionKind.CONTINUE
            ):
                self._log.info(message, *args)
            else:
                self._log.debug(message, *args)
            self._log_signature = signature
        return decided, trace


def decision_reason_for_transition(reason: DecisionReason) -> TransitionReason:
    """决策原因 → Episode 转移原因（两套词表在这里对齐，别在别处再解释一次）。"""
    if reason is DecisionReason.TASK_STARTED:
        return TransitionReason.TASK_STARTED
    if reason is DecisionReason.TASK_COMPLETED:
        return TransitionReason.TASK_COMPLETED
    if reason is DecisionReason.TASK_FAILED:
        return TransitionReason.TASK_FAILED
    if reason is DecisionReason.USER_INTERACTION:
        return TransitionReason.USER_INTERACTION
    if reason is DecisionReason.WORLD_EVENT:
        return TransitionReason.WORLD_EVENT
    if reason is DecisionReason.RECOVERY:
        return TransitionReason.RECOVERY
    if reason in {
        DecisionReason.MAX_DURATION,
        DecisionReason.BEFORE_END,
        DecisionReason.TRANSITION_WINDOW,
        DecisionReason.BOUNCE_GUARD,
    }:
        return TransitionReason.TIME_EXPIRED
    return TransitionReason.MANUAL


def is_mergeable(current: ActivityEpisode, candidate: str) -> bool:
    """§十九：候选与当前是"同一件事" → 该 EXTEND 而不是结束再开一个。

    同名的相邻活动只有在**没有真实中断**时才算可合并 —— 真实中断（任务抢占、
    用户打断）一定换了来源或活动名，所以这里只看"名字相同 + 没有真实中断标记"。
    """
    name = str(candidate or "").strip()
    if not name:
        return False
    return name.lower() == str(current.activity_name).strip().lower()


def merge_adjacent(episodes: Sequence[ActivityEpisode], *, limit: int = 10) -> list[dict[str, Any]]:
    """展示层合并（§十九）：相邻同名 Episode 合成一段，**绝不丢原始审计**。

    返回每段：``activity`` / ``episode_ids``（原始 id 全部保留）/ ``started_at`` /
    ``ended_at`` / ``merged`` / ``status``。API 与 WebUI 用它做"看起来连续"的时间线，
    数据库里的行一条都不动。
    """
    merged: list[dict[str, Any]] = []
    for episode in episodes:
        if merged and str(merged[-1]["activity"]).lower() == str(episode.activity_name).lower():
            segment = merged[-1]
            segment["episode_ids"].append(episode.episode_id)
            segment["ended_at"] = float(
                episode.ended_at or episode.updated_at or segment["ended_at"]
            )
            segment["status"] = episode.status.value
            segment["merged"] = int(segment["merged"]) + 1
            continue
        merged.append(
            {
                "activity": episode.activity_name,
                "episode_ids": [episode.episode_id],
                "started_at": float(episode.started_at or episode.created_at or 0.0),
                "ended_at": float(episode.ended_at or episode.updated_at or 0.0),
                "status": episode.status.value,
                "source": episode.source.value,
                "merged": 1,
            }
        )
    return merged[: max(1, int(limit))]


def as_trigger(value: DecisionTrigger | str) -> DecisionTrigger:
    """宽松地把字符串/枚举转成触发器（认不出来就按 MANUAL，绝不猜 random）。"""
    if isinstance(value, DecisionTrigger):
        return value
    try:
        return DecisionTrigger(str(value))
    except ValueError:
        return DecisionTrigger.MANUAL


def guard_payload(guards: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): value for key, value in guards.items()}
