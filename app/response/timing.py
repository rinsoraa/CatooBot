"""Reply timing: how long the character 'takes' before answering.

The delay is a **band**, not a constant (v0.4 规格，原文缺失（§7）): reading time + typing time,
modulated by character state, time of day, relationship and conversation
rhythm, then jittered. Deterministic under an injected RNG for tests.
"""

from __future__ import annotations

import logging
import random

from app.behavior.models import TimeContext
from app.behavior.presence import PresenceResolver
from app.character.relationship import Relationship
from app.character.state import CharacterState
from app.config.settings import BehaviorReplyTimingConfig

# Activities where the character is clearly mid-something.
BUSY_ACTIVITIES = {"gaming", "working", "studying", "reading", "eating"}


class ReplyTiming:
    def __init__(
        self,
        config: BehaviorReplyTimingConfig,
        presence: PresenceResolver,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._config = config
        self._presence = presence
        self._log = logger or logging.getLogger("CatooBot.Response")
        self._rng = rng or random.Random()

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def compute(
        self,
        *,
        reply_text: str,
        state: CharacterState,
        relationship: Relationship | None = None,
        time_context: TimeContext | None = None,
        seconds_since_last_exchange: float | None = None,
        extra_factor: float = 1.0,
    ) -> float:
        """Delay in seconds, always inside [min_delay, max_delay]."""
        if not self._config.enabled:
            return 0.0

        ctx = time_context or self._presence.time_context()
        cfg = self._config
        delay = cfg.reading_floor + min(len(reply_text), 200) * cfg.per_char_delay

        if state.activity in BUSY_ACTIVITIES:
            delay *= cfg.busy_factor
        if ctx.is_sleeping:
            delay *= cfg.sleeping_factor
        if ctx.period in ("night", "late_night"):
            delay *= cfg.night_factor
        if relationship is not None and relationship.stage in ("close", "very_close"):
            delay *= cfg.close_relationship_factor
        if (
            seconds_since_last_exchange is not None
            and seconds_since_last_exchange <= cfg.fast_exchange_window
        ):
            # A lively back-and-forth feels faster.
            delay *= cfg.fast_exchange_factor
        if extra_factor != 1.0:
            delay *= extra_factor

        if cfg.jitter > 0:
            delay *= 1.0 + self._rng.uniform(-cfg.jitter, cfg.jitter)

        clamped = max(cfg.min_delay, min(cfg.max_delay, delay))
        self._log.debug(
            "[Response] Delay=%.2fs (raw=%.2f activity=%s period=%s sleeping=%s)",
            clamped,
            delay,
            state.activity or "-",
            ctx.period,
            ctx.is_sleeping,
        )
        return clamped
