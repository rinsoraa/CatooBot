"""OneBot transport adapter for the gateway (Phase 13 §4/§5/§49-§52).

The gateway speaks to a small interface; this file adapts the *existing*
OneBot 11 server (``app/adapters/onebot_v11``) to it. No protocol logic beyond
mapping: the sandbox never imports anything from here, and this module never
touches sandbox state.
"""

from __future__ import annotations

from typing import Any, Protocol

from app.adapters.onebot_v11.api import BotApi


class OneBotTransport(Protocol):
    """What the gateway needs from a transport (nothing more)."""

    connected: bool

    async def start(self, on_event: Any) -> None: ...

    async def stop(self) -> None: ...

    async def send(self, payload: dict[str, Any]) -> None: ...

    async def reconnect(self, on_event: Any) -> None: ...


class ServerTransport:
    """Adapter over the existing WS server + API client (NapCat connects to us)."""

    def __init__(
        self,
        server: Any,
        *,
        logger: Any = None,
        manage_lifecycle: bool = False,
        fallback: Any = None,
    ) -> None:
        self._server = server
        self._api = BotApi(server)
        self._log = logger
        self._on_event = None
        #: non-message events (notice/meta/request) keep their existing path
        self._fallback = fallback
        #: server mode: the bot already owns the socket; the gateway only routes
        self._manage_lifecycle = manage_lifecycle

    @property
    def connected(self) -> bool:
        return bool(getattr(self._server, "connected", False))

    @property
    def self_id(self) -> int | None:
        return getattr(self._server, "self_id", None)

    async def start(self, on_event: Any) -> None:
        self._on_event = on_event
        # the server hands us typed OneBot events; the gateway normalizes them
        self._server.set_event_handler(self._dispatch)
        if self._manage_lifecycle:
            await self._server.start()

    async def stop(self) -> None:
        if self._manage_lifecycle:
            await self._server.stop()

    async def reconnect(self, on_event: Any) -> None:
        """Server mode: reconnections are the *client's* job — accept them again."""
        self._on_event = on_event
        self._server.set_event_handler(self._dispatch)
        if self._manage_lifecycle and not self.connected:
            await self._server.start()

    async def send(self, payload: dict[str, Any]) -> None:
        """OneBot-shaped payload → the existing API client (text + target)."""
        text = "".join(
            str((segment.get("data") or {}).get("text", ""))
            for segment in payload.get("message", [])
            if isinstance(segment, dict) and segment.get("type") == "text"
        )
        group_id = str(payload.get("group_id", "") or "")
        user_id = str(payload.get("user_id", "") or "")
        await self._api.send_msg(
            text,
            user_id=int(user_id) if user_id else None,
            group_id=int(group_id) if group_id else None,
        )

    async def _dispatch(self, event: Any) -> None:
        """Messages go to the gateway; everything else keeps the old handling."""
        if str(getattr(event, "post_type", "")) != "message":
            if self._fallback is not None:
                await self._fallback(event)
            return
        if self._on_event is not None:
            await self._on_event(event)
