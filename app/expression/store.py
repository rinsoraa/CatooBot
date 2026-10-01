"""Expression / 口癖 store (Task 22): SQLite persistence for learned phrases.

Separate from Memory: these are short, group-scoped *ways of talking* she picked
up from how a group chats, not facts about people or the world. Every row keeps
provenance (the raw, redacted source messages) so the operator can see where a
phrase came from and disable or delete it.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config.settings import ExpressionConfig

logger = logging.getLogger("CatooBot.Expression")

#: rejection reasons (deterministic, for the counters and the WebUI)
REJECT = {
    "sender_is_bot": "她自己或别的机器人",
    "quoted_only": "只是 @/引用里的被引文本",
    "no_lexical_content": "纯表情/符号/数字/链接",
    "too_long": "太长（只学短表达）",
    "contains_entity": "含人名/群名/URL/账号/数字",
    "sensitive": "命中敏感/攻击词表",
    "forbidden": "命中记忆侧禁忌判定",
    "below_threshold": "孤立梗（同一人且次数不足）",
}


@dataclass
class ExpressionPattern:
    id: int
    scope_key: str
    pattern: str
    kind: str
    sample_count: int
    speaker_count: int
    status: str
    use_count: int
    last_used_at: int | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ExpressionPattern:
        return cls(
            id=int(row["id"]),
            scope_key=str(row["scope_key"]),
            pattern=str(row["pattern"]),
            kind=str(row["kind"]),
            sample_count=int(row["sample_count"]),
            speaker_count=int(row["speaker_count"]),
            status=str(row["status"]),
            use_count=int(row["use_count"]),
            last_used_at=row["last_used_at"],
        )

    @property
    def threshold_met(self) -> bool:
        """Enough evidence to actually use this phrase (design ④)."""
        return self.speaker_count >= 2 or self.sample_count >= 3


class ExpressionStore:
    def __init__(self, database: Any, config: ExpressionConfig, *, clock: Any = time.time) -> None:
        self._db = database
        self._config = config
        self._clock = clock

    # ------------------------------------------------------------- learning

    async def learn(
        self,
        *,
        scope_key: str,
        pattern: str,
        kind: str,
        user_id: str,
        message_id: str,
        text: str,
        now: int,
    ) -> bool:
        """Record one observation of a pattern; returns True when it is new.

        ``speaker_count`` counts distinct users via the samples table (a repeat
        from the same user/message is idempotent through the unique index).
        """
        existing = await self._db.fetchone(
            "SELECT id FROM expression_patterns WHERE scope_key = ? AND pattern = ?",
            (scope_key, pattern),
        )
        if existing is None:
            pattern_id = await self._insert_pattern(
                scope_key, pattern, kind, user_id, message_id, text, now
            )
            return True
        pattern_id = int(existing["id"])
        await self._record_sample(pattern_id, user_id, message_id, text, now)
        await self._refresh_counts(pattern_id, now)
        return False

    async def _insert_pattern(
        self,
        scope_key: str,
        pattern: str,
        kind: str,
        user_id: str,
        message_id: str,
        text: str,
        now: int,
    ) -> int:
        await self._db.execute(
            "INSERT INTO expression_patterns (scope_key, pattern, kind, sample_count,"
            " speaker_count, first_seen_at, last_seen_at, status, created_at, updated_at)"
            " VALUES (?, ?, ?, 0, 0, ?, ?, 'active', ?, ?)",
            (scope_key, pattern, kind, now, now, now, now),
        )
        row = await self._db.fetchone(
            "SELECT id FROM expression_patterns WHERE scope_key = ? AND pattern = ?",
            (scope_key, pattern),
        )
        pattern_id = int(row["id"]) if row else 0
        await self._record_sample(pattern_id, user_id, message_id, text, now)
        await self._refresh_counts(pattern_id, now)
        return pattern_id

    async def _record_sample(
        self, pattern_id: int, user_id: str, message_id: str, text: str, now: int
    ) -> None:
        await self._db.execute(
            "INSERT OR IGNORE INTO expression_samples"
            " (pattern_id, message_id, user_id, text, seen_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (pattern_id, message_id, user_id, text[:80], now),
        )

    async def _refresh_counts(self, pattern_id: int, now: int) -> None:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT user_id) AS speakers"
            " FROM expression_samples WHERE pattern_id = ?",
            (pattern_id,),
        )
        if row is None:
            return
        await self._db.execute(
            "UPDATE expression_patterns SET sample_count = ?, speaker_count = ?,"
            " last_seen_at = ?, updated_at = ? WHERE id = ?",
            (int(row["n"]), int(row["speakers"]), now, now, pattern_id),
        )

    # ------------------------------------------------------------ injection

    async def active_patterns(self, scope_key: str) -> list[ExpressionPattern]:
        """Active, threshold-met patterns for this group (for the prompt)."""
        cfg = self._config
        rows = await self._db.fetchall(
            "SELECT * FROM expression_patterns WHERE scope_key = ? AND status = 'active'"
            " AND (speaker_count >= ? OR sample_count >= ?)"
            " ORDER BY sample_count DESC, speaker_count DESC, last_seen_at DESC",
            (scope_key, cfg.min_speakers, cfg.min_occurrences),
        )
        return [ExpressionPattern.from_row(r) for r in rows]

    async def list_patterns(self, scope_key: str = "") -> list[dict[str, Any]]:
        if scope_key:
            rows = await self._db.fetchall(
                "SELECT * FROM expression_patterns WHERE scope_key = ? ORDER BY id DESC",
                (scope_key,),
            )
        else:
            rows = await self._db.fetchall("SELECT * FROM expression_patterns ORDER BY id DESC")
        return [dict(r) for r in rows]

    async def samples(self, pattern_id: int) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT * FROM expression_samples WHERE pattern_id = ? ORDER BY seen_at DESC",
            (pattern_id,),
        )
        return [dict(r) for r in rows]

    # ------------------------------------------------------- disable/delete

    async def set_status(self, pattern_id: int, status: str) -> None:
        await self._db.execute(
            "UPDATE expression_patterns SET status = ?, updated_at = ? WHERE id = ?",
            (status, int(self._clock()), pattern_id),
        )

    async def delete(self, pattern_id: int) -> None:
        await self._db.execute("DELETE FROM expression_samples WHERE pattern_id = ?", (pattern_id,))
        await self._db.execute("DELETE FROM expression_vectors WHERE pattern_id = ?", (pattern_id,))
        await self._db.execute("DELETE FROM expression_patterns WHERE id = ?", (pattern_id,))

    async def mark_used(self, pattern_id: int, now: int) -> None:
        await self._db.execute(
            "UPDATE expression_patterns SET use_count = use_count + 1, last_used_at = ?,"
            " updated_at = ? WHERE id = ?",
            (now, now, pattern_id),
        )

    async def evict(self, scope_key: str) -> int:
        """Archive the lowest-value patterns when a group exceeds its cap."""
        cap = self._config.max_patterns_per_group
        rows = await self._db.fetchall(
            "SELECT id, sample_count, speaker_count, last_seen_at FROM expression_patterns"
            " WHERE scope_key = ? AND status = 'active'"
            " ORDER BY sample_count, speaker_count, last_seen_at",
            (scope_key,),
        )
        overflow = [r for r in rows[cap:]] if len(rows) > cap else []
        for row in overflow:
            await self.set_status(int(row["id"]), "archived")
        return len(overflow)
