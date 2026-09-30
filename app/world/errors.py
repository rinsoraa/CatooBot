"""World runtime error hierarchy (keeps world failures away from chat)."""

from __future__ import annotations


class WorldError(Exception):
    """Base class for world-runtime failures."""

    error_type = "world_error"


class WorldDisabledError(WorldError):
    error_type = "world_disabled"


class GoalError(WorldError):
    error_type = "goal_error"


class GoalLimitError(GoalError):
    """Too many active goals, or a daily progress budget is exhausted."""

    error_type = "goal_limit"


class EventLimitError(WorldError):
    """Event burst protection: the hourly/daily cap is reached (spec §104)."""

    error_type = "event_limit"


class SnapshotError(WorldError):
    error_type = "snapshot_error"
