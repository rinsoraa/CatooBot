"""Rehearse (or apply) the pending schema migrations on one database file.

Task 25's queue: rehearse 13 → 18 on a copy of the live database, then migrate
the live one. Both use this script so the rehearsal and the real thing run the
exact same code path — `Database.connect()`.

    python scripts/migrate_db.py --db "E:/.../rehearsal.db" --json
    python scripts/migrate_db.py --db data/catoobot.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sqlite3
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def snapshot(path: Path) -> dict[str, object]:
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        tables = [
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        ]
        counts = {}
        for name in sorted(tables):
            try:
                counts[name] = int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
            except sqlite3.DatabaseError:
                counts[name] = -1
        version = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
    finally:
        conn.close()
    return {
        "version": int(version or 0),
        "integrity": integrity,
        "tables": counts,
    }


async def migrate(path: Path) -> list[str]:
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(f"{record.levelname} {record.getMessage()}")

    handler = Capture()
    root = logging.getLogger()
    previous_level = root.level
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    database = Database(DatabaseConfig(url=f"sqlite:///{path.as_posix()}"))
    try:
        await database.connect()
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)
        await database.close()
    return records


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply pending CatooBot migrations to one DB file")
    parser.add_argument("--db", required=True, help="database file to migrate in place")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    path = Path(args.db)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    if not path.exists():
        raise SystemExit(f"[migrate] no such database: {path}")

    before = snapshot(path)
    records = asyncio.run(migrate(path))
    after = snapshot(path)

    # A migration may add tables and rows (the bookkeeping table grows by one
    # row per migration) but must never shrink existing data.
    lost = {
        name: (before["tables"][name], after["tables"][name])
        for name in before["tables"]
        if name in after["tables"]
        and before["tables"][name] >= 0
        and after["tables"][name] < before["tables"][name]
    }
    added = sorted(set(after["tables"]) - set(before["tables"]))
    report = {
        "db": str(path),
        "version": [before["version"], after["version"]],
        "integrity": [before["integrity"], after["integrity"]],
        "tables": [len(before["tables"]), len(after["tables"])],
        "added_tables": added,
        "lost_rows": lost,
        "log": records,
        "ok": (after["integrity"] == "ok" and after["version"] > before["version"] and not lost),
    }
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(f"[migrate] {path}")
        print(f"[migrate] schema v{before['version']} → v{after['version']}")
        print(
            f"[migrate] tables {len(before['tables'])} → {len(after['tables'])},"
            f" integrity={after['integrity']}"
        )
        if added:
            print(f"[migrate] new tables: {', '.join(added)}")
        for line in records:
            print(f"    {line}")
        if lost:
            print(f"[migrate] ROWS LOST: {lost}")
        print("[migrate] OK" if report["ok"] else "[migrate] FAILED")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
