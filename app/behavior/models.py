"""Behaviour-layer data models.

These describe *decisions* (may the character speak? how soon? in how many
bubbles?) rather than language — the AI never decides hard rules (spec v0.8 §47).

This file cites both v0.8 §N (behaviour era) and v1.2 §N (reply shape); a bare
§N would mean v2.0 — see docs/README.md for the convention.
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
    """Human-readable 'sense of time' handed to the character (spec v0.8 §62)."""

    timezone: str
    local_time: str  # HH:MM
    date_text: str  # 2026-09-29
    weekday: str  # 星期二
    period: str  # early_morning / morning / afternoon / evening / night / late_night
    is_weekend: bool
    is_sleeping: bool
    in_dnd: bool

    def describe(self) -> str:
        """Natural-language form — never a raw ISO timestamp (spec v0.8 §62)."""
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
    """An unfinished conversation topic (spec v0.8 §28)."""

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
    """Audit row for every behaviour decision (spec v0.8 §50)."""

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
    """Persisted rate-limit counters for proactive chat (spec v0.8 §49)."""

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
class ResponseStep:
    """One outgoing step (v1.2 §72/§73): text / sticker / pause."""

    type: str  # text | sticker | pause
    text: str = ""
    attachment: Any = None
    duration: float = 0.0


@dataclass
class ResponsePlan:
    """What the delivery layer should do with one generated reply (spec v0.8 §11).

    ``attachment`` is an optional sticker/face (v1.1) sent after the text — a
    character expression, never a generic image (v0.8 §33/§52). ``steps`` (v1.2) is
    the full response sequence; when present, delivery walks it and the legacy
    chunks/delays/attachment fields stay as the fallback for older callers.
    """

    chunks: list[str] = field(default_factory=list)
    delay: float = 0.0
    inter_chunk_delays: list[float] = field(default_factory=list)
    reason: str = ""
    attachment: Any = None
    steps: list[ResponseStep] = field(default_factory=list)

    @property
    def is_empty(self) -> bool:
        return not self.chunks and self.attachment is None and not self.steps

    @property
    def has_text(self) -> bool:
        return bool(self.chunks)

    def sequence_steps(self) -> list[ResponseStep]:
        """The plan as an ordered sequence (chunks → pauses → attachment)."""
        if self.steps:
            return self.steps
        steps: list[ResponseStep] = []
        for index, chunk in enumerate(self.chunks):
            if index > 0:
                gap = (
                    self.inter_chunk_delays[index - 1]
                    if index - 1 < len(self.inter_chunk_delays)
                    else 0.0
                )
                if gap > 0:
                    steps.append(ResponseStep(type="pause", duration=gap))
            steps.append(ResponseStep(type="text", text=chunk))
        if self.attachment is not None:
            steps.append(ResponseStep(type="sticker", attachment=self.attachment))
        return steps


@dataclass
class BehaviorDecision:
    """Whether the character participates in an incoming message."""

    respond: bool
    reason: str = ""
    probability: float = 0.0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class ScheduledJob:
    """A job registered on the ONE shared scheduler.

    There is deliberately no second scheduler: the behaviour engine already
    owns the process-wide tick, so sandbox ticks, memory maintenance and
    agent housekeeping all ride the same loop with isolation per job.
    """

    name: str
    handler: Any  # async callable, no arguments
    interval_seconds: float
    enabled: bool = True
    run_immediately: bool = False
    misfire_policy: str = "skip"  # skip | catch_up
    max_runs_per_day: int = 0  # 0 = unlimited
    last_run: float = 0.0
    runs_today: int = 0
    day_key: str = ""
    runs: int = 0
    failures: int = 0
    detail: str = ""

    def due(self, now: float, day_key: str) -> bool:
        if not self.enabled:
            return False
        if day_key != self.day_key:
            self.day_key = day_key
            self.runs_today = 0
        if self.max_runs_per_day and self.runs_today >= self.max_runs_per_day:
            return False
        if self.last_run == 0:
            return self.run_immediately
        return (now - self.last_run) >= self.interval_seconds

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "interval_seconds": self.interval_seconds,
            "enabled": self.enabled,
            "misfire_policy": self.misfire_policy,
            "max_runs_per_day": self.max_runs_per_day,
            "last_run": self.last_run or None,
            "runs_today": self.runs_today,
            "runs": self.runs,
            "failures": self.failures,
            "detail": self.detail,
        }
