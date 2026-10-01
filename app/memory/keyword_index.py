"""Keyword channel backed by SQLite FTS5 (Task 12, step 1).

Keyword *scoring* does not change — ``keyword_overlap`` still runs in Python —
but the candidate set is now chosen by an index instead of by importance alone:
the repository's candidate query is capped (``LIMIT 200`` ordered by
importance), so a memory that matches the query perfectly can be invisible to
the keyword channel. FTS5 searches the whole scope, so matches are found
wherever they sit in that ordering.

``search_text`` stores exactly the tokens :func:`bigrams` produces (CJK
characters, CJK bigrams, ascii words) pre-joined with spaces — that is what
makes short Chinese queries ("可乐") match, because the unicode61 tokenizer
has no CJK segmentation of its own.

The index is *derived* data: it is maintained by the single write path
(:class:`~app.memory.repository.MemoryRepository`) plus SQLite triggers, and
never read for content — only for row ids, which are then scored the old way.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database

#: rows whose search_text is empty are legacy rows (pre-index) — backfilled once
_BACKFILL_BATCH = 500


def bigrams(text: str) -> set[str]:
    """Character bigrams (ASCII words kept whole). Good enough for zh/en mix."""
    tokens = set[str]()
    current_ascii: list[str] = []
    for char in text.lower():
        if "\u4e00" <= char <= "\u9fff":
            tokens.add(char)
            if current_ascii:
                tokens.add("".join(current_ascii))
                current_ascii = []
        elif char.isalnum():
            current_ascii.append(char)
        else:
            if current_ascii:
                tokens.add("".join(current_ascii))
                current_ascii = []
    if current_ascii:
        tokens.add("".join(current_ascii))
    for index in range(len(text) - 1):
        a, b = text[index], text[index + 1]
        if "\u4e00" <= a <= "\u9fff" and "\u4e00" <= b <= "\u9fff":
            tokens.add(a + b)
    return tokens


def index_text(*parts: str) -> str:
    """The exact string stored in ``memories.search_text``."""
    return " ".join(sorted(bigrams(" ".join(part for part in parts if part))))


def match_expression(query: str) -> str:
    """FTS5 MATCH expression for a free-text query (tokens OR-ed together)."""
    return " OR ".join(f'"{token}"' for token in sorted(bigrams(query)))


class KeywordIndex:
    """Candidate lookup over ``memories_fts`` (never returns content)."""

    def __init__(self, database: Database, logger: logging.Logger | None = None) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Memory")
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    async def search(
        self,
        query: str,
        *,
        scope_keys: list[str],
        limit: int,
        statuses: tuple[str, ...] = ("active",),
    ) -> list[int]:
        """Row ids matching *query*, ranked by bm25 (best first)."""
        if not scope_keys or not query.strip():
            return []
        expression = match_expression(query)
        if not expression:
            return []
        scope_placeholders = ",".join("?" for _ in scope_keys)
        sql = (
            "SELECT memories_fts.rowid AS id, bm25(memories_fts) AS rank"
            " FROM memories_fts"
            " JOIN memories ON memories.id = memories_fts.rowid"
            f" WHERE memories_fts MATCH ? AND memories.scope_key IN ({scope_placeholders})"
        )
        params: list[Any] = [expression, *scope_keys]
        if statuses:
            status_placeholders = ",".join("?" for _ in statuses)
            sql += f" AND memories.status IN ({status_placeholders})"
            params.extend(statuses)
        sql += " ORDER BY rank LIMIT ?"
        params.append(limit)
        try:
            rows = await self._db.fetchall(sql, tuple(params))
        except Exception:  # noqa: BLE001 - a broken index must not break chat
            self._log.warning("[Memory.Index] keyword search failed (falling back)", exc_info=True)
            return []
        return [int(row["id"]) for row in rows]

    async def ensure_ready(self) -> int:
        """Backfill ``search_text`` for legacy rows, once per process."""
        if self._ready:
            return 0
        self._ready = True
        return await self.backfill()

    async def backfill(self, batch: int = _BACKFILL_BATCH) -> int:
        """Fill ``search_text`` for rows that predate the index (one batch)."""
        try:
            rows = await self._db.fetchall(
                "SELECT id, content, summary FROM memories WHERE search_text = '' LIMIT ?",
                (batch,),
            )
        except Exception:  # noqa: BLE001 - pre-migration databases have no column
            return 0
        for row in rows:
            await self._db.execute(
                "UPDATE memories SET search_text = ? WHERE id = ?",
                (index_text(str(row["content"]), str(row["summary"] or "")), int(row["id"])),
            )
        if rows:
            self._log.info("[Memory.Index] backfilled %d row(s) into the keyword index", len(rows))
        return len(rows)
