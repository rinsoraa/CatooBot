"""Phase 6A §九：ActivityRuntime —— "当前 Episode 到底是什么"的唯一权威。

职责（§九）：

```
start episode / get current episode / advance time / extend / complete /
interrupt / cancel / expire / recover
```

**不负责**：LLM 生成、QQ 消息、Minecraft 工具执行、Memory 检索、Policy 授权（§九/§十七/§二十）。
世界动作永远只能由 ``TaskRuntime → Policy → Confirmation → Agent Bridge → MinecraftService``
产生；这里连 Minecraft 的写 API 都不 import（有源码级 guard 测试守着，§四十九）。

关键不变量：

* **§十二/§二十六**：每个角色同一时间最多一个 primary（live）Episode；
* **§六/§四十四**：状态转移走显式状态机 + compare-and-set，两个事件同时收尾只会有一次最终转移；
* **§八**：世界 tick 只推进时间和生命周期 —— 绝不"每分钟重新决定她在干什么"；
* **§二十七/§二十八**：重启后按时间对账；**绝不伪造离线期间的活动**；
* **§三十三**：一次性转移的事件幂等（重启/重试不会重复发布）；
* **§五十一**：任何失败只降级 Activity，绝不把异常冒泡到聊天/任务/启动。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from typing import Any

from app.activity.decision import (
    ActivityDecisionEngine,
    DecisionReason,
    DecisionTrigger,
    WorldConsistencyChecker,
    as_trigger,
    decision_reason_for_transition,
    merge_adjacent,
)
from app.activity.events import (
    ACTIVITY_RECOVERED,
    ACTIVITY_SCHEDULED,
    EVENT_BY_STATUS,
    ActivityEventPublisher,
)
from app.activity.goals import GoalSnapshot
from app.activity.model import (
    TASK_STATE_OUTCOME,
    ActivityEpisode,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    TransitionReason,
    build_episode,
    duration_profile,
    looks_like_minecraft_activity,
)
from app.activity.plan import (
    HARD_PLAN_TRIGGERS,
    ActivityPlan,
    ItemReason,
    PlanItem,
    PlanTrigger,
    as_plan_trigger,
)
from app.activity.planner import (
    FALLBACK_ACTIVITIES,
    ActivityDecision,
    ActivityPlanner,
    PlannerContext,
)
from app.activity.projection import ActivityProjection
from app.activity.store import (
    ActivityConflict,
    ActivityError,
    InvalidActivityTransition,
    reason_value,
)

#: 观察缓冲写盘的默认间隔（§二十三：禁止每秒写数据库；30~60 秒一次）
DEFAULT_PERSISTENCE_INTERVAL_SECONDS = 60.0

#: rolling horizon 的默认长度（§六：4 小时；真实取值由配置注入）
DEFAULT_PLANNING_HORIZON_SECONDS = 240 * 60.0

#: 两次"软触发"重新规划之间的最短间隔（§九：默认 5 分钟；真实取值由配置注入）
DEFAULT_PLANNER_REFRESH_MIN_SECONDS = 5 * 60.0

#: horizon 里最多排几条（§六十八：默认 6，绝不排满一整天）
DEFAULT_MAX_FUTURE_EPISODES = 6

#: 计划"快耗尽"的判定余量：覆盖不到这么久之后就重新规划（§八 触发点 2）
PLAN_COVERAGE_LEAD_SECONDS = 10 * 60.0

#: Phase 6C.1 §三：continuation 边界与现实的容差（秒）。
#: 小于它就认为"已经对齐"—— 免得浮点/取整差异被当成"计划脏了"。
RECONCILE_TOLERANCE_SECONDS = 1.0

#: 状态签名只看这几个字段（Planner 真正用到的输入）
STATE_SIGNATURE_FIELDS: tuple[str, ...] = (
    "energy",
    "current_focus",
    "mood",
    "schedule_state",
    "social_state",
)


def state_signature(state: Any) -> str:
    """角色状态的**内容签名**（§八 触发点 5 靠它判"状态是不是真的变了"）。

    连续量（能量/专注）四舍五入到 0.01 再签名 —— 否则每 tick 的一点点抖动都会
    被当成"重大变化"，那就等于每个 tick 都重新规划（§九 明确禁止）。
    """
    if state is None:
        return ""
    payload: dict[str, Any] = {}
    for name in STATE_SIGNATURE_FIELDS:
        if isinstance(state, dict):
            value = state.get(name, "")
        else:
            value = getattr(state, name, "")
        if isinstance(value, int | float) and not isinstance(value, bool):
            value = round(float(value), 2)
        payload[name] = value
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class ActivityRuntime:
    """Episode 生命周期的唯一权威（不认识 Minecraft，也不产生任何世界动作）。"""

    def __init__(
        self,
        *,
        store: Any,
        clock: Any,
        character_id: str = "default",
        planner: ActivityPlanner | None = None,
        publisher: ActivityEventPublisher | None = None,
        projection: ActivityProjection | None = None,
        recent_episode_limit: int = 5,
        persistence_interval_seconds: float = DEFAULT_PERSISTENCE_INTERVAL_SECONDS,
        recovery_grace_seconds: float = 0.0,
        task_state_probe: Any = None,
        # ---- Phase 6B：决策引擎的三个旋钮（§五十三）
        transition_window_seconds: float = 300.0,
        max_extensions_per_episode: int = 2,
        bounce_cooldown_seconds: float = 600.0,
        decision_engine: Any = None,
        # ---- Phase 6C：rolling horizon 的三个旋钮（§六十八）
        planning_horizon_seconds: float = DEFAULT_PLANNING_HORIZON_SECONDS,
        planner_refresh_min_seconds: float = DEFAULT_PLANNER_REFRESH_MIN_SECONDS,
        max_future_episodes: int = DEFAULT_MAX_FUTURE_EPISODES,
        plan_store: Any = None,
        state_provider: Any = None,
        goal_source: Any = None,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.character_id = str(character_id or "default")
        self.planner = planner or ActivityPlanner()
        self._log = logger
        self.publisher = publisher or ActivityEventPublisher(logger=logger)
        self.projection = projection
        self.recent_episode_limit = max(1, int(recent_episode_limit))
        self.persistence_interval_seconds = max(0.0, float(persistence_interval_seconds))
        #: 重启宽限（§五十三）：计划结束时间过了这么久之内都不算"已过期"，
        #: 免得每次重启都因为差几秒而强行转移。
        self.recovery_grace_seconds = max(0.0, float(recovery_grace_seconds))
        #: **只读**的任务状态探针（``async def probe(task_id) -> "PAUSED"|"SUCCEEDED"|…``）：
        #: 重启对账时用它把活动与任务权威状态对齐（§四十二：不允许每个模块自己解释）。
        self.task_state_probe = task_state_probe
        #: Phase 6B：规则优先的决策引擎（§二四 的流水线；不认识任何世界写 API）
        self.engine = decision_engine or ActivityDecisionEngine(
            clock=clock,
            planner=self.planner,
            transition_window_seconds=transition_window_seconds,
            max_extensions=max_extensions_per_episode,
            bounce_cooldown_seconds=bounce_cooldown_seconds,
            logger=logger,
        )
        #: 一致性检查器（只报不修，§二一）；status()/recover() 都会跑一次并如实展示
        self.checker = WorldConsistencyChecker()
        self.last_consistency: dict[str, Any] = {}
        #: 写操作串行化（同一个进程里绝不让两次转移交错）
        self._lock = asyncio.Lock()
        #: 观察的写盘节流（§二十三）
        self._observation_buffer: dict[str, Any] = {}
        self._observation_flushed_at = 0.0
        #: 最近一次失败原因（WebUI/日志如实显示"Activity 降级"）
        self.degraded_reason = ""
        #: 观测到的世界事实（只读；用于判断"这个 Episode 还合理吗"，§十六）
        self.last_observation: dict[str, Any] = {}
        # ---- Phase 6C：rolling horizon（§六/§八/§九/§四十七）
        #: 计划存储（默认就用 episode 那个 store —— 它已经实现了计划接口）
        self.plan_store = plan_store if plan_store is not None else store
        self.planning_horizon_seconds = max(60.0, float(planning_horizon_seconds))
        self.planner_refresh_min_seconds = max(0.0, float(planner_refresh_min_seconds))
        self.max_future_episodes = max(1, int(max_future_episodes))
        #: 同步的只读角色状态（``() -> CharacterState``）—— Planner 的能量/专注/时段输入
        self.state_provider = state_provider
        #: 只读目标来源（缺省沿用 Planner 上的那个）
        if goal_source is not None:
            self.planner.goal_source = goal_source
        #: 生效计划的**内存缓存**（§六十五：普通 tick 不做数据库/全表扫描）
        self._active_plan: ActivityPlan | None = None
        self._last_planned_at = 0.0
        self._plan_signature = ""
        self._plan_refresh_count = 0
        self.last_plan_result: dict[str, Any] = {}
        #: Phase 6C.1 §七：计划是否"脏"（continuation 边界与现实不一致）。
        #: 这是**派生**缓存（由 ``plan_dirty()`` 现算后写进这里），不是新的持久化状态。
        self._plan_dirty = False

    # ------------------------------------------------------------ 读

    async def current(self) -> ActivityEpisode | None:
        try:
            return await self.store.live(self.character_id)
        except Exception as exc:  # noqa: BLE001 - 读失败只降级（§五十一）
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[World.Activity] 读取当前 Episode 失败（降级）：%s", exc)
            return None

    async def recent(self, limit: int | None = None) -> list[ActivityEpisode]:
        try:
            return await self.store.recent(
                self.character_id, limit if limit is not None else self.recent_episode_limit
            )
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            return []

    async def recent_transitions(self, episode_id: str, limit: int = 3) -> list[dict[str, Any]]:
        try:
            return await self.store.recent_transitions(episode_id, limit)
        except Exception:  # noqa: BLE001 - 上下文不是关键路径
            return []

    async def context_block(self) -> str:
        """给一次 LLM turn 的"她正在做什么"（预算：1 条 + ≤3 条转移，§四十）。"""
        from app.activity.projection import activity_context_block

        episode = await self.current()
        if episode is None:
            return ""
        transitions = await self.recent_transitions(episode.episode_id)
        return activity_context_block(
            episode,
            transitions,
            related_task=episode.related_task_id,
            clock=self.clock,
        )

    async def status(self) -> dict[str, Any]:
        """WebUI/API 的只读投影（含最近 Episode、决策视图与计划，§三六/§四七/§四八/§五八）。"""
        episode = await self.current()
        recent = await self.recent(self.recent_episode_limit)
        now = self._now(None)
        report = self.run_consistency_check(episode, now=now)
        # §七：只读视图里的 dirty 要现算（它是派生的，不落盘）
        self.plan_dirty(episode)
        return {
            "enabled": True,
            "character_id": self.character_id,
            "degraded": self.degraded_reason,
            "current": episode.to_payload() if episode else None,
            "recent": [item.to_payload() for item in recent],
            "last_observation": dict(self.last_observation),
            "context_budget": {"current_episode": 1, "recent_transitions": 3},
            # Phase 6B：决策只读视图（**没有**任何 force/extend/cancel 入口）
            "decision": self.decision_view(episode, now=now),
            "consistency": report,
            # Phase 6C：计划只读视图（§五十八：同样**没有** force select）
            "plan": self.plan_view(now=now),
            # 展示层时间线：按"从旧到新"给（recent 本身是新→旧），原始 id 全保留
            "merged_timeline": merge_adjacent(list(reversed(recent))),
        }

    def run_consistency_check(
        self, episode: ActivityEpisode | None, *, now: float | None = None
    ) -> dict[str, Any]:
        """跑一次一致性检查并记在案（只读；异常只报告，绝不自动修，§二一）。"""
        moment = self._now(now)
        live = [episode] if episode is not None else []
        report = self.checker.check(current=episode, now=moment, live=live)
        self.last_consistency = report.to_payload()
        if not report.ok and self._log is not None:
            self._log.warning(
                "[World.Activity] 一致性检查报错（只报告，不自动修）：%s",
                [item.get("rule") for item in report.errors],
            )
        return self.last_consistency

    def decision_view(
        self, episode: ActivityEpisode | None, *, now: float | None = None
    ) -> dict[str, Any]:
        """决策只读视图（§四八 的形状：decision/reason/transition_pending/elapsed/…）。"""
        return self.engine.view(episode, now=self._now(now))

    @property
    def transition_pending(self) -> bool:
        """是否已经进入 transition window（**只是准备**，不代表马上切活动，§十一）。"""
        return bool(self.last_decision_view.get("transition_pending"))

    async def refresh_decision_view(self) -> dict[str, Any]:
        episode = await self.current()
        self.last_decision_view = self.decision_view(episode, now=self._now(None))
        return self.last_decision_view

    # ------------------------------------------------------------ 计划（Phase 6C）

    async def load_plan(self) -> ActivityPlan | None:
        """读出生效计划（进程内缓存；读失败只降级，§四十八）。"""
        try:
            plan = await self.plan_store.active_plan(self.character_id)
        except Exception as exc:  # noqa: BLE001 - 计划读不出来不影响"她在做什么"
            self.degraded_reason = f"plan_{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[World.Activity] 读取生效计划失败（降级）：%s", exc)
            return None
        self._active_plan = plan
        if plan is not None:
            self._last_planned_at = max(self._last_planned_at, float(plan.generated_at or 0.0))
        return plan

    @property
    def active_plan(self) -> ActivityPlan | None:
        """内存里的生效计划（普通 tick 只读它，不查库，§六十五）。"""
        return self._active_plan

    async def refresh_plan(
        self,
        *,
        trigger: Any = PlanTrigger.MANUAL,
        now: float | None = None,
        force: bool = False,
        current: ActivityEpisode | None = None,
    ) -> dict[str, Any]:
        """重新规划 rolling horizon（§八 的触发点；**普通 tick 绝不调它**）。

        四道门，按成本从低到高排：

        1. **冷却**（§九）：软触发在 ``planner_refresh_min_seconds`` 之内直接跳过；
        2. **内容签名**（§四十三）：和生效计划一模一样 → 不生成新版本、不写库；
        3. **真的规划**：纯计算（≤6 候选 × ≤6 条目），不碰世界、不调模型；
        4. **落盘**：一个事务里把旧计划标 SUPERSEDED + 新计划标 ACTIVE_PLAN（§四十四/§六十七）。

        任何一步失败都只降级 —— 计划没了顶多是"没预习"，绝不能影响她正在做的事（§四十八）。
        """
        moment = self._now(now)
        trigger_enum = as_plan_trigger(trigger)
        if not force and trigger_enum not in HARD_PLAN_TRIGGERS:
            since_last = moment - self._last_planned_at if self._last_planned_at else 0.0
            if self._last_planned_at and since_last < self.planner_refresh_min_seconds:
                return self._plan_result(
                    refreshed=False,
                    reason="cooldown",
                    trigger=trigger_enum,
                    seconds_since_last=since_last,
                )
        episode = current if current is not None else await self.current()
        state = self._state()
        try:
            plan = self.planner.plan_next(
                state,
                episode,
                now=moment,
                horizon=self.planning_horizon_seconds,
                context=PlannerContext(
                    clock=self.clock,
                    now=moment,
                    character_state=state,
                    current_episode=episode,
                    history=tuple(await self.recent(max(self.recent_episode_limit, 6))),
                    goals=self._goals(),
                    anchors=self.planner.anchors,
                    started_today=await self._started_today(moment),
                    horizon_seconds=self.planning_horizon_seconds,
                    max_items=self.max_future_episodes,
                    trigger=trigger_enum.value,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - §四十八：Planner 失败只降级
            self.degraded_reason = "planner_failed"
            if self._log is not None:
                self._log.warning("[World.Activity] 规划失败（保留现有活动与旧计划）：%s", exc)
            return self._plan_result(refreshed=False, reason="planner_failed", trigger=trigger_enum)
        previous = self._active_plan
        signature = plan.content_signature()
        if (
            previous is not None
            and previous.content_hash == signature
            and not previous.stale(moment)
        ):
            self._last_planned_at = moment
            return self._plan_result(
                refreshed=False,
                reason="unchanged",
                trigger=trigger_enum,
                plan_id=previous.plan_id,
                plan_version=int(previous.plan_version),
            )
        result = await self._persist_plan(
            plan,
            trigger=trigger_enum,
            now=moment,
            previous=previous,
            reason="planned",
            log_label="重新规划",
        )
        # 整份重排过 → 计划与现实重新对齐（§七 的 dirty 清掉；它是派生缓存，不是持久状态）
        self._plan_dirty = False
        result["dirty"] = False
        return result

    def _plan_result(
        self,
        *,
        refreshed: bool,
        reason: str,
        trigger: PlanTrigger,
        plan_id: str = "",
        plan_version: int = 0,
        seconds_since_last: float = 0.0,
    ) -> dict[str, Any]:
        result = {
            "refreshed": bool(refreshed),
            "reason": str(reason),
            "trigger": trigger.value,
            "plan_id": str(plan_id),
            "plan_version": int(plan_version),
            "seconds_since_last": round(float(seconds_since_last), 1),
            "refreshed_at": self._now(None),
        }
        self.last_plan_result = result
        return result

    async def _maybe_refresh_plan(
        self, *, now: float, current: ActivityEpisode | None = None
    ) -> dict[str, Any]:
        """普通 tick 的**廉价检查**（§六十五）：只有真该规划时才规划。

        三个条件（全部只看内存 + 一次只读状态/目标快照，无数据库、无全表扫描）：

        1. 计划不存在或已经过期 / 快覆盖不到了（§八 触发点 2）；
        2. 目标变了（§八 触发点 4）；
        3. 角色状态变了（§八 触发点 5）。

        冷却（§九）挡在真正规划之前 —— 所以"每分钟重算整段 horizon"不会发生。
        """
        moment = float(now)
        plan = self._active_plan
        exhausted = plan is None or plan.stale(moment) or self._plan_coverage_left(moment) <= 0.0
        # Phase 6C.1 §六/§七：延长把计划弄脏了（continuation 边界与现实不一致）→ 也是一个触发点，
        # 但**软**的：``refresh_plan`` 会先过冷却；冷却没到就保持 dirty，等下一次合法时机。
        dirty = self.plan_dirty(current)
        signature = self._context_signature()
        changed = bool(signature) and signature != self._plan_signature
        if not exhausted and not dirty and not changed:
            return {"refreshed": False, "reason": "not_needed", "trigger": ""}
        if exhausted:
            trigger = PlanTrigger.PLAN_EXHAUSTED
        elif dirty:
            trigger = PlanTrigger.EPISODE_EXTENDED
        else:
            trigger = PlanTrigger.STATE_CHANGED
        result = await self.refresh_plan(trigger=trigger, now=moment, current=current)
        if result.get("refreshed"):
            self._plan_signature = signature
        return result

    def _plan_coverage_left(self, now: float) -> float:
        """现有计划还能覆盖多久（秒）。没有计划 / 已经过期就是 0。"""
        plan = self._active_plan
        if plan is None or plan.stale(now):
            return 0.0
        farthest = max((float(item.planned_end) for item in plan.items), default=0.0)
        return max(0.0, farthest - float(now) - PLAN_COVERAGE_LEAD_SECONDS)

    def _context_signature(self) -> str:
        """(目标 + 状态 + 时段) 的内容签名 —— 变了才值得重新规划（§八 触发点 4/5）。"""
        goals = self._goals()
        payload = {
            "goals": goals.signature() if goals is not None else "",
            "state": state_signature(self._state()),
            "period": str(self.clock.period(self._now(None))),
        }
        blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]

    def _state(self) -> Any:
        """同步读一次角色状态（只读；失败返回 None，Planner 会当中性状态）。"""
        provider = self.state_provider or getattr(self.planner, "state_provider", None)
        if provider is None:
            return None
        try:
            return provider()
        except Exception:  # noqa: BLE001 - 状态读不到不该影响规划
            return None

    def _goals(self) -> GoalSnapshot | None:
        try:
            return self.planner.goals_snapshot()
        except Exception:  # noqa: BLE001
            return GoalSnapshot(source="error", degraded_reason="snapshot_failed")

    def plan_view(self, *, now: float | None = None) -> dict[str, Any]:
        """计划只读视图（§五十八 的字段：Current / Candidates / Rejected / Scores /
        Constraints / Goal Relevance / Routine Preference / Anchor / Selected /
        Plan Horizon / Plan Version —— **没有**思维链，也**没有** force select）。"""
        moment = self._now(now)
        plan = self._active_plan
        base: dict[str, Any] = {
            "enabled": plan is not None,
            # Phase 6C.1 §七：计划是否已经与现实脱节（等冷却时会是 True）
            "dirty": bool(self._plan_dirty),
            "planning_horizon_seconds": self.planning_horizon_seconds,
            "refresh_min_seconds": self.planner_refresh_min_seconds,
            "max_future_episodes": self.max_future_episodes,
            "refresh_count": self._plan_refresh_count,
            "last_refresh_at": self._last_planned_at,
            "last_result": dict(self.last_plan_result),
        }
        if plan is None:
            # 形态统一：没有计划时这些字段是**空**，而不是缺席 —— 前端/QQ 都不必判 undefined
            base.update(
                {
                    "plan": None,
                    "next": None,
                    "current_item": None,
                    "upcoming": [],
                    "candidates": [],
                    "rejected": [],
                    "selected": None,
                    "anchors": [
                        anchor.to_payload(self.clock, moment)
                        for anchor in self.planner.anchors.all()
                    ],
                    "goals": None,
                    "stale": True,
                    "coverage_left_seconds": 0.0,
                    "seconds_since_last_refresh": 0.0,
                }
            )
            return base
        covering = plan.covers(moment)
        nxt = plan.next_item(moment)
        selected = plan.selected()
        goals = self._goals()
        base.update(
            {
                "plan": plan.to_payload(),
                "plan_id": plan.plan_id,
                "plan_version": int(plan.plan_version),
                "status": plan.status.value,
                "horizon_start": float(plan.horizon_start),
                "horizon_end": float(plan.horizon_end),
                "source": plan.source,
                "trigger": plan.trigger,
                "current_item": covering.to_payload() if covering is not None else None,
                "next": nxt.to_payload() if nxt is not None else None,
                "upcoming": [item.to_payload() for item in plan.upcoming(moment, 3)],
                "candidates": [item.to_payload() for item in plan.eligible()],
                "rejected": [item.to_payload() for item in plan.rejected()],
                "selected": selected.to_payload() if selected is not None else None,
                "anchors": [
                    anchor.to_payload(self.clock, moment) for anchor in self.planner.anchors.all()
                ],
                "goals": goals.to_payload() if goals is not None else None,
                "stale": plan.stale(moment),
                "coverage_left_seconds": round(self._plan_coverage_left(moment), 1),
                "seconds_since_last_refresh": (
                    round(moment - self._last_planned_at, 1) if self._last_planned_at else 0.0
                ),
            }
        )
        return base

    async def plan_context_block(self) -> str:
        """给一次 LLM turn 的"接下来打算做什么"（§五十九：计划，不是现状）。"""
        from app.activity.projection import plan_context_block as build_block

        plan = self._active_plan
        if plan is None:
            return ""
        episode = await self.current()
        return build_block(plan, episode=episode, now=self._now(None), clock=self.clock)

    # ------------------------------------------------------------ 6C.1：Episode 延长后的计划对齐

    def plan_dirty(self, episode: ActivityEpisode | None) -> bool:
        """计划是否**已经与现实脱节**（§七：派生判断，不新增持久化状态）。

        判据只有一条：**生效计划的第一条**是否还与当前 Episode 对得上 ——

        * 计划里第一条的活动 ≠ 她现在做的事 → 脏；
        * 活动一样，但第一条的结束时间 ≠ 她现实的 ``planned_end_at`` → 脏
          （**这正是 EXTEND 的情形**，§一）。

        没有 Episode（她空着）时无所谓脏不脏：下一次 ``_plan_next`` 会整份重排。
        结果会缓存到 ``self._plan_dirty``（给只读视图用），但每次调用都**现算**。
        """
        plan = self._active_plan
        dirty = False
        if episode is not None and plan is not None and plan.items:
            first = plan.first_item()
            if first is None:
                dirty = False
            elif str(first.activity) != str(episode.activity_name):
                dirty = True
            else:
                reality = float(episode.planned_end_at or 0.0)
                dirty = bool(reality) and (
                    abs(reality - float(first.planned_end)) > RECONCILE_TOLERANCE_SECONDS
                )
        self._plan_dirty = dirty
        return dirty

    async def reconcile_plan(
        self,
        *,
        now: float | None = None,
        current: ActivityEpisode | None = None,
    ) -> dict[str, Any]:
        """Episode 被 6B 延长之后，把未来的计划与现实对齐（6C.1 §二/§三/§四）。

        两条路，**都不是**"再决策一次"（§十一：6B 已经做完了那个决定）：

        * **Strategy A（§三）**：计划第一条就是"现实延续"（``CONTINUATION`` 且活动名一致），
          而且后续条目不会被新的结束时间压住 → **只**更新这一条的时间边界，
          不动任何后续活动的身份、不重算任何候选；
        * **冲突（§四）或形状对不上** → 作废当前计划、走一次受控 replan
          （``trigger=EPISODE_EXTENDED``，**软**触发：冷却没到就标 dirty，等下一次合法时机）。

        期间当前 Episode **一个字都不改**（§十二），也**不会**提前开下一个活动（§十三）。
        """
        moment = self._now(now)
        episode = current if current is not None else await self.current()
        plan = self._active_plan
        if episode is None or plan is None or not plan.items:
            self._plan_dirty = False
            return {"action": "no_plan", "plan_id": "", "reason": "no_active_plan"}
        first = plan.first_item()
        new_end = float(episode.planned_end_at or 0.0)
        if (
            first is not None
            and first.reason == ItemReason.CONTINUATION.value
            and str(first.activity) == str(episode.activity_name)
            and abs(new_end - float(first.planned_end)) <= RECONCILE_TOLERANCE_SECONDS
        ):
            # 已经对齐（例如同一个 Episode 被延长两次之间没有别的变化）
            self._plan_dirty = False
            return {"action": "consistent", "plan_id": plan.plan_id, "reason": "already_aligned"}
        conflict = self._continuation_conflict(plan, episode, new_end=new_end)
        aligned_shape = (
            first is not None
            and first.reason == ItemReason.CONTINUATION.value
            and str(first.activity) == str(episode.activity_name)
            and bool(new_end)
        )
        if not aligned_shape or conflict:
            # §四：不硬推时间线 —— 交给一次受控 replan（冷却没到就先标 dirty）
            result = await self.refresh_plan(trigger=PlanTrigger.EPISODE_EXTENDED, now=moment)
            self._plan_dirty = not bool(result.get("refreshed"))
            result["action"] = "replanned" if result.get("refreshed") else "dirty"
            result["reason_code"] = "conflict" if conflict else "not_a_continuation"
            self.last_plan_result = result
            if self._log is not None:
                self._log.info(
                    "[World.Activity] 计划对齐：%s（trigger=episode_extended）",
                    "已重排" if result.get("refreshed") else "标记 dirty（等冷却）",
                )
            return result
        # Strategy A：只改 continuation 的边界（起始 = 现实开始，结束 = 现实新的结束）
        updated = self._reconciled_plan(plan, episode, now=moment)
        result = await self._persist_plan(
            updated,
            trigger=PlanTrigger.EPISODE_EXTENDED,
            now=moment,
            previous=plan,
            reason="reconciled",
            log_label="计划对齐",
        )
        self._plan_dirty = False
        result["action"] = "reconciled"
        result["reason_code"] = "continuation_boundary"
        return result

    def _continuation_conflict(
        self, plan: ActivityPlan, episode: ActivityEpisode, *, new_end: float
    ) -> bool:
        """后续条目会不会被"新的现实结束时间"压住（§四）。

        只要有一条后续计划项的起点比新的结束时间还早，就不可能"只挪一下边界"了 ——
        那段时间已经被现实占掉。此时必须作废重排，而不是把后续项硬推（§四）。
        """
        for item in plan.items[1:]:
            if float(item.planned_start) < float(new_end) - RECONCILE_TOLERANCE_SECONDS:
                return True
        return False

    def _reconciled_plan(
        self, plan: ActivityPlan, episode: ActivityEpisode, *, now: float
    ) -> ActivityPlan:
        """Strategy A 的产物：**只有**第一条的时间边界变了，其余原样（§三）。

        刻意新建对象而不是就地改：旧计划要原封不动地留在历史里（§九）。
        """
        items = list(plan.items)
        first = items[0]
        items[0] = PlanItem(
            activity=first.activity,
            planned_start=float(episode.started_at or first.planned_start),
            planned_end=float(episode.planned_end_at or first.planned_end),
            reason=first.reason,
            priority=first.priority,
            anchor_id=first.anchor_id,
            goal_id=first.goal_id,
            score=first.score,
        )
        return ActivityPlan(
            plan_id="",
            character_id=self.character_id,
            plan_version=int(plan.plan_version),
            generated_at=float(now),
            horizon_start=float(plan.horizon_start),
            horizon_end=float(plan.horizon_end),
            status=plan.status,
            items=tuple(items),
            candidates=tuple(plan.candidates),
            constraints=dict(plan.constraints),
            source=plan.source,
            trigger=PlanTrigger.EPISODE_EXTENDED.value,
        )

    async def _persist_plan(
        self,
        plan: ActivityPlan,
        *,
        trigger: PlanTrigger,
        now: float,
        previous: ActivityPlan | None,
        reason: str,
        log_label: str = "计划落盘",
    ) -> dict[str, Any]:
        """把一份**已经算好**的计划落盘并接管为生效计划（§九/§六十七：一个事务里作废旧计划）。

        ``refresh_plan``（整份重新规划）与 ``reconcile_plan``（只对齐一条边界）共用这条尾巴：
        计划号由存储层分配、版本 = 上一版 + 1、旧计划标 SUPERSEDED 且**永不删除**。
        写失败只降级（计划仍在内存里用，如实记 ``degraded_reason``）。
        """
        plan.character_id = self.character_id
        plan.trigger = trigger.value
        plan.content_hash = plan.content_signature()
        try:
            plan.plan_id = await self.plan_store.next_plan_id(str(self.clock.day_key(now)))
            plan.plan_version = (int(previous.plan_version) + 1) if previous is not None else 1
            await self.plan_store.create_plan(plan)
        except Exception as exc:  # noqa: BLE001 - 落盘失败也只在内存里用（只降级）
            self.degraded_reason = f"plan_{type(exc).__name__}"
            if self._log is not None:
                self._log.warning("[World.Activity] 计划落盘失败（仅在内存中使用）：%s", exc)
        self._active_plan = plan
        self._last_planned_at = float(now)
        self._plan_refresh_count += 1
        if self._log is not None:
            self._log.info(
                "[World.Activity] %s plan=%s v%s trigger=%s 条目=%d 覆盖到 %s",
                log_label,
                plan.plan_id or "(未落盘)",
                plan.plan_version,
                trigger.value,
                len(plan.items),
                self.clock.isoformat(plan.horizon_end),
            )
        return self._plan_result(
            refreshed=True,
            reason=reason,
            trigger=trigger,
            plan_id=plan.plan_id,
            plan_version=int(plan.plan_version),
        )

    # ------------------------------------------------------------ 写：创建 / 转移

    async def start(
        self,
        *,
        activity_name: str,
        activity_type: ActivityType = ActivityType.VIRTUAL_LIFE,
        source: ActivitySource = ActivitySource.ROUTINE,
        location: str = "",
        social_state: str = "alone",
        tags: list[str] | None = None,
        related_task_id: str = "",
        parent_episode_id: str = "",
        duration: tuple[float, float, float] | None = None,
        now: float | None = None,
        scheduled: bool = False,
        activation_reason: TransitionReason | str | None = None,
    ) -> ActivityEpisode | None:
        """开始一个 Episode（默认直接 ACTIVE；``scheduled=True`` 时先 SCHEDULED）。

        ``activation_reason`` 说清"**这次开始**是因为什么"（§三十一）：从 SCHEDULED 到 ACTIVE
        的转移原因由调用方决定（沙盒换活动 = WORLD_EVENT、任务开始 = TASK_STARTED…），
        不写死成 SCHEDULED —— 否则审计里全是"按计划开始"，看不出真实起因。

        ``source=SYSTEM/ROUTINE`` 的活动名**不允许**是 Minecraft 活动（§二十九/§四十八）：
        虚拟活动绝不能冒用真实 Minecraft 行动的名字。
        """
        moment = self._now(now)
        name = str(activity_name or "").strip()
        if not name:
            return None
        if source is not ActivitySource.TASK and looks_like_minecraft_activity(name):
            if self._log is not None:
                self._log.warning(
                    "[World.Activity] 拒绝创建冒充真实 Minecraft 行动的虚拟活动：%s", name
                )
            return None
        # 锁**只**包住"检查 + 分配 ID + 建行"这段临界区：
        # 后面的激活/发布/投影会自己（重新）拿锁，跨方法持锁会自锁（asyncio.Lock 不可重入）。
        try:
            async with self._lock:
                if await self.store.live(self.character_id) is not None:
                    # §十二：一个角色同时只能有一个 primary Episode —— 先收尾再开新的
                    raise ActivityConflict("已经有一个未结束的 Episode")
                day = str(self.clock.day_key(moment))
                episode_id = await self.store.next_episode_id(day)
                episode = build_episode(
                    episode_id=episode_id,
                    character_id=self.character_id,
                    activity_type=activity_type,
                    activity_name=name,
                    source=source,
                    now=moment,
                    location=location,
                    social_state=social_state,
                    tags=list(tags or []),
                    related_task_id=related_task_id,
                    parent_episode_id=parent_episode_id,
                    duration=duration or duration_profile(activity_type, name),
                    status=ActivityStatus.SCHEDULED,
                )
                created = await self.store.create(episode)
        except ActivityError:
            raise
        except Exception as exc:  # noqa: BLE001 - 只降级（§五十一）
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.exception("[World.Activity] 创建 Episode 失败（降级）")
            return None
        await self.publisher.publish(
            ACTIVITY_SCHEDULED,
            created,
            store=self.store,
            timestamp=moment,
            reason=TransitionReason.SCHEDULED.value,
        )
        if scheduled:
            await self._project(created, reason="scheduled")
            return created
        try:
            return await self._activate(
                created, reason=activation_reason or TransitionReason.SCHEDULED, now=moment
            )
        except ActivityError:
            raise
        except Exception as exc:  # noqa: BLE001
            self.degraded_reason = f"{type(exc).__name__}"
            if self._log is not None:
                self._log.exception("[World.Activity] 激活 Episode 失败（降级）")
            return None

    async def switch_to(
        self,
        *,
        activity_name: str,
        activity_type: ActivityType = ActivityType.VIRTUAL_LIFE,
        source: ActivitySource = ActivitySource.ROUTINE,
        reason: TransitionReason | str = TransitionReason.MANUAL,
        location: str = "",
        social_state: str = "alone",
        tags: list[str] | None = None,
        related_task_id: str = "",
        duration: tuple[float, float, float] | None = None,
        now: float | None = None,
        parent_episode_id: str = "",
    ) -> ActivityEpisode | None:
        """换活动：先把当前 Episode 收尾，再开一个新的（**不是**并行两套）。

        这是沙盒适配器与用户交互的唯一入口 —— 保证任何时刻只有一个 primary Episode。
        ``parent_episode_id`` 用于"接在某个已经收尾的 Episode 之后"（例如任务暂停后恢复），
        不传就用当前那条（正常换活动）。
        """
        moment = self._now(now)
        current = await self.current()
        if current is not None and str(current.activity_name) == str(activity_name or "").strip():
            # 同一件事：不换（避免"吃早饭 → 吃早饭"这种无意义转移，§十三 的抖动问题）
            return current
        if current is not None:
            await self._terminate(
                current,
                to=ActivityStatus.COMPLETED,
                reason=reason,
                now=moment,
                publish=True,
            )
        created = await self.start(
            activity_name=activity_name,
            activity_type=activity_type,
            source=source,
            location=location,
            social_state=social_state,
            tags=tags,
            related_task_id=related_task_id,
            parent_episode_id=(
                parent_episode_id or (current.episode_id if current is not None else "")
            ),
            duration=duration,
            now=moment,
            activation_reason=reason,
        )
        # 现实变了 → 未来的计划要跟着重排（§八 触发点 1：Episode 边界；这是硬触发，
        # 冷却挡不住它 —— 因为它正是"计划的前提已经没了"）
        await self.refresh_plan(trigger=PlanTrigger.EPISODE_ENDED, now=moment, current=created)
        return created

    async def extend(
        self,
        episode_id: str | None = None,
        *,
        reason: TransitionReason | str = TransitionReason.TIME_EXPIRED,
        extra_seconds: float | None = None,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        """延长（ACTIVE/EXTENDED → EXTENDED）；到了硬上限就拒绝（§二十七 C）。"""
        moment = self._now(now)
        target = await self._resolve(episode_id)
        if target is None:
            return None
        limit = target.max_end_at
        if limit and moment >= limit:
            if self._log is not None:
                self._log.info(
                    "[World.Activity] episode=%s 已到硬上限，拒绝延长（转 EXPIRED）",
                    target.episode_id,
                )
            return await self.expire(episode_id=target.episode_id, now=moment)
        step = float(extra_seconds if extra_seconds is not None else target.typical_duration or 0.0)
        planned = min(limit, float(target.planned_end_at or moment) + step) if step > 0 else limit
        extended = await self._terminate(
            target,
            to=ActivityStatus.EXTENDED,
            reason=reason,
            now=moment,
            publish=True,
            # §十六：延长必须留痕 —— 秒数进审计行（新 planned_end 在 Episode 行上）
            transition_detail=f"+{int(step)}s",
            fields={
                "planned_end_at": planned,
                "extension_count": int(target.extension_count) + 1,
            },
        )
        if extended is not None:
            # Phase 6C.1 §二：6B 的 Decision **已经做完了** —— 这里只把"未来的计划"与现实对齐，
            # 绝不重新决策、绝不碰当前 Episode、也绝不提前开下一个活动（§十一/§十二/§十三）。
            await self.reconcile_plan(now=moment, current=extended)
        return extended

    async def complete(
        self,
        *,
        reason: TransitionReason | str = TransitionReason.MANUAL,
        episode_id: str | None = None,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        target = await self._resolve(episode_id)
        if target is None:
            return None
        return await self._terminate(
            target, to=ActivityStatus.COMPLETED, reason=reason, now=self._now(now), publish=True
        )

    async def interrupt(
        self,
        *,
        reason: TransitionReason | str = TransitionReason.USER_INTERACTION,
        episode_id: str | None = None,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        target = await self._resolve(episode_id)
        if target is None:
            return None
        return await self._terminate(
            target, to=ActivityStatus.INTERRUPTED, reason=reason, now=self._now(now), publish=True
        )

    async def cancel(
        self,
        *,
        reason: TransitionReason | str = TransitionReason.MANUAL,
        episode_id: str | None = None,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        target = await self._resolve(episode_id)
        if target is None:
            return None
        return await self._terminate(
            target, to=ActivityStatus.CANCELLED, reason=reason, now=self._now(now), publish=True
        )

    async def expire(
        self,
        *,
        reason: TransitionReason | str = TransitionReason.TIME_EXPIRED,
        episode_id: str | None = None,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        target = await self._resolve(episode_id)
        if target is None:
            return None
        return await self._terminate(
            target, to=ActivityStatus.EXPIRED, reason=reason, now=self._now(now), publish=True
        )

    # ------------------------------------------------------------ 世界 tick（只推进，不决策）

    async def advance(self, *, now: float | None = None) -> ActivityEpisode | None:
        """世界时钟推进一次：**只**推进时间与生命周期（§六/§八）。

        决策**只**发生在：她空着（没有任何 Episode）、或当前 Episode 已经到期/超过硬上限。

        Phase 6C：这里**不**重新规划 —— 只做一次廉价检查（内存里的计划 + 一次只读状态/目标
        快照签名）。真的重算整段 horizon 只会发生在 §八 的触发点上，而且软触发还要过冷却（§九）。
        """
        moment = self._now(now)
        current = await self.current()
        if current is None:
            # 决策点：她空着（启动/上一个 Episode 收尾之后）→ 计划先重排，再排下一个 Episode
            await self._maybe_refresh_plan(now=moment, current=None)
            return await self._plan_next(now=moment, parent_episode_id="")
        if current.status is ActivityStatus.SCHEDULED:
            if current.beyond_max(moment):
                return await self._finish_and_replan(current, to=ActivityStatus.EXPIRED, now=moment)
            return await self._activate(current, reason=TransitionReason.SCHEDULED, now=moment)
        if current.status in {ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}:
            # §八/§九：普通 tick 的规划侧只做"该不该重排"的廉价判断（冷却挡在真正规划之前）
            await self._maybe_refresh_plan(now=moment, current=current)
            # Phase 6B：普通 tick 只问"要不要做决策"（§十：没进 window 就什么都不做）。
            # 真正的决定由决策引擎按 §二四 的流水线给出（最短/最长时长 → window → 延长 → 撞车）。
            trigger = (
                DecisionTrigger.TIME_EXPIRED
                if current.overdue(moment)
                else DecisionTrigger.TIME_NEAR_END
            )
            return await self.tick(current, trigger=trigger, now=moment)
        return None

    async def tick(
        self,
        current: ActivityEpisode | None = None,
        *,
        trigger: Any = DecisionTrigger.TIME_NEAR_END,
        now: float | None = None,
    ) -> ActivityEpisode | None:
        """一次 world tick 的决策执行（§二四 第 12-14 步）：只改生命周期、只发既有事件。"""
        moment = self._now(now)
        episode = current if current is not None else await self.current()
        if episode is None:
            return None
        if episode.status not in {ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}:
            return episode
        history = await self.recent(max(self.recent_episode_limit, 6))
        live = [item for item in history if item.status.open]
        decision, _trace = await self.engine.decide(
            episode=episode,
            now=moment,
            trigger=as_trigger(trigger),
            history=history,
            live=live,
            started_today=await self._started_today(moment),
        )
        return await self.apply_decision(episode, decision, now=moment)

    async def apply_decision(
        self, episode: ActivityEpisode, decision: Any, *, now: float | None = None
    ) -> ActivityEpisode | None:
        """把决策落到生命周期上（CONTINUE 不动、EXTEND 延长、TRANSITION 收尾并排下一个）。"""
        moment = self._now(now)
        kind = str(getattr(getattr(decision, "decision", None), "value", decision) or "")
        reason = getattr(decision, "reason_code", None)
        if kind == "CONTINUE":
            return episode
        if kind == "EXTEND":
            return await self.extend(
                episode.episode_id,
                reason=TransitionReason.TIME_EXPIRED,
                extra_seconds=float(getattr(decision, "extension_seconds", 0.0) or 0.0),
                now=moment,
            )
        if kind == "TRANSITION":
            hint = str(getattr(decision, "next_activity_hint", "") or "")
            transition_reason = (
                decision_reason_for_transition(reason)
                if isinstance(reason, DecisionReason)
                else TransitionReason.TIME_EXPIRED
            )
            # §十五 的"到硬上限必须换"用 EXPIRED 收尾（她**用完了**这条命），
            # 其它原因（window 到期 / 撞车护栏 / 任务抢占）都是正常收尾 → COMPLETED。
            # 这条区分由 6A 的状态语义定下，6B 不改它。
            terminal = (
                ActivityStatus.EXPIRED
                if reason is DecisionReason.MAX_DURATION
                else ActivityStatus.COMPLETED
            )
            return await self._finish_and_replan(
                episode,
                to=terminal,
                now=moment,
                replan=True,
                replan_reason=transition_reason,
                next_name=hint,
            )
        return episode

    async def decide_now(
        self, *, trigger: Any = DecisionTrigger.MANUAL, now: float | None = None
    ) -> dict[str, Any]:
        """显式做一次决策（恢复 / 手动 / 测试用）—— 不改状态，只给决策与 trace。"""
        moment = self._now(now)
        episode = await self.current()
        if episode is None:
            return {"decision": "", "reason": "", "trace": None}
        history = await self.recent(max(self.recent_episode_limit, 6))
        decision, trace = await self.engine.decide(
            episode=episode,
            now=moment,
            trigger=as_trigger(trigger),
            history=history,
            live=[item for item in history if item.status.open],
            started_today=await self._started_today(moment),
        )
        return {
            "decision": decision.decision.value,
            "reason": decision.reason_code.value,
            "next_activity_hint": decision.next_activity_hint,
            "trace": trace.to_payload(),
        }

    async def plan_next(self, *, now: float | None = None) -> ActivityEpisode | None:
        """显式地"她空着" → 让 Planner 排一个（供测试与恢复后使用）。"""
        moment = self._now(now)
        if await self.current() is not None:
            return await self.current()
        return await self._plan_next(now=moment, parent_episode_id="")

    # ------------------------------------------------------------ 恢复（§二十七/§二十八）

    async def recover(self) -> dict[str, Any]:
        """进程重启后的对账：**只**处理已存在的 Episode，绝不凭空造一个（§二十八/§五十二）。

        Phase 6C 加了计划的恢复（§四十七）：**先**认现实（当前 Episode），再认计划；
        计划过期（``stale``）就重新规划 —— **绝不**盲目接着跑一份未来的时间表。

        返回审计用的结果字典（``action`` / ``episode_id`` / ``reason`` / ``plan``）。
        """
        moment = self._now(None)
        plan = await self.load_plan()
        plan_action = "none"
        if plan is not None:
            plan_action = "stale" if plan.stale(moment) else "loaded"
        current = await self.current()
        if current is None:
            # 合法状态：没有 Episode 就不假装有（§五十二）。她"现在做什么"交给下一次 tick。
            return {
                "action": "none",
                "episode_id": "",
                "reason": "no_episode",
                "plan": plan_action,
            }

        # 先与**任务的权威状态**对齐（§四十二）：重启之前任务就已经 PAUSED / 已经结束的情况，
        # 事件早就发过了（那时活动层可能还没装配），只能在这里用只读事实校正。
        aligned = await self._align_with_task(current, now=moment)
        if aligned is not None:
            # §二十/§二十一：即使走了"任务状态对齐"这条路，也必须**先认现实**再认计划 ——
            # 现实可能已经在停机期间变了（Episode 被延长过 / 任务已经收尾），
            # 所以计划同样要重排；Future Plan 永远不能覆盖现实。
            await self._refresh_after_recovery(moment)
            aligned.setdefault("plan", plan_action)
            return aligned

        # 再把"我重启过"如实记下来（不是伪造活动，只是说明这条 Episode 被重新接管）。
        # **同时落一行转移审计**：§五十五 要求能回答"重启后是不是同一个 Episode"，
        # 只写日志不够 —— 但 RECOVERED 不在一次性索引里，多次重启会有多行（这是对的）。
        if hasattr(self.store, "log_transition"):
            try:
                await self.store.log_transition(
                    episode_id=current.episode_id,
                    transition="RECOVERED",
                    reason=TransitionReason.RECOVERY.value,
                    source=current.source.value,
                    at=moment,
                )
            except Exception:  # noqa: BLE001 - 审计失败只降级，不影响接管
                if self._log is not None:
                    self._log.debug("[World.Activity] 接管审计落盘失败（忽略）", exc_info=True)
        await self.publisher.publish(
            ACTIVITY_RECOVERED,
            current,
            store=self.store,
            timestamp=moment,
            reason=TransitionReason.RECOVERY.value,
            audit=False,
        )
        if current.beyond_max(moment):
            # 情况 C：超过硬上限 → 终结（然后让 Planner 决定后续）
            finished = await self._finish_and_replan(
                current,
                to=ActivityStatus.EXPIRED,
                now=moment,
                replan=True,
                replan_reason=TransitionReason.RECOVERY,
            )
            return {
                "action": "expired",
                "episode_id": current.episode_id,
                "reason": TransitionReason.TIME_EXPIRED.value,
                "next": finished.episode_id if finished is not None else "",
                "plan": plan_action,
            }
        if moment >= float(current.planned_end_at or 0.0) + self.recovery_grace_seconds and (
            current.overdue(moment)
        ):
            # 情况 B：计划结束时间已过（超出重启宽限）但没超硬上限
            # → 交给 Planner（延长或换下一个）
            decision = self.planner.next_after(
                current,
                now=moment,
                clock=self.clock,
                started_today=await self._started_today(moment),
            )
            result = await self._apply_decision(current, decision, now=moment)
            await self._refresh_after_recovery(moment)
            return {
                "action": decision.action,
                "episode_id": current.episode_id,
                "reason": decision.reason.value,
                "next": result.episode_id if result is not None and result is not current else "",
                "plan": plan_action,
            }
        # 情况 A：还在计划窗口内 → 继续 ACTIVE（如果还没开始就先开始）
        if current.status is ActivityStatus.SCHEDULED:
            await self._activate(current, reason=TransitionReason.RECOVERY, now=moment)
        await self._project(await self.current(), reason="recovered")
        await self._refresh_after_recovery(moment)
        return {
            "action": "resumed",
            "episode_id": current.episode_id,
            "reason": TransitionReason.RECOVERY.value,
            "plan": plan_action,
        }

    async def _refresh_after_recovery(self, moment: float) -> None:
        """重启后的重新规划（§八 触发点 7 + §四十七）。

        计划过期就重排；计划没过期也只是"重算一遍看变没变"（内容签名一样就不会产生新版本）。
        注意方向：**现实 → 计划**，绝不是计划改现状（§三十一/§五十二）。
        """
        await self.refresh_plan(
            trigger=PlanTrigger.RECOVERY,
            now=moment,
            current=await self.current(),
            force=True,
        )

    async def _align_with_task(
        self, episode: ActivityEpisode, *, now: float
    ) -> dict[str, Any] | None:
        """重启后把"任务型 Episode"对齐到任务的权威状态（只读探针；不可用就不猜）。

        * 任务 PAUSED → INTERRUPTED（§四十二 的统一口径）
        * 任务已经在停机期间收尾 → 对应的终态（COMPLETED/INTERRUPTED/CANCELLED/EXPIRED）
        * 任务还在跑 / 探针拿不到 → 什么都不做（交给下面的时间对账）
        """
        task_id = str(episode.related_task_id or "")
        if not task_id or self.task_state_probe is None:
            return None
        try:
            state = str(await self.task_state_probe(task_id) or "").strip().upper()
        except Exception:  # noqa: BLE001 - 探针故障只降级：按时间对账继续
            if self._log is not None:
                self._log.debug("[World.Activity] 任务状态探针失败（忽略）", exc_info=True)
            return None
        outcome = TASK_STATE_OUTCOME.get(state)
        if outcome is None:
            return None
        target, reason = outcome
        if not episode.can_transition_to(target):
            return None
        updated = await self._terminate(episode, to=target, reason=reason, now=now, publish=True)
        if updated is None:
            return None
        return {
            "action": "aligned",
            "episode_id": episode.episode_id,
            "reason": reason.value,
            "task_state": state,
        }

    # ------------------------------------------------------------ 只读观察（§十六/§十七）

    async def observe(self, observation: dict[str, Any], *, now: float | None = None) -> None:
        """记下世界事实（Minecraft 当前状态等）。**观察不是命令**：它不产生任何动作。

        只用于两件事：``last_observation`` 审计、以及给上下文说明"这个 Episode 还合理吗"。
        写盘按 §二十三 节流（默认 60 秒一次），重大转移时才立即落盘。
        """
        self.last_observation = dict(observation or {})
        current = await self.current()
        if current is None:
            return
        self._observation_buffer = {**self.last_observation, "episode_id": current.episode_id}
        moment = self._now(now)
        if moment - float(self._observation_flushed_at or 0.0) < self.persistence_interval_seconds:
            return
        await self._flush_observation(current, now=moment)

    async def _flush_observation(self, episode: ActivityEpisode, *, now: float) -> None:
        if not self._observation_buffer:
            return
        try:
            await self.store.transition(
                episode.episode_id,
                expect=[episode.status],
                to=episode.status,
                reason=str(episode.transition_reason or ""),
                at=now,
                fields={"observation": dict(self._observation_buffer)},
            )
            self._observation_flushed_at = float(now)
        except Exception:  # noqa: BLE001 - 观察落盘失败只降级
            if self._log is not None:
                self._log.debug("[World.Activity] 观察落盘失败（忽略）", exc_info=True)

    def plausible(self, observation: dict[str, Any] | None = None) -> tuple[bool, list[str]]:
        """这个 Episode 与当前世界事实还一致吗（§十七：只**判断**，不产生动作）。"""
        facts = dict(observation or self.last_observation or {})
        episode = getattr(self, "_plausible_episode", None)
        reasons: list[str] = []
        if episode is not None and episode.activity_type is ActivityType.TASK_EXECUTION:
            if facts.get("online") is False:
                reasons.append("minecraft_offline")
            task_state = str(facts.get("task_state") or "")
            if task_state and task_state not in {"RUNNING", "WAITING_ACTION", "WAITING_USER"}:
                reasons.append(f"task_{task_state.lower()}")
        return (not reasons), reasons

    # ------------------------------------------------------------ 内部

    def _now(self, now: float | None) -> float:
        if now is not None:
            return float(now)
        try:
            return float(self.clock.now())
        except Exception:  # noqa: BLE001 - 时钟坏了也不能让生命周期崩掉
            import time as _time

            return float(_time.time())

    async def _resolve(self, episode_id: str | None) -> ActivityEpisode | None:
        if episode_id:
            try:
                return await self.store.get(str(episode_id))
            except Exception:  # noqa: BLE001
                return None
        return await self.current()

    async def _activate(
        self, episode: ActivityEpisode, *, reason: TransitionReason | str, now: float
    ) -> ActivityEpisode | None:
        fields = {
            "started_at": float(now),
            "planned_end_at": float(now) + float(episode.typical_duration or 0.0),
        }
        updated = await self._terminate(
            episode, to=ActivityStatus.ACTIVE, reason=reason, now=now, publish=True, fields=fields
        )
        return updated

    async def _terminate(
        self,
        episode: ActivityEpisode,
        *,
        to: ActivityStatus,
        reason: TransitionReason | str,
        now: float,
        publish: bool,
        fields: dict[str, Any] | None = None,
        transition_detail: str = "",
    ) -> ActivityEpisode | None:
        """一次状态转移：状态机裁决 → CAS 写入 → 幂等事件 → 投影。"""
        if not episode.can_transition_to(to):
            raise InvalidActivityTransition(
                f"{episode.status.value} → {to.value} 不是合法转移（§六）"
            )
        async with self._lock:
            try:
                updated = await self.store.transition(
                    episode.episode_id,
                    expect=[episode.status],
                    to=to,
                    reason=reason_value(reason),
                    at=now,
                    fields=fields,
                )
            except InvalidActivityTransition:
                raise
            except Exception as exc:  # noqa: BLE001 - 写失败只降级（§五十一）
                self.degraded_reason = f"{type(exc).__name__}"
                if self._log is not None:
                    self._log.exception("[World.Activity] 转移失败（降级）")
                return None
            if updated is None:
                # CAS 失败：另一个事件已经先收尾了（§四十四：只会有一次最终转移）
                if self._log is not None:
                    self._log.info(
                        "[World.Activity] episode=%s 的转移已被其它事件抢先（忽略本次）",
                        episode.episode_id,
                    )
                return None
        if publish:
            await self.publisher.publish(
                EVENT_BY_STATUS.get(updated.status.value, ACTIVITY_SCHEDULED),
                updated,
                store=self.store,
                timestamp=now,
                reason=reason_value(reason),
                detail=transition_detail,
            )
        if self._observation_buffer and updated.status.open:
            await self._flush_observation(updated, now=now)
        # 投影的是**当前** Episode（§十四）：已经终结的 Episode 不再占着"我现在在做什么"，
        # 所以这时把快照清空（"她刚结束一件事，还没开始下一件"）——
        # 完整历史在 activity_episodes / activity_transitions 里，审计不受影响。
        await self._project(
            None if updated.status.terminal else updated, reason=reason_value(reason)
        )
        return updated

    async def _apply_decision(
        self, episode: ActivityEpisode, decision: ActivityDecision, *, now: float
    ) -> ActivityEpisode | None:
        """Planner 的决定 → 真实转移（延长 / 收尾 + 排下一个）。"""
        if decision.extend:
            return await self.extend(episode.episode_id, reason=decision.reason, now=now)
        if decision.action == "stop":
            return await self._terminate(
                episode, to=ActivityStatus.COMPLETED, reason=decision.reason, now=now, publish=True
            )
        return await self._finish_and_replan(
            episode,
            to=ActivityStatus.COMPLETED,
            now=now,
            replan=True,
            replan_reason=decision.reason,
            next_name=decision.activity_name,
            next_duration=decision.duration,
        )

    async def _finish_and_replan(
        self,
        episode: ActivityEpisode,
        *,
        to: ActivityStatus,
        now: float,
        replan: bool = True,
        replan_reason: TransitionReason | str = TransitionReason.TIME_EXPIRED,
        next_name: str = "",
        next_duration: tuple[float, float, float] | None = None,
    ) -> ActivityEpisode | None:
        finished = await self._terminate(
            episode, to=to, reason=replan_reason, now=now, publish=True
        )
        if finished is None or not replan:
            return finished
        return await self._plan_next(
            now=now,
            parent_episode_id=finished.episode_id,
            name=next_name,
            duration=next_duration,
        )

    async def _plan_next(
        self,
        *,
        now: float,
        parent_episode_id: str = "",
        name: str = "",
        duration: tuple[float, float, float] | None = None,
    ) -> ActivityEpisode | None:
        """排下一个 Episode —— 有显式 ``name`` 就用它，否则**问计划**（§十七/§三十一）。

        Phase 6C 的关键变化：规划不再是 Planner 的"一次性回答"，而是**落盘的 rolling horizon
        计划**；Episode 只是计划第一条的落地。计划拿不到（或为空）才退回 6A 的
        :meth:`ActivityPlanner.initial` —— v1.0 §126 的"Primary Activity 不能为空"两条路都守住。
        """
        if name:
            decision = ActivityDecision(
                action="next",
                reason=TransitionReason.SCHEDULED,
                activity_name=name,
                duration=duration,
            )
        else:
            # Episode 结束/她空着 = §八 的硬触发点：先把计划刷新，再照计划的**第一条**开 Episode
            await self.refresh_plan(trigger=PlanTrigger.EPISODE_ENDED, now=now)
            planned_name = self._first_planned_activity(now)
            if planned_name:
                decision = ActivityDecision(
                    action="next",
                    reason=TransitionReason.SCHEDULED,
                    activity_name=planned_name,
                    duration=duration or duration_profile(ActivityType.VIRTUAL_LIFE, planned_name),
                )
            else:
                # 计划也给不出东西 → 退回 6A 的老路；
                # **连它都炸了**（§四十八：Planner 完全不可用）也绝不把活动置空。
                try:
                    decision = self.planner.initial(
                        now=now,
                        clock=self.clock,
                        character_id=self.character_id,
                        started_today=await self._started_today(now),
                    )
                except Exception:  # noqa: BLE001 - 兜底的兜底
                    self.degraded_reason = "planner_unavailable"
                    if self._log is not None:
                        self._log.warning(
                            "[World.Activity] Planner 完全不可用，使用最后兜底活动（§四十八）"
                        )
                    last_resort = self._last_resort_activity(now)
                    decision = ActivityDecision(
                        action="next",
                        reason=TransitionReason.SCHEDULED,
                        activity_name=last_resort,
                        duration=duration_profile(ActivityType.VIRTUAL_LIFE, last_resort),
                    )
        try:
            return await self.start(
                activity_name=decision.activity_name,
                activity_type=decision.activity_type,
                source=decision.source,
                parent_episode_id=parent_episode_id,
                duration=decision.duration,
                now=now,
            )
        except ActivityConflict:
            # 竞态：已经有人开了一个（单进程内不该发生；发生了就以现存的为准）
            return await self.current()
        except Exception:  # noqa: BLE001 - 只降级
            self.degraded_reason = "replan_failed"
            if self._log is not None:
                self._log.exception("[World.Activity] 排下一个 Episode 失败（降级）")
            return None

    def _last_resort_activity(self, now: float) -> str:
        """Planner **完全**不可用时的最后兜底活动（§四十九：idle / resting / free_time）。

        刻意不经过 Planner 对象的任何方法（它可能整个坏掉）：只看时段，确定性二选一。
        """
        try:
            period = str(self.clock.period(now))
        except Exception:  # noqa: BLE001 - 时钟也坏了就当白天
            period = "afternoon"
        return "resting" if period == "night" else FALLBACK_ACTIVITIES[0]

    def _first_planned_activity(self, now: float) -> str:
        """计划里"下一步做什么"（跳过 CONTINUATION 与已经过去/同名的条目）。

        拿不到就返回空串，调用方退回 6A 的老路 —— 绝不在这里编一个活动出来。
        """
        plan = self._active_plan
        if plan is None:
            return ""
        for item in plan.items:
            if item.reason == ItemReason.CONTINUATION.value:
                continue
            if float(item.planned_end) <= float(now):
                continue
            activity = str(item.activity or "")
            if not activity or looks_like_minecraft_activity(activity):
                continue  # 虚拟活动绝不冒用 Minecraft 名字（6A §二十九）
            return activity
        return ""

    async def _project(self, episode: ActivityEpisode | None, *, reason: str) -> None:
        if self.projection is None:
            return
        self._plausible_episode = episode
        await self.projection.apply(episode, reason=reason)

    async def _started_today(self, now: float) -> int:
        """今天已经排过多少个 Episode（Planner 用它做确定性旋转）。"""
        try:
            day = str(self.clock.day_key(now))
            return sum(
                1
                for episode in await self.store.recent(self.character_id, limit=50)
                if str(self.clock.day_key(episode.created_at or episode.started_at or now)) == day
            )
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------ 生命周期

    async def shutdown(self) -> None:
        """收尾：把缓冲里的观察落盘（**不**终结 Episode —— 重启后要能接管它）。"""
        current = await self.current()
        if current is not None:
            await self._flush_observation(current, now=self._now(None))
