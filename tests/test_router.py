"""Event bus + command router tests: MessageEvent reaches the right handler."""

from __future__ import annotations

from typing import Any

from app.core.event_bus import EventBus
from app.message.event import (
    Event,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
)
from tests.conftest import group_event, private_event


class TestEventBus:
    async def test_class_subscription(self) -> None:
        bus = EventBus()
        seen: list[MessageEvent] = []

        async def handler(event: MessageEvent) -> None:
            seen.append(event)

        bus.on(MessageEvent, handler)
        await bus.emit(group_event())
        await bus.emit(private_event())
        assert len(seen) == 2

    async def test_string_subscription(self) -> None:
        bus = EventBus()
        hits: list[str] = []

        async def message_handler(event: Event) -> None:
            hits.append("message")

        async def group_handler(event: Event) -> None:
            hits.append("message.group")

        async def meta_handler(event: Event) -> None:
            hits.append("meta")

        bus.on("message", message_handler)
        bus.on("message.group", group_handler)
        bus.on("meta_event", meta_handler)
        await bus.emit(group_event())
        await bus.emit(MetaEvent(post_type="meta_event", self_id=1))
        assert hits == ["message", "message.group", "meta"]

    async def test_subclass_matches_class_subscription(self) -> None:
        bus = EventBus()
        hits: list[str] = []

        async def handler(event: Event) -> None:
            hits.append(type(event).__name__)

        bus.on(MessageEvent, handler)
        await bus.emit(group_event())
        assert hits == ["GroupMessageEvent"]

    async def test_non_matching_handler_not_called(self) -> None:
        bus = EventBus()
        calls: list[Any] = []

        async def handler(event: Event) -> None:
            calls.append(event)

        bus.on(NoticeEvent, handler)
        await bus.emit(group_event())
        assert calls == []

    async def test_handler_exception_isolated(self) -> None:
        bus = EventBus()
        hits: list[str] = []

        async def bad(_event: Event) -> None:
            raise RuntimeError("boom")

        async def good(_event: Event) -> None:
            hits.append("good")

        bus.on(MessageEvent, bad)
        bus.on(MessageEvent, good)
        await bus.emit(group_event())  # must not raise
        assert hits == ["good"]

    async def test_registration_order_preserved(self) -> None:
        bus = EventBus()
        order: list[int] = []

        async def first(_e: Event) -> None:
            order.append(1)

        async def second(_e: Event) -> None:
            order.append(2)

        bus.on(MessageEvent, first)
        bus.on(MessageEvent, second)
        await bus.emit(group_event())
        assert order == [1, 2]

    async def test_off_removes(self) -> None:
        bus = EventBus()
        calls: list[Any] = []

        async def handler(_e: Event) -> None:
            calls.append(1)

        bus.on(MessageEvent, handler)
        assert bus.off(MessageEvent, handler)
        await bus.emit(group_event())
        assert calls == []

    async def test_duplicate_registration_is_idempotent(self) -> None:
        bus = EventBus()
        calls: list[Any] = []

        async def handler(_e: Event) -> None:
            calls.append(1)

        bus.on(MessageEvent, handler)
        bus.on(MessageEvent, handler)
        await bus.emit(group_event())
        assert calls == [1]
