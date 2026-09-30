"""Adapter layer: bridges CatooBot core and chat protocols (OneBot 11, ...).

The core never imports a concrete adapter; it only depends on
:class:`Adapter`. Swapping NapCat for another implementation later means
writing a new class satisfying this protocol — nothing else changes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any, Protocol, runtime_checkable

from app.message.event import Event

EventHandler = Callable[[Event], Awaitable[None]]


@runtime_checkable
class Adapter(Protocol):
    """Contract every protocol adapter must satisfy."""

    @property
    def connected(self) -> bool: ...

    @property
    def self_id(self) -> int | None: ...

    async def start(self) -> None: ...

    async def stop(self) -> None: ...

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any: ...
