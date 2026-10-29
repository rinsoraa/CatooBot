"""Phase 6C §十二-§十五：日程锚点（ScheduleAnchor）。

锚点 = "她的一天里大致要发生的事"（三餐、睡觉、固定事件）。它是 **Planner 的输入**，
不是状态机、不是 Episode、**不**创建任何活动：

    锚点 → Planner 打分 → ActivityPlan → （6B 允许时）ActivityEpisode

§十四 说得很清楚：硬锚点**不**等于"12:00:00 强制切活动"。它只做两件事：

1. 进窗口时把"锚点要的那件事"顶到候选前面（打分）；
2. 窗口本身（``window_before``/``window_after``）是弹性的 —— 到底换不换，仍由
   6B 的 transition guard 说了算（最短时长、延长预算、撞车护栏一个都不绕过）。

所有时间判断都基于**本地钟面**（``clock.local_minute``/``day_start``），
这样"午饭 12:00"在配置时区里就是 12:00，跟机器时区无关（§二十六 复用 WorldClock）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

#: 锚点优先级（§十三：从高到低）。用字符串值，方便直接写进审计/JSON。
ANCHOR_PRIORITY_ORDER: tuple[str, ...] = (
    "sleep",
    "meal",
    "fixed_event",
    "routine_activity",
    "free_activity",
)


class AnchorPriority(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """§十三 的五档优先级（高 → 低）。"""

    SLEEP = "sleep"
    MEAL = "meal"
    FIXED_EVENT = "fixed_event"
    ROUTINE_ACTIVITY = "routine_activity"
    FREE_ACTIVITY = "free_activity"

    @property
    def rank(self) -> int:
        """越小越强（tie-break 用它，§三十七）。"""
        return ANCHOR_PRIORITY_ORDER.index(self.value)

    @property
    def weight(self) -> float:
        """给打分用的权重：最强 1.0，最弱 0.2（确定性线性，不做玄学）。"""
        return 1.0 - 0.2 * self.rank


class AnchorPhase(str, Enum):  # noqa: UP042
    """锚点相对"现在"处在哪一段（只用于解释与打分，不参与状态机）。"""

    BEFORE = "before"  # 还没进窗口
    DUE = "due"  # 正在窗口里
    MISSED = "missed"  # 窗口已经过去（今天这次没赶上）
    DONE = "done"  # 已经过去很久（今天不再考虑）


def _parse_hhmm(text: str) -> int:
    """``"12:00"`` → 720（本地钟面分钟）。格式不对直接报错，绝不猜。"""
    raw = str(text or "").strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ValueError(f"锚点时间必须是 HH:MM：{text!r}")
    hour, minute = parts
    if not (hour.isdigit() and minute.isdigit()):
        raise ValueError(f"锚点时间必须是 HH:MM：{text!r}")
    if not (0 <= int(hour) <= 23 and 0 <= int(minute) <= 59):
        raise ValueError(f"锚点时间超出范围：{text!r}")
    return int(hour) * 60 + int(minute)


@dataclass(frozen=True)
class ScheduleAnchor:
    """一个日程锚点（§十二）。

    ``target_time``
        本地钟面 ``"HH:MM"``（午饭就是 ``"12:00"``）。
    ``window_before`` / ``window_after``
        弹性窗口（秒）。窗口内它才算"该发生了"。
    ``priority`` / ``hard``
        §十三/§十四：睡觉 > 三餐 > 固定事件 > 例行活动 > 自由活动；``hard`` 的是
        睡觉、三餐、必须出席的事 —— 它们只是**权重最高**，绝不强制切活动。
    ``days``
        生效的星期（0=周一 … 6=周日）；``None`` = 每天。
    """

    anchor_id: str
    activity: str
    target_time: str
    window_before: float = 45 * 60.0
    window_after: float = 45 * 60.0
    priority: AnchorPriority = AnchorPriority.MEAL
    hard: bool = False
    days: frozenset[int] | None = None
    #: 给审计/WebUI 的解释（"这是午饭"）
    note: str = ""

    def __post_init__(self) -> None:
        _parse_hhmm(self.target_time)  # 构造即校验（早失败好过半夜才发现）
        if not str(self.anchor_id or "").strip():
            raise ValueError("锚点必须有 anchor_id")
        if not str(self.activity or "").strip():
            raise ValueError("锚点必须有 activity")

    # ------------------------------------------------------------ 时间

    @property
    def target_minute(self) -> int:
        return _parse_hhmm(self.target_time)

    def active_on(self, clock: Any, at: float) -> bool:
        """这个锚点今天生效吗（``days=None`` 就是每天）。"""
        if self.days is None:
            return True
        return int(clock.local_weekday(at)) in self.days

    def window(self, clock: Any, at: float) -> tuple[float, float]:
        """今天这个锚点的窗口 ``(start, end)``（绝对时间戳）。"""
        base = float(clock.day_start(at)) + self.target_minute * 60.0
        return (base - float(self.window_before), base + float(self.window_after))

    def target_at(self, clock: Any, at: float) -> float:
        """今天这个锚点的目标时刻（绝对时间戳）。"""
        return float(clock.day_start(at)) + self.target_minute * 60.0

    def phase(self, clock: Any, at: float) -> AnchorPhase:
        """相对"现在"处在哪一段（确定性，只看分钟差）。"""
        if not self.active_on(clock, at):
            return AnchorPhase.DONE
        start, end = self.window(clock, at)
        moment = float(at)
        if moment < start:
            return AnchorPhase.BEFORE
        if moment <= end:
            return AnchorPhase.DUE
        # 窗口刚过去一小时之内算"今天这次没赶上"，更久就当今天不会再提它
        if moment <= end + 3600.0:
            return AnchorPhase.MISSED
        return AnchorPhase.DONE

    def is_due(self, clock: Any, at: float) -> bool:
        return self.phase(clock, at) is AnchorPhase.DUE

    def upcoming(self, clock: Any, at: float) -> bool:
        """今天还没到、而且今天会到（Planner 用它提前排）。"""
        return self.phase(clock, at) is AnchorPhase.BEFORE

    def seconds_until(self, clock: Any, at: float) -> float:
        """还有多少秒到目标时刻（已经过了就是 0）。"""
        return max(0.0, self.target_at(clock, at) - float(at))

    def fit(self, clock: Any, at: float) -> float:
        """锚点契合度 0~1（进打分）。

        * 窗口内 = 1.0（越接近目标时刻越是 1.0 —— 窗口内一律给满，避免"分钟级抖动"）；
        * 窗口外 = 线性衰减：离窗口还有半个窗口宽就衰减到 0。

        只读 ``clock``，无随机、无状态。
        """
        phase = self.phase(clock, at)
        if phase is AnchorPhase.DUE:
            return 1.0
        if phase is AnchorPhase.BEFORE:
            distance = max(0.0, self.window(clock, at)[0] - float(at))
            span = max(float(self.window_before), 60.0)
            return max(0.0, 1.0 - distance / (2.0 * span))
        if phase is AnchorPhase.MISSED:
            # 刚错过：还留一点"补上"的意愿，但比正常低
            return 0.35
        return 0.0

    def to_payload(self, clock: Any = None, at: float | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "anchor_id": self.anchor_id,
            "activity": self.activity,
            "target_time": self.target_time,
            "window_before": float(self.window_before),
            "window_after": float(self.window_after),
            "priority": self.priority.value,
            "hard": bool(self.hard),
            "days": sorted(self.days) if self.days is not None else None,
            "note": self.note,
        }
        if clock is not None and at is not None:
            start, end = self.window(clock, at)
            payload["phase"] = self.phase(clock, at).value
            payload["fit"] = float(self.fit(clock, at))
            payload["window_start"] = start
            payload["window_end"] = end
            payload["target_at"] = self.target_at(clock, at)
        return payload


#: 默认的一天（§十二/§十四）：睡觉 + 三餐是硬锚点，其余是弹性的。
#: **只影响排序与窗口**，绝不强制在整点切活动（§十四）。
DEFAULT_ANCHORS: tuple[ScheduleAnchor, ...] = (
    ScheduleAnchor(
        anchor_id="sleep",
        activity="sleeping",
        target_time="23:00",
        window_before=60 * 60.0,
        window_after=90 * 60.0,
        priority=AnchorPriority.SLEEP,
        hard=True,
        note="睡觉（硬）",
    ),
    ScheduleAnchor(
        anchor_id="breakfast",
        activity="eating",
        target_time="08:00",
        priority=AnchorPriority.MEAL,
        hard=True,
        note="早饭（硬）",
    ),
    ScheduleAnchor(
        anchor_id="lunch",
        activity="eating",
        target_time="12:00",
        priority=AnchorPriority.MEAL,
        hard=True,
        note="午饭（硬，±45 分钟窗口）",
    ),
    ScheduleAnchor(
        anchor_id="dinner",
        activity="eating",
        target_time="18:30",
        priority=AnchorPriority.MEAL,
        hard=True,
        note="晚饭（硬）",
    ),
)


@dataclass
class AnchorBook:
    """一批锚点的只读查询入口（排序稳定，绝不依赖 dict 顺序）。"""

    anchors: tuple[ScheduleAnchor, ...] = field(default_factory=lambda: DEFAULT_ANCHORS)

    def __post_init__(self) -> None:
        ids = [anchor.anchor_id for anchor in self.anchors]
        if len(set(ids)) != len(ids):
            raise ValueError("锚点 id 不能重复")
        # 稳定排序：优先级 → 钟面时刻 → id（确定性，tie-break 与展示都靠它）
        self.anchors = tuple(
            sorted(
                self.anchors,
                key=lambda item: (item.priority.rank, item.target_minute, item.anchor_id),
            )
        )

    def all(self) -> tuple[ScheduleAnchor, ...]:
        return self.anchors

    def due(self, clock: Any, at: float) -> tuple[ScheduleAnchor, ...]:
        """正在窗口里的锚点（按优先级排序）。"""
        return tuple(anchor for anchor in self.anchors if anchor.is_due(clock, at))

    def upcoming(self, clock: Any, at: float) -> tuple[ScheduleAnchor, ...]:
        """今天还没到窗口的锚点。"""
        return tuple(anchor for anchor in self.anchors if anchor.upcoming(clock, at))

    def next_after(self, clock: Any, at: float) -> ScheduleAnchor | None:
        """下一个要到窗口的锚点（没有就 None）—— Planner 用它决定"排到哪里为止"。"""
        pending = self.upcoming(clock, at)
        if not pending:
            return None
        return min(pending, key=lambda item: (item.target_at(clock, at), item.anchor_id))

    def best_fit(self, activity: str, clock: Any, at: float) -> tuple[ScheduleAnchor | None, float]:
        """某个活动此刻从锚点拿到的最高契合度（0 表示没有任何锚点在等它）。"""
        best: ScheduleAnchor | None = None
        best_fit = 0.0
        for anchor in self.anchors:
            if anchor.activity != str(activity or "").strip().lower():
                continue
            fit = anchor.fit(clock, at)
            if fit > best_fit:
                best, best_fit = anchor, fit
        return best, best_fit

    def to_payload(self, clock: Any = None, at: float | None = None) -> list[dict[str, Any]]:
        return [anchor.to_payload(clock, at) for anchor in self.anchors]


def anchor_adherence(
    anchors: AnchorBook | tuple[ScheduleAnchor, ...],
    spans: list[tuple[str, float, float]],
    *,
    clock: Any,
    now: float,
) -> dict[str, Any]:
    """锚点遵守度（§六十三 的 3 天模拟检查项）。

    ``spans`` 是"真实发生过的时间段"列表 ``(activity, start, end)``（Episode 或计划项都行）。
    对 **spans 覆盖到的每一天 × 每个当天生效的锚点**问一句：这个锚点的目标时刻是不是落在
    某个"该活动"的时间段里？

    已经过去（``window_end <= now``）的锚点才计入分母 —— 还没到的锚点不算失约，
    这样 3 天模拟跑完时这个比率才有意义。
    """
    anchor_list = anchors.all() if isinstance(anchors, AnchorBook) else tuple(anchors)
    day_starts = sorted({float(clock.day_start(start)) for _, start, _ in spans})
    hits = 0
    checked = 0
    details: list[dict[str, Any]] = []
    for day_start in day_starts:
        for anchor in sorted(
            anchor_list, key=lambda item: (item.priority.rank, item.target_minute)
        ):
            if not anchor.active_on(clock, day_start + 1.0):
                continue
            window_end = anchor.window(clock, day_start + 1.0)[1]
            if window_end > float(now):
                continue  # 还没到 / 正在窗口里：不算一次"应该已经发生"
            target = anchor.target_at(clock, day_start + 1.0)
            matched = any(
                str(activity) == anchor.activity and float(start) <= target <= float(end)
                for activity, start, end in spans
            )
            checked += 1
            if matched:
                hits += 1
            details.append(
                {
                    "anchor_id": anchor.anchor_id,
                    "activity": anchor.activity,
                    "day": clock.day_key(day_start + 1.0),
                    "matched": matched,
                }
            )
    return {
        "checked": checked,
        "matched": hits,
        "ratio": (hits / checked) if checked else 1.0,
        "anchors": details,
    }
