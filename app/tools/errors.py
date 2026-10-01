"""Standardized tool errors (spec §18) and the tool interface (spec §7)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any

from app.tools.models import ToolContext, ToolMetadata, ToolResult


class ToolError(Exception):
    """Base class for every tool failure; the runtime normalizes to this."""

    error_type = "unknown"
    retryable = False
    user_hint = ""


class InvalidArgumentsError(ToolError):
    error_type = "invalid_arguments"
    user_hint = "参数不合法"


class ToolTimeoutError(ToolError):
    error_type = "timeout"
    retryable = True
    user_hint = "执行超时"


class ToolRateLimitError(ToolError):
    error_type = "rate_limit"
    user_hint = "调用过于频繁"


class ToolPermissionError(ToolError):
    error_type = "permission_denied"
    user_hint = "没有权限使用"


class ToolBudgetExceededError(ToolError):
    error_type = "budget_exceeded"
    user_hint = "本次对话的工具调用次数已达上限"


class ToolLoopError(ToolError):
    error_type = "loop_detected"
    user_hint = "重复调用被阻止"


class ExternalServiceError(ToolError):
    error_type = "external_service"
    retryable = True
    user_hint = "外部服务异常"


class ToolAuthenticationError(ToolError):
    error_type = "authentication"
    user_hint = "凭据无效"


class NotFoundError(ToolError):
    error_type = "not_found"
    user_hint = "没有找到结果"


class UnknownToolError(ToolError):
    error_type = "unknown_tool"
    user_hint = "工具不存在"


class ToolNotAvailableError(ToolError):
    error_type = "unavailable"
    user_hint = "工具未启用或未配置"


class Tool(ABC):
    """Interface every tool implements (spec §7/§112).

    Tools are pure capability: they never decide *whether* they run, never
    talk to the database directly (§31) and never return raw third-party
    payloads to the model (§17).
    """

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
