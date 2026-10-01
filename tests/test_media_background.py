"""Option A: QQ-metadata stickers get background recognition + collection.

The observed gap: a group sticker with no @ never reached vision (which hangs
off the reply path), so the log showed nothing. Now QQ-marked stickers
(sub_type=1 / summary=[动画表情]) are sticker sources, and a throttled
background pass recognizes + possibly keeps them — regardless of whether she
replies. The reply path reuses the cached summary for free.
"""

from __future__ import annotations

import asyncio
import logging

from app.config.settings import DatabaseConfig, MediaConfig
from app.database.database import Database
from app.media.models import MediaContent
from app.media.normalizer import MessageMediaNormalizer
from app.media.runtime import MediaRuntime
from app.message.message import Message
from app.message.segment import ImageSegment


def image(data: dict) -> Message:
    return Message([ImageSegment(type="image", data=data)])


class FakeClock:
    def __init__(self, start: float = 1_700_000_000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeVisionEngine:
    """Stands in for the AI engine; the real vision runtime (with its sha256
    cache) wraps it, so the cache/one-call behaviour is genuinely exercised."""

    enabled = True

    def __init__(self, summary: str) -> None:
        self.summary = summary
        self.ocr: list[str] = []
        self.calls = 0

    async def chat(self, request: object) -> object:
        import json

        from app.ai.models import AIResponse

        self.calls += 1
        return AIResponse(
            content=json.dumps(
                {"summary": self.summary, "ocr_text": self.ocr, "confidence": 0.9}
            ),
            model="fake", provider="fake",
        )


def make_vision(engine: FakeVisionEngine, db: Database):  # type: ignore[no-untyped-def]
    from app.media.vision import ImageUnderstandingRuntime

    return ImageUnderstandingRuntime(
        engine=engine, database=db, vision_model="fake-vision"
    )


def make_runtime(tmp_path, *, fetcher=None, clock=None, cap: int = 20):
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'bg.db'}"))
    config = MediaConfig(
        sticker_dir=str(tmp_path / "stickers"),
        background_vision_max_per_hour=cap,
    )
    runtime = MediaRuntime(
        config=config, engine=None, database=db,
        clock=clock or FakeClock(), file_fetcher=fetcher,
    )
    return runtime, db


PNG = b"\x89PNG\r\n\x1a\nfake-sticker-bytes"


class TestQqStickerDetection:
    def test_sub_type_one_is_a_sticker(self) -> None:
        media = MessageMediaNormalizer().normalize(
            image({"file": "a.jpg", "sub_type": "1", "summary": "[动画表情]",
                   "url": "https://x/a.jpg"})
        )
        assert media[0].media_type == "sticker"
        assert media[0].is_sticker_source is True
        assert media[0].emoji_summary == "动画表情"

    def test_animated_summary_alone_is_a_sticker(self) -> None:
        media = MessageMediaNormalizer().normalize(
            image({"file": "a.jpg", "sub_type": "0", "summary": "[动画表情]"})
        )
        assert media[0].media_type == "sticker"

    def test_plain_photo_stays_an_image(self) -> None:
        media = MessageMediaNormalizer().normalize(
            image({"file": "a.jpg", "sub_type": "0", "summary": "", "url": "https://x/p.jpg"})
        )
        assert media[0].media_type == "image"
        assert media[0].is_sticker_source is False


class TestThrottle:
    async def test_hourly_budget_per_scope(self, tmp_path) -> None:
        clock = FakeClock()
        runtime, db = await self._runtime(tmp_path, clock, cap=2)
        assert runtime.allow_background_vision("group:1") is True
        assert runtime.allow_background_vision("group:1") is True
        assert runtime.allow_background_vision("group:1") is False
        assert runtime.allow_background_vision("group:2") is True  # other scope
        clock.advance(3600)
        assert runtime.allow_background_vision("group:1") is True  # new hour
        await db.close()

    async def test_zero_cap_disables(self, tmp_path) -> None:
        clock = FakeClock()
        runtime, db = await self._runtime(tmp_path, clock, cap=0)
        assert runtime.allow_background_vision("group:1") is False
        await db.close()

    async def _runtime(self, tmp_path, clock, *, cap):  # type: ignore[no-untyped-def]
        runtime, db = make_runtime(tmp_path, clock=clock, cap=cap)
        await db.connect()
        return runtime, db


