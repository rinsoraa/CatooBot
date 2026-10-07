"""Phase 6A：世界活动（World Activity / Activity Episode）。

```
World Clock（app/activity/clock.py）
      ↓
Activity Planner（确定性；只说"下一步可能是什么"）
      ↓
Activity Episode + ActivityRuntime（唯一权威："当前到底是什么"）
      ↓
CharacterState 投影（activity 只是 Episode 的派生快照）
      ↓
Behavior / Social Cognition / 聊天上下文
```

边界（写死在代码里的规则）：

* **绝不自主行动**：这个包不知道任何 Minecraft 工具，也不认识 Mineflayer；
  真实的 Minecraft 动作只能经 ``TaskRuntime → Policy → Confirmation → ActionRuntime`` 产生。
* **Episode 不等于 Task**（§十八）：只带 ``related_task_id`` 引用。
* **Episode 是 context，不是 permission**（§五十）：Policy 不认识它。
* 失败只降级 Activity（§五十一），绝不阻断聊天/任务/启动。
"""

from __future__ import annotations

from app.activity.adapters import (
    MinecraftObservationAdapter,
    SandboxActivityAdapter,
    TaskActivityAdapter,
    UserInteractionAdapter,
)
from app.activity.clock import DEFAULT_TIMEZONE, FakeClock, WorldClock
from app.activity.events import (
    ACTIVITY_CANCELLED,
    ACTIVITY_COMPLETED,
    ACTIVITY_EVENT_NAMES,
    ACTIVITY_EXPIRED,
    ACTIVITY_EXTENDED,
    ACTIVITY_INTERRUPTED,
    ACTIVITY_RECOVERED,
    ACTIVITY_SCHEDULED,
    ACTIVITY_STARTED,
    ActivityEventPublisher,
)
from app.activity.model import (
    ACTIVITY_LABELS,
    ALLOWED_ACTIVITY_TRANSITIONS,
    LIVE_ACTIVITY_STATUSES,
    TERMINAL_ACTIVITY_STATUSES,
    ActivityEpisode,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    TransitionReason,
    activity_label,
    duration_profile,
    episode_id_for,
    looks_like_minecraft_activity,
    parse_episode_id,
)
from app.activity.planner import ROUTINE_BY_PERIOD, ActivityDecision, ActivityPlanner
from app.activity.projection import ActivityProjection, activity_context_block
from app.activity.runtime import ActivityRuntime
from app.activity.store import (
    ActivityConflict,
    ActivityError,
    ActivityStore,
    InMemoryActivityStore,
    InvalidActivityTransition,
    SqliteActivityStore,
)

__all__ = [
    "ACTIVITY_CANCELLED",
    "ACTIVITY_COMPLETED",
    "ACTIVITY_EVENT_NAMES",
    "ACTIVITY_EXPIRED",
    "ACTIVITY_EXTENDED",
    "ACTIVITY_INTERRUPTED",
    "ACTIVITY_LABELS",
    "ACTIVITY_RECOVERED",
    "ACTIVITY_SCHEDULED",
    "ACTIVITY_STARTED",
    "ALLOWED_ACTIVITY_TRANSITIONS",
    "DEFAULT_TIMEZONE",
    "LIVE_ACTIVITY_STATUSES",
    "ROUTINE_BY_PERIOD",
    "TERMINAL_ACTIVITY_STATUSES",
    "ActivityConflict",
    "ActivityDecision",
    "ActivityEpisode",
    "ActivityError",
    "ActivityEventPublisher",
    "ActivityPlanner",
    "ActivityProjection",
    "ActivityRuntime",
    "ActivitySource",
    "ActivityStatus",
    "ActivityStore",
    "ActivityType",
    "FakeClock",
    "InMemoryActivityStore",
    "InvalidActivityTransition",
    "MinecraftObservationAdapter",
    "SandboxActivityAdapter",
    "SqliteActivityStore",
    "TaskActivityAdapter",
    "TransitionReason",
    "UserInteractionAdapter",
    "WorldClock",
    "activity_context_block",
    "activity_label",
    "duration_profile",
    "episode_id_for",
    "looks_like_minecraft_activity",
    "parse_episode_id",
]
