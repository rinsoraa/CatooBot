"""Phase 8G Follow-up F4：迁移演练状态表达（no-op / 成功 / 失败三态）。"""

from __future__ import annotations

from typing import Any

from app.database.migration_report import (
    STATUS_FAILED,
    STATUS_MIGRATED,
    STATUS_NO_OP,
    rehearsal_report,
    rehearsal_status,
)


def _report(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "db": "copy.db",
        "version_before": 36,
        "version_after": 36,
        "integrity_before": "ok",
        "integrity_after": "ok",
        "tables_before": 83,
        "tables_after": 83,
        "added_tables": [],
        "lost_rows": {},
        "log": [],
    }
    base.update(overrides)
    return rehearsal_report(**base)


def test_no_pending_migration_is_a_legal_no_op_not_a_failure() -> None:
    """版本没变、完整性 ok、没有丢行 = **合法 no-op**（旧定义会误报 ok:false）。"""
    report = _report()
    assert report["status"] == STATUS_NO_OP
    assert report["ok"] is True
    assert report["version"] == [36, 36]


def test_applied_migration_is_reported_as_migrated() -> None:
    report = _report(version_before=35, version_after=36, added_tables=["new_table"])
    assert report["status"] == STATUS_MIGRATED
    assert report["ok"] is True
    assert report["added_tables"] == ["new_table"]


def test_broken_integrity_is_a_real_failure() -> None:
    report = _report(integrity_after="corrupt")
    assert report["status"] == STATUS_FAILED
    assert report["ok"] is False
    assert report["integrity"] == ["ok", "corrupt"]


def test_lost_rows_are_a_real_failure() -> None:
    report = _report(lost_rows={"sessions": (10, 7)})
    assert report["status"] == STATUS_FAILED
    assert report["ok"] is False
    assert report["lost_rows"] == {"sessions": (10, 7)}


def test_version_regression_is_a_real_failure() -> None:
    """版本倒退（例如被降级）也是失败，绝不当成 no-op。"""
    report = _report(version_before=36, version_after=35)
    assert report["status"] == STATUS_FAILED
    assert report["ok"] is False


def test_status_helper_is_pure_and_covers_the_three_states() -> None:
    assert (
        rehearsal_status(integrity_ok=True, version_before=36, version_after=36, lost_rows={})
        == STATUS_NO_OP
    )
    assert (
        rehearsal_status(integrity_ok=True, version_before=35, version_after=36, lost_rows={})
        == STATUS_MIGRATED
    )
    assert (
        rehearsal_status(integrity_ok=False, version_before=36, version_after=36, lost_rows={})
        == STATUS_FAILED
    )


def test_report_keeps_the_legacy_fields() -> None:
    """兼容：既有调用方读的字段一个都不能少（只新增 status）。"""
    report = _report()
    assert set(report) >= {
        "db",
        "version",
        "integrity",
        "tables",
        "added_tables",
        "lost_rows",
        "log",
        "ok",
        "status",
    }
