"""WebUI security helpers (Task 18): login throttling and CSRF tokens.

Two gaps the audit called out, both closed here:

* **login throttling** — five failed attempts per IP+account within five
  minutes get a 429 instead of an unlimited password oracle;
* **CSRF** — every mutating request must carry a token derived from the session
  cookie (hidden field on forms, ``X-CSRF-Token`` header otherwise). Pages get
  the field injected automatically in :func:`inject_csrf`, so a new form cannot
  forget it, and a token stolen from another session is useless.

Both are deliberately boring: no extra storage, no new dependency, deterministic
and unit-testable (the clock is injectable).
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from contextvars import ContextVar
from typing import Any

_log = logging.getLogger("CatooBot.Web.Security")

#: methods that change state and therefore need a CSRF token
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})

#: paths reachable without a session (and so without a token)
CSRF_EXEMPT_PATHS = frozenset({"/login", "/api/login"})

CSRF_FIELD = "csrf_token"
CSRF_HEADER = "X-CSRF-Token"

_FORM_RE = re.compile(r'(<form\b[^>]*\bmethod="post"[^>]*>)', re.IGNORECASE)

_current_token: ContextVar[str] = ContextVar("catoobot_csrf_token", default="")


def csrf_token(session_token: str | None) -> str:
    """Deterministic per-session token (no storage, no expiry to leak)."""
    if not session_token:
        return ""
    digest = hashlib.sha256(f"catoobot-csrf:{session_token}".encode()).hexdigest()
    return digest[:32]


def set_csrf_token(session_token: str | None) -> str:
    token = csrf_token(session_token)
    _current_token.set(token)
    return token


def current_csrf_token() -> str:
    return _current_token.get()


def inject_csrf(html: str) -> str:
    """Add the hidden token to every POST form in a rendered page."""
    token = current_csrf_token()
    if not token or "<form" not in html.lower():
        return html
    field = f'<input type="hidden" name="{CSRF_FIELD}" value="{token}">'
    return _FORM_RE.sub(rf"\1{field}", html)


class LoginThrottle:
    """Per-key failed-login window (key = client + username)."""

    def __init__(
        self,
        *,
        max_attempts: int = 5,
        window_seconds: float = 300.0,
        clock: Any = time.time,
        logger: logging.Logger | None = None,
        metrics: Any = None,
    ) -> None:
        self._max = max(1, int(max_attempts))
        self._window = max(1.0, float(window_seconds))
        self._clock = clock
        self._log = logger or _log
        self._metrics = metrics
        self._attempts: dict[str, list[float]] = {}

    def _recent(self, key: str) -> list[float]:
        now = float(self._clock())
        stamps = [stamp for stamp in self._attempts.get(key, []) if now - stamp < self._window]
        if stamps:
            self._attempts[key] = stamps
        else:
            self._attempts.pop(key, None)
        return stamps

    def allowed(self, key: str) -> bool:
        return len(self._recent(key)) < self._max

    def retry_after(self, key: str) -> float:
        stamps = self._recent(key)
        if len(stamps) < self._max:
            return 0.0
        return max(0.0, self._window - (float(self._clock()) - min(stamps)))

    def record_failure(self, key: str) -> int:
        stamps = self._recent(key)
        stamps.append(float(self._clock()))
        self._attempts[key] = stamps
        if len(stamps) >= self._max:
            if self._metrics is not None:
                self._metrics.inc("login_throttled")
            self._log.warning(
                "[Web.Security] 登录失败 %d 次（%s）—— %.0f 秒内拒绝继续尝试",
                len(stamps),
                key,
                self.retry_after(key),
            )
        return len(stamps)

    def reset(self, key: str) -> None:
        self._attempts.pop(key, None)

    def stats(self) -> dict[str, Any]:
        return {
            "tracked_keys": len(self._attempts),
            "max_attempts": self._max,
            "window_seconds": self._window,
        }
