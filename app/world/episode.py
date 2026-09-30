"""Activity Episode: the primary fact-source for the character's current activity.

v1.0 changes "activity" from a *label* into a *lifecycle*: an episode knows what
it is, when it started, when it should end, whether it was extended/interrupted,
and why it ended. ``CharacterState.activity`` becomes a derived snapshot of the
current episode (spec §7/§8), never a second, independent value.
"""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, Field

EpisodeStatus = Literal[
    "scheduled", "active", "extended", "completed", "interrupted", "cancelled", "expired"
]

EPISODE_STATUSES: tuple[str, ...] = (
    "scheduled", "active", "extended", "completed", "interrupted", "cancelled", "expired",
)

TRANSITION_REASONS: tuple[str, ...] = (
    "natural_completion",
    "schedule_boundary",
    "extended",
    "user_interruption",
    "goal_trigger",
    "state_change",
    "energy_low",
    "sleep_transition",
    "manual",
    "recovery",
)

SOURCE_VALUES: tuple[str, ...] = ("routine", "anchor", "goal", "manual", "recovery", "interaction")


class ActivityProfile(BaseModel):
    """Duration & behaviour characteristics of one activity type (spec §12/§34)."""

    key: str
    label: str = ""
    location: str = ""
    social: str = "alone"
    tags: list[str] = Field(default_factory=list)
    min_minutes: int = 15
    typical_minutes: int = 30
    max_minutes: int = 60
    momentum: float = Field(default=0.5, ge=0.0, le=1.0)
    interruptible: bool = True
    ambient_eligible: bool = True
    ambient_max_per_episode: int = 1
    energy_delta_per_hour: float = 0.0
    flexibility: float = Field(default=0.5, ge=0.0, le=1.0)

    def duration_range_minutes(self) -> tuple[int, int]:
        return max(1, self.min_minutes), max(self.min_minutes, self.max_minutes)


class ActivityEpisode(BaseModel):
    """One continuous stretch of the character doing one thing (spec §5/§6)."""

    id: str = ""
    activity_key: str = ""
    activity_label: str = ""
    activity_detail: str = ""
    location: str = ""
    social_state: str = "alone"
    tags: list[str] = Field(default_factory=list)

    started_at: float = 0.0
    planned_end_at: float = 0.0
    ended_at: float | None = None

    status: EpisodeStatus = "active"
    source: str = "routine"
    transition_reason: str = ""
    parent_episode_id: str = ""

    extension_count: int = 0
    ambient_count: int = 0

    created_at: float = 0.0
    updated_at: float = 0.0

    # ------------------------------------------------------------ helpers

    def elapsed_minutes(self, now: float) -> int:
        return max(0, int((now - self.started_at) // 60))

    def remaining_minutes(self, now: float) -> int:
        return max(0, int((self.planned_end_at - now) // 60))

    def is_active(self, now: float) -> bool:
        return self.status in ("active", "extended") and self.ended_at is None

    def is_overdue(self, now: float) -> bool:
        return now >= self.planned_end_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "activity_key": self.activity_key,
            "activity_label": self.activity_label,
            "activity_detail": self.activity_detail,
            "location": self.location,
            "social_state": self.social_state,
            "tags": list(self.tags),
            "started_at": self.started_at,
            "planned_end_at": self.planned_end_at,
            "ended_at": self.ended_at,
            "status": self.status,
            "source": self.source,
            "transition_reason": self.transition_reason,
            "parent_episode_id": self.parent_episode_id,
            "extension_count": self.extension_count,
            "ambient_count": self.ambient_count,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


def new_episode_id() -> str:
    return f"ep_{uuid.uuid4().hex[:12]}"
