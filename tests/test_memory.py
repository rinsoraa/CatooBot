"""Memory subsystem tests: dedup, merge, conflict update, retrieval, extraction."""

from __future__ import annotations

import pytest

from app.config.settings import MemoryConfig
from app.memory.manager import MemoryManager
from app.memory.model import scope_key
from tests.ai_mocks import MockAIProvider


def make_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'mem.db'}"))


async def make_manager(tmp_path, config: MemoryConfig | None = None):
    database = make_db(tmp_path)
    await database.connect()
    return MemoryManager(config or MemoryConfig(), database), database


class TestScopes:
    def test_scope_key_format(self) -> None:
        assert scope_key("user", "123") == "user:123"
        assert scope_key("group", "456") == "group:456"
        with pytest.raises(ValueError):
            scope_key("galaxy", "1")


class TestRemember:
    async def test_store_and_list(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        memory = await manager.remember("user", "1", "用户喜欢猫", category="preference")
        assert memory is not None and memory.id > 0
        listed = await manager.list_memories(scope_key="user:1")
        assert [m.content for m in listed] == ["用户喜欢猫"]
        await database.close()

    async def test_exact_duplicate_reinforces(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        first = await manager.remember("user", "1", "用户喜欢猫")
        second = await manager.remember("user", "1", "用户喜欢猫")
        assert second.id == first.id
        assert second.confidence > first.confidence
        assert await manager.count() == 1  # no duplicate row
        await database.close()

    async def test_whitespace_insensitive_dedup(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫")
        await manager.remember("user", "1", "用户 喜欢猫 ")
        assert await manager.count() == 1
        await database.close()

    async def test_conflict_supersedes_old_memory(self, tmp_path) -> None:
        """v0.5: a contradicting statement creates a new row and marks the old
        one superseded — history is preserved instead of overwritten (spec §11)."""
        manager, database = await make_manager(tmp_path)
        old = await manager.remember("user", "1", "用户喜欢猫", category="preference")
        updated = await manager.remember("user", "1", "用户现在更喜欢狗", category="preference")

        assert updated.id != old.id  # a new row, not an in-place edit
        assert updated.supersedes_id == old.id
        assert (await manager.repository.get(old.id)).status == "superseded"
        assert (await manager.repository.get(updated.id)).status == "active"

        # only the active memory participates in listing / retrieval
        listed = await manager.list_memories(scope_key="user:1", status="active")
        assert [m.content for m in listed] == ["用户现在更喜欢狗"]
        await database.close()

    async def test_compatible_statements_coexist(self, tmp_path) -> None:
        """'喜欢 A' and '也喜欢 B' are not a conflict (spec §33)."""
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫", category="preference")
        await manager.remember("user", "1", "用户也喜欢狗", category="interest")
        assert await manager.count() == 2
        await database.close()

    async def test_unrelated_memories_coexist(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫")
        await manager.remember("user", "1", "用户在开发一个游戏项目")
        assert await manager.count() == 2
        await database.close()

    async def test_scopes_are_isolated(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫")
        await manager.remember("user", "2", "用户喜欢猫")  # same content, other user
        assert await manager.count() == 2  # no cross-scope dedup
        await database.close()

    async def test_forbidden_judgements_rejected(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        assert await manager.remember("user", "1", "用户很蠢") is None
        assert await manager.remember("user", "1", "用户智力低，性格有缺陷") is None
        assert await manager.remember("user", "1", "用户好感度 73") is None
        assert await manager.count() == 0
        await database.close()

    async def test_too_short_skipped(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        assert await manager.remember("user", "1", "嗯") is None
        await database.close()

    async def test_edit_and_delete(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        memory = await manager.remember("user", "1", "用户喜欢猫")
        assert await manager.edit_memory(memory.id, "用户特别喜欢猫", "preference", 0.9)
        listed = await manager.list_memories(scope_key="user:1")
        assert listed[0].content == "用户特别喜欢猫"
        assert listed[0].importance == 0.9
        assert await manager.delete_memory(memory.id)
        assert await manager.count() == 0
        await database.close()


class TestRetrieval:
    async def test_relevant_memory_selected(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫", importance=0.8)
        await manager.remember("user", "1", "用户在学 Python")
        await manager.remember("user", "1", "用户住在南方")
        memories = await manager.retrieve_for_session("private:1", "我养的猫最近老叫")
        assert len(memories) == 1
        assert "猫" in memories[0].content
        await database.close()

    async def test_top_k_limit(self, tmp_path) -> None:
        from app.config.settings import MemoryConfig, MemoryRetrievalConfig

        config = MemoryConfig(retrieval=MemoryRetrievalConfig(top_k=2))
        manager, database = await make_manager(tmp_path, config)
        for i in range(5):
            await manager.remember("user", "1", f"用户的兴趣话题编号{i}是钓鱼")
        memories = await manager.retrieve_for_session("private:1", "钓鱼这个话题怎么样")
        assert len(memories) <= 2
        await database.close()

    async def test_session_isolation_in_retrieval(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫")
        memories = await manager.retrieve_for_session("private:2", "我喜欢什么动物")
        assert memories == []
        await database.close()

    async def test_mark_used(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        await manager.remember("user", "1", "用户喜欢猫", importance=0.9)
        memories = await manager.retrieve_for_session("private:1", "猫猫猫")
        assert memories, "expected the cat memory to be retrieved"
        fresh = await manager.repository.get(memories[0].id)
        assert fresh.use_count == 1  # persisted by mark_used
        await database.close()

    async def test_recency_boosts_fresh_memory(self, tmp_path) -> None:
        manager, database = await make_manager(tmp_path)
        old = await manager.remember(
            "user", "1", "用户喜欢钓鱼", category="interest", importance=0.9
        )
        await manager.remember("user", "1", "用户每周骑行车", category="habit", importance=0.9)
        # artificially age the first memory by rewriting updated_at
        await database.execute("UPDATE memories SET updated_at = ? WHERE id = ?", (0, old.id))
        memories = await manager.retrieve_for_session("private:1", "周末去骑车怎么样")
        assert len(memories) == 1  # the aged one scores below min_score
        assert "骑行" in memories[0].content
        await database.close()


class TestExtraction:
    async def test_schedule_and_extract(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.ai.models import AIRequest  # noqa: F401
        from app.config.settings import AIConfig
        from app.memory.extraction import MemoryExtractor

        manager, database = await make_manager(tmp_path)
        provider = MockAIProvider(
            behaviors={
                "A": [
                    '{"memories": ['
                    '{"category": "preference", "content": "用户喜欢猫",'
                    '"importance": 0.8},'
                    '{"category": "fact", "content": "用户在写小说",'
                    '"importance": 0.6}'
                    "]}"
                ]
            }
        )
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        extractor = MemoryExtractor(MemoryConfig(), engine, manager)
        await extractor.schedule("private:5", "5", None, "我喜欢猫，还在写小说", "真好呀！")
        await extractor.wait_idle()
        memories = await manager.list_memories(scope_key="user:5")
        assert {m.content for m in memories} == {"用户喜欢猫", "用户在写小说"}
        await database.close()

    async def test_extraction_failure_is_silent(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig
        from app.memory.extraction import MemoryExtractor
        from tests.ai_mocks import MockAIProvider as Mock

        manager, database = await make_manager(tmp_path)
        provider = Mock(behaviors={"A": ["这不是 JSON"]})
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        extractor = MemoryExtractor(MemoryConfig(), engine, manager)
        await extractor.schedule("private:5", "5", None, "msg", "reply")
        await extractor.wait_idle()
        assert await manager.count() == 0
        await database.close()

    async def test_disabled_extraction_noop(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig, MemoryConfig
        from app.memory.extraction import MemoryExtractor
        from tests.ai_mocks import MockAIProvider as Mock

        manager, database = await make_manager(tmp_path)
        provider = Mock(behaviors={"A": ["x"]})
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        config = MemoryConfig(extraction={"enabled": False})
        extractor = MemoryExtractor(config, engine, manager)
        await extractor.schedule("private:5", "5", None, "msg", "reply")
        await extractor.wait_idle()
        assert provider.calls == []
        await database.close()
