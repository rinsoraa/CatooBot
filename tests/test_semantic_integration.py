"""v0.5 integration: semantic memory inside the real chat pipeline (spec §85).

QQ message → behaviour gate → character runtime → hybrid memory retrieval →
prompt → AI → response planner → delivery, plus the failure paths the spec
demands (embedding down, memory DB down, cross-scope isolation).
"""

from __future__ import annotations

import pytest

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, AppConfig
from app.memory.consolidation import ConsolidationScheduler, MemoryConsolidator
from app.memory.embedding import EmbeddingService
from app.memory.manager import MemoryManager
from tests.ai_mocks import MockAIProvider
from tests.conftest import make_bot, private_event
from tests.test_semantic_memory import KeywordEmbeddingProvider


async def make_semantic_bot(tmp_path, provider: MockAIProvider):
    """A bot wired with mocked AI + mocked embeddings, all in-memory."""
    bot = make_bot(tmp_path)
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'sem.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        behavior={"reply": {"enabled": False}},
        memory={"semantic": {"enabled": True}},
    )
    bot.config = config
    bot.ai = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        bot.database,
        providers={"mock": provider},
    )
    bot.character.engine = bot.ai
    await bot.database.connect()
    embeddings = EmbeddingService(
        config.memory.semantic.embedding, bot.database, provider=KeywordEmbeddingProvider()
    )
    bot.embeddings = embeddings
    bot.memory = MemoryManager(config.memory, bot.database, embeddings=embeddings)
    bot.character.memory = bot.memory
    bot.consolidator = MemoryConsolidator(config.memory, bot.memory)
    bot.consolidation_scheduler = ConsolidationScheduler(bot.consolidator, "manual")
    if bot.character.extractor is not None:
        bot.character.extractor.engine = bot.ai
        bot.character.extractor._manager = bot.memory  # noqa: SLF001
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    bot.config.memory.extraction.enabled = False  # keep mock call lists chat-only
    return bot


