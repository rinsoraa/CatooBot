"""Mode runtime (v2.0 §78-§83) — definition-driven (§18/§19).

Modes are *data* (:class:`~app.sandbox.definition.ModeDefinition` parsed from
the bible), never a Python enum. They derive from real sandbox state
(location/time/action/social) — never guessed by a model — and they stack
(primary + secondary, §19). They change *expression and preference*, never
core identity.
"""

from __future__ import annotations

from typing import Any

from app.sandbox.models import ActionDefinition, ModeState


class ModeRuntime:
    """Derives the active mode set from state (§80), from bible definitions."""

    def __init__(self, *, clock: Any, definitions: list[dict[str, Any]] | None = None) -> None:
        self._clock = clock
        self._active: dict[str, ModeState] = {}
        #: mode id → definition payload (id/name/trigger_kind/time_window/priority/…)
        self._defs: dict[str, dict[str, Any]] = {
            str(defn.get("id")): defn for defn in (definitions or []) if defn.get("id")
        }

    # ------------------------------------------------------------------ derive

    def derive(
        self,
        *,
        space_id: str,
        hour: int,
        definition: ActionDefinition | None,
        social_active: bool,
        is_home: bool,
    ) -> list[str]:
        """Evaluate every mode's trigger against the current state (§18)."""
        modes: list[str] = []
        action_mode_tags = set(definition.modes) if definition is not None else set()
        for mode_id, defn in self._defs.items():
            kind = str(defn.get("trigger_kind", ""))
            window = defn.get("time_window")
            if kind == "home":
                if is_home:
                    modes.append(mode_id)
            elif kind == "outdoor":
                if not is_home:
                    modes.append(mode_id)
            elif kind == "time":
                if self._in_window(window, hour):
                    modes.append(mode_id)
            elif kind == "action":
                if mode_id in action_mode_tags:
                    modes.append(mode_id)
            elif kind == "social":
                if (mode_id in action_mode_tags and social_active) or (social_active and is_home):
                    modes.append(mode_id)
            elif mode_id in action_mode_tags:
                modes.append(mode_id)
        # A live exchange puts the social face on regardless of activity (§79).
        social_ids = [
            mode_id for mode_id, defn in self._defs.items() if defn.get("trigger_kind") == "social"
        ]
        if social_active and is_home and not any(m in modes for m in social_ids):
            modes.extend(social_ids[:1])
        return list(dict.fromkeys(modes))

    @staticmethod
    def _in_window(window: Any, hour: int) -> bool:
        """Overnight-safe hour-window check for time-triggered modes."""
        if not window:
            return False
        start, end = int(window[0]), int(window[1])
        if start <= end:
            return start <= hour < end
        return hour >= start or hour < end  # window crosses midnight

    # ------------------------------------------------------------------ update

    def update(self, modes: list[str]) -> list[str]:
        """Record entered_at; returns the ids that were newly entered."""
        now = float(self._clock())
        entered: list[str] = []
        for mode_id in modes:
            if mode_id not in self._active:
                self._active[mode_id] = ModeState(
                    id=mode_id,
                    priority=self._priority_of(mode_id),
                    entered_at=now,
                    trigger="derived",
                )
                entered.append(mode_id)
        for mode_id in list(self._active):
            if mode_id not in modes:
                del self._active[mode_id]
        return entered

    def _priority_of(self, mode_id: str) -> int:
        defn = self._defs.get(mode_id)
        if defn is not None:
            return int(defn.get("priority", 60))
        return 60

    def active(self) -> list[ModeState]:
        return sorted(self._active.values(), key=lambda m: m.priority)

    def ids(self) -> list[str]:
        return [mode.id for mode in self.active()]

    def primary(self) -> str:
        ordered = self.active()
        default = next(
            (mid for mid, defn in self._defs.items() if defn.get("is_default")),
            next(iter(self._defs), "home"),
        )
        return ordered[0].id if ordered else default

    # --------------------------------------------------------------- speech

    def speech_policy(self) -> dict[str, Any]:
        """Merged policy: the most specific mode wins on conflicts (§83)."""
        policy: dict[str, Any] = {}
        for mode in self.active():  # priority ascending = broad → specific
            defn = self._defs.get(mode.id, {})
            mode_policy = {
                "style": defn.get("style", ""),
                "tone": defn.get("tone", ""),
                "length": defn.get("length", ""),
            }
            phrases = defn.get("phrases") or []
            if phrases:
                quoted = "、".join(f"“{phrase}”" for phrase in phrases[:6])
                mode_policy["style"] = f"{mode_policy['style']}（可用：{quoted}）".strip("（）：")
            policy.update({k: v for k, v in mode_policy.items() if v})
        return policy

    def prompt_line(self) -> str:
        ids = self.ids()
        if not ids:
            return ""
        policy = self.speech_policy()
        style = str(policy.get("style", "") or "")
        labels = {mode_id: str(defn.get("name") or mode_id) for mode_id, defn in self._defs.items()}
        shown = " + ".join(labels.get(mode, mode) for mode in ids)
        line = f"你现在的状态：{shown}"
        if style:
            line += f"（说话方式：{style}）"
        return line

    def snapshot(self) -> list[dict[str, Any]]:
        return [mode.model_dump(mode="json") for mode in self.active()]

    def restore(self, data: list[dict[str, Any]]) -> None:
        self._active = {}
        for item in data or []:
            mode = ModeState.model_validate(item)
            self._active[mode.id] = mode
