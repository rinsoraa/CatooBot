"""Image memory bridge (Task 21, milestone 1): "I have seen this" becomes recallable.

Vision results used to live only in the sha256 cache and the prompt of the turn
they arrived in, so she could never remember what she had seen. This module
turns *worthwhile* photo understanding into one episodic memory per turn.

Boundaries, all deliberate (see docs/V3_IMAGE_MEMORY.md):

* stickers are assets, not memories — they stay in the sticker library;
* a photo only earns a memory when it carries information (text, people, two or
  more objects, or a scene the vision model was sure about);
* the content is the existing vision text, never raw bytes, URLs or thinking;
* one memory per turn (up to three photos summarised), fingerprinted so the same
  image never becomes a second memory.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("CatooBot.Media.Memory")

#: at most this many photos are summarised individually; more becomes a count
MAX_IMAGES_PER_TURN = 3

#: neither of these ever becomes a memory
SKIPPED_TYPES = frozenset({"sticker", "native_face"})


def deserves_memory(vision: Any) -> bool:
    """Information threshold — a blurry landscape deserves nothing."""
    if vision is None:
        return False
    if not str(getattr(vision, "summary", "") or "").strip():
        return False
    if list(getattr(vision, "ocr_text", []) or []):
        return True
    if int(getattr(vision, "people_count", 0) or 0) > 0:
        return True
    if len(list(getattr(vision, "objects", []) or [])) >= 2:
        return True
    scene = str(getattr(vision, "scene", "") or "").strip()
    return bool(scene) and float(getattr(vision, "confidence", 0.0) or 0.0) >= 0.6


def fingerprint(item: Any) -> str:
    """Short content fingerprint so the same image cannot be remembered twice."""
    sha = str(getattr(item, "sha256", "") or "")
    if sha:
        return f"[img:{sha[:16]}]"
    url = str(getattr(item, "url", "") or "")
    return f"[img:{abs(hash(url)) & 0xFFFFFFFF:08x}]" if url else ""


def compose(texts: list[str], *, total: int) -> str:
    """One turn's photos as a single memory line (never a list of everything)."""
    shown = [text.strip() for text in texts[:MAX_IMAGES_PER_TURN] if text.strip()]
    if not shown:
        return ""
    if total > len(shown):
        head = f"看过 {total} 张图片（记下前 {len(shown)} 张）"
    elif len(shown) == 1:
        head = "看过一张图片"
    else:
        head = f"看过 {len(shown)} 张图片"
    body = "；".join(f"第{i + 1}张：{text}" for i, text in enumerate(shown))
    return f"{head}：{body}"


async def record_image_memories(
    manager: Any,
    *,
    scope: str,
    ref: str,
    entries: list[tuple[Any, Any]],
    user_id: str | None = None,
    group_id: str | None = None,
) -> Any:
    """Store one episodic image memory for a turn (``None`` when not worth it).

    ``entries`` are ``(media_item, vision_result)`` pairs. Photos only — the
    caller is expected to have filtered stickers already, and they are filtered
    again here. Failures never propagate: this runs after a reply was sent.
    """
    if manager is None or not entries:
        return None
    worth: list[tuple[Any, Any]] = []
    for item, vision in entries:
        if str(getattr(item, "media_type", "")) in SKIPPED_TYPES:
            continue
        if deserves_memory(vision):
            worth.append((item, vision))
    if not worth:
        return None
    texts = [f"{vision.as_text()} {fingerprint(item)}".strip() for item, vision in worth]
    content = compose(texts, total=len(texts))
    if not content:
        return None
    confidence = max(float(getattr(vision, "confidence", 0.0) or 0.0) for _item, vision in worth)
    try:
        return await manager.remember(
            scope,
            ref,
            content,
            category="event",
            layer="episodic",
            importance=0.3 + 0.1 * min(3, len(worth)),
            confidence=min(0.8, 0.4 + confidence * 0.4),
            summary=str(getattr(worth[0][1], "summary", "") or ""),
            source="vision",
            user_id=user_id,
            group_id=group_id,
        )
    except Exception:  # noqa: BLE001 - a memory is never worth breaking a reply
        logger.exception("[Media.Memory] could not store an image memory")
        return None
