"""OneBot 11 reverse-WebSocket server: CatooBot listens, NapCat connects.

Lifecycle::

    start() ─► listening ─► NapCat connects ─► path/token verified
            ─► events flow ─► (drop) ─► pending APIs failed ─► listen again

The server outlives any single NapCat connection: a disconnect never stops
the process, it just waits for NapCat to reconnect. Only one NapCat client is
served at a time; a new connection supersedes (closes) the previous one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import ParseResult, parse_qs, urlparse

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response

from app.adapters.onebot_v11.client import OneBotV11Connection
from app.adapters.onebot_v11.models import ApiResult
from app.adapters.onebot_v11.parser import parse_payload
from app.config.settings import OneBotConfig
from app.core.exceptions import AdapterNotConnected, ParseError
from app.message.event import Event

EventHandler = Callable[[Event], Awaitable[None]]


class OneBotV11Server:
    """WebSocket server accepting OneBot 11 clients (e.g. NapCat)."""

    def __init__(
        self,
        config: OneBotConfig,
        event_handler: EventHandler | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._config = config
        self._event_handler = event_handler
        self._lifecycle_handler: Any = None
        self._log = logger or logging.getLogger("CatooBot.OneBot")
        self._server: Any = None  # websockets.Server
        self._connection: OneBotV11Connection | None = None
        self._self_id: int | None = None
        self._connected = asyncio.Event()
        self._stopping = False
        self._dispatch_tasks: set[asyncio.Task[None]] = set()

    # ------------------------------------------------------------- properties

    @property
    def connected(self) -> bool:
        return self._connection is not None and not self._connection.closed

    @property
    def self_id(self) -> int | None:
        return self._self_id

    @property
    def connection(self) -> OneBotV11Connection | None:
        return self._connection

    def set_lifecycle_handler(self, handler: Any) -> None:
        """Optional callback for connect/disconnect (Phase 13.1 §22).

        Called with ``"connected"`` / ``"disconnected"``; failures are logged and
        never allowed to disturb the connection itself.
        """
        self._lifecycle_handler = handler

    def set_event_handler(self, handler: EventHandler) -> None:
        """Register the sink for parsed events (usually ``bot.handle_event``)."""
        self._event_handler = handler

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        """Start listening. Returns immediately; does not wait for NapCat."""
        self._stopping = False
        self._server = await serve(
            self._handle_client,
            self._config.host,
            self._config.port,
            process_request=self._check_request,
        )
        self._log.info("WebSocket server started at %s", self._config.url)

    async def stop(self) -> None:
        """Close the active connection and stop listening."""
        self._stopping = True
        if self._connection is not None:
            await self._connection.close(1001, "server shutting down")
            self._connection = None
            self._connected.clear()
        if self._dispatch_tasks:
            for task in self._dispatch_tasks:
                task.cancel()
            await asyncio.gather(*self._dispatch_tasks, return_exceptions=True)
            self._dispatch_tasks.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
            self._log.info("WebSocket server stopped")

    async def wait_for_connection(self, timeout: float | None = None) -> bool:
        """Wait until a client connects. Returns False on timeout."""
        if timeout is None:
            await self._connected.wait()
            return True
        try:
            await asyncio.wait_for(self._connected.wait(), timeout)
            return True
        except TimeoutError:
            return False

    # ------------------------------------------------------------ HTTP gate

    def _check_request(self, connection: ServerConnection, request: Request) -> Response | None:
        """Reject wrong paths and bad tokens before the WS handshake."""
        parsed = urlparse(request.path)
        if parsed.path != self._config.path:
            self._log.warning(
                "Rejected connection to unknown path '%s' (expected '%s')",
                parsed.path,
                self._config.path,
            )
            return connection.respond(404, "Not Found\n")

        if self._config.access_token:
            token = self._extract_token(request, parsed)
            if token != self._config.access_token:
                self._log.warning(
                    "Rejected connection from %s: invalid or missing access token",
                    _remote_of(connection),
                )
                return connection.respond(401, "Unauthorized\n")
        return None

    @staticmethod
    def _extract_token(request: Request, parsed: ParseResult) -> str | None:
        """Token may arrive as ``Authorization: Bearer <t>``, plain header,
        or ``?access_token=<t>`` query parameter (OneBot/NapCat compatibility)."""
        auth = request.headers.get("Authorization")
        if auth:
            if auth.lower().startswith("bearer "):
                return auth[7:].strip()
            return auth.strip()
        query = parse_qs(parsed.query)
        values = query.get("access_token") or query.get("token")
        return values[0] if values else None

    # -------------------------------------------------------- WS connection

    async def _handle_client(self, websocket: ServerConnection) -> None:
        connection = OneBotV11Connection(
            websocket, default_timeout=self._config.api_timeout, logger=self._log
        )
        previous = self._connection
        if previous is not None and not previous.closed:
            self._log.warning("New NapCat connection supersedes the previous one")
            await previous.close(1000, "replaced by a new connection")

        self._connection = connection
        self._connected.set()
        self._log.info("NapCat connected from %s", connection.remote)
        self._notify_lifecycle("connected")
        if self._config.access_token:
            self._log.info("Bot authenticated")
        asyncio.create_task(self._resolve_login_info(connection))

        try:
            await self._recv_loop(connection)
        except ConnectionClosed:
            pass
        except Exception:  # noqa: BLE001 - a broken connection must not kill the server
            self._log.exception("Unexpected error in connection handler")
        finally:
            await connection.close()
            if self._connection is connection:
                self._connection = None
                self._connected.clear()
                self._notify_lifecycle("disconnected")
            self._log.info("Connection closed, waiting for NapCat to reconnect...")

    def _notify_lifecycle(self, state: str) -> None:
        handler = getattr(self, "_lifecycle_handler", None)
        if handler is None:
            return
        try:
            handler(state)
        except Exception:  # noqa: BLE001 - a callback must never break the connection
            self._log.exception("Lifecycle handler failed (%s)", state)

    async def _recv_loop(self, connection: OneBotV11Connection) -> None:
        while True:
            text = await connection.recv()
            await self._on_raw_text(connection, text)

    async def _on_raw_text(self, connection: OneBotV11Connection, text: str) -> None:
        try:
            payload = json.loads(text)
        except ValueError:
            self._log.warning("Received invalid JSON (%.80s...), ignored", text)
            return
        if not isinstance(payload, dict):
            self._log.warning("Received non-object JSON payload, ignored")
            return

        self._log.debug("OneBot raw: %s", text)

        try:
            parsed = parse_payload(payload)
        except ParseError as exc:
            self._log.warning("Invalid OneBot payload ignored: %s", exc)
            return

        if isinstance(parsed, ApiResult):
            connection.handle_api_result(parsed)
            return

        assert isinstance(parsed, Event)
        if parsed.self_id:
            self._self_id = parsed.self_id
        if self._event_handler is not None:
            # Dispatch in its own task: handlers may await API calls whose
            # responses only arrive via this recv loop — awaiting them inline
            # here would deadlock the transport until the API timeout.
            task = asyncio.create_task(self._dispatch_event(parsed))
            self._dispatch_tasks.add(task)
            task.add_done_callback(self._dispatch_tasks.discard)

    async def _dispatch_event(self, event: Event) -> None:
        assert self._event_handler is not None
        try:
            await self._event_handler(event)
        except Exception:  # noqa: BLE001 - handler failure must not kill anything
            self._log.exception("Event handler raised, continuing")

    async def _resolve_login_info(self, connection: OneBotV11Connection) -> None:
        """Query who we are logged in as; also proves the API round-trip works."""
        try:
            info = await connection.call_api("get_login_info", timeout=10.0)
            if isinstance(info, dict) and info.get("user_id"):
                self._self_id = int(info["user_id"])
                self._log.info(
                    "Bot logged in as %s (%s)",
                    info.get("nickname", "unknown"),
                    self._self_id,
                )
        except Exception as exc:  # noqa: BLE001 - login probe is best-effort
            self._log.warning("get_login_info failed after connect: %s", exc)

    # ---------------------------------------------------------------- API

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        connection = self._connection
        if connection is None or connection.closed:
            raise AdapterNotConnected(
                f"Cannot call '{action}': no OneBot client connected (is NapCat running?)"
            )
        return await connection.call_api(action, params, timeout)


def _remote_of(connection: ServerConnection) -> str:
    try:
        return str(connection.remote_address)
    except Exception:  # noqa: BLE001
        return "unknown"
