"""Safe backup for CatooBot's SQLite database.

Never copy ``data/catoobot.db`` with the filesystem while the bot may be
running: SQLite runs in WAL mode here, so a plain file copy can miss everything
still in ``-wal`` (recent messages, memories) or catch a half-written page.
``VACUUM INTO`` takes a consistent snapshot *inside* SQLite and writes a
compact, standalone database file.

The backup is then verified, not assumed: ``PRAGMA integrity_check`` must pass
and every table must have the same row count as the source. The schema version
(``schema_migrations``) is printed so a backup taken before/after a migration
is unambiguous.

Usage::

    python scripts/backup_db.py
    python scripts/backup_db.py --out "D:\\backups\\before-v3.db"
    python scripts/backup_db.py --source data/catoobot.db --json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "data" / "catoobot.db"
DEFAULT_OUT_DIR = PROJECT_ROOT.parent / "CatooBot_backups"

#: Bookkeeping tables whose version the operator wants to know at a glance.
VERSION_TABLES = ("schema_migrations",)


def _connect_readonly(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)


def _table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    counts: dict[str, int] = {}
    for name in sorted(names):
        try:
            counts[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        except sqlite3.DatabaseError:
            counts[name] = -1  # virtual/shadow table without a simple count
    return counts


def _schema_version(conn: sqlite3.Connection) -> int:
    try:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    except sqlite3.DatabaseError:
        return 0
    return int(row[0]) if row and row[0] is not None else 0


def backup(source: Path, target: Path) -> dict[str, object]:
    if not source.exists():
        raise SystemExit(f"[backup] source not found: {source}")
    if target.exists():
        raise SystemExit(f"[backup] refusing to overwrite an existing file: {target}")
    target.parent.mkdir(parents=True, exist_ok=True)

    started = time.time()
    src = _connect_readonly(source)
    try:
        before = _table_counts(src)
        version = _schema_version(src)
        src.execute("VACUUM INTO ?", (str(target),))
    finally:
        src.close()

    check = sqlite3.connect(f"file:{target.as_posix()}?mode=ro", uri=True)
    try:
        integrity = check.execute("PRAGMA integrity_check").fetchone()[0]
        after = _table_counts(check)
        backup_version = _schema_version(check)
    finally:
        check.close()

    mismatched = {
        name: (before.get(name), after.get(name))
        for name in after
        if name in before and before[name] >= 0 and before[name] != after[name]
    }
    ok = integrity == "ok" and not mismatched and version == backup_version
    return {
        "ok": ok,
        "source": str(source),
        "source_bytes": source.stat().st_size,
        "target": str(target),
        "target_bytes": target.stat().st_size,
        "schema_version": version,
        "integrity": integrity,
        "tables": len(after),
        "rows": sum(v for v in after.values() if v > 0),
        "mismatched": mismatched,
        "seconds": round(time.time() - started, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verified SQLite snapshot of CatooBot's database")
    parser.add_argument("--source", default=str(DEFAULT_SOURCE), help="database to back up")
    parser.add_argument(
        "--out", default="", help="target file (default: <backup dir>/catoobot-<ts>.db)"
    )
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args()

    source = Path(args.source)
    if not source.is_absolute():
        source = PROJECT_ROOT / source
    stamp = time.strftime("%Y%m%d-%H%M%S")
    target = Path(args.out) if args.out else DEFAULT_OUT_DIR / f"catoobot-{stamp}.db"

    report = backup(source, target)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[backup] {report['source']} → {report['target']}")
        print(
            f"[backup] schema v{report['schema_version']} · {report['tables']} tables · "
            f"{report['rows']} rows · integrity={report['integrity']} · "
            f"{report['target_bytes']} bytes in {report['seconds']}s"
        )
        if report["mismatched"]:
            print(f"[backup] ROW-COUNT MISMATCH: {report['mismatched']}")
        print("[backup] OK" if report["ok"] else "[backup] FAILED — do not trust this file")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
