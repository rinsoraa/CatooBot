"""Plugin discovery and loading.

v0.1 scans two static locations at startup:

* ``app/plugins/builtin/``  — shipped with CatooBot
* ``plugins/`` (project root) — user plugins, one module or package each

Every plugin must ship a ``plugin.json`` manifest next to its code
(:mod:`app.plugins.manifest`); a plugin without a valid manifest is refused.
The plugin receives a capability-gated :class:`~app.plugins.api.PluginApi`, and
its failures (load / unload / refused capabilities) are counted per plugin.

Dynamic loading / hot reload is a later milestone; the loader is structured so
``load_plugin`` can be reused by a future management API.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from app.core.exceptions import PluginError
from app.plugins.api import PluginApi
from app.plugins.base import Plugin
from app.plugins.manifest import (
    PluginManifest,
    PluginManifestError,
    load_manifest,
    manifest_path_for,
)

if TYPE_CHECKING:
    from app.core.bot import Bot

_BUILTIN_PKG = "app.plugins.builtin"


class PluginLoader:
    def __init__(self, bot: Bot, user_plugin_dir: str | Path | None = None) -> None:
        self._bot = bot
        self._log = logging.getLogger("CatooBot.Plugin")
        self._audit = logging.getLogger("CatooBot.Plugins.Audit")
        self._loaded: dict[str, Plugin] = {}
        self._failures: dict[str, int] = {}
        self._user_dir = Path(user_plugin_dir) if user_plugin_dir is not None else Path("plugins")

    @property
    def loaded(self) -> dict[str, Plugin]:
        return dict(self._loaded)

    @property
    def failure_counts(self) -> dict[str, int]:
        """Per-plugin failure counter (load / unload / capability refusals)."""
        return dict(self._failures)

    # ------------------------------------------------------------ public API

    async def load_all(self) -> list[Plugin]:
        """Load builtin plugins, then user plugins from ``plugins/``."""
        plugins: list[Plugin] = []
        plugins.extend(await self._load_builtin())
        plugins.extend(await self._load_user_dir())
        if self._failures:
            self._log.warning("[Plugins] failure counts: %s", self._failures)
        return plugins

    async def load_plugin(self, plugin: Plugin) -> None:
        """Initialize one plugin instance: manifest → guarded api → on_load."""
        if plugin.name in self._loaded:
            raise PluginError(f"Plugin name '{plugin.name}' already loaded")
        manifest = self._manifest_for(plugin)
        plugin.manifest = manifest
        plugin.api = PluginApi(
            self._bot,
            manifest,
            log=self._audit,
            metrics=getattr(self._bot, "metrics", None),
        )
        plugin.bot = plugin.api
        try:
            await plugin.on_load(plugin.api)
        except Exception as exc:  # noqa: BLE001 - a broken plugin must not kill startup
            self._count_failure(plugin.name)
            # on_load may have subscribed before failing; do not leak handlers.
            self._release(plugin)
            raise PluginError(f"Plugin '{plugin.name}' failed in on_load: {exc}") from exc

        # Auto-register @command-marked functions defined in the plugin module.
        module = sys.modules.get(type(plugin).__module__)
        if module is not None:
            registered = self._bot.commands.register_marked(vars(module), plugin=plugin.name)
            if registered:
                self._log.debug(
                    "Plugin '%s' registered %d command(s)", plugin.name, len(registered)
                )

        self._loaded[plugin.name] = plugin
        self._log.info(
            "Plugin loaded: %s v%s (%s, capabilities=%s)",
            plugin.name,
            plugin.version,
            manifest.id,
            ",".join(sorted(manifest.capabilities)),
        )

    async def unload_all(self) -> None:
        for name, plugin in list(self._loaded.items()):
            try:
                await plugin.on_unload()
                self._log.info("Plugin unloaded: %s", name)
            except Exception:  # noqa: BLE001
                self._count_failure(name)
                self._log.exception("Plugin '%s' failed in on_unload", name)
            self._release(plugin)
            self._loaded.pop(name, None)

    def _count_failure(self, name: str) -> None:
        self._failures[name] = self._failures.get(name, 0) + 1
        metrics = getattr(self._bot, "metrics", None)
        if metrics is not None:
            metrics.inc("plugin_failures")

    def _manifest_for(self, plugin: Plugin) -> PluginManifest:
        try:
            return load_manifest(manifest_path_for(type(plugin)))
        except PluginManifestError as exc:
            self._count_failure(plugin.name)
            raise PluginError(f"Plugin '{plugin.name}' refused: {exc}") from exc

    def _release(self, plugin: Plugin) -> None:
        """Drop everything a plugin registered: bus handlers + commands.

        Without this, reloading a plugin leaves the previous instance still
        subscribed and every message gets handled once per stale instance.
        """
        removed = self._bot.event_bus.remove_owner(plugin)
        commands = self._bot.commands.unregister_plugin(plugin.name)
        if removed or commands:
            self._log.debug(
                "Plugin '%s' released %d subscription(s) and %d command(s)",
                plugin.name,
                removed,
                commands,
            )

    # ------------------------------------------------------------- scanning

    async def _load_builtin(self) -> list[Plugin]:
        return await self._load_package(_BUILTIN_PKG, origin="builtin")

    async def _load_user_dir(self) -> list[Plugin]:
        root = self._user_dir
        if not root.is_absolute():
            root = Path(__file__).resolve().parent.parent.parent / root
        if not root.is_dir():
            self._log.info("User plugin directory %s not found, skipping", root)
            return []
        sys.path.insert(0, str(root))
        loaded: list[Plugin] = []
        try:
            for info in sorted(pkgutil.iter_modules([str(root)]), key=lambda m: m.name):
                if info.name.startswith("_"):
                    continue
                try:
                    module = importlib.import_module(info.name)
                    loaded.extend(await self._load_module(module, origin=f"user:{info.name}"))
                    if info.ispkg:
                        # Package plugins may define their Plugin class in a
                        # submodule (e.g. plugins/chat/plugin.py).
                        loaded.extend(
                            await self._load_submodules(module, origin=f"user:{info.name}")
                        )
                except Exception:  # noqa: BLE001
                    self._log.exception("Failed to load user plugin module '%s'", info.name)
        finally:
            sys.path.remove(str(root))
        return loaded

    async def _load_submodules(self, package, origin: str) -> list[Plugin]:
        """Scan one level of a package's submodules for Plugin subclasses."""
        loaded: list[Plugin] = []
        for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
            if info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package.__name__}.{info.name}")
            loaded.extend(await self._load_module(module, origin=f"{origin}:{info.name}"))
        return loaded

    async def _load_package(self, package_name: str, origin: str) -> list[Plugin]:
        loaded: list[Plugin] = []
        try:
            package = importlib.import_module(package_name)
        except ImportError:
            return []
        for info in sorted(pkgutil.iter_modules(package.__path__), key=lambda m: m.name):
            if info.name.startswith("_"):
                continue
            module = importlib.import_module(f"{package_name}.{info.name}")
            loaded.extend(await self._load_module(module, origin=f"{origin}:{info.name}"))
        return loaded

    async def _load_module(self, module, origin: str) -> list[Plugin]:
        """Instantiate Plugin subclasses found in a module and load them."""
        loaded: list[Plugin] = []
        for _, cls in inspect.getmembers(module, inspect.isclass):
            if not issubclass(cls, Plugin) or cls is Plugin or cls.__module__ != module.__name__:
                continue
            if getattr(cls, "abstract", False):
                continue
            plugin = cls()
            try:
                await self.load_plugin(plugin)
                loaded.append(plugin)
            except PluginError as exc:
                self._log.error("Skipping plugin from %s: %s", origin, exc)
        return loaded
