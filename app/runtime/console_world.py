"""Change-driven console world log (observability only).

The per-second :class:`RuntimeScheduler` asks the world to advance every second;
*printing* the world every second is a different thing and made the terminal
unreadable. This module keeps the two apart:

    tick → build the *terminal-visible* snapshot → compare with the last printed
        → identical: stay quiet
        → changed:   print once

Only display semantics take part in the comparison (the action label, the place,
the modes, the rendered need bands) — never tick counters, timestamps, internal
floats or revisions, and never a whole-state dump. Nothing here writes world
state: it is a read-only projection plus one runtime-memory field.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class ConsoleWorldSnapshot:
    """Exactly what the terminal shows about the world — nothing internal."""

    doing: str
    location: str
    modes: tuple[str, ...]
    #: the rendered pressure entries, order-insensitive (band text only)
    pressing: tuple[str, ...]

    def line(self) -> str:
        modes = "+".join(self.modes) if self.modes else "-"
        return f"{self.doing}（{self.location}）  ·  {modes}"

    def detail(self) -> str:
        return "沙盒心跳" + (f"（{'；'.join(self.pressing)}）" if self.pressing else "")


def console_world_snapshot(runtime: Any) -> ConsoleWorldSnapshot:
    """Project the runtime into the terminal-visible world state (§5/§6).

    Read-only: it calls the very same accessors the log line used before
    (``status_line`` / ``modes.ids`` / ``needs.summary_line``), so what is
    compared is what a reader would see.
    """
    summary = runtime.needs.summary_line() if runtime.needs.pressing() else ""
    pressing = tuple(sorted(entry for entry in summary.split("；") if entry))
    definition = runtime.actions.definition(runtime.current_action)
    doing = definition.name if definition is not None else "闲着"
    return ConsoleWorldSnapshot(
        doing=doing,
        location=runtime.spaces.name(runtime.character.location),
        modes=tuple(runtime.modes.ids()),
        pressing=pressing,
    )


class ConsoleWorldReporter:
    """Prints the world line only when the visible snapshot really changed."""

    def __init__(self, *, emit: Callable[[ConsoleWorldSnapshot], None] | None = None) -> None:
        self._emit = emit
        #: runtime memory only — a restart prints the first state again (§14)
        self.last: ConsoleWorldSnapshot | None = None
        self.printed = 0
        self.skipped = 0

    def observe(self, snapshot: ConsoleWorldSnapshot) -> bool:
        """True when this observation produced a line; False when suppressed."""
        if self.last is not None and snapshot == self.last:
            self.skipped += 1
            return False
        self.last = snapshot
        self.printed += 1
        if self._emit is not None:
            self._emit(snapshot)
        return True
