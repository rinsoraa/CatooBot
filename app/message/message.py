"""Message container: an ordered list of typed segments.

Business code should treat messages as structured data, not raw strings::

    Message([TextSegment("你好 "), AtSegment(user_id=123), TextSegment(" 今天怎么样？")])

    message.text          # plain text ("你好  今天怎么样？")
    message.segments      # structured segment list
    message.is_mentioned(bot.self_id)
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from app.message.segment import (
    AtSegment,
    FaceSegment,
    ImageSegment,
    MfaceSegment,
    ReplySegment,
    Segment,
    TextSegment,
    _unescape_cq,
)

_CQ_PATTERN = re.compile(r"\[CQ:(?P<type>[a-zA-Z0-9_.-]+)(?P<params>[^\]]*)\]")


def parse_cq_string(raw: str) -> list[Segment]:
    """Parse a CQ-code message string into segments."""
    segments: list[Segment] = []
    pos = 0
    for match in _CQ_PATTERN.finditer(raw):
        if match.start() > pos:
            plain = _unescape_cq(raw[pos : match.start()])
            segments.append(TextSegment(type="text", data={"text": plain}))
        params: dict[str, Any] = {}
        param_str = match.group("params")
        for pair in param_str.split(",") if param_str else []:
            if not pair:
                continue
            key, sep, value = pair.partition("=")
            if sep:
                params[key.strip()] = _unescape_cq(value)
        segments.append(Segment.from_onebot({"type": match.group("type"), "data": params}))
        pos = match.end()
    if pos < len(raw):
        segments.append(TextSegment(type="text", data={"text": _unescape_cq(raw[pos:])}))
    return segments


class Message:
    """Ordered, typed message segment list."""

    def __init__(self, segments: list[Segment] | None = None) -> None:
        self._segments: list[Segment] = list(segments or [])

    # ------------------------------------------------------------------ build

    @classmethod
    def from_onebot(cls, raw: Any) -> Message:
        """Accept OneBot array format (``list[dict]``) or CQ string format."""
        if raw is None:
            return cls()
        if isinstance(raw, Message):
            return cls(raw._segments)
        if isinstance(raw, str):
            return cls(parse_cq_string(raw))
        if isinstance(raw, Segment):
            return cls([raw])
        if isinstance(raw, list):
            return cls(
                [
                    Segment.from_onebot(item) if isinstance(item, dict) else item
                    for item in raw
                ]
            )
        raise TypeError(f"Cannot build Message from {type(raw).__name__}")

    @classmethod
    def text_message(cls, text: str) -> Message:
        return cls([TextSegment(type="text", data={"text": text})])

    def to_onebot(self) -> list[dict[str, Any]]:
        """Serialize to OneBot array format (NapCat's preferred form)."""
        return [seg.to_onebot() for seg in self._segments]

    def to_cq_string(self) -> str:
        return "".join(seg.to_cq() for seg in self._segments)

    # ------------------------------------------------------------- accessors

    @property
    def segments(self) -> list[Segment]:
        return list(self._segments)

    @property
    def text(self) -> str:
        """Concatenated plain text of all text segments (non-text ignored)."""
        return "".join(seg.text for seg in self._segments if isinstance(seg, TextSegment))

    def is_mentioned(self, user_id: int | str) -> bool:
        """True if the message contains an @ targeting ``user_id`` (not @all)."""
        uid = int(user_id)
        return any(
            isinstance(seg, AtSegment) and seg.user_id == uid for seg in self._segments
        )

    def strip_prefix_at(self, user_id: int | str) -> bool:
        """Remove leading @bot segments (and stray whitespace text) in-place.

        Used by the command router so that ``@CatooBot /ping`` and ``/ping``
        parse identically. Returns True if anything was stripped.
        """
        uid = int(user_id)
        original_len = len(self._segments)
        while self._segments:
            head = self._segments[0]
            if isinstance(head, AtSegment) and head.user_id == uid:
                self._segments.pop(0)
                continue
            if isinstance(head, TextSegment) and head.text.strip() == "":
                self._segments.pop(0)
                continue
            break
        return len(self._segments) != original_len

    def get(self, seg_type: str) -> list[Segment]:
        """All segments of a given type, e.g. ``message.get("image")``."""
        return [seg for seg in self._segments if seg.type == seg_type]

    def first(self, seg_type: str) -> Segment | None:
        for seg in self._segments:
            if seg.type == seg_type:
                return seg
        return None

    # ------------------------------------------------------------- dunders

    def append(self, segment: Segment | str) -> Message:
        if isinstance(segment, str):
            segment = TextSegment(type="text", data={"text": segment})
        self._segments.append(segment)
        return self

    def __iter__(self) -> Iterator[Segment]:
        return iter(self._segments)

    def __len__(self) -> int:
        return len(self._segments)

    def __getitem__(self, index: int | str) -> Any:
        if isinstance(index, str):
            items = self.get(index)
            if not items:
                raise KeyError(index)
            return items[0] if len(items) == 1 else items
        return self._segments[index]

    def __add__(self, other: Message | Segment | str) -> Message:
        merged = Message(self._segments)
        if isinstance(other, Message):
            merged._segments.extend(other._segments)
        elif isinstance(other, Segment):
            merged._segments.append(other)
        elif isinstance(other, str):
            merged.append(other)
        else:
            return NotImplemented
        return merged

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Message):
            return self._segments == other._segments
        if isinstance(other, str):
            return self.text == other
        return NotImplemented

    def __repr__(self) -> str:
        return f"Message({self._segments!r})"

    def __str__(self) -> str:
        return self.to_cq_string()


# Convenience constructors matching the spec's example usage ---------------

def Text(text: str) -> TextSegment:  # noqa: N802 - mirrors spec naming
    return TextSegment(type="text", data={"text": text})


def At(user_id: int | str) -> AtSegment:  # noqa: N802
    return AtSegment(type="at", data={"qq": user_id})


def AtAll() -> AtSegment:  # noqa: N802
    return AtSegment(type="at", data={"qq": "all"})


def Image(url: str | None = None, file: str | None = None) -> ImageSegment:  # noqa: N802
    data: dict[str, Any] = {}
    if url is not None:
        data["url"] = url
    if file is not None:
        data["file"] = file
    return ImageSegment(type="image", data=data)


def Face(face_id: int) -> FaceSegment:  # noqa: N802
    return FaceSegment(type="face", data={"id": face_id})


def Mface(
    emoji_id: str | None = None,
    emoji_package_id: str | None = None,
    key: str | None = None,
    summary: str | None = None,
    url: str | None = None,
) -> MfaceSegment:  # noqa: N802
    data: dict[str, Any] = {}
    if emoji_id is not None:
        data["emoji_id"] = emoji_id
    if emoji_package_id is not None:
        data["emoji_package_id"] = emoji_package_id
    if key is not None:
        data["key"] = key
    if summary is not None:
        data["summary"] = summary
    if url is not None:
        data["url"] = url
    return MfaceSegment(type="mface", data=data)


def Reply(message_id: int) -> ReplySegment:  # noqa: N802
    return ReplySegment(type="reply", data={"id": message_id})
