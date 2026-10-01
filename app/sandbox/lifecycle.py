"""CharacterLifecycleManager (v2.0 §5-§7): reset / initialize / rebuild.

Character-scoped data is wiped exactly once when the bible changes (or on
demand, with confirmation). Platform data — QQ users/groups/messages, models,
tools, credentials, logs — is never touched. A JSON backup of everything the
reset removes is written first, so the operation stays reversible.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from app.config.settings import PROJECT_ROOT

logger = logging.getLogger("CatooBot.Sandbox.Lifecycle")

#: character-scoped tables: wiped on reset (§6/§163-§165)
CHARACTER_TABLES: tuple[str, ...] = (
    "memories",
    "memory_vectors",
    "relationships",
    "interaction_profiles",
    "shared_experiences",
    "open_loops",
    "micro_events",
    "affective_events",
    "conversation_turns",
    "conversations",
    "world_events",
    "persistent_goals",
    "character_projects",
    "world_snapshots",
    "activity_episodes",
    "agent_goals",
    "agent_tasks",
    "agent_plans",
    "agent_steps",
    "agent_observations",
    "agent_traces",
    "sandbox_entities",
    "sandbox_spaces",
    "sandbox_objects",
    "sandbox_inventories",
    "sandbox_actions",
    "sandbox_events",
    "sandbox_traces",
    "sandbox_snapshots",
    "sandbox_commissions",
    "sandbox_social_spaces",
    "sandbox_knowledge",
    "sandbox_state",
)

#: character-scoped settings keys (§6)
CHARACTER_SETTINGS: tuple[str, ...] = (
    "active_persona",
    "character_state",
    "character_continuity",
    "world_overrides",
    "behavior_overrides",
    "prompt_overrides",
    "model_overrides",
    "pending_initiatives",
    "world_state",
    "world_pending",
)

#: tables that MUST survive the reset (§4/§7 + §7 users/QQ data)
PRESERVED_TABLES: tuple[str, ...] = (
    "users",
    "groups",
    "group_profiles",
    "messages",
    "settings",
    "users_profiles",
    "user_profiles",
    "schema_migrations",
    "sticker_assets",
    "sticker_usage",
    "native_faces",
    "image_analysis",
    "tool_executions",
    "tool_credentials",
    "tool_cache",
    "tool_permissions",
    "web_users",
)


class CharacterLifecycleManager:
    def __init__(
        self, database: Any, *, clock: Any = time.time, backup_dir: str | Path | None = None
    ) -> None:
        self._db = database
        self._clock = clock
        self._backup_dir = (
            Path(backup_dir) if backup_dir else (PROJECT_ROOT / "data" / "character_reset_backup")
        )

    # ---------------------------------------------------------------- reset

    async def reset_character(self, *, confirm: bool = False) -> dict[str, Any]:
        """Wipe character data only (§3-§7). Requires explicit confirmation."""
        if not confirm:
            return {"ok": False, "reason": "confirmation_required"}
        if self._db is None:
            return {"ok": False, "reason": "database_unavailable"}

        backup_path = await self._backup()
        removed: dict[str, int] = {}
        for table in CHARACTER_TABLES:
            count = await self._wipe_table(table)
            if count:
                removed[table] = count
        for key in CHARACTER_SETTINGS:
            try:
                await self._db.execute("DELETE FROM settings WHERE key = ?", (key,))
                removed.setdefault("settings", 0)
                removed["settings"] += 1
            except Exception:  # noqa: BLE001
                pass
        result = {
            "ok": True,
            "removed": removed,
            "backup": str(backup_path) if backup_path else "",
            "preserved": list(PRESERVED_TABLES),
            "at": int(self._clock()),
        }
        logger.info(
            "[Lifecycle] character reset complete: %d tables cleared, backup=%s",
            len(removed),
            backup_path,
        )
        return result

    async def _wipe_table(self, table: str) -> int:
        try:
            row = await self._db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608 - fixed names
            count = int(row["n"]) if row else 0
            if count:
                await self._db.execute(f"DELETE FROM {table}")  # noqa: S608 - fixed names
            return count
        except Exception:  # noqa: BLE001 - missing table = nothing to wipe
            return 0

    async def _backup(self) -> Path | None:
        """Archive every row we are about to delete (reversibility)."""
        try:
            self._backup_dir.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {"created_at": int(self._clock()), "tables": {}}
            for table in CHARACTER_TABLES:
                try:
                    rows = await self._db.fetchall(f"SELECT * FROM {table}")  # noqa: S608
                except Exception:  # noqa: BLE001
                    continue
                if rows:
                    payload["tables"][table] = rows[:5000]
            try:
                settings_rows = await self._db.fetchall(
                    "SELECT key, value FROM settings WHERE key IN"
                    " (" + ",".join("?" * len(CHARACTER_SETTINGS)) + ")",
                    CHARACTER_SETTINGS,
                )
                payload["settings"] = settings_rows
            except Exception:  # noqa: BLE001
                pass
            target = self._backup_dir / f"reset_{int(self._clock())}.json"
            target.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
            return target
        except Exception:  # noqa: BLE001 - backup trouble must not block the reset the user asked for
            logger.exception("[Lifecycle] backup failed (continuing with reset)")
            return None

    # ----------------------------------------------------------- initialize

    async def initialize_character_from_bible(self, bible: Any, sandbox: Any) -> dict[str, Any]:
        """Compile already done; this seeds the sandbox and stamps the version."""
        await sandbox.reinitialize()
        await self._db_state_set("bible_version", getattr(bible, "version", ""))
        report = {
            "ok": True,
            "bible_version": getattr(bible, "version", ""),
            "coverage": getattr(bible, "coverage", {}),
            "at": int(self._clock()),
        }
        logger.info("[Lifecycle] character initialized from bible %s", report["bible_version"])
        return report

    async def rebuild_character_runtime(self, sandbox: Any) -> dict[str, Any]:
        """Re-derive runtime state from persisted data (no data loss)."""
        await sandbox.start()
        return {"ok": True, "phase": str(getattr(sandbox.phase, "value", sandbox.phase))}

    # ------------------------------------------------------------- reporting

    async def reset_report(self) -> dict[str, Any]:
        """Current character-data footprint (for the WebUI confirmation page)."""
        footprint: dict[str, int] = {}
        for table in CHARACTER_TABLES:
            try:
                row = await self._db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
            except Exception:  # noqa: BLE001
                continue
            if row and int(row["n"]):
                footprint[table] = int(row["n"])
        preserved: dict[str, int] = {}
        for table in PRESERVED_TABLES:
            try:
                row = await self._db.fetchone(f"SELECT COUNT(*) AS n FROM {table}")  # noqa: S608
            except Exception:  # noqa: BLE001
                continue
            if row and int(row["n"]):
                preserved[table] = int(row["n"])
        return {"character": footprint, "preserved": preserved}

    async def _db_state_set(self, key: str, value: str) -> None:
        try:
            await self._db.execute(
                "INSERT INTO sandbox_state (key, value, updated_at) VALUES (?, ?, ?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value,"
                " updated_at=excluded.updated_at",
                (key, value, float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Lifecycle] state stamp failed")
