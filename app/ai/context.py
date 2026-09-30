"""Short-term conversation context, isolated per session.

Session ids are ``private:<user_id>`` and ``group:<group_id>`` so no context
can ever leak between users or groups. Only recent turns are kept (v0.2 has
no long-term memory); turns are mirrored into SQLite so context survives a
CatooBot restart. Long-term memory / RAG would extend this class later.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from app.ai.models import ChatMessage

if TYPE_CHECKING:
    from app.config.settings import AIContextConfig
    from app.database.database import Database


class ConversationManager:
    def __init__(
        self,
        context_config: AIContextConfig,
        database: Database | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = context_config
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.AI.Context")
        self._sessions: dict[str, list[ChatMessage]] = {}
        self._loaded: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    # ---------------------------------------------------------------- read

    async def get_context(self, session_id: str) -> list[ChatMessage]:
        """Recent turns of a session (copy). Empty when context is disabled."""
        if not self._config.enabled:
            return []
        if session_id not in self._loaded:
            await self._load_session(session_id)
        return list(self._sessions.get(session_id, []))

    # --------------------------------------------------------------- write

    async def append_user_message(self, session_id: str, content: str) -> None:
        await self._append(session_id, ChatMessage.user(content))

    async def append_assistant_message(self, session_id: str, content: str) -> None:
        await self._append(session_id, ChatMessage.assistant(content))

    async def clear_context(self, session_id: str) -> None:
        """Delete one session's context (other sessions are untouched)."""
        self._sessions.pop(session_id, None)
        self._loaded.discard(session_id)
        if self._db is not None:
            await self._db.execute("DELETE FROM conversations WHERE session_id = ?", (session_id,))
        self._log.info("Cleared context for session=%s", session_id)

    async def reset(self) -> None:
        """Forget everything (used at shutdown / tests)."""
        self._sessions.clear()
        self._loaded.clear()

    # ------------------------------------------------------------ internals

    def _trim(self, messages: list[ChatMessage]) -> list[ChatMessage]:
        max_messages = self._config.max_messages
        return messages[-max_messages:]

    async def _append(self, session_id: str, message: ChatMessage) -> None:
        if not self._config.enabled:
            return
        if session_id not in self._loaded:
            await self._load_session(session_id)
        if message.created_at is None:
            message.created_at = time.time()
        history = self._sessions.setdefault(session_id, [])
        history.append(message)
        self._sessions[session_id] = self._trim(history)
        if self._db is not None:
            try:
                await self._db.execute(
                    "INSERT INTO conversations"
                    " (session_id, role, content, created_at) VALUES (?, ?, ?, ?)",
                    (session_id, message.role, message.content, int(time.time())),
                )
            except Exception:  # noqa: BLE001 - a DB hiccup must not eat the reply
                self._log.exception("Failed to persist conversation turn")

    async def _load_session(self, session_id: str) -> None:
        """Lazily restore one session from SQLite (restart persistence)."""
        self._loaded.add(session_id)
        if self._db is None:
            return
        try:
            rows = await self._db.fetchall(
                "SELECT role, content, created_at FROM conversations"
                " WHERE session_id = ? ORDER BY id ASC",
                (session_id,),
            )
        except Exception:  # noqa: BLE001 - persistence trouble must not break chat
            self._log.exception("Failed to load context for session=%s", session_id)
            return
        messages = [
            ChatMessage(
                role=row["role"],
                content=row["content"],
                created_at=float(row["created_at"]) if row["created_at"] else None,
            )
            for row in rows
        ]
        self._sessions[session_id] = self._trim(messages)
        if messages:
            self._log.debug("Restored %d messages for session=%s", len(messages), session_id)
