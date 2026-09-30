"""OneBot 11 protocol data models (adapter-internal).

These models describe the wire format of OneBot 11 API calls and responses.
They stay inside the adapter package; the rest of CatooBot only sees the
typed events from :mod:`app.message.event`.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class ApiRequest(BaseModel):
    """An outgoing OneBot API call (action + params + echo for matching)."""

    action: str
    params: dict[str, Any] = {}
    echo: str


class ApiResult(BaseModel):
    """An incoming OneBot API response, matched by ``echo``."""

    status: str = "ok"
    retcode: int = 0
    data: Any = None
    echo: str | None = None

    @property
    def ok(self) -> bool:
        return self.retcode == 0 or self.status in ("ok", "async")
