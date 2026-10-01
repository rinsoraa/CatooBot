"""Engagement memory: how well her self-initiated turns land (Task 20, milestone 3).

Per group, an exponential moving average of the engagement scores from turns
**she opened herself** (being @-ed nearly always ends in an answer and would
push the average above 1 — see the design v0.9 §6). Two deliberate properties:

* **time-based decay** — ``0.5 ** (Δt / half_life)``. Decaying per sample would
  make the half-life depend on how often she speaks, and that is exactly the
  quantity this loop controls: a feedback ring.
* **sample dead zone** — below ``min_samples`` settled turns the factor is
  exactly 1.0, so a brand-new group behaves as if the loop did not exist.

The state is persisted (``bot_state``) and re-decayed on load, because a
restart that zeroed it would throw away everything it learned.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

#: engagement half-life: a week of silence roughly halves the memory
HALF_LIFE_SECONDS = 7 * 86400.0

#: below this many settled self-initiated turns the factor stays exactly 1.0
MIN_SAMPLES = 8

#: the soft multiplier applied to the participation credit *rate* (design v0.9 §6)
FACTOR_MIN = 0.9
FACTOR_MAX = 1.1

#: how strongly a non-neutral ema moves the factor (ema ±1 → ±0.1)
_FACTOR_SLOPE = 0.1


@dataclass
class _GroupEngagement:
    """Time-decayed weighted evidence (mean = value / weight)."""

    value: float = 0.0
    weight: float = 0.0
    samples: int = 0
    updated_at: float = 0.0

    @property
    def mean(self) -> float:
        return self.value / self.weight if self.weight > 0 else 0.0


@dataclass
class SocialEngagement:
    """Per-group engagement EMAs (in-memory + persistable snapshot)."""

    half_life: float = HALF_LIFE_SECONDS
    min_samples: int = MIN_SAMPLES
    clock: Any = time.time
    logger: logging.Logger | None = None
    _groups: dict[str, _GroupEngagement] = field(default_factory=dict)

    # ---------------------------------------------------------------- update

    def note(self, group_id: str, score: float, *, now: float | None = None) -> float:
        """Fold one settled turn in; returns the group's effective average.

        Both the accumulated evidence *and* its weight decay with time before
        the new sample lands (a leaky weighted mean): old opinions lose their
        say at a fixed half-life, and one sample at Δt=0 still counts fully.
        """
        key = str(group_id)
        moment = float(now if now is not None else self.clock())
        state = self._decayed(key, moment)
        state.value += max(-1.0, min(1.0, float(score)))
        state.weight += 1.0
        state.samples += 1
        state.updated_at = moment
        self._groups[key] = state
        return self.ema(key, now=moment)

    def _decayed(self, key: str, now: float) -> _GroupEngagement:
        state = self._groups.get(key)
        if state is None:
            return _GroupEngagement(updated_at=now)
        factor = self._decay(now - state.updated_at)
        state.value *= factor
        state.weight *= factor
        state.updated_at = now
        return state

    def _decay(self, elapsed: float) -> float:
        if elapsed <= 0 or self.half_life <= 0:
            return 1.0
        return float(0.5 ** (elapsed / self.half_life))

    # ------------------------------------------------------------------ read

    def ema(self, group_id: str, *, now: float | None = None) -> float:
        """Effective average *now*: the mean fades with time since her last turn."""
        state = self._groups.get(str(group_id))
        if state is None or state.weight <= 0:
            return 0.0
        moment = float(now if now is not None else self.clock())
        return state.mean * self._decay(moment - state.updated_at)

    def samples(self, group_id: str) -> int:
        state = self._groups.get(str(group_id))
        return state.samples if state is not None else 0

    def factor(self, group_id: str, *, now: float | None = None) -> float:
        """Soft multiplier for the participation credit (1.0 inside the dead zone)."""
        key = str(group_id)
        state = self._groups.get(key)
        if state is None or state.samples < self.min_samples:
            return 1.0
        value = self.ema(key, now=now)
        return max(FACTOR_MIN, min(FACTOR_MAX, 1.0 + value * _FACTOR_SLOPE))

    # ----------------------------------------------------------- persistence

    def snapshot(self) -> dict[str, Any]:
        return {
            "half_life": self.half_life,
            "min_samples": self.min_samples,
            "groups": {
                key: {
                    "value": state.value,
                    "weight": state.weight,
                    "samples": state.samples,
                    "updated_at": state.updated_at,
                }
                for key, state in self._groups.items()
            },
        }

    def load(self, data: Any) -> None:
        """Restore a snapshot; decay is applied lazily on the next read."""
        if not isinstance(data, dict):
            return
        groups = data.get("groups")
        if not isinstance(groups, dict):
            return
        for key, payload in groups.items():
            if not isinstance(payload, dict):
                continue
            self._groups[str(key)] = _GroupEngagement(
                value=float(payload.get("value", 0.0) or 0.0),
                weight=float(payload.get("weight", 0.0) or 0.0),
                samples=int(payload.get("samples", 0) or 0),
                updated_at=float(payload.get("updated_at", 0.0) or 0.0),
            )

    def stats(self) -> dict[str, Any]:
        """Small summary for the WebUI card (milestone 4)."""
        return {
            "groups": len(self._groups),
            "half_life_days": round(self.half_life / 86400.0, 1),
            "min_samples": self.min_samples,
            "per_group": {
                key: {
                    "ema": round(self.ema(key), 4),
                    "samples": state.samples,
                    "factor": round(self.factor(key), 3),
                }
                for key, state in self._groups.items()
            },
        }
