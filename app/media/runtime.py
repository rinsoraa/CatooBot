"""MediaRuntime facade (v1.1 §3): normalizer + vision + sticker runtime.

The single entry point the bot wires. It keeps the media-type boundary
(§2.1/§2.2): plain images go to image understanding only; stickers go to the
library. Acquisition runs in the background and never blocks a reply (§18).
"""

from __future__ import annotations

import logging
import time
from typing import Any

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
from app.media.normalizer import MessageMediaNormalizer
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


class MediaRuntime:
    def __init__(
        self,
        *,
        config: Any,
        engine: Any = None,
        database: Any = None,
        logger: logging.Logger | None = None,
        clock: Any = time.time,
    ) -> None:
        self.config = config
        self._log = logger or logging.getLogger("CatooBot.Media")
        self._clock = clock
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
            await self.library.insert(asset)
        return decision

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
