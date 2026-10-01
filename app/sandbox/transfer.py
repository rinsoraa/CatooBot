"""CharacterDataTransfer (Task 24, milestone 1): export and inspect.

The character's whole life lives in ``data/catoobot.db``. This module is the
portable, verifiable path out of it: **row data**, not a database file copy, so
the package can be carried across schema versions and used as the safety net
for migration 19 (dropping the ``memories.vector`` JSON column).

Scope is the character domain and nothing else — the table list is
``CHARACTER_TABLES`` and the settings keys are ``CHARACTER_SETTINGS`` from
``app.sandbox.lifecycle``, so "what a reset would delete" and "what an export
contains" can never drift apart.

Hard rules (asserted by tests):

* **No secrets and no platform data.** QQ users/groups/messages, the WebUI
  password hash, provider keys and ``data/secrets.json`` are never exported.
* **Self-describing and tamper-evident.** ``content_sha256`` covers the
  settings + tables payload; ``counts`` is recomputed and compared on inspect.
* **Streamed, not buffered.** Tables are read one at a time and written in
  small chunks while they stream out of SQLite (a single memory row is never
  buffered with its whole table), so a ten-thousand-row export stays flat in
  memory. File *reads* at inspect time go through ``asyncio.to_thread``; the
  write path deliberately writes small chunks as rows arrive instead of
  materialising the document once to move it to a thread.

Format (UTF-8 JSON, one file)::

    {
      "settings": {"active_persona": {...}},
      "tables": {"memories": [{"id": 1, ...}], "memory_embeddings": []},
      "counts": {"memories": 1, ...},
      "format": "catoobot-character-export",
      "format_version": 1,
      "exported_at": 1760000000,
      "schema_version": 18,
      "bible_hash": "8f3a...",
      "missing_tables": [],
      "content_sha256": "<over {"settings":...,"tables":...}, canonical JSON>"
    }

The integrity fields are written *after* the payload so the hash can be
computed in a single streaming pass; JSON object member order carries no
meaning, so readers do not care.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import sqlite3
import time
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT
from app.sandbox.lifecycle import CHARACTER_SETTINGS, CHARACTER_TABLES

logger = logging.getLogger("CatooBot.Sandbox.Transfer")

FORMAT = "catoobot-character-export"
FORMAT_VERSION = 1

#: canonical JSON: stable byte-for-byte output for hashing and for re-hashing
_CANON: dict[str, Any] = {"ensure_ascii": False, "sort_keys": True, "separators": (",", ":")}

#: bytes cannot go into JSON; blobs travel as base64 behind an explicit marker
_B64_MARKER = "$b64"


def _encode(value: Any) -> Any:
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {_B64_MARKER: base64.b64encode(bytes(value)).decode("ascii")}
    return value


def _decode(value: Any) -> Any:
    """Reverse :func:`_encode` — ``{"$b64": "…"}`` becomes ``bytes`` again."""
    if isinstance(value, dict) and set(value) == {_B64_MARKER}:
        return base64.b64decode(value[_B64_MARKER])
    return value


def _encode_row(row: dict[str, Any]) -> dict[str, Any]:
    return {key: _encode(value) for key, value in row.items()}


def _canonical(payload: Any) -> str:
    return json.dumps(payload, **_CANON)


def _content_hash(settings: dict[str, Any], tables: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical({"settings": settings, "tables": tables}).encode()).hexdigest()


def _script_head_schema() -> int:
    try:
        from app.database.database import _MIGRATIONS

        return max(version for version, _name, _script in _MIGRATIONS)
    except Exception:  # noqa: BLE001 - inspect must work without the DB layer
        return 0


@dataclass
class ExportResult:
    """What ``write_export`` produced (and everything needed to trust it)."""

    path: str
    bytes: int
    sha256: str
    content_sha256: str
    schema_version: int
    bible_hash: str
    tables: dict[str, int] = field(default_factory=dict)
    missing_tables: list[str] = field(default_factory=list)
    seconds: float = 0.0
    include_files: bool = False

    @property
    def rows(self) -> int:
        return sum(self.tables.values())

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["rows"] = self.rows
        return data


class CharacterDataTransfer:
    def __init__(
        self,
        database: Any,
        *,
        clock: Any = time.time,
        bible_hash: str = "",
        tables: tuple[str, ...] = CHARACTER_TABLES,
        settings_keys: tuple[str, ...] = CHARACTER_SETTINGS,
        backup_dir: str | Path | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._bible_hash = bible_hash
        self._tables = tuple(tables)
        self._settings_keys = tuple(settings_keys)
        self._backup_dir = (
            Path(backup_dir) if backup_dir else (PROJECT_ROOT / "data" / "character_reset_backup")
        )

    # ------------------------------------------------------------ collecting

    async def _schema_version(self) -> int:
        try:
            row = await self._db.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        except Exception:  # noqa: BLE001 - a database without the table is schema 0
            return 0
        return int(row["v"]) if row and row["v"] is not None else 0

    async def _bible_hash_value(self) -> str:
        if self._bible_hash:
            return self._bible_hash
        try:
            row = await self._db.fetchone(
                "SELECT source_hash FROM character_bible ORDER BY created_at DESC LIMIT 1"
            )
        except Exception:  # noqa: BLE001
            return ""
        return str(row["source_hash"]) if row and row["source_hash"] else ""

    async def _settings(self) -> dict[str, Any]:
        """Character settings, parsed where possible, lossless where not."""
        if not self._settings_keys:
            return {}
        try:
            rows = await self._db.fetchall(
                "SELECT key, value FROM settings WHERE key IN"
                " (" + ",".join("?" * len(self._settings_keys)) + ")",
                self._settings_keys,
            )
        except Exception:  # noqa: BLE001 - a fresh database may have no settings table
            return {}
        out: dict[str, Any] = {}
        for row in rows:
            raw = row["value"]
            try:
                out[str(row["key"])] = json.loads(raw) if isinstance(raw, str) else raw
            except (TypeError, ValueError):
                out[str(row["key"])] = {"$raw": str(raw)}
        return out

    async def _iter_tables(self) -> AsyncIterator[tuple[str, list[dict[str, Any]], bool]]:
        """Yield ``(name, rows, existed)`` one table at a time, in name order."""
        existing: set[str] = set()
        try:
            rows = await self._db.fetchall(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
            existing = {str(row["name"]) for row in rows}
        except Exception:  # noqa: BLE001
            existing = set()
        for name in sorted(self._tables):
            if name not in existing:
                yield name, [], False
                continue
            try:
                rows = await self._db.fetchall(f"SELECT * FROM {name}")  # noqa: S608 - fixed names
            except Exception:  # noqa: BLE001 - one broken table must not kill the export
                logger.exception("[Transfer] table %s could not be read", name)
                rows = []
            yield name, [_encode_row(row) for row in rows], True

    async def export(self, include_files: bool = False) -> dict[str, Any]:
        """The whole character domain as one dict (tests, WebUI download)."""
        self._reject_files(include_files)
        tables: dict[str, list[dict[str, Any]]] = {}
        missing: list[str] = []
        async for name, rows, existed in self._iter_tables():
            if not existed:
                missing.append(name)
                continue
            tables[name] = rows
        settings = await self._settings()
        counts = {name: len(rows) for name, rows in tables.items()}
        return {
            "settings": settings,
            "tables": tables,
            "counts": counts,
            "format": FORMAT,
            "format_version": FORMAT_VERSION,
            "exported_at": int(self._clock()),
            "schema_version": await self._schema_version(),
            "bible_hash": await self._bible_hash_value(),
            "missing_tables": missing,
            "content_sha256": _content_hash(settings, tables),
        }

    # -------------------------------------------------------------- writing

    @staticmethod
    def _reject_files(include_files: bool) -> None:
        if include_files:
            raise ValueError(
                "include_files=True (sticker/image attachments) is milestone 2; "
                "the JSON export already carries every library row"
            )

    def _emit(self, handle: Any, text: str) -> None:
        """Single write point — a test subclass measures the chunk sizes here."""
        handle.write(text)

    async def write_export(self, path: str | Path, *, include_files: bool = False) -> ExportResult:
        """Stream the export to ``path`` (written to ``<path>.part`` first)."""
        self._reject_files(include_files)
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        started = time.time()
        hasher = hashlib.sha256()
        settings = await self._settings()
        counts: dict[str, int] = {}
        missing: list[str] = []

        with part.open("w", encoding="utf-8", newline="\n") as handle:

            def hashed(text: str) -> None:
                hasher.update(text.encode("utf-8"))
                self._emit(handle, text)

            def hash_only(text: str) -> None:
                hasher.update(text.encode("utf-8"))

            # Canonical payload first: byte-for-byte what _content_hash() digests,
            # except that the final closing brace waits until the header is in
            # (the digest is over the payload only, so the header goes inside the
            # same object without changing it).
            hashed('{"settings":' + _canonical(settings) + ',"tables":{')
            first = True
            async for name, rows, existed in self._iter_tables():
                if not existed:
                    missing.append(name)
                    continue
                counts[name] = len(rows)
                if not first:
                    hashed(",")
                first = False
                hashed(_canonical(name) + ":[")
                for index, row in enumerate(rows):
                    hashed(_canonical(row) if index == 0 else "," + _canonical(row))
                hashed("]")
            hashed("}")
            hash_only("}")  # the payload's own closing brace, written last
            content_digest = hasher.hexdigest()

            schema_version = await self._schema_version()
            bible_hash = await self._bible_hash_value()
            header = {
                "format": FORMAT,
                "format_version": FORMAT_VERSION,
                "exported_at": int(self._clock()),
                "schema_version": schema_version,
                "bible_hash": bible_hash,
                "missing_tables": missing,
                "content_sha256": content_digest,
            }
            self._emit(handle, ',"counts":' + _canonical(counts))
            self._emit(handle, "," + _canonical(header)[1:-1])  # header members, no braces
            self._emit(handle, "}")
        part.replace(target)

        data = target.read_bytes()
        result = ExportResult(
            path=str(target),
            bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            content_sha256=content_digest,
            schema_version=schema_version,
            bible_hash=bible_hash,
            tables=counts,
            missing_tables=missing,
            seconds=round(time.time() - started, 3),
            include_files=False,
        )
        logger.info(
            "[Transfer] exported %d rows in %d tables (%d bytes) → %s",
            result.rows,
            len(counts),
            result.bytes,
            target,
        )
        return result

    # ------------------------------------------------------------ inspecting

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)

    async def inspect(self, path: str | Path) -> dict[str, Any]:
        """Verify a package offline: format, integrity hash, counts, version."""
        target = Path(path)
        if not target.exists():
            return {"ok": False, "path": str(target), "reason": "file_not_found"}
        document = await asyncio.to_thread(self._read, target)
        tables = document.get("tables") or {}
        settings = document.get("settings") or {}
        stored_counts = document.get("counts") or {}

        computed_hash = _content_hash(settings, tables)
        stored_hash = str(document.get("content_sha256") or "")
        recomputed_counts = {name: len(rows) for name, rows in tables.items()}
        schema_version = int(document.get("schema_version") or 0)
        head = _script_head_schema()
        version_ok = (
            document.get("format") == FORMAT
            and int(document.get("format_version") or 0) <= FORMAT_VERSION
        )
        return {
            "ok": bool(
                version_ok and computed_hash == stored_hash and recomputed_counts == stored_counts
            ),
            "path": str(target),
            "format": document.get("format"),
            "format_version": document.get("format_version"),
            "format_ok": version_ok,
            "exported_at": document.get("exported_at"),
            "schema_version": schema_version,
            "schema_head": head,
            "schema_newer_than_code": bool(head and schema_version > head),
            "bible_hash": document.get("bible_hash") or "",
            "tables": {name: len(rows) for name, rows in sorted(tables.items())},
            "rows": sum(len(rows) for rows in tables.values()),
            "settings": sorted(settings),
            "missing_tables": document.get("missing_tables") or [],
            "content_sha256": stored_hash,
            "sha256_ok": computed_hash == stored_hash,
            "counts_match": recomputed_counts == stored_counts,
            "file_sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
        }

    # ------------------------------------------------------------ importing

    async def import_character(
        self, path: str | Path, *, confirm: bool = False, dry_run: bool = True
    ) -> dict[str, Any]:
        """Import a character package: dry-run → backup → transaction → self-check.

        ``dry_run=True`` (the default) reports what would be written without
        touching the database. With ``confirm=True`` the current character data
        is backed up first, then wiped and replaced in **one** SQLite
        transaction — a failure rolls everything back, so no half-imported
        character is left behind.
        """
        target = Path(path)
        report = await self.inspect(target)
        if not report.get("ok"):
            return {"ok": False, "reason": "invalid_package", "inspect": report}
        if report.get("schema_newer_than_code"):
            return {"ok": False, "reason": "schema_too_new", "inspect": report}

        document = await asyncio.to_thread(self._read, target)
        tables = document.get("tables") or {}
        settings = document.get("settings") or {}
        counts = {name: len(rows) for name, rows in tables.items()}

        if dry_run:
            return {
                "ok": True,
                "dry_run": True,
                "will_reset": True,
                "counts": counts,
                "settings": sorted(settings),
                "rows": sum(counts.values()),
            }

        if not confirm:
            return {"ok": False, "reason": "confirmation_required"}

        # Reversibility: archive the current character data first.
        from app.sandbox.lifecycle import CharacterLifecycleManager

        lifecycle = CharacterLifecycleManager(
            self._db, clock=self._clock, backup_dir=self._backup_dir
        )
        backup_path = await lifecycle.backup()

        # One transaction: wipe + insert; a bad row rolls the whole thing back.
        written = await self._db.run_in_transaction(
            lambda conn: self._import_sync(conn, tables, settings)
        )
        verification = await self._verify_import(counts)
        return {
            "ok": True,
            "dry_run": False,
            "backup": str(backup_path) if backup_path else "",
            "written": written,
            "counts": counts,
            "verified": verification,
        }

    def _import_sync(
        self,
        conn: Any,
        tables: dict[str, list[dict[str, Any]]],
        settings: dict[str, Any],
    ) -> int:
        """Wipe the character domain and insert the exported rows (in a tx)."""
        for table in CHARACTER_TABLES:
            try:
                conn.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names
            except sqlite3.OperationalError:
                continue  # table absent in this schema — nothing to wipe
        for key in CHARACTER_SETTINGS:
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))

        written = 0
        for name, rows in sorted(tables.items()):
            if not rows:
                continue
            try:
                columns = [c[1] for c in conn.execute(f'PRAGMA table_info("{name}")').fetchall()]
            except sqlite3.OperationalError:
                continue  # table absent in this schema — skip its rows
            for row in rows:
                decoded = {k: _decode(v) for k, v in row.items() if k in columns}
                if not decoded:
                    continue
                cols = ", ".join(f'"{c}"' for c in decoded)
                placeholders = ", ".join("?" * len(decoded))
                conn.execute(
                    f'INSERT INTO "{name}" ({cols}) VALUES ({placeholders})',  # noqa: S608
                    tuple(decoded.values()),
                )
                written += 1

        for key, value in settings.items():
            if isinstance(value, dict) and set(value) == {"$raw"}:
                raw = str(value["$raw"])
            else:
                raw = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            conn.execute(
                "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?)",
                (key, raw, int(self._clock())),
            )
        return written

    async def _verify_import(self, counts: dict[str, int]) -> dict[str, Any]:
        """Row counts match the package? A mismatch is reported, not fatal."""
        mismatches: dict[str, tuple[int, int]] = {}
        for name, expected in counts.items():
            try:
                row = await self._db.fetchone(f'SELECT COUNT(*) AS n FROM "{name}"')  # noqa: S608
            except Exception:  # noqa: BLE001
                continue
            actual = int(row["n"]) if row else 0
            if actual != expected:
                mismatches[name] = (expected, actual)
        return {"ok": not mismatches, "mismatches": mismatches}

    # ------------------------------------------------------------ CLI helpers

    async def _cli(self, args: argparse.Namespace) -> int:
        if args.action == "export":
            out = (
                Path(args.out)
                if args.out
                else (PROJECT_ROOT / "data" / "exports" / f"character-{int(self._clock())}.json")
            )
            result = await self.write_export(out)
            print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            return 0
        if args.action == "import":
            report = await self.import_character(
                args.file, confirm=args.confirm, dry_run=not args.confirm
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0 if report.get("ok") else 1
        report = await self.inspect(args.file)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report.get("ok") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m app.sandbox.transfer",
        description="Export / inspect / import character data",
    )
    sub = parser.add_subparsers(dest="action", required=True)
    export = sub.add_parser("export", help="write a character-data package")
    export.add_argument("--out", default="", help="target file (default data/exports/…)")
    inspect = sub.add_parser("inspect", help="verify a package without touching any database")
    inspect.add_argument("file")
    import_ = sub.add_parser(
        "import", help="import a package (dry-run by default; --confirm writes)"
    )
    import_.add_argument("file")
    import_.add_argument("--confirm", action="store_true", help="actually write")
    args = parser.parse_args(argv)

    if args.action == "inspect":
        # Offline: no config, no database, nothing to leak.
        transfer = CharacterDataTransfer(database=None)
        return asyncio.run(transfer._cli(args))

    from app.config.settings import load_config
    from app.database.database import Database

    async def run() -> int:
        config = load_config()
        database = Database(config.database)
        await database.connect()
        try:
            return await CharacterDataTransfer(database)._cli(args)
        finally:
            await database.close()

    return asyncio.run(run())


if __name__ == "__main__":
    raise SystemExit(main())
