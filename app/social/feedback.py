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
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.database.database import Database
    from app.memory.outbox import Outbox

#: reasons that mean "someone addressed her" — recorded, but never scored
ADDRESSED_REASONS = frozenset({"direct_mention", "reply_to_bot", "direct_follow_up"})

#: how long we watch for a reaction; also the row's settlement deadline
DEFAULT_WINDOW_SECONDS = 90.0


def is_self_initiated(reason_code: str, *, is_group: bool = True) -> bool:
    """True when *she* opened the topic rather than answering an address.

    Private chat is always an address by definition (someone DM'd her), so a
    non-group turn is never self-initiated — it must never feed the group
    engagement average.
    """
    return is_group and reason_code not in ADDRESSED_REASONS


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
            "self_initiated": is_self_initiated(reason_code, is_group=is_group),
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

    #: bot_state key holding the engagement snapshot (Task 20 v0.9 §6)
    ENGAGEMENT_KEY = "social_engagement"

    async def load_engagement(self) -> dict[str, Any] | None:
        """Read the persisted engagement memory (None when never written)."""
        try:
            data = await self._db.get_setting_json(self.ENGAGEMENT_KEY)
        except Exception as exc:  # noqa: BLE001 - a missing snapshot is not an error
            self._log.warning("[Social.Feedback] engagement load failed (%s)", exc)
            return None
        return data if isinstance(data, dict) else None

    async def save_engagement(self, snapshot: dict[str, Any]) -> bool:
        try:
            await self._db.set_setting_json(self.ENGAGEMENT_KEY, snapshot, int(self._clock()))
        except Exception as exc:  # noqa: BLE001 - persisting must never break a sweep
            self._count("reply_feedback_failed")
            self._log.warning("[Social.Feedback] engagement save failed (%s)", exc)
            return False
        return True

    async def prune(self, retention_days: int) -> int:
        """Drop settled rows older than the retention window (daily job)."""
        cutoff = float(self._clock()) - max(1, retention_days) * 86400
        try:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM reply_outcomes"
                " WHERE verdict != 'pending' AND sent_at < ?",
                (cutoff,),
            )
            count = int(row["n"]) if row else 0
            if count:
                await self._db.execute(
                    "DELETE FROM reply_outcomes WHERE verdict != 'pending' AND sent_at < ?",
                    (cutoff,),
                )
            return count
        except Exception as exc:  # noqa: BLE001 - upkeep must not raise
            self._log.warning("[Social.Feedback] prune failed (%s)", exc)
            return 0

    async def replay_entry(self, entry: Any) -> None:
        """Outbox handler for kind='reply_outcome' (Task 14 integration)."""
        if entry.kind != "reply_outcome":
            raise ValueError(f"unexpected outbox kind for the feedback store: {entry.kind!r}")
        payload = dict(entry.payload or {})
        await self._db.execute(
            """INSERT INTO reply_outcomes
                   (turn_id, scope_key, is_group, reason_code, self_initiated,
                    sent_at, window_seconds, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(turn_id) DO NOTHING""",
            (
                str(payload.get("turn_id", "")),
                str(payload.get("scope_key", "")),
                1 if payload.get("is_group") else 0,
                str(payload.get("reason_code", "")),
                1 if payload.get("self_initiated") else 0,
                float(payload.get("sent_at", 0.0) or 0.0),
                float(
                    payload.get("window_seconds", DEFAULT_WINDOW_SECONDS) or DEFAULT_WINDOW_SECONDS
                ),
                float(payload.get("sent_at", 0.0) or 0.0),
            ),
        )
        self._count("outbox_replayed")

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

    async def settle(
        self,
        row_id: int,
        *,
        verdict: str,
        polarity: int = 0,
        replies: int = 0,
        first_reply_after: float | None = None,
        addressed_back: bool = False,
        baseline: int = 0,
        note: str = "",
    ) -> None:
        """Close one row with its verdict (milestone 2 writes these)."""
        await self._db.execute(
            """UPDATE reply_outcomes
                  SET verdict = ?, polarity = ?, replies = ?, first_reply_after = ?,
                      addressed_back = ?, baseline = ?, settled_at = ?, note = ?
                WHERE id = ? AND verdict = 'pending'""",
            (
                verdict,
                polarity,
                int(replies),
                first_reply_after,
                1 if addressed_back else 0,
                int(baseline),
                float(self._clock()),
                note,
                int(row_id),
            ),
        )

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


# --------------------------------------------------------------------- judging

#: rows pending this long are not judged any more — a restart (or a long stall)
#: makes the window unknowable, and 'unknown' is honest where 'silence' lies.
STALE_AFTER_SECONDS = 30 * 60.0

#: conservative negative vocabulary — the dictionary can only ever return 0 or
#: -1 (positive signals come from structure, i.e. someone answering her).
NEGATIVE_PHRASES = (
    "别刷屏",
    "别说了",
    "闭嘴",
    "别吵",
    "烦不烦",
    "少说两句",
    "安静点",
    "shut up",
    "stop spamming",
)


@dataclass
class Observation:
    """What the monitor saw in the window (and just before it)."""

    replies: int = 0
    first_reply_after: float | None = None
    addressed_back: bool = False
    baseline: int = 0
    first_content: str = ""
    first_is_strongly_targeted: bool = False
    restart_gap: bool = False


@dataclass
class Verdict:
    verdict: str
    polarity: int = 0
    quiet_group: bool = False

    @property
    def score(self) -> float:
        """Engagement score fed to the EMA (milestone 3); see the design v0.9 §6."""
        if self.verdict == "engaged":
            return 1.0
        if self.verdict == "negative":
            return -1.0
        if self.verdict == "silence" and not self.quiet_group:
            return -0.25
        return 0.0  # ambient, quiet silence, unknown


