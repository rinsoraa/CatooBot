"""Phase 7A §十六-§十九/§二十八/§三十二/§五十三：**确定性**候选意图生成。

输入只有一个 :class:`InitiativeContext`（bounded 的只读事实快照），输出 0..N 条
:class:`LifeIntent`（``status=PROPOSED``）。这一层：

* **不联网、不调模型、不随机**（§三十/§六十一/§六十二）——同一个 context 永远给出同一批候选；
* **不写任何东西**（写只发生在 service/gate 之后）；
* 只 import :mod:`app.initiative.model` 与标准库 —— 于是"能不能执行"在这里是**结构上不可能**的。

候选生成的边界（刻意的低噪声设计，§五十三/§六十）：

* **ACTIVITY_CONTINUATION 不由这里产生** —— "继续 gaming" 属于 6B/6C 的日常连续性，
  不是 Initiative（§五十三）。Initiative 只表示**额外的动机 / 新想法**；
* 只有"长闲置"（§三十二：``idle``/``free_time``/``resting`` 持续 ≥ 2 小时）才问"要不要做点别的"；
* 每条候选都有一个**确定性语义键**，配合时间桶构成 §二十 的指纹。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field

from app.initiative.model import (
    InitiativeSource,
    LifeIntent,
    LifeIntentStatus,
    LifeIntentType,
    execution_class_for,
    intent_fingerprint,
    intent_ttl_seconds,
    time_bucket,
)

# ---------------------------------------------------------------- 常量（确定性）

#: §三十二：只有"长闲置"才考虑"要不要做点别的"（1 分钟的 free_time 不算）
LONG_IDLE_SECONDS = 2 * 3600.0

#: 什么算"闲置"活动
IDLE_ACTIVITIES = ("idle", "free_time", "resting")

#: 什么算"休息"活动（REST 意图不会对它们重复提议）
REST_ACTIVITIES = ("resting", "napping", "sleeping", "idle", "free_time")

#: §二十八：低能量才提"要不要歇会儿"
REST_ENERGY_THRESHOLD = 0.3

#: 与 Minecraft 有关的**目标类型**（结构性判据，不靠关键词）
MINECRAFT_GOAL_KINDS = ("complete_project", "restock_resource")

#: 自由文本（记忆 / 承诺）的关键词判据。**真机教训**：她自己的记忆写的是"橡**树** / 原**木**"，
#: 所以判据必须有**结构化**的那一路（记忆的 domain / scope），关键词只作补充。
MINECRAFT_KEYWORDS = (
    "minecraft",
    "mc ",
    "橡木",
    "橡树",
    "原木",
    "挖矿",
    "矿物",
    "砍树",
    "小城",
    "服务器",
    "my world",
)

#: 结构化判据：记忆/承诺的 domain 值
MINECRAFT_DOMAINS = ("minecraft", "mc")

#: 探索类信号的确定性关键词
EXPLORE_KEYWORDS = ("探索", "去看看", "远行", "explore", "探险")

#: 一次最多看几条（bounded，§六十三）
#: ★真机教训：记忆窗口原来是 3 —— 一条"想去 Minecraft"的记忆被三条日常记忆一挤就再也
#: 触不到（真机 06:5x 实测：MC 记忆稳定停在第 4 位）。放宽到 5（**仍然有界**，
#: 与写侧 `MEMORY_SCAN_LIMIT` 同宽）。
MAX_GOAL_SIGNALS = 3
MAX_MEMORY_SIGNALS = 5
MAX_SOCIAL_SIGNALS = 2
MAX_COMMITMENT_SIGNALS = 2


# ---------------------------------------------------------------- 只读输入


@dataclass(frozen=True)
class GoalSignal:
    """一条开放目标的**只读**摘要（绝不复制 Goal 的生命周期，只引用 goal_id）。"""

    goal_id: str
    kind: str = ""
    label: str = ""
    progress: float = 0.0
    priority: float = 0.5
    blocked: bool = False


@dataclass(frozen=True)
class MemorySignal:
    """一条记忆的**只读**摘要（`Memory != permission`，§十七）。

    ``domain`` 是结构化判据（例如 ``"minecraft"``）—— 真机上她的 Minecraft 记忆
    写在 ``character:<角色>:minecraft`` 作用域、provenance 里带 ``domain=minecraft``，
    比"内容里有没有某个词"可靠得多。
    """

    text: str
    domain: str = ""
    scope: str = ""
    importance: float = 0.5


@dataclass(frozen=True)
class CommitmentSignal:
    """一条"答应了别人"的只读摘要（§十九 的 USER_CONTEXT 来源）。"""

    commitment_id: str
    kind: str = ""
    label: str = ""
    player: str = ""
    due_at: float = 0.0


@dataclass(frozen=True)
class InitiativeContext:
    """门禁与候选生成要的**全部**只读输入（构造它的适配器负责 bounded）。

    这里**没有**任何执行句柄、没有 store、没有 Policy、没有任务对象 ——
    只有事实。拿不到的字段保持默认值，绝不猜。
    """

    character_id: str
    now: float
    period: str = ""
    # ---- 现实（ActivityEpisode）
    current_activity: str = ""
    current_activity_elapsed: float = 0.0
    current_activity_status: str = ""
    recent_activities: tuple[str, ...] = ()
    # ---- 生活状态
    energy: float = 1.0
    focus: float = 0.5
    mood: str = ""
    sleeping: bool = False
    quiet_hours: bool = False
    #: 社交疲劳（§十 HIGH_SOCIAL_FATIGUE）—— 由只读适配器从既有社交层读出，缺省 0
    social_fatigue: float = 0.0
    # ---- 只读信号
    goals: tuple[GoalSignal, ...] = ()
    commitments: tuple[CommitmentSignal, ...] = ()
    memories: tuple[MemorySignal, ...] = ()
    social_topics: tuple[str, ...] = ()
    routine_due: tuple[str, ...] = ()
    planner_candidates: tuple[str, ...] = ()
    # ---- 任务与交互
    task_states: tuple[str, ...] = ()
    pending_confirmation: bool = False
    user_interaction_at: float = 0.0
    last_initiative_at: float = 0.0
    # ---- 世界
    minecraft_online: bool = False
    degraded: str = ""
    recovered_at: float = 0.0
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------ 便捷判定

    @property
    def current(self) -> str:
        return str(self.current_activity or "").strip().lower()

    def is_idle(self) -> bool:
        return self.current in IDLE_ACTIVITIES

    def is_resting(self) -> bool:
        return self.current in REST_ACTIVITIES

    def minecraft_related(self, text: str, *, domain: str = "") -> bool:
        """结构化判据优先，关键词兜底（两条都是确定性的）。"""
        if str(domain or "").strip().lower() in MINECRAFT_DOMAINS:
            return True
        lowered = str(text or "").lower()
        return any(keyword in lowered for keyword in MINECRAFT_KEYWORDS)


def _label(text: str, limit: int = 60) -> str:
    cleaned = " ".join(str(text or "").split())
    return cleaned[:limit]


def _priority(base: float, *, relevance: float = 0.0, urgency: float = 0.0) -> float:
    """§二十八：priority ∈ [0,1]，来自确定性项（goal relevance / continuity / urgency…）。

    **priority ≠ permission**：再高的优先级也绕不过 §十 的 hard guard。
    """
    value = 0.35 + 0.45 * max(0.0, min(1.0, base)) + 0.15 * max(0.0, min(1.0, relevance))
    value += 0.05 * max(0.0, min(1.0, urgency))
    return round(max(0.0, min(1.0, value)), 4)


def _intent(
    context: InitiativeContext,
    *,
    intent_type: LifeIntentType,
    source: InitiativeSource,
    title: str,
    description: str,
    semantic_key: str,
    origin: str,
    priority: float,
    confidence: float,
    related_goal: str = "",
    related_activity: str = "",
    related_memory: str = "",
    related_player: str = "",
    tags: tuple[str, ...] = (),
) -> LifeIntent:
    now = float(context.now)
    return LifeIntent(
        intent_id="",  # id 由 store 在**真正落盘**那一刻分配（§四十六：幂等）
        character_id=str(context.character_id),
        intent_type=intent_type,
        title=_label(title, 80),
        description=_label(description, 200),
        source=source,
        origin=origin,
        priority=priority,
        created_at=now,
        expires_at=now + intent_ttl_seconds(intent_type),
        related_activity=str(related_activity or ""),
        related_goal=str(related_goal or ""),
        related_memory=str(related_memory or ""),
        related_player=str(related_player or ""),
        status=LifeIntentStatus.PROPOSED,
        confidence=confidence,
        fingerprint=intent_fingerprint(
            character_id=context.character_id,
            intent_type=intent_type,
            semantic_key=semantic_key,
            related_goal=related_goal,
            related_activity=related_activity,
            bucket=time_bucket(now),
        ),
        execution_class=execution_class_for(intent_type),
        tags=tags,
    )


# ---------------------------------------------------------------- 候选生成


def propose_intents(context: InitiativeContext) -> tuple[LifeIntent, ...]:
    """把只读 context 变成候选意图（确定性顺序：优先级降序 → 类型 → 指纹）。"""
    candidates: list[LifeIntent] = []
    candidates.extend(_routine_intents(context))
    candidates.extend(_goal_intents(context))
    candidates.extend(_memory_intents(context))
    candidates.extend(_social_intents(context))
    candidates.extend(_commitment_intents(context))
    candidates.extend(_rest_intents(context))
    candidates.extend(_long_idle_intents(context))
    unique: dict[str, LifeIntent] = {}
    for intent in candidates:
        unique.setdefault(intent.fingerprint, intent)
    return tuple(
        sorted(
            unique.values(),
            key=lambda item: (
                -float(item.priority),
                item.intent_type.value,
                item.fingerprint,
            ),
        )
    )


def _routine_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§五/§三十一：时段日常里"今天还没做过"的那一件（读适配器算好的 routine_due）。"""
    due = [name for name in context.routine_due[:2] if name and name != context.current]
    if not due or context.sleeping:
        return ()
    activity = str(due[0])
    return (
        _intent(
            context,
            intent_type=LifeIntentType.PERSONAL_ROUTINE,
            source=InitiativeSource.ROUTINE,
            title=f"今天还没怎么{activity}",
            description=f"按她的日常，这个时段本来有「{activity}」，今天还没轮到。",
            semantic_key=f"routine:{activity}",
            origin=f"routine_due({context.period})",
            priority=_priority(0.45, relevance=0.2),
            confidence=0.5,
            related_activity=activity,
            tags=("routine",),
        ),
    )


