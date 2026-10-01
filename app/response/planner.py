"""Response planner: turn one AI reply into a delivery plan.

Responsibilities are deliberately narrow (spec §11/§46): *behaviour* (delay +
bubble count) is decided here; the *language* was decided by the LLM. The
planner never rewrites the character's words.
"""

from __future__ import annotations

import logging
import random
from typing import TYPE_CHECKING

from app.behavior.models import ResponsePlan
from app.config.settings import BehaviorChunkingConfig
from app.response.splitter import MessageChunker
from app.response.timing import ReplyTiming

if TYPE_CHECKING:
    from app.character.relationship import Relationship
    from app.character.state import CharacterState


class CharacterResponsePlanner:
    def __init__(
        self,
        timing: ReplyTiming,
        chunking: BehaviorChunkingConfig,
        logger: logging.Logger | None = None,
        rng: random.Random | None = None,
    ) -> None:
        self._timing = timing
        self._chunker = MessageChunker(chunking, rng=rng or random.Random())
        self._chunk_cfg = chunking
        self._log = logger or logging.getLogger("CatooBot.Response")
        self._rng = rng or random.Random()

    def plan_reply(
        self,
        text: str,
        *,
        state: CharacterState,
        relationship: Relationship | None = None,
        time_context=None,
        seconds_since_last_exchange: float | None = None,
        force_single_message: bool = False,
    ) -> ResponsePlan:
        chunks = self._chunker.plan(text, force_single=force_single_message)
        if not chunks:
            return ResponsePlan(reason="empty")

        joined = "\n".join(chunks)
        delay = self._timing.compute(
            reply_text=joined,
            state=state,
            relationship=relationship,
            time_context=time_context,
            seconds_since_last_exchange=seconds_since_last_exchange,
        )
        inter_delays = self._inter_chunk_delays(len(chunks) - 1)
        if len(chunks) > 1:
            self._log.info("[Response] Planned %d message chunks", len(chunks))
        self._log.debug("[Response] Delay=%.2fs", delay)

        return ResponsePlan(
            chunks=chunks,
            delay=delay,
            inter_chunk_delays=inter_delays,
            reason="planned",
        )

    def _inter_chunk_delays(self, count: int) -> list[float]:
        if count <= 0:
            return []
        cfg = self._chunk_cfg
        low, high = cfg.inter_chunk_delay_min, cfg.inter_chunk_delay_max
        if high < low:
            low, high = high, low
        return [self._rng.uniform(low, high) if high > low else low for _ in range(count)]
