"""Topic threads: unfinished conversations the character can naturally resume.

TopicManager is deliberately scope-isolated (``user:<id>`` / ``group:<id>``),
so a topic from one chat can never surface in another. Topics are what makes
initiative feel motivated (spec v0.8 §30) instead of random small talk.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from app.behavior.models import TopicThread
from app.memory.retrieval import bigrams

if TYPE_CHECKING:
    from app.database.database import Database

ACTIVE_STATUSES = ("active", "waiting")


class TopicManager:
    def __init__(
        self,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Topic")
        self._clock = clock

    @property
    def enabled(self) -> bool:
        return self._db is not None

    # ---------------------------------------------------------------- write

    async def create(
        self,
        scope_key: str,
        title: str,
        *,
        importance: float = 0.5,
        participants: list[str] | None = None,
        status: str = "active",
    ) -> TopicThread | None:
        title = title.strip()
        if not title or self._db is None:
            return None
        existing = await self.find_similar(scope_key, title)
        if existing is not None and existing.status in ACTIVE_STATUSES:
            return await self.touch(existing.id)
        now = int(self._clock())
        import json

        await self._db.execute(
            """INSERT INTO topics
                   (scope_key, title, status, importance, participants,
                    last_discussed_at, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scope_key,
                title,
                status,
                max(0.0, min(1.0, importance)),
                json.dumps(participants or [], ensure_ascii=False),
                now,
                now,
                now,
            ),
        )
        topic = await self.find_similar(scope_key, title)
        self._log.info("[Topic] created (%s): %.40s", scope_key, title)
        return topic

    async def update(
        self,
        topic_id: int,
        *,
        title: str | None = None,
        status: str | None = None,
        importance: float | None = None,
    ) -> bool:
        if self._db is None:
            return False
        topic = await self.get(topic_id)
        if topic is None:
            return False
        now = int(self._clock())
        await self._db.execute(
            """UPDATE topics SET title = ?, status = ?, importance = ?,
                   last_discussed_at = ?, updated_at = ? WHERE id = ?""",
            (
                title or topic.title,
                status or topic.status,
                importance if importance is not None else topic.importance,
                now,
                now,
                topic_id,
            ),
        )
        return True

    async def touch(self, topic_id: int) -> TopicThread | None:
        """Mark a topic as just discussed (keeps it alive)."""
        if self._db is None:
            return None
        now = int(self._clock())
        await self._db.execute(
            "UPDATE topics SET last_discussed_at = ?, updated_at = ? WHERE id = ?",
            (now, now, topic_id),
        )
        return await self.get(topic_id)

    async def resolve(self, topic_id: int) -> bool:
        return await self.update(topic_id, status="resolved")

    async def forget(self, topic_id: int) -> bool:
        return await self.update(topic_id, status="forgotten")

    async def delete(self, topic_id: int) -> bool:
        if self._db is None:
            return False
        await self._db.execute("DELETE FROM topics WHERE id = ?", (topic_id,))
        return True

    # ----------------------------------------------------------------- read

    async def get(self, topic_id: int) -> TopicThread | None:
        if self._db is None:
            return None
        row = await self._db.fetchone("SELECT * FROM topics WHERE id = ?", (topic_id,))
        return self._to_model(row) if row else None

    async def get_active_topics(self, scope_key: str, limit: int = 10) -> list[TopicThread]:
        """Unfinished topics, most recently discussed first."""
        if self._db is None:
            return []
        placeholders = ",".join("?" for _ in ACTIVE_STATUSES)
        rows = await self._db.fetchall(
            f"""SELECT * FROM topics WHERE scope_key = ? AND status IN ({placeholders})
                ORDER BY importance DESC, COALESCE(last_discussed_at, created_at) DESC
                LIMIT ?""",
            (scope_key, *ACTIVE_STATUSES, limit),
        )
        return [self._to_model(row) for row in rows]

    async def all_topics(
        self, scope_key: str = "", status: str = "", limit: int = 100
    ) -> list[TopicThread]:
        if self._db is None:
            return []
        sql = "SELECT * FROM topics WHERE 1=1"
        params: list[Any] = []
        if scope_key:
            sql += " AND scope_key = ?"
            params.append(scope_key)
        if status:
            sql += " AND status = ?"
            params.append(status)
        sql += " ORDER BY COALESCE(last_discussed_at, created_at) DESC LIMIT ?"
        params.append(limit)
        rows = await self._db.fetchall(sql, tuple(params))
        return [self._to_model(row) for row in rows]

    async def find_similar(self, scope_key: str, title: str) -> TopicThread | None:
        """Lexical near-duplicate lookup inside one scope (no embeddings)."""
        if self._db is None:
            return None
        rows = await self._db.fetchall(
            "SELECT * FROM topics WHERE scope_key = ? ORDER BY updated_at DESC LIMIT 50",
            (scope_key,),
        )
        wanted = bigrams(title)
        if not wanted:
            return None
        best: tuple[float, TopicThread] | None = None
        for row in rows:
            topic = self._to_model(row)
            overlap = len(wanted & bigrams(topic.title)) / max(1, len(wanted))
            if overlap >= 0.6 and (best is None or overlap > best[0]):
                best = (overlap, topic)
        return best[1] if best else None

    async def count(self, scope_key: str = "") -> int:
        if self._db is None:
            return 0
        if scope_key:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM topics WHERE scope_key = ?", (scope_key,)
            )
        else:
            row = await self._db.fetchone("SELECT COUNT(*) AS n FROM topics")
        return int(row["n"]) if row else 0

    # ------------------------------------------------------------ internals

    @staticmethod
    def _to_model(row: Any) -> TopicThread:
        import json

        data = dict(row)
        raw_participants = data.get("participants") or "[]"
        try:
            participants = json.loads(raw_participants)
        except (TypeError, ValueError):
            participants = []
        data["participants"] = participants
        return TopicThread.model_validate(data)
