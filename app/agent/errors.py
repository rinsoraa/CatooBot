"""Agent error hierarchy (spec §106/§107).

Errors are classified so the runtime never falls back to a blanket "retry
everything": only transient tool failures are retried, everything else either
replans, degrades to a partial result, or fails the task.
"""

from __future__ import annotations


class AgentError(Exception):
    """Base class for every agent failure."""

    error_type = "agent_error"


class PlanningError(AgentError):
    """The planner produced nothing usable."""

    error_type = "planning_failed"


class PlanValidationError(AgentError):
    """The plan was malformed (unknown tool, bad dependency, over budget)."""

    error_type = "plan_invalid"


class StepExecutionError(AgentError):
    """A step could not be executed."""

    error_type = "step_failed"


class EvaluationError(AgentError):
    """Completion could not be judged."""

    error_type = "evaluation_failed"


class ReplanLimitError(AgentError):
    """`max_replans` reached (spec §26)."""

    error_type = "replan_limit"


class BudgetExceededError(AgentError):
    """A hard cap was hit (steps / tool calls / time)."""

    error_type = "budget_exceeded"


class TaskTimeoutError(AgentError):
    """The task ran longer than `max_execution_seconds` (spec §72)."""

    error_type = "task_timeout"


class TaskCancelledError(AgentError):
    """The user asked to stop (spec §36/§98)."""

    error_type = "task_cancelled"
