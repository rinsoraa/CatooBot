"""World models: the shared vocabulary of the persistent world (v0.8).

All of these are *fictional character-world* concepts. They exist so the
character has a continuous life between messages — never to claim something
verifiable happened in the real world (spec §3/§45).
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field

# ---------------------------------------------------------------- taxonomies

#: Every state change must name a reason (spec §20). No anonymous mutation.
STATE_CHANGE_REASONS: tuple[str, ...] = (
    "time_passage",
    "activity",
    "scheduled_event",
    "user_interaction",
    "task_completion",
    "task_failure",
    "topic_progress",
    "relationship_event",
    "ambient_event",
    "manual",
    "recovery",
)

LIFE_EVENT_TYPES: tuple[str, ...] = (
    "activity",
    "goal",
    "project",
    "interest",
    "routine",
    "relationship",
    "ambient",
    "milestone",
)

#: Event types that are pure background colour: low frequency, never a reason
#: to message anyone by themselves (spec §47 background life != messaging).
AMBIENT_TYPES: tuple[str, ...] = ("ambient", "activity", "interest")

SCHEDULE_STATES: tuple[str, ...] = ("awake", "resting", "sleeping", "busy")
SOCIAL_STATES: tuple[str, ...] = ("alone", "chatting", "with_friends", "quiet")
GOAL_STATUSES: tuple[str, ...] = ("active", "paused", "done", "abandoned")
PROJECT_STATUSES: tuple[str, ...] = ("planning", "active", "paused", "done")
SOURCE_VALUES: tuple[str, ...] = ("scheduled", "agent", "user", "system", "manual", "ambient")

#: Importance defaults per event type, so callers don't invent numbers.
DEFAULT_IMPORTANCE: dict[str, float] = {
    "activity": 0.25,
    "goal": 0.55,
    "project": 0.60,
    "interest": 0.30,
    "routine": 0.35,
    "relationship": 0.50,
    "ambient": 0.20,
    "milestone": 0.75,
}


def make_event_id(event_type: str, key: str, bucket: str) -> str:
    """Deterministic id -> emitting twice in the same bucket is a no-op.

    This is the idempotency mechanism for the whole world (spec §25/§26):
    a retried tick, a restart replay or a duplicate scheduler pass can never
    double-write a life event.
    """
    raw = f"{event_type}|{key}|{bucket}".encode("utf-8", "replace")
    return hashlib.sha1(raw).hexdigest()[:24]


# ------------------------------------------------------------------- models


class Milestone(BaseModel):
    name: str
    done: bool = False
    done_at: int | None = None
    note: str = ""


class PersistentGoal(BaseModel):
    """A goal that survives restarts and advances slowly in the background."""

    goal_id: str = ""
    name: str = ""
    description: str = ""
    status: str = "active"          # GOAL_STATUSES
    priority: int = 3               # 1 = highest
    progress: float = 0.0           # 0..1
    next_action: str = ""           # the very next small step (spec §29)
    milestones: list[Milestone] = Field(default_factory=list)
    project_id: str = ""
    source: str = "system"
    enabled: bool = True
    created_at: int = 0
    updated_at: int = 0
    last_progress_at: int = 0
    completed_at: int | None = None

    @property
    def progress_percent(self) -> int:
        return int(round(max(0.0, min(1.0, self.progress)) * 100))

    def next_open_milestone(self) -> Milestone | None:
        for milestone in self.milestones:
            if not milestone.done:
                return milestone
        return None

    def done_milestone_names(self) -> list[str]:
        return [m.name for m in self.milestones if m.done]

    def summary(self) -> str:
        parts = [f"{self.name}（进度 {self.progress_percent}%）"]
        upcoming = self.next_open_milestone()
        if upcoming is not None:
            parts.append(f"下一步：{upcoming.name}")
        elif self.next_action:
            parts.append(f"下一步：{self.next_action}")
        return "，".join(parts)

    def transient_snapshot(self) -> dict[str, Any]:
        return {
            "goal_id": self.goal_id,
            "name": self.name,
            "status": self.status,
            "progress": round(self.progress, 4),
            "next_action": self.next_action,
            "project_id": self.project_id,
        }


class CharacterProject(BaseModel):
    """A longer-running creative effort the goals hang off of."""

    project_id: str = ""
    name: str = ""
    description: str = ""
    status: str = "active"
    progress: float = 0.0
    created_at: int = 0
    updated_at: int = 0

    def summary(self) -> str:
        return f"{self.name}（{self.status}，{int(round(self.progress * 100))}%）"


class LifeEvent(BaseModel):
    """One thing that happened in the character's world (spec §24)."""

    event_id: str = ""
    type: str = "ambient"
    summary: str = ""
    detail: dict[str, Any] = Field(default_factory=dict)
    source: str = "scheduled"
    importance: float = 0.2
    related_goal: str = ""
    related_topic: str = ""
    session_id: str = ""
    user_id: str = ""
    surfaced_at: int | None = None
    created_at: int = 0

    @property
    def created_text(self) -> str:
        return time.strftime("%m-%d %H:%M", time.localtime(self.created_at or 0))

    def to_row(self) -> tuple[Any, ...]:
        import json

        return (
            self.event_id,
            self.type,
            self.summary,
            json.dumps(self.detail, ensure_ascii=False),
            self.source,
            float(self.importance),
            self.related_goal,
            self.related_topic,
            self.session_id,
            self.user_id,
            self.surfaced_at,
            int(self.created_at),
        )

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> LifeEvent:
        import json

        detail: dict[str, Any] = {}
        raw = row.get("detail")
        if raw:
            try:
                detail = json.loads(raw)
            except (TypeError, ValueError):
                detail = {}
        return cls(
            event_id=row.get("event_id", ""),
            type=row.get("type", "ambient"),
            summary=row.get("summary", ""),
            detail=detail,
            source=row.get("source", "scheduled"),
            importance=float(row.get("importance") or 0.0),
            related_goal=row.get("related_goal") or "",
            related_topic=row.get("related_topic") or "",
            session_id=row.get("session_id") or "",
            user_id=row.get("user_id") or "",
            surfaced_at=row.get("surfaced_at"),
            created_at=int(row.get("created_at") or 0),
        )


