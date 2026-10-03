"""Sticker / media admin service (v1.1 §37-§41, W5 §7.4).

WebUI-only: library browsing, search, tag/emotion filters, disable / re-analyse,
and the expression + acquisition debug views. Nothing here sends to QQ.

W5 additions (all read-only projections): the row keeps the asset fields the
model already has (created_at / safety_status / origin user / file path) and a
``valid`` integrity flag (the file reference actually resolves), plus ``offset``
paging and a per-status ``count`` for list totals.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot


class StickerAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Media")

    @property
    def media(self):
        return getattr(self.bot, "media", None)

    @property
    def available(self) -> bool:
        media = self.media
        return media is not None and getattr(media, "enabled", False)

    async def list_stickers(
        self,
        *,
        query: str = "",
        emotion: str = "",
        intent: str = "",
        status: str = "",
        limit: int = 200,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Library rows, filtered and paged.

        ``search`` is a *ranking* helper (score 0 rows included), so emotion /
        intent are enforced as real filters here — a filter must match, not
        merely rank. Text-only search keeps the ranked list.
        """
        if not self.available:
            return []
        offset = max(0, int(offset))
        fetch = max(1, int(limit)) + offset
        if query or emotion or intent:
            assets = await self.media.library.search(
                query=query, emotion=emotion, intent=intent, limit=fetch
            )
            if emotion:
                assets = [asset for asset in assets if emotion in asset.emotion_tags]
            if intent:
                assets = [asset for asset in assets if intent in asset.intent_tags]
            if status:
                assets = [asset for asset in assets if asset.status == status]
        else:
            assets = await self.media.library.all(status=status or "active", limit=fetch)
        return [self._asset_row(asset) for asset in assets[offset:]]

    async def count(self, status: str = "active") -> int:
        """Number of assets in one status (``0`` when media is off)."""
        if not self.available:
            return 0
        return await self.media.library.count(status)

    async def sticker(self, sticker_id: str) -> dict[str, Any] | None:
        """One asset row by id (``None`` when unknown / media off)."""
        if not self.available:
            return None
        asset = await self.media.library.get(sticker_id)
        return self._asset_row(asset) if asset is not None else None

    def _asset_row(self, asset: Any) -> dict[str, Any]:
        return {
            "id": asset.id,
            "file": asset.file_path,
            "file_name": asset.file_name or asset.emoji_key or asset.visual_summary,
            "mime_type": asset.mime_type,
            "file_size": int(asset.file_size or 0),
            "emoji_id": asset.emoji_id,
            "visual_summary": asset.visual_summary,
            "ocr_text": asset.ocr_text,
            "emotion_tags": asset.emotion_tags,
            "intent_tags": asset.intent_tags,
            "general_tags": asset.general_tags,
            "origin": asset.origin,
            "origin_user_id": asset.origin_user_id,
            "origin_group_id": asset.origin_group_id,
            "usage_count": int(asset.usage_count or 0),
            "last_used_at": int(asset.last_used_at or 0) or None,
            "created_at": int(asset.created_at or 0) or None,
            "safety_status": asset.safety_status,
            "status": asset.status,
            "valid": self._file_present(asset),
            "expressiveness": round(asset.expressiveness, 2),
            "novelty": round(asset.novelty, 2),
            "quality_score": round(asset.quality_score, 2),
        }

    def _file_present(self, asset: Any) -> bool:
        """Best-effort integrity check: does the stored file path resolve?

        ``file_path`` may be relative to the project root or absolute; anything
        unreadable is reported as ``False`` rather than guessed.
        """
        raw = str(getattr(asset, "file_path", "") or "")
        if not raw:
            return False
        try:
            path = Path(raw)
            if path.is_file():
                return True
            library = getattr(self.media, "library", None)
            base = getattr(library, "dir", None)
            return bool(base is not None and (Path(base) / raw).is_file())
        except OSError:
            return False

    async def stats(self) -> dict[str, Any]:
        if not self.available:
            return {"enabled": False}
        total = await self.media.library.count("active")
        disabled = await self.media.library.count("disabled")
        return {
            "enabled": True,
            "total": total,
            "disabled": disabled,
            "indexer": self.media.indexer.state if hasattr(self.media, "indexer") else {},
            "native_faces": len(self.media.faces.all()),
            "vision_enabled": getattr(self.media.vision, "enabled", False),
        }

    async def set_status(self, sticker_id: str, status: str) -> bool:
        if not self.available:
            return False
        return await self.media.library.set_status(sticker_id, status)

    async def reindex(self) -> dict[str, int]:
        if not self.available:
            return {}
        return await self.media.indexer.scan(full=True)

    async def rescan(self) -> dict[str, int]:
        if not self.available:
            return {}
        return await self.media.indexer.scan(full=False)
