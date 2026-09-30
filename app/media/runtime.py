"""MediaRuntime facade (v1.1 §3): normalizer + vision + sticker runtime.

The single entry point the bot wires. It keeps the media-type boundary
(§2.1/§2.2): plain images go to image understanding only; stickers go to the
library. Acquisition runs in the background and never blocks a reply (§18).

v1.2 refinement: a plain image that *vision* classifies as a sticker/meme is
a legitimate acquisition source — it is reclassified into a sticker candidate
and, once saved, becomes a library asset like any other. The boundary that
never breaks is the SELECTOR: arbitrary photos are never usable as stickers;
only library assets are.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT
from app.media.indexer import StickerLibraryIndexer
from app.media.models import (
    AcquisitionDecision,
    ExpressionContext,
    ExpressionDecision,
    MediaContent,
    NativeFace,
    StickerAsset,
    VisionResult,
)
from app.media.normalizer import (
    MessageMediaNormalizer,
    average_hash,
    sha256_bytes,
)
from app.media.sticker import (
    ExpressionDecisionEngine,
    NativeFaceRegistry,
    StickerAcquisitionEvaluator,
    StickerAnalyzer,
    StickerLibrary,
    StickerSelector,
    StickerSender,
)
from app.media.vision import ImageUnderstandingRuntime
from app.message.message import Message

FileFetcher = Callable[[str], Awaitable[tuple[bytes, str]]]


async def _default_fetcher(url: str) -> tuple[bytes, str]:
    """Download one image; returns (bytes, content_type)."""
    import httpx

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        response = await client.get(url)
        response.raise_for_status()
        return response.content, str(response.headers.get("content-type", ""))


class MediaRuntime:
    def __init__(
        self,
        *,
        config: Any,
        engine: Any = None,
        database: Any = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
        file_fetcher: FileFetcher | None = None,
    ) -> None:
        self.config = config
        self._log = logger or logging.getLogger("CatooBot.Media")
        self._clock = clock
        self._fetch_file = file_fetcher or _default_fetcher
        self.normalizer = MessageMediaNormalizer(logger=self._log)

        self.vision = ImageUnderstandingRuntime(
            engine=engine, database=database, vision_model=config.vision_model,
            logger=self._log, clock=clock,
        )
        self.library = StickerLibrary(database=database, sticker_dir=config.sticker_dir,
                                      logger=self._log, clock=clock)
        self.analyzer = StickerAnalyzer(analysis_version=config.analysis_version)
        self.acquisition = StickerAcquisitionEvaluator()
        self.faces = NativeFaceRegistry()
        self.expression = ExpressionDecisionEngine(
            cooldown_seconds=config.sticker_cooldown_seconds,
            max_per_turn=config.max_stickers_per_turn,
            clock=clock,
        )
        self.selector = StickerSelector(library=self.library, faces=self.faces,
                                        logger=self._log, clock=clock)
        self.sender = StickerSender()
        self.indexer = StickerLibraryIndexer(
            library=self.library, analyzer=self.analyzer, vision=self.vision,
            import_dir=config.sticker_dir, analysis_version=config.analysis_version,
            analysis_model=config.vision_model, logger=self._log,
        )

    @property
    def enabled(self) -> bool:
        return bool(self.config.enabled)

    # ------------------------------------------------------------- incoming

    def normalize(self, message: Message, **provenance: str) -> list[MediaContent]:
        return self.normalizer.normalize(message, **provenance)

    async def understand_image(self, media: MediaContent) -> VisionResult:
        """Analyse a plain image (image understanding only — never the library)."""
        if media.media_type != "image":
            return VisionResult()
        image = media.url or ""
        if not image:
            return VisionResult()
        return await self.vision.analyze(image, sha256_hash=media.sha256)

    async def consider_collect(
        self, media: MediaContent, *, vision: VisionResult | None = None
    ) -> AcquisitionDecision:
        """Background: should the character keep this sticker? (never blocks chat)."""
        if not self.enabled or not self.config.auto_collect:
            return AcquisitionDecision(decision="reject", reason_codes=["disabled"])
        if not media.is_sticker_source:
            return AcquisitionDecision(decision="reject", reason_codes=["not_sticker"])
        asset = self.analyzer.analyze(media, vision)
        decision = await self.acquisition.evaluate(asset, self.library)
        if decision.decision == "save":
            asset.novelty = decision.novelty
            await self._persist_file(asset, media)
            await self.library.insert(asset)
        return decision

    # ------------------------------------------------- v1.2 vision collection

    @staticmethod
    def looks_like_sticker(vision: VisionResult) -> bool:
        """Conservative meme heuristic: vision explicitly calls it a sticker,
        or it carries caption text with a cute/expressive subject."""
        summary = vision.summary or ""
        if "表情包" in summary or "meme" in summary.lower():
            return True
        return bool(vision.ocr_text) and vision.confidence >= 0.5

    @staticmethod
    def as_sticker_candidate(item: MediaContent) -> MediaContent:
        """Reclassify one vision-confirmed meme into a sticker source."""
        return item.model_copy(
            update={"media_type": "sticker", "source_type": "vision_meme"}
        )

    async def _persist_file(self, asset: StickerAsset, media: MediaContent) -> None:
        """Download the image so 'saved' is literally true (metadata-only before).

        Native faces have no file; assets that already carry a real file skip.
        A failed download keeps the asset metadata-only and logs why.
        """
        if media.media_type == "native_face":
            return
        raw_url = media.url or ""
        if not raw_url or (asset.file_path and Path(asset.file_path).exists()):
            return
        try:
            data, content_type = await self._fetch_file(raw_url)
            if not data:
                raise ValueError("empty download")
            digest = sha256_bytes(data)
            mime = (content_type or "image/jpeg").split(";")[0].strip().lower()
            ext = {"image/jpeg": ".jpg", "image/png": ".png", "image/gif": ".gif",
                   "image/webp": ".webp"}.get(mime, ".jpg")
            directory = Path(self.config.sticker_dir)
            if not directory.is_absolute():
                directory = PROJECT_ROOT / directory
            directory = directory / "library"
            directory.mkdir(parents=True, exist_ok=True)
            target = directory / f"{digest}{ext}"
            target.write_bytes(data)
            asset.file_path = str(target)
            asset.file_name = target.name
            asset.mime_type = mime
            asset.file_size = len(data)
            asset.sha256 = digest
            if not asset.phash:
                asset.phash = average_hash(data)
            self._log.info("[Media.Sticker] downloaded %s (%d bytes)", target.name, len(data))
        except Exception:  # noqa: BLE001 - collection must never break chat
            self._log.warning(
                "[Media.Sticker] file download failed (%s) — keeping metadata only",
                raw_url[:80],
            )

    # ------------------------------------------------------------- outgoing

    def decide_expression(
        self, context: ExpressionContext, *, user_text: str, scope_key: str
    ) -> ExpressionDecision:
        return self.expression.decide(context, user_text=user_text, scope_key=scope_key)

    async def select(
        self, decision: ExpressionDecision, *, scope_key: str = ""
    ) -> StickerAsset | NativeFace | None:
        if not decision.selection_required:
            return None
        return await self.selector.select(decision, scope_key=scope_key)

    def build_message(self, item: StickerAsset | NativeFace) -> Message:
        return self.sender.build_message(item)

    def note_sent(self, scope_key: str, sticker_id: str = "") -> None:
        self.expression.note_sent(scope_key)
        if sticker_id:
            self.selector.note_selected(sticker_id)
