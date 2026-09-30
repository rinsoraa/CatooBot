"""Activity Planner + Continuation Evaluator + Bounce Guard (v1.0 §21-§24/§30/§75).

* ``ActivityPlanner`` produces the *next* episode (a `WorldActivity`) only when
  a transition is genuinely needed — never per tick (§29/§40).
* ``ActivityContinuationEvaluator`` turns the *continuation* decision into a
  structured, rule-driven result: continue / extend / transition. Momentum is a
  comparison input, not a die (§23/§24).
* ``ActivityBounceGuard`` stops A→B→A→B oscillation (§74/§75).
"""

from __future__ import annotations

import logging
from collections import deque
from typing import Any

from app.world.profiles import profile_for


class ActivityBounceGuard:
    """Remembers recent episode keys so the planner avoids ping-ponging."""

    def __init__(self, *, maxlen: int = 6) -> None:
        self._recent: deque[str] = deque(maxlen=maxlen)

    def note(self, key: str) -> None:
        self._recent.append(key)

    def would_bounce(self, key: str) -> bool:
        """True when choosing ``key`` next recreates an A→B→A→B oscillation."""
        if len(self._recent) < 3:
            return False
        tail = list(self._recent)[-3:]
        # recent looks like [X, Y, X] and we are about to pick Y again.
        return tail[0] == tail[2] and tail[1] != tail[0] and key == tail[1]

    def recent_keys(self, limit: int = 4) -> list[str]:
        return list(self._recent)[-limit:]

    def clear(self) -> None:
        self._recent.clear()


class ActivityContinuationEvaluator:
    """Rule-based continue / extend / transition decision (no randomness)."""

    def decide(
        self,
        *,
        episode: Any,
        profile: Any,
        energy: float,
        focus: str,
        sleeping: bool,
        now: float,
    ) -> dict[str, Any]:
        elapsed = episode.elapsed_minutes(now)

        # Hard constraints first (rule > model, spec §121/§24).
        if sleeping:
            return {
                "decision": "transition", "reason_code": "sleep_transition",
                "extension_minutes": 0, "next_hint": "sleep",
            }
        if elapsed >= profile.max_minutes:
            return {
                "decision": "transition", "reason_code": "natural_completion",
                "extension_minutes": 0, "next_hint": "",
            }
        if energy < 0.25 and profile.energy_delta_per_hour < 0:
            return {
                "decision": "transition", "reason_code": "energy_low",
                "extension_minutes": 0, "next_hint": "rest",
            }
        if elapsed < profile.min_minutes:
            return {"decision": "continue", "reason_code": "within_minimum",
                    "extension_minutes": 0, "next_hint": ""}

        # At/after typical duration: keep going only if it still makes sense.
        engaged = profile.momentum >= 0.6 and energy >= 0.4
        focus_fits = (not focus) or any(tag in (focus or "") for tag in profile.tags)
        if engaged and focus_fits:
            extension = min(profile.max_minutes - elapsed, 45)
            if extension > 0:
                return {
                    "decision": "extend", "reason_code": "high_focus",
                    "extension_minutes": extension, "next_hint": "",
                }
        return {
            "decision": "transition", "reason_code": "natural_completion",
            "extension_minutes": 0, "next_hint": "",
        }


class ActivityPlanner:
    """Chooses the next activity when a transition is required (spec §30)."""

    def __init__(
        self,
        *,
        routine: Any = None,
        profiles_overrides: dict[str, dict] | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._routine = routine
        self._overrides = profiles_overrides or {}
        self._log = logger or logging.getLogger("CatooBot.World")
        self.bounce = ActivityBounceGuard()

    def profile(self, key: str) -> Any:
        return profile_for(key, overrides=self._overrides)

    def plan_next(
        self,
        *,
        period: str,
        sleeping: bool,
        energy: float,
        current_key: str,
        current_goal: str = "",
    ) -> Any:
        """Pick the next `WorldActivity` from the routine's period candidates."""
        from app.world.routine import activity_for_label

        if sleeping:
            labels = self._routine.labels_for(period) if self._routine else []
            preferred = next((label for label in labels if "睡" in label), "睡觉")
            activity = activity_for_label(preferred)
            self.bounce.note(activity.key)
            return activity

        labels = self._routine.labels_for(period) if self._routine else []
        candidates = [activity_for_label(label) for label in labels] if labels else []
        if not candidates:
            idle = activity_for_label("发呆")
            self.bounce.note(idle.key)
            return idle

        # Prefer a real change of scene; avoid bouncing and repeating the current.
        filtered = [c for c in candidates if c.key != current_key]
        if not filtered:
            filtered = list(candidates)
        filtered = [c for c in filtered if not self.bounce.would_bounce(c.key)]
        if not filtered:
            filtered = [c for c in candidates if c.key != current_key] or list(candidates)

        # Goal relevance: prefer an activity whose tags match the current goal.
        if current_goal:
            goal_text = current_goal or ""
            goal_preferred = [
                c for c in filtered
                if any(tag in goal_text or goal_text in tag for tag in c.tags)
            ]
            if goal_preferred:
                filtered = goal_preferred

        # Low energy -> prefer a restful / light activity.
        if energy < 0.3:
            light = [
                c for c in filtered
                if "sleep" in c.tags or "slow" in c.tags or "ambient" in c.tags
            ]
            if light:
                filtered = light

        chosen = filtered[0]
        self.bounce.note(chosen.key)
        return chosen
