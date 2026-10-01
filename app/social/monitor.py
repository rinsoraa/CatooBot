"""Group Conversation Monitor (v0.9 §8/§10/§25).

Keeps a bounded, per-group ring buffer of recent messages and tracks how many
*external* (non-bot) messages are waiting to be observed. The buffer is memory
only — deeper history always comes from Topic / Memory, never from here
(spec §10).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from app.social.models import GroupMessage


class GroupConversationMonitor:
    def __init__(
        self,
        *,
        max_messages: int = 30,
        logger: logging.Logger | None = None,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._max = max(1, int(max_messages))
        self._buffers: dict[str, deque[GroupMessage]] = {}
        #: per group -> message_id up to which the observer has consumed
        self._observed_until: dict[str, str] = {}

    # ---------------------------------------------------------------- record

    def record(
        self,
        group_id: str,
        message_id: str,
        user_id: str,
        nickname: str,
        content: str,
        *,
        timestamp: float,
        is_bot: bool = False,
        reply_to: str | None = None,
    ) -> GroupMessage:
        message = GroupMessage(
            message_id=str(message_id),
            group_id=str(group_id),
            user_id=str(user_id),
            nickname=nickname,
            timestamp=timestamp,
            content=content,
            is_bot_message=is_bot,
            reply_to=str(reply_to) if reply_to is not None else None,
        )
        buffer = self._buffers.setdefault(str(group_id), deque())
        buffer.append(message)
        while len(buffer) > self._max:
            buffer.popleft()
        return message

    def record_bot(self, group_id: str, message_id: str, content: str, timestamp: float) -> None:
        """Record the character's own message (never counts for observation)."""
        self.record(
            group_id,
            message_id,
            user_id="bot",
            nickname="",
            content=content,
            timestamp=timestamp,
            is_bot=True,
        )

    # ------------------------------------------------------------------ read

    def recent(self, group_id: str, limit: int | None = None) -> list[GroupMessage]:
        buffer = self._buffers.get(str(group_id))
        if not buffer:
            return []
        return list(buffer)[-(limit or len(buffer)) :]

    def external_recent(self, group_id: str, limit: int | None = None) -> list[GroupMessage]:
        return [m for m in self.recent(group_id, limit) if m.external]

    def last_bot_message(self, group_id: str) -> GroupMessage | None:
        for message in reversed(self.recent(str(group_id))):
            if message.is_bot_message:
                return message
        return None

    def last_external_message(self, group_id: str) -> GroupMessage | None:
        for message in reversed(self.recent(str(group_id))):
            if message.external:
                return message
        return None

    # ------------------------------------------------------------ observation

    def unobserved(self, group_id: str) -> list[GroupMessage]:
        """External messages the observer has not yet consumed (spec §82)."""
        seen = self._observed_until.get(str(group_id))
        result: list[GroupMessage] = []
        for message in self.recent(str(group_id)):
            if not message.external:
                continue
            if seen is None or message.message_id > seen:
                result.append(message)
        return result

    def unobserved_count(self, group_id: str) -> int:
        return len(self.unobserved(str(group_id)))

    def mark_observed(self, group_id: str) -> None:
        """Advance the observation pointer past the newest external message."""
        latest = self.last_external_message(str(group_id))
        if latest is not None:
            self._observed_until[str(group_id)] = latest.message_id

    def is_bot_message_id(self, group_id: str, message_id: str) -> bool:
        return any(
            m.is_bot_message and m.message_id == str(message_id) for m in self.recent(str(group_id))
        )

    # ------------------------------------------------------------------ view

    def snapshot(self, group_id: str) -> dict[str, Any]:
        recent = self.recent(str(group_id))
        last_bot = self.last_bot_message(str(group_id))
        return {
            "group_id": str(group_id),
            "recent_count": len(recent),
            "unobserved": self.unobserved_count(str(group_id)),
            "last_bot_message": last_bot.content if last_bot else "",
            "recent_messages": [
                {
                    "user_id": m.user_id,
                    "nickname": m.nickname,
                    "content": m.content[:80],
                    "is_bot": m.is_bot_message,
                    "message_id": m.message_id,
                }
                for m in recent[-10:]
            ],
        }

    def groups(self) -> list[str]:
        return sorted(self._buffers)
