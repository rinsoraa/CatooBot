"""AdminService: the only bridge between WebUI routes and CatooBot core.

Routes never touch SQLite connections, providers or sockets directly — every
operation goes through this service, which also guarantees the degradation
rule: admin-side failures are logged and returned as data, never thrown into
the chat pipeline.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

from app.character.persona import Persona
from app.config.settings import project_path

if TYPE_CHECKING:
    from app.core.bot import Bot

LOG_TAIL_LINES = 400


class AdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Web")

    # ------------------------------------------------------------- dashboard

    async def live_status(self) -> dict[str, Any]:
        """Small, whitelisted snapshot for the live WebSocket (Task 17).

        Only what an authenticated admin can already read on a page: her state
        line, the model router snapshot, counters and the loop watchdog. No
        conversation text, no raw protocol payloads.
        """
        bot = self.bot
        sandbox = ""
        sandbox_engine = getattr(bot, "sandbox", None)
        if sandbox_engine is not None:
            try:
                sandbox = sandbox_engine.status_line()
            except Exception:  # noqa: BLE001 - a status snapshot must never raise
                sandbox = ""
        watchdog = getattr(bot, "watchdog", None)
        gateway = getattr(bot, "onebot_gateway", None)
        database = getattr(bot, "database", None)
        scheduler = getattr(bot, "runtime_scheduler", None)
        return {
            "online": bot.is_connected,
            "sandbox": sandbox,
            "models": [
                {
                    "name": model["name"],
                    "enabled": model["enabled"],
                    "cooldown": model["in_cooldown"],
                    "fails": model["failure_count"],
                }
                for model in bot.ai.router.snapshot()
            ],
            "metrics": bot.metrics.snapshot(),
            "watchdog": watchdog.stats() if watchdog is not None else {},
            "plugins": bot.plugins.failure_counts,
            # ---- v1 additive blocks (契约 §8 的 status 轻量版) ----------------
            # 旧键全部保留：旧客户端只读 online/sandbox/models/metrics/watchdog。
            "qq": {
                "online": bot.is_connected,
                "self_id": bot.self_id,
                "last_event_at": self._last_event_at(gateway),
            },
            "world": self._world_live(sandbox_engine),
            "runtime": {
                "scheduler": self._scheduler_live(scheduler),
                "database": {
                    "connected": bool(
                        database is not None and getattr(database, "_conn", None) is not None
                    )
                },
            },
        }

    @staticmethod
    def _last_event_at(gateway: Any) -> float | None:
        """Last accepted inbound event time (None when the gateway is off)."""
        if gateway is None:
            return None
        try:
            return float(getattr(gateway, "last_event_at", 0.0) or 0.0) or None
        except (TypeError, ValueError):  # noqa: BLE001 - a status snapshot never raises
            return None

    @staticmethod
    def _scheduler_live(scheduler: Any) -> dict[str, Any]:
        if scheduler is None:
            return {
                "running": False,
                "interval_seconds": None,
                "ticks": None,
                "catchups": None,
                "last_tick_at": None,
            }
        return {
            "running": bool(scheduler.running),
            "interval_seconds": float(scheduler.interval),
            "ticks": int(scheduler.ticks),
            "catchups": int(scheduler.catchups),
            "last_tick_at": float(scheduler.last_tick_at) or None,
        }

    @staticmethod
    def _world_live(sandbox: Any) -> dict[str, Any]:
        """Read-only world snapshot for the v1 status/world topics (never raises)."""
        block: dict[str, Any] = {
            "phase": None,
            "location": None,
            "action": None,
            "world_revision": None,
            "cognitive_revision": None,
        }
        if sandbox is None:
            return block
        try:
            definition = sandbox.actions.definition(sandbox.current_action)
            block.update(
                {
                    "phase": sandbox.phase.value,
                    "location": sandbox.context().get("location"),
                    "action": definition.name if definition is not None else None,
                    "world_revision": int(getattr(sandbox, "world_revision", 0) or 0),
                    "cognitive_revision": int(getattr(sandbox, "cognitive_revision", 0) or 0),
                }
            )
        except Exception:  # noqa: BLE001 - a status snapshot must never raise
            pass
        return block

    async def dashboard(self) -> dict[str, Any]:
        bot = self.bot
        router = bot.ai.router
        models = router.snapshot()
        current = next(
            (m["name"] for m in models if m["enabled"] and not m["in_cooldown"]),
            None,
        )
        return {
            "online": bot.is_connected,
            "self_id": bot.self_id,
            "started_at": bot.started_at,
            "current_model": current,
            "models": models,
            "metrics": bot.metrics.snapshot(),
            "users": await self._count("users"),
            "groups": await self._count("groups"),
            "memories": await self._count("memories"),
            "sessions": await self._count_sessions(),
            "webui": bot.web is not None,
        }

    async def _count(self, table: str) -> int:
        if not self.bot.database or self.bot.database._conn is None:
            return 0
        try:
            row = await self.bot.database.fetchone(f"SELECT COUNT(*) AS n FROM {table}")
            return int(row["n"]) if row else 0
        except Exception:  # noqa: BLE001
            return 0

    async def _count_sessions(self) -> int:
        try:
            row = await self.bot.database.fetchone(
                "SELECT COUNT(DISTINCT session_id) AS n FROM conversations"
            )
            return int(row["n"]) if row else 0
        except Exception:  # noqa: BLE001
            return 0

    # ------------------------------------------------------------- character

    async def get_persona(self) -> Persona:
        return self.bot.personas.persona

    async def save_persona(self, data: dict[str, Any]) -> Persona:
        """Validate + persist + hot-apply. Raises ValueError on bad input."""
        current = self.bot.personas.persona
        merged = {**current.model_dump(), **data}
        try:
            persona = Persona.model_validate(merged)
        except Exception as exc:
            raise ValueError(f"Invalid persona data: {exc}") from exc
        return await self.bot.personas.save(persona)

    async def character_state(self) -> dict[str, Any]:
        return self.bot.character.states.state.model_dump()

    async def set_state(self, **changes: Any) -> dict[str, Any]:
        state = await self.bot.character.states.update(**changes)
        return state.model_dump()

    # ---------------------------------------------------------------- memory

    async def list_memories(self, **filters: Any) -> list[dict[str, Any]]:
        if self.bot.memory is None:
            return []
        memories = await self.bot.memory.list_memories(**filters)
        return [m.model_dump() for m in memories]

    async def memory_action(
        self, action: str, memory_id: int, data: dict[str, Any] | None = None
    ) -> bool:
        if self.bot.memory is None:
            return False
        if action == "delete":
            return await self.bot.memory.delete_memory(memory_id)
        if action == "edit" and data:
            return await self.bot.memory.edit_memory(
                memory_id,
                str(data.get("content", "")).strip(),
                str(data.get("category", "fact")),
                float(data.get("importance", 0.5)),
            )
        return False

    # ----------------------------------------------------------- users/groups

    async def list_users(self) -> list[dict[str, Any]]:
        if not self.bot.database or self.bot.database._conn is None:
            return []
        rows = await self.bot.database.fetchall(
            """SELECT u.user_id, u.nickname, u.last_seen,
                      COALESCE(p.interaction_count, r.interaction_count, 0) AS interactions,
                      COALESCE(r.stage, 'new') AS stage,
                      COALESCE(p.initiative_enabled, 1) AS initiative_enabled,
                      p.nickname_override, p.notes, p.tags
               FROM users u
               LEFT JOIN user_profiles p ON p.user_id = u.user_id
               LEFT JOIN relationships r ON r.user_id = u.user_id
               ORDER BY u.last_seen DESC LIMIT 200"""
        )
        return [dict(row) for row in rows]

    async def save_user_profile(self, user_id: str, data: dict[str, Any]) -> None:
        enabled = data.get("initiative_enabled")
        await self.bot.database.execute(
            """INSERT INTO user_profiles
                   (user_id, nickname_override, notes, tags, initiative_enabled)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   nickname_override=excluded.nickname_override,
                   notes=excluded.notes, tags=excluded.tags,
                   initiative_enabled=excluded.initiative_enabled""",
            (
                user_id,
                data.get("nickname_override") or None,
                data.get("notes", ""),
                str(data.get("tags", "[]")),
                1 if enabled is None or str(enabled) == "1" else 0,
            ),
        )

    async def list_groups(self) -> list[dict[str, Any]]:
        if not self.bot.database or self.bot.database._conn is None:
            return []
        rows = await self.bot.database.fetchall(
            """SELECT g.group_id, g.name, g.last_seen,
                      COALESCE(p.participation_enabled, 1) AS participation_enabled,
                      p.notes, p.tags, p.interaction_count
                 FROM groups g
                 LEFT JOIN group_profiles p ON p.group_id = g.group_id
                ORDER BY g.last_seen DESC LIMIT 200"""
        )
        return [dict(row) for row in rows]

    async def set_group_participation(self, group_id: str, enabled: bool) -> None:
        """The single write path for a group's participation switch (W5).

        Both the legacy ``POST /groups/toggle`` form and the v1
        ``PATCH /api/v1/social/groups/{group_id}`` call this method, so the
        upsert semantics cannot drift apart.
        """
        now = int(time.time())
        await self.bot.database.execute(
            """INSERT INTO group_profiles (group_id, participation_enabled, last_seen)
               VALUES (?, ?, ?)
               ON CONFLICT(group_id) DO UPDATE SET
                   participation_enabled=excluded.participation_enabled""",
            (str(group_id), 1 if enabled else 0, now),
        )

    # -------------------------------------------------------------- sessions

    async def list_sessions(self) -> list[dict[str, Any]]:
        if not self.bot.database or self.bot.database._conn is None:
            return []
        rows = await self.bot.database.fetchall(
            """SELECT session_id, COUNT(*) AS messages, MAX(created_at) AS last_at
               FROM conversations GROUP BY session_id ORDER BY last_at DESC LIMIT 100"""
        )
        sessions = [dict(row) for row in rows]
        for session in sessions:
            session["type"] = "group" if session["session_id"].startswith("group:") else "private"
        return sessions

    async def session_context(self, session_id: str) -> list[dict[str, Any]]:
        rows = await self.bot.database.fetchall(
            "SELECT role, content, created_at FROM conversations"
            " WHERE session_id = ? ORDER BY id ASC LIMIT 200",
            (session_id,),
        )
        return [dict(row) for row in rows]

    async def clear_session(self, session_id: str) -> None:
        await self.bot.ai.conversations.clear_context(session_id)

    # ---------------------------------------------------------------- models

    async def model_overrides(self) -> dict[str, Any]:
        data = (
            await self.bot.database.get_setting_json("model_overrides")
            if self.bot.database
            else None
        )
        return data if isinstance(data, dict) else {}

    async def apply_model_override(self, name: str, **changes: Any) -> bool:
        """Enable/disable, edit provider/model, or reorder — hot + persisted."""
        router = self.bot.ai.router
        if "enabled" in changes and not router.set_enabled(name, bool(changes["enabled"])):
            return False
        if (changes.get("model") or changes.get("provider")) and not router.update_spec(
            name, provider=changes.get("provider"), model=changes.get("model")
        ):
            return False
        if "priority" in changes:
            names = [s["name"] for s in router.snapshot()]
            names.remove(name)
            names.insert(max(0, int(changes["priority"])), name)
            if not router.reorder(names):
                return False
        overrides = await self.model_overrides()
        overrides[name] = {**overrides.get(name, {}), **changes}
        await self.bot.database.set_setting_json("model_overrides", overrides, int(time.time()))
        return True

    async def restore_model_overrides(self) -> None:
        """Re-apply persisted overrides after a restart."""
        try:
            overrides = await self.model_overrides()
            for name, changes in overrides.items():
                if "enabled" in changes:
                    self.bot.ai.router.set_enabled(name, bool(changes["enabled"]))
                if changes.get("model") or changes.get("provider"):
                    self.bot.ai.router.update_spec(
                        name, provider=changes.get("provider"), model=changes.get("model")
                    )
            if overrides:
                self._log.info("Restored %d model override(s)", len(overrides))
        except Exception:  # noqa: BLE001
            self._log.exception("Failed to restore model overrides")

    # --------------------------------------------------------------- prompts

    async def get_prompts(self) -> dict[str, str]:
        overrides = (
            await self.bot.database.get_setting_json("prompt_overrides")
            if self.bot.database
            else None
        ) or {}
        return {
            "persona_system_prompt": self.bot.personas.persona.system_prompt,
            "memory_extraction_prompt": str(overrides.get("memory_extraction_prompt", "")),
        }

    async def save_prompts(self, data: dict[str, str]) -> None:
        if "persona_system_prompt" in data:
            persona = self.bot.personas.persona.model_copy(
                update={"system_prompt": str(data["persona_system_prompt"])}
            )
            await self.bot.personas.save(persona)
        overrides: dict[str, Any] = {}
        if "memory_extraction_prompt" in data:
            overrides["memory_extraction_prompt"] = str(data["memory_extraction_prompt"])
        if overrides and self.bot.database:
            await self.bot.database.set_setting_json(
                "prompt_overrides", overrides, int(time.time())
            )
        # 立即生效：提取器持有的是提示词本身，不重新读库
        extractor = getattr(self.bot, "extractor", None)
        if extractor is not None and "memory_extraction_prompt" in data:
            extractor.set_system_prompt(str(data["memory_extraction_prompt"]))

    # ------------------------------------------------------------------ logs

    async def logs(self, level: str = "", keyword: str = "", lines: int = 200) -> list[str]:
        path = project_path(self.bot.config.logging.log_dir)
        path = path / "catoobot.log"
        if not path.exists():
            return []

        def read_tail() -> list[str]:
            with path.open("r", encoding="utf-8", errors="replace") as fp:
                return fp.readlines()[-LOG_TAIL_LINES:]

        try:
            tail = await asyncio.to_thread(read_tail)
        except OSError:
            return []
        wanted = level.upper() if level else ""
        result = []
        for line in reversed(tail):
            text = line.rstrip("\n")
            if wanted and f"[{wanted}]" not in text:
                continue
            if keyword and keyword.lower() not in text.lower():
                continue
            result.append(text)
            if len(result) >= lines:
                break
        return result

    # --------------------------------------------------------------- runtime

    async def runtime_action(self, action: str) -> dict[str, Any]:
        bot = self.bot
        if action == "reload_persona":
            persona = await bot.character.reload_persona()
            return {"ok": True, "detail": f"persona={persona.name}"}
        if action == "reload_models":
            await bot.database.set_setting_json("model_overrides", {}, int(time.time()))
            return {"ok": True, "detail": "model overrides cleared"}
        if action == "reload_plugins":
            await bot.plugins.unload_all()
            await bot.plugins.load_all()
            return {
                "ok": True,
                "detail": f"{len(bot.plugins.loaded)} plugin(s)",
            }
        if action == "restore_model_overrides":
            await self.restore_model_overrides()
            return {"ok": True, "detail": "overrides restored"}
        return {"ok": False, "detail": f"unknown action: {action}"}

    # ---------------------------------------------------------- data transfer

    async def export_character(self) -> dict[str, Any]:
        """The whole character domain as one export document (Task 24 M1)."""
        from app.sandbox.transfer import CharacterDataTransfer

        return await CharacterDataTransfer(self.bot.database).export()

    async def import_character_preview(self, path: str) -> dict[str, Any]:
        """Dry-run an import: report what it would overwrite, never writes."""
        from app.sandbox.transfer import CharacterDataTransfer

        return await CharacterDataTransfer(self.bot.database).import_character(
            path, confirm=False, dry_run=True
        )

    async def import_character_confirm(self, path: str) -> dict[str, Any]:
        """Import with backup + a single transaction (overwrites character data)."""
        from app.sandbox.transfer import CharacterDataTransfer

        return await CharacterDataTransfer(self.bot.database).import_character(
            path, confirm=True, dry_run=False
        )
