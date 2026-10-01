"""Memory-correction tests (v0.9 UI work).

The key invariant: correcting "用户喜欢吃西瓜 → 用户喜欢吃草莓" must leave the
database in a state where the *new* fact is the only thing retrieval sees, with
no "corrected/changed" marker anywhere — so the character genuinely believes
"用户一直喜欢吃草莓", not "曾经喜欢西瓜后来改了".
"""

from __future__ import annotations

import json


async def make_memory_manager(tmp_path):
    from app.config.settings import DatabaseConfig, MemoryConfig
    from app.database.database import Database
    from app.memory.manager import MemoryManager

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'mem.db'}"))
    await database.connect()
    manager = MemoryManager(MemoryConfig(enabled=True), database)
    return manager, database


class TestForceSupersede:
    async def test_corrected_fact_is_the_only_active_one(self, tmp_path) -> None:
        manager, database = await make_memory_manager(tmp_path)
        old = await manager.remember(
            "user", "123456789", "用户喜欢吃西瓜", category="preference", user_id="123456789"
        )
        replacement = await manager.force_supersede(
            old.id, content="用户喜欢吃草莓", category="preference"
        )
        assert replacement is not None

        active = await manager.list_memories(scope_key="user:123456789", status="active")
        assert [m.content for m in active] == ["用户喜欢吃草莓"]

        # the old fact is retired, not deleted (audit trail intact)
        history = await manager.list_memories(scope_key="user:123456789", status="superseded")
        assert [m.content for m in history] == ["用户喜欢吃西瓜"]
        assert replacement.supersedes_id == old.id
        await database.close()

    async def test_no_correction_marker_in_the_new_fact(self, tmp_path) -> None:
        manager, database = await make_memory_manager(tmp_path)
        old = await manager.remember("user", "1", "用户不喜欢出门", category="habit", user_id="1")
        replacement = await manager.force_supersede(old.id, content="用户喜欢宅在家")
        for marker in ("改成", "更正", "不再", "以前", "现在", "更新", "修正"):
            assert marker not in replacement.content
        await database.close()

    async def test_retrieval_never_returns_the_old_fact(self, tmp_path) -> None:
        manager, database = await make_memory_manager(tmp_path)
        old = await manager.remember(
            "user", "1", "用户喜欢养猫", category="preference", user_id="1"
        )
        await manager.force_supersede(old.id, content="用户对猫过敏")
        rows = await manager.retrieve_for_session("user:1", "猫")
        contents = [row.content if hasattr(row, "content") else row for row in rows]
        flat = " ".join(str(item) for item in contents)
        assert "过敏" in flat
        assert "喜欢养猫" not in flat
        await database.close()

    async def test_missing_memory_returns_none(self, tmp_path) -> None:
        manager, database = await make_memory_manager(tmp_path)
        assert await manager.force_supersede(9999, content="x") is None
        await database.close()

    async def test_forget_archives_instead_of_deleting(self, tmp_path) -> None:
        manager, database = await make_memory_manager(tmp_path)
        memory = await manager.remember("user", "1", "一条会被归档的记忆", user_id="1")
        assert await manager.forget(memory.id, reason="webui") is True
        assert await manager.forget(12345) is False
        archived = await manager.list_memories(scope_key="user:1", status="archived")
        assert [m.content for m in archived] == ["一条会被归档的记忆"]
        await database.close()