def judge(observation: Observation, *, is_group: bool = True, stale: bool = False) -> Verdict:
    """Turn one window observation into a verdict (design v0.9 §4).

    Order matters: a negative reaction wins over ``addressed_back`` — otherwise
    "有人 @ 她说别刷屏" would be recorded as engagement, which is exactly
    backwards. ``unknown`` is returned instead of silence whenever the window
    cannot be trusted (private chat, a restart inside it, a stale row).
    """
    if not is_group:
        return Verdict("unknown")  # private chat is recorded, never judged
    if stale or observation.restart_gap:
        return Verdict("unknown")

    polarity = _polarity(observation)
    if polarity < 0:
        return Verdict("negative", polarity=-1)
    if observation.addressed_back:
        return Verdict("engaged")
    if observation.replies >= max(1, observation.baseline):
        return Verdict("ambient")
    # quiet means the group was not talking *before* her window either —
    # "fewer replies than baseline" would call a busy group quiet.
    return Verdict("silence", quiet_group=observation.baseline < 1)


def _polarity(observation: Observation) -> int:
    """0 or -1 only, and only when the negative is aimed straight at her."""
    if not observation.first_content:
        return 0
    text = observation.first_content.lower()
    if not any(phrase in text for phrase in NEGATIVE_PHRASES):
        return 0
    # adjacent to her message AND strongly targeted — otherwise it is chatter
    if observation.first_reply_after is None or observation.first_reply_after > 20:
        return 0
    if not observation.first_is_strongly_targeted:
        return 0
    return -1


class ReplyFeedbackSettler:
    """Sweep the pending rows whose window has passed (milestone 2).

    Observation comes from the group monitor (in memory, and it already
    excludes her own messages). A row is settled ``unknown`` whenever the
    window cannot be trusted: private chat, a gap in what the monitor can see
    (restart), or a row so old that judging it would be guesswork.
    """

    def __init__(
        self,
        store: ReplyFeedbackStore,
        monitor: Any = None,
        *,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
        metrics: Any = None,
        on_settled: Any = None,
    ) -> None:
        self._store = store
        self._monitor = monitor
        #: called with (group_id, score) for settled self-initiated turns only
        self._on_settled = on_settled
        self._log = logger or logging.getLogger("CatooBot.Social.Feedback")
        self._clock = clock
        self._metrics = metrics

    async def settle(self, *, limit: int = 200) -> dict[str, int]:
        now = float(self._clock())
        rows = await self._store.pending_before(now, limit=limit)
        counts: dict[str, int] = {}
        for row in rows:
            try:
                observation, stale = self._observe(row, now)
                result = judge(observation, is_group=bool(row["is_group"]), stale=stale)
                await self._store.settle(
                    int(row["id"]),
                    verdict=result.verdict,
                    polarity=result.polarity,
                    replies=observation.replies,
                    first_reply_after=observation.first_reply_after,
                    addressed_back=observation.addressed_back,
                    baseline=observation.baseline,
                    note="quiet_group" if result.quiet_group else "",
                )
                counts[result.verdict] = counts.get(result.verdict, 0) + 1
                self._count("reply_outcomes_settled")
                if self._on_settled is not None and row.get("self_initiated"):
                    group_key = str(row.get("scope_key", ""))
                    if group_key.startswith("group:"):
                        await self._on_settled(group_key, result.score)
            except Exception as exc:  # noqa: BLE001 - the sweep must keep going
                self._count("reply_feedback_failed")
                self._log.warning("[Social.Feedback] settle failed for #%s (%s)", row["id"], exc)
        if counts:
            self._log.info("[Social.Feedback] settled %s", counts)
        return counts

    def _observe(self, row: dict[str, Any], now: float) -> tuple[Observation, bool]:
        """(observation, stale) — never raises, always returns something judgeable."""
        window = float(row.get("window_seconds") or DEFAULT_WINDOW_SECONDS)
        sent_at = float(row.get("sent_at") or 0.0)
        stale = (now - sent_at) > STALE_AFTER_SECONDS + window
        if not row.get("is_group") or self._monitor is None:
            return Observation(), stale
        group_id = str(row.get("scope_key", "")).split(":", 1)[-1]
        recent = list(self._monitor.recent(group_id))
        if not recent:
            # the monitor has no memory of this group: restart or a quiet leak
            return Observation(restart_gap=True), stale
        bot_message = self._monitor.last_bot_message(group_id)
        bot_id = str(getattr(bot_message, "message_id", "") or "")
        external = [m for m in recent if getattr(m, "external", False)]
        in_window = [m for m in external if sent_at <= float(m.timestamp) <= sent_at + window]
        before = [m for m in external if sent_at - window <= float(m.timestamp) < sent_at]
        first = in_window[0] if in_window else None
        targeted = bool(first is not None and first.reply_to and first.reply_to == bot_id)
        return (
            Observation(
                replies=len(in_window),
                first_reply_after=(float(first.timestamp) - sent_at) if first is not None else None,
                addressed_back=any(
                    m.reply_to and (not bot_id or str(m.reply_to) == bot_id) for m in in_window
                ),
                baseline=len(before),
                first_content=str(getattr(first, "content", "") or ""),
                first_is_strongly_targeted=targeted,
            ),
            stale,
        )

    def _count(self, key: str, amount: int = 1) -> None:
        if self._metrics is not None:
            self._metrics.inc(key, amount)
