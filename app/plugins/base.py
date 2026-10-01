"""Plugin base class.

Lifecycle::

    loader reads plugin.json ─► instance created ─► api attached ─► on_load(api)
                                                                    on_unload() ◄─ shutdown

Every plugin ships a ``plugin.json`` (:mod:`app.plugins.manifest`) declaring its
capabilities; ``on_load`` receives a capability-gated view of the bot
(:class:`app.plugins.api.PluginApi`, also available as ``self.api``). A surface
the manifest does not declare is refused with an audit line instead of being
reached silently.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.plugins.api import PluginApi
    from app.plugins.manifest import PluginManifest


class Plugin:
    """Base class every CatooBot plugin inherits from."""

    name: str = "plugin"
    version: str = "0.0.0"
    description: str = ""

    def __init__(self) -> None:
        self.bot: Any = None  # set by the loader before on_load (a PluginApi)
        self.api: PluginApi | None = None
        self.manifest: PluginManifest | None = None

    async def on_load(self, bot: PluginApi) -> None:
        """Called when the plugin is loaded. Override to initialize."""

    async def on_unload(self) -> None:
        """Called on shutdown. Override to release resources."""

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<Plugin {self.name} v{self.version}>"
