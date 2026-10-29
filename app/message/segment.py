"""Typed message segments for CatooBot.

Segments mirror the OneBot 11 message-segment structure (``type`` + ``data``)
so conversion is lossless, while exposing typed accessors for the common kinds
(text / at / image / reply). Unknown segment types are preserved as generic
:class:`Segment` and can still be forwarded back to the protocol untouched.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


def _unescape_cq(value: str) -> str:
    """Reverse OneBot CQ-code escaping inside parameter values."""
    return (
        value.replace("&#91;", "[")
        .replace("&#93;", "]")
        .replace("&comma;", ",")
        .replace("&amp;", "&")
    )


def _escape_cq(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace(",", "&comma;")
        .replace("[", "&#91;")
        .replace("]", "&#93;")
    )


class Segment(BaseModel):
    """Base message segment. ``data`` keeps the raw protocol payload."""

    type: str
    data: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_onebot(cls, raw: dict[str, Any]) -> Segment:
        """Build the most specific segment subclass for a raw OneBot segment."""
        seg_type = str(raw.get("type", ""))
        data = dict(raw.get("data") or {})
        segment_cls = _TYPED_SEGMENTS.get(seg_type, cls)
        return segment_cls(type=seg_type, data=data)

    def to_onebot(self) -> dict[str, Any]:
        """Serialize back to the OneBot array-format segment."""
        return {"type": self.type, "data": self.data}

    def to_cq(self) -> str:
        """Serialize to CQ-code string form (used for debug / fallback)."""
        if self.type == "text":
            return str(self.data.get("text", ""))
        params = ",".join(f"{k}={_escape_cq(str(v))}" for k, v in self.data.items())
        return f"[CQ:{self.type}{',' + params if params else ''}]"


class TextSegment(Segment):
    @property
    def text(self) -> str:
        return str(self.data.get("text", ""))


class AtSegment(Segment):
    @property
    def user_id(self) -> int | str:
        """The @-ed user id, or the string ``"all"`` for @全体成员."""
        raw = self.data.get("qq", "all")
        if raw == "all":
            return "all"
        try:
            return int(raw)
        except (TypeError, ValueError):
            return raw

    @property
    def is_all(self) -> bool:
        return self.data.get("qq") == "all"


class ImageSegment(Segment):
    @property
    def url(self) -> str | None:
        raw = self.data.get("url")
        return str(raw) if raw is not None else None

    @property
    def file(self) -> str | None:
        raw = self.data.get("file")
        return str(raw) if raw is not None else None


class ReplySegment(Segment):
    @property
    def message_id(self) -> int | None:
        raw = self.data.get("id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None


class FaceSegment(Segment):
    """QQ native emoji (``type=face``)."""

    @property
    def face_id(self) -> int | None:
        raw = self.data.get("id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            return None


class MfaceSegment(Segment):
    """QQ market/sticker emoji (``type=mface``), or an image carrying its metadata."""

    @property
    def emoji_id(self) -> str | None:
        raw = self.data.get("emoji_id")
        return str(raw) if raw is not None else None

    @property
    def emoji_package_id(self) -> str | None:
        raw = self.data.get("emoji_package_id")
        return str(raw) if raw is not None else None

    @property
    def key(self) -> str | None:
        raw = self.data.get("key")
        return str(raw) if raw is not None else None

    @property
    def summary(self) -> str:
        return str(self.data.get("summary", ""))

    @property
    def url(self) -> str | None:
        raw = self.data.get("url")
        return str(raw) if raw is not None else None


class VideoSegment(Segment):
    """QQ video message (``type=video``).

    Recognised as a *typed* segment so the character receives a readable
    placeholder instead of an invisible blank. The bot never downloads or
    transcodes video — ``url``/``file`` are exposed read-only only.
    """

    @property
    def url(self) -> str | None:
        raw = self.data.get("url")
        return str(raw) if raw is not None else None

    @property
    def file(self) -> str | None:
        raw = self.data.get("file")
        return str(raw) if raw is not None else None


class FileSegment(Segment):
    """QQ file message (``type=file``). Typed for a readable placeholder; never downloaded."""

    @property
    def file(self) -> str | None:
        raw = self.data.get("file")
        return str(raw) if raw is not None else None

    @property
    def name(self) -> str | None:
        raw = self.data.get("file") or self.data.get("name")
        return str(raw) if raw is not None else None


_TYPED_SEGMENTS: dict[str, type[Segment]] = {
    "text": TextSegment,
    "at": AtSegment,
    "image": ImageSegment,
    "reply": ReplySegment,
    "face": FaceSegment,
    "mface": MfaceSegment,
    "video": VideoSegment,
    "file": FileSegment,
}
