"""The tool interface (spec §7/§112).

Tools are pure capability: they never decide *whether* they run, never talk to
the database directly (§31) and never return raw third-party payloads to the
model (§17). The failures they may raise live in :mod:`app.tools.errors`.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.tools.models import ToolContext, ToolMetadata, ToolResult


class Tool(ABC):
    """Interface every tool implements (spec §7/§112)."""

    metadata: ToolMetadata

    def __init__(self) -> None:
        self.log = logging.getLogger(f"CatooBot.Tools.{self.metadata.name}")

    # ------------------------------------------------------------- contract

    @property
    def name(self) -> str:
        return self.metadata.name

    @abstractmethod
    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """Run the tool. Raise a :class:`ToolError` subclass for failures."""

    async def close(self) -> None:  # noqa: B027 - optional hook, not abstract
        """Release resources (HTTP clients, ...). Default: nothing to do."""

    # -------------------------------------------------------------- helpers

    @staticmethod
    def failure(
        name: str,
        error: str,
        *,
        error_type: str = "unknown",
        metadata: dict[str, Any] | None = None,
    ) -> ToolResult:
        return ToolResult(
            tool_name=name,
            success=False,
            error=error,
            error_type=error_type,
            metadata=metadata or {"source_type": "internal", "confidence": 0.0},
        )

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return (
            f"<Tool {self.metadata.name} v{self.metadata.version} risk={self.metadata.risk_level}>"
        )
