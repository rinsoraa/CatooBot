"""Phase 7A §三-§九/§四十二：LifeIntent —— "她**想**去做什么"的领域模型。

三个概念必须严格分开（§七/§八）：

* :class:`ActivityEpisode` = **现实状态**（她此刻正在做什么，6A 的唯一事实来源）；
* :class:`LifeIntent` = **动机 / 想法**（未来可能发生什么，本模块）；
* Task = **执行计划**（怎么去做，5A 的 TaskRuntime）。

本层只允许：**提出 / 评估 / 记录 / 抑制 / 过期**。执行层恒为 ``NONE``
（§四十一/§六十八）：这里既没有 ``EXECUTING``/``RUNNING`` 状态，也没有任何执行入口。
``LifeIntentExecutionClass.AUTONOMOUS_TASK_CANDIDATE`` 只是**枚举值**（§四十二），
7A 永远不会给它赋值（见 :func:`execution_class_for`）——
"我想做什么"与"我被允许做什么"在这里永久分开（§七十一）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

# ---------------------------------------------------------------- 状态（§四）


class LifeIntentStatus(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """意图状态（§四）。**没有** EXECUTING / RUNNING —— ``LifeIntent != Task``。"""

    PROPOSED = "PROPOSED"
    SUPPRESSED = "SUPPRESSED"
    EXPIRED = "EXPIRED"
    CANCELLED = "CANCELLED"
    RESOLVED = "RESOLVED"

    @property
    def terminal(self) -> bool:
        return self in TERMINAL_INTENT_STATUSES

    @property
    def open(self) -> bool:
        """还"挂着"的状态（可以继续被评估 / 推进）。"""
        return self in OPEN_INTENT_STATUSES


#: 终态：到了这里这条意图就结束了，永远不会再回到 PROPOSED（历史不可改写，§二十四）
TERMINAL_INTENT_STATUSES = frozenset(
    {LifeIntentStatus.EXPIRED, LifeIntentStatus.CANCELLED, LifeIntentStatus.RESOLVED}
)

#: 还开着的状态
OPEN_INTENT_STATUSES = frozenset({LifeIntentStatus.PROPOSED, LifeIntentStatus.SUPPRESSED})

#: 允许的状态转移（其余一律拒绝）。注意：**没有**回到 PROPOSED 的路 ——
#: §二十六 要求的"抑制之后还能再出现"靠**新的一条意图**实现（新 id、新指纹），
#: 而不是把旧的那条复活。这样历史永远是追加式的。
ALLOWED_INTENT_TRANSITIONS: dict[LifeIntentStatus, frozenset[LifeIntentStatus]] = {
    LifeIntentStatus.PROPOSED: frozenset(
        {
            LifeIntentStatus.SUPPRESSED,
            LifeIntentStatus.EXPIRED,
            LifeIntentStatus.CANCELLED,
            LifeIntentStatus.RESOLVED,
        }
    ),
    LifeIntentStatus.SUPPRESSED: frozenset(
        {
            LifeIntentStatus.EXPIRED,
            LifeIntentStatus.CANCELLED,
            LifeIntentStatus.RESOLVED,
        }
    ),
    LifeIntentStatus.EXPIRED: frozenset(),
    LifeIntentStatus.CANCELLED: frozenset(),
    LifeIntentStatus.RESOLVED: frozenset(),
}


def intent_transition_allowed(current: LifeIntentStatus, target: LifeIntentStatus) -> bool:
    """状态机是否允许这条转移（非法转移直接拒绝，不靠"看起来对"）。"""
    return target in ALLOWED_INTENT_TRANSITIONS.get(current, frozenset())


# ---------------------------------------------------------------- 类型 / 来源


class LifeIntentType(str, Enum):  # noqa: UP042
    """意图类型（§五，第一版只允许这个有限集合）。"""

    ACTIVITY_CONTINUATION = "ACTIVITY_CONTINUATION"
    ACTIVITY_CHANGE = "ACTIVITY_CHANGE"
    GOAL_PROGRESS = "GOAL_PROGRESS"
    SOCIAL = "SOCIAL"
    REST = "REST"
    EXPLORATION = "EXPLORATION"
    PERSONAL_ROUTINE = "PERSONAL_ROUTINE"
    #: 只是"想玩 Minecraft / 想去看看"（§五/§三十六）—— **不是**"立即执行 Minecraft Task"
    MINECRAFT_INTEREST = "MINECRAFT_INTEREST"


class InitiativeSource(str, Enum):  # noqa: UP042
    """意图的来源（§六）。**没有 RANDOM**：随机性不能决定"为什么她突然想做某事"。"""

    ROUTINE = "ROUTINE"
    GOAL = "GOAL"
    ACTIVITY = "ACTIVITY"
    MEMORY = "MEMORY"
    WORLD_EVENT = "WORLD_EVENT"
    SOCIAL = "SOCIAL"
    USER_CONTEXT = "USER_CONTEXT"
    SYSTEM = "SYSTEM"


class LifeIntentExecutionClass(str, Enum):  # noqa: UP042
    """未来执行层的预留分类（§四十二）。7A 只允许第一种（见 :func:`execution_class_for`）。"""

    VIRTUAL_ONLY = "VIRTUAL_ONLY"
    #: 未来阶段：可以变成"给用户的提议"，仍然不是执行
    USER_TASK_PROPOSAL = "USER_TASK_PROPOSAL"
    #: 未来阶段才可能出现；7A 里**只是枚举值**，永远不会被赋值，更不会被执行
    AUTONOMOUS_TASK_CANDIDATE = "AUTONOMOUS_TASK_CANDIDATE"


def execution_class_for(intent_type: LifeIntentType | str) -> LifeIntentExecutionClass:
    """7A 的唯一映射：**全部**是 ``VIRTUAL_ONLY``。

    刻意不在这一层做"按类型升级执行类"的判断 —— 那需要授权 / 确认 / Policy（§六十八），
    属于未来阶段的执行层，7A 的执行层是 ``NONE``。
    """
    return LifeIntentExecutionClass.VIRTUAL_ONLY


#: 每类意图的存活时间（§二十二：过几个小时没有执行意义就 EXPIRED，绝不无限挂着）
INTENT_TTL_SECONDS: dict[str, float] = {
    LifeIntentType.ACTIVITY_CHANGE.value: 3 * 3600.0,
    LifeIntentType.ACTIVITY_CONTINUATION.value: 3 * 3600.0,
    LifeIntentType.SOCIAL.value: 4 * 3600.0,
    LifeIntentType.MINECRAFT_INTEREST.value: 6 * 3600.0,
    LifeIntentType.EXPLORATION.value: 6 * 3600.0,
    LifeIntentType.REST.value: 2 * 3600.0,
    LifeIntentType.PERSONAL_ROUTINE.value: 4 * 3600.0,
    LifeIntentType.GOAL_PROGRESS.value: 8 * 3600.0,
}

#: 默认存活时间（未知类型；确定性，不用随机）
DEFAULT_INTENT_TTL_SECONDS = 4 * 3600.0

#: 去重桶（§二十 的 "time bucket"）：同一个语义键在一个小时里最多提一次
DEDUPE_BUCKET_SECONDS = 3600.0


def intent_ttl_seconds(intent_type: LifeIntentType | str) -> float:
    value = intent_type.value if isinstance(intent_type, LifeIntentType) else str(intent_type)
    return float(INTENT_TTL_SECONDS.get(value, DEFAULT_INTENT_TTL_SECONDS))


def time_bucket(now: float, *, bucket_seconds: float = DEDUPE_BUCKET_SECONDS) -> int:
    """确定性时间桶（§二十/§六十一）—— 同样的时刻永远落进同一个桶。"""
    return int(float(now) // max(1.0, float(bucket_seconds)))


def intent_fingerprint(
    *,
    character_id: str,
    intent_type: LifeIntentType | str,
    semantic_key: str,
    related_goal: str = "",
    related_activity: str = "",
    bucket: int = 0,
) -> str:
    """§二十 的确定性指纹：``character_id | type | goal | activity | semantic key | bucket``。

    同一个小时里同一条想法 → 同一个指纹 → 被合并 / 忽略（绝不是一个小时十条）。
    """
    kind = intent_type.value if isinstance(intent_type, LifeIntentType) else str(intent_type)
    parts = (
        str(character_id or "").strip().lower(),
        kind,
        str(related_goal or "").strip().lower(),
        str(related_activity or "").strip().lower(),
        str(semantic_key or "").strip().lower(),
        str(int(bucket)),
    )
    return "|".join(parts)


# ---------------------------------------------------------------- 意图本体


@dataclass(frozen=True)
class LifeIntent:
    """一条"她想去做什么"（§三）。

    **只读语义**：所有状态推进都通过 :func:`with_status` 产生**新对象**，
    绝不就地把历史改掉（§二十四 的追加式审计）。
    """

    intent_id: str
    character_id: str
    intent_type: LifeIntentType
    title: str
    description: str = ""
    #: 为什么出现（§六）；``source`` 是枚举词表，``origin`` 是一句人话（可审计）
    source: InitiativeSource = InitiativeSource.SYSTEM
    origin: str = ""
    priority: float = 0.5
    created_at: float = 0.0
    expires_at: float = 0.0
    #: 关联（§三）：全是**引用**，绝不复制对方的生命周期
    related_activity: str = ""
    related_goal: str = ""
    related_memory: str = ""
    related_player: str = ""
    related_task: str = ""
    status: LifeIntentStatus = LifeIntentStatus.PROPOSED
    suppression_reason: str = ""
    resolution_reason: str = ""
    confidence: float = 0.5
    #: §二十 的确定性指纹（去重用）
    fingerprint: str = ""
    #: 未来执行层预留；7A 恒为 VIRTUAL_ONLY
    execution_class: LifeIntentExecutionClass = LifeIntentExecutionClass.VIRTUAL_ONLY
    tags: tuple[str, ...] = field(default_factory=tuple)

    # ------------------------------------------------------------ 只读派生

    @property
    def terminal(self) -> bool:
        return self.status.terminal

    @property
    def open(self) -> bool:
        return self.status.open

    def expired_at(self, now: float) -> bool:
        """是否已经过了 ``expires_at``（§二十二）。``expires_at<=0`` = 不过期（不该出现）。"""
        return bool(self.expires_at) and float(now) >= float(self.expires_at)

    @property
    def is_suppressed(self) -> bool:
        return self.status is LifeIntentStatus.SUPPRESSED

    def with_status(
        self,
        status: LifeIntentStatus,
        *,
        reason: str = "",
        suppression_reason: str = "",
        resolution_reason: str = "",
    ) -> LifeIntent:
        """换成新状态（**新对象**；非法转移会被拒绝）。"""
        if not intent_transition_allowed(self.status, status):
            raise ValueError(f"illegal intent transition: {self.status.value} -> {status.value}")
        payload = self.to_payload()
        # `.value`（不是 str(status)）：(str, Enum) 的 str() 会给出 "LifeIntentStatus.X"，
        # 那会让 from_payload 的兜底把状态悄悄退回 PROPOSED。
        payload["status"] = status.value
        payload["suppression_reason"] = suppression_reason or (
            reason if status is LifeIntentStatus.SUPPRESSED else self.suppression_reason
        )
        payload["resolution_reason"] = resolution_reason or (
            reason if status in (LifeIntentStatus.RESOLVED, LifeIntentStatus.CANCELLED) else ""
        )
        return LifeIntent.from_payload(payload)

    # ------------------------------------------------------------ 序列化

    def to_payload(self) -> dict[str, Any]:
        return {
            "intent_id": self.intent_id,
            "character_id": self.character_id,
            "intent_type": self.intent_type.value,
            "title": self.title,
            "description": self.description,
            "source": self.source.value,
            "origin": self.origin,
            "priority": float(self.priority),
            "created_at": float(self.created_at),
            "expires_at": float(self.expires_at),
            "related_activity": self.related_activity,
            "related_goal": self.related_goal,
            "related_memory": self.related_memory,
            "related_player": self.related_player,
            "related_task": self.related_task,
            "status": self.status.value,
            "suppression_reason": self.suppression_reason,
            "resolution_reason": self.resolution_reason,
            "confidence": float(self.confidence),
            "fingerprint": self.fingerprint,
            "execution_class": self.execution_class.value,
            "tags": list(self.tags),
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> LifeIntent:
        """从 JSON/DB 行还原（坏数据不猜：未知枚举值一律退回安全默认）。"""
        tags = payload.get("tags") or ()
        return cls(
            intent_id=str(payload.get("intent_id") or ""),
            character_id=str(payload.get("character_id") or ""),
            intent_type=_enum_or(
                LifeIntentType, payload.get("intent_type"), LifeIntentType.PERSONAL_ROUTINE
            ),
            title=str(payload.get("title") or ""),
            description=str(payload.get("description") or ""),
            source=_enum_or(InitiativeSource, payload.get("source"), InitiativeSource.SYSTEM),
            origin=str(payload.get("origin") or ""),
            priority=_clamp01(payload.get("priority"), 0.5),
            created_at=float(payload.get("created_at") or 0.0),
            expires_at=float(payload.get("expires_at") or 0.0),
            related_activity=str(payload.get("related_activity") or ""),
            related_goal=str(payload.get("related_goal") or ""),
            related_memory=str(payload.get("related_memory") or ""),
            related_player=str(payload.get("related_player") or ""),
            related_task=str(payload.get("related_task") or ""),
            status=_enum_or(LifeIntentStatus, payload.get("status"), LifeIntentStatus.PROPOSED),
            suppression_reason=str(payload.get("suppression_reason") or ""),
            resolution_reason=str(payload.get("resolution_reason") or ""),
            confidence=_clamp01(payload.get("confidence"), 0.5),
            fingerprint=str(payload.get("fingerprint") or ""),
            execution_class=_enum_or(
                LifeIntentExecutionClass,
                payload.get("execution_class"),
                LifeIntentExecutionClass.VIRTUAL_ONLY,
            ),
            tags=tuple(str(item) for item in tags),
        )


def _enum_or(enum_cls: Any, value: Any, default: Any) -> Any:
    try:
        return enum_cls(str(value))
    except (TypeError, ValueError):
        return default


def _clamp01(value: Any, default: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(0.0, min(1.0, number))
