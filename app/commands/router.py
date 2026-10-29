"""Routes message events to registered commands.

Matching rules (v0.1):

* the message text must start with the configured prefix (default ``/``)
* in groups, leading ``@bot`` segments are stripped first, so both
  ``/ping`` and ``@CatooBot /ping`` work
* the first whitespace-separated token after the prefix selects the command
  (aliases included); remaining tokens become ``ctx.args``
* permission failures and handler exceptions are logged, never propagated
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.commands.registry import Command, CommandRegistry
from app.core.context import Context
from app.core.exceptions import PermissionDenied
from app.message.event import MessageEvent
from app.message.message import Message

if TYPE_CHECKING:
    from app.core.bot import Bot


class CommandRouter:
    def __init__(
        self,
        bot: Bot,
        registry: CommandRegistry,
        prefix: str = "/",
        logger: logging.Logger | None = None,
    ) -> None:
        self._bot = bot
        self._registry = registry
        self._prefix = prefix
        self._log = logger or logging.getLogger("CatooBot.Command")

    async def handle(self, event: MessageEvent) -> None:
        """Event-bus subscriber for MessageEvent; dispatches commands."""
        # Parse on a copy: later bus subscribers (e.g. the chat plugin) must
        # still be able to detect the leading @bot mention in the original.
        parse_view = Message(event.message.segments)

        # Allow "@CatooBot /ping" (leading at-bot + optional whitespace).
        self_id = self._bot.self_id
        if self_id is not None:
            parse_view.strip_prefix_at(self_id)

        text = parse_view.text.strip()
        if not text.startswith(self._prefix):
            return
        body = text[len(self._prefix) :].strip()
        if not body:
            return

        parts = body.split()
        name, args = parts[0], parts[1:]
        cmd = self._registry.get(name)
        if cmd is None:
            self._log.debug("Unknown command '%s' from user %s", name, event.user_id)
            return

        self._log.info(
            "Command %s%s from user=%s%s",
            self._prefix,
            cmd.name,
            event.user_id,
            f" group={event.group_id}" if event.is_group else "",
        )

        if not self._bot.permissions.has(
            event.user_id, cmd.permission, group_role=event.sender.role
        ):
            self._log.warning(
                "Permission denied: user %s lacks '%s' for command '%s'",
                event.user_id,
                cmd.permission,
                cmd.name,
            )
            try:
                ctx = Context(self._bot, event, args)
                await ctx.reply("⛔ 权限不足，无法执行该命令。")
            except Exception:  # noqa: BLE001 - reply failure must not propagate
                self._log.exception("Failed to deliver permission-denied notice")
            return

        ctx = Context(self._bot, event, args)
        try:
            await cmd.handler(ctx)
        except PermissionDenied as exc:
            self._log.warning("Command '%s' raised PermissionDenied: %s", cmd.name, exc)
        except Exception:  # noqa: BLE001 - one broken command must not kill the bot
            self._log.exception("Command '%s' raised an exception", cmd.name)

    def matches(self, text: str) -> Command | None:
        """Non-side-effect helper (used by tests): parse text -> command."""
        stripped = text.strip()
        if not stripped.startswith(self._prefix):
            return None
        body = stripped[len(self._prefix) :].strip()
        if not body:
            return None
        return self._registry.get(body.split()[0])
