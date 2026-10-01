"""Command registry and the ``@command`` decorator.

A command bundles invocation metadata (name, aliases, usage, permission) with
its handler coroutine ``async def handler(ctx: Context) -> None``. The
decorator only *marks* functions; registration into the live registry happens
when a plugin is loaded (see :mod:`app.plugins.loader`), keeping the registry
per-Bot instead of a module-level global.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterator
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from app.core.exceptions import CommandError

if TYPE_CHECKING:
    from app.core.context import Context

CommandHandler = Callable[["Context"], Awaitable[None]]


@dataclass
class Command:
    name: str
    handler: CommandHandler
    aliases: list[str] = field(default_factory=list)
    description: str = ""
    usage: str = ""
    permission: str = "user"
    plugin: str | None = None

    @property
    def all_names(self) -> list[str]:
        return [self.name, *self.aliases]

    def help_line(self, prefix: str = "/") -> str:
        if self.description:
            return f"{prefix}{self.name} — {self.description}"
        return f"{prefix}{self.name}"


def command(
    name: str | None = None,
    *,
    aliases: list[str] | None = None,
    description: str = "",
    usage: str = "",
    permission: str = "user",
) -> Callable[[CommandHandler], CommandHandler]:
    """Mark a coroutine as a CatooBot command::

    @command("ping", description="Test connectivity")
    async def ping(ctx): ...
    """

    def decorator(func: CommandHandler) -> CommandHandler:
        # The attribute is read back via getattr(), so typing stays loose here.
        marked = cast(Any, func)
        marked.__catoobot_command__ = {
            "name": name or func.__name__,
            "aliases": aliases or [],
            "description": description,
            "usage": usage,
            "permission": permission,
        }
        return func

    return decorator


def iter_command_functions(
    module_dict: dict[str, object],
) -> Iterator[tuple[CommandHandler, dict[str, Any]]]:
    """Yield ``(marked_function, spec)`` for every @command in a module dict."""
    for value in list(module_dict.values()):
        spec = getattr(value, "__catoobot_command__", None)
        if isinstance(spec, dict):
            yield cast(CommandHandler, value), spec


class CommandRegistry:
    """Holds the commands registered on one Bot instance."""

    def __init__(self) -> None:
        self._commands: dict[str, Command] = {}

    def register(
        self,
        name: str,
        handler: CommandHandler,
        *,
        aliases: list[str] | None = None,
        description: str = "",
        usage: str = "",
        permission: str = "user",
        plugin: str | None = None,
    ) -> Command:
        cmd = Command(
            name=name,
            handler=handler,
            aliases=list(aliases or []),
            description=description,
            usage=usage,
            permission=permission,
            plugin=plugin,
        )
        for candidate in cmd.all_names:
            if candidate in self._commands:
                raise CommandError(
                    f"Command '{candidate}' already registered "
                    f"(by plugin '{self._commands[candidate].plugin}')"
                )
        for candidate in cmd.all_names:
            self._commands[candidate] = cmd
        return cmd

    def register_marked(
        self, module_dict: dict[str, object], plugin: str | None = None
    ) -> list[Command]:
        """Register every @command-marked function found in a module."""
        registered: list[Command] = []
        for func, spec in iter_command_functions(module_dict):
            extra = {k: v for k, v in spec.items() if k != "name"}
            registered.append(self.register(str(spec["name"]), func, plugin=plugin, **extra))
        return registered

    def unregister_plugin(self, plugin: str) -> int:
        names = [n for n, c in self._commands.items() if c.plugin == plugin]
        for name in names:
            del self._commands[name]
        return len(names)

    def get(self, name: str) -> Command | None:
        return self._commands.get(name)

    def all(self) -> list[Command]:
        """Unique commands (by primary name), sorted alphabetically."""
        seen: dict[str, Command] = {}
        for cmd in self._commands.values():
            seen.setdefault(cmd.name, cmd)
        return [seen[k] for k in sorted(seen)]

    def __len__(self) -> int:
        return len(self.all())
