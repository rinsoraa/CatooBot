"""Phase 6A §十/§十一/§四十五 + Phase 6C §三-§九/§三十-§四十一：确定性 Activity Planner。

Planner 只回答一件事：**"接下来这一段大概做什么"**，并且只产出**意图**：

    Routine + Anchors + Persistent Goals + History + Character State
                              ↓
                        ActivityPlanner
                              ↓
                       ActivityPlan（意图，不是事实）
                              ↓（6B 允许时）
                       ActivityEpisode（唯一事实）

它**不**执行、**不**碰世界、**不**调模型、**不**用随机（§一/§五十/§六十四）。它产出的计划
永远不能自己变成 Episode —— 必须经过 ``ActivityDecisionEngine`` 与
``ActivityRuntime``（§三十一/§三十二）。

三条边界（§一/§七十六）：

* **Planner ≠ Executor**：本模块只 import 领域模型与纯计算模块；不 import Minecraft 服务、
  ActionRuntime、Minecraft 工具、TaskRuntime 的确认入口、ConfirmationStore、Policy、AgentBridge
  （§六十六 有 AST guard 守着）。
* **Plan ≠ Reality**：计划项没有 id、没有状态机（见 ``plan.py``）。
* **Goal ≠ Task**：目标只做排名加成（§二十二），绝不因为"目标在推"就去创建任务或动作。

性能（§六十五）：普通 tick **不做**任何规划（§八/§九）；规划本身是 O(候选数 × 项数) 的小常数，
候选上限 6、项数上限 6，绝不扫全表、绝不全量重算。
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import Any

from app.activity.anchors import AnchorBook, ScheduleAnchor
from app.activity.goals import GoalSnapshot
from app.activity.initiative import (
    EMPTY_HINT_BOOK,
    HINT_TERM,
    InitiativeHintBook,
    hint_book_from,
)
from app.activity.model import (
    VIRTUAL_DURATIONS,
    ActivityEpisode,
    ActivitySource,
    ActivityType,
    TransitionReason,
    duration_profile,
    looks_like_minecraft_activity,
)
from app.activity.plan import (
    ActivityPlan,
    Candidate,
    ItemReason,
    PlanItem,
    RejectionReason,
)
from app.activity.profiles import (
    FOCUS_PREFERRED_HIGH,
    FOCUS_PREFERRED_LOW,
    FREE_ACTIVITY_POOL,
    HIGH_FOCUS_THRESHOLD,
    ILLEGAL_BY_PERIOD,
    LOW_ENERGY_EXEMPT,
    LOW_ENERGY_SAFE,
    LOW_ENERGY_THRESHOLD,
    LOW_FOCUS_THRESHOLD,
    SCORE_WEIGHTS,
    ActivityKind,
    plannable,
    profile_for,
)

#: 时段 → 候选虚拟活动（**确定性旋转**：按今天已排过的数量取模，不用随机）。
#: 词表刻意与**沙盒动作定义**一致（eating/gaming/reading/out/sleeping/…），
#: 这样"谁来创建 Episode"都不会让 ``CharacterState.activity`` 变成两套语言。
#: 6C 把它降级为"偏好"来源之一（还有锚点、目标、自由池），不再是唯一来源（§十）。
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

#: 兜底活动（v1.0 §126：Primary Activity 不能为空）。顺序**就是**优先级，确定性。
#: （Phase 6C：这个常量原来在 decision.py，搬到 Planner 这一层 —— "哪个活动能当兜底"
#: 是日程知识，不是决策引擎的知识；decision.py 现在从这里 import。）
FALLBACK_ACTIVITIES: tuple[str, ...] = ("idle", "resting", "free_time")

#: 默认 rolling horizon（§六：4 小时。真实取值由配置注入，绝不在这里写死运行时行为）
DEFAULT_HORIZON_SECONDS = 240 * 60.0

#: 状态未知时的中性能量/专注（CharacterState 的默认能量就是 0.8）
NEUTRAL_ENERGY = 0.8
NEUTRAL_FOCUS = 0.5

#: 时段 → (偏好的活动类别, 明显不合适的类别)。取值全部来自画像的 ``category``。
#: §四十：既有 WorldClock 已有 morning/afternoon/evening/night 四档（"day" 就是下午，
#: "late_night" 并进 night），这里**复用**它，不另立一套时段（§四十 "已有更细时间系统：复用"）。
PERIOD_CATEGORY_FIT: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "morning": (
        frozenset({"meal", "care", "chore", "leisure", "social"}),
        frozenset({"sleep"}),
    ),
    "afternoon": (
        frozenset({"work", "leisure", "chore", "social"}),
        frozenset({"sleep"}),
    ),
    "evening": (
        frozenset({"leisure", "social", "meal", "care"}),
        frozenset({"work"}),
    ),
    "night": (
        frozenset({"sleep", "leisure", "care"}),
        frozenset({"work", "chore", "social"}),
    ),
}

#: 类别契合度的三档取值（确定性；不做连续插值 —— 分数要么说得清，要么别给）
CATEGORY_FIT_PREFERRED = 1.0
CATEGORY_FIT_NEUTRAL = 0.5
CATEGORY_FIT_DISFAVORED = 0.15

#: 候选数量上限（§三十三：3~6 个，绝不扫描全项目所有活动）
DEFAULT_MAX_CANDIDATES = 6
#: 候选数量下限（不够就用兜底池补到这么多个，好让"为什么不是别的"永远有东西可看）
MIN_CANDIDATES = 3
#: horizon 里最多排几条（§六十八：默认不超过 6）
DEFAULT_MAX_ITEMS = 6


@dataclass(frozen=True)
class ActivityDecision:
    """Planner 的**唯一**输出形状（6A §十一）：下一步 / 原因 / 时长。

    Phase 6C 起这是 :meth:`ActivityPlanner.plan_next` 产出的计划在"下一步是什么"上的
    **投影**（``initial`` / ``next_after`` 两个 6A 入口仍然返回它，签名一字未改）。
    """

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


@dataclass
class PlannerContext:
    """一次规划的全部输入（§三 的 ``context``）。

    Planner 是**纯函数**：同样的输入必然给同样的计划（§六十四）。所以这里只放
    "已经读出来的只读事实"，没有任何句柄、连接、模型或回调。
    """

    clock: Any
    now: float
    character_state: Any = None
    current_episode: ActivityEpisode | None = None
    history: tuple[ActivityEpisode, ...] = ()
    goals: GoalSnapshot | None = None
    anchors: AnchorBook | None = None
    started_today: int = 0
    horizon_seconds: float = DEFAULT_HORIZON_SECONDS
    #: horizon 的终点（绝对时间戳）。**一次性定死**：计划项铺到未来时，``now`` 会前移，
    #: 但 horizon 不许跟着缩（否则"下一个锚点在 horizon 之外"会把计划挤成空的）。
    horizon_end_at: float = 0.0
    max_items: int = DEFAULT_MAX_ITEMS
    max_candidates: int = DEFAULT_MAX_CANDIDATES
    trigger: str = ""
    #: 现实里正在进行的 Episode 结束时间（CONTINUATION 条目的终点；0 = 用 typical 推）
    continue_until: float = 0.0
    #: 这个计划里**已经排过**的锚点（同一个锚点在一条计划里只排一次；铺到未来时逐个累加）
    planned_anchors: frozenset[str] = frozenset()
    #: Phase 7B §四：角色主动意图的**软**偏好（只读；空书 = 与 6C 逐字一致，§五）
    hints: Any = EMPTY_HINT_BOOK

    # ------------------------------------------------------------ 只读派生

    @property
    def horizon_start(self) -> float:
        return float(self.now)

    @property
    def horizon_end(self) -> float:
        fixed = float(self.horizon_end_at or 0.0)
        if fixed:
            return fixed
        return float(self.now) + max(0.0, float(self.horizon_seconds))

    def _field(self, name: str, default: Any) -> Any:
        state = self.character_state
        if state is None:
            return default
        if isinstance(state, dict):
            return state.get(name, default)
        return getattr(state, name, default)

    @property
    def energy(self) -> float:
        """能量 0~1（§二十七：第一版不做复杂动力学）。未知就当中性。"""
        try:
            return max(0.0, min(1.0, float(self._field("energy", NEUTRAL_ENERGY))))
        except (TypeError, ValueError):
            return NEUTRAL_ENERGY

    @property
    def focus(self) -> float:
        """专注 0~1（既有字段名是 ``current_focus``，§二十六：适配而不是新建）。"""
        raw = self._field("current_focus", "")
        if raw in ("", None):
            # 也接受更像"数值专注"的写法（适配，不重复创建字段）
            raw = self._field("focus", NEUTRAL_FOCUS)
            if raw in ("", None) or not _is_number(raw):
                return NEUTRAL_FOCUS
        if not _is_number(raw):
            # ``current_focus`` 是文字（例如 "reading"）时，专注度未知 → 中性
            return NEUTRAL_FOCUS
        try:
            return max(0.0, min(1.0, float(raw)))
        except (TypeError, ValueError):
            return NEUTRAL_FOCUS

    @property
    def sleep_state(self) -> str:
        """既有字段名是 ``schedule_state``（§二十六 适配）。"""
        return str(self._field("schedule_state", "") or "")

    @property
    def social_state(self) -> str:
        return str(self._field("social_state", "") or "")

    @property
    def mood(self) -> str:
        return str(self._field("mood", "") or "")

    @property
    def period(self) -> str:
        try:
            return str(self.clock.period(self.now))
        except Exception:  # noqa: BLE001 - 时钟不认识就问不出时段，退回白天
            return "afternoon"

    def state_payload(self) -> dict[str, Any]:
        """进 ``constraints`` 的状态快照（只用于审计与解释）。"""
        return {
            "energy": round(self.energy, 4),
            "focus": round(self.focus, 4),
            "period": self.period,
            "sleep_state": self.sleep_state,
            "social_state": self.social_state,
            "mood": self.mood,
        }


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool)


@dataclass(frozen=True)
class _Step:
    """铺计划时的一步（内部结构）。

    ``activity`` 为空 = "这一步什么都不排"，此时 ``jump_to`` 指向下一个锚点窗口
    （调用方据此前移时间或收工）。用具名结构而不是裸元组，是因为这里每一步都有 5 个字段，
    靠位置读代码迟早会读错。
    """

    activity: str = ""
    reason: str = ""
    anchor_id: str = ""
    start: float = 0.0
    end: float = 0.0
    jump_to: float = 0.0
    #: Phase 7B：这一步的活动是不是被某条意图"往前排"了（审计用）
    intent_id: str = ""


class ActivityPlanner:
    """确定性日程规划器（无 LLM、无随机；同一输入永远同一输出，§六十四）。

    6A 的两个入口 :meth:`initial` / :meth:`next_after` **签名与语义一字未改**
    （6B 的决策引擎与若干测试都依赖它们）；6C 新增 :meth:`plan_next` 产出整段
    rolling horizon 计划，两个老入口现在都从同一份计划里取"下一步"，
    所以"Planner 的选择"与"计划里写的下一步"永远是同一个东西（§十七）。
    """

    def __init__(
        self,
        *,
        routine: dict[str, tuple[str, ...]] | None = None,
        anchors: AnchorBook | None = None,
        goal_source: Any = None,
        state_provider: Callable[[], Any] | None = None,
        weights: dict[str, float] | None = None,
        max_candidates: int = DEFAULT_MAX_CANDIDATES,
        max_items: int = DEFAULT_MAX_ITEMS,
        # ---- Phase 7B §三：只读的意图建议来源（鸭子类型；默认 None = 与 6C 逐字一致）
        intent_source: Any = None,
        logger: Any = None,
    ) -> None:
        table = dict(routine or ROUTINE_BY_PERIOD)
        # 边界守卫：日程表里**不允许**出现 Minecraft 活动名（§二十九）
        for period, names in table.items():
            for name in names:
                if looks_like_minecraft_activity(name):
                    raise ValueError(f"日程表里的虚拟活动不能是 Minecraft 活动：{period}/{name}")
        self._routine = table
        #: 日程锚点（§十二）。默认给"睡觉 + 三餐"这套最基本的作息；运行时可注入别的。
        self.anchors = anchors if anchors is not None else AnchorBook()
        #: 只读目标来源（§二十；默认空 —— 没有目标也照样能规划）
        self.goal_source = goal_source
        #: 同步的只读状态提供者（``() -> CharacterState``）。6A 的两个入口没有
        #: state 参数，从这里拿；拿不到就当中性状态（绝不报错）。
        self.state_provider = state_provider
        self.weights = dict(weights or SCORE_WEIGHTS)
        self.max_candidates = max(MIN_CANDIDATES, int(max_candidates))
        self.max_items = max(1, int(max_items))
        #: Phase 7B §三：只读建议来源（有 ``hints(*, limit, now)`` 就行）。
        #: **只在规划触发点读**（不是每个 tick），读失败只会得到空书（§三.6/§十一）。
        self.intent_source = intent_source
        self._log = logger

    # ------------------------------------------------------------ 6C：整段计划

    def plan_next(
        self,
        character_state: Any = None,
        current_episode: ActivityEpisode | None = None,
        *,
        now: float,
        horizon: float | None = None,
        context: PlannerContext | None = None,
    ) -> ActivityPlan:
        """规划未来一段（§三 的接口形状）：rolling horizon，绝不是全天时间表（§六）。

        ``plan_id`` 留空 —— 计划号由存储层在落盘时分配（``PLAN-YYYYMMDD-NNN``），
        与 Episode 的分配方式完全一致；Planner 是纯函数，不碰存储（§六十五）。
        """
        if context is None:
            context = PlannerContext(
                clock=self._clock(),
                now=float(now),
                character_state=(character_state if character_state is not None else self._state()),
                current_episode=current_episode,
                goals=self._goals(),
                anchors=self.anchors,
                horizon_seconds=float(horizon or DEFAULT_HORIZON_SECONDS),
                max_items=self.max_items,
                max_candidates=self.max_candidates,
            )
        if horizon is not None:
            context.horizon_seconds = float(horizon)
        # Phase 7B §三/§十一：建议**只在规划这一刻读一次**（不是每个 tick），
        # 读失败 → 空书 → 后面逐字退回 6C 的行为（§三.6/§五）。
        context.hints = self._hint_book(now=float(context.now))
        # horizon 的终点一次性定死（计划项铺到未来时 now 会前移，horizon 不许跟着缩）
        context.horizon_end_at = context.horizon_start + max(0.0, float(context.horizon_seconds))
        candidates = self._collect_candidates(context)
        ranked = self._rank(candidates)
        items = self._build_items(ranked, context)
        source = self._plan_source(items)
        episode = context.current_episode
        return ActivityPlan(
            plan_id="",
            character_id=str(getattr(episode, "character_id", "") or ""),
            plan_version=0,  # 版本由 runtime 按内容签名递增（§四十三）
            generated_at=float(context.now),
            horizon_start=context.horizon_start,
            horizon_end=context.horizon_end,
            items=items,
            candidates=tuple(ranked),
            constraints=self._constraints(context),
            source=source,
            trigger=str(context.trigger or ""),
        )

    # ------------------------------------------------------------ 6A 入口（签名不变）

    def initial(
        self, *, now: float, clock: Any, character_id: str = "", started_today: int = 0
    ) -> ActivityDecision:
        """她"空着"时的下一个活动（启动 / 重启后没有 Episode / Episode 收尾后无后续）。

        6C 起走同一套计划：先规划一段 horizon，再取第一条。仍然是**确定性**的
        （同一输入永远同一输出，§六十四）。
        """
        plan = self.plan_next(
            self._state(),
            None,
            now=float(now),
            context=self._context(clock, float(now), started_today=started_today, episode=None),
        )
        name = self._first_activity(plan)
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
        ctx = self._context(
            clock,
            float(now),
            started_today=started_today,
            episode=episode,
            avoid=episode.activity_name,
        )
        plan = self.plan_next(self._state(), None, now=float(now), context=ctx)
        name = self._first_activity(plan, avoid=str(episode.activity_name or ""))
        if (
            episode.source is not ActivitySource.ROUTINE
            and episode.source is not ActivitySource.RECOVERY
        ):
            # 任务 / 用户交互引起的活动结束后**不自动续摊**：让她回到自己的日常，
            # 由调度器下一次询问（这也是"绝不自主行动"的一部分）。
            return ActivityDecision(
                action="next",
                reason=TransitionReason.TIME_EXPIRED,
                activity_name=name,
                duration=duration_profile(ActivityType.VIRTUAL_LIFE, name),
            )
        return ActivityDecision(
            action="next",
            reason=TransitionReason.TIME_EXPIRED,
            activity_name=name,
            duration=duration_profile(ActivityType.VIRTUAL_LIFE, name),
        )

    # ------------------------------------------------------------ 候选（§三十三/§三十四）

    def _collect_candidates(self, ctx: PlannerContext) -> list[Candidate]:
        """生成 3~6 个候选并逐个判资格（§三十三/§三十四）。

        来源顺序（确定性的）：**锚点**（今天要发生的事）→ **时段习惯** → **目标亲和** →
        **自由活动池** → （能量低时）**安全集合**。去重保序，然后截到上限。

        目标亲和排在自由池**之前**是刻意的：自由池是"通用填充物"，如果它先占满候选上限，
        目标就永远进不了候选、也就永远影响不了排序（§二十二 的排名加成会变成死代码）。
        """
        period = ctx.period
        anchors = ctx.anchors or self.anchors
        ordered: list[str] = []
        for anchor in anchors.all():
            if anchor.seconds_until(ctx.clock, ctx.now) > ctx.horizon_seconds:
                continue
            ordered.append(anchor.activity)
        ordered.extend(self._routine.get(period) or ())
        if ctx.goals is not None:
            for goal in ctx.goals.open_goals:
                ordered.extend(goal.affinity)
        # Phase 7B §三：被建议的活动也要进候选池，否则"她的想法"永远进不了排序
        # （放在目标亲和之后、自由池之前：与"目标在推它"同级，不越权）。
        ordered.extend(ctx.hints.activities())
        ordered.extend(FREE_ACTIVITY_POOL)
        if ctx.energy < LOW_ENERGY_THRESHOLD:
            ordered.extend(sorted(LOW_ENERGY_SAFE))
        ordered.extend(FALLBACK_ACTIVITIES)

        seen: set[str] = set()
        activities: list[str] = []
        for name in ordered:
            key = str(name or "").strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            activities.append(key)
            if len(activities) >= self.max_candidates:
                break
        # 候选太少就补（§三十三 的下限；自由池之后仍可能不足）
        for name in FREE_ACTIVITY_POOL + FALLBACK_ACTIVITIES:
            if len(activities) >= MIN_CANDIDATES:
                break
            if name not in seen:
                seen.add(name)
                activities.append(name)

        candidates: list[Candidate] = []
        for order, name in enumerate(activities):
            candidate = self._judge(name, ctx, order=order)
            candidates.append(candidate)
        return candidates

    def _judge(self, activity: str, ctx: PlannerContext, *, order: int) -> Candidate:
        """一个候选的资格与分数（§三十四/§三十五/§三十六）。"""
        profile = profile_for(activity)
        anchor, anchor_fit = (ctx.anchors or self.anchors).best_fit(activity, ctx.clock, ctx.now)
        goal_value, goal_id = (0.0, "")
        if ctx.goals is not None:
            goal_value, goal_id = ctx.goals.relevance_for(activity)
        hint = ctx.hints.hint_for(activity)
        breakdown = self._score_breakdown(
            activity,
            ctx,
            anchor_fit=anchor_fit,
            goal_value=goal_value,
            hint_bonus=float(hint.score_bonus) if hint is not None else 0.0,
        )
        reason = self._rejection(activity, ctx, profile=profile, anchor_ref=anchor)
        return Candidate(
            activity=activity,
            eligible=not reason,
            reason=reason,
            score=round(sum(breakdown.values()), 6),
            breakdown=breakdown,
            anchor_id=str(getattr(anchor, "anchor_id", "") or ""),
            goal_id=goal_id,
            # §八：归因只记"哪条意图"；资格与分数照旧由规则说了算
            intent_id=str(hint.intent_id) if hint is not None else "",
            order=order,
        )

    def _rejection(
        self,
        activity: str,
        ctx: PlannerContext,
        *,
        profile: Any,
        anchor_ref: ScheduleAnchor | None,
    ) -> str:
        """拒绝原因（没有就返回空串表示 eligible，§三十四）。

        顺序**就是**优先级：最"硬"的理由先说（说不清的语言就不该出现在日志里）。
        """
        if not plannable(activity) or activity not in VIRTUAL_DURATIONS:
            return RejectionReason.UNKNOWN_ACTIVITY.value
        if looks_like_minecraft_activity(activity):
            # 6A §二十九：虚拟活动绝不冒用 Minecraft 名字（到不了这里也要拦一道）
            return RejectionReason.UNKNOWN_ACTIVITY.value
        illegal = ILLEGAL_BY_PERIOD.get(ctx.period, frozenset())
        if activity in illegal:
            return RejectionReason.PERIOD_ILLEGAL.value
        if (
            activity not in LOW_ENERGY_SAFE
            and activity not in LOW_ENERGY_EXEMPT
            and (ctx.energy < LOW_ENERGY_THRESHOLD)
        ):
            return RejectionReason.ENERGY_TOO_LOW.value
        # §十四/§十五：**硬**锚点正在窗口里 → 这段时间就是它的事（换不换仍由 6B 决定）。
        # 已经在这个计划里排过的锚点不再挡路（她这顿已经排上了，别人可以接着来）。
        due_hard = [
            item
            for item in (ctx.anchors or self.anchors).due(ctx.clock, ctx.now)
            if item.hard and item.anchor_id not in ctx.planned_anchors
        ]
        if due_hard and activity not in {item.activity for item in due_hard}:
            return RejectionReason.ANCHOR_CONFLICT.value
        # 固定活动（睡觉/三餐）**只由锚点窗口安排**（§十九 的 ``fixed`` 就是这个意思）：
        # 窗口没到（哪怕快到了）不排，窗口里这一轮已经排过也不排 ——
        # 否则"吃饭"会靠习惯分在一天里到处冒出来（06:13 的早饭、17:30 的提前晚饭）。
        if profile.kind is ActivityKind.FIXED:
            if anchor_ref is None or not anchor_ref.is_due(ctx.clock, ctx.now):
                return RejectionReason.NOT_MOVEABLE.value
            if anchor_ref.anchor_id in ctx.planned_anchors:
                return RejectionReason.NOT_MOVEABLE.value
        return ""

    def _rank(self, candidates: list[Candidate]) -> list[Candidate]:
        """确定性排序（§三十五/§三十七）：分数降序 + 固定 tie-break，绝不 random。"""
        return sorted(candidates, key=self._sort_key)

    def _sort_key(self, candidate: Candidate) -> tuple[Any, ...]:
        """tie-break（§三十七，顺序**一字不差**）：锚点优先级 → 目标优先级 → 习惯 → 活动名。

        §三十七 明确列了这四道，所以这里**不**插入"生成顺序"之类的私货：同分时最后一道
        一定是稳定的活动名（字典序），既确定又说得清。
        """
        anchors = self.anchors
        anchor = next(
            (item for item in anchors.all() if item.anchor_id == candidate.anchor_id), None
        )
        anchor_rank = anchor.priority.rank if anchor is not None else len(anchors.all())
        return (
            0 if candidate.eligible else 1,  # 先排 eligible（资格不是 tie-break，是门槛）
            -round(float(candidate.score), 6),
            anchor_rank,
            -round(float(candidate.breakdown.get("goal_relevance", 0.0)), 6),
            -round(float(candidate.breakdown.get("routine_preference", 0.0)), 6),
            candidate.activity,
        )

    def _score_breakdown(
        self,
        activity: str,
        ctx: PlannerContext,
        *,
        anchor_fit: float,
        goal_value: float,
        hint_bonus: float = 0.0,
    ) -> dict[str, float]:
        """§三十六 的加权确定性求和。每一项都能单独解释，且都不是概率。

        Phase 7B §四：``initiative_fit`` 是唯一的**软**项（权重低于锚点/习惯/目标），
        它只在候选**已经合格**之后参与排序 —— 越不过任何硬约束。
        """
        weights = self.weights
        profile = profile_for(activity)
        period = ctx.period
        anchor, _ = (ctx.anchors or self.anchors).best_fit(activity, ctx.clock, ctx.now)
        anchor_weight = anchor.priority.weight if anchor is not None else 0.0
        routine = self._routine_preference(activity, ctx)
        repetition = self._repetition_penalty(activity, ctx.history)
        category = self._category_fit(profile.category, period)
        return {
            "anchor_fit": float(anchor_fit) * anchor_weight * float(weights.get("anchor_fit", 0.0)),
            "routine_preference": routine * float(weights.get("routine_preference", 0.0)),
            "energy_fit": self._energy_fit(activity, ctx, profile=profile)
            * float(weights.get("energy_fit", 0.0)),
            "focus_fit": self._focus_fit(activity, ctx) * float(weights.get("focus_fit", 0.0)),
            "goal_relevance": float(goal_value) * float(weights.get("goal_relevance", 0.0)),
            "repetition_penalty": repetition * float(weights.get("repetition_penalty", 0.0)),
            "flexibility": float(profile.flexibility) * float(weights.get("flexibility", 0.0)),
            "time_period_fit": category * float(weights.get("time_period_fit", 0.0)),
            HINT_TERM: float(hint_bonus) * float(weights.get(HINT_TERM, 0.0)),
        }

    def _routine_preference(self, activity: str, ctx: PlannerContext) -> float:
        """时段习惯（§三十九/§四十）：表里越靠前越偏好，外加一点点确定性轮换。

        轮换让一天里不至于永远同一件事，同时**完全可复现**（只看 ``started_today``）。
        **固定活动（睡觉/三餐）不给习惯分** —— 它们的位置由锚点决定（§十九），
        否则"三餐"会靠习惯分整天霸榜，把她的一整天都排成吃饭。
        """
        if profile_for(activity).kind is ActivityKind.FIXED:
            return 0.0
        table = self._routine.get(ctx.period) or ()
        if activity not in table:
            return 0.0
        index = table.index(activity)
        base = max(0.0, 1.0 - 0.25 * index)
        rotation = 1 if len(table) and int(ctx.started_today) % len(table) == index else 0
        return base + (0.15 if rotation else 0.0)

    def _energy_fit(self, activity: str, ctx: PlannerContext, *, profile: Any) -> float:
        """能量契合度（§二十七/§二十八）：**硬的那一半**。

        休息类（``energy_cost < 0``）越累越合适；耗能类越有精神越合适。
        """
        energy = ctx.energy
        cost = float(profile.energy_cost)
        if cost <= 0.0:
            return 1.0 - energy
        return max(0.0, 1.0 - cost) * energy

    def _focus_fit(self, activity: str, ctx: PlannerContext) -> float:
        """专注契合度（§二十九）：**软的那一半** —— 只是排序信号，绝不拒绝任何活动。"""
        focus = ctx.focus
        wants_high = activity in FOCUS_PREFERRED_HIGH and focus >= HIGH_FOCUS_THRESHOLD
        wants_low = activity in FOCUS_PREFERRED_LOW and focus <= LOW_FOCUS_THRESHOLD
        if wants_high or wants_low:
            return 1.0
        if activity in FOCUS_PREFERRED_HIGH or activity in FOCUS_PREFERRED_LOW:
            return 0.35  # 方向对但状态不匹配：只是排序信号（§二十九）
        return 0.5

    def _repetition_penalty(self, activity: str, history: tuple[ActivityEpisode, ...]) -> float:
        """最近 ≤5 个 Episode 里的重复度（§三十）：防"游戏→休息→游戏→休息"无意义震荡。"""
        recent = list(history)[-5:]
        total = 0.0
        for index, episode in enumerate(reversed(recent)):
            if str(getattr(episode, "activity_name", "") or "") == activity:
                total += 1.0 / (index + 1)
        normalizer = sum(1.0 / (i + 1) for i in range(max(1, len(recent) or 5)))
        return min(1.0, total / normalizer) if normalizer else 0.0

    def _category_fit(self, category: str, period: str) -> float:
        preferred, disfavored = PERIOD_CATEGORY_FIT.get(period, (frozenset(), frozenset()))
        if category in disfavored:
            return CATEGORY_FIT_DISFAVORED
        if category in preferred:
            return CATEGORY_FIT_PREFERRED
        return CATEGORY_FIT_NEUTRAL

    # ------------------------------------------------------------ 计划项（§四十一/§四十二）

    def _build_items(self, ranked: list[Candidate], ctx: PlannerContext) -> tuple[PlanItem, ...]:
        """把排序结果铺成一段 rolling horizon（§四十一）：够 1~4 小时就停，绝不排满全天。

        铺法（确定性，一圈四步）：

        1. **现实优先**（§三十一/§四十七）：正在进行的 Episode 先占一条 ``CONTINUATION``，
           后面的一切都从它的结束时间往后排 —— 计划绝不假装她现在就能去干别的。
        2. **锚点窗口**：某锚点正在窗口里且**这个计划还没排过它** → 这条就是它的活动（§十四）。
        3. **装不下就先别开始**（§十五）：到下一个锚点窗口的空档短于候选最短时长 → 跳过空档，
           直接跳到窗口起点，避免"玩到一半被打断"。
        4. **相邻不同名**（§十五/§十九）：刚排过的不再连着排；锚点之后也不再回到锚点前那件事
           （否则就成了 gaming → lunch → gaming）。
        """
        items: list[PlanItem] = []
        horizon_end = ctx.horizon_end
        t = float(ctx.now)
        #: 这一步**暂时不排**的活动（紧邻刚做完的那个 + 锚点之前正在做的那个，§十五/§十九）。
        #: 只是偏好：真的没得选时（relaxed 路径）宁可重复也不把计划排空。
        avoid: tuple[str, ...] = ()
        planned: set[str] = set()

        if ctx.current_episode is not None and ctx.current_episode.status.open:
            end = float(ctx.continue_until or 0.0) or float(
                ctx.current_episode.planned_end_at or 0.0
            )
            if end <= t:
                end = t + max(
                    float(ctx.current_episode.typical_duration or 0.0),
                    float(ctx.current_episode.min_duration or 0.0),
                )
            end = min(end, horizon_end)
            if end > t:
                items.append(
                    PlanItem(
                        activity=str(ctx.current_episode.activity_name or ""),
                        planned_start=t,
                        planned_end=end,
                        reason=ItemReason.CONTINUATION.value,
                        priority=1.0,
                    )
                )
                avoid = (str(ctx.current_episode.activity_name or ""),)
                t = end
        elif ctx.continue_until > t:
            t = min(float(ctx.continue_until), horizon_end)

        guards = 0
        max_steps = max(1, ctx.max_items) * 4
        while t < horizon_end and len(items) < max(1, ctx.max_items) and guards < max_steps:
            guards += 1
            anchors = ctx.anchors or self.anchors
            step = self._anchor_step(anchors, ctx, t=t, planned=planned)
            if not step.activity:
                step = self._free_step(
                    ranked, ctx, t=t, avoid=avoid, horizon_end=horizon_end, planned=planned
                )
            if not step.activity:
                if step.jump_to > t:
                    t = min(float(step.jump_to), horizon_end)
                    continue
                break
            profile = profile_for(step.activity)
            before = items[-1].activity if items else ""
            if step.reason == ItemReason.ANCHOR.value and step.anchor_id:
                planned.add(step.anchor_id)
            if self._merge(items, step, profile=profile):
                avoid = (step.activity,)
                t = float(items[-1].planned_end)
                continue
            items.append(
                PlanItem(
                    activity=step.activity,
                    planned_start=float(step.start),
                    planned_end=float(step.end),
                    reason=step.reason,
                    priority=self._item_priority(step.activity, step.reason, anchors=anchors),
                    anchor_id=step.anchor_id,
                    intent_id=step.intent_id,
                )
            )
            # 锚点之后绝不回到锚点前那件事（§十五：gaming → lunch 而不是 gaming → lunch → gaming）
            if step.reason == ItemReason.ANCHOR.value and before:
                avoid = (step.activity, before)
            else:
                avoid = (step.activity,)
            t = float(step.end)
        return tuple(items)

    def _anchor_step(
        self,
        anchors: AnchorBook,
        ctx: PlannerContext,
        *,
        t: float,
        planned: set[str],
    ) -> _Step:
        """这一步是不是某个锚点在窗口里（§十四）。同一个锚点在一个计划里只排一条。"""
        due = [item for item in anchors.due(ctx.clock, t) if item.anchor_id not in planned]
        if not due:
            return _Step()
        chosen = due[0]
        profile = profile_for(chosen.activity)
        _start, window_end = chosen.window(ctx.clock, t)
        # 锚点条目覆盖"这顿/这觉"本身：至少 typical，最多不超过它自己的硬上限
        end = min(max(t + float(profile.typical_duration), float(window_end)), ctx.horizon_end)
        end = min(end, t + float(profile.max_duration))
        if end <= t:
            end = min(t + float(profile.min_duration), ctx.horizon_end)
        return _Step(
            activity=chosen.activity,
            reason=ItemReason.ANCHOR.value,
            anchor_id=chosen.anchor_id,
            start=t,
            end=max(t, end),
        )

    def _free_step(
        self,
        ranked: list[Candidate],
        ctx: PlannerContext,
        *,
        t: float,
        avoid: tuple[str, ...],
        horizon_end: float,
        planned: set[str],
    ) -> _Step:
        """自由时段的一步（§十六/§三十三/§三十四）。

        **按 ``t`` 重新判资格**：``plan.candidates`` 里那份判定说明的是"**现在**为什么选它"；
        时间往前走，锚点窗口会开也会关 —— 拿规划时刻的结论去排三小时后的事，计划就是假的。
        重新判定很便宜（≤6 个候选的纯算术），而且它让"装不下就先别开始"（§十五）真的成立。

        返回没有活动名的 :class:`_Step` 表示"这一步不排"：此时 ``jump_to`` 指向下一个锚点窗口，
        调用方据此继续（或收工）。
        """
        moment = replace(ctx, now=float(t), planned_anchors=frozenset(planned))
        anchors = moment.anchors or self.anchors
        next_anchor = anchors.next_after(moment.clock, t)
        gap = horizon_end - t
        if next_anchor is not None:
            window_start = anchors_window_start(next_anchor, moment.clock, t)
            if window_start > t:
                gap = min(gap, window_start - t)
        blocked = {name for name in avoid if name}
        pick: Candidate | None = None
        relaxed: Candidate | None = None
        for candidate in self._rank(self._judge_all(ranked, moment)):
            if not candidate.eligible:
                continue
            if float(profile_for(candidate.activity).min_duration) > gap:
                continue
            if relaxed is None:
                relaxed = candidate
            if candidate.activity in blocked:
                continue
            pick = candidate
            break
        if pick is None:
            # 相邻不同名只是**偏好**：宁可重复，也不能把计划排空（§四十八：绝不留空）
            pick = relaxed
        if pick is None:
            if next_anchor is not None:
                window_start = anchors_window_start(next_anchor, moment.clock, t)
                if window_start > t:
                    return _Step(jump_to=min(window_start, horizon_end))
            fallback = self._fallback_activity(moment, avoid=blocked)
            if not fallback:
                return _Step(jump_to=horizon_end)
            return _Step(
                activity=fallback,
                reason=ItemReason.FALLBACK.value,
                start=t,
                end=min(t + float(profile_for(fallback).typical_duration), horizon_end),
            )
        profile = profile_for(pick.activity)
        # 到此为止：既不超过 typical，也不越过下一个锚点的窗口起点（§十五）
        end = min(t + float(profile.typical_duration), t + gap, horizon_end)
        reason = self._item_reason(pick, moment, at=t)
        anchor_id = pick.anchor_id if reason == ItemReason.ANCHOR.value else ""
        return _Step(
            activity=pick.activity,
            intent_id=str(pick.intent_id or ""),
            reason=reason,
            anchor_id=anchor_id,
            start=t,
            end=max(t, end),
        )

    def _judge_all(self, ranked: list[Candidate], ctx: PlannerContext) -> list[Candidate]:
        """按给定时刻重新判一遍资格（顺序不变，``order`` 保留，便于稳定排序）。"""
        return [self._judge(item.activity, ctx, order=item.order) for item in ranked]

    def _item_reason(self, candidate: Candidate, ctx: PlannerContext, *, at: float) -> str:
        """这条计划项**为什么**被排进来（§四：计划项必须带 reason）。

        优先级：锚点（**真的在窗口里**，而且这个计划还没排过它）> 目标 > 自由活动 > 时段习惯。
        已经排过的锚点不再认领新条目 —— 否则一顿饭会被排成好几条一样的（§十九）。
        """
        if candidate.anchor_id and candidate.anchor_id not in ctx.planned_anchors:
            for anchor in (ctx.anchors or self.anchors).all():
                if anchor.anchor_id == candidate.anchor_id and anchor.is_due(ctx.clock, at):
                    return ItemReason.ANCHOR.value
        if candidate.goal_id and float(candidate.breakdown.get("goal_relevance", 0.0)) > 0.0:
            return ItemReason.GOAL.value
        if candidate.activity in FREE_ACTIVITY_POOL:
            return ItemReason.FREE.value
        return ItemReason.ROUTINE.value

    def _merge(self, items: list[PlanItem], step: _Step, *, profile: Any) -> bool:
        """相邻同名合并（§十九）：能延长就延长，别排两条一样的；超过硬上限就不合。

        合并时**理由向上取**（ANCHOR > 别的）：一条"习惯上的吃饭"被锚点追上之后，
        这条计划项就是"锚点安排的饭" —— 审计要看得见这件事（§四）。
        两个不同锚点的条目绝不合并（那会是两件事）。
        """
        if not items or not step.activity:
            return False
        last = items[-1]
        if last.activity != step.activity:
            return False
        if step.reason == ItemReason.ANCHOR.value:
            if last.reason == ItemReason.ANCHOR.value and last.anchor_id != step.anchor_id:
                return False
            if last.reason == ItemReason.CONTINUATION.value:
                return False  # 现实正在做的那条要保原样
        grown = float(step.end) - float(last.planned_start)
        if grown > float(profile.max_duration):
            return False
        if float(step.end) <= float(last.planned_end):
            # 没有实际增长：也算"合掉了"（否则会原地打转，把计划排成无限循环）
            return True
        reason = last.reason
        anchor_id = last.anchor_id
        goal_id = last.goal_id
        if step.reason == ItemReason.ANCHOR.value and step.anchor_id:
            reason, anchor_id = ItemReason.ANCHOR.value, step.anchor_id
        items[-1] = PlanItem(
            activity=last.activity,
            planned_start=last.planned_start,
            planned_end=float(step.end),
            reason=reason,
            priority=last.priority,
            anchor_id=anchor_id,
            goal_id=goal_id,
            score=last.score,
        )
        return True

    def _fallback_activity(self, ctx: PlannerContext, *, avoid: Any = ()) -> str:
        """§四十八/§四十九：没有可用候选时的**确定性**兜底（绝不把活动置空）。

        能量低或夜里 → ``resting``；否则 → ``free_time``。
        """
        blocked = {str(name) for name in avoid if name}
        preferred = (
            "resting"
            if (ctx.energy < LOW_ENERGY_THRESHOLD or ctx.period == "night")
            else "free_time"
        )
        if preferred not in blocked:
            return preferred
        for name in FALLBACK_ACTIVITIES:
            if name not in blocked:
                return name
        return FALLBACK_ACTIVITIES[0]

    def _item_priority(self, activity: str, reason: str, *, anchors: AnchorBook) -> float:
        """计划项的优先级（展示/审计用）：锚点最强，兜底最弱。"""
        if reason == ItemReason.ANCHOR.value:
            for anchor in anchors.all():
                if anchor.activity == activity:
                    return anchor.priority.weight
            return 0.8
        if reason == ItemReason.GOAL.value:
            return 0.7
        if reason == ItemReason.ROUTINE.value:
            return 0.5
        if reason == ItemReason.FREE.value:
            return 0.3
        return 0.1

    def _plan_source(self, items: tuple[PlanItem, ...]) -> str:
        """计划的来源标签（§四 的 ``source``）：锚点/目标/习惯/自由/兜底/混合。"""
        reasons = {item.reason for item in items}
        if not reasons:
            return "EMPTY"
        if len(reasons) == 1:
            return next(iter(reasons))
        return "MIXED"

    def _constraints(self, ctx: PlannerContext) -> dict[str, Any]:
        """当时的硬约束快照（只用于审计/解释，Planner 不拿它改任何东西）。"""
        anchors = ctx.anchors or self.anchors
        payload: dict[str, Any] = {
            "state": ctx.state_payload(),
            "horizon_seconds": round(float(ctx.horizon_seconds), 3),
            "max_items": int(ctx.max_items),
            "max_candidates": int(ctx.max_candidates),
            "anchors": [anchor.anchor_id for anchor in anchors.all()],
            "trigger": str(ctx.trigger or ""),
        }
        if ctx.goals is not None:
            payload["goals_signature"] = ctx.goals.signature()
            payload["open_goals"] = [goal.goal_id for goal in ctx.goals.open_goals]
        payload["weights"] = {key: float(value) for key, value in sorted(self.weights.items())}
        # Phase 7B §十二：这次规划**真的采纳了哪些建议**（审计用；不参与内容签名，§五）
        payload["initiative"] = ctx.hints.payload()
        return payload

    def _hint_book(self, *, now: float) -> InitiativeHintBook:
        """读一次意图建议（bounded / 只读 / 可降级）。"""
        return hint_book_from(self.intent_source, now=float(now))

    def preview_plan(
        self,
        *,
        episode: ActivityEpisode | None = None,
        now: float,
        clock: Any,
        started_today: int = 0,
    ) -> ActivityPlan:
        """只读预览一份计划（**不落盘**）—— 给 6D 顾问看候选与未来安排。

        Planner 负责候选生成 / 资格 / 排序（§二十五），顾问只拿到摘要；这里不产生任何副作用。
        """
        return self.plan_next(
            self._state(),
            episode,
            now=float(now),
            context=self._context(
                clock, float(now), started_today=int(started_today), episode=episode
            ),
        )

    @property
    def routine_table(self) -> dict[str, tuple[str, ...]]:
        """时段 → 候选活动的**只读**快照（顾问的 routine_candidates 用它）。"""
        return {period: tuple(names) for period, names in self._routine.items()}

    # ------------------------------------------------------------ 内部

    def _can_extend(self, episode: ActivityEpisode) -> bool:
        """能不能再延长一次：活动可延长 + 没超过次数上限 + 现在还没到硬上限。"""
        if str(episode.activity_name or "") not in EXTENDABLE_ACTIVITIES:
            return False
        if int(episode.extension_count or 0) >= MAX_AUTO_EXTENSIONS:
            return False
        return float(episode.typical_duration or 0.0) > 0.0

    def _pick(self, period: str, *, started_today: int, avoid: str) -> str:
        """确定性挑一个名字（6A 的时段旋转；``initial`` 的测试与审计仍会看到它）。"""
        candidates = self._routine.get(period) or ("idle",)
        index = int(max(0, started_today)) % len(candidates)
        name = candidates[index]
        if name == avoid and len(candidates) > 1:
            name = candidates[(index + 1) % len(candidates)]
        return name

    def _clock(self) -> Any:
        """给没有显式 clock 的调用兜一个世界时钟（只在纯计算入口用到）。"""
        try:
            from app.activity.clock import WorldClock

            return WorldClock()
        except Exception:  # noqa: BLE001 - 时钟都造不出来就不该阻塞规划
            return _NullClock()

    def _state(self) -> Any:
        """同步读一次角色状态（只读；失败当中性，绝不抛，§四十八）。"""
        if self.state_provider is None:
            return None
        try:
            return self.state_provider()
        except Exception:  # noqa: BLE001
            return None

    def goals_snapshot(self) -> GoalSnapshot | None:
        """只读目标快照（公开入口：runtime 也要用它判"目标变了没"，§八 触发点 4）。"""
        return self._goals()

    def _goals(self) -> GoalSnapshot | None:
        source = self.goal_source
        if source is None:
            return None
        try:
            return source.snapshot()
        except Exception:  # noqa: BLE001 - 目标读不到就不加分，绝不阻塞规划
            return GoalSnapshot(source="error", degraded_reason="snapshot_failed")

    def _context(
        self,
        clock: Any,
        now: float,
        *,
        started_today: int,
        episode: ActivityEpisode | None,
        avoid: str = "",
        trigger: str = "",
    ) -> PlannerContext:
        """组装 6A 两个入口要用的上下文（它们没有这些参数，只能从实例上取）。

        ``avoid`` 非空 = "刚结束的那个活动"：它只能进 **history**（去影响重复惩罚），
        绝不能作为 ``CONTINUATION`` 再次出现在计划里 —— 这正是 ``next_after`` 的语义
        （Episode 刚到期，计划必须从"下一件事"开始）。
        """
        history: tuple[ActivityEpisode, ...] = ()
        if episode is not None and avoid:
            history = (episode,)
        return PlannerContext(
            clock=clock,
            now=float(now),
            character_state=self._state(),
            current_episode=None if avoid else episode,
            history=history,
            goals=self._goals(),
            anchors=self.anchors,
            started_today=int(started_today),
            horizon_seconds=DEFAULT_HORIZON_SECONDS,
            max_items=self.max_items,
            max_candidates=self.max_candidates,
            trigger=trigger,
        )

    def _first_activity(self, plan: ActivityPlan, avoid: str = "") -> str:
        """计划里"下一步做什么"（给 6A 的两个入口用）。

        ``CONTINUATION`` 条目说的是"现在这件事继续"，不是一个"新选择" ——
        对 ``next_after``（Episode 刚到期）来说必须跳过它，否则就成了"还做同一件事"。
        """
        for item in plan.items:
            if item.reason == ItemReason.CONTINUATION.value:
                continue
            if avoid and item.activity == avoid:
                continue
            return item.activity
        fallback = FALLBACK_ACTIVITIES[0]
        for name in FALLBACK_ACTIVITIES:
            if name != avoid:
                return name
        return fallback


class _NullClock:
    """连世界时钟都造不出来时的最后兜底（只有测试环境才可能发生）。"""

    def period(self, at: float | None = None) -> str:
        return "afternoon"

    def local_minute(self, at: float | None = None) -> int:
        return 12 * 60

    def local_weekday(self, at: float | None = None) -> int:
        return 0

    def day_start(self, at: float | None = None) -> float:
        return 0.0

    def day_key(self, at: float | None = None) -> str:
        return "19700101"


def anchors_window_start(anchor: ScheduleAnchor, clock: Any, at: float) -> float:
    """锚点今天窗口的起点（``at`` 之前的话就是过去的时间；调用方自己比大小）。"""
    start, _end = anchor.window(clock, at)
    if start > float(at):
        return start
    # 已经进过窗口又出来了：看明天那一次（rolling horizon 最多几小时，够用）
    return anchor.window(clock, float(at) + 86400.0)[0]


#: 日程表里用到的名字必须有 duration 档（否则用默认档也能跑，但这里保证有据可查）
def known_routine_names() -> frozenset[str]:
    names: set[str] = set()
    for entries in ROUTINE_BY_PERIOD.values():
        names.update(entries)
    return frozenset(names)


def missing_durations() -> frozenset[str]:
    """日程表里没有时长档的名字（测试会用；空集 = 全部有据可查）。"""
    return frozenset(name for name in known_routine_names() if name not in VIRTUAL_DURATIONS)


def period_category_fit() -> dict[str, tuple[frozenset[str], frozenset[str]]]:
    """时段—类别契合表的只读快照（测试/文档用）。"""
    return {
        key: (frozenset(value[0]), frozenset(value[1]))
        for key, value in PERIOD_CATEGORY_FIT.items()
    }


__all__ = [
    "ActivityDecision",
    "ActivityPlanner",
    "DEFAULT_HORIZON_SECONDS",
    "DEFAULT_MAX_CANDIDATES",
    "DEFAULT_MAX_ITEMS",
    "EXTENDABLE_ACTIVITIES",
    "FALLBACK_ACTIVITIES",
    "MAX_AUTO_EXTENSIONS",
    "MIN_CANDIDATES",
    "PlannerContext",
    "ROUTINE_BY_PERIOD",
    "anchors_window_start",
    "known_routine_names",
    "missing_durations",
    "period_category_fit",
]
