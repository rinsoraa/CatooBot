"""Phase 5 tests (§30): sandbox life reaches the chat context — and only reads.

Twelve checks: retrieval, exclusion, precedence, isolation, continuity
injection, source separation, no-mutation, determinism, budget, no-LLM,
conversation-memory survival, initiative reuse.
"""

from __future__ import annotations

import time
from pathlib import Path

from app.config.settings import DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.memory.model import Memory, scope_key
from app.memory.repository import MemoryRepository
from app.sandbox.bible import BibleCompiler
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.conftest import private_event
from tests.test_chat_integration import make_character_bot

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


class Clock:
    def __init__(self, now: float | None = None) -> None:
        self.now = float(now if now is not None else time.time())

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'cognitive.db'}"))
    await db.connect()
    return db


async def make_sandbox(db, *, bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock or Clock(),
    )
    await runtime.start()
    return runtime


async def write_memory(db, character_id: str, content: str, *, importance: float = 0.7) -> int:  # type: ignore[no-untyped-def]
    repo = MemoryRepository(db)
    memory = await repo.add(
        Memory(
            scope_key=scope_key("character", character_id),
            category="fact",
            content=content,
            summary=content,
            layer="semantic",
            source="sandbox",
            character_id=character_id,
            importance=importance,
            confidence=0.9,
            dedupe_key=f"{character_id}|world_fact|manual:{content[:12]}",
        )
    )
    return memory.id


async def make_chat_bot(tmp_path, provider, *, sandbox):  # type: ignore[no-untyped-def]
    """A character bot whose sandbox is the provided (db-backed) runtime."""
    bot = await make_character_bot(tmp_path, provider, models=["A"])
    bot.config.sandbox.enabled = True
    bot.sandbox = sandbox
    bot.character.sandbox = sandbox
    return bot


# ---------------------------------------------------------------- Test 1/2


