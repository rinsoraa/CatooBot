"""Phase 6C §二十-§二十五：持久目标（PersistentGoal）—— **复用既有 Goal 层**，只读投影。

§二 与 §四十五 都要求"先查现有实现，能复用就别新建"。查过了：仓库里**已经**有持久目标层
—— ``app/sandbox/goals.py`` 的 ``GoalManager`` + ``sandbox_goals`` / ``sandbox_goal_steps``
（migration 23），它带 ``kind`` / ``status`` / ``priority`` / ``progress``（已经是 0.0~1.0）/
``target_item`` / ``target_project``，并且**本来就**遵守"Goal 是世界上下文，永远不是动作"。

所以本模块**不建第二套目标系统、不加表**：

    sandbox_goals（既有，唯一事实来源）
              ↓ 只读投影（SandboxGoalSource）
        PersistentGoal（Planner 视角的形状）
              ↓
        goal_relevance 打分项（只影响排序）

三条边界（§二十一/§二十二/§二十三）：

* **Goal ≠ Task**：goal 是长期方向，Task 是"去挖 1 个橡木"。这里只出现 goal，一个 Task 字段都没有。
* **Goal 只影响排名**：:data:`GOAL_KIND_AFFINITY` 给相关活动加分（§二十二"ranking bonus"），
  绝不"必须 building"。
* **Goal 不得覆盖生理约束**：睡觉/吃饭/休息/能量是硬规则（profiles.py 的 ``LOW_ENERGY_SAFE``
  与 anchors 的 ``sleep``/``meal``），优先级 100 的 goal 也压不过它们（§二十三）。

完成条件（§二十五）只有**结构化来源**：本模块只**读**沙盒目标的既有状态（那是由
结构化事件驱动的：物品到手、宠物喂过、项目完成、约定履行），没有"模型说完成就完成"这条路。
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

# ---------------------------------------------------------------- 状态


class GoalStatus(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """§二十：至少 ACTIVE / PAUSED / COMPLETED / CANCELLED（EXPIRED 是既有层也会给的）。"""

    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    @property
    def open(self) -> bool:
        """还没结束的目标（Planner 只关心这些）。"""
        return self in {GoalStatus.ACTIVE, GoalStatus.PAUSED}


#: 既有沙盒目标状态 → 本层的状态（**唯一**映射口径，不各自解释）
GOAL_STATUS_ALIASES: dict[str, GoalStatus] = {
    "pending": GoalStatus.ACTIVE,
    "active": GoalStatus.ACTIVE,
    "blocked": GoalStatus.PAUSED,  # 被挡住 = 暂时不追（等重试窗口）
    "completed": GoalStatus.COMPLETED,
    "cancelled": GoalStatus.CANCELLED,
    "expired": GoalStatus.EXPIRED,
}


def normalize_status(raw: Any) -> GoalStatus:
    """把任意来源的状态名归一（不认识当 ACTIVE —— 保守：宁可不加分，也不丢目标）。"""
    text = str(getattr(raw, "value", raw) or "").strip().lower()
    return GOAL_STATUS_ALIASES.get(text, GoalStatus.ACTIVE)


# ---------------------------------------------------------------- 亲和


#: goal 类型 → 它更偏向哪些**虚拟**活动（§二十二：goal relevance 只是排名信号）。
#: 名字全部来自 ``VIRTUAL_DURATIONS`` 的既有词表 —— 不新造活动，也绝不含 Minecraft 活动名
#: （6A §二十九）。``building`` 是虚拟的"做自己的项目"，**不带** related_task_id，
#: 因此永远不会被说成"她在现实世界里动手"（§五十六）。
GOAL_KIND_AFFINITY: dict[str, tuple[str, ...]] = {
    "restock_resource": ("out", "household"),
    "pet_care": ("pet_care",),
    "complete_project": ("building", "working"),
    "fulfill_commitment": ("online", "out"),
    "shopping_trip": ("out",),
}

#: goal 类型 → 一句话标题模板（**只用于展示**；不认识就退化成 kind 原文）
GOAL_KIND_TITLE: dict[str, str] = {
    "restock_resource": "把东西补上",
    "pet_care": "照顾宠物",
    "complete_project": "推进自己的项目",
    "fulfill_commitment": "履行约定",
    "shopping_trip": "出门把几件事一起办了",
}

#: 亲和度加成（0~1 的确定性常数，乘 ``priority`` 后进 score；§二十二）
GOAL_AFFINITY_BONUS = 1.0


def goal_affinity(kind: Any) -> tuple[str, ...]:
    return GOAL_KIND_AFFINITY.get(str(getattr(kind, "value", kind) or "").strip(), ())


# ---------------------------------------------------------------- 模型


@dataclass(frozen=True)
class PersistentGoal:
    """Planner 视角的持久目标（§二十 的字段形状）。

    ``progress`` 统一用 **0.0~1.0**（§二十四 二选一，本项目选这个 —— 既有沙盒层本来就是这个口径，
    不搞两套）。

    只读对象：**没有**任何改状态的方法。Planner 永远不会完成/取消一个 goal（§二十五）。
    """

    goal_id: str
    title: str
    description: str = ""
    priority: float = 0.5
    progress: float = 0.0
    status: GoalStatus = GoalStatus.ACTIVE
    kind: str = ""
    source: str = ""
    affinity: tuple[str, ...] = ()
    target_item: str = ""
    target_project: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0

    @property
    def open(self) -> bool:
        return self.status.open

    def relevance_for(self, activity: str) -> float:
        """这个 goal 对某个活动的相关度（0~1）：亲和 × 优先级 × 未完成度。

        * 亲和：活动在这个 goal 的亲和表里才有基础分；
        * 优先级：goal 越重要越能带动；
        * 未完成度：``1 - progress``（快完成的目标更值得推一把）。
        全部确定性，**没有**随机、没有阈值概率（v1.0 §23）。
        """
        name = str(activity or "").strip().lower()
        if name not in self.affinity:
            return 0.0
        remaining = max(0.0, min(1.0, 1.0 - float(self.progress)))
        return round(
            GOAL_AFFINITY_BONUS * max(0.0, min(1.0, float(self.priority))) * remaining,
            6,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "title": self.title,
            "description": self.description,
            "priority": float(self.priority),
            "progress": float(self.progress),
            "status": self.status.value,
            "kind": self.kind,
            "source": self.source,
            "affinity": list(self.affinity),
            "target_item": self.target_item,
            "target_project": self.target_project,
            "created_at": float(self.created_at),
            "updated_at": float(self.updated_at),
        }


def build_goal(
    *,
    goal_id: str,
    kind: Any,
    status: Any = GoalStatus.ACTIVE,
    priority: float = 0.5,
    progress: float = 0.0,
    description: str = "",
    source: str = "",
    target_item: str = "",
    target_project: str = "",
    created_at: float = 0.0,
    updated_at: float = 0.0,
    title: str = "",
) -> PersistentGoal:
    """造一个只读目标（标题可显式给，否则按 kind 确定性生成）。"""
    kind_text = str(getattr(kind, "value", kind) or "").strip()
    target_item = str(target_item or "")
    target_project = str(target_project or "")
    default_title = GOAL_KIND_TITLE.get(kind_text, kind_text) or kind_text
    detail = target_project or target_item
    if detail:
        default_title = f"{default_title}（{detail}）"
    return PersistentGoal(
        goal_id=str(goal_id or ""),
        title=str(title or default_title),
        description=str(description or ""),
        priority=max(0.0, min(1.0, float(priority or 0.0))),
        progress=max(0.0, min(1.0, float(progress or 0.0))),
        status=status if isinstance(status, GoalStatus) else normalize_status(status),
        kind=kind_text,
        source=str(source or ""),
        affinity=goal_affinity(kind),
        target_item=target_item,
        target_project=target_project,
        created_at=float(created_at or 0.0),
        updated_at=float(updated_at or 0.0),
    )


# ---------------------------------------------------------------- 快照


@dataclass
class GoalSnapshot:
    """某一刻的目标快照（Planner 的输入；runtime 用它检测"目标变了没"）。"""

    goals: tuple[PersistentGoal, ...] = ()
    source: str = "none"
    degraded_reason: str = ""

    @property
    def open_goals(self) -> tuple[PersistentGoal, ...]:
        return tuple(goal for goal in self.goals if goal.open)

    def signature(self) -> str:
        """内容签名（只有真的变了才换签名）—— §八"Goal 发生变化"触发点用它，O(n) 且 n 很小。"""
        payload = [
            {
                "id": goal.goal_id,
                "status": goal.status.value,
                "priority": round(float(goal.priority), 4),
                "progress": round(float(goal.progress), 4),
            }
            for goal in sorted(self.goals, key=lambda item: item.goal_id)
        ]
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def relevance_for(self, activity: str) -> tuple[float, str]:
        """某个活动从**所有**开放目标得到的最大相关度 + 是哪个目标给的。

        没有目标在推这个活动就是 ``(0.0, "")``。
        """
        best_value = 0.0
        best_id = ""
        for goal in self.open_goals:
            value = goal.relevance_for(activity)
            if value > best_value:
                best_value, best_id = value, goal.goal_id
        return best_value, best_id

    def to_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "degraded": self.degraded_reason,
            "signature": self.signature(),
            "goals": [goal.to_payload() for goal in self.goals],
            "open_count": len(self.open_goals),
        }


# ---------------------------------------------------------------- 来源


class GoalSource(Protocol):
    """只读目标来源（**只有** snapshot；没有 create/complete/cancel）。"""

    def snapshot(self) -> GoalSnapshot: ...


class NullGoalSource:
    """没有目标层时的来源（沙盒关闭 / 单测）：永远给空快照，绝不让 Planner 因它失败。"""

    def snapshot(self) -> GoalSnapshot:
        return GoalSnapshot(source="none")


@dataclass
class SandboxGoalSource:
    """既有沙盒目标层的**只读**适配器（§二"优先复用"）。

    用鸭子类型（``manager.all(statuses=...)``）而不是 import ``app.sandbox``：
    Planner 不需要知道沙盒的内部类型，也就不可能顺手调用它的执行路径（§六十六）。
    读失败只降级成空快照 + 原因，绝不抛给世界 tick。
    """

    manager: Any = None
    #: 只取这些状态的目标（默认两种"还活着"的状态）
    statuses: tuple[Any, ...] = ()
    _last_reason: str = field(default="", init=False, repr=False)

    def snapshot(self) -> GoalSnapshot:
        manager = self.manager
        if manager is None:
            return GoalSnapshot(source="sandbox", degraded_reason=self._last_reason)
        try:
            raw = manager.all(statuses=self.statuses or None) or []
        except TypeError:
            # 老签名不接受 statuses：退化成"全都要"，再自己过滤（仍然只读）
            try:
                raw = manager.all() or []
            except Exception as exc:  # noqa: BLE001 - 只降级
                self._last_reason = f"{type(exc).__name__}"
                return GoalSnapshot(source="sandbox", degraded_reason=self._last_reason)
        except Exception as exc:  # noqa: BLE001 - 只降级（§四十八：绝不让活动变 null）
            self._last_reason = f"{type(exc).__name__}"
            return GoalSnapshot(source="sandbox", degraded_reason=self._last_reason)
        self._last_reason = ""
        return GoalSnapshot(
            goals=tuple(_from_sandbox_goal(item) for item in raw),
            source="sandbox",
        )

    @property
    def degraded_reason(self) -> str:
        return self._last_reason


def _from_sandbox_goal(raw: Any) -> PersistentGoal:
    """沙盒 Goal → 只读 PersistentGoal（只读字段，一个都不写回）。"""
    kind = getattr(raw, "kind", "")
    return build_goal(
        goal_id=str(getattr(raw, "goal_id", "") or ""),
        kind=kind,
        status=normalize_status(getattr(raw, "status", "")),
        priority=float(getattr(raw, "priority", 0.0) or 0.0),
        progress=float(getattr(raw, "progress", 0.0) or 0.0),
        description=str(getattr(raw, "reason", "") or ""),
        source=str(getattr(getattr(raw, "source", ""), "value", getattr(raw, "source", "")) or ""),
        target_item=str(getattr(raw, "target_item", "") or ""),
        target_project=str(getattr(raw, "target_project", "") or ""),
        created_at=float(getattr(raw, "created_at", 0.0) or 0.0),
        updated_at=float(getattr(raw, "updated_at", 0.0) or 0.0),
    )
