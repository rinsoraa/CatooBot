"""Continuity data models (v1.2 §25/§30/§34/§41/§45).

Every fact carries value + confidence + updated_at + reason_code so the
WebUI inspector can show where it came from and when it decays (§134).
Nothing here stores model reasoning.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class OpenLoopType(str, Enum):  # noqa: UP042
    activity = "activity"
    conversation = "conversation"
    goal = "goal"
    interest = "interest"
    question = "question"
    plan = "plan"
    shared_event = "shared_event"


class OpenLoopStatus(str, Enum):  # noqa: UP042
    open = "open"
    paused = "paused"
    completed = "completed"
    abandoned = "abandoned"
    expired = "expired"


class OpenLoop(BaseModel):
    """Something unfinished in her life (§29): "木屋屋顶还没建完"."""

    id: str = ""
    type: OpenLoopType = OpenLoopType.plan
    summary: str
    detail: str = ""
    status: OpenLoopStatus = OpenLoopStatus.open
    scope_key: str = ""                   # "" = hers alone; user:<id> = shared with user
    progress: float = Field(default=0.0, ge=0.0, le=1.0)
    source: str = ""                      # reason_code of creation (§46)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    created_at: float = 0.0
    updated_at: float = 0.0
    expires_at: float | None = None


class SharedExperienceType(str, Enum):  # noqa: UP042
    shared_event = "shared_event"
    inside_joke = "inside_joke"
    nickname = "nickname"
    recurring_joke = "recurring_joke"
    shared_project = "shared_project"
    memorable_moment = "memorable_moment"
    mutual_reference = "mutual_reference"
    unfinished_story = "unfinished_story"
    shared_topic = "shared_topic"


class SharedExperience(BaseModel):
    """What she and a user have been through *together* (§33) — not what the
    user said. Retrieved by relevance, never dumped wholesale (§37)."""

    id: str = ""
    user_id: str
    type: SharedExperienceType = SharedExperienceType.shared_event
    summary: str
    detail: str = ""
    keywords: list[str] = Field(default_factory=list)
    times_referenced: int = 0
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    source: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


class InteractionPattern(BaseModel):
    """One observed user habit: value + confidence + samples (§41) — never a
    permanent label; it decays and may reverse (§42/§43)."""

    value: float = 0.0
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_count: int = 0
    updated_at: float = 0.0


class InteractionProfile(BaseModel):
    """How *this user* usually chats (§38-§40) — user-specific context, never
    a mutation of the global persona (§115)."""

    user_id: str
    patterns: dict[str, InteractionPattern] = Field(default_factory=dict)
    favorite_topics: list[str] = Field(default_factory=list)
    updated_at: float = 0.0

    def observe(self, key: str, value: float, *, now: float, alpha: float = 0.3) -> None:
        """Exponential moving average with growing confidence."""
        pattern = self.patterns.get(key) or InteractionPattern()
        total = pattern.sample_count + 1
        blended = pattern.value * (1 - alpha) + value * alpha if pattern.sample_count else value
        self.patterns[key] = InteractionPattern(
            value=round(blended, 4),
            confidence=round(min(0.95, total / (total + 8.0)), 4),
            sample_count=total,
            updated_at=now,
        )
        self.updated_at = now

    def decay(self, now: float, half_life_days: float = 7.0) -> None:
        """Patterns fade without fresh evidence (§42): confidence halves."""
        import math

        for key, pattern in list(self.patterns.items()):
            age_days = max(0.0, (now - pattern.updated_at) / 86400.0)
            factor = math.pow(0.5, age_days / max(0.1, half_life_days))
            if factor < 0.05:
                del self.patterns[key]
                continue
            if factor < 1.0:
                self.patterns[key] = pattern.model_copy(
                    update={"confidence": round(pattern.confidence * factor, 4)}
                )


class AffectDimension(str, Enum):  # noqa: UP042
    amusement = "amusement"
    interest = "interest"
    curiosity = "curiosity"
    social_energy = "social_energy"
    annoyance = "annoyance"
    warmth = "warmth"
    engagement = "engagement"


class AffectiveContext(BaseModel):
    """Light, event-driven conversational affect (§44-§49). Separate from the
    global mood: a joke lifts turn-level amusement without rewriting her day."""

    dimensions: dict[str, float] = Field(default_factory=dict)
    reason_code: str = ""
    updated_at: float = 0.0

    def bump(self, dimension: str, delta: float, *, reason: str, now: float) -> str:
        """Event-driven change with a reason (§46/§47); returns the reason."""
        current = self.dimensions.get(dimension, 0.0)
        self.dimensions[dimension] = round(max(0.0, min(1.0, current + delta)), 4)
        self.reason_code = reason
        self.updated_at = now
        return reason

    def decay(self, now: float, half_life_minutes: float = 45.0) -> None:
        """Turn-level affect drains back toward neutral (§48/§51)."""
        import math

        for key, value in list(self.dimensions.items()):
            age = max(0.0, (now - self.updated_at) / 60.0)
            factor = math.pow(0.5, age / max(1.0, half_life_minutes))
            decayed = round(value * factor, 4)
            if decayed < 0.02:
                del self.dimensions[key]
            else:
                self.dimensions[key] = decayed

    def level(self, dimension: str) -> float:
        return self.dimensions.get(dimension, 0.0)


class MicroEvent(BaseModel):
    """A tiny world event (§98): "发现了一个新 Mod" — internal continuity only,
    never spam (§99); it only surfaces when conversationally relevant."""

    id: str = ""
    summary: str
    kind: str = "ambient"                 # ambient / discovery / progress / mishap
    related_activity: str = ""            # activity key when it belongs to one
    reason_code: str = ""
    created_at: float = 0.0


class CharacterContinuityState(BaseModel):
    """The assembled short-timescale self (§25). Persisted with per-field TTLs
    (§106); loaded through the store which applies decay on read (§105)."""

    user_id: str = ""                     # "" = character-global parts
    current_interest: str = ""            # "Minecraft 木屋屋顶"
    current_focus: str = ""               # "正在看动画高潮部分"
    unfinished_thought: str = ""          # "刚才想到一个新房间布局" — not reasoning
    recent_emotion: str = ""              # short-lived emotional tint
    interest_updated_at: float = 0.0
    focus_updated_at: float = 0.0
    thought_updated_at: float = 0.0
    emotion_updated_at: float = 0.0
    affect: AffectiveContext = Field(default_factory=AffectiveContext)
    recent_events: list[str] = Field(default_factory=list)   # capped, compressed (§172)
    open_loops: list[OpenLoop] = Field(default_factory=list)
    last_response_context: str = ""
    next_natural_direction: str = ""
    updated_at: float = 0.0
