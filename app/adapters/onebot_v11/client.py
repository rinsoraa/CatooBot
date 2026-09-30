"""A single live WebSocket connection from NapCat (reverse WS client).

Owns the API request/response machinery for that connection:

    call_api() ──► {"action", "params", "echo"} ──► NapCat
         ▲                                              │
         └── Future resolved by echo ◄── ApiResult ◄────┘

On disconnect every pending future fails with :class:`AdapterDisconnected`
so no ``await`` ever hangs forever.
"""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
from typing import Any

from websockets.asyncio.server import ServerConnection

from app.adapters.onebot_v11.models import ApiResult
from app.core.exceptions import (
    AdapterDisconnected,
    AdapterNotConnected,
    ApiTimeoutError,
    OneBotApiError,
)


class OneBotV11Connection:
    """One accepted NapCat connection: JSON send/recv + echo-matched API calls."""

    def __init__(
        self,
        websocket: ServerConnection,
        default_timeout: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._ws = websocket
        self._default_timeout = default_timeout
        self._log = logger or logging.getLogger("CatooBot.OneBot")
        self._echo_counter = itertools.count(1)
        self._pending: dict[str, tuple[asyncio.Future[Any], str]] = {}
        self._send_lock = asyncio.Lock()
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def remote(self) -> str:
        try:
            return str(self._ws.remote_address)
        except Exception:  # noqa: BLE001 - address may be unavailable mid-close
            return "unknown"

    # ------------------------------------------------------------------ send

    async def send_json(self, payload: dict[str, Any]) -> None:
        if self._closed:
            raise AdapterNotConnected("Connection is closed")
        data = json.dumps(payload, ensure_ascii=False)
        async with self._send_lock:
            await self._ws.send(data)

    async def recv(self) -> str:
        """Receive the next text frame (binary frames are decoded as UTF-8)."""
        raw = await self._ws.recv()
        if isinstance(raw, bytes):
            return raw.decode("utf-8", errors="replace")
        return raw

    # -------------------------------------------------------------- API calls

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        """Send an OneBot API request and await its response (matched by echo)."""
        if self._closed:
            raise AdapterNotConnected(f"Cannot call '{action}': connection closed")

        echo = str(next(self._echo_counter))
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[echo] = (future, action)
        await self.send_json({"action": action, "params": params or {}, "echo": echo})

        effective_timeout = timeout if timeout is not None else self._default_timeout
        try:
            return await asyncio.wait_for(future, effective_timeout)
        except TimeoutError:
            self._pending.pop(echo, None)
            raise ApiTimeoutError(
                f"OneBot API '{action}' timed out after {effective_timeout:.1f}s"
            ) from None

    def handle_api_result(self, result: ApiResult) -> None:
        """Resolve the future waiting on ``result.echo`` (called from recv loop)."""
        if result.echo is None:
            self._log.debug("Received API result without echo, ignoring: %s", result.retcode)
            return
        entry = self._pending.pop(result.echo, None)
        if entry is None:
            self._log.warning("API result with unknown echo=%s (already timed out?)", result.echo)
            return
        future, action = entry
        if future.done():
            return
        if result.ok:
            future.set_result(result.data)
        else:
            future.set_exception(
                OneBotApiError(action, result.retcode, result.status, str(result.data or ""))
            )

    # ------------------------------------------------------------- lifecycle

    def fail_pending(self, exc: Exception) -> None:
        """Fail every in-flight API call (used when the connection drops)."""
        count = len(self._pending)
        for future, _action in self._pending.values():
            if not future.done():
                future.set_exception(exc)
        self._pending.clear()
        if count:
            self._log.warning("Failed %d pending API call(s): %s", count, exc)
        self._pending.clear()

    def mark_closed(self) -> None:
        if self._closed:
            return
        self._closed = True
        self.fail_pending(AdapterDisconnected("OneBot connection closed"))

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Close the WebSocket gracefully and fail pending calls."""
        self.mark_closed()
        try:
            await self._ws.close(code, reason)
        except Exception:  # noqa: BLE001 - closing an already-broken socket is fine
            pass

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"<OneBotV11Connection remote={self.remote} closed={self._closed}>"
