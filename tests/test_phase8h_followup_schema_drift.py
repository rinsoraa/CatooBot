"""Phase 8H Follow-up F1：完整 schema 漂移检测（全部使用**隔离的真实临时 SQLite 库**）。"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from app.database.migration_report import (
    STATUS_FAILED,
    STATUS_MIGRATED,
    STATUS_NO_OP,
    rehearsal_report,
    schema_diff,
    schema_fingerprint,
    schema_snapshot,
    schema_snapshot_of,
)


def _seed(conn: sqlite3.Connection) -> None:
    conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT NOT NULL)")
    conn.execute("CREATE TABLE empty_one (id INTEGER)")
    conn.execute("CREATE INDEX items_name ON items (name)")
    conn.commit()


def _verdict(
    path: Path,
    conn: sqlite3.Connection,
    *,
    before: dict[str, str],
    version_before: int,
    version_after: int,
    added_tables: tuple[str, ...] = (),
) -> dict[str, Any]:
    return rehearsal_report(
        db=str(path),
        version_before=version_before,
        version_after=version_after,
        integrity_before="ok",
        integrity_after="ok",
        tables_before=2,
        tables_after=2,
        added_tables=list(added_tables),
        lost_rows={},
        log=[],
        schema_before=before,
        schema_after=schema_snapshot(conn),
    )


def test_unchanged_database_is_a_no_op(tmp_path: Path) -> None:
    path = tmp_path / "clean.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_NO_OP
    assert report["ok"] is True
    assert report["schema_changed"] is False
    assert report["schema_evidence_ok"] is True
    conn.close()


def test_version_bump_with_expected_schema_change_is_migrated(tmp_path: Path) -> None:
    path = tmp_path / "migrated.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("CREATE TABLE added_by_migration (id INTEGER)")
    conn.commit()
    report = _verdict(
        path,
        conn,
        before=before,
        version_before=36,
        version_after=37,
        added_tables=("added_by_migration",),
    )
    assert report["status"] == STATUS_MIGRATED
    assert report["ok"] is True
    assert report["schema_changed"] is True
    assert "table:added_by_migration" in report["schema_diff"]["added"]
    conn.close()


def test_version_unchanged_with_a_new_table_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_table.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("CREATE TABLE rogue (id INTEGER)")
    conn.commit()
    report = _verdict(
        path, conn, before=before, version_before=36, version_after=36, added_tables=("rogue",)
    )
    assert report["status"] == STATUS_FAILED
    assert report["ok"] is False
    assert report["schema_changed_without_version_bump"] is True
    conn.close()


def test_version_unchanged_with_a_new_column_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_column.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("ALTER TABLE items ADD COLUMN weight INTEGER")
    conn.commit()
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_FAILED
    assert "column:items.weight" in report["schema_diff"]["added"]
    conn.close()


def test_version_unchanged_with_a_changed_table_definition_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_def.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("ALTER TABLE items RENAME COLUMN name TO label")
    conn.commit()
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_FAILED
    assert "table:items" in report["schema_diff"]["changed"]
    conn.close()


def test_version_unchanged_with_a_dropped_empty_table_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_drop.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("DROP TABLE empty_one")
    conn.commit()
    # 空表被删掉不会丢任何行（lost_rows 抓不到）—— 只有 schema 快照能发现。
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_FAILED
    assert "table:empty_one" in report["schema_diff"]["removed"]
    conn.close()


def test_version_unchanged_with_an_index_change_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_index.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("CREATE INDEX items_id ON items (id)")
    conn.commit()
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_FAILED
    assert any(item.startswith("index:") for item in report["schema_diff"]["added"])
    conn.close()


def test_version_unchanged_with_a_view_or_trigger_change_is_failed(tmp_path: Path) -> None:
    path = tmp_path / "drift_view.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    before = schema_snapshot(conn)
    conn.execute("CREATE VIEW item_names AS SELECT name FROM items")
    conn.execute("CREATE TRIGGER items_touch AFTER UPDATE ON items BEGIN SELECT 1; END")
    conn.commit()
    report = _verdict(path, conn, before=before, version_before=36, version_after=36)
    assert report["status"] == STATUS_FAILED
    kinds = {item.split(":", 1)[0] for item in report["schema_diff"]["added"]}
    assert {"view", "trigger"} <= kinds
    conn.close()


def test_missing_schema_evidence_is_failed_not_a_healthy_no_op(tmp_path: Path) -> None:
    report = rehearsal_report(
        db=str(tmp_path / "missing.db"),
        version_before=36,
        version_after=36,
        integrity_before="ok",
        integrity_after="ok",
        tables_before=2,
        tables_after=2,
        added_tables=[],
        lost_rows={},
        log=[],
        schema_before=None,
        schema_after=None,
    )
    assert report["schema_evidence_ok"] is False
    assert report["status"] == STATUS_FAILED
    assert report["ok"] is False


def test_internal_sqlite_objects_do_not_create_false_drift(tmp_path: Path) -> None:
    path = tmp_path / "internal.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE t (id INTEGER PRIMARY KEY AUTOINCREMENT, code TEXT UNIQUE)")
    conn.execute("INSERT INTO t (code) VALUES ('x')")
    conn.commit()
    snapshot = schema_snapshot(conn)
    assert all(not key.split(":", 1)[1].lower().startswith("sqlite_") for key in snapshot)
    names = [row[0] for row in conn.execute("SELECT name FROM sqlite_master")]
    assert any(str(name).lower().startswith("sqlite_") for name in names), (
        "前提：库里确实有内部对象"
    )
    assert schema_diff(snapshot, schema_snapshot(conn))["changed_count"] == 0
    conn.close()


def test_snapshot_of_a_real_file_is_deterministic(tmp_path: Path) -> None:
    path = tmp_path / "real.db"
    conn = sqlite3.connect(path)
    _seed(conn)
    conn.close()
    first = schema_snapshot_of(path)
    second = schema_snapshot_of(path)
    assert first == second
    assert schema_fingerprint(first) == schema_fingerprint(second)
    assert schema_diff(first, second)["changed_count"] == 0
