"""Command registry infrastructure tests.

Since v0.3 the QQ surface mounts no commands; the registry stays as internal
infrastructure, so only its unit behavior is covered here.
"""

from __future__ import annotations

import pytest

from app.commands.registry import CommandRegistry, command
from app.core.context import Context
from app.core.exceptions import CommandError


class TestRegistry:
    def test_register_and_get(self) -> None:
        registry = CommandRegistry()

        @command("demo", aliases=["d"], description="demo cmd")
        async def demo(ctx: Context) -> None:
            return None

        registry.register_marked({"demo": demo})
        assert registry.get("demo") is not None
        assert registry.get("d") is not None
        assert registry.get("demo").aliases == ["d"]

    def test_duplicate_raises(self) -> None:
        registry = CommandRegistry()

        async def handler(ctx: Context) -> None:
            return None

        registry.register("x", handler)
        with pytest.raises(CommandError):
            registry.register("x", handler)
        with pytest.raises(CommandError):
            registry.register("y", handler, aliases=["x"])

    def test_unregister_plugin(self) -> None:
        registry = CommandRegistry()

        async def handler(ctx: Context) -> None:
            return None

        registry.register("a", handler, plugin="p1")
        registry.register("b", handler, plugin="p2")
        assert registry.unregister_plugin("p1") == 1
        assert registry.get("a") is None
        assert registry.get("b") is not None

    def test_all_sorted_unique(self) -> None:
        registry = CommandRegistry()

        async def handler(ctx: Context) -> None:
            return None

        registry.register("b", handler)
        registry.register("a", handler)
        registry.register("c", handler, aliases=["b2"])
        assert [c.name for c in registry.all()] == ["a", "b", "c"]

    def test_empty_registry_gets_none(self) -> None:
        registry = CommandRegistry()
        assert registry.get("ping") is None
        assert registry.all() == []