class WorldSnapshot(BaseModel):
    """A point-in-time capture used for fast, bounded recovery (spec §31)."""

    snapshot_at: int = 0
    world_time: str = ""
    state: dict[str, Any] = Field(default_factory=dict)
    goals: list[dict[str, Any]] = Field(default_factory=list)
    projects: list[dict[str, Any]] = Field(default_factory=list)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)
    counters: dict[str, Any] = Field(default_factory=dict)
    version: int = 1

    def to_json(self) -> str:
        import json

        return json.dumps(self.model_dump(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> WorldSnapshot:
        import json

        return cls.model_validate(json.loads(raw))


# ---------------------------------------------------------------- scheduler


@dataclass
class ScheduledJob:
    """A job registered on the ONE shared scheduler (spec §34/§35).

    There is deliberately no second scheduler: the behaviour engine already
    owns the process-wide tick, so world jobs, memory maintenance and agent
    housekeeping all ride the same loop with isolation per job.
    """

    name: str
    handler: Any                    # async callable, no arguments
    interval_seconds: float
    enabled: bool = True
    run_immediately: bool = False
    misfire_policy: str = "skip"    # skip | catch_up
    max_runs_per_day: int = 0       # 0 = unlimited
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


@dataclass
class WorldActivity:
    """One candidate thing the character might be doing in a period."""

    key: str
    label: str
    period: str = ""
    location: str = ""
    energy_delta: float = 0.0
    mood_bias: int = 0
    social: str = "alone"
    weight: float = 1.0
    tags: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "label": self.label,
            "period": self.period,
            "location": self.location,
            "social": self.social,
            "weight": self.weight,
            "tags": list(self.tags),
        }
