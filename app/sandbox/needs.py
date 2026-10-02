"""NeedSystem (v2.0 §34-§37): why the character wants to do things.

Needs grow deterministically with time/actions/environment — never random
per-tick noise (§35). They create *tendencies* (weights), not commands;
only a critical level forces priority (§37).
"""

from __future__ import annotations

from typing import Any

from app.sandbox.models import ActionDefinition, NeedState


class NeedSystem:
    def __init__(
        self,
        needs: dict[str, NeedState],
        *,
        clock: Any,
        labels: dict[str, str] | None = None,
    ) -> None:
        self._needs = needs
        self._clock = clock
        self._last_ts = float(clock())
        #: seed-injected character labels override the generic defaults
        self._labels = dict(labels or {})

    def all(self) -> dict[str, NeedState]:
        return self._needs

    def get(self, key: str) -> NeedState | None:
        return self._needs.get(key)

    def level(self, key: str) -> float:
        need = self._needs.get(key)
        return need.level if need else 0.0

    # ------------------------------------------------------------------ time

    def advance(self, minutes: float, *, rest: float = 0.0) -> None:
        """Linear growth (deterministic); resting slows pressure (§182).

        ``rest`` 0.0 = awake, 0.5 = napping, 1.0 = asleep (sleepiness/energy
        frozen; other needs still drift slowly).
        """
        if minutes <= 0:
            return
        hours = minutes / 60.0
        for need in self._needs.values():
            if need.key in ("sleepiness", "energy"):
                factor = 1.0 - rest
            else:
                factor = 1.0 - 0.65 * rest
            need.level = min(1.0, need.level + need.growth_per_hour * hours * factor)
        self._last_ts += minutes * 60.0

    def relieve(self, effects: dict[str, float]) -> None:
        """Action completion: reduce pressure proportionally."""
        for key, amount in effects.items():
            need = self._needs.get(key)
            if need is None:
                continue
            need.level = max(0.0, need.level * (1.0 - amount))

    def add(self, key: str, amount: float) -> None:
        need = self._needs.get(key)
        if need is None:
            return
        need.level = max(0.0, min(1.0, need.level + amount))

    # ------------------------------------------------------------- readings

    def critical(self) -> list[NeedState]:
        return [need for need in self._needs.values() if need.band() == "critical"]

    def pressing(self) -> list[NeedState]:
        return [need for need in self._needs.values() if need.band() in ("strong", "critical")]

    def pressure(self, key: str) -> float:
        """0..1 urgency used as a decision weight (§36 bands)."""
        need = self._needs.get(key)
        if need is None:
            return 0.0
        if need.level < need.thresholds.soft:
            return 0.0
        span = max(0.01, 1.0 - need.thresholds.soft)
        return min(1.0, (need.level - need.thresholds.soft) / span)

    def weight_for(self, definition: ActionDefinition) -> float:
        """How strongly this action is *wanted* right now (needs side)."""
        score = 0.0
        for key, relief in definition.need_relief.items():
            score += self.pressure(key) * relief
        return score

    def restore(self, data: dict[str, Any]) -> None:
        for key, payload in (data or {}).items():
            self._needs[key] = NeedState.model_validate(payload)

    def summary_line(self, *, limit: int = 3) -> str:
        """Prompt-ready: only the notable pressures (§86 — not a full dump)."""
        notable = sorted(self.pressing(), key=lambda n: n.level, reverse=True)[:limit]
        # generic labels; character-specific ones (pet/project) arrive via seed
        labels = {
            "hunger": "有点饿",
            "thirst": "想喝点冰的",
            "sleepiness": "困了",
            "energy": "没什么力气",
            "hygiene": "该洗澡了",
            "social_need": "想上网找人聊",
            "entertainment": "想找点乐子",
            "pet_care": "该管管宠物了",
            "household_maintenance": "家里该收拾了",
            "work_need": "有个活拖着",
            "project_progress": "想着没做完的事",
        }
        labels.update(self._labels)
        return "；".join(
            labels.get(need.key, need.key) + ("（很强烈）" if need.band() == "critical" else "")
            for need in notable
        )
