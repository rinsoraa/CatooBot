"""Phase 6C §十八/§十九/§二十六-§二十九：活动画像（ActivityProfile）与固定/弹性/自由三分类。

一个活动"是什么样的一件事"——它的时长档、能不能挪、耗精力/耗专注、以及它能配合哪一类
**日程锚点**（anchors.py）。Planner 的候选筛选与打分**只**读这里，绝不读活动名去猜。

四条设计约束：

* **时长只有一个来源**（§十八"其余如果已有模型则复用"）：``min/typical/max`` 是**属性**，
  直接读 :func:`app.activity.model.duration_profile`（就是 6A 定的那份 ``VIRTUAL_DURATIONS``），
  本模块**不**复制一份时长表 —— 两份时长表迟早会打起来。
* **三分类是硬输入**（§十九）：``fixed`` / ``flexible`` / ``free`` 决定"能不能被 Planner 挪动"。
* **不引入人格数学**（§十八结尾）：没有性格向量、没有概率、没有随机；cost 是 0~1 的确定性常数。
* **词表就是 ``VIRTUAL_DURATIONS`` 的词表**（§十六"如果项目已有活动池：复用"）。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from app.activity.model import VIRTUAL_DURATIONS, ActivityType, duration_profile

# ---------------------------------------------------------------- 分类


class ActivityKind(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """§十九：固定 / 弹性 / 自由。Planner 候选筛选的重要输入。"""

    FIXED = "fixed"  # 时间基本钉死（睡觉、三餐）—— Planner 只能"围着它排"
    FLEXIBLE = "flexible"  # 可以挪一段（工作、家务、出门）；Minecraft 任务也属这一类
    FREE = "free"  # 自由填充（听音乐、看剧、发呆）


#: 允许在自由时段里被 Planner 挑出来的活动池（§十六）。
#: 刻意**不含**任何 Minecraft 活动名（6A §二十九：虚拟活动不许冒用真实世界动作名）。
FREE_ACTIVITY_POOL: tuple[str, ...] = (
    "gaming",
    "music",
    "watching_show",
    "reading",
    "relax",
    "free_time",
    "building",
)

#: 锚点优先级类别（§十三 的取值，**从高到低**）——画像用它声明"我能配合哪一类锚点"。
#: 用字符串而不是枚举，是为了让 profiles 不依赖 anchors（避免循环 import）。
ANCHOR_CLASSES: tuple[str, ...] = (
    "sleep",
    "meal",
    "fixed_event",
    "routine_activity",
    "free_activity",
)

#: 需要"睡觉/休息"语义的活动（§二十八 能量极低时的安全集合）
REST_ACTIVITIES: frozenset[str] = frozenset({"sleeping", "napping", "resting"})

#: 能量极低时仍然允许的活动（§二十八：不能 energy=0.15 → gaming 两小时）。
#: 「除非角色 profile 明确允许」在 6C 的结构化落法就是这张白名单 —— 空集之外没有豁免。
LOW_ENERGY_SAFE: frozenset[str] = frozenset(
    {
        "sleeping",
        "napping",
        "resting",
        "free_time",
        "idle",
        "eating",
        "self_care",
        "relax",
        "music",
        "reading",
        "watching_show",
    }
)

#: 角色 profile 可以显式豁免的清单（默认**空**：没有任何活动被特批）。
#: 这一格存在的意义是"§二十八 的例外必须写下来"，而不是让代码偷偷放宽。
LOW_ENERGY_EXEMPT: frozenset[str] = frozenset()

#: 能量低于这个值时只允许 :data:`LOW_ENERGY_SAFE`（§二十八）
LOW_ENERGY_THRESHOLD = 0.25

#: 专注的软阈值（§二十九：只影响排序信号，绝不是硬动作）
HIGH_FOCUS_THRESHOLD = 0.65
LOW_FOCUS_THRESHOLD = 0.35

#: 某些活动在**某些时段**就是不合理（§三十五 time-period fit 的硬那一半）。
#: 只列"明显不该发生"的组合，保持确定性、可解释、够小。
ILLEGAL_BY_PERIOD: dict[str, frozenset[str]] = {
    "late_night": frozenset({"out", "working", "household"}),
    "night": frozenset({"out", "working"}),
    "morning": frozenset({"sleeping"}),
}

#: 专注高时更合适（§二十九），专注低时更合适 —— **只是排序信号**
FOCUS_PREFERRED_HIGH: frozenset[str] = frozenset({"reading", "building", "working", "online"})
FOCUS_PREFERRED_LOW: frozenset[str] = frozenset(
    {"music", "watching_show", "relax", "idle", "free_time"}
)

# ---------------------------------------------------------------- 画像


@dataclass(frozen=True)
class ActivityProfile:
    """一个活动的画像（§十八）。

    真正被 Planner 用到的字段：``kind`` / ``min|typical|max_duration`` / ``flexibility`` /
    ``energy_cost`` / ``focus_cost`` / ``anchor_compatibility``。其余字段是给候选与
    WebUI 解释用的（§五十八：管理员要看得见"为什么是它"）。
    """

    activity: str
    category: str
    kind: ActivityKind
    flexibility: float
    energy_cost: float
    focus_cost: float
    social_preference: str
    anchor_compatibility: frozenset[str]

    # ---- 时长：**不**在本模块复制，直接问 6A 的词表（§十八） ----

    @property
    def min_duration(self) -> float:
        return duration_profile(ActivityType.VIRTUAL_LIFE, self.activity)[0]

    @property
    def typical_duration(self) -> float:
        return duration_profile(ActivityType.VIRTUAL_LIFE, self.activity)[1]

    @property
    def max_duration(self) -> float:
        return duration_profile(ActivityType.VIRTUAL_LIFE, self.activity)[2]

    @property
    def moveable(self) -> bool:
        """能不能被 Planner 挪时间（fixed 不行）。"""
        return self.kind is not ActivityKind.FIXED

    @property
    def servable_anchor_classes(self) -> frozenset[str]:
        return self.anchor_compatibility

    def to_payload(self) -> dict[str, Any]:
        return {
            "activity": self.activity,
            "category": self.category,
            "kind": self.kind.value,
            "min_duration": self.min_duration,
            "typical_duration": self.typical_duration,
            "max_duration": self.max_duration,
            "flexibility": float(self.flexibility),
            "energy_cost": float(self.energy_cost),
            "focus_cost": float(self.focus_cost),
            "social_preference": self.social_preference,
            "anchor_compatibility": sorted(self.anchor_compatibility),
        }


#: (category, kind, flexibility, energy_cost, focus_cost, social_preference)
#: —— 一张表说清全部画像，**不**给单个活动写 if 分支。
_SPECS: dict[str, tuple[str, ActivityKind, float, float, float, str]] = {
    # 睡眠类：钉死的作息，能量为负、专注为负
    "sleeping": ("sleep", ActivityKind.FIXED, 0.05, -0.8, -0.6, "alone"),
    "napping": ("sleep", ActivityKind.FLEXIBLE, 0.35, -0.5, -0.4, "alone"),
    "resting": ("sleep", ActivityKind.FLEXIBLE, 0.6, -0.5, -0.5, "alone"),
    # 三餐：锚点活动（§十二 的 lunch）
    "eating": ("meal", ActivityKind.FIXED, 0.15, -0.2, -0.2, "either"),
    # 工作/项目：弹性、耗专注
    "working": ("work", ActivityKind.FLEXIBLE, 0.45, 0.55, 0.75, "alone"),
    "building": ("work", ActivityKind.FLEXIBLE, 0.5, 0.5, 0.7, "either"),
    # 自由娱乐（§十六 的自由活动池）
    "gaming": ("leisure", ActivityKind.FREE, 0.8, 0.35, 0.5, "either"),
    "music": ("leisure", ActivityKind.FREE, 0.9, -0.1, 0.1, "alone"),
    "watching_show": ("leisure", ActivityKind.FREE, 0.85, 0.15, 0.25, "either"),
    "reading": ("leisure", ActivityKind.FREE, 0.7, 0.1, 0.55, "alone"),
    "relax": ("leisure", ActivityKind.FREE, 0.9, -0.3, -0.2, "alone"),
    "online": ("social", ActivityKind.FREE, 0.8, 0.1, 0.3, "either"),
    "free_time": ("leisure", ActivityKind.FREE, 0.95, -0.1, -0.1, "either"),
    "idle": ("idle", ActivityKind.FREE, 0.95, -0.4, -0.4, "alone"),
    # 照料/家务/出门：可挪动的"正经事"
    "household": ("chore", ActivityKind.FLEXIBLE, 0.5, 0.3, 0.2, "alone"),
    "self_care": ("care", ActivityKind.FLEXIBLE, 0.4, -0.1, 0.0, "alone"),
    "pet_care": ("care", ActivityKind.FLEXIBLE, 0.4, 0.1, 0.1, "alone"),
    "out": ("chore", ActivityKind.FLEXIBLE, 0.45, 0.35, 0.25, "either"),
}


def _anchor_compat(activity: str, category: str) -> frozenset[str]:
    """画像能服务哪一类锚点（§十三 的五档）。确定性推导，不写死 18 行。"""
    classes: set[str] = {"free_activity"}
    if activity in REST_ACTIVITIES:
        classes.add("sleep")
    if category == "meal":
        classes.add("meal")
    if category in {"work", "chore"}:
        classes.add("routine_activity")
    if category in {"care", "chore"}:
        classes.add("fixed_event")
    return frozenset(classes)


ACTIVITY_PROFILES: dict[str, ActivityProfile] = {
    activity: ActivityProfile(
        activity=activity,
        category=category,
        kind=kind,
        flexibility=float(flexibility),
        energy_cost=float(energy_cost),
        focus_cost=float(focus_cost),
        social_preference=social,
        anchor_compatibility=_anchor_compat(activity, category),
    )
    for activity, (category, kind, flexibility, energy_cost, focus_cost, social) in _SPECS.items()
}

#: 未知活动的确定性兜底画像（不猜、不随机：当成"自由活动"处理）
DEFAULT_PROFILE = ActivityProfile(
    activity="",
    category="leisure",
    kind=ActivityKind.FREE,
    flexibility=0.5,
    energy_cost=0.3,
    focus_cost=0.3,
    social_preference="either",
    anchor_compatibility=frozenset({"free_activity"}),
)


def profile_for(activity: str) -> ActivityProfile:
    """活动的画像（认识就给真的，不认识给确定性兜底）。"""
    name = str(activity or "").strip().lower()
    found = ACTIVITY_PROFILES.get(name)
    if found is not None:
        return found
    return DEFAULT_PROFILE


def is_free_activity(activity: str) -> bool:
    return profile_for(activity).kind is ActivityKind.FREE


def plannable(activity: str) -> bool:
    """这个活动名能不能被 Planner 当候选（必须有画像 + 有时长档 + 不在禁止集）。

    §三十三：候选必须是**已知**活动；6A §二十九：虚拟活动不许用 Minecraft 名字。
    """
    name = str(activity or "").strip().lower()
    if not name:
        return False
    if name not in ACTIVITY_PROFILES:
        return False
    return name in VIRTUAL_DURATIONS


def missing_profiles() -> frozenset[str]:
    """词表里没有画像的名字（测试用；空集 = 每个活动都说得出画像）。"""
    return frozenset(name for name in VIRTUAL_DURATIONS if name not in ACTIVITY_PROFILES)


# ---------------------------------------------------------------- 打分权重（§三十六）

#: 评分项的权重（**确定性加权求和**，§三十六"权重配置化"）。
#: 放在代码里的常量而不是 YAML —— §六十八 明确要求"不要增加几十个 tuning knobs"；
#: Planner 的构造函数可以把整份权重换掉（测试/管理员要用时），配置面只保留三个真旋钮。
#:
#: 能量与专注**分开计**（原本是一个 ``state_fit``）：§二十七-§二十九 要求"能量是硬的、
#: 专注是软的"，混成一项之后就没法从只读视图里看出专注到底有没有起作用（§五十八）。
#: 两者权重和 = 1.6（能量 0.6、专注 0.4 的拆分）。
SCORE_WEIGHTS: dict[str, float] = {
    "anchor_fit": 3.0,
    "routine_preference": 2.0,
    "energy_fit": 0.96,
    "focus_fit": 0.64,
    "goal_relevance": 1.2,
    "repetition_penalty": -1.8,
    "flexibility": 0.5,
    "time_period_fit": 1.0,
}

#: tie-break 顺序（§三十七）—— 分数打平时**固定**按这个顺序比，绝不随机挑一个。
#: 代码里的 ``_sort_key`` 就是照着这张表实现的（顺序一字不差）。
TIE_BREAK_ORDER: tuple[str, ...] = ("anchor_priority", "goal_priority", "routine_priority", "name")


def score_breakdown() -> dict[str, float]:
    """给 WebUI/测试看的权重快照（只读副本）。"""
    return dict(SCORE_WEIGHTS)
