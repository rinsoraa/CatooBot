"""Typed event models for CatooBot.

The OneBot adapter converts raw protocol JSON into these classes; business
logic (commands, plugins) must depend only on these types, never on raw
OneBot payloads. ``raw_event`` keeps the original dict for debugging /
forward-compat, but code should not branch on it.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.message.message import Message


class Sender(BaseModel):
    """Author info attached to a message event (fields optional per protocol)."""

    user_id: int | None = None
    nickname: str | None = None
    card: str | None = None
    sex: str | None = None
    age: int | None = None
    area: str | None = None
    level: str | None = None
    role: str | None = None
    title: str | None = None

    @property
    def display_name(self) -> str:
        """Group card if present, else nickname."""
        return self.card or self.nickname or str(self.user_id or "unknown")


class Event(BaseModel):
    """Base class for all events flowing through the event bus."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    post_type: str
    self_id: int = 0
    time: int = 0
    raw_event: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def timestamp(self) -> int:
        return self.time


class MessageEvent(Event):
    """A private or group chat message."""

    message_type: str  # "private" | "group"
    sub_type: str = ""
    message_id: int = -1
    user_id: int = 0
    group_id: int | None = None
    message: Message = Field(default_factory=Message)
    raw_message: str = ""
    sender: Sender = Field(default_factory=Sender)

    @field_validator("message", mode="before")
    @classmethod
    def _coerce_message(cls, value: Any) -> Message:
        return Message.from_onebot(value)

    @property
    def is_private(self) -> bool:
        return self.message_type == "private"

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def is_to_me(self) -> bool:
        """True when the message starts by @-ing the bot (or is a private chat)."""
        if self.is_private:
            return True
        return self.message.is_mentioned(self.self_id)


class PrivateMessageEvent(MessageEvent):
    message_type: str = "private"


class GroupMessageEvent(MessageEvent):
    message_type: str = "group"


class NoticeEvent(Event):
    """Group file uploads, recalls, pokes, friend additions, etc."""

    notice_type: str = ""
    sub_type: str = ""
    group_id: int | None = None
    user_id: int | None = None
    operator_id: int | None = None
    target_id: int | None = None
    file: dict[str, Any] | None = None
    duration: int | None = None
    comment: str | None = None
    flag: str | None = None


class PokeNotice(NoticeEvent):
    """Someone poked someone (OneBot 11 ``notice_type=poke``)."""

    notice_type: str = "poke"


class RecallNotice(NoticeEvent):
    """A message was recalled (``group_recall`` / ``friend_recall``)."""

    notice_type: str = "group_recall"
    message_id: int | str | None = None


class BanNotice(NoticeEvent):
    """Mute / unmute (``group_ban``; ``sub_type`` is ban or lift_ban, duration in seconds)."""

    notice_type: str = "group_ban"


class CardChangeNotice(NoticeEvent):
    """A group member changed their card (``group_card``)."""

    notice_type: str = "group_card"
    card_new: str = ""
    card_old: str = ""


class RequestEvent(Event):
    """Friend / group join requests."""

    request_type: str = ""
    sub_type: str = ""
    user_id: int = 0
    comment: str = ""
    flag: str = ""


class MetaEvent(Event):
    """Lifecycle (connect) and heartbeat events from the protocol side."""

    meta_event_type: str = ""
    sub_type: str = ""
    status: dict[str, Any] | None = None
    interval: int | None = None


EVENT_POST_TYPES: dict[str, type[Event]] = {
    "message": MessageEvent,
    "notice": NoticeEvent,
    "request": RequestEvent,
    "meta_event": MetaEvent,
}
