"""Sticker library indexer (v1.1 §14-§17/§47/§69).

Incrementally scans the import directory on startup: files are hashed and only
*new or changed* files are analysed (sha256 + analysis_version + model). It runs
in the background so a folder full of GIFs can never delay QQ from coming up.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

from app.config.settings import project_path
from app.media.models import MediaContent
from app.media.normalizer import average_hash, sha256_bytes
from app.media.sticker import StickerAnalyzer, StickerLibrary

_STICKER_EXTS = {".gif", ".png", ".jpg", ".jpeg", ".webp", ".bmp"}


class StickerLibraryIndexer:
    def __init__(
        self,
        *,
        library: StickerLibrary,
        analyzer: StickerAnalyzer,
        vision: Any = None,
        import_dir: str = "data/stickers",
        analysis_version: str = "v1",
        analysis_model: str = "",
        logger: logging.Logger | None = None,
    ) -> None:
        self._library = library
        self._analyzer = analyzer
        self._vision = vision
        self._dir = project_path(import_dir)
        self._version = analysis_version
        self._model = analysis_model
        self._log = logger or logging.getLogger("CatooBot.Media")
        #: indexer state for the WebUI
        self.state = {"pending": 0, "analyzing": 0, "done": 0, "failed": 0, "duplicate": 0}

    def discover_files(self) -> list[Path]:
        if not self._dir.exists():
            return []
        return [
            path
            for path in sorted(self._dir.rglob("*"))
            if path.is_file() and path.suffix.lower() in _STICKER_EXTS
        ]

    async def scan(self, *, full: bool = False) -> dict[str, int]:
        """Incremental scan: analyse only new / changed / stale files (§15)."""
        self.state = {"pending": 0, "analyzing": 0, "done": 0, "failed": 0, "duplicate": 0}
        for _path in self.discover_files():
            self.state["pending"] += 1
        for path in self.discover_files():
            try:
                await self._index_one(path, full=full)
            except Exception as exc:  # noqa: BLE001 - one bad file must not stop the scan
                self._log.warning("[Media.Indexer] failed on %s: %s", path.name, exc)
                self.state["failed"] += 1
                self.state["pending"] -= 1
        return dict(self.state)

    async def _index_one(self, path: Path, *, full: bool) -> None:
        data = path.read_bytes()
        sha = sha256_bytes(data)
        existing = await self._library.by_sha256(sha)
        if existing is not None and not full and existing.analysis_version == self._version:
            # unchanged, already analysed at this version — skip (§15)
            self.state["duplicate"] += 1
            self.state["pending"] -= 1
            return

        self.state["analyzing"] += 1
        mime = _mime(path.suffix)
        media = MediaContent(
            media_type="sticker",
            source_type="manual_import",
            file=str(path),
            mime_type=mime,
            file_size=len(data),
            sha256=sha,
            is_animated=path.suffix.lower() in (".gif", ".webp"),
        )
        vision_result = None
        if self._vision is not None and getattr(self._vision, "enabled", False):
            try:
                from app.media.vision import data_url_from_bytes

                vision_result = await self._vision.analyze(
                    data_url_from_bytes(data, mime), sha256_hash=sha
                )
            except Exception:  # noqa: BLE001 - analysis is best-effort
                self._log.debug("[Media.Indexer] vision skipped for %s", path.name)
        asset = self._analyzer.analyze(media, vision_result)
        asset.phash = average_hash(data)
        asset.analysis_model = self._model
        asset.analyzed_at = int(time.time())
        asset.origin = "manual_import"
        if existing is not None:
            asset.id = existing.id
            asset.created_at = existing.created_at
            asset.usage_count = existing.usage_count
            await self._library.update(asset)
        else:
            await self._library.insert(asset)
        self.state["done"] += 1
        self.state["analyzing"] -= 1
        self.state["pending"] -= 1


def _mime(suffix: str) -> str:
    return {
        ".gif": "image/gif",
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
    }.get(suffix.lower(), "application/octet-stream")
