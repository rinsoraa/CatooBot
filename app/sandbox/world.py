"""World systems: spaces, objects, inventories, and the rule engine.

The rule engine (§48/§126) is the hard gate: action proposals that violate
canonical rules (no school, no formal job, privacy, cat care…) are rejected
before they can touch state. The state manager is the only writer (§77).
"""

from __future__ import annotations

from typing import Any

from app.sandbox.bible import CharacterBible
from app.sandbox.models import (
    ExternalEvent,
    Inventory,
    SpaceNode,
    WorldObjectItem,
)

#: rules that forbid certain action ids outright (canonical hard stops)
RULE_ACTION_BANS: dict[str, set[str]] = {
    "game_preference": {"play_fighting_game"},
    "no_family_refs": set(),
    "night_owl": set(),  # night owl *encourages*, never bans
}


class SpaceSystem:
    def __init__(self, spaces: list[SpaceNode]) -> None:
        self._spaces = {space.id: space for space in spaces}

    def all(self) -> list[SpaceNode]:
        return list(self._spaces.values())

    def get(self, space_id: str) -> SpaceNode | None:
        return self._spaces.get(space_id)

    def name(self, space_id: str) -> str:
        space = self._spaces.get(space_id)
        return space.name if space else space_id

    def reachable(self, space_id: str) -> list[SpaceNode]:
        here = self._spaces.get(space_id)
        if here is None:
            return []
        return [self._spaces[sid] for sid in here.connects if sid in self._spaces]

    def is_home(self, space_id: str) -> bool:
        space = self._spaces.get(space_id)
        if space is None:
            return False
        if space.private:
            return True
        return any(
            self._spaces.get(sid) is not None and self._spaces[sid].private
            for sid in space.connects
        )

    def actions_allowed(self, space_id: str, action_defs: dict[str, Any]) -> list[str]:
        """Action ids valid *here* (space list + wildcard)."""
        allowed: list[str] = []
        for action_id, definition in action_defs.items():
            if "*" in definition.spaces or space_id in definition.spaces:
                allowed.append(action_id)
        return allowed

    def path_home(self, space_id: str) -> list[str]:
        """Bounded BFS to the nearest private space (for going home)."""
        if self.is_home(space_id):
            return [space_id]
        frontier: list[list[str]] = [[space_id]]
        seen = {space_id}
        while frontier:
            path = frontier.pop(0)
            for node in self.reachable(path[-1]):
                if node.id in seen:
                    continue
                if node.private:
                    return [*path, node.id]
                seen.add(node.id)
                frontier.append([*path, node.id])
        return [space_id]


class ObjectSystem:
    def __init__(self, objects: list[WorldObjectItem]) -> None:
        self._objects = {obj.id: obj for obj in objects}

    def all(self) -> list[WorldObjectItem]:
        return list(self._objects.values())

    def get(self, object_id: str) -> WorldObjectItem | None:
        return self._objects.get(object_id)

    def name(self, object_id: str) -> str:
        obj = self._objects.get(object_id)
        return obj.name if obj else object_id

    def in_space(self, space_id: str) -> list[WorldObjectItem]:
        return [obj for obj in self._objects.values() if obj.space_id == space_id]

    def apply_effect(self, effect_key: str, delta: float) -> None:
        """`object:<id>.<field>` effects from action completions."""
        _, _, rest = effect_key.partition(":")
        object_id, _, field = rest.partition(".")
        obj = self._objects.get(object_id)
        if obj is None:
            return
        current = obj.state.get(field, 0.0)
        if isinstance(current, bool):
            obj.state[field] = bool(delta)
        else:
            obj.state[field] = round(float(current) + delta, 4)


class InventorySystem:
    def __init__(self, inventories: dict[str, Inventory]) -> None:
        self._inventories = inventories

    def all(self) -> dict[str, Inventory]:
        return self._inventories

    def get(self, key: str) -> Inventory:
        if key not in self._inventories:
            self._inventories[key] = Inventory(key=key)
        return self._inventories[key]

    def can_consume(self, consumes: dict[str, dict[str, int]]) -> bool:
        """Negative counts mean "must exist, not consumed" (e.g. cat food -1)."""
        for key, items in consumes.items():
            inventory = self._inventories.get(key)
            for item, count in items.items():
                have = inventory.count(item) if inventory else 0
                if count >= 0 and have < count:
                    return False
                if count < 0 and have < abs(count):
                    return False
        return True

    def consume(self, consumes: dict[str, dict[str, int]]) -> None:
        for key, items in consumes.items():
            inventory = self.get(key)
            for item, count in items.items():
                if count > 0:
                    inventory.take(item, count)

    def apply_effect(self, effect_key: str, value: float) -> None:
        """`inventory:<key>:<item>` adds ``value`` copies (int)."""
        _, key, item = effect_key.split(":", 2)
        self.get(key).add(item, int(value))

    def summary(self, key: str, *, limit: int = 5) -> str:
        inventory = self._inventories.get(key)
        if inventory is None:
            return ""
        parts = [f"{item}×{count}" for item, count in list(inventory.items.items())[:limit]]
        return "、".join(parts)


class WorldRuleEngine:
    """Hard rules (§47-§48/§126): the AI can never override canon."""

    def __init__(self, bible: CharacterBible) -> None:
        self.bible = bible

    def allows_action(self, action_id: str) -> tuple[bool, str]:
        for rule_id, banned in RULE_ACTION_BANS.items():
            if action_id in banned and self.bible.has_rule(rule_id):
                return False, f"rule:{rule_id}"
        return True, ""

    def allows_location(self, space_id: str) -> tuple[bool, str]:
        if space_id in ("bedroom", "bathroom") and self.bible.has_rule("privacy_strict"):
            return True, ""  # private spaces fine; the privacy rule guards *disclosure*
        return True, ""

    def validate_proposal(self, action_id: str, space_id: str, definition: Any) -> tuple[bool, str]:
        ok, reason = self.allows_action(action_id)
        if not ok:
            return False, reason
        ok, reason = self.allows_location(space_id)
        if not ok:
            return False, reason
        return True, ""

    def boundary_line(self) -> str:
        """One prompt-ready line from the social boundaries (§conversation)."""
        lines = [b for b in self.bible.boundaries]
        return "；".join(line[:40] for line in lines[:4])

    def external_influence_action(self, event: ExternalEvent) -> tuple[str, str]:
        """User requests are *influence*, not commands (§92-§94/§142-§144).

        Returns ``(meaning, reason)``: how the character reads the message.
        """
        text = str(event.data.get("text", ""))
        if event.data.get("is_core_friend") and any(
            word in text for word in ("联机", "一起玩", "来打", "开服", "上号")
        ):
            return "invitation_game", "core_friend_invitation"
        return "message", "ordinary_stimulus"
