"""Message delivery: send a planned reply with delays, never blocking others.

Delivery is the only place that sleeps before sending, so the QQ reply path
gains 'natural rhythm' without any of it leaking into the character logic.
The sleep function is injectable so tests can assert the planned delays
without waiting.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.behavior.models import ResponsePlan
from app.message.event import MessageEvent
from app.utils.narrator import narrate

if TYPE_CHECKING:
    from app.core.bot import Bot


@dataclass
class DeliveryTarget:
    """Where a message goes: a private user or a group."""

    user_id: int | None = None
    group_id: int | None = None

    @property
    def is_group(self) -> bool:
        return self.group_id is not None

    @classmethod
    def from_event(cls, event: MessageEvent) -> DeliveryTarget:
        if event.is_group and event.group_id is not None:
            return cls(group_id=event.group_id)
        return cls(user_id=event.user_id)


SleepFn = Callable[[float], Awaitable[None]]


class MessageDelivery:
    def __init__(
        self,
        bot: Bot,
        logger: logging.Logger | None = None,
        sleep: SleepFn | None = None,
    ) -> None:
        self._bot = bot
        self._log = logger or logging.getLogger("CatooBot.Response")
        self._sleep: SleepFn = sleep or asyncio.sleep
        #: last real message_id sent per target key (group:<id> / user:<id>).
        #: Used by the social engine to detect "reply to the bot".
        self.last_sent_ids: dict[str, int] = {}

    async def deliver(self, target: DeliveryTarget, plan: ResponsePlan) -> list[str]:
        """Send every chunk, honouring the planned delay before the first one."""
        if plan.is_empty:
            return []
        where = "群聊" if target.is_group else "私聊"
        story = narrate()
        if plan.delay > 0:
            story.flow(
                f"心里过了一下，{plan.delay:.1f}s 后发",
                detail=f"{where} · {len(plan.chunks)} 条 · {plan.reason or 'rhythm'}",
            )
            await self._sleep(plan.delay)

        sent: list[str] = []
        last_id = -1
        for index, chunk in enumerate(plan.chunks):
            if index > 0:
                gap = (
                    plan.inter_chunk_delays[index - 1]
                    if index - 1 < len(plan.inter_chunk_delays)
                    else 0.0
                )
                if gap > 0:
                    await self._sleep(gap)
            last_id = await self._send_one(target, chunk)
            sent.append(chunk)
            story.reply(chunk, index=index + 1, total=len(plan.chunks))

        # v1.1: an optional sticker/face attachment after the text (never a plain image).
        if plan.attachment is not None:
            last_id = await self._send_attachment(target, plan.attachment)

        self._bot.metrics.inc("replies_sent")
        if last_id and last_id > 0:
            key = f"group:{target.group_id}" if target.is_group else f"user:{target.user_id}"
            self.last_sent_ids[key] = int(last_id)
        return sent

    async def _send_attachment(self, target: DeliveryTarget, attachment: Any) -> int:
        """Send one sticker / native face via the MediaRuntime sender (§32)."""
        media = getattr(self._bot, "media", None)
        if media is None:
            return -1
        message = media.build_message(attachment)
        if not len(message):
            return -1
        if target.is_group and target.group_id is not None:
            return await self._bot.api.send_group_msg(target.group_id, message)
        if target.user_id is not None:
            return await self._bot.api.send_private_msg(target.user_id, message)
        return -1

    async def _send_one(self, target: DeliveryTarget, text: str) -> int:
        """One QQ message; over-long text still falls back to hard splitting."""
        from app.message.splitter import split_message

        last_id = -1
        for part in split_message(text, 2000):
            if target.is_group and target.group_id is not None:
                last_id = await self._bot.api.send_group_msg(target.group_id, part)
            elif target.user_id is not None:
                last_id = await self._bot.api.send_private_msg(target.user_id, part)
        return int(last_id or -1)

    @staticmethod
    def target_for_user(user_id: int) -> DeliveryTarget:
        return DeliveryTarget(user_id=user_id)

    def snapshot(self) -> dict[str, Any]:
        return {"sleep_injected": self._sleep is not asyncio.sleep}
