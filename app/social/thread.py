"""Conversation thread tracking (v0.9 §12/§13/§103/§104).

A thread is a short-lived "the character is mid-conversation here" marker. It
opens when she replies, tracks the last bot message (so a follow-up like
"看的什么剧?" can be recognized without @), and expires after a configurable
window so it never grows unbounded (v0.9 §109).
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any

from app.config.settings import SocialContinuationConfig
from app.social.models import ConversationThread


class ThreadManager:
    def __init__(
        self,
        *,
        config: SocialContinuationConfig | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config or SocialContinuationConfig()
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock
        self._threads: dict[str, ConversationThread] = {}

    def _window_seconds(self) -> float:
        return max(1, self._config.window_minutes) * 60.0

    # ------------------------------------------------------------------ read

    def get(self, group_id: str) -> ConversationThread | None:
        thread = self._threads.get(str(group_id))
        if thread is None:
            return None
        if not thread.is_alive(self._clock()):
            thread.status = "closed"
            return None
        return thread

    def all_active(self) -> list[ConversationThread]:
        return [t for t in self._threads.values() if t.is_alive(self._clock())]

    # ----------------------------------------------------------------- write

    def open(
        self,
        group_id: str,
        *,
        topic: str = "",
        bot_message: str = "",
        bot_message_id: str = "",
        participants: list[str] | None = None,
    ) -> ConversationThread:
        now = self._clock()
        existing = self._threads.get(str(group_id))
        if existing is not None and existing.is_alive(now):
            thread = existing
        else:
            thread = ConversationThread(
                thread_id=f"th_{uuid.uuid4().hex[:10]}",
                group_id=str(group_id),
                created_at=now,
            )
        thread.topic = topic or thread.topic
        thread.participants.update(participants or [])
        thread.last_bot_message = bot_message or thread.last_bot_message
        thread.last_bot_message_id = bot_message_id or thread.last_bot_message_id
        thread.status = "active"
        thread.updated_at = now
        thread.expires_at = now + self._window_seconds()
        self._threads[str(group_id)] = thread
        self._log.info("[Social] thread open group=%s topic=%.30s", group_id, thread.topic or "-")
        return thread

    def note_user_message(
        self, group_id: str, *, user_id: str, message: str, message_id: str
    ) -> None:
        thread = self.get(str(group_id))
        if thread is None:
            return
        thread.participants.add(str(user_id))
        thread.last_user_message = message
        thread.last_message_id = message_id
        thread.updated_at = self._clock()

    def close(self, group_id: str) -> None:
        thread = self._threads.get(str(group_id))
        if thread is not None:
            thread.status = "closed"

    def expire_stale(self) -> int:
        """Mark expired threads closed; returns how many were expired."""
        now = self._clock()
        closed = 0
        for thread in self._threads.values():
            if thread.status == "active" and now >= thread.expires_at:
                thread.status = "closed"
                closed += 1
        return closed

    # ------------------------------------------------------------------ view

    def snapshot(self, group_id: str) -> dict[str, Any] | None:
        thread = self.get(str(group_id))
        return thread.as_dict() if thread is not None else None
