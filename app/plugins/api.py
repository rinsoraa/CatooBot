"""Capability guard + the bot surface a plugin actually receives (spec §88-§91).

``PluginApi`` is what the loader hands to a plugin (as ``bot``): the bot's
services pass through, but every surface that reaches outside the process is
gated by the plugin's manifest. Undeclared use is *refused*, logged to the
audit channel and counted — a plugin can never quietly reach further than it
says.

Gated surfaces:

===========================  ==================
capability                   surface
===========================  ==================
``send.group``               ``api.send_group_msg`` / ``api.send_msg(group_id=…)``
``send.private``             ``api.send_private_msg`` / ``api.send_msg(user_id=…)``
``api.call``                 ``api.call_api`` / any other BotApi method / ``bot.adapter``
``message.read``             ``bot.event_bus`` / ``api.on_message``
``schedule``                 ``bot.scheduler``
``tools``                    ``bot.tools``
``services``                 every other bot attribute (character, memory, sandbox, …)
===========================  ==================

``log`` / ``metrics`` / ``self_id`` are free (pure observability).
"""

from __future__ import annotations

import logging
from typing import Any

from app.plugins.manifest import PluginManifest

#: read-only bot attributes a plugin may use without declaring anything
FREE_ATTRIBUTES: frozenset[str] = frozenset({"log", "metrics", "self_id", "started_at"})

#: bot attributes that reach outside the process → the capability each needs
GATED_ATTRIBUTES: dict[str, str] = {
    "adapter": "api.call",
    "event_bus": "message.read",
    "scheduler": "schedule",
    "tools": "tools",
}


class PluginCapabilityError(Exception):
    """A plugin used a surface it did not declare in its manifest."""


class CapabilityGuard:
    """Answers one question: may this plugin use this capability, right now?"""

    def __init__(
        self,
        manifest: PluginManifest,
        *,
        log: logging.Logger | None = None,
        metrics: Any = None,
    ) -> None:
        self._manifest = manifest
        self._log = log or logging.getLogger("CatooBot.Plugins.Audit")
        self._metrics = metrics

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    def require(self, capability: str, detail: str = "") -> None:
        """Pass silently when declared; refuse loudly when not."""
        if capability in self._manifest.capabilities:
            return
        self._log.warning(
            "[Plugins.Audit] capability denied — plugin=%s needs=%s detail=%s declared=%s",
            self._manifest.id,
            capability,
            detail or "?",
            sorted(self._manifest.capabilities),
        )
        if self._metrics is not None:
            self._metrics.inc("plugin_capability_denied")
        raise PluginCapabilityError(
            f"plugin '{self._manifest.id}' used '{capability}' "
            f"({detail or 'unknown call site'}) without declaring it in plugin.json"
        )


class _GuardedBotApi:
    """``BotApi`` with per-method capability checks (no silent raw calls)."""

    def __init__(self, api: Any, guard: CapabilityGuard) -> None:
        self._api = api
        self._guard = guard

    async def send_group_msg(self, group_id: int, message: Any) -> int:
        self._guard.require("send.group", "api.send_group_msg")
        return await self._api.send_group_msg(group_id, message)

    async def send_private_msg(self, user_id: int, message: Any) -> int:
        self._guard.require("send.private", "api.send_private_msg")
        return await self._api.send_private_msg(user_id, message)

    async def send_msg(
        self,
        message: Any,
        *,
        user_id: int | None = None,
        group_id: int | None = None,
    ) -> int:
        capability = "send.group" if group_id is not None else "send.private"
        self._guard.require(capability, "api.send_msg")
        return await self._api.send_msg(message, user_id=user_id, group_id=group_id)

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        self._guard.require("api.call", f"api.call_api({action})")
        return await self._api.call_api(action, params, timeout)

    def __getattr__(self, name: str) -> Any:
        # Any other BotApi method (delete_msg, get_group_info, …) is a raw call.
        if name.startswith("_"):
            raise AttributeError(name)
        self._guard.require("api.call", f"api.{name}")
        return getattr(self._api, name)


class PluginApi:
    """The bot a plugin sees: services pass through, the outside world is gated.

    Unknown names stay unknown (``AttributeError`` from the real bot) so a
    plugin cannot silently poke at internals we never classified.
    """

    def __init__(
        self,
        bot: Any,
        manifest: PluginManifest,
        *,
        log: logging.Logger | None = None,
        metrics: Any = None,
    ) -> None:
        self._bot = bot
        self.manifest = manifest
        self._guard = CapabilityGuard(manifest, log=log, metrics=metrics)
        # free attributes are bound here so __getattr__ never runs for them
        self.log = getattr(bot, "log", logging.getLogger("CatooBot.Plugin"))
        self.metrics = getattr(bot, "metrics", None)
        self.self_id = getattr(bot, "self_id", None)
        self.api = _GuardedBotApi(getattr(bot, "api", None), self._guard)

    # --------------------------------------------------------- helpers

    def on_message(self, handler: Any) -> Any:
        """Subscribe to incoming messages (needs ``message.read``)."""
        self._guard.require("message.read", "on_message()")
        return self._bot.event_bus.on("message", handler)

    async def send_group(self, group_id: int, message: Any) -> int:
        """Send into a group (needs ``send.group``)."""
        return await self.api.send_group_msg(group_id, message)

    async def send_private(self, user_id: int, message: Any) -> int:
        """Send to a user (needs ``send.private``)."""
        return await self.api.send_private_msg(user_id, message)

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Raw OneBot action (needs ``api.call``)."""
        return await self.api.call_api(action, params, timeout)

    def schedule(self, job: Any) -> None:
        """Register a background job (needs ``schedule``)."""
        self._guard.require("schedule", "schedule()")
        self._bot.scheduler.register_job(job)

    # ------------------------------------------------------- pass-through

    def __getattr__(self, name: str) -> Any:
        if name.startswith("_"):
            raise AttributeError(name)
        if name in GATED_ATTRIBUTES:
            self._guard.require(GATED_ATTRIBUTES[name], f"bot.{name}")
            return getattr(self._bot, name)
        if name in FREE_ATTRIBUTES:
            return getattr(self._bot, name)
        self._guard.require("services", f"bot.{name}")
        return getattr(self._bot, name)

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<PluginApi {self.manifest.id} capabilities={sorted(self.manifest.capabilities)}>"
