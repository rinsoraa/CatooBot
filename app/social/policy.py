"""Participation policy: hard limits and the final gate (v0.9 §67/§68/§121).

Hard rules are evaluated in code, never by the model. Soft cognition decides
*if it wants to speak*; this layer decides *if it is allowed to*. The final
gate re-checks cooldown / daily budget / group state right before sending, so a
decision that went stale while the model was thinking gets cancelled (§121).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.config.settings import SocialConfig

_DATE_FMT = "%Y-%m-%d"


class ParticipationPolicy:
    def __init__(
        self,
        *,
        config: SocialConfig,
        presence: Any = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
        engagement: Any = None,
    ) -> None:
        self._config = config
        self._presence = presence
        #: optional engagement memory (Task 20): a *soft* 0.9-1.1 multiplier on
        #: the credit rate. The hard gate (cooldown/daily/enabled) never reads it.
        self._engagement = engagement
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock
        # per-group counters (in-memory; the daily budget is generous by design)
        self._last_sent: dict[str, float] = {}
        self._daily: dict[str, tuple[str, int]] = {}  # group -> (day_key, count)
        #: deterministic participation credit (rate → chime-in, never a dice)
        self._credit: dict[str, float] = {}

    # ------------------------------------------------------------- hard gate

    def block_reason(
        self,
        group_id: str,
        *,
        group_enabled: bool,
        text: str,
        min_length: int,
        addressed_other: bool,
        hard_block: str | None,
    ) -> str:
        """First gate for a non-@, non-reply message. Returns a reason or ""."""
        if not self._config.enabled:
            return "social_disabled"
        if not group_enabled:
            return "group_disabled"
        if hard_block:
            return hard_block  # sleeping / dnd
        if len((text or "").strip()) < min_length:
            return "too_short"
        if addressed_other:
            return "addressed_to_someone_else"
        now = self._clock()
        last = self._last_sent.get(str(group_id))
        if last is not None and now - last < self._config.participation.cooldown_seconds:
            return "cooldown"
        day_key = time.strftime(_DATE_FMT, time.localtime(now))
        day, count = self._daily.get(str(group_id), (day_key, 0))
        if day != day_key:
            count = 0
        limit = self._config.participation.daily_limit
        if limit and count >= limit:
            return "daily_limit"
        return ""

    # ------------------------------------------------------------ credit

    def credit_participation(self, group_id: str, rate: float) -> bool:
        """Accumulate participation credit; True when a chime-in is earned.

        ``rate`` is the configured participation probability (expected
        participations per eligible message). Deterministic: no ``random()``.
        Credit is capped at 1.0 so a burst of messages can't stack up replies.
        """
        if rate <= 0.0:
            return False
        key = str(group_id)
        factor = self._engagement.factor(key) if self._engagement is not None else 1.0
        credit = min(1.0, self._credit.get(key, 0.0) + rate * factor)
        if credit >= 1.0:
            self._credit[key] = credit - 1.0
            return True
        self._credit[key] = credit
        return False

    # --------------------------------------------------------------- records

    def record_send(self, group_id: str) -> None:
        now = self._clock()
        self._last_sent[str(group_id)] = now
        day_key = time.strftime(_DATE_FMT, time.localtime(now))
        day, count = self._daily.get(str(group_id), (day_key, 0))
        if day != day_key:
            day, count = day_key, 0
        self._daily[str(group_id)] = (day_key, count + 1)

    # ------------------------------------------------------------------ view

    def snapshot(self, group_id: str) -> dict[str, Any]:
        now = self._clock()
        day_key = time.strftime(_DATE_FMT, time.localtime(now))
        day, count = self._daily.get(str(group_id), (day_key, 0))
        return {
            "cooldown_seconds": self._config.participation.cooldown_seconds,
            "daily_limit": self._config.participation.daily_limit,
            "daily_count": count,
            "last_sent": self._last_sent.get(str(group_id)),
            "credit": round(self._credit.get(str(group_id), 0.0), 4),
        }
