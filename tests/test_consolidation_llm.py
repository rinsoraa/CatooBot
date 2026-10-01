"""LLM memory compression (Task 13): structured JSON, rule fallback, provenance.

``compression_use_llm`` used to be a config flag nothing implemented. Now the
model writes the cluster summary as structured JSON — entities stay inside the
sentence, time/causality travel as fields — and every failure (provider error,
unparsable reply, missing engine) falls back to the rule-based summary with a
WARNING. The sources stay linked through ``compressed_from`` either way, so the
WebUI memory detail can still answer "where did this come from?".
"""

from __future__ import annotations

import json
import logging

import pytest

from app.ai.engine import AIEngine
from app.config.settings import AIConfig, MemoryConfig
from app.memory.consolidation import MemoryConsolidator, compose_summary, parse_compression
from tests.ai_mocks import MockAIProvider
from tests.test_semantic_memory import fake_vector, make_manager

EPISODES = ("用户开始做个人网站", "用户在做网站的页面", "用户继续完善网站")

MODEL_REPLY = json.dumps(
    {
        "summary": "用户把自己的网站页面做完了，还加上了音乐播放器",
        "time": "三月底",
        "causality": "因为想边听歌边写代码，所以先把播放器做了",
    },
    ensure_ascii=False,
)


def config_with(*, use_llm: bool) -> MemoryConfig:
    return MemoryConfig(
        consolidation={
            "compression_min_cluster": 3,
            "conflict_threshold": 0.4,  # the mock semantic space is coarse
            "duplicate_threshold": 0.995,
            "compression_use_llm": use_llm,
        }
    )


async def seed_and_consolidate(tmp_path, *, use_llm: bool, reply: object = MODEL_REPLY):  # type: ignore[no-untyped-def]
    config = config_with(use_llm=use_llm)
    manager, database = await make_manager(tmp_path, config=config)
    for text in EPISODES:
        memory = await manager.remember(
            "user", "1", text, category="event", layer="episodic", importance=0.5
        )
        await manager.vectors.upsert(memory.id, fake_vector(text), "mock-embed")
    provider = MockAIProvider(behaviors={"A": [reply]})
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        database,
        providers={"mock": provider},
    )
    consolidator = MemoryConsolidator(config, manager, engine=engine)
    report = await consolidator.run("user:1")
    return report, manager, database, provider


async def semantic_memory(manager) -> object:  # type: ignore[no-untyped-def]
    active = await manager.repository.candidates(["user:1"], limit=50)
    return next(memory for memory in active if memory.layer == "semantic")


class TestParsing:
    def test_fenced_json_is_accepted(self) -> None:
        payload = parse_compression('```json\n{"summary": "做了网站"}\n```')
        assert payload == {"summary": "做了网站"}

    def test_missing_summary_is_no_opinion(self) -> None:
        assert parse_compression('{"time": "昨天"}') is None
        assert parse_compression("嗯嗯，记住了") is None

    def test_compose_keeps_time_and_causality_once(self) -> None:
        assert compose_summary({"summary": "做了网站"}) == "做了网站"
        assert compose_summary({"summary": "做了网站", "time": "三月底"}) == "做了网站（三月底）"
        # a detail the sentence already carries is not appended twice
        assert compose_summary({"summary": "三月底做了网站", "time": "三月底"}) == "三月底做了网站"
        assert compose_summary({"summary": ""}) == ""

    @pytest.mark.parametrize("reply", [MODEL_REPLY, "not json at all", ""])
    def test_parse_never_raises(self, reply: str) -> None:
        parse_compression(reply)  # must not raise for any reply


class TestLlmCompression:
    async def test_model_writes_the_semantic_memory(self, tmp_path) -> None:
        report, manager, database, provider = await seed_and_consolidate(tmp_path, use_llm=True)
        try:
            memory = await semantic_memory(manager)
            assert memory.content == (
                "用户把自己的网站页面做完了，还加上了音乐播放器"
                "（三月底；因为想边听歌边写代码，所以先把播放器做了）"
            )
            assert report.compressed_clusters == 1
            assert report.llm_compressions == 1
            assert provider.call_count("A") == 1  # one call per cluster
            assert memory.source == "system"
        finally:
            await database.close()

    async def test_sources_stay_linked_and_archived(self, tmp_path) -> None:
        report, manager, database, _ = await seed_and_consolidate(tmp_path, use_llm=True)
        try:
            memory = await semantic_memory(manager)
            rows = await database.fetchall(
                "SELECT from_id, to_id, relation FROM memory_relations WHERE relation = ?",
                ("compressed_from",),
            )
            assert {int(row["from_id"]) for row in rows} == {memory.id}
            assert len(rows) == len(EPISODES)
            archived = await manager.repository.candidates(["user:1"], statuses=("archived",))
            assert len(archived) == len(EPISODES)
            assert report.compressed_sources == len(EPISODES)
        finally:
            await database.close()


class TestFallback:
    async def test_provider_error_keeps_the_rule_summary(self, tmp_path, caplog) -> None:
        from app.ai.errors import RateLimitError

        with caplog.at_level(logging.WARNING, logger="CatooBot.Memory.Consolidation"):
            report, manager, database, _ = await seed_and_consolidate(
                tmp_path, use_llm=True, reply=RateLimitError("mock", model="A")
            )
        try:
            memory = await semantic_memory(manager)
            assert memory.content.startswith("用户在这段时间里陆续聊到：")
            assert report.compressed_clusters == 1
            assert report.llm_compressions == 0
            assert any("keeping rules" in r.getMessage() for r in caplog.records)
        finally:
            await database.close()

    async def test_unparsable_reply_keeps_the_rule_summary(self, tmp_path, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger="CatooBot.Memory.Consolidation"):
            report, manager, database, _ = await seed_and_consolidate(
                tmp_path, use_llm=True, reply="嗯嗯，记住了"
            )
        try:
            memory = await semantic_memory(manager)
            assert memory.content.startswith("用户在这段时间里陆续聊到：")
            assert report.llm_compressions == 0
            assert any("no usable JSON" in r.getMessage() for r in caplog.records)
        finally:
            await database.close()

    async def test_disabled_flag_never_calls_the_model(self, tmp_path) -> None:
        report, manager, database, provider = await seed_and_consolidate(tmp_path, use_llm=False)
        try:
            memory = await semantic_memory(manager)
            assert memory.content.startswith("用户在这段时间里陆续聊到：")
            assert provider.call_count("A") == 0
            assert report.llm_compressions == 0
        finally:
            await database.close()
