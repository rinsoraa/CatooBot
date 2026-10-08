"""Phase 6A §三-§七/§三十/§三十一：Activity Episode 的领域模型与状态机。

对齐 ``docs/specs/v1.0.md`` §5-§9（"Episode-Based Activity Model"）与 6A 任务书 §三。

设计要点：

* **Episode 是"当前活动"的唯一事实来源**（§一/§八）：``CharacterState.activity`` 只是它的
  派生快照，永远不允许反向写回（§十四）。
* 状态一律**显式枚举** + **显式转移表**（§五/§六）：非法转移直接拒绝，不靠"看起来对"。
* **持续时间是 Episode 的属性**（§七）：``min/typical/max`` 三个值，``planned_end_at`` 由
  ``typical`` 推出、硬上限由 ``max`` 推出 —— 绝不在 tick 里随机生成时长（§四十五）。
* Episode **不是 Task**（§十八）：它只带 ``related_task_id`` 引用，绝不复制任务状态机。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------- 枚举


class ActivityStatus(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """Episode 状态（§五）。"""

    SCHEDULED = "SCHEDULED"
    ACTIVE = "ACTIVE"
    EXTENDED = "EXTENDED"
    COMPLETED = "COMPLETED"
    INTERRUPTED = "INTERRUPTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_ACTIVITY_STATUSES

    @property
    def open(self) -> bool:
        """还"活着"的状态（可以继续推进生命周期）。"""
        return self in LIVE_ACTIVITY_STATUSES


#: 终态：到了这里这个 Episode 就结束了，永远不能再回到 ACTIVE（§六）
TERMINAL_ACTIVITY_STATUSES = frozenset(
    {
        ActivityStatus.COMPLETED,
        ActivityStatus.INTERRUPTED,
        ActivityStatus.CANCELLED,
        ActivityStatus.EXPIRED,
    }
)

#: "占用 primary 槽位"的状态（§十二/§二十六：每个角色同时最多一个）
LIVE_ACTIVITY_STATUSES = frozenset(
    {ActivityStatus.SCHEDULED, ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}
)

#: 允许的状态转移（§六；其余一律拒绝）
ALLOWED_ACTIVITY_TRANSITIONS: dict[ActivityStatus, frozenset[ActivityStatus]] = {
    # 计划好了还没开始：可以开始、可以取消、也可以在错过后作废
    ActivityStatus.SCHEDULED: frozenset(
        {ActivityStatus.ACTIVE, ActivityStatus.CANCELLED, ActivityStatus.EXPIRED}
    ),
    ActivityStatus.ACTIVE: frozenset(
        {
            ActivityStatus.EXTENDED,
            ActivityStatus.COMPLETED,
            ActivityStatus.INTERRUPTED,
            ActivityStatus.CANCELLED,
            ActivityStatus.EXPIRED,
        }
    ),
    # EXTENDED 可以继续再延长，也可以正常收尾
    ActivityStatus.EXTENDED: frozenset(
        {
            ActivityStatus.EXTENDED,
            ActivityStatus.COMPLETED,
            ActivityStatus.INTERRUPTED,
            ActivityStatus.CANCELLED,
            ActivityStatus.EXPIRED,
        }
    ),
    # 终态没有出口（COMPLETED → ACTIVE / CANCELLED → ACTIVE / EXPIRED → ACTIVE 全部拒绝）
    ActivityStatus.COMPLETED: frozenset(),
    ActivityStatus.INTERRUPTED: frozenset(),
    ActivityStatus.CANCELLED: frozenset(),
    ActivityStatus.EXPIRED: frozenset(),
}


class ActivitySource(str, Enum):  # noqa: UP042
    """Episode 由什么机制创建（§三十）。**本阶段没有 AUTONOMOUS。**"""

    USER = "USER"
    TASK = "TASK"
    ROUTINE = "ROUTINE"
    WORLD_EVENT = "WORLD_EVENT"
    RECOVERY = "RECOVERY"
    SYSTEM = "SYSTEM"


class ActivityType(str, Enum):  # noqa: UP042
    """活动的种类（§三的 ``activity_type``）。"""

    VIRTUAL_LIFE = "virtual_life"  # 她自己世界里的日常（沙盒 / 例行）
    TASK_EXECUTION = "task_execution"  # 真实任务执行（Minecraft 等）
    USER_INTERACTION = "user_interaction"  # 用户交互引起的活动
    RECOVERY = "recovery"  # 重启恢复时补建的收尾性 Episode
    SYSTEM = "system"  # 系统自身（维护、观察）


class TransitionReason(str, Enum):  # noqa: UP042
    """转移原因（§三十一）—— 每一次状态变化都必须能回答"为什么"。"""

    TIME_EXPIRED = "TIME_EXPIRED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    USER_INTERACTION = "USER_INTERACTION"
    WORLD_EVENT = "WORLD_EVENT"
    SCHEDULED = "SCHEDULED"
    RECOVERY = "RECOVERY"
    MANUAL = "MANUAL"

    @property
    def value_or_default(self) -> str:
        return str(self.value)


#: 终态转移（这些 transition 对同一个 Episode 只允许发生一次，§三十三）
ONE_SHOT_TRANSITIONS = (
    ActivityStatus.COMPLETED.value,
    ActivityStatus.INTERRUPTED.value,
    ActivityStatus.CANCELLED.value,
    ActivityStatus.EXPIRED.value,
    ActivityStatus.ACTIVE.value,
)

#: Minecraft 相关的活动名（虚拟活动**绝不**能用这些名字，§二十九/§四十八）
MINECRAFT_ACTIVITY_MARKERS = ("minecraft", "mc_", "dig", "mining", "explore")

#: 虚拟生活的 duration 表（§七：min/typical/max，单位秒）——确定性、无随机。
#:
#: 词表与**沙盒的动作定义**保持一致（``app/sandbox/action_templates.py`` 的
#: ``activity``：eating/gaming/reading/out/sleeping/idle/…）——``CharacterState.activity``
#: 一直存的就是这些 token，绝不能变成"两套语言"。给模型看的人话在
#: :data:`ACTIVITY_LABELS` 里做映射（**只影响措辞**，不影响任何语义/权限）。
VIRTUAL_DURATIONS: dict[str, tuple[float, float, float]] = {
    "eating": (10 * 60.0, 30 * 60.0, 60 * 60.0),
    "reading": (20 * 60.0, 60 * 60.0, 150 * 60.0),
    "gaming": (20 * 60.0, 60 * 60.0, 180 * 60.0),
    "online": (10 * 60.0, 40 * 60.0, 120 * 60.0),
    "out": (10 * 60.0, 30 * 60.0, 120 * 60.0),
    "household": (10 * 60.0, 30 * 60.0, 90 * 60.0),
    "pet_care": (5 * 60.0, 20 * 60.0, 60 * 60.0),
    "self_care": (10 * 60.0, 30 * 60.0, 90 * 60.0),
    "working": (30 * 60.0, 90 * 60.0, 240 * 60.0),
    "napping": (20 * 60.0, 60 * 60.0, 120 * 60.0),
    "sleeping": (180 * 60.0, 480 * 60.0, 600 * 60.0),
    "idle": (5 * 60.0, 20 * 60.0, 60 * 60.0),
}

#: 活动 token → 人话（**只用于给模型/WebUI 的措辞**，不参与任何判断）
ACTIVITY_LABELS: dict[str, str] = {
    "eating": "吃饭",
    "reading": "看书",
    "gaming": "打游戏",
    "online": "上网",
    "out": "出门",
    "household": "做家务",
    "pet_care": "照顾宠物",
    "self_care": "打理自己",
    "working": "工作",
    "napping": "小睡",
    "sleeping": "睡觉",
    "idle": "发呆",
    "minecraft_task": "执行 Minecraft 任务",
}

#: 默认档（未知活动：保守的 15/45/180 分钟）
DEFAULT_VIRTUAL_PROFILE = (15 * 60.0, 45 * 60.0, 180 * 60.0)


def activity_label(name: str) -> str:
    """给模型/界面看的人话（认识就翻译，不认识就原样返回 —— 绝不猜）。"""
    text = str(name or "").strip()
    return ACTIVITY_LABELS.get(text, text)


def looks_like_minecraft_activity(name: str) -> bool:
    """这个名字是不是"真实 Minecraft 活动"（虚拟活动不许冒用，§二十九）。"""
    text = str(name or "").strip().lower()
    return any(marker in text for marker in MINECRAFT_ACTIVITY_MARKERS)


#: Task 的**终态** → Episode 的 (状态, 原因)。这是 §四十二"必须统一"的唯一口径：
#: 无论走"实时事件"还是"重启后对账"，同一种任务结局都映射到同一种活动结局。
#: （任务侧的状态名来自 ``TaskState``；这里只认终态 + PAUSED。）
TASK_STATE_OUTCOME: dict[str, tuple[ActivityStatus, TransitionReason]] = {
    "SUCCEEDED": (ActivityStatus.COMPLETED, TransitionReason.TASK_COMPLETED),
    "FAILED": (ActivityStatus.INTERRUPTED, TransitionReason.TASK_FAILED),
    "CANCELLED": (ActivityStatus.CANCELLED, TransitionReason.MANUAL),
    "EXPIRED": (ActivityStatus.EXPIRED, TransitionReason.TIME_EXPIRED),
    "PAUSED": (ActivityStatus.INTERRUPTED, TransitionReason.USER_INTERACTION),
}

#: Episode 终态 → ActivityRuntime 的公开收尾方法（事件适配器只走这些入口，绕不过状态机）
FINISH_METHOD_BY_STATUS: dict[ActivityStatus, str] = {
    ActivityStatus.COMPLETED: "complete",
    ActivityStatus.INTERRUPTED: "interrupt",
    ActivityStatus.CANCELLED: "cancel",
    ActivityStatus.EXPIRED: "expire",
}


def duration_profile(activity_type: ActivityType, activity_name: str) -> tuple[float, float, float]:
    """活动的 (min, typical, max) 时长（秒）。

    **确定性**：同样的 (type, name) 永远给同样的三个值；未知活动用默认档。
    真实 Minecraft 任务活动的时长由任务自己决定（这里给宽档，只用于兜底）。
    """
    profile = VIRTUAL_DURATIONS.get(str(activity_name or "").strip())
    if profile is not None:
        return profile
    if activity_type is ActivityType.TASK_EXECUTION:
        return (60.0, 600.0, 3600.0)
    if activity_type is ActivityType.USER_INTERACTION:
        return (30.0, 5 * 60.0, 30 * 60.0)
    return DEFAULT_VIRTUAL_PROFILE


# ---------------------------------------------------------------- 模型


@dataclass
class ActivityEpisode:
    """一个具有生命周期的活动片段（§三）。

    字段语义（对齐 v1.0 §5 与 6A §二十五）：

    ``activity_type`` / ``activity_name``
        是什么活动（种类 + 人话名字）。
    ``location`` / ``social_state`` / ``tags``
        当时在哪、和谁、有什么标签（**语义**位置即可，§十五）。
    ``started_at`` / ``planned_end_at``
        什么时候开始、**计划**什么时候结束（由 typical duration 推出）。
    ``min_duration`` / ``typical_duration`` / ``max_duration``
        生命周期属性（§七），**不是**每次 tick 现算的。
    ``status`` / ``transition_reason`` / ``source``
        当前状态、为什么变成现在这样、由什么创建。
    ``parent_episode_id``
        如果它是某个 Episode 的后续（例如被打断之后 planner 排的下一个）。
    ``related_task_id``
        如果是任务引起的活动，指向真实 Task（§十八：只引用，不复制任务状态机）。
    """

    episode_id: str
    character_id: str
    activity_type: ActivityType
    activity_name: str
    status: ActivityStatus = ActivityStatus.SCHEDULED

    location: str = ""
    social_state: str = "alone"
    tags: list[str] = field(default_factory=list)

    started_at: float = 0.0
    planned_end_at: float = 0.0
    ended_at: float = 0.0

    min_duration: float = 0.0
    typical_duration: float = 0.0
    max_duration: float = 0.0

    transition_reason: str = ""
    source: ActivitySource = ActivitySource.SYSTEM
    parent_episode_id: str = ""
    related_task_id: str = ""

    #: 已经延长过几次（§二十七：延长有上限，不能无限续命）
    extension_count: int = 0
    #: 最近一次只读观察（Minecraft / 世界事实；只用于判断"这个 Episode 还合理吗"，§十六）
    observation: dict[str, Any] = field(default_factory=dict)

    created_at: float = 0.0
    updated_at: float = 0.0

    # ------------------------------------------------------------ 生命周期

    def __post_init__(self) -> None:
        if not isinstance(self.status, ActivityStatus):
            self.status = ActivityStatus(str(self.status))
        if not isinstance(self.source, ActivitySource):
            self.source = ActivitySource(str(self.source))
        if not isinstance(self.activity_type, ActivityType):
            self.activity_type = ActivityType(str(self.activity_type))
        self.tags = [str(tag) for tag in (self.tags or [])]
        if self.observation is None:
            self.observation = {}

    @property
    def max_end_at(self) -> float:
        """硬上限：到了这里必须终结（§二十七 情况 C：不能无限延长）。"""
        if not self.started_at:
            return 0.0
        return float(self.started_at) + float(self.max_duration or 0.0)

    def can_transition_to(self, target: ActivityStatus) -> bool:
        """状态机裁决（§六）—— 非法转移一律拒绝。"""
        return target in ALLOWED_ACTIVITY_TRANSITIONS.get(self.status, frozenset())

    def elapsed(self, now: float) -> float:
        return max(0.0, float(now) - float(self.started_at or now))

    def overdue(self, now: float) -> bool:
        """已经超过计划结束时间（还没到硬上限）。"""
        return bool(self.planned_end_at) and float(now) >= float(self.planned_end_at)

    def beyond_max(self, now: float) -> bool:
        """已经超过硬上限。"""
        limit = self.max_end_at
        return bool(limit) and float(now) >= limit

    def to_payload(self) -> dict[str, Any]:
        """JSON 投影（WebUI / API / 日志 / 事件都用它，不另造一套）。"""
        return {
            "episode_id": self.episode_id,
            "character_id": self.character_id,
            "activity_type": self.activity_type.value,
            "activity_name": self.activity_name,
            "status": self.status.value,
            "location": self.location,
            "social_state": self.social_state,
            "tags": list(self.tags),
            "started_at": self.started_at,
            "planned_end_at": self.planned_end_at,
            "ended_at": self.ended_at,
            "max_end_at": self.max_end_at,
            "min_duration": self.min_duration,
            "typical_duration": self.typical_duration,
            "max_duration": self.max_duration,
            "transition_reason": self.transition_reason,
            "source": self.source.value,
            "parent_episode_id": self.parent_episode_id,
            "related_task_id": self.related_task_id,
            "extension_count": int(self.extension_count),
            "observation": dict(self.observation),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ActivityEpisode:
        return cls(
            episode_id=str(payload.get("episode_id") or ""),
            character_id=str(payload.get("character_id") or ""),
            activity_type=ActivityType(str(payload.get("activity_type") or "system")),
            activity_name=str(payload.get("activity_name") or ""),
            status=ActivityStatus(str(payload.get("status") or "SCHEDULED")),
            location=str(payload.get("location") or ""),
            social_state=str(payload.get("social_state") or "alone"),
            tags=list(payload.get("tags") or []),
            started_at=float(payload.get("started_at") or 0.0),
            planned_end_at=float(payload.get("planned_end_at") or 0.0),
            ended_at=float(payload.get("ended_at") or 0.0),
            min_duration=float(payload.get("min_duration") or 0.0),
            typical_duration=float(payload.get("typical_duration") or 0.0),
            max_duration=float(payload.get("max_duration") or 0.0),
            transition_reason=str(payload.get("transition_reason") or ""),
            source=ActivitySource(str(payload.get("source") or "SYSTEM")),
            parent_episode_id=str(payload.get("parent_episode_id") or ""),
            related_task_id=str(payload.get("related_task_id") or ""),
            extension_count=int(payload.get("extension_count") or 0),
            observation=dict(payload.get("observation") or {}),
            created_at=float(payload.get("created_at") or 0.0),
            updated_at=float(payload.get("updated_at") or 0.0),
        )


def build_episode(
    *,
    episode_id: str,
    character_id: str,
    activity_type: ActivityType,
    activity_name: str,
    source: ActivitySource,
    now: float,
    location: str = "",
    social_state: str = "alone",
    tags: list[str] | None = None,
    related_task_id: str = "",
    parent_episode_id: str = "",
    duration: tuple[float, float, float] | None = None,
    status: ActivityStatus = ActivityStatus.SCHEDULED,
) -> ActivityEpisode:
    """按 §七 的 duration 档造一个 Episode（``planned_end_at`` 由 typical 推出）。

    **起点**：``started_at`` 先留 0（SCHEDULED 还没开始），真正 ACTIVE 时才落时间。
    """
    minimum, typical, maximum = duration or duration_profile(activity_type, activity_name)
    return ActivityEpisode(
        episode_id=str(episode_id),
        character_id=str(character_id),
        activity_type=activity_type,
        activity_name=str(activity_name),
        status=status,
        location=str(location or ""),
        social_state=str(social_state or "alone"),
        tags=list(tags or []),
        min_duration=float(minimum),
        typical_duration=float(typical),
        max_duration=float(maximum),
        transition_reason=TransitionReason.SCHEDULED.value,
        source=source,
        related_task_id=str(related_task_id or ""),
        parent_episode_id=str(parent_episode_id or ""),
        created_at=float(now),
        updated_at=float(now),
    )


def episode_id_for(day: str, sequence: int) -> str:
    """``ACT-YYYYMMDD-NNN``（§四：稳定、可审计、持久化、Recovery 后不变）。"""
    return f"ACT-{day}-{int(sequence):03d}"


def parse_episode_id(episode_id: str) -> tuple[str, int] | None:
    """反解 Episode ID；不是这个形状就返回 None（绝不猜）。"""
    text = str(episode_id or "").strip()
    parts = text.split("-")
    if len(parts) != 3 or parts[0] != "ACT":
        return None
    day, seq = parts[1], parts[2]
    if len(day) != 8 or not day.isdigit() or not seq.isdigit():
        return None
    return day, int(seq)


def now_seconds() -> float:
    return float(time.time())
