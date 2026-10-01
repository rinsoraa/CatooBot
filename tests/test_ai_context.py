"""ConversationManager tests: isolation, trimming, clearing, persistence."""

from __future__ import annotations

from app.ai.context import ConversationManager
from app.config.settings import AIContextConfig
from app.database.database import Database


def make_manager(database: Database | None, max_messages: int = 20) -> ConversationManager:
    return ConversationManager(AIContextConfig(enabled=True, max_messages=max_messages), database)


async def make_db(tmp_path) -> Database:
    database = Database(type("Cfg", (), {"sqlite_path": tmp_path / "ctx.db"})())
    await database.connect()
    return database


class TestIsolation:
    async def test_sessions_are_isolated(self, tmp_path) -> None:
        manager = make_manager(await make_db(tmp_path))
        await manager.append_user_message("private:111", "私聊消息")
        await manager.append_user_message("group:222", "群消息")

        assert [m.content for m in await manager.get_context("private:111")] == ["私聊消息"]
        assert [m.content for m in await manager.get_context("group:222")] == ["群消息"]
        assert await manager.get_context("private:999") == []

    async def test_roles_recorded(self, tmp_path) -> None:
        manager = make_manager(await make_db(tmp_path))
        await manager.append_user_message("s1", "hi")
        await manager.append_assistant_message("s1", "hello")
        roles = [m.role for m in await manager.get_context("s1")]
        assert roles == ["user", "assistant"]


class TestTrimming:
    async def test_trim_keeps_most_recent(self, tmp_path) -> None:
        manager = make_manager(await make_db(tmp_path), max_messages=4)
        for i in range(10):
            await manager.append_user_message("s1", f"u{i}")
            await manager.append_assistant_message("s1", f"a{i}")
        history = await manager.get_context("s1")
        assert len(history) == 4
        # last four turns of u0,a0,...,u9,a9 are: u8, a8, u9, a9
        assert [m.content for m in history] == ["u8", "a8", "u9", "a9"]


class TestClear:
    async def test_clear_only_target_session(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        manager = make_manager(database)
        await manager.append_user_message("s1", "one")
        await manager.append_user_message("s2", "two")

        await manager.clear_context("s1")
        assert await manager.get_context("s1") == []
        assert [m.content for m in await manager.get_context("s2")] == ["two"]


class TestPersistence:
    async def test_context_survives_restart(self, tmp_path) -> None:
        db_path = tmp_path / "ctx.db"
        database1 = Database(type("Cfg", (), {"sqlite_path": db_path})())
        await database1.connect()
        manager1 = make_manager(database1)
        await manager1.append_user_message("private:55", "第一句")
        await manager1.append_assistant_message("private:55", "第一答")
        await database1.close()

        # simulate restart: fresh manager over the same sqlite file
        database2 = Database(type("Cfg", (), {"sqlite_path": db_path})())
        await database2.connect()
        manager2 = make_manager(database2)
        history = await manager2.get_context("private:55")
        assert [m.content for m in history] == ["第一句", "第一答"]
        await database2.close()


class TestDisabled:
    async def test_disabled_is_noop(self, tmp_path) -> None:
        manager = ConversationManager(AIContextConfig(enabled=False), await make_db(tmp_path))
        await manager.append_user_message("s1", "ignored")
        assert await manager.get_context("s1") == []