def _goal_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§十六：开放目标 → GOAL_PROGRESS；与 Minecraft 有关的目标 → MINECRAFT_INTEREST。

    ``Goal != Task``：这里只产生**意图**，"怎么去做"属于未来的执行层（§八/§四十一）。
    """
    out: list[LifeIntent] = []
    for goal in context.goals[:MAX_GOAL_SIGNALS]:
        if goal.blocked or goal.progress >= 1.0:
            continue
        minecraft = str(goal.kind) in MINECRAFT_GOAL_KINDS or context.minecraft_related(goal.label)
        label = _label(goal.label or goal.goal_id, 40)
        if minecraft:
            out.append(
                _intent(
                    context,
                    intent_type=LifeIntentType.MINECRAFT_INTEREST,
                    source=InitiativeSource.GOAL,
                    title=f"有点想回 Minecraft 推进「{label}」",
                    description="想起那个还没做完的项目，想去看看、再动一点。",
                    semantic_key=f"mc_goal:{goal.goal_id}",
                    origin=f"goal:{goal.goal_id}({goal.kind})",
                    priority=_priority(goal.priority, relevance=goal.priority, urgency=0.1),
                    confidence=0.6,
                    related_goal=goal.goal_id,
                    tags=("goal", "virtual_interest" if not context.minecraft_online else "world"),
                )
            )
            continue
        out.append(
            _intent(
                context,
                intent_type=LifeIntentType.GOAL_PROGRESS,
                source=InitiativeSource.GOAL,
                title=f"想推进一下「{label}」",
                description="这件事还开着，今天可以再往前一点。",
                semantic_key=f"goal:{goal.goal_id}:{round(float(goal.progress), 2)}",
                origin=f"goal:{goal.goal_id}({goal.kind})",
                priority=_priority(goal.priority, relevance=goal.priority),
                confidence=0.6,
                related_goal=goal.goal_id,
                tags=("goal",),
            )
        )
    return out


def _memory_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§十七：Memory 只是**支持性上下文**（``Memory ≠ permission``）。"""
    out: list[LifeIntent] = []
    for memory in context.memories[:MAX_MEMORY_SIGNALS]:
        text = str(getattr(memory, "text", memory) or "")
        domain = str(getattr(memory, "domain", "") or "")
        if not context.minecraft_related(text, domain=domain):
            continue
        out.append(
            _intent(
                context,
                intent_type=LifeIntentType.MINECRAFT_INTEREST,
                source=InitiativeSource.MEMORY,
                title="有点想回 Minecraft 看看",
                description=f"最近记着这件事：{_label(text, 50)}",
                semantic_key="mc_memory",
                origin=f"memory:{domain or 'minecraft'}",
                priority=_priority(0.3, relevance=0.3),
                confidence=0.45,
                related_memory=_label(text, 60),
                tags=("memory", "virtual_interest"),
            )
        )
        break
    if not out:
        for memory in context.memories[:MAX_MEMORY_SIGNALS]:
            text = str(getattr(memory, "text", memory) or "")
            if any(keyword in text.lower() for keyword in EXPLORE_KEYWORDS):
                out.append(
                    _intent(
                        context,
                        intent_type=LifeIntentType.EXPLORATION,
                        source=InitiativeSource.MEMORY,
                        title="有点想去外面转转",
                        description=f"想起：{_label(text, 50)}",
                        semantic_key="explore_memory",
                        origin="memory:explore",
                        priority=_priority(0.28, relevance=0.25),
                        confidence=0.4,
                        related_memory=_label(text, 60),
                        tags=("memory",),
                    )
                )
                break
    return out


