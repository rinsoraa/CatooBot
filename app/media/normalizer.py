"""Media normalizer (v1.1 §4/§2): classifies incoming message segments.

The rule that anchors everything else (v1.x 规格，原文缺失：媒体类型边界): a plain ``image`` is
never a sticker. Only ``face`` / ``mface`` / an image carrying explicit QQ
sticker metadata / a manual import are sticker sources.

QQ's own sticker markers count as that metadata (v2.0): an ``image`` with
``sub_type=1`` (动画表情) or a ``summary`` saying so IS a sticker — QQ is
stating it, we are not guessing.
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid

from app.media.models import MediaContent
from app.message.message import Message
from app.message.segment import (
    FaceSegment,
    ImageSegment,
    MfaceSegment,
    Segment,
)


class MessageMediaNormalizer:
    """Turns a :class:`Message` into structured :class:`MediaContent` items."""

    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("CatooBot.Media")

    def normalize(
        self,
        message: Message,
        *,
        source_message_id: str = "",
        source_user_id: str = "",
        source_group_id: str = "",
    ) -> list[MediaContent]:
        """Extract media segments (image/face/mface) from a message."""
        result: list[MediaContent] = []
        for segment in message:
            media = self._classify(segment)
            if media is None:
                continue
            media.media_id = f"media_{uuid.uuid4().hex[:12]}"
            media.source_message_id = source_message_id
            media.source_user_id = source_user_id
            media.source_group_id = source_group_id
            media.created_at = int(time.time())
            result.append(media)
        return result

    def _classify(self, segment: Segment) -> MediaContent | None:
        # 1. Native QQ emoji (face) — a sticker candidate, cheap to reuse.
        if isinstance(segment, FaceSegment):
            return MediaContent(
                media_type="native_face",
                source_type="qq_face",
                face_id=segment.face_id or 0,
            )
        # 2. QQ market/sticker emoji (mface).
        if isinstance(segment, MfaceSegment):
            return MediaContent(
                media_type="sticker",
                source_type="qq_mface",
                url=segment.url or "",
                emoji_id=segment.emoji_id or "",
                emoji_package_id=segment.emoji_package_id or "",
                emoji_key=segment.key or "",
                emoji_summary=segment.summary or "",
            )
        # 3. image: a sticker when QQ says so (emoji metadata / sub_type=1 /
        #    summary=[动画表情]); otherwise a plain image.
        if isinstance(segment, ImageSegment):
            data = segment.data
            has_emoji_meta = any(key in data for key in ("emoji_id", "emoji_package_id", "key"))
            summary = str(data.get("summary", "") or "")
            sub_type = str(data.get("sub_type", "") or "")
            qq_sticker = sub_type == "1" or "动画表情" in summary
            if has_emoji_meta or qq_sticker:
                clean_summary = summary.strip().strip("[]").strip()
                return MediaContent(
                    media_type="sticker",
                    source_type="qq_image",
                    url=segment.url or "",
                    file=segment.file or "",
                    emoji_id=str(data.get("emoji_id", "")),
                    emoji_package_id=str(data.get("emoji_package_id", "")),
                    emoji_key=str(data.get("key", "")),
                    emoji_summary=clean_summary or ("动画表情" if qq_sticker else ""),
                )
            return MediaContent(
                media_type="image",
                source_type="qq_image",
                url=segment.url or "",
                file=segment.file or "",
            )
        return None

    # ----------------------------------------------------------------- helpers

    @staticmethod
    def text_and_media(message: Message) -> tuple[str, list[MediaContent]]:
        """Split a message into its plain text plus its media."""
        from app.message.segment import TextSegment

        text = "".join(
            segment.data.get("text", "") for segment in message if isinstance(segment, TextSegment)
        )
        return text, MessageMediaNormalizer().normalize(message)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def average_hash(data: bytes, size: int = 8) -> str:
    """A tiny perceptual hash (aHash) from raw image bytes — pure-Python, best-effort.

    Returns '' when the bytes cannot be decoded as an image (no PIL dependency
    is required by the core). Dedup falls back to sha256 in that case.
    """
    try:
        import io

        from PIL import Image  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 - PIL is optional
        return ""
    try:
        image = Image.open(io.BytesIO(data)).convert("L").resize((size, size))
    except Exception:  # noqa: BLE001 - invalid image
        return ""
    pixels = list(image.getdata())
    average = sum(pixels) / max(1, len(pixels))
    bits = "".join("1" if pixel >= average else "0" for pixel in pixels)
    return format(int(bits, 2), "x").zfill(size * size // 4)


__all__ = [
    "MessageMediaNormalizer",
    "sha256_bytes",
    "sha256_file",
    "average_hash",
]
