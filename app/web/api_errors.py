"""`/api/v1` 的错误原语（WebUI v1.0 · W2/W4）。

放在 `api` 包之外是有意的：Admin Service 也要抛这些错误，而导入
`app.web.api.common` 会连带执行整个 `api` 包的初始化，形成 services ↔ api 的
循环依赖。信封与请求体解析仍然留在 `api/common.py`。
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """An error that maps 1:1 onto the contract's envelope."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        field: str | None = None,
        detail: Any = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.field = field
        self.detail = detail


def bad_request(
    message: str, *, code: str = "request.invalid", field: str | None = None
) -> ApiError:
    return ApiError(400, code, message, field=field)


def unauthorized(message: str = "未登录或会话已失效") -> ApiError:
    return ApiError(401, "auth.unauthorized", message)


def forbidden(message: str = "操作被拒绝", *, code: str = "auth.forbidden") -> ApiError:
    return ApiError(403, code, message)


def not_found(message: str, *, code: str = "resource.not_found") -> ApiError:
    return ApiError(404, code, message)


def conflict(message: str, *, code: str = "resource.conflict", detail: Any = None) -> ApiError:
    return ApiError(409, code, message, detail=detail)


def unprocessable(
    message: str, *, code: str = "validation.failed", field: str | None = None, detail: Any = None
) -> ApiError:
    return ApiError(422, code, message, field=field, detail=detail)


def rate_limited(message: str = "请求过于频繁") -> ApiError:
    return ApiError(429, "rate_limited", message)


def unavailable(message: str, *, code: str = "service.unavailable") -> ApiError:
    return ApiError(503, code, message)


def redact_message(text: str) -> str:
    """Scrub secrets from anything that may reach a client or a log line."""
    from app.utils.logger import redact

    return redact(text)


# ------------------------------------------------------------------ decorator
