"""Minimal async event bus.

Subscriptions can be keyed by event class or by dotted string names::

    bus.on(MessageEvent, handler)      # fires for Group/Private subclasses too
    bus.on("message", handler)          # post_type level
    bus.on("message.group", handler)   # finer granularity

Handlers run sequentially in registration order. One handler raising never
blocks the others and never propagates to the caller of :meth:`emit`.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.message.event import (
    Event,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
    RequestEvent,
)

# Handlers declare the event subtype they care about (e.g. MessageEvent);
# typing as Callable[[Any], ...] keeps that contravariance simple for v0.1.
Handler = Callable[[Any], Awaitable[None]]
SubscriptionKey = type[Event] | str


def event_names(event: Event) -> list[str]:
    """Dotted string names an event should match, coarse to fine."""
    if isinstance(event, MessageEvent):
        return ["message", f"message.{event.message_type}"]
    if isinstance(event, NoticeEvent):
        return ["notice", f"notice.{event.notice_type}"]
    if isinstance(event, RequestEvent):
        return ["request", f"request.{event.request_type}"]
    if isinstance(event, MetaEvent):
        return ["meta_event", f"meta_event.{event.meta_event_type}"]
    return [event.post_type]


class _Subscription:
    __slots__ = ("key", "handler")

    def __init__(self, key: SubscriptionKey, handler: Handler) -> None:
        self.key = key
        self.handler = handler


class EventBus:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("CatooBot.EventBus")
        self._subscriptions: list[_Subscription] = []

    # ---------------------------------------------------------- subscribe

    def on(self, key: SubscriptionKey, handler: Handler) -> Handler:
        """Register ``handler`` for a class or string key (idempotent per pair)."""
        if not callable(handler):
            raise TypeError("handler must be callable")
        if isinstance(key, str):
            key = key.strip().lower()
        if not any(sub.key == key and sub.handler == handler for sub in self._subscriptions):
            self._subscriptions.append(_Subscription(key, handler))
        return handler

    def off(self, key: SubscriptionKey, handler: Handler) -> bool:
        """Remove a subscription. Returns True if it existed."""
        if isinstance(key, str):
            key = key.strip().lower()
        for index, sub in enumerate(self._subscriptions):
            if sub.key == key and sub.handler == handler:
                del self._subscriptions[index]
                return True
        return False

    def remove_owner(self, owner: object) -> int:
        """Remove every subscription whose handler belongs to ``owner``.

        Plugins subscribe with bound methods; when a plugin is unloaded (or
        reloaded) its handlers must be dropped or the old instance keeps
        receiving events — which duplicated every reply after a reload.
        """
        kept: list[_Subscription] = []
        removed = 0
        for sub in self._subscriptions:
            if getattr(sub.handler, "__self__", None) is owner:
                removed += 1
            else:
                kept.append(sub)
        self._subscriptions = kept
        return removed

    # ------------------------------------------------------------- emit

    def _matches(self, sub: _Subscription, event: Event, names: list[str]) -> bool:
        if isinstance(sub.key, str):
            return sub.key in names
        return isinstance(event, sub.key)

    async def emit(self, event: Event) -> None:
        """Dispatch to all matching handlers; never raises."""
        names = event_names(event)
        for sub in list(self._subscriptions):  # copy: handlers may subscribe
            if not self._matches(sub, event, names):
                continue
            try:
                await sub.handler(event)
            except Exception:  # noqa: BLE001 - isolate handler failures
                self._log.exception(
                    "Event handler %r failed for %s",
                    getattr(sub.handler, "__name__", sub.handler),
                    names[-1],
                )

    def subscriber_count(self) -> int:
        return len(self._subscriptions)
