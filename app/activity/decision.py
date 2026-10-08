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
        #: v1.0 §22 的"模型那一半"接口：**6B 永远是 None**（§三 硬约束）
        self.advisor = advisor
        self._log = logger
        #: 最近几次决策的 trace（内存里、只读展示；持久事实在 Episode 行上）
        self.traces: list[DecisionTrace] = []
        self.max_traces = 32

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
            hint = await self._next_hint(
                episode, now=now, started_today=started_today, period=period
            )
            return self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.TRANSITION,
                    REASON_BY_TRIGGER.get(trigger, DecisionReason.USER_INTERACTION),
                    next_activity_hint=hint,
                ),
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
        if allowed.ok and self._extendable(episode):
            seconds = float(allowed.detail.get("extension_seconds") or 0.0)
            return self._finish(
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
        # 10. 换活动（过撞车护栏）
        return await self._transition(
            episode,
            now=now,
            trigger=trigger,
            period=period,
            guards=guards,
            reason=DecisionReason.TRANSITION_WINDOW,
            started_today=started_today,
            history=history,
        )

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
        verdict = self.bounce.check(current=episode, candidate=hint, history=history, now=now)
        guards["bounce"] = verdict.to_payload()
        if not verdict.ok:
            # §十八：拒绝之后要么继续当前（能延长就延长），要么换一个**中性**活动
            extension = self.guard.extension(
                episode, now=now, extension_seconds=float(episode.typical_duration or 0.0)
            )
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
            neutral = self.bounce.neutral_fallback(period=period)
            if str(neutral).lower() == str(episode.activity_name).lower():
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
            return self._finish(
                episode,
                ActivityDecision(
                    ActivityDecisionKind.TRANSITION,
                    DecisionReason.BOUNCE_GUARD,
                    next_activity_hint=neutral,
                    confidence=0.9,
                ),
                now=now,
                trigger=trigger,
                period=period,
                guards=guards,
            )
        return self._finish(
            episode,
            ActivityDecision(ActivityDecisionKind.TRANSITION, reason, next_activity_hint=hint),
            now=now,
            trigger=trigger,
            period=period,
            guards=guards,
            transition_pending=True,
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
            self._log.info(
                "[World.Activity] episode=%s decision=%s reason=%s trigger=%s"
                " elapsed=%ds planned_end=%s pending=%s hint=%s",
                episode.episode_id,
                decided.decision.value,
                decided.reason_code.value,
                trigger.value,
                int(trace.elapsed),
                int(trace.planned_end_at),
                trace.transition_pending,
                decided.next_activity_hint or "-",
            )
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
