"""A fake OneBot transport for tests (Phase 13 §76/§85) — no network, ever."""

from __future__ import annotations

import asyncio
from typing import Any


class FakeOneBotTransport:
    """In-memory transport: receive events, record sends, fail on demand."""

    def __init__(self, *, self_id: str = "10000") -> None:
        self.self_id = self_id
        self.connected = False
        self.sent_messages: list[dict[str, Any]] = []
        self.send_attempts = 0
        self.starts = 0
        self.stops = 0
        self.reconnects = 0
        self._on_event: Any = None
        self._on_connected: Any = None
        self._on_disconnected: Any = None
        #: a fake never dials: reconnect means "the client connects again"
        self.can_dial = False
        self._fail_next_sends = 0
        self._send_delay = 0.0
        self._receive_delay = 0.0
        self._lock = asyncio.Lock()

    # ---------------------------------------------------------------- inbound

    async def receive(self, raw: dict[str, Any]) -> dict[str, Any]:
        if self._receive_delay:
            await asyncio.sleep(self._receive_delay)
        assert self._on_event is not None, "transport not started"
        return await self._on_event(raw)

    def delay_next_receive(self, seconds: float) -> None:
        self._receive_delay = seconds

    # --------------------------------------------------------------- outbound

    async def send(self, payload: dict[str, Any]) -> None:
        async with self._lock:
            self.send_attempts += 1
            if self._fail_next_sends > 0:
                self._fail_next_sends -= 1
                raise ConnectionError("fake send failure")
        if self._send_delay:
            await asyncio.sleep(self._send_delay)
        self.sent_messages.append(dict(payload))

    def fail_next_send(self, times: int = 1) -> None:
        self._fail_next_sends = int(times)

    def delay_next_send(self, seconds: float) -> None:
        self._send_delay = seconds

    # -------------------------------------------------------------- lifecycle

    def set_lifecycle(self, *, on_connected: Any = None, on_disconnected: Any = None) -> None:
        self._on_connected = on_connected
        self._on_disconnected = on_disconnected

    async def start(self, on_event: Any) -> None:
        self.starts += 1
        self._on_event = on_event
        self.connected = True
        if self._on_connected is not None:
            self._on_connected()

    async def stop(self) -> None:
        self.stops += 1
        self.connected = False
        self._on_event = None

    async def reconnect(self, on_event: Any) -> None:
        self.reconnects += 1
        self._on_event = on_event
        self.connected = True

    def disconnect(self) -> None:
        """Drop the connection the way a real socket would (transport state only)."""
        self.connected = False
        if self._on_disconnected is not None:
            self._on_disconnected()

    def reconnect_client(self) -> None:
        """NapCat dials us back: the server accepts and reports "connected" (§25)."""
        self.connected = True
        if self._on_connected is not None:
            self._on_connected()


def qq_message(  # noqa: PLR0913 - a test builder mirrors the protocol
    *,
    message_id: str = "1",
    user_id: str = "20001",
    text: str = "在吗",
    self_id: str = "10000",
    group_id: str = "",
    at_self: bool = False,
    display_name: str = "",
    time: float = 1700000000.0,
) -> dict[str, Any]:
    """One raw OneBot 11 message event (array format)."""
    segments: list[dict[str, Any]] = []
    if at_self:
        segments.append({"type": "at", "data": {"qq": self_id}})
    segments.append({"type": "text", "data": {"text": text}})
    raw: dict[str, Any] = {
        "post_type": "message",
        "message_type": "group" if group_id else "private",
        "message_id": message_id,
        "self_id": self_id,
        "user_id": user_id,
        "message": segments,
        "raw_message": text,
        "time": time,
        "sender": {"nickname": display_name or f"user{user_id}"},
    }
    if group_id:
        raw["group_id"] = group_id
    return raw
