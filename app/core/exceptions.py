"""CatooBot exception hierarchy.

Every subsystem raises subclasses of :class:`CatooBotError` so callers can
distinguish framework errors from plugin bugs. A single event/command/plugin
failure must never propagate to the runtime loop.
"""

from __future__ import annotations

from typing import Any


class CatooBotError(Exception):
    """Base class for all CatooBot errors."""


class ConfigError(CatooBotError):
    """Configuration is missing or invalid."""


class AdapterError(CatooBotError):
    """Adapter-level failure (transport, authentication...)."""


class AdapterNotConnected(AdapterError):
    """An API call was made while no protocol client is connected."""


class AdapterDisconnected(AdapterError):
    """The connection was closed while a request was pending."""


class AuthenticationError(AdapterError):
    """A client failed access-token verification."""


class ParseError(CatooBotError):
    """A raw payload could not be converted into a typed event."""


class ApiTimeoutError(CatooBotError):
    """An OneBot API call did not answer in time."""


class OneBotApiError(AdapterError):
    """The OneBot implementation returned a failed API response."""

    def __init__(self, action: str, retcode: int, status: str, message: str = "") -> None:
        self.action = action
        self.retcode = retcode
        self.status = status
        self.message = message
        super().__init__(
            f"OneBot API '{action}' failed: retcode={retcode} status={status} {message}"
        )


class CommandError(CatooBotError):
    """Command registration or execution failure."""


class CommandNotFound(CommandError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Command not found: {name}")


class PermissionDenied(CommandError):
    """The sender lacks the permission required by a command."""

    def __init__(self, user_id: int, permission: str) -> None:
        self.user_id = user_id
        self.permission = permission
        super().__init__(f"User {user_id} lacks permission '{permission}'")


class PluginError(CatooBotError):
    """Plugin loading / lifecycle failure."""


class DatabaseError(CatooBotError):
    """Database access failure."""

    def __init__(self, operation: str, detail: Any = "") -> None:
        self.operation = operation
        super().__init__(f"Database error during '{operation}': {detail}")