class TestSandboxMemoryEntersChatContext:
    async def test_relevant_life_memory_is_retrieved_and_traced(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        await write_memory(
            db, sandbox.character_id, "图书馆屋顶完工了，小城又多了一座建筑", importance=0.9
        )
        provider = MockAIProvider(behaviors={"A": ["屋顶早就搞定啦"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            await bot.event_bus.emit(private_event("你上次那个屋顶怎么样了？", user_id=777))
            await bot.conversation.wait_idle()

            system = provider.calls[0]["messages"][0].content
            assert "图书馆屋顶" in system, "sandbox memory did not reach the prompt"
            assert "【相关生活记忆】" in system
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()

    async def test_irrelevant_memories_are_excluded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        await write_memory(db, sandbox.character_id, "项目A：图书馆屋顶完工了", importance=0.9)
        await write_memory(db, sandbox.character_id, "项目B：自动农场调试成功", importance=0.9)
        await write_memory(db, sandbox.character_id, "宠物今天精神不错", importance=0.7)

        memories, trace = await sandbox.memory.retrieve_relevant(
            query="图书馆屋顶", limit=5, min_score=0.12
        )
        texts = [m["text"] for m in memories]
        assert any("图书馆" in text for text in texts)
        assert trace["selected"] == len(memories) <= 5
        assert not any("自动农场" in text for text in texts), "unrelated memory injected"

    async def test_no_life_memories_means_no_section(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        provider = MockAIProvider(behaviors={"A": ["嗯嗯"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            await bot.event_bus.emit(private_event("你好呀", user_id=777))
            await bot.conversation.wait_idle()
            system = provider.calls[0]["messages"][0].content
            assert "【相关生活记忆】" not in system
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 3


class TestCurrentWorldPrecedence:
    async def test_stale_memory_and_current_world_coexist_with_precedence(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        await write_memory(db, sandbox.character_id, "冰箱里还有可乐", importance=0.8)
        fridge_key = sandbox._fridge_key  # noqa: SLF001
        sandbox.inventories.get(fridge_key).items = {}  # world truth: none left
        provider = MockAIProvider(behaviors={"A": ["我去看看冰箱"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            trace: dict = {}
            await bot.character.respond(
                "private:777", 777, "冰箱里还有可乐吗？", context_trace=trace
            )
            system = provider.calls[0]["messages"][0].content
            # the memory is present as *reference*…
            assert "【相关生活记忆】" in system
            assert "冰箱里还有可乐" in system
            # …and the section explicitly defers to the current world
            assert "以当前世界状态为准" in system
            layers = {row["layer"]: row for row in trace["layers"]}
            assert layers["sandbox_memory"]["included"] is True
            assert layers["sandbox_facts"]["included"] is True  # world facts layer
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 4


class TestCharacterIsolation:
    async def test_other_characters_memories_never_appear(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        first = await make_sandbox(db)
        second = await make_sandbox(db, bible_path=OTHER_BIBLE_PATH)
        try:
            await write_memory(db, first.character_id, "罐头的图书馆屋顶完工了", importance=0.9)
            await write_memory(db, second.character_id, "阿澈的木工台修好了", importance=0.9)
            memories, _trace = await second.memory.retrieve_relevant(query="屋顶 木工台", limit=5)
            texts = " ".join(m["text"] for m in memories)
            assert "木工台" in texts
            assert "罐头" not in texts and "屋顶" not in texts
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 5/6


class TestContinuityInjection:
    async def test_continuity_and_experience_reach_the_prompt(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        sandbox = await make_sandbox(db, clock=clock)
        started = await sandbox._start_action("play_singleplayer")  # noqa: SLF001
        assert started is not None
        sandbox.current_action.planned_end_at = clock.now
        clock.advance(1)
        await sandbox._complete_action()  # noqa: SLF001
        await sandbox.flush_experiences()
        project_id = next(iter(sandbox.projects))
        sandbox.projects[project_id]["progress"] = 0.5
        sandbox.projects[project_id]["name"] = "小城"
        await sandbox._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001

        provider = MockAIProvider(behaviors={"A": ["在看动画呢"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            trace: dict = {}
            await bot.character.respond("private:777", 777, "在做什么？", context_trace=trace)
            system = provider.calls[0]["messages"][0].content
            assert "【近期延续状态】" in system
            assert "小城" in system  # unfinished project
            assert "【最近发生的经历】" in system
            layers = {row["layer"]: row for row in trace["layers"]}
            assert layers["continuity_snapshot"]["count"] >= 1
            assert layers["recent_experience"]["count"] >= 1
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()

    async def test_conversation_and_sandbox_continuity_stay_separate(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        provider = MockAIProvider(behaviors={"A": ["好呀"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            trace: dict = {}
            v12_continuity = {"current_interest": "聊剧", "unfinished_thought": "还没说完"}
            await bot.character.respond(
                "private:777",
                777,
                "继续聊？",
                continuity=v12_continuity,
                context_trace=trace,
            )
            layers = {row["layer"] for row in trace["layers"]}
            assert "continuity" in layers  # v1.2 conversation continuity
            assert "continuity_snapshot" in layers  # v2.1 sandbox continuity
            # the two did not overwrite each other
            system = provider.calls[0]["messages"][0].content
            assert "聊剧" in system or "还没说完" in system
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 7


class TestNoSandboxMutation:
    async def test_respond_changes_no_sandbox_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """record_interaction=False isolates the bridge: the only thing under
        test here is the *bridge*, since a normal reply also feeds the sandbox
        a social stimulus by design (Phase 3, unchanged)."""
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        await write_memory(db, sandbox.character_id, "之前把屋顶完成了", importance=0.9)
        provider = MockAIProvider(behaviors={"A": ["嗯嗯"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            snapshot_before = (
                sandbox.character.location,
                sandbox.current_action.definition_id if sandbox.current_action else "",
                {k: v.level for k, v in sandbox.needs.all().items()},
                {k: dict(v.items) for k, v in sandbox.inventories.all().items()},
                {k: dict(v) for k, v in sandbox.projects.items()},
                dict(sandbox.knowledge),
                len(sandbox.mutations),
                len(sandbox.events.recent(limit=10_000)),
            )
            await bot.character.respond(
                "private:777", 777, "屋顶做完了吗", context_trace={}, record_interaction=False
            )
            snapshot_after = (
                sandbox.character.location,
                sandbox.current_action.definition_id if sandbox.current_action else "",
                {k: v.level for k, v in sandbox.needs.all().items()},
                {k: dict(v.items) for k, v in sandbox.inventories.all().items()},
                {k: dict(v) for k, v in sandbox.projects.items()},
                dict(sandbox.knowledge),
                len(sandbox.mutations),
                len(sandbox.events.recent(limit=10_000)),
            )
            assert snapshot_before == snapshot_after
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()

    async def test_bridge_is_read_only_by_source(self) -> None:
        """The bridge must not even *name* a mutation API (§14)."""
        root = Path(__file__).resolve().parent.parent / "app" / "sandbox"
        source = (root / "cognitive.py").read_text(encoding="utf-8")
        for forbidden in (
            "adjust_need(",
            "update_project_progress(",
            "set_knowledge(",
            "move_entity(",
            "feed_pet(",
            "take_item(",
            "_start_action(",
            "engine.chat(",
        ):
            assert forbidden not in source, f"cognitive.py 调用了 {forbidden!r}"


# ----------------------------------------------------------------- Test 8/9


class TestDeterministicRetrieval:
    async def test_same_input_same_order(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        for index in range(6):
            await write_memory(
                db,
                sandbox.character_id,
                f"第 {index} 次小城扩建完成",
                importance=0.6 + index * 0.05,
            )
        first, _ = await sandbox.memory.retrieve_relevant(query="小城 扩建", limit=4)
        second, _ = await sandbox.memory.retrieve_relevant(query="小城 扩建", limit=4)
        assert [m["memory_id"] for m in first] == [m["memory_id"] for m in second]
        assert [round(m["score"], 6) for m in first] == [round(m["score"], 6) for m in second]

    async def test_budget_caps_count_and_characters(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        for index in range(12):
            await write_memory(
                db,
                sandbox.character_id,
                f"小城扩建第 {index} 段完工，" + "很长的描述" * 20,
                importance=0.9,
            )
        memories, trace = await sandbox.memory.retrieve_relevant(
            query="小城扩建", limit=3, max_chars=120
        )
        assert len(memories) <= 3
        assert sum(len(m["text"]) for m in memories) <= 120 + 1  # ellipsis slack
        assert trace["selected"] == len(memories)
        assert trace["memory_ids"]

        # highly relevant / important rows rank first even under pressure
        top = await sandbox.memory.retrieve_relevant(query="小城扩建第 11 段", limit=1)
        assert "11" in top[0][0]["text"]

    async def test_superseded_memories_are_never_injected(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        memory_id = await write_memory(db, sandbox.character_id, "冰箱里还有可乐", importance=0.9)
        repo = MemoryRepository(db)
        await repo.set_status(memory_id, "superseded")
        memories, _trace = await sandbox.memory.retrieve_relevant(query="可乐 冰箱")
        assert memories == []


# --------------------------------------------------------------- Test 10/11


class TestExistingLayersSurvive:
    async def test_conversation_memory_still_reaches_context(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        provider = MockAIProvider(behaviors={"A": ["记得呀"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            # write a conversation memory through the existing manager path
            await bot.memory.remember("user", "777", "用户喜欢猫", category="preference")
            trace: dict = {}
            await bot.character.respond("user:777", 777, "还记得我喜欢什么吗", context_trace=trace)
            layers = {row["layer"]: row for row in trace["layers"]}
            assert layers["memory"]["included"] is True or layers["conversation_memory"]
            assert "conversation_memory" in layers
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()

    async def test_sandbox_off_keeps_chat_working(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        provider = MockAIProvider(behaviors={"A": ["好好好"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            trace: dict = {}
            reply = await bot.character.respond("private:777", 777, "你好", context_trace=trace)
            assert reply == "好好好"
            layers = {row["layer"]: row for row in trace["layers"]}
            assert layers["sandbox_memory"]["included"] is False
        finally:
            await bot.shutdown()


# ----------------------------------------------------------------- Test 12


class TestInitiativeReusesBridge:
    async def test_compose_initiative_carries_sandbox_context(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        sandbox = await make_sandbox(db)
        await write_memory(db, sandbox.character_id, "小城图书馆屋顶完工了", importance=0.9)
        provider = MockAIProvider(behaviors={"A": ["跟你说，屋顶弄好了"]})
        bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
        try:
            # initiative goes through respond(): one path, one retrieval chain
            text = await bot.character.compose_initiative(
                session_id="private:777",
                user_id=777,
                reason="life_event",
                topic="屋顶",
            )
            assert text
            system = provider.calls[0]["messages"][0].content
            assert "【相关生活记忆】" in system
            assert "屋顶" in system
            # no second retrieval implementation exists for initiative
            initiative_source = (
                Path(__file__).resolve().parent.parent / "app" / "behavior" / "initiative.py"
            ).read_text(encoding="utf-8")
            assert "retrieve_relevant" not in initiative_source
            assert "sandbox" not in initiative_source
        finally:
            await bot.shutdown()
            await sandbox.shutdown()
            await db.close()

    async def test_ai_unavailable_never_breaks_the_bridge(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§33: chat still works when the sandbox memory layer is absent."""
        provider = MockAIProvider(behaviors={"A": ["没事"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            bot.config.sandbox.enabled = True
            bot.sandbox = None  # sandbox unavailable
            bot.character.sandbox = None
            reply = await bot.character.respond("private:777", 777, "在吗")
            assert reply == "没事"
        finally:
            await bot.shutdown()
