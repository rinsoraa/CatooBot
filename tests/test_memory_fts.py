"""Keyword channel over SQLite FTS5 (Task 12, step 1).

The point of the index is *recall*: the repository's candidate query is capped
by importance, so before this a memory that matched the query perfectly could
never reach the keyword channel. FTS5 searches the whole scope instead, and the
hits join the candidate pool that the (unchanged) Python scorer ranks.

Covered here: index maintenance (insert / update / delete / legacy backfill),
short Chinese queries, scope isolation, the recall gain that motivated it, and
that scoring stays identical for the same pool.
"""

from __future__ import annotations

import time

from app.config.settings import DatabaseConfig, MemoryConfig
from app.database.database import Database
from app.memory.keyword_index import KeywordIndex, index_text, match_expression
from app.memory.manager import MemoryManager
from app.memory.model import Memory
from app.memory.retrieval import bigrams

SCOPE = "user:1"


async def make_manager(tmp_path) -> tuple[MemoryManager, Database]:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'fts.db'}"))
    await db.connect()
    config = MemoryConfig(semantic={"enabled": False})
    return MemoryManager(config, db), db


async def seed(  # type: ignore[no-untyped-def]
    manager: MemoryManager, items: list[str], *, scope: str = SCOPE, importance: float = 0.5
) -> list[int]:
    """Insert rows straight through the repository.

    ``remember()`` dedups near-identical content and enforces a quota, which is
    right for production and wrong for building a *controlled* corpus — the
    index only ever sees what the repository wrote.
    """
    ids: list[int] = []
    for content in items:
        memory = await manager.repository.add(
            Memory(scope_key=scope, content=content, summary="", importance=importance)
        )
        ids.append(memory.id)
    return ids


class TestTokenizer:
    def test_index_text_matches_bigrams(self) -> None:
        text = "冰箱里还有十罐可乐"
        assert set(index_text(text).split(" ")) == bigrams(text)

    def test_match_expression_quotes_tokens(self) -> None:
        assert match_expression("可乐") == '"乐" OR "可" OR "可乐"'

    def test_empty_query_has_no_expression(self) -> None:
        assert match_expression("   ") == ""


class TestIndexMaintenance:
    async def test_insert_update_delete_keep_the_index_in_sync(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            (memory_id,) = await seed(manager, ["冰箱里还有十罐可乐"])
            hit = await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=5)
            assert hit == [memory_id]

            memory = await manager.repository.get(memory_id)
            assert memory is not None
            await manager.repository.update(memory.model_copy(update={"content": "可乐喝完了"}))
            assert await manager.keyword_index.search("冰箱", scope_keys=[SCOPE], limit=5) == []
            assert await manager.keyword_index.search("喝", scope_keys=[SCOPE], limit=5) == [
                memory_id
            ]

            await manager.repository.delete(memory_id)
            assert await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=5) == []
        finally:
            await db.close()

    async def test_legacy_rows_are_backfilled_once(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            (memory_id,) = await seed(manager, ["用户养了一只叫抹茶的猫"])
            # simulate a row written before the index existed
            await db.execute("UPDATE memories SET search_text = '' WHERE id = ?", (memory_id,))
            assert await manager.keyword_index.search("抹茶", scope_keys=[SCOPE], limit=5) == []

            index = KeywordIndex(db)
            assert await index.ensure_ready() == 1  # one legacy row filled
            assert await index.ensure_ready() == 0  # …and only once
            assert await manager.keyword_index.search("抹茶", scope_keys=[SCOPE], limit=5) == [
                memory_id
            ]
        finally:
            await db.close()

    async def test_scope_isolation(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            (mine,) = await seed(manager, ["我喜欢喝可乐"])
            await seed(manager, ["别人也喜欢喝可乐"], scope="group:9")
            assert await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=5) == [mine]
            both = await manager.keyword_index.search(
                "可乐", scope_keys=[SCOPE, "group:9"], limit=5
            )
            assert len(both) == 2
        finally:
            await db.close()


class TestShortChineseQueries:
    async def test_one_and_two_character_queries_match(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            (cat,) = await seed(manager, ["用户养了一只叫抹茶的猫"])
            (cola,) = await seed(manager, ["冰箱里还有十罐可乐"])
            assert await manager.keyword_index.search("猫", scope_keys=[SCOPE], limit=5) == [cat]
            assert await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=5) == [cola]
            assert await manager.keyword_index.search("抹茶猫", scope_keys=[SCOPE], limit=5) != []
        finally:
            await db.close()


class TestRecallGain:
    async def test_match_below_the_importance_cut_is_still_found(self, tmp_path) -> None:
        """The candidate list is capped at 200 by importance — the exact match
        sits below the cut, so only the index can surface it."""
        manager, db = await make_manager(tmp_path)
        try:
            (needle,) = await seed(manager, ["上周把家里的旧吉他送给了表弟"], importance=0.1)
            await seed(manager, [f"普通琐事 {i}" for i in range(250)], importance=0.9)

            pool = await manager.repository.candidates([SCOPE], limit=200)
            assert needle not in {memory.id for memory in pool}  # invisible before

            candidates = await manager._candidates_for("吉他", [SCOPE], limit=200)
            assert needle in {memory.id for memory in candidates}
        finally:
            await db.close()

    async def test_scoring_is_unchanged_for_the_same_pool(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await seed(manager, ["冰箱里还有十罐可乐", "用户喜欢猫", "今天不下雨"])
            pool = await manager.repository.candidates([SCOPE], limit=200)
            scored = await manager.retriever.retrieve("可乐", pool, use_cache=False)
            assert scored and scored[0].memory.content == "冰箱里还有十罐可乐"
            # the index must not invent candidates outside the scoring path
            index_only = await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=5)
            assert index_only == [scored[0].memory.id]
        finally:
            await db.close()


class TestScale:
    async def test_index_lookup_beats_a_python_scan(self, tmp_path) -> None:
        manager, db = await make_manager(tmp_path)
        try:
            await seed(manager, [f"第 {i} 条生活记录：今天做了点事情 {i}" for i in range(400)])
            await seed(manager, ["冰箱里还有十罐可乐"])
            rows = await db.fetchall("SELECT id, content, summary FROM memories")
            query_tokens = bigrams("可乐")

            started = time.perf_counter()
            for _ in range(20):
                hits = await manager.keyword_index.search("可乐", scope_keys=[SCOPE], limit=20)
            indexed_ms = (time.perf_counter() - started) * 1000

            started = time.perf_counter()
            for _ in range(20):
                scanned = [
                    row["id"]
                    for row in rows
                    if query_tokens & bigrams(f"{row['content']} {row['summary']}")
                ]
            scan_ms = (time.perf_counter() - started) * 1000

            assert hits == scanned
            assert indexed_ms < scan_ms  # the index wins even at this size
        finally:
            await db.close()
