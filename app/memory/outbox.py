"""Append-only write outbox (Task 14): a database outage must not lose memories.

When a write fails — database down, locked, closed — the *intent* is appended to
``pending.jsonl`` instead of vanishing, and the scheduler replays it once the
database answers again. Entries are replayed in order through the normal write
path (so dedup/conflict/quota logic applies again); a replayed entry is dropped
from the file with an atomic rewrite, so the file always holds exactly what
still needs writing.

The file lives next to the database it protects (:func:`outbox_path_for`), which
keeps a test database from ever writing into the operator's live data.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

OUTBOX_DIR = "outbox"
OUTBOX_FILE = "pending.jsonl"


def outbox_path_for(database_url: str) -> Path | None:
    """``data/outbox/pending.jsonl`` beside a ``sqlite:///…/x.db`` URL.

    ``None`` for anything that is not a local SQLite file (a future
    pgvector/Qdrant backend has its own durability story).
    """
    if not database_url.startswith("sqlite:///"):
        return None
    raw = database_url[len("sqlite:///") :]
    if not raw or raw.startswith(":memory:"):
        return None
    return Path(raw).parent / OUTBOX_DIR / OUTBOX_FILE


@dataclass
class OutboxEntry:
    kind: str
    payload: dict[str, Any]
    created_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(
            {"kind": self.kind, "payload": self.payload, "created_at": self.created_at},
            ensure_ascii=False,
        )

    @classmethod
    def from_line(cls, line: str, *, now: float) -> OutboxEntry | None:
        try:
            data = json.loads(line)
        except (TypeError, ValueError):
            return None
        if not isinstance(data, dict) or not isinstance(data.get("payload"), dict):
            return None
        kind = str(data.get("kind", "")).strip()
        if not kind:
            return None
        return cls(
            kind=kind, payload=data["payload"], created_at=float(data.get("created_at") or now)
        )


@dataclass
class ReplayReport:
    replayed: int = 0
    failed: int = 0
    remaining: int = 0
    dropped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "replayed": self.replayed,
            "failed": self.failed,
            "remaining": self.remaining,
            "dropped": self.dropped,
        }


class Outbox:
    """JSONL queue of writes the database refused (append-only, replayable)."""

    def __init__(
        self,
        path: str | Path,
        logger: logging.Logger | None = None,
        metrics: Any = None,
        clock: Any = time.time,
    ) -> None:
        self.path = Path(path)
        self._log = logger or logging.getLogger("CatooBot.Memory.Outbox")
        self._metrics = metrics
        self._clock = clock
        self._lock = asyncio.Lock()
        #: kind -> handler; let each subsystem own its own entry type
        self._handlers: dict[str, Any] = {}

    # ---------------------------------------------------------------- write

    async def enqueue(self, kind: str, payload: dict[str, Any]) -> bool:
        """Append one entry; ``False`` means it could not be persisted either."""
        entry = OutboxEntry(kind=kind, payload=payload, created_at=float(self._clock()))
        async with self._lock:
            try:
                await asyncio.to_thread(self._append, entry)
            except OSError as exc:
                self._count("outbox_failed")
                self._log.warning(
                    "[Memory.Outbox] could not append the entry (%s) — this write is lost", exc
                )
                return False
        self._count("outbox_enqueued")
        self._log.warning(
            "[Memory.Outbox] queued a %s write (%d pending) — it will be replayed",
            kind,
            await self.pending_count(),
        )
        return True

    def _append(self, entry: OutboxEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(entry.to_json() + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    # ----------------------------------------------------------------- read

    async def pending(self, limit: int | None = None) -> list[OutboxEntry]:
        """Entries still waiting, oldest first (malformed lines are dropped)."""
        entries, dropped = await self._load()
        if dropped:
            self._count("outbox_dropped", dropped)
        return entries[:limit] if limit else entries

    async def _load(self) -> tuple[list[OutboxEntry], int]:
        """(entries, malformed_line_count) — nothing is rewritten here."""
        if not self.path.exists():
            return [], 0
        raw_lines = await asyncio.to_thread(self._read_lines)
        entries: list[OutboxEntry] = []
        dropped = 0
        for line in raw_lines:
            if not line.strip():
                continue
            entry = OutboxEntry.from_line(line, now=float(self._clock()))
            if entry is None:
                dropped += 1
                self._log.warning("[Memory.Outbox] dropping an unreadable line: %.80s", line)
                continue
            entries.append(entry)
        return entries, dropped

    def _read_lines(self) -> list[str]:
        return self.path.read_text(encoding="utf-8").splitlines()

    async def pending_count(self) -> int:
        return len(await self.pending())

    async def stats(self) -> dict[str, Any]:
        return {"pending": await self.pending_count(), "path": str(self.path)}

    # --------------------------------------------------------------- replay

    def register(self, kind: str, handler: Any) -> None:
        """Route one entry kind to its owner (memory, social, ...)."""
        self._handlers[str(kind)] = handler

    async def replay(self, handler: Any = None, *, limit: int = 200) -> ReplayReport:
        """Hand every pending entry to *handler* (or the registered one); keep failures."""
        handler = handler or self._dispatch
        async with self._lock:
            entries, dropped = await self._load()
            report = ReplayReport(dropped=dropped)
            if dropped:
                self._count("outbox_dropped", dropped)
            if not entries and not dropped:
                return report
            remaining: list[OutboxEntry] = []
            for index, entry in enumerate(entries):
                if index >= limit:
                    remaining.append(entry)
                    continue
                try:
                    await handler(entry)
                except Exception as exc:  # noqa: BLE001 - keep it queued, never lose it
                    report.failed += 1
                    remaining.append(entry)
                    report.errors.append(f"{entry.kind}: {exc}")
                    self._count("outbox_failed")
                    continue
                report.replayed += 1
                self._count("outbox_replayed")
            if remaining != entries or dropped:
                await asyncio.to_thread(self._rewrite, remaining)
            report.remaining = len(remaining)
            if report.failed:
                self._log.warning(
                    "[Memory.Outbox] replayed %d, %d still failing (%s)",
                    report.replayed,
                    report.failed,
                    report.errors[0] if report.errors else "?",
                )
            elif report.replayed:
                self._log.info(
                    "[Memory.Outbox] replayed %d queued write(s), %d left",
                    report.replayed,
                    report.remaining,
                )
            return report

    async def _dispatch(self, entry: OutboxEntry) -> None:
        handler = self._handlers.get(entry.kind)
        if handler is None:
            raise ValueError(f"no handler registered for outbox kind {entry.kind!r}")
        await handler(entry)

    def _rewrite(self, entries: list[OutboxEntry]) -> None:
        """Atomically replace the file with what is still pending."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not entries:
            self.path.unlink(missing_ok=True)
            return
        descriptor, temp_name = tempfile.mkstemp(dir=str(self.path.parent), suffix=".tmp")
        temp_path = Path(temp_name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                for entry in entries:
                    handle.write(entry.to_json() + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            temp_path.replace(self.path)
        except OSError:
            temp_path.unlink(missing_ok=True)
            raise

    def _count(self, key: str, amount: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.inc(key, amount)
