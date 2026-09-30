"""Tool execution policy: every hard rule lives here, never in the prompt.

    Tool call → Policy → allowed?

Covers (spec §21/§22/§32/§33/§34/§35):
* global enable + risk-level allowlist,
* per-user / per-group allow & deny lists (WebUI managed),
* rate limits (per user, per group, global) with asyncio-safe windows,
* per-turn call budget,
* loop detection (same tool + same arguments repeated).
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from typing import Any

from app.config.settings import ToolsConfig
from app.tools.errors import (
    ToolBudgetExceededError,
    ToolLoopError,
    ToolPermissionError,
    ToolRateLimitError,
)
from app.tools.models import ToolCall, ToolContext, ToolMetadata

WINDOW_SECONDS = 60.0
LOOP_MIN_REPEATS = 2  # third identical call in one turn is blocked


@dataclass
class TurnBudget:
    """Per-message counters (spec §34/§52)."""

    max_calls: int = 3
    max_execution_time: float = 30.0
    calls: int = 0
    started_at: float = field(default_factory=time.monotonic)
    calls_seen: list[tuple[str, str]] = field(default_factory=list)

    def remaining(self) -> int:
        return max(0, self.max_calls - self.calls)

    def register(self, call: ToolCall) -> None:
        self.calls += 1
        self.calls_seen.append((call.name, self.fingerprint(call)))

    @staticmethod
    def fingerprint(call: ToolCall) -> str:
        blob = json.dumps(call.arguments, ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(f"{call.name}:{blob}".encode()).hexdigest()[:16]

    def repeated(self, call: ToolCall) -> int:
        """How many times this exact call already happened in this turn."""
        target = self.fingerprint(call)
        return sum(1 for _, fingerprint in self.calls_seen if fingerprint == target)

    def timed_out(self) -> bool:
        return (time.monotonic() - self.started_at) > self.max_execution_time


class ToolPolicy:
    def __init__(
        self,
        config: ToolsConfig,
        logger: logging.Logger | None = None,
        clock: Any = time.monotonic,
    ) -> None:
        self.config = config
        self._log = logger or logging.getLogger("CatooBot.Tools")
        self._clock = clock
        # rate-limit windows: scope key -> deque of timestamps
        self._windows: dict[str, deque[float]] = defaultdict(deque)
        # permission overrides loaded from DB: (scope, ref, tool) -> allowed
        self._permissions: dict[tuple[str, str, str], bool] = {}

    # ----------------------------------------------------------- permissions

    def load_permissions(self, rows: list[dict[str, Any]]) -> None:
        self._permissions = {
            (str(row["scope"]), str(row["ref"]), str(row["tool_name"])): bool(row["allowed"])
            for row in rows
        }
        if self._permissions:
            self._log.info("[Tool] loaded %d permission rule(s)", len(self._permissions))

    def check_permission(
        self,
        metadata: ToolMetadata,
        *,
        enabled: bool,
        context: ToolContext,
    ) -> None:
        """Raise when the call is forbidden; return None when allowed."""
        if not enabled:
            raise ToolPermissionError(f"Tool '{metadata.name}' is disabled")

        allowed_risks = set(self.config.permissions.allowed_risk_levels)
        if metadata.risk_level not in allowed_risks:
            raise ToolPermissionError(
                f"Tool '{metadata.name}' has risk level '{metadata.risk_level}'"
                f" which is not allowed (allowed: {sorted(allowed_risks)})"
            )

        # Deny wins over allow; user rules win over group rules.
        for scope, ref in (("user", context.user_id), ("group", context.group_id or "")):
            if not ref:
                continue
            rule = self._permissions.get((scope, ref, metadata.name))
            if rule is False:
                raise ToolPermissionError(
                    f"Tool '{metadata.name}' is not allowed for {scope} {ref}"
                )

    # ------------------------------------------------------------ rate limit

    def check_rate_limit(self, metadata: ToolMetadata, context: ToolContext) -> None:
        limits = self._limits_for(metadata)
        now = self._clock()
        checks: list[tuple[str, str, int]] = [
            ("global", f"tool:{metadata.name}", limits.get("global_per_minute", 0)),
            (
                "user",
                f"tool:{metadata.name}:user:{context.user_id}",
                limits.get("per_user_per_minute", 0),
            ),
        ]
        if context.group_id:
            checks.append(
                (
                    "group",
                    f"tool:{metadata.name}:group:{context.group_id}",
                    limits.get("per_group_per_minute", 0),
                )
            )

        for scope, key, limit in checks:
            if limit <= 0:
                continue
            window = self._windows[key]
            while window and now - window[0] > WINDOW_SECONDS:
                window.popleft()
            if len(window) >= limit:
                raise ToolRateLimitError(
                    f"Tool '{metadata.name}' hit the {scope} rate limit ({limit}/min)"
                )

    def record_call(self, metadata: ToolMetadata, context: ToolContext) -> None:
        now = self._clock()
        keys = [f"tool:{metadata.name}", f"tool:{metadata.name}:user:{context.user_id}"]
        if context.group_id:
            keys.append(f"tool:{metadata.name}:group:{context.group_id}")
        for key in keys:
            self._windows[key].append(now)

    def _limits_for(self, metadata: ToolMetadata) -> dict[str, int]:
        override = self.config.configs.get(metadata.name)
        limits = override.rate_limit if override and override.rate_limit else self.config.rate_limit
        return {
            "per_user_per_minute": limits.per_user_per_minute,
            "per_group_per_minute": limits.per_group_per_minute,
            "global_per_minute": limits.global_per_minute,
        }

    # ---------------------------------------------------------------- budget

    def check_budget(self, budget: TurnBudget) -> None:
        if budget.remaining() <= 0:
            raise ToolBudgetExceededError(
                f"Tool call budget exhausted ({budget.max_calls} per turn)"
            )
        if budget.timed_out():
            raise ToolBudgetExceededError("Tool execution time budget exhausted")

    # ---------------------------------------------------------------- loops

    def check_loop(self, budget: TurnBudget, call: ToolCall) -> None:
        repeats = budget.repeated(call)
        if repeats > LOOP_MIN_REPEATS:
            raise ToolLoopError(
                f"Repeated identical call to '{call.name}' ({repeats} times) — stopped"
            )

    # ------------------------------------------------------- convenience

    def authorize(
        self,
        metadata: ToolMetadata,
        call: ToolCall,
        context: ToolContext,
        budget: TurnBudget,
        *,
        enabled: bool,
    ) -> None:
        """Run every check in a fixed order; raises on the first violation."""
        self.check_permission(metadata, enabled=enabled, context=context)
        self.check_budget(budget)
        self.check_loop(budget, call)
        self.check_rate_limit(metadata, context)

    def snapshot(self) -> dict[str, Any]:
        return {
            "allowed_risk_levels": self.config.permissions.allowed_risk_levels,
            "rate_limit": self.config.rate_limit.model_dump(),
            "max_calls_per_turn": self.config.max_calls_per_turn,
            "max_execution_time": self.config.max_execution_time,
            "permission_rules": len(self._permissions),
            "active_windows": len(self._windows),
        }
