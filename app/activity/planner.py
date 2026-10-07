"""Phase 6A §十/§十一/§四十五：确定性 Activity Planner（"下一步可能是什么"）。

职责**只有**一件事：在**决策点**给出"接下来该是什么活动"。它**不**执行、**不**碰世界、
**不**调模型、**不**用随机（§四十五）。

决策点（§八）与谁负责：

| 决策点 | 谁决定 |
| --- | --- |
| Episode 到期 / 超过硬上限 | **本 Planner**（查时段表 → 延长或下一个） |
| 真实任务开始/结束/失败 | Task 适配器（source=TASK，Planner 不插手） |
| 沙盒里的虚拟生活换了动作 | 沙盒适配器（source=ROUTINE，它本来就是她的日程引擎） |
| 用户交互 / 世界事件 / 重启恢复 | 各自的适配器 |

也就是说：**当且仅当**没人决定时（她"空着"、Episode 到期、重启后没有任何 Episode），
这个 Planner 用一张确定的时段表给出下一个虚拟活动 —— 保证她的世界不会"卡死"。

三条硬约束：

* 绝不产生 Minecraft 活动名（§二十九：Minecraft 掉线/没任务时不许凭空造
  ``minecraft_exploring`` 这类活动）；
* 绝不用 ``random``（§四十五）；
* 延长有上限（§二十七：超过 ``max_duration`` 必须终结）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.activity.model import (
    VIRTUAL_DURATIONS,
    ActivityEpisode,
    ActivitySource,
    ActivityType,
    TransitionReason,
    duration_profile,
    looks_like_minecraft_activity,
)

#: 时段 → 候选虚拟活动（**确定性旋转**：按今天已排过的数量取模，不用随机）。
#: 词表刻意与**沙盒动作定义**一致（eating/gaming/reading/out/sleeping/…），
#: 这样"谁来创建 Episode"都不会让 ``CharacterState.activity`` 变成两套语言。
ROUTINE_BY_PERIOD: dict[str, tuple[str, ...]] = {
    "morning": ("eating", "reading", "out"),
    "afternoon": ("reading", "gaming", "out"),
    "evening": ("eating", "online", "idle"),
    "night": ("sleeping", "napping"),
}

#: 允许被延长一次的虚拟活动（§二十七 情况 B 的"evaluate extension"；不是所有活动都能续）
EXTENDABLE_ACTIVITIES = frozenset(
    {"reading", "gaming", "online", "out", "napping", "sleeping", "working"}
)

#: 一个 Episode 最多自动延长几次（保持确定性；超过就必须换或收尾）
MAX_AUTO_EXTENSIONS = 1


@dataclass(frozen=True)
class ActivityDecision:
    """Planner 的**唯一**输出形状（§十一）：下一步 / 原因 / 时长。"""

    action: str  # "extend" | "next" | "stop"
    reason: TransitionReason
    activity_name: str = ""
    activity_type: ActivityType = ActivityType.VIRTUAL_LIFE
    source: ActivitySource = ActivitySource.ROUTINE
    duration: tuple[float, float, float] | None = None

    @property
    def extend(self) -> bool:
        return self.action == "extend"

    def to_payload(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "reason": self.reason.value,
            "activity_name": self.activity_name,
            "activity_type": self.activity_type.value,
            "source": self.source.value,
            "duration": list(self.duration) if self.duration else None,
        }


class ActivityPlanner:
    """确定性日程表（无 LLM、无随机；同一输入永远同一输出）。"""

    def __init__(self, *, routine: dict[str, tuple[str, ...]] | None = None) -> None:
        table = dict(routine or ROUTINE_BY_PERIOD)
        # 边界守卫：日程表里**不允许**出现 Minecraft 活动名（§二十九）
        for period, names in table.items():
            for name in names:
                if looks_like_minecraft_activity(name):
                    raise ValueError(f"日程表里的虚拟活动不能是 Minecraft 活动：{period}/{name}")
        self._routine = table

    # ------------------------------------------------------------ 决策

    def initial(
        self, *, now: float, clock: Any, character_id: str = "", started_today: int = 0
    ) -> ActivityDecision:
        """她"空着"时的下一个活动（启动 / 重启后没有 Episode / Episode 收尾后无后续）。

        只按世界时钟的时段查表 + 今天已排数量做**确定性旋转**（同一输入永远同一输出）。
        """
        period = str(clock.period(now))
        name = self._pick(period, started_today=started_today, avoid="")
        return ActivityDecision(
            action="next",
            reason=TransitionReason.SCHEDULED,
            activity_name=name,
            duration=duration_profile(ActivityType.VIRTUAL_LIFE, name),
        )

    def next_after(
        self,
        episode: ActivityEpisode,
        *,
        now: float,
        clock: Any,
        started_today: int = 0,
    ) -> ActivityDecision:
        """一个 Episode 到期之后的决定：延长一次，还是换下一个，还是收尾。"""
        if self._can_extend(episode):
            return ActivityDecision(
                action="extend",
                reason=TransitionReason.TIME_EXPIRED,
                activity_name=episode.activity_name,
                activity_type=episode.activity_type,
                source=episode.source,
            )
        if (
            episode.source is not ActivitySource.ROUTINE
            and episode.source is not ActivitySource.RECOVERY
        ):
            # 任务 / 用户交互引起的活动结束后**不自动续摊**：让她回到自己的日常，
            # 由调度器下一次询问（这也是"绝不自主行动"的一部分）。
            period = str(clock.period(now))
            return ActivityDecision(
                action="next",
                reason=TransitionReason.TIME_EXPIRED,
                activity_name=self._pick(period, started_today=started_today, avoid=""),
                duration=duration_profile(ActivityType.VIRTUAL_LIFE, ""),
            )
        period = str(clock.period(now))
        name = self._pick(period, started_today=started_today, avoid=episode.activity_name)
        return ActivityDecision(
            action="next",
            reason=TransitionReason.TIME_EXPIRED,
            activity_name=name,
            duration=duration_profile(ActivityType.VIRTUAL_LIFE, name),
        )

    # ------------------------------------------------------------ 内部

    def _can_extend(self, episode: ActivityEpisode) -> bool:
        """能不能再延长一次：活动可延长 + 没超过次数上限 + 现在还没到硬上限。"""
        if str(episode.activity_name or "") not in EXTENDABLE_ACTIVITIES:
            return False
        if int(episode.extension_count or 0) >= MAX_AUTO_EXTENSIONS:
            return False
        return float(episode.typical_duration or 0.0) > 0.0

    def _pick(self, period: str, *, started_today: int, avoid: str) -> str:
        """确定性挑一个名字：按时段候选表 + 今天已排数量取模，避开刚做完的那个。"""
        candidates = self._routine.get(period) or ("idle",)
        index = int(max(0, started_today)) % len(candidates)
        name = candidates[index]
        if name == avoid and len(candidates) > 1:
            name = candidates[(index + 1) % len(candidates)]
        return name


#: 日程表里用到的名字必须有 duration 档（否则用默认档也能跑，但这里保证有据可查）
def known_routine_names() -> frozenset[str]:
    names: set[str] = set()
    for entries in ROUTINE_BY_PERIOD.values():
        names.update(entries)
    return frozenset(names)


def missing_durations() -> frozenset[str]:
    """日程表里没有时长档的名字（测试会用；空集 = 全部有据可查）。"""
    return frozenset(name for name in known_routine_names() if name not in VIRTUAL_DURATIONS)
