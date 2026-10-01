"""Shared rendering helpers + the context route modules may use (Task 18).

Route modules are mixins on :class:`~app.web.server.WebServer`: they see the
same ``self._admin`` / ``self._hub`` / … attributes, declared here so mypy and
readers know what a domain may touch.
"""

from __future__ import annotations

import html
from typing import Any

from app.web import ui
from app.web.security import inject_csrf


class WebContext:
    """Attributes the composed WebServer provides to every route module."""

    _config: Any
    _bot: Any
    _admin: Any
    _behavior: Any
    _memory_admin: Any
    _config_admin: Any
    _correction: Any
    _tool_admin: Any
    _agent_admin: Any
    _social_admin: Any
    _sticker_admin: Any
    _auth: Any
    _throttle: Any
    _hub: Any
    _runner: Any


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def layout(title: str, active: str, body: str, *, subtitle: str = "", actions: str = "") -> str:
    """Render the shared themed shell (see :mod:`app.web.ui`).

    POST forms get the session's CSRF token injected here, in one place, so a
    new form cannot forget it (Task 18).
    """
    return inject_csrf(ui.page(title, active, body, subtitle=subtitle, actions=actions))
