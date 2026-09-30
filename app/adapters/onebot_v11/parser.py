"""OneBot 11 JSON -> typed CatooBot event conversion.

This is the *only* place where raw OneBot payloads are interpreted. Everything
downstream (event bus, commands, plugins) receives the typed models from
:mod:`app.message.event`.

Supported OneBot event categories: message / notice / request / meta_event.
Unknown categories produce a generic :class:`Event` instead of crashing.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from app.adapters.onebot_v11.models import ApiResult
from app.core.exceptions import ParseError
from app.message.event import (
    Event,
    GroupMessageEvent,
    MessageEvent,
    MetaEvent,
    NoticeEvent,
    PrivateMessageEvent,
    RequestEvent,
)

ParsedPayload = Event | ApiResult


def parse_payload(raw: dict[str, Any]) -> ParsedPayload | None:
    """Classify an incoming JSON object as an API response or an event."""
    if "post_type" not in raw:
        if "retcode" in raw or "echo" in raw or "status" in raw:
            return ApiResult.model_validate(raw)
        raise ParseError(f"Unrecognized payload (no post_type / retcode): {str(raw)[:200]}")
    return parse_event(raw)


def parse_event(raw: dict[str, Any]) -> Event:
    """Convert a raw OneBot event dict into the matching typed Event."""
    post_type = str(raw.get("post_type", ""))

    cls: type[Event]
    if post_type == "message":
        cls = _message_event_class(raw)
    elif post_type == "notice":
        cls = NoticeEvent
    elif post_type == "request":
        cls = RequestEvent
    elif post_type == "meta_event":
        cls = MetaEvent
    else:
        # Unknown category: keep it as a generic Event so handlers can log it.
        cls = Event

    try:
        event = cls.model_validate(raw)
    except ValidationError as exc:
        raise ParseError(f"Invalid OneBot {post_type} event: {exc}") from exc
    # Preserve the original payload even though validation may ignore extras.
    event.raw_event = raw
    return event


def _message_event_class(raw: dict[str, Any]) -> type[MessageEvent]:
    message_type = str(raw.get("message_type", ""))
    if message_type == "private":
        return PrivateMessageEvent
    if message_type == "group":
        return GroupMessageEvent
    return MessageEvent