class TestBackgroundRecognition:
    async def _runtime(self, tmp_path, *, summary: str = "一只猫在笑"):  # type: ignore[no-untyped-def]
        async def fetcher(url: str) -> tuple[bytes, str]:
            return PNG, "image/png"

        runtime, db = make_runtime(tmp_path, fetcher=fetcher)
        await db.connect()
        runtime.vision = make_vision(FakeVisionEngine(summary), db)
        return runtime, db

    def _sticker(self) -> MediaContent:
        return MediaContent(
            media_type="sticker", source_type="qq_image",
            url="https://x/sticker.gif", emoji_summary="动画表情",
            source_group_id="999", source_user_id="7",
        )

    async def test_recognizes_and_keeps_even_without_a_reply(self, tmp_path) -> None:
        runtime, db = await self._runtime(tmp_path)
        outcome = await runtime.background_recognize_and_collect(
            self._sticker(), scope_key="group:999"
        )
        assert outcome.status == "done"
        assert "一只猫在笑" in outcome.vision_text
        assert outcome.decision is not None and outcome.decision.decision == "save"
        assets = await runtime.library.all()
        assert len(assets) == 1 and assets[0].visual_summary == "一只猫在笑"
        assert assets[0].file_path.endswith(".png")  # downloaded for real
        await db.close()

    async def test_download_and_vision_happen_once(self, tmp_path) -> None:
        """The reply path reuses the cached summary — no second vision call."""
        calls = {"fetch": 0}

        async def fetcher(url: str) -> tuple[bytes, str]:
            calls["fetch"] += 1
            return PNG, "image/png"

        runtime, db = make_runtime(tmp_path, fetcher=fetcher)
        await db.connect()
        engine = FakeVisionEngine("一只戴帽子的猫")
        runtime.vision = make_vision(engine, db)

        item = self._sticker()
        await runtime.background_recognize_and_collect(item, scope_key="group:999")
        assert calls["fetch"] == 1
        assert item.sha256  # computed during the background pass

        again = await runtime.understand_image(item)  # the reply path
        assert "一只戴帽子的猫" in again.as_text()
        assert engine.calls == 1          # cache hit (sha256)
        assert calls["fetch"] == 1        # no re-download
        await db.close()

    async def test_throttled_scope_is_skipped(self, tmp_path) -> None:
        runtime, db = await self._runtime(tmp_path)
        for _ in range(20):
            await runtime.background_recognize_and_collect(
                self._sticker(), scope_key="group:999"
            )
        outcome = await runtime.background_recognize_and_collect(
            self._sticker(), scope_key="group:999"
        )
        assert outcome.status == "throttled"
        await db.close()


