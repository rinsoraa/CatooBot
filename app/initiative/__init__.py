"""Phase 7A：Initiative Gate / Life Intent Foundation。

对外只有这几样东西（**没有任何执行入口**，§四十一/§六十八）：

* :class:`LifeIntent` 与它的枚举（状态 / 类型 / 来源 / 执行类）；
* :class:`LifeIntentService` —— 编排：候选 → 门禁 → 记录 → 事件；
* :class:`InitiativeGate` 与抑制原因词表；
* :class:`InitiativeContext` / :func:`propose_intents` —— 确定性候选生成；
* 存储与事件（SQLite ``life_intents`` + 复用 ``behavior_events``）。
"""

from __future__ import annotations

from app.initiative.adapters import InitiativeContextAdapter
from app.initiative.candidates import (
    CommitmentSignal,
    GoalSignal,
    InitiativeContext,
    propose_intents,
)
from app.initiative.events import (
    INITIATIVE_CANCELLED,
    INITIATIVE_CREATED,
    INITIATIVE_EVENT_NAMES,
    INITIATIVE_EXPIRED,
    INITIATIVE_RESOLVED,
    INITIATIVE_SUPPRESSED,
    InitiativeEventPublisher,
)
from app.initiative.gate import (
    HARD_GUARD_ORDER,
    CandidateVerdict,
    GateDecision,
    InitiativeGate,
    SuppressionReason,
    is_guard_reason,
    suppression_reasons,
)
from app.initiative.model import (
    ALLOWED_INTENT_TRANSITIONS,
    DEDUPE_BUCKET_SECONDS,
    INTENT_TTL_SECONDS,
    InitiativeSource,
    LifeIntent,
    LifeIntentExecutionClass,
    LifeIntentStatus,
    LifeIntentType,
    execution_class_for,
    intent_fingerprint,
    intent_transition_allowed,
    intent_ttl_seconds,
    time_bucket,
)
from app.initiative.service import LifeIntentService
from app.initiative.store import (
    InMemoryLifeIntentStore,
    LifeIntentStore,
    SqliteLifeIntentStore,
)

__all__ = [
    "ALLOWED_INTENT_TRANSITIONS",
    "CandidateVerdict",
    "CommitmentSignal",
    "GateDecision",
    "GoalSignal",
    "HARD_GUARD_ORDER",
    "INITIATIVE_CANCELLED",
    "INITIATIVE_CREATED",
    "INITIATIVE_EVENT_NAMES",
    "INITIATIVE_EXPIRED",
    "INITIATIVE_RESOLVED",
    "INITIATIVE_SUPPRESSED",
    "INTENT_TTL_SECONDS",
    "InMemoryLifeIntentStore",
    "InitiativeContext",
    "InitiativeContextAdapter",
    "InitiativeEventPublisher",
    "InitiativeGate",
    "InitiativeSource",
    "LifeIntent",
    "LifeIntentExecutionClass",
    "LifeIntentService",
    "LifeIntentStatus",
    "LifeIntentStore",
    "LifeIntentType",
    "SqliteLifeIntentStore",
    "DEDUPE_BUCKET_SECONDS",
    "SuppressionReason",
    "execution_class_for",
    "execution_class_for",
    "intent_fingerprint",
    "intent_ttl_seconds",
    "intent_ttl_seconds",
    "intent_transition_allowed",
    "is_guard_reason",
    "propose_intents",
    "suppression_reasons",
    "time_bucket",
]
