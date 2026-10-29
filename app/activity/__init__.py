"""Phase 6A/6B/6C：世界活动（World Activity / Activity Episode / Activity Plan）。

```
World Clock（app/activity/clock.py）
      ↓
Routine + Schedule Anchors + Persistent Goals + History + Character State
      ↓
Activity Planner（确定性；§三）──→ Rolling Horizon ──→ ActivityPlan（**意图**，plan.py）
      ↓（6B 的 ActivityDecisionEngine 说"现在允不允许换"）
Activity Episode + ActivityRuntime（唯一权威："当前到底是什么"）
      ↓
CharacterState 投影（activity 只是 Episode 的派生快照）
      ↓
Behavior / Social Cognition / 聊天上下文
```

边界（写死在代码里的规则）：

* **绝不自主行动**：这个包不知道任何 Minecraft 工具，也不认识 Mineflayer；
  真实的 Minecraft 动作只能经 ``TaskRuntime → Policy → Confirmation → ActionRuntime`` 产生。
* **Plan ≠ Reality**（6C §五/§四十六）：``ActivityPlan`` 只是未来打算，现实永远只有 Episode；
  计划**不能**自己变成 Episode（必须过 6B 的护栏 + Runtime）。
* **Goal ≠ Task ≠ Activity**（6C §五十七）：持久目标只做排名加成（``goals.py``），
  既不创建任务也不创建动作。
* **Episode 是 context，不是 permission**（§五十）：Policy 不认识它。
* 失败只降级 Activity（§五十一），绝不阻断聊天/任务/启动；Planner 失败也绝不让活动变空
  （6C §四十八）。
"""

from __future__ import annotations

from app.activity.adapters import (
    MinecraftObservationAdapter,
    SandboxActivityAdapter,
    TaskActivityAdapter,
    UserInteractionAdapter,
)
from app.activity.anchors import (
    ANCHOR_PRIORITY_ORDER,
    DEFAULT_ANCHORS,
    AnchorBook,
    AnchorPhase,
    AnchorPriority,
    ScheduleAnchor,
    anchor_adherence,
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
from app.activity.goals import (
    GOAL_KIND_AFFINITY,
    GoalSnapshot,
    GoalStatus,
    NullGoalSource,
    PersistentGoal,
    SandboxGoalSource,
    build_goal,
)
from app.activity.model import (
    ACTIVITY_LABELS,
    ALLOWED_ACTIVITY_TRANSITIONS,
    LIVE_ACTIVITY_STATUSES,
    TERMINAL_ACTIVITY_STATUSES,
    VIRTUAL_DURATIONS,
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
from app.activity.model_advisor import (
    DEFAULT_TIMEOUT_MS,
    SYSTEM_PROMPT,
    ActivityDecisionProposal,
    ActivityModelAdvisor,
    ActivityModelReceipt,
    ModelAdvisorError,
    ModelFailureCode,
    StructuredModelProvider,
    advisory_cycle_key,
    build_advisor,
)
from app.activity.plan import (
    HARD_PLAN_TRIGGERS,
    ActivityPlan,
    Candidate,
    ItemReason,
    PlanItem,
    PlanStatus,
    PlanTrigger,
    RejectionReason,
    as_plan_trigger,
    plan_id_for,
)
from app.activity.planner import (
    FREE_ACTIVITY_POOL,
    ROUTINE_BY_PERIOD,
    ActivityDecision,
    ActivityPlanner,
    PlannerContext,
)
from app.activity.profiles import (
    ACTIVITY_PROFILES,
    LOW_ENERGY_EXEMPT,
    LOW_ENERGY_SAFE,
    LOW_ENERGY_THRESHOLD,
    SCORE_WEIGHTS,
    ActivityKind,
    ActivityProfile,
    profile_for,
)
from app.activity.projection import (
    ActivityProjection,
    activity_context_block,
    plan_context_block,
)
from app.activity.runtime import ActivityRuntime, state_signature
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
    "ACTIVITY_PROFILES",
    "ACTIVITY_RECOVERED",
    "ACTIVITY_SCHEDULED",
    "ACTIVITY_STARTED",
    "ALLOWED_ACTIVITY_TRANSITIONS",
    "ANCHOR_PRIORITY_ORDER",
    "DEFAULT_ANCHORS",
    "DEFAULT_TIMEOUT_MS",
    "DEFAULT_TIMEZONE",
    "FREE_ACTIVITY_POOL",
    "GOAL_KIND_AFFINITY",
    "HARD_PLAN_TRIGGERS",
    "LIVE_ACTIVITY_STATUSES",
    "LOW_ENERGY_EXEMPT",
    "LOW_ENERGY_SAFE",
    "LOW_ENERGY_THRESHOLD",
    "ROUTINE_BY_PERIOD",
    "SCORE_WEIGHTS",
    "TERMINAL_ACTIVITY_STATUSES",
    "VIRTUAL_DURATIONS",
    "ActivityConflict",
    "ActivityDecision",
    "ActivityEpisode",
    "ActivityError",
    "ActivityEventPublisher",
    "ActivityDecisionProposal",
    "ActivityKind",
    "ActivityModelAdvisor",
    "ActivityModelReceipt",
    "ActivityPlan",
    "ActivityPlanner",
    "ActivityProfile",
    "ActivityProjection",
    "ActivityRuntime",
    "ActivitySource",
    "ActivityStatus",
    "ActivityStore",
    "ActivityType",
    "AnchorBook",
    "AnchorPhase",
    "AnchorPriority",
    "Candidate",
    "FakeClock",
    "GoalSnapshot",
    "GoalStatus",
    "InMemoryActivityStore",
    "InvalidActivityTransition",
    "ItemReason",
    "MinecraftObservationAdapter",
    "ModelAdvisorError",
    "ModelFailureCode",
    "NullGoalSource",
    "PersistentGoal",
    "PlanItem",
    "PlanStatus",
    "PlanTrigger",
    "PlannerContext",
    "RejectionReason",
    "SandboxActivityAdapter",
    "SYSTEM_PROMPT",
    "SandboxGoalSource",
    "ScheduleAnchor",
    "SqliteActivityStore",
    "StructuredModelProvider",
    "TaskActivityAdapter",
    "TransitionReason",
    "UserInteractionAdapter",
    "WorldClock",
    "activity_context_block",
    "advisory_cycle_key",
    "build_advisor",
    "activity_label",
    "anchor_adherence",
    "as_plan_trigger",
    "build_goal",
    "duration_profile",
    "episode_id_for",
    "looks_like_minecraft_activity",
    "parse_episode_id",
    "plan_context_block",
    "plan_id_for",
    "profile_for",
    "state_signature",
]
