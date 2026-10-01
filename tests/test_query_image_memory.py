"""Task 21 milestone 3: query_image_memory — read-only recall of images she saw."""

from __future__ import annotations

from app.config.settings import DatabaseConfig, MemoryConfig
from app.database.database import Database
from app.memory.manager import MemoryManager
from app.tools.builtins.query_image_memory import QueryImageMemoryTool
from app.tools.models import ToolContext


async def make_manager(tmp_path) -> tuple[MemoryManager, Database]:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'qimg.db'}"))
    await db.connect()
    manager = MemoryManager(MemoryConfig(semantic={"enabled": False}), db)
    return manager, db


def context(manager: MemoryManager, session_id: str = "private:7") -> ToolContext:
    return ToolContext(user_id="7", session_id=session_id, metadata={"memory": manager})


class TestQueryImageMemory:
    async def test_returns_only_vision_memories(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await manager.remember(
                "user",
                "7",
                "看过一张图片：一只猫举着牌子，上面写着你好",
                source="vision",
                category="event",
            )
            await manager.remember("user", "7", "用户喜欢喝冰可乐", source="conversation")

            result = await QueryImageMemoryTool().execute({"query": "猫"}, context(manager))
            assert result.success is True
            items = result.data["memories"]
            assert len(items) == 1, items
            assert "猫" in items[0]["content"]
            assert result.metadata["count"] == 1
        finally:
            await db.close()

    async def test_no_vision_memories_is_a_clean_empty(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await manager.remember("user", "7", "用户喜欢喝冰可乐", source="conversation")
            result = await QueryImageMemoryTool().execute({"query": "猫"}, context(manager))
            assert result.success is True
            assert result.data["memories"] == []
            assert result.metadata["count"] == 0
        finally:
            await db.close()

    async def test_scope_isolation(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await manager.remember(
                "user", "7", "看过一张图片：机票行程单", source="vision", category="event"
            )
            await manager.remember(
                "user", "8", "看过一张图片：购物小票", source="vision", category="event"
            )
            # session private:7 → scope user:7 → only the first one
            result = await QueryImageMemoryTool().execute({"query": "机票"}, context(manager))
            items = result.data["memories"]
            assert len(items) == 1
            assert "机票" in items[0]["content"]
        finally:
            await db.close()

    async def test_memory_disabled_is_an_unavailable_failure(self, tmp_path) -> None:
        tool = QueryImageMemoryTool()
        result = await tool.execute(
            {"query": "猫"}, ToolContext(user_id="7", session_id="private:7", metadata={})
        )
        assert result.success is False
        assert result.error_type == "unavailable"
