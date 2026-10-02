"""OneBot-inbound normalization (Phase 13 §7/§8/§35-§38).

    OneBot JSON → NormalizedMessageEvent → (gateway) → ExternalWorldEvent

The normalizer knows OneBot shapes and *nothing* about the sandbox; the
gateway knows the sandbox and never sees raw protocol JSON. Unknown fields and
unknown segment types are kept, never fatal (§38/§93): a future OneBot minor
extension must not break the whole message.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from pydantic import BaseModel, Field

#: segments this phase understands; anything else is preserved verbatim
KNOWN_SEGMENTS = frozenset({"text", "at", "image", "face", "reply"})


class NormalizedMessageEvent(BaseModel):
    """One inbound message, transport-neutral (§7)."""

    transport_event_id: str
    self_id: str = ""
    post_type: str = "message"
    message_type: str = "private"  # private / group
    message_id: str = ""
    user_id: str = ""
    group_id: str = ""
    #: who/what sent it, as the platform states it (never a person_id, §10)
    display_name: str = ""
    raw_message: str = ""
    plain_text: str = ""
    segments: list[dict[str, Any]] = Field(default_factory=list)
    #: the bot itself was addressed (@ / reply / private)
    mentioned_self: bool = False
    reply_to_bot: bool = False
    raw_time: float = 0.0
    ingest_time: float = 0.0
    ingest_seq: int = 0
    #: the *social* space this message belongs to (group or private lane, §11/§12)
    social_space_id: str = ""

    @property
    def is_group(self) -> bool:
        return self.message_type == "group"

    @property
    def lane_id(self) -> str:
        """§23: one lane per social space — never per person inside a group."""
        return self.social_space_id or f"private:{self.user_id}"


def _text_of(segments: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for segment in segments:
        if str(segment.get("type", "")) == "text":
            data = segment.get("data") or {}
            parts.append(str(data.get("text", "")))
    return "".join(parts).strip()


def _at_targets(segments: list[dict[str, Any]]) -> list[str]:
    targets: list[str] = []
    for segment in segments:
        if str(segment.get("type", "")) == "at":
            data = segment.get("data") or {}
            target = str(data.get("qq", "") or "")
            if target:
                targets.append(target)
    return targets


def digest_identity(*parts: str) -> str:
    """§14: a deterministic id from canonical fields — never a random UUID."""
    payload = json.dumps([str(part) for part in parts], ensure_ascii=False)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]  # noqa: S324 - identity


def normalize_message_event(
    raw: dict[str, Any],
    *,
    ingest_time: float,
    ingest_seq: int = 0,
    self_ids: tuple[str, ...] = (),
    social_space_map: dict[str, str] | None = None,
) -> NormalizedMessageEvent | None:
    """One RAW OneBot dict → normalized event; None when it cannot be one (§92)."""
    if not isinstance(raw, dict):
        return None
    post_type = str(raw.get("post_type", "") or "")
    if post_type and post_type != "message":
        return None
    message_type = str(raw.get("message_type", "") or "private")
    if message_type not in ("private", "group"):
        return None
    user_id = str(raw.get("user_id", "") or "")
    if not user_id:
        return None  # §92: no actor, no event — never a half-processed message
    self_id = str(raw.get("self_id", "") or "")
    message_id = str(raw.get("message_id", "") or "")
    group_id = str(raw.get("group_id", "") or "") if message_type == "group" else ""
    segments = _parse_segments(raw.get("message"))
    plain = _text_of(segments) or str(raw.get("raw_message", "") or "")
    mentioned_self = bool(self_ids) and any(target in self_ids for target in _at_targets(segments))
    display_name = ""
    sender = raw.get("sender")
    if isinstance(sender, dict):
        display_name = str(sender.get("card") or sender.get("nickname") or "")
    raw_time = float(raw.get("time", 0) or 0.0)
    transport_event_id = (
        f"message:{self_id}:{message_id}"
        if message_id
        else f"message:{digest_identity(self_id, user_id, group_id, str(raw.get('time', '')))}"
    )
    social_space_id = ""
    if group_id:
        mapped = (social_space_map or {}).get(group_id, "")
        social_space_id = str(mapped or f"qq:{group_id}")
    return NormalizedMessageEvent(
        transport_event_id=transport_event_id,
        self_id=self_id,
        post_type=post_type or "message",
        message_type=message_type,
        message_id=message_id,
        user_id=user_id,
        group_id=group_id,
        display_name=display_name,
        raw_message=str(raw.get("raw_message", "") or ""),
        plain_text=plain,
        segments=segments,
        mentioned_self=mentioned_self,
        reply_to_bot=_replies_to_self(segments, self_ids),
        raw_time=raw_time,
        ingest_time=float(ingest_time),
        ingest_seq=int(ingest_seq),
        social_space_id=social_space_id,
    )


def _parse_segments(message: Any) -> list[dict[str, Any]]:
    """OneBot array / CQ-string / plain text → segment dicts (unknown kept)."""
    if isinstance(message, list):
        return [
            {"type": str(item.get("type", "")), "data": dict(item.get("data") or {})}
            for item in message
            if isinstance(item, dict)
        ]
    if isinstance(message, str) and message:
        return [{"type": "text", "data": {"text": message}}]
    return []


def _replies_to_self(segments: list[dict[str, Any]], self_ids: tuple[str, ...]) -> bool:
    for segment in segments:
        if str(segment.get("type", "")) != "reply":
            continue
        data = segment.get("data") or {}
        # OneBot only gives the reply's message_id; the parser upstream may add
        # the resolved sender — treat an explicit self sender as authoritative.
        sender = str(data.get("user_id", "") or data.get("sender", "") or "")
        if sender and sender in self_ids:
            return True
    return False


def normalize_typed_event(
    event: Any,
    *,
    ingest_time: float,
    ingest_seq: int = 0,
    self_ids: tuple[str, ...] = (),
    social_space_map: dict[str, str] | None = None,
) -> NormalizedMessageEvent | None:
    """The parser's typed ``MessageEvent`` → normalized event (§7).

    The adapter converts protocol → typed; this turns typed → normalized, so
    the sandbox never meets either shape.
    """
    if str(getattr(event, "post_type", "")) != "message":
        return None
    message_type = str(getattr(event, "message_type", "") or "")
    if message_type not in ("private", "group"):
        return None
    user_id = str(getattr(event, "user_id", "") or "")
    if not user_id:
        return None
    segments = segments_from_typed_event(event)
    sender = getattr(event, "sender", None)
    display_name = str(getattr(sender, "card", "") or getattr(sender, "nickname", "") or "")
    raw_event = getattr(event, "raw_event", {}) or {}
    message_id = str(getattr(event, "message_id", "") or "")
    self_id = str(raw_event.get("self_id", "") or "")
    group_id = str(getattr(event, "group_id", "") or "") if message_type == "group" else ""
    return normalize_message_event(
        {
            "post_type": "message",
            "message_type": message_type,
            "message_id": message_id,
            "user_id": user_id,
            "group_id": group_id,
            "self_id": self_id,
            "sender": {"card": getattr(sender, "card", ""), "nickname": display_name},
            "message": segments,
            "raw_message": str(getattr(event, "raw_message", "") or ""),
            "time": float(raw_event.get("time", 0) or 0.0),
        },
        ingest_time=ingest_time,
        ingest_seq=ingest_seq,
        self_ids=self_ids,
        social_space_map=social_space_map,
    )


def segments_from_typed_event(event: Any) -> list[dict[str, Any]]:
    """A typed :class:`app.message.event.MessageEvent` → normalized segments."""
    segments: list[dict[str, Any]] = []
    for segment in getattr(getattr(event, "message", None), "segments", []) or []:
        kind = type(segment).__name__.replace("Segment", "").lower()
        data = dict(getattr(segment, "data", {}) or {})
        segments.append({"type": kind or "unknown", "data": data})
    return segments
