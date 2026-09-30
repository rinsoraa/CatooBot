"""Behaviour-layer data models.

These describe *decisions* (may the character speak? how soon? in how many
bubbles?) rather than language — the AI never decides hard rules (spec §47).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel, Field

TopicStatus = Literal["active", "waiting", "resolved", "forgotten"]

# Hard-block reasons returned by the Initiative Gate / participation gate.
GATE_REASONS = (
    "disabled",
    "sleeping",
    "dnd",
    "cooldown",
    "daily_limit",
    "hourly_limit",
    "user_disabled",
    "group_disabled",
    "relationship_too_new",
    "no_reason",
    "awaiting_reply",
    "duplicate",
    "low_probability",
    "asleep_or_busy",
)


@dataclass
class TimeContext:
    """Human-readable 'sense of time' handed to the character (spec §62)."""

    timezone: str
    local_time: str  # HH:MM
    date_text: str  # 2026-09-29
    weekday: str  # 星期二
    period: str  # early_morning / morning / afternoon / evening / night / late_night
    is_weekend: bool
    is_sleeping: bool
    in_dnd: bool

    def describe(self) -> str:
        """Natural-language form — never a raw ISO timestamp (spec §62)."""
        weekend = "，今天是周末" if self.is_weekend else ""
        return f"现在是{self._period_text()}（{self.local_time}），{self.weekday}{weekend}。"

    def _period_text(self) -> str:
        return {
            "early_morning": "清晨",
            "morning": "上午",
            "noon": "中午",
            "afternoon": "下午",
            "evening": "晚上",
            "night": "夜里",
            "late_night": "深夜",
        }.get(self.period, "现在")


class TopicThread(BaseModel):
    """An unfinished conversation topic (spec §28)."""

    id: int = 0
    scope_key: str
    title: str
    status: TopicStatus = "active"
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    participants: list[str] = Field(default_factory=list)
    last_discussed_at: int | None = None
    created_at: int = 0
    updated_at: int = 0


class BehaviorEvent(BaseModel):
    """Audit row for every behaviour decision (spec §50)."""

    id: int = 0
    type: str
    scope_key: str | None = None
    user_id: str | None = None
    group_id: str | None = None
    reason: str = ""
    detail: str = ""
    status: str = "done"
    created_at: int = 0
    executed_at: int | None = None


class InitiativeState(BaseModel):
    """Persisted rate-limit counters for proactive chat (spec §49)."""

    scope_key: str
    last_sent_at: int | None = None
    last_candidate_at: int | None = None
    unanswered_count: int = 0
    daily_count: int = 0
    daily_date: str = ""
    hourly_count: int = 0
    hourly_bucket: str = ""
    last_message: str = ""
    updated_at: int | None = None


@dataclass
class ResponsePlan:
    """What the delivery layer should do with one generated reply (spec §11).

    ``attachment`` is an optional sticker/face (v1.1) sent after the text — a
    character expression, never a generic image (§33/§52).
    """

    chunks: list[str] = field(default_factory=list)
    delay: float = 0.0
    inter_chunk_delays: list[float] = field(default_factory=list)
    reason: str = ""
    attachment: Any = None

    @property
    def is_empty(self) -> bool:
        return not self.chunks and self.attachment is None

    @property
    def has_text(self) -> bool:
        return bool(self.chunks)


@dataclass
class BehaviorDecision:
    """Whether the character participates in an incoming message."""

    respond: bool
    reason: str = ""
    probability: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)