def _social_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§十八：Social Cognition 只能形成**候选意图**，永远不能直接发消息。"""
    out: list[LifeIntent] = []
    for topic in context.social_topics[:MAX_SOCIAL_SIGNALS]:
        label = _label(topic, 40)
        if not label:
            continue
        out.append(
            _intent(
                context,
                intent_type=LifeIntentType.SOCIAL,
                source=InitiativeSource.SOCIAL,
                title=f"想看看大家在聊什么（{label}）",
                description="群里最近老提到这个话题，想知道后续。",
                semantic_key=f"social:{label}",
                origin="social:group_topic",
                priority=_priority(0.4, relevance=0.35),
                confidence=0.5,
                tags=("social",),
            )
        )
    return out


def _commitment_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§十九：用户/朋友明确说过的话（"晚上一起玩"）→ USER_CONTEXT 声明,不是立即行动。"""
    out: list[LifeIntent] = []
    for commitment in context.commitments[:MAX_COMMITMENT_SIGNALS]:
        label = _label(commitment.label, 50)
        if not label:
            continue
        out.append(
            _intent(
                context,
                intent_type=LifeIntentType.MINECRAFT_INTEREST
                if context.minecraft_related(label)
                else LifeIntentType.PERSONAL_ROUTINE,
                source=InitiativeSource.USER_CONTEXT,
                title=f"记着答应过的事：{label}",
                description="这是未来上下文，不是立刻要做的事。",
                semantic_key=f"commitment:{commitment.commitment_id}",
                origin=f"commitment:{commitment.commitment_id}({commitment.kind})",
                priority=_priority(0.55, relevance=0.4, urgency=0.3),
                confidence=0.6,
                related_player=str(commitment.player or ""),
                tags=("commitment", "user_context"),
            )
        )
    return out


