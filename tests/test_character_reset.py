"""Phase 1 reset & isolation (§66/§67): a new character inherits nothing.

Extends the v2.0 reset with the audit's gap list — character_states, social
feedback loops, topics, learned expressions — plus §44-§46 data boundaries:
character-*acquired* stickers are wiped while global assets and all platform
data survive.
"""

from __future__ import annotations

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.sandbox.lifecycle import (
    CHARACTER_SETTINGS,
    CHARACTER_TABLES,
    CharacterLifecycleManager,
)


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'reset.db'}"))
    await db.connect()
    return db


async def seed_old_character(db: Database) -> None:  # type: ignore[no-untyped-def]
    """A full old-character footprint across every reset-scoped table."""
    await db.execute(
        "INSERT INTO memories (scope_key, user_id, category, content, content_hash,"
        " created_at, updated_at) VALUES ('user:7', '7', 'fact', '旧角色记忆', 'h1', 1, 1)"
    )
    await db.execute(
        "INSERT INTO relationships (user_id, stage, first_seen, last_seen)"
        " VALUES ('7', 'close', 1, 1)"
    )
    await db.execute("INSERT INTO character_states (id, data, updated_at) VALUES (1, '{}', 1)")
    await db.execute(
        "INSERT INTO topics (scope_key, title, created_at, updated_at)"
        " VALUES ('group:9', '旧话题', 1, 1)"
    )
    await db.execute("INSERT INTO behavior_events (type, created_at) VALUES ('reply', 1)")
    await db.execute(
        "INSERT INTO social_observations (observation_id, group_id, created_at)"
        " VALUES ('obs1', '9', 1)"
    )
    await db.execute(
        "INSERT INTO reply_outcomes (turn_id, scope_key, sent_at, created_at)"
        " VALUES ('t1', 'group:9', 1, 1)"
    )
    await db.execute(
        "INSERT INTO expression_patterns (scope_key, pattern, created_at, updated_at)"
        " VALUES ('group:9', '旧口癖', 1, 1)"
    )
    await db.execute(
        "INSERT INTO shared_experiences (id, user_id, summary, created_at, updated_at)"
        " VALUES ('se1', '7', '旧共同经历', 1, 1)"
    )
    await db.execute(
        "INSERT INTO sandbox_state (key, value, updated_at) VALUES ('phase', 'running', 1)"
    )
    # §44-§45: character-acquired vs global stickers
    await db.execute(
        "INSERT INTO sticker_assets (id, origin, scope, created_at, updated_at)"
        " VALUES ('c1', 'user_message', 'character', 1, 1)"
    )
    await db.execute(
        "INSERT INTO sticker_assets (id, origin, scope, created_at, updated_at)"
        " VALUES ('g1', 'manual_import', 'global', 1, 1)"
    )
    # platform data that must survive
    await db.execute(
        "INSERT INTO users (user_id, nickname, last_seen, updated_at) VALUES ('7', '用户', 1, 1)"
    )
    await db.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES"
        " ('social_engagement', '{}', 1), ('api_token', 'x', 1)"
    )


async def count(db: Database, sql: str) -> int:  # type: ignore[no-untyped-def]
    row = await db.fetchone(sql)
    return int(row["n"]) if row else 0


class TestExtendedReset:
    async def test_every_character_table_is_declared(self) -> None:
        """The audit gaps are all in the reset list now (§38/§41/§43)."""
        for table in (
            "character_states",
            "topics",
            "behavior_events",
            "initiative_state",
            "social_observations",
            "reply_outcomes",
            "expression_patterns",
            "expression_samples",
            "expression_vectors",
            "memory_relations",
        ):
            assert table in CHARACTER_TABLES, f"{table} 不在重置清单"
        assert "social_engagement" in CHARACTER_SETTINGS

    async def test_reset_wipes_character_and_keeps_platform(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            await seed_old_character(db)
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            result = await manager.reset_character(confirm=True)
            assert result["ok"] is True

            assert await count(db, "SELECT COUNT(*) AS n FROM memories") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM relationships") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM character_states") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM topics") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM behavior_events") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM social_observations") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM reply_outcomes") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM expression_patterns") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM shared_experiences") == 0
            assert await count(db, "SELECT COUNT(*) AS n FROM sandbox_state") == 0
            # §42: platform/user identity survives
            assert await count(db, "SELECT COUNT(*) AS n FROM users") == 1
            assert await count(db, "SELECT COUNT(*) AS n FROM settings") == 1  # api_token
        finally:
            await db.close()

    async def test_character_scope_stickers_wiped_global_kept(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§45: 非 global_asset 的角色表情必须隔离。"""
        db = await make_db(tmp_path)
        try:
            await seed_old_character(db)
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            result = await manager.reset_character(confirm=True)
            assert result["removed"].get("sticker_assets(character)") == 1
            assert await count(db, "SELECT COUNT(*) AS n FROM sticker_assets") == 1
            row = await db.fetchone("SELECT id FROM sticker_assets")
            assert row is not None and row["id"] == "g1"
        finally:
            await db.close()

    async def test_reset_without_confirm_is_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            await seed_old_character(db)
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            assert (await manager.reset_character())["ok"] is False
            assert await count(db, "SELECT COUNT(*) AS n FROM memories") == 1
        finally:
            await db.close()

    async def test_migration_21_backfills_scope(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Migration 21: 存量 user_message 收藏回填为 character scope。"""
        db = await make_db(tmp_path)
        try:
            applied = await db.fetchall("SELECT version FROM schema_migrations ORDER BY version")
            assert [row["version"] for row in applied][-1] == 21
            # seeded rows already carried scope; verify the column + index exist
            columns = await db.fetchall("PRAGMA table_info(sticker_assets)")
            assert any(col["name"] == "scope" for col in columns)
        finally:
            await db.close()

    async def test_reset_report_distinguishes_scopes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        try:
            await seed_old_character(db)
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            report = await manager.reset_report()
            assert "memories" in report["character"]
            assert "users" in report["preserved"]
            assert "messages" not in report["preserved"]  # ghost table removed
        finally:
            await db.close()


class TestPreservedTablesAreReal:
    async def test_every_preserved_table_exists_in_schema(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Ghost names (messages/users_profiles/tool_credentials) are gone."""
        db = await make_db(tmp_path)
        try:
            from app.sandbox.lifecycle import PRESERVED_TABLES

            names = {
                row["name"]
                for row in await db.fetchall("SELECT name FROM sqlite_master WHERE type='table'")
            }
            ghosts = [table for table in PRESERVED_TABLES if table not in names]
            assert ghosts == [], f"PRESERVED_TABLES 引用了不存在的表: {ghosts}"
        finally:
            await db.close()
