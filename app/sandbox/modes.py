"""Mode runtime (v2.0 §78-§83): HOME / OUTDOOR / GAMING / ONLINE_SOCIAL / DEEP_NIGHT.

Modes are derived from real sandbox state (location/time/action/social) — never
guessed by a model. They change *expression and preference*, never core identity.
"""

from __future__ import annotations

from typing import Any

from app.sandbox.models import ActionDefinition, ModeState

#: mode id -> speech policy (§82)
SPEECH_POLICY: dict[str, dict[str, Any]] = {
    "home": {
        "style": "懒散、拖长音、自言自语式；常用“啊——”“好麻烦”“不想动”“再一局”",
        "length": "短，可以自言自语", "tone": "casual", "emoji": "偶尔",
    },
    "outdoor": {
        "style": "简洁、温和、标准敬语；不拖长音，礼貌但疏离",
        "length": "很短", "tone": "polite", "emoji": "几乎不用",
    },
    "gaming": {
        "style": "简短、利落、术语多；不撒娇，像冷静的玩家",
        "length": "很短", "tone": "focused", "emoji": "很少",
    },
    "online_social": {
        "style": "轻松、随意、话多、有梗；句尾可用“www”“草”“笑死”“确实”“有一说一”",
        "length": "灵活", "tone": "chatty", "emoji": "可以接梗发表情",
    },
    "deep_night": {
        "style": "低沉、缓慢、少话；偶尔哲思式短句，很快被“好饿”“算了”打断",
        "length": "短", "tone": "quiet", "emoji": "几乎不用",
    },
}

MODE_PRIORITY = {
    "deep_night": 30,
    "gaming": 40,
    "online_social": 50,
    "outdoor": 60,
    "home": 10,
}


class ModeRuntime:
    """Derives the active mode set from state (§80)."""

    def __init__(self, *, clock: Any) -> None:
        self._clock = clock
        self._active: dict[str, ModeState] = {}

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
        modes: list[str] = []
        if is_home:
            modes.append("home")
        else:
            modes.append("outdoor")
        if definition is not None and "gaming" in definition.modes:
            modes.append("gaming")
        if (
            definition is not None and "online_social" in definition.modes and social_active
        ):
            modes.append("online_social")
        # A live exchange (someone is talking to her right now) puts the
        # online-social face on regardless of what she was doing (§2.5 overlap).
        if social_active and "online_social" not in modes and is_home:
            modes.append("online_social")
        if 2 <= hour < 5 and "gaming" not in modes and "online_social" not in modes:
            modes.append("deep_night")
        return list(dict.fromkeys(modes))

    # ------------------------------------------------------------------ update

    def update(self, modes: list[str]) -> list[str]:
        """Record entered_at; returns the ids that were newly entered."""
        now = float(self._clock())
        entered: list[str] = []
        for mode_id in modes:
            if mode_id not in self._active:
                self._active[mode_id] = ModeState(
                    id=mode_id, priority=MODE_PRIORITY.get(mode_id, 0),
                    entered_at=now, trigger="derived",
                )
                entered.append(mode_id)
        for mode_id in list(self._active):
            if mode_id not in modes:
                del self._active[mode_id]
        return entered

    def active(self) -> list[ModeState]:
        return sorted(self._active.values(), key=lambda m: m.priority)

    def ids(self) -> list[str]:
        return [mode.id for mode in self.active()]

    def primary(self) -> str:
        ordered = self.active()
        return ordered[0].id if ordered else "home"

    # --------------------------------------------------------------- speech

    def speech_policy(self) -> dict[str, Any]:
        """Merged policy: the most specific mode wins on conflicts (§83)."""
        policy: dict[str, Any] = {}
        for mode in self.active():  # priority ascending = broad → specific
            policy.update(SPEECH_POLICY.get(mode.id, {}))
        return policy

    def prompt_line(self) -> str:
        ids = self.ids()
        if not ids:
            return ""
        policy = self.speech_policy()
        style = str(policy.get("style", "") or "")
        labels = {
            "home": "宅家", "outdoor": "外出", "gaming": "游戏",
            "online_social": "网络社交", "deep_night": "深夜",
        }
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
