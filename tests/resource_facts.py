"""Explicit known unit observations for non-server integration fixtures."""


def unit_harvest_facts(*, name: str | None, damageable: bool, remaining: int | None):
    return {
        "known": True,
        "can_harvest": True,
        "tool": {
            "known": True,
            "name": name,
            "damageable": damageable,
            "durability_known": True,
            "remaining_uses": remaining,
            "enchantments_known": True,
            "has_enchantments": False,
            "components_known": True,
            "unsupported_components": [],
        },
    }
