"""Plugin base class.

Lifecycle::

    loader instantiates ─► bot attached ─► on_load(bot) ─► running
                                                  on_unload() ◄─ shutdown

Inside ``on_load`` a plugin may register commands (``@command`` decorator at
module level is picked up automatically), subscribe to events
(``bot.event_bus.on(...)``) and call ``bot.call_api(...)``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot


class Plugin:
    """Base class every CatooBot plugin inherits from."""

    name: str = "plugin"
    version: str = "0.0.0"
    description: str = ""

    def __init__(self) -> None:
        self.bot: Any = None  # set by the loader before on_load

    async def on_load(self, bot: Bot) -> None:
        """Called when the plugin is loaded. Override to initialize."""

    async def on_unload(self) -> None:
        """Called on shutdown. Override to release resources."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Plugin {self.name} v{self.version}>"
