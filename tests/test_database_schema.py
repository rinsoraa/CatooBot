"""Schema hygiene + character-reset scope (Task 8).

The v1.x world was deleted in v2.0 but five of its tables stayed behind as
empty schema, and the reset list kept a stale name (``memory_vectors``) that
matched no table — so a character reset silently left every embedding behind.
These tests keep both failure modes from coming back:

* the drop migration must actually run, without harming the live schema;
* every table the reset claims to wipe must exist (a typo = silent data kept);
* a reset must clear memories *and* their vectors, while never touching
  platform data.
"""

from __future__ import annotations

from array import array

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.sandbox.lifecycle import CHARACTER_TABLES, CharacterLifecycleManager

WORLD_TABLES = (
    "world_events",
    "persistent_goals",
    "character_projects",
    "world_snapshots",
    "activity_episodes",
)


async def make_db(tmp_path):  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'schema.db'}"))
    await db.connect()
    return db


async def table_names(db: Database) -> set[str]:
    rows = await db.fetchall("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row["name"]) for row in rows}


class TestMigrationChain:
    async def test_world_tables_are_dropped_and_the_live_schema_survives(self, tmp_path) -> None:
        db = await make_db(tmp_path)
        try:
            tables = await table_names(db)
            assert not (set(WORLD_TABLES) & tables)
            # the tables the sandbox actually uses are untouched
            assert {
                "memories",
                "memory_embeddings",
                "character_states",
                "sandbox_entities",
            } <= tables

            applied = await db.fetchall(
                "SELECT version, name FROM schema_migrations ORDER BY version"
            )
            versions = [row["version"] for row in applied]
            assert versions == sorted(versions)
            # the drop migration is present (newer migrations may follow it)
            assert (14, "drop v1.x world tables") in [
                (row["version"], row["name"]) for row in applied
            ]
        finally:
            await db.close()


class TestResetScope:
    async def test_every_listed_table_exists(self, tmp_path) -> None:
        """A stale name in the list means a reset keeps data it claims to wipe."""
        db = await make_db(tmp_path)
        try:
            missing = sorted(set(CHARACTER_TABLES) - await table_names(db))
            assert missing == []
        finally:
            await db.close()

    async def test_reset_clears_memories_and_their_vectors(self, tmp_path) -> None:
        db = await make_db(tmp_path)
        try:
            await db.execute(
                "INSERT INTO memories (scope_key, category, content, content_hash,"
                " created_at, updated_at) VALUES ('user:1', 'fact', '用户喜欢猫',"
                " 'h1', 0, 0)"
            )
            await db.execute(
                "INSERT INTO memory_embeddings (memory_id, model, dimensions, version,"
                " vector_blob, norm, created_at, updated_at)"
                " VALUES (1, 'm', 3, 1, ?, 1.0, 0, 0)",
                (array("d", [0.1, 0.2, 0.3]).tobytes(),),
            )
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            result = await manager.reset_character(confirm=True)
            assert result["ok"] is True
            assert (await db.fetchone("SELECT COUNT(*) AS n FROM memories"))["n"] == 0
            assert (await db.fetchone("SELECT COUNT(*) AS n FROM memory_embeddings"))["n"] == 0
            assert list((tmp_path / "backup").glob("reset_*.json"))  # reversible
        finally:
            await db.close()

    async def test_reset_never_touches_platform_data(self, tmp_path) -> None:
        db = await make_db(tmp_path)
        try:
            await db.execute("INSERT INTO users (user_id, nickname) VALUES ('9', '空凛猫')")
            await db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('web_password_hash', 'x', 0)"
            )
            manager = CharacterLifecycleManager(db, backup_dir=tmp_path / "backup")
            await manager.reset_character(confirm=True)
            assert (await db.fetchone("SELECT COUNT(*) AS n FROM users"))["n"] == 1
            row = await db.fetchone(
                "SELECT COUNT(*) AS n FROM settings WHERE key = 'web_password_hash'"
            )
            assert row["n"] == 1
        finally:
            await db.close()
