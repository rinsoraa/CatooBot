"""EntitySystem: the pet as a real entity (v2.0 §22-§26).

小喵 is not a sentence in a prompt — she is an entity with needs and
rule-based behavior. Her rules are deterministic; she is *not* driven by an
LLM (§26). Critical pet needs can wake the sandbox (§70).
"""

from __future__ import annotations

import random
from typing import Any

from app.sandbox.models import PetActivity, PetState

#: where a cat may nap, in preference order
CAT_NAP_SPOTS = ("sofa", "bed", "windowsill", "floor")
OWNER_ROOM_GAMES = ("play_minecraft", "play_singleplayer")


class PetSystem:
    def __init__(self, pet: PetState, *, rng: random.Random, clock: Any) -> None:
        self.pet = pet
        self._rng = rng
        self._clock = clock

    # ------------------------------------------------------------------- tick

    def advance(self, minutes: float) -> None:
        hours = minutes / 60.0
        self.pet.hunger = min(1.0, self.pet.hunger + 0.06 * hours)
        if self.pet.activity == PetActivity.sleeping:
            self.pet.energy = min(1.0, self.pet.energy + 0.25 * hours)
        else:
            self.pet.energy = max(0.0, self.pet.energy - 0.05 * hours)
        if self.pet.energy > 0.85 and self.pet.activity == PetActivity.sleeping:
            self.pet.activity = PetActivity.idle

    def tick(  # noqa: PLR0911 - explicit rule branches, keep flat
        self,
        *,
        minutes: float,
        owner_space: str,
        owner_action: str,
        owner_home: bool,
        food_available: bool,
    ) -> list[str]:
        """Deterministic behavior rules (§26). Returns notable event lines."""
        self.advance(minutes)
        pet = self.pet
        events: list[str] = []

        # Sleeping cats stay asleep — no random switching (§26).
        if pet.activity == PetActivity.sleeping:
            if pet.hunger >= 0.8 and pet.energy >= 0.6:
                pet.activity = PetActivity.idle
                return events
            if pet.energy < 0.85:
                return events
            pet.activity = PetActivity.idle

        # Hungry → find the owner (or the bowl).
        if pet.hunger >= 0.7:
            if owner_home and pet.hunger >= 0.75:
                if pet.activity != PetActivity.approaching_owner:
                    pet.activity = PetActivity.approaching_owner
                    if pet.location != owner_space:
                        pet.location = owner_space
                    events.append("小喵饿了，蹭过来找罐头")
                    pet.last_interaction = float(self._clock())
                return events
            if food_available and pet.hunger >= 0.8:
                pet.activity = PetActivity.eating
                pet.hunger = max(0.0, pet.hunger - 0.35)
                events.append("小喵自己去吃了几口猫粮")
                return events

        # Tired → find a comfortable spot.
        if pet.energy <= 0.3:
            pet.activity = PetActivity.sleeping
            pet.location = self._rng.choice(CAT_NAP_SPOTS)
            events.append("小喵找了个地方睡下了")
            return events

        # Owner playing at the computer → the keyboard is a magnet.
        if (
            pet.location == owner_space
            and owner_action in OWNER_ROOM_GAMES
            and pet.activity not in (PetActivity.on_keyboard, PetActivity.sitting_near_owner)
        ):
            if self._rng.random() < 0.25:
                pet.activity = PetActivity.on_keyboard
                events.append("小喵跳上了键盘")
                pet.last_interaction = float(self._clock())
            else:
                pet.activity = PetActivity.sitting_near_owner
                events.append("小喵趴在旁边看她玩")
            return events

        # Owner is home and near → sit close.
        if (
            owner_home
            and pet.location == owner_space
            and pet.activity in (PetActivity.idle, PetActivity.wandering)
        ):
            pet.activity = PetActivity.sitting_near_owner
            return events

        # Otherwise: idle / wander.
        if pet.activity not in (PetActivity.idle, PetActivity.wandering):
            pet.activity = PetActivity.idle
        elif self._rng.random() < 0.15:
            pet.activity = PetActivity.wandering
            if owner_home and self._rng.random() < 0.4:
                pet.location = owner_space
        return events

    def feed(self) -> bool:
        """The one thing that gets her off the sofa (§dogfood)."""
        if self.pet.hunger < 0.15 and self.pet.activity == PetActivity.sitting_near_owner:
            return False
        self.pet.hunger = max(0.0, self.pet.hunger - 0.6)
        self.pet.affection = min(1.0, self.pet.affection + 0.1)
        self.pet.activity = PetActivity.eating
        self.pet.last_interaction = float(self._clock())
        return True

    # ---------------------------------------------------------------- reading

    def activity_label(self) -> str:
        labels = {
            PetActivity.sleeping: "睡着了",
            PetActivity.idle: "趴着",
            PetActivity.wandering: "在屋里溜达",
            PetActivity.approaching_owner: "在蹭罐头要吃的",
            PetActivity.sitting_near_owner: "趴在她旁边",
            PetActivity.on_keyboard: "踩在键盘上",
            PetActivity.eating: "在吃猫粮",
            PetActivity.drinking: "在喝水",
            PetActivity.grooming: "在舔毛",
        }
        return labels.get(self.pet.activity, self.pet.activity.value)

    def prompt_line(self) -> str:
        return f"小喵{self.activity_label()}（{self.pet.location}）"

    def snapshot(self) -> dict[str, Any]:
        return self.pet.model_dump(mode="json")

    def restore(self, data: dict[str, Any]) -> None:
        self.pet = PetState.model_validate(data)
