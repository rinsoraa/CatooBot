"""Standardized AI error hierarchy.

Every provider converts its transport/HTTP failures into these types so the
Model Router can make *policy* decisions (fallback vs abort vs cooldown)
without knowing any vendor's HTTP semantics. Never embed API keys or
Authorization headers in exception messages.
"""

from __future__ import annotations


def _join(*parts: str) -> str:
    return " ".join(p for p in parts if p)


class AIError(Exception):
    """Base class for all AI subsystem errors."""


class AuthenticationError(AIError):
    """API key missing / invalid / forbidden (HTTP 401 / 403).

    Fail-fast: switching models under the same provider will not help.
    """

    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        super().__init__(
            _join(f"Provider '{provider}' authentication failed", f": {detail}" if detail else "")
        )


class RateLimitError(AIError):
    """HTTP 429 — quota or rate exhausted. Switchable + cooldown."""

    def __init__(self, provider: str, model: str = "", detail: str = "") -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            _join(
                f"Provider '{provider}' rate limited",
                f"(model={model})" if model else "",
                f": {detail}" if detail else "",
            )
        )


class InvalidRequestError(AIError):
    """Malformed request / bad parameters (HTTP 400 / 422). Fail-fast."""

    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' rejected the request: {detail}")


class ModelNotFoundError(AIError):
    """The requested model does not exist / is unavailable (model-specific)."""

    def __init__(self, provider: str, model: str = "", detail: str = "") -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            _join(
                f"Model '{model}' not found on provider '{provider}'",
                f": {detail}" if detail else "",
            )
        )


class AITimeoutError(AIError):
    """The provider did not answer in time. Switchable."""

    def __init__(self, provider: str, model: str = "", detail: str = "") -> None:
        self.provider = provider
        self.model = model
        super().__init__(
            _join(
                f"Provider '{provider}' timed out",
                f"(model={model})" if model else "",
                f": {detail}" if detail else "",
            )
        )


class AIConnectionError(AIError):
    """Network-level failure (DNS, connect, reset). Switchable + one retry."""

    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' connection failed: {detail}")


class ServerError(AIError):
    """Temporary provider-side error (5xx). Switchable + cooldown."""

    def __init__(
        self, provider: str, model: str = "", status: int = 0, detail: str = ""
    ) -> None:
        self.provider = provider
        self.model = model
        self.status = status
        super().__init__(
            _join(
                f"Provider '{provider}' server error",
                f"(status={status})" if status else "",
                f": {detail}" if detail else "",
            )
        )


class UnknownAIError(AIError):
    """Unclassified provider failure. Treated as switchable."""

    def __init__(self, provider: str, detail: str = "") -> None:
        self.provider = provider
        super().__init__(f"Provider '{provider}' unknown error: {detail}")


class AllModelsFailedError(AIError):
    """Every candidate model failed; the request could not be served."""

    def __init__(self, attempts: list[str]) -> None:
        self.attempts = attempts
        summary = "; ".join(attempts) if attempts else "no models available"
        super().__init__(f"All AI models failed: {summary}")
