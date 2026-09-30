"""Sticker / media admin service (v1.1 §37-§41).

WebUI-only: library browsing, search, tag/emotion filters, disable / re-analyse,
and the expression + acquisition debug views. Nothing here sends to QQ.
"""

from __future__ import annotations

import logging
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
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        if query or emotion or intent:
            assets = await self.media.library.search(
                query=query, emotion=emotion, intent=intent, limit=limit
            )
        elif status:
            assets = await self.media.library.all(status=status, limit=limit)
        else:
            assets = await self.media.library.all(status="active", limit=limit)
        return [self._asset_row(asset) for asset in assets]

    def _asset_row(self, asset: Any) -> dict[str, Any]:
        return {
            "id": asset.id,
            "file_name": asset.file_name or asset.emoji_key or asset.visual_summary,
            "emoji_id": asset.emoji_id,
            "visual_summary": asset.visual_summary,
            "emotion_tags": asset.emotion_tags,
            "intent_tags": asset.intent_tags,
            "general_tags": asset.general_tags,
            "origin": asset.origin,
            "usage_count": asset.usage_count,
            "last_used_at": asset.last_used_at,
            "status": asset.status,
            "expressiveness": round(asset.expressiveness, 2),
            "novelty": round(asset.novelty, 2),
            "quality_score": round(asset.quality_score, 2),
        }

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
