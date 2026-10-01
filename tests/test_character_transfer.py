"""Task 24 milestone 1: character-data export + inspect.

Acceptance points covered here: the package carries the character domain and
nothing else (4), and a large export streams in bounded chunks instead of
buffering the document (7). The tamper/counts tests exist because a backup you
cannot verify is not a backup.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.sandbox.transfer import FORMAT, CharacterDataTransfer


async def make_db(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'transfer.db'}"))
    await database.connect()
    return database


async def make_db_at(tmp_path, name):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    return database


async def seed_character(database) -> None:
    await database.execute(
        "INSERT INTO memories (scope_key, user_id, category, content, content_hash,"
        " importance, confidence, created_at, updated_at, layer)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            "private:7",
            "7",
            "preference",
            "她记得我喜欢草莓蛋糕",
            "h1",
            0.6,
            0.8,
            10,
            10,
            "semantic",
        ),
    )
    await database.execute(
        "INSERT INTO memories (scope_key, user_id, category, content, content_hash,"
        " importance, confidence, created_at, updated_at, layer)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("private:7", "7", "event", "上周我们聊过杭州出差", "h2", 0.4, 0.7, 11, 11, "episodic"),
    )
    await database.execute(
        "INSERT INTO memory_embeddings (memory_id, model, dimensions, vector,"
        " created_at, updated_at, vector_blob, norm)"
        " VALUES (1, 'm', 2, '[0.1,0.2]', 10, 10, ?, 0.5)",
        (b"\x00\x01\x02",),
    )
    await database.execute(
        "INSERT INTO conversation_turns (turn_id, session_id, user_id, text, started_at)"
        " VALUES ('t1', 'private:7', '7', '你好呀', 12.0)"
    )
    await database.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('active_persona', ?, 10)",
        ('{"name": "小夜"}',),
    )


async def seed_platform(database) -> None:
    await database.execute(
        "INSERT INTO users (user_id, nickname, last_seen, updated_at) VALUES ('7', ?, 1, 1)",
        ("SECRET_NICK",),
    )
    await database.execute(
        "INSERT INTO web_users (username, password_hash, created_at) VALUES ('admin', ?, 1)",
        ("SECRET_HASH",),
    )
    await database.execute(
        "INSERT INTO settings (key, value, updated_at) VALUES ('api_token', ?, 1)",
        (json.dumps("SECRET_TOKEN"),),
    )


class TestExport:
    async def test_shape_and_counts(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        try:
            await seed_character(database)
            document = await CharacterDataTransfer(database).export()
        finally:
            await database.close()

        assert document["format"] == FORMAT
        assert document["format_version"] == 1
        assert document["schema_version"] > 0
        assert document["counts"]["memories"] == 2
        assert document["counts"]["memory_embeddings"] == 1
        assert document["counts"]["conversation_turns"] == 1
        assert len(document["tables"]["memories"]) == 2
        assert document["settings"] == {"active_persona": {"name": "小夜"}}
        assert document["missing_tables"] == [] or "missing_tables" in document
        assert document["content_sha256"]

    async def test_a_missing_table_is_reported_not_fatal(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        try:
            document = await CharacterDataTransfer(
                database, tables=("memories", "no_such_table")
            ).export()
        finally:
            await database.close()
        assert document["missing_tables"] == ["no_such_table"]
        assert "no_such_table" not in document["tables"]

    async def test_blobs_travel_as_base64(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        try:
            await seed_character(database)
            document = await CharacterDataTransfer(database).export()
        finally:
            await database.close()
        blob = document["tables"]["memory_embeddings"][0]["vector_blob"]
        assert blob == {"$b64": "AAEC"}, blob

    async def test_attachments_are_a_later_milestone(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        try:
            with pytest.raises(ValueError, match="milestone 2"):
                await CharacterDataTransfer(database).export(include_files=True)
        finally:
            await database.close()

    async def test_export_changes_nothing(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        try:
            await seed_character(database)
            before = await database.fetchone("SELECT COUNT(*) AS n FROM memories")
            await CharacterDataTransfer(database).export()
            after = await database.fetchone("SELECT COUNT(*) AS n FROM memories")
        finally:
            await database.close()
        assert before == after


class TestNoSecrets:
    async def test_platform_data_and_secrets_never_reach_the_file(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "out.json"
        try:
            await seed_character(database)
            await seed_platform(database)
            await CharacterDataTransfer(database).write_export(target)
        finally:
            await database.close()

        text = target.read_text(encoding="utf-8")
        for sentinel in ("SECRET_NICK", "SECRET_HASH", "SECRET_TOKEN", "password_hash"):
            assert sentinel not in text, sentinel
        for table in ("users", "web_users", "user_profiles", "schema_migrations", "sticker_assets"):
            assert f'"{table}"' not in text, table
        # positive control: the character data IS there
        assert "她记得我喜欢草莓蛋糕" in text
        assert '"memories"' in text


class TestWriteAndInspect:
    async def test_roundtrip_verifies(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            transfer = CharacterDataTransfer(database)
            result = await transfer.write_export(target)
            report = await transfer.inspect(target)
        finally:
            await database.close()

        assert target.exists()
        assert result.bytes == target.stat().st_size
        assert result.rows == 4, result.tables
        assert report["ok"] is True, report
        assert report["sha256_ok"] is True
        assert report["counts_match"] is True
        assert report["format_ok"] is True
        assert report["tables"]["memories"] == 2
        assert report["settings"] == ["active_persona"]
        assert report["schema_newer_than_code"] is False

    async def test_no_leftover_part_file(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            await CharacterDataTransfer(database).write_export(target)
        finally:
            await database.close()
        assert not Path(str(target) + ".part").exists()

    async def test_tampering_with_a_row_is_detected(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            transfer = CharacterDataTransfer(database)
            await transfer.write_export(target)
        finally:
            await database.close()

        document = json.loads(target.read_text(encoding="utf-8"))
        document["tables"]["memories"][0]["content"] = "被改过的内容"
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        report = await transfer.inspect(target)
        assert report["sha256_ok"] is False
        assert report["ok"] is False

    async def test_a_doctored_count_is_detected(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            transfer = CharacterDataTransfer(database)
            await transfer.write_export(target)
        finally:
            await database.close()

        document = json.loads(target.read_text(encoding="utf-8"))
        document["counts"]["memories"] = 99
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        report = await transfer.inspect(target)
        assert report["sha256_ok"] is True, "counts are recomputed, not hashed"
        assert report["counts_match"] is False
        assert report["ok"] is False

    async def test_inspect_does_not_touch_the_file(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            transfer = CharacterDataTransfer(database)
            await transfer.write_export(target)
        finally:
            await database.close()
        before = target.read_bytes()
        await transfer.inspect(target)
        assert target.read_bytes() == before

    async def test_missing_file_is_reported(self, tmp_path) -> None:
        report = await CharacterDataTransfer(database=None).inspect(tmp_path / "nope.json")
        assert report["ok"] is False
        assert report["reason"] == "file_not_found"

    async def test_a_newer_format_version_is_not_ok(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "pkg.json"
        try:
            await seed_character(database)
            transfer = CharacterDataTransfer(database)
            await transfer.write_export(target)
        finally:
            await database.close()
        document = json.loads(target.read_text(encoding="utf-8"))
        document["format_version"] = 99
        target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        report = await transfer.inspect(target)
        assert report["format_ok"] is False
        assert report["ok"] is False


class _Recording(CharacterDataTransfer):
    """Records every write so the test can prove the document never lands in
    one buffer."""

    def __init__(self, *args, **kwargs) -> None:  # type: ignore[no-untyped-def]
        super().__init__(*args, **kwargs)
        self.chunks: list[int] = []

    def _emit(self, handle, text: str) -> None:  # type: ignore[no-untyped-def]
        self.chunks.append(len(text))
        super()._emit(handle, text)


class TestStreaming:
    async def test_a_large_export_is_written_in_small_chunks(self, tmp_path) -> None:
        database = await make_db(tmp_path)
        target = tmp_path / "big.json"
        content = "记" * 300
        try:
            for index in range(1500):
                await database.execute(
                    "INSERT INTO memories (scope_key, user_id, category, content, content_hash,"
                    " importance, confidence, created_at, updated_at, layer)"
                    " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        "private:7",
                        "7",
                        "fact",
                        f"{content}{index}",
                        f"h{index}",
                        0.5,
                        0.7,
                        1,
                        1,
                        "semantic",
                    ),
                )
            transfer = _Recording(database)
            result = await transfer.write_export(target)
            report = await transfer.inspect(target)
        finally:
            await database.close()

        assert result.tables["memories"] == 1500
        assert result.bytes > 200_000, "the document must be big enough to prove the point"
        assert max(transfer.chunks) < 65_536, f"largest chunk {max(transfer.chunks)} characters"
        assert len(transfer.chunks) > 1500, "one write per row at least"
        assert report["ok"] is True, report


class TestImport:
    """Task 24 milestone 2: import_character (dry-run → backup → tx → self-check)."""

    async def _package(self, tmp_path, source_name="src.db") -> Path:
        source = await make_db_at(tmp_path, source_name)
        try:
            await seed_character(source)
            transfer = CharacterDataTransfer(source)
            pkg = tmp_path / "pkg.json"
            await transfer.write_export(pkg)
        finally:
            await source.close()
        return pkg

    def _transfer(self, database, tmp_path):  # type: ignore[no-untyped-def]
        return CharacterDataTransfer(database, backup_dir=tmp_path / "backups")

    async def test_roundtrip_to_a_fresh_db(self, tmp_path) -> None:
        pkg = await self._package(tmp_path)
        target = await make_db_at(tmp_path, "target.db")
        try:
            report = await self._transfer(target, tmp_path).import_character(
                pkg, confirm=True, dry_run=False
            )
            assert report["ok"] is True, report
            assert report["verified"]["ok"] is True, report
            rows = await target.fetchall("SELECT content FROM memories ORDER BY id")
            assert len(rows) == 2
            assert {r["content"] for r in rows} == {"她记得我喜欢草莓蛋糕", "上周我们聊过杭州出差"}
        finally:
            await target.close()

    async def test_dry_run_writes_nothing(self, tmp_path) -> None:
        pkg = await self._package(tmp_path)
        target = await make_db_at(tmp_path, "target.db")
        await seed_character(target)  # existing data
        try:
            before = await target.fetchone("SELECT COUNT(*) AS n FROM memories")
            report = await self._transfer(target, tmp_path).import_character(
                pkg, confirm=False, dry_run=True
            )
            after = await target.fetchone("SELECT COUNT(*) AS n FROM memories")
            assert report["ok"] is True and report["dry_run"] is True
            assert before == after, "干跑不得写库"
        finally:
            await target.close()

    async def test_requires_confirmation(self, tmp_path) -> None:
        pkg = await self._package(tmp_path)
        target = await make_db_at(tmp_path, "target.db")
        try:
            report = await self._transfer(target, tmp_path).import_character(
                pkg, confirm=False, dry_run=False
            )
            assert report["ok"] is False
            assert report["reason"] == "confirmation_required"
        finally:
            await target.close()

    async def test_overwrites_and_backs_up(self, tmp_path) -> None:
        pkg = await self._package(tmp_path)
        target = await make_db_at(tmp_path, "target.db")
        await seed_character(target)
        # put a marker the package does not contain, so overwrite is provable
        await target.execute(
            "INSERT INTO memories (scope_key, user_id, category, content, content_hash,"
            " importance, confidence, created_at, updated_at, layer)"
            " VALUES ('private:9', '9', 'fact', '旧数据该被清掉', 'h9', 0.5, 0.7, 1, 1, 'semantic')"
        )
        try:
            report = await self._transfer(target, tmp_path).import_character(
                pkg, confirm=True, dry_run=False
            )
            assert report["ok"] is True, report
            assert report["backup"], "导入前必须留备份"
            assert Path(report["backup"]).exists()
            rows = await target.fetchall("SELECT content FROM memories")
            assert {r["content"] for r in rows} == {"她记得我喜欢草莓蛋糕", "上周我们聊过杭州出差"}
        finally:
            await target.close()

    async def test_rejects_a_tampered_package(self, tmp_path) -> None:
        pkg = await self._package(tmp_path)
        doc = json.loads(pkg.read_text(encoding="utf-8"))
        doc["tables"]["memories"][0]["content"] = "被改过的内容"
        pkg.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        target = await make_db_at(tmp_path, "target.db")
        try:
            report = await self._transfer(target, tmp_path).import_character(
                pkg, confirm=True, dry_run=False
            )
            assert report["ok"] is False
            assert report["reason"] == "invalid_package"
        finally:
            await target.close()

    async def test_run_in_transaction_rolls_back(self, tmp_path) -> None:
        db = await make_db_at(tmp_path, "tx.db")
        try:
            await db.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES ('keep', '1', 1)"
            )

            def fail_midway(conn):  # type: ignore[no-untyped-def]
                conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('a', '1', 1)")
                raise RuntimeError("boom")

            with pytest.raises(RuntimeError):
                await db.run_in_transaction(fail_midway)
            row = await db.fetchone("SELECT COUNT(*) AS n FROM settings")
            assert row["n"] == 1, "失败事务内的写入必须回滚"
        finally:
            await db.close()