def _rest_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§二十八：低能量 → REST 意图（仍然只是意图；要不要睡由 6B/6C 决定）。"""
    if context.sleeping or context.is_resting():
        return ()
    if float(context.energy) >= REST_ENERGY_THRESHOLD:
        return ()
    return (
        _intent(
            context,
            intent_type=LifeIntentType.REST,
            source=InitiativeSource.SYSTEM,
            title="有点累了，想歇会儿",
            description=f"现在精力只有 {float(context.energy):.2f}，接下来更适合轻轻的安排。",
            semantic_key="low_energy",
            origin="state:energy",
            priority=_priority(0.5, relevance=0.3, urgency=0.4),
            confidence=0.5,
            tags=("state",),
        ),
    )


def _long_idle_intents(context: InitiativeContext) -> Iterable[LifeIntent]:
    """§三十二：``idle``/``free_time``/``resting`` 持续 ≥ 2 小时才问"要不要做点别的"。"""
    if context.sleeping or context.quiet_hours:
        return ()
    if not context.is_idle():
        return ()
    if float(context.current_activity_elapsed) < LONG_IDLE_SECONDS:
        return ()
    suggestion = next(
        (name for name in context.planner_candidates if name and name != context.current),
        "",
    )
    if not suggestion:
        suggestion = next((name for name in context.routine_due if name != context.current), "")
    if not suggestion:
        return ()
    hours = float(context.current_activity_elapsed) / 3600.0
    return (
        _intent(
            context,
            intent_type=LifeIntentType.ACTIVITY_CHANGE,
            source=InitiativeSource.ACTIVITY,
            title=f"闲太久了，想去做点别的（比如{suggestion}）",
            description=f"已经「{context.current}」了 {hours:.1f} 小时，换个节奏也许更好。",
            semantic_key=f"long_idle:{context.current}",
            origin=f"activity:{context.current}",
            priority=_priority(0.5, relevance=0.3, urgency=0.2),
            confidence=0.5,
            related_activity=str(suggestion),
            tags=("long_idle",),
        ),
    )
