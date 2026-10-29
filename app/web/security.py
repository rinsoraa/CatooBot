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

#: single- OR double-quoted ``method='post'`` / ``method="post"`` (and unquoted
#: ``method=post``), with optional whitespace around ``=``. The single-quote
#: variant once slipped through the old double-quote-only pattern and every
#: submit 403'd silently.
_FORM_RE = re.compile(r'(<form\b[^>]*?\bmethod\s*=\s*["\']?post["\']?[^>]*>)', re.IGNORECASE)

#: for the post-injection self-check: any <form> opening tag, and a POST method
_FORM_OPEN_RE = re.compile(r"<form\b[^>]*>", re.IGNORECASE)
_METHOD_POST_RE = re.compile(r'\bmethod\s*=\s*["\']?post["\']?', re.IGNORECASE)

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
    injected = _FORM_RE.sub(rf"\1{field}", html)
    _warn_unprotected_forms(injected)
    return injected


def _warn_unprotected_forms(html: str) -> None:
    """Turn a silent auth failure into a visible bug.

    If a POST form survives injection without a token field, every submit is a
    403 — log it loudly instead of letting the operator chase a phantom "refresh
    the page" hint.
    """
    for match in _FORM_OPEN_RE.finditer(html):
        if not _METHOD_POST_RE.search(match.group(0)):
            continue
        end = html.find("</form>", match.end())
        tail = html[match.end() : end] if end != -1 else html[match.end() : match.end() + 512]
        if CSRF_FIELD not in tail:
            _log.warning(
                "[Web.Security] POST 表单缺少 %s 隐藏字段（提交会被 403）：%r",
                CSRF_FIELD,
                match.group(0)[:140],
            )


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
