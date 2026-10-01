"""Reply outcomes: what happened after she spoke (Task 20, milestone 1).

One row per **turn** — a turn is two or three bubbles and ``MessageDelivery``
keeps only the last message id per scope, so per-message rows would weight the
engagement average by bubble count and double-count a single bad reaction.

A row is written as ``pending`` the moment she answers; the settlement sweep
(milestone 2) fills in what followed once the observation window has passed.
``self_initiated`` is computed here from the reason she spoke — being @-ed
nearly always ends in an answer, so those turns are recorded but never feed the
engagement average (they would push it above 1 and make her speak *more* where
nobody answers her — the exact failure mode this loop exists to avoid).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database
    from app.memory.outbox import Outbox

#: reasons that mean "someone addressed her" — recorded, but never scored
ADDRESSED_REASONS = frozenset({"direct_mention", "reply_to_bot", "direct_follow_up"})

#: how long we watch for a reaction; also the row's settlement deadline
DEFAULT_WINDOW_SECONDS = 90.0


def is_self_initiated(reason_code: str) -> bool:
    """True when *she* opened the topic rather than answering an address."""
    return reason_code not in ADDRESSED_REASONS


class ReplyFeedbackStore:
    """Persistence for reply outcomes (the social side of Task 20)."""

    def __init__(
        self,
        database: Database,
        *,
        outbox: Outbox | None = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
        metrics: Any = None,
    ) -> None:
        self._db = database
        self._outbox = outbox
        self._log = logger or logging.getLogger("CatooBot.Social.Feedback")
        self._clock = clock
        self._metrics = metrics

    async def record_turn(
        self,
        *,
        turn_id: str,
        scope_key: str,
        reason_code: str,
        is_group: bool = True,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
    ) -> bool:
        """Write the ``pending`` row for one answered turn (idempotent)."""
        if not turn_id:
            return False
        payload = {
            "turn_id": turn_id,
            "scope_key": scope_key,
            "is_group": is_group,
            "reason_code": reason_code,
            "self_initiated": is_self_initiated(reason_code),
            "sent_at": float(self._clock()),
            "window_seconds": float(window_seconds),
        }
        try:
            await self._db.execute(
                """INSERT INTO reply_outcomes
                       (turn_id, scope_key, is_group, reason_code, self_initiated,
                        sent_at, window_seconds, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(turn_id) DO NOTHING""",
                (
                    payload["turn_id"],
                    payload["scope_key"],
                    1 if is_group else 0,
                    reason_code,
                    1 if payload["self_initiated"] else 0,
                    payload["sent_at"],
                    payload["window_seconds"],
                    payload["sent_at"],
                ),
            )
        except Exception as exc:  # noqa: BLE001 - feedback must never break chat
            self._count("reply_feedback_failed")
            await self._defer(payload, exc)
            return False
        self._count("reply_outcomes_recorded")
        return True

    async def _defer(self, payload: dict[str, Any], exc: Exception) -> None:
        """Queue the row when the database refuses it (Task 14 outbox)."""
        if self._outbox is None:
            self._log.warning("[Social.Feedback] could not record a turn (%s)", exc)
            return
        queued = await self._outbox.enqueue("reply_outcome", payload)
        self._log.warning(
            "[Social.Feedback] turn not recorded (%s) — %s",
            exc,
            "queued for replay" if queued else "and could not be queued, lost",
        )
        if queued:
            self._count("reply_outcome_enqueued")

    async def pending_before(self, cutoff: float, *, limit: int = 200) -> list[dict[str, Any]]:
        """Rows whose observation window has passed (oldest first)."""
        rows = await self._db.fetchall(
            "SELECT * FROM reply_outcomes"
            " WHERE verdict = 'pending' AND sent_at + window_seconds <= ?"
            " ORDER BY sent_at LIMIT ?",
            (float(cutoff), int(limit)),
        )
        return [dict(row) for row in rows]

    async def mark_unknown(self, row_ids: list[int], *, note: str = "") -> int:
        """Close rows we cannot fairly judge (restart, private chat, too stale)."""
        if not row_ids:
            return 0
        placeholders = ",".join("?" for _ in row_ids)
        await self._db.execute(
            f"UPDATE reply_outcomes SET verdict = 'unknown', settled_at = ?, note = ?"
            f" WHERE id IN ({placeholders})",
            (float(self._clock()), note, *row_ids),
        )
        return len(row_ids)

    async def recent(self, *, days: int = 7, limit: int = 500) -> list[dict[str, Any]]:
        """Settled rows for the WebUI card (newest first)."""
        since = float(self._clock()) - max(1, days) * 86400
        rows = await self._db.fetchall(
            "SELECT * FROM reply_outcomes WHERE sent_at >= ? AND verdict != 'pending'"
            " ORDER BY sent_at DESC LIMIT ?",
            (since, int(limit)),
        )
        return [dict(row) for row in rows]

    def _count(self, key: str, amount: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.inc(key, amount)
