"""Social attention + fatigue (v0.9 §62-§66/§101).

A narrative-modelling layer for how engaged the character currently is with one
group. It has *momentum* (a topic keeps her engaged for a while) and *decay*
(engagement fades), and a *fatigue* term that slowly recovers. All three only
soften the decision — a direct @ still wins, and a truly relevant topic still
gets through (v0.9 §101/§102).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.social.models import SocialAttentionState

_MENTION_BOOST = 0.45
_THREAD_BOOST = 0.35
_TOPIC_BOOST = 0.20
_DECAY_PER_SECOND = 0.0004  # ~2.4%/min
_FATIGUE_PER_REPLY = 0.18
_FATIGUE_DECAY = 0.00005  # slow recovery


class SocialAttention:
    def __init__(self, *, logger: logging.Logger | None = None, clock: Any = time.time) -> None:
        self._log = logger or logging.getLogger("CatooBot.Social")
        self._clock = clock
        self._states: dict[str, SocialAttentionState] = {}

    def get(self, group_id: str) -> SocialAttentionState:
        state = self._states.get(str(group_id))
        if state is None:
            state = SocialAttentionState(last_updated=self._clock())
            self._states[str(group_id)] = state
        self._decay(state)
        return state

    def _decay(self, state: SocialAttentionState) -> None:
        now = self._clock()
        elapsed = max(0.0, now - state.last_updated)
        if elapsed <= 0:
            return
        state.attention_level = max(0.0, state.attention_level - _DECAY_PER_SECOND * elapsed)
        state.momentum = max(0.0, state.momentum - _DECAY_PER_SECOND * elapsed * 1.5)
        state.fatigue = max(0.0, state.fatigue - _FATIGUE_DECAY * elapsed)
        state.last_updated = now

    def _commit(self, state: SocialAttentionState, **changes: Any) -> None:
        state.last_updated = self._clock()
        for key, value in changes.items():
            setattr(state, key, value)

    def note_mention(self, group_id: str, topic: str = "") -> None:
        state = self.get(str(group_id))
        level = min(1.0, state.attention_level + _MENTION_BOOST)
        self._commit(
            state,
            attention_level=level,
            momentum=min(1.0, state.momentum + 0.3),
            current_topic=topic or state.current_topic,
        )

    def note_thread(self, group_id: str, topic: str = "") -> None:
        state = self.get(str(group_id))
        level = min(1.0, state.attention_level + _THREAD_BOOST)
        self._commit(
            state,
            attention_level=level,
            momentum=min(1.0, state.momentum + 0.25),
            current_topic=topic or state.current_topic,
        )

    def note_interesting_topic(self, group_id: str, topic: str) -> None:
        state = self.get(str(group_id))
        level = min(1.0, state.attention_level + _TOPIC_BOOST)
        self._commit(
            state,
            attention_level=level,
            current_topic=topic,
            momentum=min(1.0, state.momentum + 0.2),
        )

    def note_reply_outcome(self, group_id: str, score: float) -> None:
        """Fold an engagement score into momentum (Task 20 v0.9 §6).

        The step is deliberately small (``score × 0.1``): momentum lives in
        [0, 1] and every other ``note_*`` adds 0.2-0.3, so feeding ±1 would
        saturate it immediately. A negative score can only cancel existing
        momentum — it never drives it below zero (unchanged semantics).
        """
        state = self.get(group_id)
        delta = max(-1.0, min(1.0, float(score))) * 0.1
        self._commit(state, momentum=max(0.0, min(1.0, state.momentum + delta)))

    def note_reply(self, group_id: str) -> None:
        """Each reply tires her a little; it recovers on its own (v0.9 §66)."""
        state = self.get(str(group_id))
        self._commit(state, fatigue=min(1.0, state.fatigue + _FATIGUE_PER_REPLY))

    # ------------------------------------------------------------------ view

    def snapshot(self, group_id: str) -> dict[str, Any]:
        return self.get(str(group_id)).as_dict()

    def groups(self) -> list[str]:
        return sorted(self._states)
