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
from app.social.models import ParticipationDecision

_DATE_FMT = "%Y-%m-%d"


class ParticipationPolicy:
    def __init__(
        self,
        *,
        config: SocialConfig,
        presence: Any = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self._config = config
        self._presence = presence
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock
        # per-group counters (in-memory; the daily budget is generous by design)
        self._last_sent: dict[str, float] = {}
        self._daily: dict[str, tuple[str, int]] = {}  # group -> (day_key, count)

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

    def _within_cooldown(self, group_id: str) -> bool:
        last = self._last_sent.get(str(group_id))
        if last is None:
            return False
        return self._clock() - last < self._config.participation.cooldown_seconds

    # --------------------------------------------------------------- records

    def record_send(self, group_id: str) -> None:
        now = self._clock()
        self._last_sent[str(group_id)] = now
        day_key = time.strftime(_DATE_FMT, time.localtime(now))
        day, count = self._daily.get(str(group_id), (day_key, 0))
        if day != day_key:
            day, count = day_key, 0
        self._daily[str(group_id)] = (day_key, count + 1)

    # ------------------------------------------------------------- final gate

    async def final_gate(
        self,
        decision: ParticipationDecision,
        *,
        group_id: str,
        group_enabled: bool,
        thread_alive: bool,
        hard_block: str | None,
    ) -> ParticipationDecision:
        """Re-check a ``reply`` decision immediately before sending (§121).

        Returns a possibly-demoted decision: if the situation changed while the
        model was thinking (topic moved on, group disabled, cooldown hit), the
        reply is cancelled instead of firing into a stale conversation.
        """
        if decision.decision != "reply":
            return decision
        if not self._config.enabled:
            return ParticipationDecision(decision="observe", reason_code="social_disabled")
        if not group_enabled:
            return ParticipationDecision(decision="ignore", reason_code="group_disabled")
        if hard_block:
            return ParticipationDecision(decision="observe", reason_code=hard_block)
        if self._within_cooldown(group_id):
            return ParticipationDecision(decision="ignore", reason_code="cooldown")
        if not thread_alive and decision.reason_code in ("direct_follow_up", "topic_continuation"):
            # The exchange the reply was answering has expired / moved on.
            return ParticipationDecision(decision="defer", reason_code="poor_timing")
        return decision

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
        }
