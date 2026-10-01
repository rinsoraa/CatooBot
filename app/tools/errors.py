"""Standardized tool errors (spec §18).

The tool interface itself lives in :mod:`app.tools.base`.
"""

from __future__ import annotations


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
