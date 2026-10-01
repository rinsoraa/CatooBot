"""Image memory (Task 21, milestone 1): which photos become memories.

The bridge keeps three boundaries: stickers stay assets, a photo only earns a
memory when it carries information, and the content is the vision text she
already produced — never raw bytes, URLs or thinking.
"""

from __future__ import annotations

from app.config.settings import DatabaseConfig, MemoryConfig
from app.database.database import Database
from app.media.memory_bridge import (
    MAX_IMAGES_PER_TURN,
    compose,
    deserves_memory,
    fingerprint,
    record_image_memories,
)
from app.media.models import MediaContent, VisionResult
from app.memory.manager import MemoryManager


def rich(summary: str = "一只猫举着牌子", **fields: object) -> VisionResult:
    base = {"summary": summary, "ocr_text": ["在吗"], "confidence": 0.8}
    base.update(fields)  # type: ignore[arg-type]
    return VisionResult(**base)  # type: ignore[arg-type]


def photo(sha: str = "a" * 64, url: str = "https://x/photo.jpg") -> MediaContent:
    return MediaContent(media_type="image", source_type="qq_image", url=url, sha256=sha)


async def make_manager(tmp_path):  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'imgmem.db'}"))
    await db.connect()
    manager = MemoryManager(MemoryConfig(semantic={"enabled": False}), db)
    return manager, db


class TestAdmission:
    def test_text_people_objects_or_confident_scene(self) -> None:
        assert deserves_memory(rich()) is True
        assert deserves_memory(VisionResult(summary="两个人合影", people_count=2)) is True
        assert deserves_memory(VisionResult(summary="桌上东西", objects=["杯", "书"])) is True
        assert deserves_memory(VisionResult(summary="客厅", scene="室内", confidence=0.7)) is True

    def test_blurry_or_empty_is_refused(self) -> None:
        assert deserves_memory(None) is False
        assert deserves_memory(VisionResult()) is False
        assert deserves_memory(VisionResult(summary="  ")) is False
        assert (
            deserves_memory(VisionResult(summary="一片模糊", scene="室外", confidence=0.3)) is False
        )
        assert deserves_memory(VisionResult(summary="一张图", objects=["云"])) is False

    def test_fingerprint_prefers_sha_and_survives_missing_urls(self) -> None:
        assert fingerprint(photo()).startswith("[img:aaaaaaaaaaaaaaaa]")
        assert fingerprint(MediaContent(media_type="image", url="https://x/y.jpg")).startswith(
            "[img:"
        )
        assert fingerprint(MediaContent(media_type="image")) == ""

    def test_compose_marks_the_count_and_caps_the_detail(self) -> None:
        one = compose(["第一张的摘要"], total=1)
        assert one.startswith("看过一张图片") and "第一张的摘要" in one
        many = compose(["a", "b", "c", "d"], total=4)
        assert "看过 4 张图片（记下前 3 张）" in many and "d" not in many
        assert MAX_IMAGES_PER_TURN == 3


class TestWritePath:
    async def test_a_rich_photo_becomes_one_vision_memory(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            stored = await record_image_memories(
                manager, scope="user", ref="7", entries=[(photo(), rich())]
            )
            assert stored is not None and stored.source == "vision"
            assert stored.layer == "episodic" and stored.category == "event"
            assert stored.content.startswith("看过一张图片") and "在吗" in stored.content
            assert stored.content.endswith("[img:aaaaaaaaaaaaaaaa]")
            assert 0.3 <= stored.importance <= 0.6
            assert stored.confidence <= 0.8
        finally:
            await db.close()

    async def test_a_turn_with_three_photos_is_one_memory(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            entries = [
                (photo(sha=f"{i}" * 64), rich(f"第{i}张的摘要", ocr_text=[f"文字{i}"]))
                for i in range(1, 4)
            ]
            stored = await record_image_memories(manager, scope="user", ref="7", entries=entries)
            assert stored is not None
            assert "看过 3 张图片" in stored.content
            assert all(f"第{i}张的摘要" in stored.content for i in (1, 2, 3))
            memories = await manager.list_memories(scope_key="user:7")
            assert len(memories) == 1  # one memory per turn
        finally:
            await db.close()

    async def test_the_same_image_is_never_remembered_twice(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            for _ in range(2):
                await record_image_memories(
                    manager, scope="user", ref="7", entries=[(photo(), rich())]
                )
            memories = await manager.list_memories(scope_key="user:7")
            assert len(memories) == 1  # the fingerprint makes the content identical
        finally:
            await db.close()

    async def test_stickers_and_empty_photos_are_not_remembered(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            sticker = MediaContent(media_type="sticker", url="https://x/s.gif", sha256="b" * 64)
            assert (
                await record_image_memories(
                    manager, scope="user", ref="7", entries=[(sticker, rich())]
                )
                is None
            )
            assert (
                await record_image_memories(
                    manager, scope="user", ref="7", entries=[(photo(), VisionResult())]
                )
                is None
            )
            assert await manager.list_memories(scope_key="user:7") == []
        finally:
            await db.close()

    async def test_scopes_stay_isolated_and_the_memory_is_recallable(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await record_image_memories(
                manager,
                scope="user",
                ref="7",
                entries=[(photo(), rich("桌上有一罐可乐和一本书"))],
            )
            mine = await manager.retrieve_for_session("private:7", "那张图里有什么可乐吗")
            assert any("可乐" in memory.content for memory in mine)
            theirs = await manager.retrieve_for_session("private:8", "可乐")
            assert all("看过" not in memory.content for memory in theirs)
        finally:
            await db.close()


class TestWebuiFilter:
    async def test_the_source_filter_returns_only_vision_memories(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await manager.remember("user", "7", "用户喜欢喝冰可乐", source="conversation")
            await record_image_memories(manager, scope="user", ref="7", entries=[(photo(), rich())])
            vision = await manager.list_memories(source="vision")
            assert [m.source for m in vision] == ["vision"]
            spoken = await manager.list_memories(source="conversation")
            assert [m.source for m in spoken] == ["conversation"]
            assert len(await manager.list_memories()) == 2  # no filter = both
        finally:
            await db.close()

    def test_vision_is_a_registered_source(self) -> None:
        from app.memory.model import SOURCES

        assert "vision" in SOURCES
