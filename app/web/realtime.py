"""Live push to the WebUI (Task 17).

The admin UI is server-rendered, so until now "watch what she is doing" meant
reloading a page. This hub streams two things to a logged-in browser over one
WebSocket:

* **narration** — the same lines the operator sees in the terminal (感知/判断/
  心理/沙盒心跳/工具…), redacted, never the raw event payload;
* **status** — a periodic summary built from the admin services (sandbox state
  line, model router snapshot, watchdog numbers).

Design rules that keep it safe in a single-loop process:

* **whitelist only** — nothing is pushed that an authenticated admin could not
  already read on a page; raw OneBot payloads, conversation text and secrets
  never travel here (``redact`` runs over every narration line);
* **never block** — each subscriber owns a *bounded* queue; when a slow browser
  falls behind the oldest message is dropped and counted, so publishing can
  never stall QQ chat;
* **auth** — the WebSocket goes through the same session cookie as every page;
  an unauthenticated upgrade is refused with 401 (see the server middleware).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from app.utils.logger import redact

#: how many messages a slow browser may fall behind before we start dropping
QUEUE_SIZE = 200

#: status snapshot cadence (seconds) — the "watch it live" heartbeat
STATUS_INTERVAL = 5.0

_NARRATION_LOGGER = "CatooBot.Narration"


class RealtimeHub:
    """Fan-out of narration lines + periodic status snapshots to browsers."""

    def __init__(
        self,
        *,
        queue_size: int = QUEUE_SIZE,
        metrics: Any = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._queue_size = max(1, queue_size)
        self._metrics = metrics
        self._log = logger or logging.getLogger("CatooBot.Web.Realtime")
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self.published = 0
        self.dropped = 0

    # ------------------------------------------------------------- fan-out

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)

    def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        self._subscribers.discard(queue)

    def publish(self, topic: str, payload: dict[str, Any]) -> int:
        """Hand one message to every subscriber; never blocks, never raises.

        Returns how many subscribers received it. A full queue drops its oldest
        entry (a browser that fell behind loses history, not the live tail).
        """
        self.published += 1
        if not self._subscribers:
            return 0
        message = {"topic": topic, "ts": int(time.time()), "data": payload}
        delivered = 0
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
                delivered += 1
            except asyncio.QueueFull:
                try:
                    queue.get_nowait()  # drop the oldest, keep the newest
                    queue.put_nowait(message)
                    delivered += 1
                except (asyncio.QueueEmpty, asyncio.QueueFull):  # pragma: no cover - race
                    pass
                self.dropped += 1
                self._count("realtime_dropped")
        return delivered

    def stats(self) -> dict[str, Any]:
        return {
            "subscribers": self.subscriber_count,
            "published": self.published,
            "dropped": self.dropped,
            "queue_size": self._queue_size,
        }

    def _count(self, key: str) -> None:
        if self._metrics is not None:
            self._metrics.inc(key)


class NarrationFeed(logging.Handler):
    """Forward narration records to the hub (redacted, structured, no payloads)."""

    def __init__(self, hub: RealtimeHub, level: int = logging.INFO) -> None:
        super().__init__(level=level)
        self._hub = hub

    def emit(self, record: logging.LogRecord) -> None:
        try:
            channel = str(getattr(record, "channel", "") or "")
            self._hub.publish(
                "narration",
                {
                    "channel": channel,
                    "level": record.levelname,
                    "message": redact(record.getMessage()),
                },
            )
        except Exception:  # noqa: BLE001 - pushing must never break logging
            return


def attach_narration_feed(hub: RealtimeHub) -> NarrationFeed:
    """Install the feed on the narration logger; returns it for removal."""
    feed = NarrationFeed(hub)
    logging.getLogger(_NARRATION_LOGGER).addHandler(feed)
    return feed


def detach_narration_feed(feed: NarrationFeed) -> None:
    logging.getLogger(_NARRATION_LOGGER).removeHandler(feed)
