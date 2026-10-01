"""Character subsystem tests: persona, state, relationship, runtime."""

from __future__ import annotations

from app.character.persona import Persona
from app.character.persona_manager import PersonaManager
from app.character.relationship import RelationshipManager, stage_for_interactions
from app.character.state import CharacterState, StateManager
from app.config.settings import CharacterConfig
from tests.ai_mocks import MockAIProvider


def make_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'char.db'}"))


class TestPersona:
    def test_default_persona_empty(self) -> None:
        persona = Persona()
        assert not persona.is_configured()
        assert persona.identity.name == ""

    def test_from_config_wraps_rules_list(self) -> None:
        persona = Persona.from_config(
            {
                "identity": {"name": "小星"},
                "behavior_rules": ["不骂人", "不说教"],
                "system_prompt": "你是小星。",
            }
        )
        assert persona.is_configured()
        assert persona.identity.name == "小星"
        assert persona.behavior_rules.rules == ["不骂人", "不说教"]

    def test_json_roundtrip(self) -> None:
        persona = Persona.from_config({"identity": {"name": "A"}})
        assert Persona.from_json(persona.to_json()).identity.name == "A"

    async def test_manager_db_persistence(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        manager = PersonaManager(CharacterConfig(), database)
        await manager.save(Persona.from_config({"identity": {"name": "小月"}}))
        await database.close()

        database2 = make_db(tmp_path)
        await database2.connect()
        manager2 = PersonaManager(CharacterConfig(), database2)
        persona = await manager2.load()
        assert persona.identity.name == "小月"
        await database2.close()

    async def test_invalid_stored_persona_keeps_current(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        await database.set_setting_json("active_persona", {"bogus": True}, 1)
        manager = PersonaManager(CharacterConfig(), database)
        persona = await manager.load()
        assert persona.name == "default"  # fell back to config/default persona
        await database.close()


class TestState:
    def test_decay_returns_to_neutral(self) -> None:
        state = CharacterState(mood="happy", mood_updated_at=1000)
        later = state.decayed(1000 + 4 * 3600 + 1)
        assert later.mood == "neutral"
        # within the decay window the mood persists
        assert state.decayed(1000 + 100).mood == "happy"

    async def test_persistence_roundtrip(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        manager = StateManager(database)
        await manager.update(mood="excited", activity="打游戏", energy=0.4)
        manager2 = StateManager(database)
        state = await manager2.load()
        assert state.mood == "excited"
        assert state.activity == "打游戏"
        assert state.energy == 0.4
        await database.close()


class TestRelationship:
    async def test_stage_progression(self, tmp_path) -> None:
        assert stage_for_interactions(1) == "new"
        assert stage_for_interactions(5) == "familiar"
        assert stage_for_interactions(30) == "close"
        assert stage_for_interactions(100) == "very_close"

    async def test_interaction_counts_and_persistence(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        manager = RelationshipManager(database)
        for _ in range(6):
            rel = await manager.record_interaction(7)
        assert rel.interaction_count == 6
        assert rel.stage == "familiar"

        manager2 = RelationshipManager(database)
        rel2 = await manager2.get(7)
        assert rel2.interaction_count == 6
        assert rel2.first_seen > 0
        await database.close()

    async def test_users_are_isolated(self, tmp_path) -> None:
        database = make_db(tmp_path)
        await database.connect()
        manager = RelationshipManager(database)
        await manager.record_interaction(1)
        await manager.record_interaction(1)
        rel2 = await manager.get(2)
        assert rel2.interaction_count == 0
        await database.close()


class TestRuntime:
    async def test_respond_uses_persona_and_memory(self, tmp_path) -> None:
        from app.character.runtime import CharacterRuntime
        from app.config.settings import MemoryConfig
        from app.memory.manager import MemoryManager

        database = make_db(tmp_path)
        await database.connect()
        provider = MockAIProvider(behaviors={"A": ["当然记得！"]})
        from app.ai.engine import AIEngine
        from app.config.settings import AIConfig

        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        memory = MemoryManager(MemoryConfig(), database)
        await memory.remember("user", "9", "用户喜欢猫", category="preference")
        persona_manager = PersonaManager(CharacterConfig(identity={"name": "小星"}), database)
        runtime = CharacterRuntime(persona_manager, engine, memory, database=database)
        await runtime.start()

        reply = await runtime.respond("private:9", 9, "我之前说过喜欢什么来着？")
        assert reply == "当然记得！"
        system = provider.calls[0]["messages"][0].content
        assert "用户喜欢猫" in system
        assert "小星" in system
        # relationship was recorded
        rel = await runtime.relationships.get(9)
        assert rel.interaction_count == 1
        await database.close()

    async def test_memory_failure_degrades_gracefully(self, tmp_path) -> None:
        from app.ai.engine import AIEngine
        from app.character.runtime import CharacterRuntime
        from app.config.settings import AIConfig

        database = make_db(tmp_path)
        await database.connect()
        provider = MockAIProvider(behaviors={"A": ["嗯嗯"]})
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        runtime = CharacterRuntime(
            PersonaManager(CharacterConfig(), database),
            engine,
            memory_manager=None,
            database=database,
        )
        await runtime.start()
        reply = await runtime.respond("private:1", 1, "在吗")
        assert reply == "嗯嗯"
        await database.close()
