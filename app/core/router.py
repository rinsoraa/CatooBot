"""Core-owned message event processing that is not command dispatch.

v0.1 responsibilities: log incoming messages (DEBUG shows raw JSON, INFO a
one-line summary) and upsert user/group rows into SQLite. AI and other
features must join as plugins subscribing to the event bus instead.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from app.message.event import MessageEvent
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.core.bot import Bot


class CoreRouter:
    def __init__(self, bot: Bot) -> None:
        self._bot = bot
        self._log = bot.log

    async def on_message(self, event: MessageEvent) -> None:
        self._bot.metrics.inc("messages_received")
        scope = f"group={event.group_id} " if event.is_group else ""
        text = event.message.text or event.message.to_cq_string()
        self._log.info("Message %suser=%s message=%s", scope, event.user_id, text)
        from app.utils import console

        channel = "群聊" if event.is_group else "私聊"
        where = f"群 {event.group_id}" if event.is_group else "私聊"
        doing = ""
        world = getattr(self._bot, "world", None)
        if world is not None and getattr(world, "enabled", False):
            try:
                doing = f"她正在{world.status_line()}"
            except Exception:  # noqa: BLE001 - cosmetic only
                doing = ""
        narrate().sense(
            f"{console.paint(channel, 'bright_cyan')} "
            f"{console.paint(event.sender.display_name or event.user_id, 'bold')} "
            f"{console.paint('›', 'bright_black')} {text}",
            detail=f"{where} · user {event.user_id}" + (f" · {doing}" if doing else ""),
        )
        if self._bot.config.bot.debug:
            self._log.debug("MessageEvent raw: %s", event.raw_event)

        await self._record(event)

    async def _record(self, event: MessageEvent) -> None:
        """Best-effort persistence; failures only log."""
        try:
            now = int(time.time())
            await self._bot.database.upsert_user(
                event.user_id, event.sender.display_name, now
            )
            if event.is_group and event.group_id is not None:
                await self._bot.database.upsert_group(event.group_id, None, now)
        except Exception:  # noqa: BLE001
            self._bot.log.exception("Failed to persist message metadata")