class TestCorrectionService:
    async def _service(self, tmp_path, reply: str):
        from app.config.settings import AIConfig
        from app.web.services.memory_correction import MemoryCorrectionService
        from tests.ai_mocks import MockAIProvider
        from tests.conftest import FakeAdapter, make_bot

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        from app.ai.engine import AIEngine

        engine = AIEngine(
            AIConfig(
                enabled=True,
                models=[{"name": "a", "provider": "mock", "model": "a"}],
            ),
            bot.database,
            providers={"mock": MockAIProvider(behaviors={"a": [reply]})},
        )
        bot.ai = engine
        await bot.memory.remember(
            "user", "123456789", "用户喜欢吃西瓜", category="preference", user_id="123456789"
        )
        return MemoryCorrectionService(bot), bot

    async def test_plan_proposes_replace_without_edit_markers(self, tmp_path) -> None:
        reply = json.dumps(
            {
                "action": "replace",
                "memory_id": 1,
                "new_content": "用户喜欢吃草莓",
                "category": "preference",
                "importance": 0.7,
                "reason": "指令要求把西瓜改成草莓",
            },
            ensure_ascii=False,
        )
        service, bot = await self._service(tmp_path, reply)
        plan = await service.plan("user:123456789", "把用户喜欢吃的东西改为草莓")
        assert plan["action"] == "replace"
        assert plan["after"] == "用户喜欢吃草莓"
        assert plan["before"] == "用户喜欢吃西瓜"
        await bot.database.close()

    async def test_apply_changes_what_retrieval_sees(self, tmp_path) -> None:
        reply = json.dumps(
            {
                "action": "replace",
                "memory_id": 1,
                "new_content": "用户喜欢吃草莓",
                "category": "preference",
                "importance": 0.7,
            },
            ensure_ascii=False,
        )
        service, bot = await self._service(tmp_path, reply)
        plan = await service.plan("user:123456789", "把西瓜改成草莓")
        result = await service.apply(plan)
        assert result["changed"] is True

        active = await bot.memory.list_memories(scope_key="user:123456789", status="active")
        assert [m.content for m in active] == ["用户喜欢吃草莓"]
        # audit event recorded
        row = await bot.database.fetchone(
            "SELECT type FROM behavior_events"
            " WHERE type = 'memory_correction' ORDER BY id DESC LIMIT 1"
        )
        assert row is not None and row["type"] == "memory_correction"
        await bot.database.close()

    async def test_model_uncertain_yields_no_change(self, tmp_path) -> None:
        reply = json.dumps(
            {"action": "no_change", "reason": "无法定位目标记忆"}, ensure_ascii=False
        )
        service, bot = await self._service(tmp_path, reply)
        plan = await service.plan("user:123456789", "随便说点什么")
        assert plan["action"] == "no_change"
        result = await service.apply(plan)
        assert result["changed"] is False
        await bot.database.close()

    async def test_create_action_adds_a_fact(self, tmp_path) -> None:
        reply = json.dumps(
            {
                "action": "create",
                "new_content": "用户有一只叫糯米的猫",
                "category": "fact",
                "importance": 0.6,
            },
            ensure_ascii=False,
        )
        service, bot = await self._service(tmp_path, reply)
        plan = await service.plan("user:123456789", "补充：用户有一只猫")
        result = await service.apply(plan)
        assert result["changed"] is True
        contents = [
            m.content
            for m in await bot.memory.list_memories(scope_key="user:123456789", status="active")
        ]
        assert any("糯米" in content for content in contents)
        await bot.database.close()

    async def test_garbage_model_reply_is_rejected_gracefully(self, tmp_path) -> None:
        service, bot = await self._service(tmp_path, "这不是 JSON")
        from app.web.services.memory_correction import MemoryCorrectionService

        assert isinstance(service, MemoryCorrectionService)
        try:
            await service.plan("user:123456789", "把西瓜改成草莓")
        except ValueError as exc:
            assert "更具体" in str(exc)
        await bot.database.close()

    async def test_memory_id_out_of_range_becomes_no_change(self, tmp_path) -> None:
        reply = json.dumps(
            {"action": "replace", "memory_id": 999, "new_content": "用户喜欢草莓"},
            ensure_ascii=False,
        )
        service, bot = await self._service(tmp_path, reply)
        plan = await service.plan("user:123456789", "改一条不存在的记忆")
        assert plan["action"] == "no_change"
        await bot.database.close()
