"""Dashboard counters actually move (Task 4).

The WebUI cards read ``Metrics.snapshot()``. Before this, ``memories_extracted``
was never incremented and ``ai_requests`` counted reply turns in one plugin
instead of model calls, so the cards were meaningless:

* ``ai_requests`` — one per provider attempt (router "request" event)
* ``ai_errors`` — one per request no model could answer ("failed" event)
* ``memories_extracted`` — memories the extractor actually saved
"""

from __future__ import annotations

import json

from app.config.settings import AIConfig, MemoryConfig
from tests.ai_mocks import MockAIProvider
from tests.conftest import private_event

PERFECT_REPLY = json.dumps(
    {
        "memories": [
            {"category": "preference", "content": "用户喜欢猫", "importance": 0.8},
            {"category": "fact", "content": "用户在写小说", "importance": 0.6},
        ]
    },
    ensure_ascii=False,
)


class TestAiRequestCounters:
    async def test_each_model_call_is_counted(self, tmp_path) -> None:
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": ["在的呀", "还在的"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        bot.config.memory.extraction.enabled = False  # only the chat call is model-backed
        try:
            await bot.event_bus.emit(private_event("在吗", user_id=777))
            await bot.conversation.wait_idle()
            first = bot.metrics.snapshot()["ai_requests"]
            assert first == 1
            assert bot.metrics.get("ai_errors") == 0

            await bot.event_bus.emit(private_event("还在吗", user_id=777))
            await bot.conversation.wait_idle()
            assert bot.metrics.snapshot()["ai_requests"] == first + 1
        finally:
            await bot.shutdown()

    async def test_request_no_model_can_answer_counts_an_error(self, tmp_path) -> None:
        from tests.test_chat_integration import RateLimitedMock, make_character_bot

        bot = await make_character_bot(tmp_path, RateLimitedMock(), models=["A", "B"])
        bot.config.memory.extraction.enabled = False
        try:
            await bot.event_bus.emit(private_event("在吗", user_id=777))
            await bot.conversation.wait_idle()
            snapshot = bot.metrics.snapshot()
            assert snapshot["ai_requests"] == 2  # one attempt per model
            assert snapshot["ai_errors"] == 1  # the request as a whole failed
            assert snapshot["rate_limited"] >= 1
        finally:
            await bot.shutdown()


class TestMemoriesExtractedCounter:
    async def _extractor(self, tmp_path, reply: str):  # type: ignore[no-untyped-def]
        from app.ai.engine import AIEngine
        from app.core.metrics import Metrics
        from app.memory.extraction import MemoryExtractor
        from tests.test_memory import make_manager

        manager, database = await make_manager(tmp_path)
        provider = MockAIProvider(behaviors={"A": [reply]})
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        metrics = Metrics()
        extractor = MemoryExtractor(MemoryConfig(), engine, manager, metrics=metrics)
        return extractor, metrics, database

    async def test_saved_memories_are_counted(self, tmp_path) -> None:
        extractor, metrics, database = await self._extractor(tmp_path, PERFECT_REPLY)
        await extractor.schedule("private:5", "5", None, "我喜欢猫，还在写小说", "真好呀！")
        await extractor.wait_idle()
        assert metrics.snapshot()["memories_extracted"] == 2
        await database.close()

    async def test_unparsable_reply_keeps_the_counter_at_zero(self, tmp_path) -> None:
        extractor, metrics, database = await self._extractor(tmp_path, "嗯嗯，记住啦")
        await extractor.schedule("private:5", "5", None, "msg", "reply")
        await extractor.wait_idle()
        assert metrics.snapshot()["memories_extracted"] == 0
        await database.close()