class TestSemanticChatPipeline:
    async def test_stored_memory_reaches_the_prompt(self, tmp_path) -> None:
        """A fact learned days ago is used without sharing wording (§100 场景 1)."""
        provider = MockAIProvider(behaviors={"A": ["记得啊，你那个网站弄得怎么样了"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "777", "用户在做个人网站的音乐播放器",
                category="project", importance=0.9,
            )
            await bot.event_bus.emit(private_event("之前那个听歌的东西做好了吗", user_id=777))
            system = provider.calls[0]["messages"][0].content
            assert "音乐播放器" in system or "网站" in system
            assert "参考资料" in system  # labelled as reference, not instructions
            assert bot.adapter.sent_texts()  # reply was delivered
        finally:
            await bot.shutdown()

    async def test_unrelated_memory_is_not_injected(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["嗯嗯"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "777", "用户喜欢 Minecraft", category="interest", importance=0.5
            )
            await bot.event_bus.emit(private_event("今天天气不错", user_id=777))
            system = provider.calls[0]["messages"][0].content
            assert "Minecraft" not in system  # relevance guard kept it out
        finally:
            await bot.shutdown()

    async def test_other_users_memory_never_injected(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["我不太清楚诶"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "A", "用户 A 喜欢猫", category="preference", importance=0.9
            )
            await bot.event_bus.emit(private_event("我喜欢什么动物", user_id=999))
            system = provider.calls[0]["messages"][0].content
            assert "用户 A 喜欢猫" not in system
        finally:
            await bot.shutdown()

    async def test_group_memory_isolated_from_private(self, tmp_path) -> None:
        from tests.conftest import group_event

        provider = MockAIProvider(behaviors={"A": ["哦哦"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "42", "用户私下说在准备比赛", category="event", importance=0.9
            )
            await bot.event_bus.emit(group_event("大家在聊什么", user_id=42, at_bot=True))
            system = provider.calls[0]["messages"][0].content
            assert "准备比赛" not in system  # private memory stays private
        finally:
            await bot.shutdown()

    async def test_usage_count_grows_when_recalled(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["好的"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            memory = await bot.memory.remember(
                "user", "777", "用户喜欢猫", category="preference", importance=0.9
            )
            await bot.event_bus.emit(private_event("猫猫好可爱", user_id=777))
            refreshed = await bot.memory.repository.get(memory.id)
            assert refreshed.use_count >= 1
        finally:
            await bot.shutdown()


class TestDegradation:
    async def test_embedding_down_still_chats(self, tmp_path) -> None:
        """Semantic provider 500 → keyword retrieval, normal reply (spec §83)."""
        from app.memory.embedding import EmbeddingError, EmbeddingProvider

        class Broken(EmbeddingProvider):
            name = "broken"
            model = "broken"
            dimensions = 4

            async def embed(self, texts: list[str]) -> list[list[float]]:
                raise EmbeddingError("500")

        provider = MockAIProvider(behaviors={"A": ["在的在的"]})
        bot = await make_semantic_bot(tmp_path, provider)
        bot.embeddings = EmbeddingService(
            bot.config.memory.semantic.embedding, bot.database, provider=Broken()
        )
        bot.memory = MemoryManager(
            bot.config.memory, bot.database, embeddings=bot.embeddings
        )
        bot.character.memory = bot.memory
        try:
            await bot.memory.remember("user", "1", "用户喜欢猫", category="preference")
            await bot.event_bus.emit(private_event("猫", user_id=1))
            assert bot.adapter.sent_texts() == ["在的在的"]  # chat unaffected
            assert bot.embeddings.failures >= 1
        finally:
            await bot.shutdown()

    async def test_database_down_still_chats(self, tmp_path) -> None:
        """Memory/DB trouble must not take chat down (spec §84)."""
        provider = MockAIProvider(behaviors={"A": ["还在呢"]})
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.database.close()  # yank the database out
            await bot.event_bus.emit(private_event("在吗", user_id=5))
            assert bot.adapter.sent_texts() == ["还在呢"]
        finally:
            await bot.shutdown()

    async def test_memory_disabled_entirely(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["嗯"]})
        bot = await make_semantic_bot(tmp_path, provider)
        bot.character.memory = None
        try:
            await bot.event_bus.emit(private_event("你好", user_id=5))
            assert bot.adapter.sent_texts() == ["嗯"]
        finally:
            await bot.shutdown()


class TestConsolidationSchedulerIntegration:
    async def test_scheduler_runs_and_reports(self, tmp_path) -> None:
        provider = MockAIProvider()
        bot = await make_semantic_bot(tmp_path, provider)
        scheduler = ConsolidationScheduler(bot.consolidator, "manual")
        try:
            await bot.memory.remember("user", "1", "用户喜欢猫", category="preference")
            report = await scheduler.tick()
            assert report.scanned >= 1
            assert scheduler.runs == 1
            health = await bot.consolidator.health()
            assert health["total"] >= 1
            assert health["last_consolidation"] is not None
        finally:
            await bot.shutdown()

    async def test_manual_schedule_is_not_automatic(self, tmp_path) -> None:
        provider = MockAIProvider()
        bot = await make_semantic_bot(tmp_path, provider)
        scheduler = ConsolidationScheduler(bot.consolidator, "manual")
        try:
            assert scheduler.enabled is False
            await scheduler.start()
            assert scheduler.running is False  # nothing scheduled
        finally:
            await bot.shutdown()


_MODE_QUERIES = {
    "hybrid": "网站做得怎么样了",
    "semantic": "网站做得怎么样了",
    "keyword": "网站",
}


@pytest.mark.parametrize("mode", ["hybrid", "keyword", "semantic"])
class TestWebSearchModes:
    async def test_search_modes_return_results(self, tmp_path, mode: str) -> None:
        from app.web.services.memory import MemoryAdminService

        provider = MockAIProvider()
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "1", "用户在做个人网站", category="project", importance=0.8
            )
            await bot.memory.remember(
                "user", "1", "用户喜欢 Minecraft", category="interest", importance=0.5
            )
            service = MemoryAdminService(bot)
            data = await service.search(_MODE_QUERIES[mode], mode=mode, scope_key="user:1")
            assert data["results"], f"mode={mode} returned nothing"
            assert "error" not in data
        finally:
            await bot.shutdown()

    async def test_retrieval_debugger_explains_scores(self, tmp_path, mode: str) -> None:
        from app.web.services.memory import MemoryAdminService

        provider = MockAIProvider()
        bot = await make_semantic_bot(tmp_path, provider)
        try:
            await bot.memory.remember(
                "user", "1", "用户在做个人网站", category="project", importance=0.8
            )
            service = MemoryAdminService(bot)
            debug = await service.retrieval_debug("网站怎么样了", "user:1")
            assert debug["candidates"] >= 1
            assert "weights" in debug and "min_final_score" in debug
            assert debug["results"]
            top = debug["results"][0]
            assert {"final", "semantic", "keyword", "importance", "recency"} <= set(top)
        finally:
            await bot.shutdown()
