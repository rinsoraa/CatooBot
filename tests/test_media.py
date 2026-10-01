"""Media + sticker runtime tests (v1.1 §61).

Core invariants: a plain image is never a sticker; mface/face/image-with-emoji
metadata are sticker sources; acquisition is save/reject/defer (dedup by sha256);
the expression decision is rule-driven and never fires for neutral text; and
the indexer is incremental.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from app.ai.models import ChatMessage
from app.media.models import ExpressionContext, MediaContent, VisionResult
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
from app.message.message import Face, Image, Message, Mface, Text


def make_library(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'media.db'}"))
    return database, None


class TestNormalizer:
    def test_plain_image_is_image_not_sticker(self) -> None:
        message = Message([Text("看这个"), Image(url="http://x/photo.jpg")])
        media = MessageMediaNormalizer().normalize(message)
        assert len(media) == 1
        assert media[0].media_type == "image"
        assert media[0].is_sticker_source is False

    def test_face_is_native_face(self) -> None:
        message = Message([Face(66)])
        media = MessageMediaNormalizer().normalize(message)
        assert media[0].media_type == "native_face"
        assert media[0].face_id == 66

    def test_mface_is_sticker(self) -> None:
        message = Message(
            [Mface(emoji_id="1", emoji_package_id="2", key="cat", summary="猫猫无语")]
        )
        media = MessageMediaNormalizer().normalize(message)
        assert media[0].media_type == "sticker"
        assert media[0].emoji_summary == "猫猫无语"
        assert media[0].is_sticker_source is True

    def test_image_with_emoji_metadata_is_sticker(self) -> None:
        from app.message.segment import ImageSegment

        message = Message(
            [ImageSegment(type="image", data={"url": "http://x.gif", "emoji_id": "9"})]
        )
        media = MessageMediaNormalizer().normalize(message)
        assert media[0].media_type == "sticker"

    def test_unknown_segment_ignored(self) -> None:
        from app.message.segment import Segment

        message = Message([Segment(type="video", data={"file": "x.mp4"}), Text("hi")])
        media = MessageMediaNormalizer().normalize(message)
        assert media == []

    def test_text_and_media_split(self) -> None:
        message = Message([Text("看这个"), Image(url="http://x/1.png")])
        text, media = MessageMediaNormalizer.text_and_media(message)
        assert text == "看这个"
        assert len(media) == 1 and media[0].media_type == "image"


class TestChatMessageMultimodal:
    def test_text_only_keeps_string_content(self) -> None:
        message = ChatMessage.user("你好")
        assert message.to_openai() == {"role": "user", "content": "你好"}

    def test_with_images_emits_list_content(self) -> None:
        message = ChatMessage.user_with_images("这是什么", ["data:image/png;base64,xxx"])
        payload = message.to_openai()
        assert isinstance(payload["content"], list)
        assert payload["content"][0] == {"type": "text", "text": "这是什么"}
        assert payload["content"][1]["type"] == "image_url"


class TestStickerLibrary:
    async def test_insert_and_dedup_by_sha256(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'lib.db'}"))
        await database.connect()
        library = StickerLibrary(database=database, sticker_dir=str(tmp_path / "stickers"))
        asset = StickerAnalyzer().analyze(
            MediaContent(
                media_type="sticker",
                source_type="qq_mface",
                sha256="abc123",
                emoji_id="1",
                emoji_package_id="2",
                emoji_summary="猫猫无语",
            ),
            None,
        )
        await library.insert(asset)
        assert await library.by_sha256("abc123") is not None
        assert await library.count("active") == 1
        await database.close()

    async def test_search_by_emotion(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'lib2.db'}"))
        await database.connect()
        library = StickerLibrary(database=database, sticker_dir=str(tmp_path / "stickers"))
        analyzer = StickerAnalyzer()
        happy = analyzer.analyze(
            MediaContent(media_type="sticker", emoji_summary="一只猫在笑"), None
        )
        happy.emotion_tags = ["开心"]
        sad = analyzer.analyze(MediaContent(media_type="sticker", emoji_summary="一只猫在哭"), None)
        sad.emotion_tags = ["难过"]
        await library.insert(happy)
        await library.insert(sad)
        results = await library.search(emotion="开心")
        assert results and results[0].id == happy.id
        await database.close()


class TestAcquisition:
    async def test_duplicate_rejected(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'acq.db'}"))
        await database.connect()
        library = StickerLibrary(database=database, sticker_dir=str(tmp_path / "stickers"))
        media = MediaContent(media_type="sticker", sha256="dup", emoji_summary="猫猫")
        asset = StickerAnalyzer().analyze(media, None)
        await library.insert(asset)
        decision = await StickerAcquisitionEvaluator().evaluate(asset, library)
        assert decision.decision == "reject"
        assert "duplicate_sha256" in decision.reason_codes
        await database.close()

    async def test_novel_saved(self, tmp_path) -> None:
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'acq2.db'}"))
        await database.connect()
        library = StickerLibrary(database=database, sticker_dir=str(tmp_path / "stickers"))
        media = MediaContent(media_type="sticker", sha256="new", emoji_summary="一只生无可恋的猫")
        asset = StickerAnalyzer().analyze(media, None)
        asset.expressiveness = 0.8
        decision = await StickerAcquisitionEvaluator().evaluate(asset, library)
        assert decision.decision == "save"
        await database.close()


class TestExpressionDecision:
    def _engine(self) -> ExpressionDecisionEngine:
        return ExpressionDecisionEngine(cooldown_seconds=60)

    def test_neutral_text_is_text_only(self) -> None:
        decision = self._engine().decide(
            ExpressionContext(), user_text="明天天气怎么样", scope_key="private:1"
        )
        assert decision.response_mode == "text"
        assert decision.selection_required is False

    def test_incoming_sticker_is_expression_cue(self) -> None:
        context = ExpressionContext(
            incoming_media_type="sticker", incoming_sticker_semantics="猫猫"
        )
        decision = self._engine().decide(context, user_text="", scope_key="private:1")
        assert decision.response_mode == "text_and_sticker"
        assert decision.selection_required is True

    def test_emotional_text_is_expression_cue(self) -> None:
        decision = self._engine().decide(
            ExpressionContext(), user_text="哈哈哈哈", scope_key="private:1"
        )
        assert decision.selection_required is True

    def test_cooldown_suppresses(self) -> None:
        engine = self._engine()
        engine.note_sent("private:1")
        decision = engine.decide(
            ExpressionContext(incoming_media_type="sticker"), user_text="", scope_key="private:1"
        )
        assert decision.response_mode == "text"


class TestSelectorAndSender:
    def test_native_face_selected_by_emotion(self) -> None:
        registry = NativeFaceRegistry()
        library = _NullLibrary()
        selector = StickerSelector(library=library, faces=registry)
        from app.media.models import ExpressionDecision

        decision = ExpressionDecision(
            response_mode="text_and_sticker", emotion="开心", selection_required=True
        )
        # run in a fresh loop since select is async
        import asyncio

        item = asyncio.run(selector.select(decision))
        assert item is not None and getattr(item, "face_id", 0) in (14, 274)

    def test_sender_builds_face_message(self) -> None:
        sender = StickerSender()
        face = NativeFaceRegistry().get(14)
        message = sender.build_message(face)
        assert message[0].type == "face"
        assert message[0].data["id"] == 14

    def test_sender_builds_mface_message(self) -> None:
        from app.media.models import StickerAsset

        asset = StickerAsset(emoji_id="1", emoji_package_id="2", emoji_key="k", visual_summary="猫")
        message = StickerSender().build_message(asset)
        assert message[0].type == "mface"


class _NullLibrary:
    async def search(self, **kwargs):
        return []


class TestIndexer:
    async def test_incremental_scan_skips_unchanged(self, tmp_path) -> None:

        from app.config.settings import DatabaseConfig
        from app.database.database import Database
        from app.media.indexer import StickerLibraryIndexer

        import_dir = tmp_path / "stickers"
        import_dir.mkdir()
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'idx.db'}"))
        await database.connect()
        library = StickerLibrary(database=database, sticker_dir=str(import_dir))
        analyzer = StickerAnalyzer(analysis_version="v1")
        indexer = StickerLibraryIndexer(
            library=library,
            analyzer=analyzer,
            vision=None,
            import_dir=str(import_dir),
            analysis_version="v1",
        )
        # first scan: one file -> analysed
        (import_dir / "cat.png").write_bytes(b"not-a-real-png")
        stats = await indexer.scan()
        assert stats["done"] == 1
        # second scan: unchanged -> skipped as duplicate, not re-analysed
        stats2 = await indexer.scan()
        assert stats2["duplicate"] == 1 and stats2["done"] == 0
        await database.close()


class TestVisionCollection:
    """v1.2: vision-confirmed memes are collected AND the file really lands."""

    def _runtime(self, tmp_path, fetcher):
        from app.config.settings import MediaConfig
        from app.media.runtime import MediaRuntime

        return MediaRuntime(
            config=MediaConfig(sticker_dir=str(tmp_path / "stickers")),
            engine=None,
            database=self._db,
            file_fetcher=fetcher,
        )

    async def _make_db(self, tmp_path):
        from app.config.settings import DatabaseConfig
        from app.database.database import Database

        self._db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'm.db'}"))
        await self._db.connect()

    async def test_vision_meme_is_collected_and_file_downloaded(self, tmp_path) -> None:
        png = b"\x89PNG\r\n\x1a\nfake-cat-image"

        async def fetcher(url):  # type: ignore[no-untyped-def]
            return png, "image/png"

        await self._make_db(tmp_path)
        runtime = self._runtime(tmp_path, fetcher)
        item = self._image(url="https://multimedia.example/cat.png")
        vision = VisionResult(
            summary="一只戴粉色小熊帽的猫，可爱表情包",
            ocr_text=["你在干森么呢"],
            confidence=0.9,
        )
        assert runtime.looks_like_sticker(vision)
        candidate = runtime.as_sticker_candidate(item)
        assert candidate.is_sticker_source

        decision = await runtime.consider_collect(candidate, vision=vision)
        assert decision.decision == "save"
        assets = await runtime.library.all()
        assert len(assets) == 1
        saved = assets[0]
        saved_path = Path(saved.file_path)
        assert saved_path.exists(), "the sticker file must actually be on disk"
        assert saved_path.parent.name == "library"
        assert saved.file_size == len(png)
        assert saved.sha256 == hashlib.sha256(png).hexdigest()
        assert saved.ocr_text == "你在干森么呢"
        await self._db.close()

    async def test_duplicate_download_rejected_by_sha256(self, tmp_path) -> None:
        png = b"\x89PNG\r\n\x1a\nfake-cat-image"

        async def fetcher(url):  # type: ignore[no-untyped-def]
            return png, "image/png"

        await self._make_db(tmp_path)
        runtime = self._runtime(tmp_path, fetcher)
        item = runtime.as_sticker_candidate(self._image(url="https://x.example/a.png"))
        vision = VisionResult(summary="可爱表情包", confidence=0.9)
        await runtime.consider_collect(item, vision=vision)
        decision = await runtime.consider_collect(item, vision=vision)
        assert decision.decision == "reject"
        await self._db.close()

    async def test_plain_photo_is_not_collected(self, tmp_path) -> None:
        await self._make_db(tmp_path)
        runtime = self._runtime(tmp_path, None)
        vision = VisionResult(summary="一张办公室白板照片", confidence=0.8)
        assert not runtime.looks_like_sticker(vision)

    async def test_failed_download_keeps_metadata(self, tmp_path) -> None:
        async def broken(url):  # type: ignore[no-untyped-def]
            raise RuntimeError("network down")

        await self._make_db(tmp_path)
        runtime = self._runtime(tmp_path, broken)
        item = runtime.as_sticker_candidate(self._image(url="https://x.example/b.png"))
        vision = VisionResult(summary="可爱表情包", confidence=0.9)
        decision = await runtime.consider_collect(item, vision=vision)
        assert decision.decision == "save"
        assets = await runtime.library.all()
        assert assets[0].file_path == "" or not Path(assets[0].file_path).exists()
        await self._db.close()

    @staticmethod
    def _image(url: str) -> MediaContent:
        return MediaContent(media_type="image", source_type="qq_image", url=url, is_animated=True)
