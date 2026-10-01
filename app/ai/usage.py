"""Per-call model usage (Task 15): tokens, latency and outcome, one row each.

The router records every provider attempt — success or failure — so the model
page can answer "which model got slower", "where are the tokens going" and
"what is failing" without guesswork. Recording is best-effort by design: a
usage write must never break a model call, so trouble is logged and counted
instead of raised.

Rows are pruned by retention (``ai.usage.retention_days``) on the shared
scheduler; the table is local SQLite, where one row per call is cheap.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database


class UsageRecorder:
    def __init__(
        self,
        database: Database,
        logger: logging.Logger | None = None,
        metrics: Any = None,
        clock: Any = time.time,
    ) -> None:
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.AI.Usage")
        self._metrics = metrics
        self._clock = clock

    async def record(
        self,
        *,
        provider: str,
        model: str,
        latency_ms: float,
        ok: bool,
        usage: dict[str, Any] | None = None,
        error_type: str = "",
        attempt: int = 1,
        purpose: str = "",
    ) -> None:
        """One row per attempt; never raises into the calling path."""
        usage = usage or {}
        try:
            await self._db.execute(
                """INSERT INTO ai_usage
                       (ts, provider, model, purpose, prompt_tokens, completion_tokens,
                        total_tokens, latency_ms, attempt, ok, error_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    int(self._clock()),
                    provider,
                    model,
                    purpose,
                    _as_int(usage.get("prompt_tokens")),
                    _as_int(usage.get("completion_tokens")),
                    _as_int(usage.get("total_tokens")),
                    round(float(latency_ms), 1),
                    int(attempt),
                    1 if ok else 0,
                    error_type,
                ),
            )
        except Exception as exc:  # noqa: BLE001 - usage must never break a model call
            if self._metrics is not None:
                self._metrics.inc("ai_usage_failed")
            self._log.warning("[AI.Usage] could not record a call (%s)", exc)

    async def prune(self, retention_days: int) -> int:
        """Drop rows older than the retention window; returns how many went."""
        cutoff = int(self._clock()) - max(1, retention_days) * 86400
        try:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM ai_usage WHERE ts < ?", (cutoff,)
            )
            count = int(row["n"]) if row else 0
            if count:
                await self._db.execute("DELETE FROM ai_usage WHERE ts < ?", (cutoff,))
                self._log.info(
                    "[AI.Usage] pruned %d row(s) older than %d days", count, retention_days
                )
            return count
        except Exception as exc:  # noqa: BLE001 - upkeep must not raise
            self._log.warning("[AI.Usage] prune failed (%s)", exc)
            return 0

    async def summary(self, *, days: int = 7) -> list[dict[str, Any]]:
        """Per-model rollup for the WebUI: calls, tokens, latency, failures."""
        since = int(self._clock()) - max(1, days) * 86400
        try:
            rows = await self._db.fetchall(
                """SELECT model,
                          COUNT(*)                        AS calls,
                          SUM(ok)                         AS ok_calls,
                          SUM(prompt_tokens)              AS prompt_tokens,
                          SUM(completion_tokens)          AS completion_tokens,
                          ROUND(AVG(latency_ms), 1)       AS avg_latency_ms,
                          ROUND(MAX(latency_ms), 1)       AS max_latency_ms
                     FROM ai_usage WHERE ts >= ?
                     GROUP BY model ORDER BY calls DESC""",
                (since,),
            )
        except Exception:  # noqa: BLE001 - a pre-migration database has no table
            return []
        return [
            {
                "model": str(row["model"]),
                "calls": int(row["calls"] or 0),
                "failures": int(row["calls"] or 0) - int(row["ok_calls"] or 0),
                "prompt_tokens": int(row["prompt_tokens"] or 0),
                "completion_tokens": int(row["completion_tokens"] or 0),
                "avg_latency_ms": float(row["avg_latency_ms"] or 0.0),
                "max_latency_ms": float(row["max_latency_ms"] or 0.0),
            }
            for row in rows
        ]


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0