class TestGroupStickerEndToEnd:
    async def test_group_sticker_is_recognized_without_a_reply(self, tmp_path, caplog) -> None:
        """群里没 @ 的表情包：不回复，但后台识别 + 入库 + 日志可见。"""
        from app.message.event import GroupMessageEvent
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["不该出现"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        bot.media.vision = make_vision(FakeVisionEngine("一只猫在笑"), bot.database)

        async def fetcher(url: str) -> tuple[bytes, str]:
            return PNG, "image/png"

        bot.media._fetch_file = fetcher  # noqa: SLF001 - test injection

        raw = {
            "post_type": "message", "self_id": 10001, "time": 1700000000,
            "message_type": "group", "sub_type": "normal", "message_id": 77,
            "user_id": 888, "group_id": 999,
            "message": [
                {"type": "image", "data": {
                    "file": "STICKER.jpg", "sub_type": "1",
                    "summary": "[动画表情]", "url": "https://x/sticker.gif",
                }}
            ],
            "raw_message": "[动画表情]",
            "sender": {"user_id": 888, "nickname": "某人", "role": "member"},
        }
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(GroupMessageEvent.model_validate(raw))
                await bot.conversation.wait_idle()
                plugin = bot.plugins.loaded["character"]
                await plugin.drain_background()

            # she did NOT reply to the group sticker ...
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
            beats = [r.getMessage() for r in caplog.records if r.name == "CatooBot.Narration"]
            joined = "\n".join(beats)
            assert "收到表情包" in joined                # submit-time narration
            assert "看懂了：一只猫在笑" in joined          # background recognition
            assert "表情库" in joined                    # kept in the library
            # ... but the sticker is in her library now
            assets = await bot.media.library.all()
            assert len(assets) == 1 and assets[0].visual_summary == "一只猫在笑"
        finally:
            await bot.shutdown()


def _beat_index(beats: list[str], *needles: str) -> int:
    for index, beat in enumerate(beats):
        if any(needle in beat for needle in needles):
            return index
    raise AssertionError(f"no narration matched {needles!r}: {beats!r}")


class TestRecognitionOrdering:
    """The 17:24 log showed 静默 before the vision result. Recognition now runs
    first, so the participation decision is made on what the sticker says."""

    @staticmethod
    def _event():  # type: ignore[no-untyped-def]
        from app.message.event import GroupMessageEvent

        return GroupMessageEvent.model_validate(
            {
                "post_type": "message", "self_id": 10001, "time": 1700000000,
                "message_type": "group", "sub_type": "normal", "message_id": 91,
                "user_id": 888, "group_id": 999,
                "message": [
                    {"type": "image", "data": {
                        "file": "STICKER.jpg", "sub_type": "1",
                        "summary": "[动画表情]", "url": "https://x/sticker.gif",
                    }}
                ],
                "raw_message": "[动画表情]",
                "sender": {"user_id": 888, "nickname": "某人", "role": "member"},
            }
        )

    async def _make(self, tmp_path, *, timeout: float = 12.0, fetcher=None):  # type: ignore[no-untyped-def]
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        bot = await make_character_bot(tmp_path, MockAIProvider(), models=["A"])
        bot.media.vision = make_vision(FakeVisionEngine("一只猫在笑"), bot.database)
        bot.config.media.recognition_timeout_seconds = timeout

        async def default_fetcher(url: str) -> tuple[bytes, str]:
            return PNG, "image/png"

        bot.media._fetch_file = fetcher or default_fetcher  # noqa: SLF001 - test injection
        return bot

    @staticmethod
    def _beats(caplog) -> list[str]:  # type: ignore[no-untyped-def]
        return [r.getMessage() for r in caplog.records if r.name == "CatooBot.Narration"]

    async def test_recognition_precedes_the_group_decision(self, tmp_path, caplog) -> None:
        bot = await self._make(tmp_path)
        seen: list[str] = []
        original = bot.social.decide_for_turn

        async def spy(**kwargs):  # type: ignore[no-untyped-def]
            seen.append(str(kwargs.get("text", "")))
            return await original(**kwargs)

        bot.social.decide_for_turn = spy  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(self._event())
                await bot.conversation.wait_idle()
                await bot.plugins.loaded["character"].drain_background()
            beats = self._beats(caplog)
            seen_at = _beat_index(beats, "看懂了：一只猫在笑")
            assert seen_at < _beat_index(beats, "不接", "要回")   # 识别 → 判断
            assert seen_at < _beat_index(beats, "表情库")          # 识别 → 收藏
            assert seen and "一只猫在笑" in seen[0]               # 判断用的就是识别结果
            assets = await bot.media.library.all()
            assert len(assets) == 1
        finally:
            await bot.shutdown()

    async def test_late_recognition_is_still_narrated_and_collected(
        self, tmp_path, caplog
    ) -> None:
        async def slow_fetcher(url: str) -> tuple[bytes, str]:
            await asyncio.sleep(0.3)
            return PNG, "image/png"

        bot = await self._make(tmp_path, timeout=0.05, fetcher=slow_fetcher)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(self._event())
                await bot.conversation.wait_idle()
                assert "还在识别" in "\n".join(self._beats(caplog))
                await asyncio.sleep(0.5)  # the shielded task finishes afterwards
                await bot.plugins.loaded["character"].drain_background()
            joined = "\n".join(self._beats(caplog))
            assert "看懂了：一只猫在笑" in joined and "迟到的识别结果" in joined
            assets = await bot.media.library.all()
            assert len(assets) == 1
        finally:
            await bot.shutdown()


class TestPlainImageRecognition:
    """A plain photo (sub_type=0) must be recognized too — the 17:54 gap: only
    stickers got the background pass, so a silent group photo was never seen."""

    @staticmethod
    def _event():  # type: ignore[no-untyped-def]
        from app.message.event import GroupMessageEvent

        return GroupMessageEvent.model_validate(
            {
                "post_type": "message", "self_id": 10001, "time": 1700000000,
                "message_type": "group", "sub_type": "normal", "message_id": 92,
                "user_id": 888, "group_id": 999,
                "message": [
                    {"type": "image", "data": {
                        "file": "PHOTO.png", "sub_type": "0", "summary": "",
                        "url": "https://x/photo.png",
                    }}
                ],
                "raw_message": "[图片]",
                "sender": {"user_id": 888, "nickname": "空凛", "role": "member"},
            }
        )

    async def _make(self, tmp_path, *, summary: str, ocr: list[str] | None = None):  # type: ignore[no-untyped-def]
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        bot = await make_character_bot(tmp_path, MockAIProvider(), models=["A"])
        engine = FakeVisionEngine(summary)
        if ocr:
            engine.ocr = ocr  # type: ignore[attr-defined]
        bot.media.vision = make_vision(engine, bot.database)

        async def fetcher(url: str) -> tuple[bytes, str]:
            return PNG, "image/png"

        bot.media._fetch_file = fetcher  # noqa: SLF001 - test injection
        return bot

    @staticmethod
    def _beats(caplog) -> list[str]:  # type: ignore[no-untyped-def]
        return [r.getMessage() for r in caplog.records if r.name == "CatooBot.Narration"]

    async def test_silent_group_photo_is_still_recognized(self, tmp_path, caplog) -> None:
        summary = "桌上摊着一份外卖和一杯可乐"
        bot = await self._make(tmp_path, summary=summary)
        seen: list[str] = []
        original = bot.social.decide_for_turn

        async def spy(**kwargs):  # type: ignore[no-untyped-def]
            seen.append(str(kwargs.get("text", "")))
            return await original(**kwargs)

        bot.social.decide_for_turn = spy  # type: ignore[method-assign]
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(self._event())
                await bot.conversation.wait_idle()
                await bot.plugins.loaded["character"].drain_background()
            beats = self._beats(caplog)
            assert _beat_index(beats, "收到图片") < _beat_index(beats, f"看懂了：{summary}")
            assert _beat_index(beats, f"看懂了：{summary}") < _beat_index(
                beats, "不接", "要回"
            )
            assert seen and summary in seen[0]
            # a plain photo is not a sticker — nothing enters the library
            assert await bot.media.library.all() == []
        finally:
            await bot.shutdown()

    async def test_captioned_meme_photo_is_kept_as_a_sticker(self, tmp_path, caplog) -> None:
        """The v1.1 boundary: a photo only enters the library when vision
        confirms it is a meme (caption text counts)."""
        bot = await self._make(
            tmp_path, summary="一只猫举着牌子", ocr=["在吗"]
        )
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                await bot.event_bus.emit(self._event())
                await bot.conversation.wait_idle()
                await bot.plugins.loaded["character"].drain_background()
            assets = await bot.media.library.all()
            assert len(assets) == 1 and assets[0].visual_summary == "一只猫举着牌子"
            assert "表情库" in "\n".join(self._beats(caplog))
        finally:
            await bot.shutdown()

    async def test_caption_reaches_the_reply_prompt(self, tmp_path) -> None:
        """A captioned image in private chat: the model must be told to answer
        the caption, not describe the picture (the '你在干森么呢' rule)."""
        from app.message.event import PrivateMessageEvent
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["在摸鱼呢"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        engine = FakeVisionEngine("一只猫举着牌子")
        engine.ocr = ["你在干森么呢"]
        bot.media.vision = make_vision(engine, bot.database)

        async def fetcher(url: str) -> tuple[bytes, str]:
            return PNG, "image/png"

        bot.media._fetch_file = fetcher  # noqa: SLF001 - test injection
        try:
            event = PrivateMessageEvent.model_validate(
                {
                    "post_type": "message", "self_id": 10001, "time": 1700000000,
                    "message_type": "private", "sub_type": "friend", "message_id": 93,
                    "user_id": 777,
                    "message": [
                        {"type": "image", "data": {
                            "file": "MEME.jpg", "sub_type": "0", "summary": "",
                            "url": "https://x/meme.jpg",
                        }}
                    ],
                    "raw_message": "[图片]",
                    "sender": {"user_id": 777, "nickname": "空凛猫"},
                }
            )
            await bot.event_bus.emit(event)
            await bot.conversation.wait_idle()
            await bot.plugins.loaded["character"].drain_background()
            assert provider.calls, "私聊图片应当触发回复"
            user_text = "\n".join(m.content for m in provider.calls[0]["messages"])
            assert "你在干森么呢" in user_text      # the caption is visible
            assert "配字" in user_text              # …and framed as what she means
        finally:
            await bot.shutdown()
