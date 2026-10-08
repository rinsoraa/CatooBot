"""Phase 6C §四/§四十一/§四十三/§四十四/§四十六：计划对象（ActivityPlan）。

**Plan ≠ Reality**（§五/§四十六/§七十六）：计划里的一切都只是**意图**。
唯一的事实来源始终是 :class:`app.activity.model.ActivityEpisode`；计划项是
"打算做什么"，永远不能被当成"她现在正在做什么"。

三条由此推出的硬设计：

* 计划项**不是** Episode：它没有 id、没有状态机、不进 ``activity_episodes``。
  一条计划项要变成现实，只能经由 ``ActivityRuntime.start()``（而且 6B 的护栏说了算）。
* 计划**可被整体作废**（§四十二/§四十四）：世界一变（任务、用户、目标、锚点）就生成新计划，
  旧计划标 ``SUPERSEDED`` 并**保留在库里**（绝不 delete）。
* ``plan_version`` 只在**内容真的变了**时 +1（§四十三）：同样的输入重复规划不涨版本。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class PlanStatus(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """§四十四：当前生效的计划 / 被替换掉的旧计划（历史永远保留）。"""

    ACTIVE_PLAN = "ACTIVE_PLAN"
    SUPERSEDED = "SUPERSEDED"


class PlanTrigger(str, Enum):  # noqa: UP042
    """**什么时候**才重新规划（§八 的八个触发点；普通 tick 不在里面）。

    ``HARD_PLAN_TRIGGERS`` 里的触发点可以突破刷新冷却（§九）—— 因为它们是"世界真的变了"：
    Episode 结束、锚点配置变了、用户交互、重启恢复、管理员手动刷新。
    其余（目标漂移、状态漂移、计划快耗尽）走冷却，避免每个 tick 都重算整段 horizon。
    """

    EPISODE_ENDED = "episode_ended"
    PLAN_EXHAUSTED = "plan_exhausted"
    ANCHOR_CHANGED = "anchor_changed"
    GOAL_CHANGED = "goal_changed"
    STATE_CHANGED = "state_changed"
    USER_INTERACTION = "user_interaction"
    RECOVERY = "recovery"
    MANUAL = "manual"


#: 能突破刷新冷却的触发点（§九 的"重大触发"）
HARD_PLAN_TRIGGERS: frozenset[PlanTrigger] = frozenset(
    {
        PlanTrigger.EPISODE_ENDED,
        PlanTrigger.ANCHOR_CHANGED,
        PlanTrigger.USER_INTERACTION,
        PlanTrigger.RECOVERY,
        PlanTrigger.MANUAL,
    }
)


class RejectionReason(str, Enum):  # noqa: UP042
    """候选被拒的原因码（§三十四：``eligible`` / ``rejected`` / ``reason`` 三件套）。"""

    ENERGY_TOO_LOW = "ENERGY_TOO_LOW"  # §二十八：能量不够做这件事
    PERIOD_ILLEGAL = "PERIOD_ILLEGAL"  # 这个时段就不该发生（例如凌晨三点出门）
    ANCHOR_CONFLICT = "ANCHOR_CONFLICT"  # 会和某个正在窗口里的硬锚点撞车
    NOT_ENOUGH_TIME = "NOT_ENOUGH_TIME"  # 剩余 horizon 装不下它的最短时长
    UNKNOWN_ACTIVITY = "UNKNOWN_ACTIVITY"  # 没有画像/时长档（§三十三：候选必须已知）
    NOT_MOVEABLE = "NOT_MOVEABLE"  # 固定活动（睡觉/三餐）不能由 Planner 塞进自由时段
    RESTRICTED = "RESTRICTED"  # 角色 profile 明确不允许（当前没有这种配置）


class ItemReason(str, Enum):  # noqa: UP042
    """一条计划项**为什么**被排进来（§四：计划项必须带 reason）。"""

    ANCHOR = "ANCHOR"  # 锚点要它（三餐/睡觉）
    GOAL = "GOAL"  # 目标在推它（§二十二：只是排名）
    ROUTINE = "ROUTINE"  # 时段习惯（ROUTINE_BY_PERIOD）
    FREE = "FREE"  # 自由时段的自由活动（§十六）
    FALLBACK = "FALLBACK"  # 没有合适候选时的确定性兜底（§四十八/§四十九）
    CONTINUATION = "CONTINUATION"  # 现实里正在做的这件事还没结束（§三十一）


@dataclass(frozen=True)
class Candidate:
    """一个候选活动 + 它的资格与分数（§三十四/§三十五/§三十六）。

    ``score`` **不是概率**（§三十五）：它只是确定性打分，用来排序；没有任何阈值与随机。
    """

    activity: str
    eligible: bool
    reason: str = ""
    score: float = 0.0
    breakdown: dict[str, float] = field(default_factory=dict)
    anchor_id: str = ""
    goal_id: str = ""
    #: 生成顺序（确定性：同一个输入永远是同一个顺序）
    order: int = 0

    def to_payload(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "eligible": bool(self.eligible),
            "reason": self.reason,
            "score": float(self.score),
            "breakdown": {key: float(value) for key, value in self.breakdown.items()},
            "anchor_id": self.anchor_id,
            "goal_id": self.goal_id,
            "order": int(self.order),
        }


@dataclass(frozen=True)
class PlanItem:
    """一条"打算做的事"（§四/§四十六）—— **不是** Episode，也不是承诺。"""

    activity: str
    planned_start: float
    planned_end: float
    reason: str = ItemReason.ROUTINE.value
    priority: float = 0.0
    anchor_id: str = ""
    goal_id: str = ""
    score: float = 0.0

    @property
    def duration(self) -> float:
        return max(0.0, float(self.planned_end) - float(self.planned_start))

    def covers(self, at: float) -> bool:
        return float(self.planned_start) <= float(at) < float(self.planned_end)

    def to_payload(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "planned_start": float(self.planned_start),
            "planned_end": float(self.planned_end),
            "duration": self.duration,
            "reason": self.reason,
            "priority": float(self.priority),
            "anchor_id": self.anchor_id,
            "goal_id": self.goal_id,
            "score": float(self.score),
        }


@dataclass
class ActivityPlan:
    """一段 rolling horizon 的活动计划（§四）。

    ``candidates`` 保留**全部**候选（含被拒的与它们的拒绝原因）—— §五十八 要求管理员能看见
    "为什么不是别的"；``constraints`` 是当时生效的硬约束快照（能量/时段/锚点/目标签名），
    只为了审计与解释，Planner 不会拿它去改任何东西。
    """

    plan_id: str
    character_id: str
    plan_version: int
    generated_at: float
    horizon_start: float
    horizon_end: float
    status: PlanStatus = PlanStatus.ACTIVE_PLAN
    items: tuple[PlanItem, ...] = ()
    candidates: tuple[Candidate, ...] = ()
    constraints: dict[str, Any] = field(default_factory=dict)
    source: str = "ROUTINE"
    trigger: str = ""
    content_hash: str = ""
    superseded_by: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.status, PlanStatus):
            self.status = PlanStatus(str(self.status))
        self.items = tuple(self.items or ())
        self.candidates = tuple(self.candidates or ())
        if not self.content_hash:
            self.content_hash = self.content_signature()

    # ------------------------------------------------------------ 读

    @property
    def horizon_seconds(self) -> float:
        return max(0.0, float(self.horizon_end) - float(self.horizon_start))

    @property
    def active(self) -> bool:
        return self.status is PlanStatus.ACTIVE_PLAN

    def eligible(self) -> tuple[Candidate, ...]:
        return tuple(item for item in self.candidates if item.eligible)

    def rejected(self) -> tuple[Candidate, ...]:
        return tuple(item for item in self.candidates if not item.eligible)

    def selected(self) -> Candidate | None:
        """当前被选中的候选（分数最高的那个 eligible）—— 只用于解释。"""
        choices = self.eligible()
        return choices[0] if choices else None

    def first_item(self) -> PlanItem | None:
        return self.items[0] if self.items else None

    def covers(self, at: float) -> PlanItem | None:
        """``at`` 时刻落在哪条计划项里（**计划**，不是现实）。"""
        for item in self.items:
            if item.covers(at):
                return item
        return None

    def next_item(self, at: float) -> PlanItem | None:
        """``at`` 之后的第一条（用于"接下来准备干嘛"）。"""
        for item in self.items:
            if float(item.planned_start) > float(at):
                return item
        return None

    def upcoming(self, at: float, limit: int = 3) -> tuple[PlanItem, ...]:
        alive = (item for item in self.items if float(item.planned_end) > float(at))
        return tuple(alive)[: max(0, limit)]

    def stale(self, at: float) -> bool:
        """计划是否已经"过期得没法用"：整段 horizon 都过去了，或者它根本没排到东西。

        §四十七：重启后如果 active plan 已经 stale，就重新规划 —— **绝不**盲目续用。
        """
        if not self.items:
            return True
        return float(self.horizon_end) <= float(at)

    # ------------------------------------------------------------ 序列化

    def content_signature(self) -> str:
        """内容签名（只包含**计划内容**，不含生成时刻/版本）。

        §四十三 靠它决定"这次重新规划到底变没变"：变了才 +1 版本。因此条目用的是相对
        ``horizon_start`` 的**偏移**而不是绝对时间 —— 否则"同样决定的计划晚一分钟生成"
        也会被当成内容变化，版本号就失去意义了。
        """
        base = float(self.horizon_start)
        payload = {
            "horizon_seconds": round(self.horizon_seconds, 1),
            "items": [
                [
                    item.activity,
                    round(float(item.planned_start) - base, 3),
                    round(float(item.planned_end) - base, 3),
                    item.reason,
                    item.anchor_id,
                    item.goal_id,
                ]
                for item in self.items
            ],
            "source": self.source,
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def to_payload(self) -> dict[str, Any]:
        """给 API / WebUI / 日志的只读投影（§五十八 的字段都在这里）。"""
        return {
            "plan_id": self.plan_id,
            "character_id": self.character_id,
            "plan_version": int(self.plan_version),
            "status": self.status.value,
            "generated_at": float(self.generated_at),
            "horizon_start": float(self.horizon_start),
            "horizon_end": float(self.horizon_end),
            "horizon_seconds": self.horizon_seconds,
            "source": self.source,
            "trigger": self.trigger,
            "content_hash": self.content_hash,
            "superseded_by": self.superseded_by,
            "items": [item.to_payload() for item in self.items],
            "candidates": [item.to_payload() for item in self.candidates],
            "rejected": [item.to_payload() for item in self.rejected()],
            "constraints": dict(self.constraints),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ActivityPlan:
        return cls(
            plan_id=str(payload.get("plan_id") or ""),
            character_id=str(payload.get("character_id") or ""),
            plan_version=int(payload.get("plan_version") or 0),
            generated_at=float(payload.get("generated_at") or 0.0),
            horizon_start=float(payload.get("horizon_start") or 0.0),
            horizon_end=float(payload.get("horizon_end") or 0.0),
            status=PlanStatus(str(payload.get("status") or "ACTIVE_PLAN")),
            items=tuple(_item_from(obj) for obj in (payload.get("items") or [])),
            candidates=tuple(_candidate_from(obj) for obj in (payload.get("candidates") or [])),
            constraints=dict(payload.get("constraints") or {}),
            source=str(payload.get("source") or "ROUTINE"),
            trigger=str(payload.get("trigger") or ""),
            content_hash=str(payload.get("content_hash") or ""),
            superseded_by=str(payload.get("superseded_by") or ""),
        )


def _item_from(payload: dict[str, Any]) -> PlanItem:
    return PlanItem(
        activity=str(payload.get("activity") or ""),
        planned_start=float(payload.get("planned_start") or 0.0),
        planned_end=float(payload.get("planned_end") or 0.0),
        reason=str(payload.get("reason") or ItemReason.ROUTINE.value),
        priority=float(payload.get("priority") or 0.0),
        anchor_id=str(payload.get("anchor_id") or ""),
        goal_id=str(payload.get("goal_id") or ""),
        score=float(payload.get("score") or 0.0),
    )


def _candidate_from(payload: dict[str, Any]) -> Candidate:
    return Candidate(
        activity=str(payload.get("activity") or ""),
        eligible=bool(payload.get("eligible")),
        reason=str(payload.get("reason") or ""),
        score=float(payload.get("score") or 0.0),
        breakdown={key: float(value) for key, value in (payload.get("breakdown") or {}).items()},
        anchor_id=str(payload.get("anchor_id") or ""),
        goal_id=str(payload.get("goal_id") or ""),
        order=int(payload.get("order") or 0),
    )


def plan_id_for(day: str, sequence: int) -> str:
    """``PLAN-YYYYMMDD-NNN`` —— 与 Episode 同一种可读、可审计、稳定的形状。"""
    return f"PLAN-{str(day)}-{int(sequence):03d}"


def as_plan_trigger(value: Any) -> PlanTrigger:
    """把字符串/枚举都归一成 :class:`PlanTrigger`（不认识就当手动，绝不猜成"重大触发"）。"""
    if isinstance(value, PlanTrigger):
        return value
    try:
        return PlanTrigger(str(value))
    except ValueError:
        return PlanTrigger.MANUAL


def parse_plan_id(plan_id: str) -> tuple[str, int] | None:
    """反解计划 ID；不是这个形状就返回 None（绝不猜）。"""
    parts = str(plan_id or "").strip().split("-")
    if len(parts) != 3 or parts[0] != "PLAN":
        return None
    day, seq = parts[1], parts[2]
    if len(day) != 8 or not day.isdigit() or not seq.isdigit():
        return None
    return day, int(seq)
