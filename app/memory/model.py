"""Long-term memory data model (v0.5: layers, status, temporal validity).

``layer`` separates *episodic* ("something happened") from *semantic*
("stable facts distilled from many interactions"); ``status`` keeps history
instead of deleting it — a superseded memory stays readable in the WebUI.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field, field_validator

# Business categories (kept from v0.3/v0.4, extended with episode/semantic hints)
CATEGORIES = (
    "fact",
    "preference",
    "profile",
    "project",
    "interest",
    "habit",
    "event",
    "relationship",
    "instruction",
)

LAYERS = ("semantic", "episodic")

STATUSES = ("active", "archived", "superseded", "expired", "deleted")

SOURCES = ("explicit", "conversation", "inferred", "imported", "system", "vision")

TEMPORAL_SCOPES = ("long_term", "short_term", "event")

SCOPES = ("user", "group", "character", "global")

# Relation names used in ``memory_relations`` (spec v0.5 §8 — not a full graph).
RELATION_TYPES = ("related_to", "supersedes", "caused_by", "conflicts_with", "compressed_from")


def scope_key(scope: str, ref: str) -> str:
    """``user:123`` / ``group:456`` — the isolation boundary for memories."""
    if scope not in SCOPES:
        raise ValueError(f"Invalid memory scope: {scope!r}")
    return f"{scope}:{ref}"


class Memory(BaseModel):
    id: int = 0
    scope_key: str
    user_id: str | None = None
    group_id: str | None = None
    category: str = "fact"
    content: str
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    use_count: int = 0
    created_at: int = 0
    updated_at: int = 0
    last_used_at: int | None = None

    # v0.5 additions
    layer: str = "semantic"
    summary: str = ""
    source: str = "conversation"
    status: str = "active"
    supersedes_id: int | None = None
    conflicts_with_id: int | None = None
    valid_from: int | None = None
    valid_until: int | None = None
    event_at: int | None = None

    @field_validator("category")
    @classmethod
    def _valid_category(cls, value: str) -> str:
        if value not in CATEGORIES:
            raise ValueError(f"Invalid memory category: {value!r} (expected one of {CATEGORIES})")
        return value

    @field_validator("layer")
    @classmethod
    def _valid_layer(cls, value: str) -> str:
        if value not in LAYERS:
            raise ValueError(f"Invalid memory layer: {value!r} (expected one of {LAYERS})")
        return value

    @field_validator("status")
    @classmethod
    def _valid_status(cls, value: str) -> str:
        if value not in STATUSES:
            raise ValueError(f"Invalid memory status: {value!r} (expected one of {STATUSES})")
        return value

    @field_validator("source")
    @classmethod
    def _valid_source(cls, value: str) -> str:
        if value not in SOURCES:
            raise ValueError(f"Invalid memory source: {value!r} (expected one of {SOURCES})")
        return value

    @property
    def display_text(self) -> str:
        """What goes into the prompt: the short summary when we have one."""
        return self.summary.strip() or self.content

    @property
    def is_active(self) -> bool:
        return self.status == "active"

    def to_json(self) -> str:
        return json.dumps(self.model_dump(), ensure_ascii=False)
