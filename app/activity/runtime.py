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
from app.activity.planner import ActivityDecision, ActivityPlanner
from app.activity.projection import ActivityProjection
from app.activity.store import (
    ActivityConflict,
    ActivityError,
    InvalidActivityTransition,
    reason_value,
)

#: 观察缓冲写盘的默认间隔（§二十三：禁止每秒写数据库；30~60 秒一次）
DEFAULT_PERSISTENCE_INTERVAL_SECONDS = 60.0


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
        return activity_context_block(episode, transitions, related_task=episode.related_task_id)

    async def status(self) -> dict[str, Any]:
        """WebUI/API 的只读投影（含最近 Episode 与决策视图，§三六/§四七/§四八）。"""
        episode = await self.current()
        recent = await self.recent(self.recent_episode_limit)
        now = self._now(None)
        report = self.run_consistency_check(episode, now=now)
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
        return await self.start(
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
        return await self._terminate(
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
        """世界时钟推进一次：**只**推进时间与生命周期（§八）。

        决策**只**发生在：她空着（没有任何 Episode）、或当前 Episode 已经到期/超过硬上限。
        """
        moment = self._now(now)
        current = await self.current()
        if current is None:
            # 决策点：她空着（启动/上一个 Episode 收尾之后）→ 让 Planner 排下一个
            return await self._plan_next(now=moment, parent_episode_id="")
        if current.status is ActivityStatus.SCHEDULED:
            if current.beyond_max(moment):
                return await self._finish_and_replan(current, to=ActivityStatus.EXPIRED, now=moment)
            return await self._activate(current, reason=TransitionReason.SCHEDULED, now=moment)
        if current.status in {ActivityStatus.ACTIVE, ActivityStatus.EXTENDED}:
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

        返回审计用的结果字典（``action`` / ``episode_id`` / ``reason``）。
        """
        moment = self._now(None)
        current = await self.current()
        if current is None:
            # 合法状态：没有 Episode 就不假装有（§五十二）。她"现在做什么"交给下一次 tick。
            return {"action": "none", "episode_id": "", "reason": "no_episode"}

        # 先与**任务的权威状态**对齐（§四十二）：重启之前任务就已经 PAUSED / 已经结束的情况，
        # 事件早就发过了（那时活动层可能还没装配），只能在这里用只读事实校正。
        aligned = await self._align_with_task(current, now=moment)
        if aligned is not None:
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
            return {
                "action": decision.action,
                "episode_id": current.episode_id,
                "reason": decision.reason.value,
                "next": result.episode_id if result is not None and result is not current else "",
            }
        # 情况 A：还在计划窗口内 → 继续 ACTIVE（如果还没开始就先开始）
        if current.status is ActivityStatus.SCHEDULED:
            await self._activate(current, reason=TransitionReason.RECOVERY, now=moment)
        await self._project(await self.current(), reason="recovered")
        return {
            "action": "resumed",
            "episode_id": current.episode_id,
            "reason": TransitionReason.RECOVERY.value,
        }

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
        decision = (
            ActivityDecision(
                action="next",
                reason=TransitionReason.SCHEDULED,
                activity_name=name,
                duration=duration,
            )
            if name
            else self.planner.initial(
                now=now,
                clock=self.clock,
                character_id=self.character_id,
                started_today=await self._started_today(now),
            )
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
