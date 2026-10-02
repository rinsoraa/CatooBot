"""Sandbox persistence: entity/object/needs/action/event/trace/snapshot I/O.

State deltas land in targeted tables; full snapshots are periodic (§74).
A blind DB failure degrades the sandbox, never crashes the bot (§168).
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from app.sandbox.bible import CharacterBible
from app.sandbox.models import (
    ActionInstance,
    DecisionTrace,
    SandboxEventRecord,
    SandboxSnapshot,
)


def _json_dumps(value: Any) -> str:
    import json

    return json.dumps(value, ensure_ascii=False, default=str)


def _json_loads(value: Any, *, default: Any = None) -> Any:
    import json

    if not isinstance(value, str) or not value.strip():
        return default if default is not None else []
    return json.loads(value)


logger = logging.getLogger("CatooBot.Sandbox.Store")


class SandboxStore:
    def __init__(self, database: Any, *, clock: Any = time.time) -> None:
        self._db = database
        self._clock = clock

    @property
    def available(self) -> bool:
        return self._db is not None

    # -------------------------------------------------------------- entities

    async def save_entity(
        self,
        entity_id: str,
        *,
        type: str,
        name: str,
        space_id: str,
        data: dict[str, Any],
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_entities (id, type, name, space_id, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET type=excluded.type, name=excluded.name,
                       space_id=excluded.space_id, data=excluded.data,
                       updated_at=excluded.updated_at""",
                (entity_id, type, name, space_id, _json(data), float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] entity save failed: %s", entity_id)

    async def load_entities(self) -> dict[str, dict[str, Any]]:
        if not self.available:
            return {}
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_entities")
        except Exception:  # noqa: BLE001
            return {}
        return {
            row["id"]: {**json.loads(row["data"] or "{}"), "_type": row["type"]} for row in rows
        }

    # --------------------------------------------------------------- spaces

    async def save_space(
        self, space_id: str, *, name: str, parent_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_spaces (id, name, parent_id, kind, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       parent_id=excluded.parent_id, kind=excluded.kind,
                       data=excluded.data, updated_at=excluded.updated_at""",
                (space_id, name, parent_id, kind, _json(data), float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] space save failed: %s", space_id)

    async def load_spaces(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_spaces")
        except Exception:  # noqa: BLE001
            return []
        return [json.loads(row["data"] or "{}") for row in rows]

    # ------------------------------------------------------------- objects

    async def save_object(
        self, object_id: str, *, name: str, space_id: str, kind: str, data: dict[str, Any]
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_objects (id, name, space_id, kind, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       space_id=excluded.space_id, kind=excluded.kind,
                       data=excluded.data, updated_at=excluded.updated_at""",
                (object_id, name, space_id, kind, _json(data), float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] object save failed: %s", object_id)

    async def load_objects(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_objects")
        except Exception:  # noqa: BLE001
            return []
        return [json.loads(row["data"] or "{}") for row in rows]

    # ----------------------------------------------------------- inventories

    async def save_inventory(self, key: str, data: dict[str, Any]) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_inventories (key, data, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET data=excluded.data,
                       updated_at=excluded.updated_at""",
                (key, _json(data), float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] inventory save failed: %s", key)

    async def load_inventories(self) -> dict[str, dict[str, Any]]:
        if not self.available:
            return {}
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_inventories")
        except Exception:  # noqa: BLE001
            return {}
        return {row["key"]: json.loads(row["data"] or "{}") for row in rows}

    # --------------------------------------------------------------- actions

    async def save_action(self, action: ActionInstance) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_actions
                   (id, definition_id, status, started_at, ended_at, data)
                   VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                       ended_at=excluded.ended_at, data=excluded.data""",
                (
                    action.id,
                    action.definition_id,
                    action.status.value,
                    action.started_at,
                    action.ended_at,
                    _json(action.model_dump(mode="json")),
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] action save failed: %s", action.id)

    async def active_action(self) -> ActionInstance | None:
        if not self.available:
            return None
        try:
            row = await self._db.fetchone(
                "SELECT data FROM sandbox_actions WHERE status = 'active'"
                " ORDER BY started_at DESC LIMIT 1"
            )
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        try:
            return ActionInstance.model_validate(json.loads(row["data"]))
        except (TypeError, ValueError):
            return None

    # ---------------------------------------------------------------- events

    async def append_event(self, event: SandboxEventRecord) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_events
                   (id, kind, priority, source, summary, reason_code, data, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    event.id,
                    event.kind,
                    event.level.value,
                    event.source.value,
                    event.summary,
                    event.reason_code,
                    _json(event.data),
                    event.created_at,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] event save failed")

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM sandbox_events ORDER BY created_at DESC LIMIT ?", (limit,)
            )
        except Exception:  # noqa: BLE001
            return []
        return [dict(row) for row in rows]

    async def timeline(self, *, limit: int = 150) -> list[dict[str, Any]]:
        return await self.recent_events(limit=limit)

    # ---------------------------------------------------------------- traces

    async def append_trace(self, trace: DecisionTrace) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_traces (ts, kind, summary, factors, reason_code, data)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    trace.ts,
                    trace.kind,
                    trace.summary,
                    _json(trace.factors),
                    trace.reason_code,
                    _json(trace.data),
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] trace save failed")

    async def recent_traces(self, *, limit: int = 200) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM sandbox_traces ORDER BY ts DESC LIMIT ?", (limit,)
            )
        except Exception:  # noqa: BLE001
            return []
        out = []
        for row in rows:
            item = dict(row)
            item["factors"] = json.loads(item.get("factors") or "[]")
            out.append(item)
        return out

    # ------------------------------------------------------------- snapshots

    async def save_snapshot(self, snapshot: SandboxSnapshot, *, keep: int = 48) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                "INSERT INTO sandbox_snapshots (created_at, elapsed_min, data) VALUES (?, ?, ?)",
                (
                    snapshot.created_at,
                    snapshot.elapsed_minutes,
                    _json(snapshot.model_dump(mode="json")),
                ),
            )
            await self._db.execute(
                "DELETE FROM sandbox_snapshots WHERE id NOT IN"
                " (SELECT id FROM sandbox_snapshots ORDER BY id DESC LIMIT ?)",
                (keep,),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] snapshot save failed")

    async def latest_snapshot(self) -> SandboxSnapshot | None:
        if not self.available:
            return None
        try:
            row = await self._db.fetchone(
                "SELECT data FROM sandbox_snapshots ORDER BY id DESC LIMIT 1"
            )
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        try:
            return SandboxSnapshot.model_validate(json.loads(row["data"]))
        except (TypeError, ValueError):
            return None

    # -------------------------------------------------- social / commissions

    async def save_social_space(
        self, space_id: str, *, name: str, kind: str, data: dict[str, Any]
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_social_spaces (id, name, kind, data, updated_at)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name, kind=excluded.kind,
                       data=excluded.data, updated_at=excluded.updated_at""",
                (space_id, name, kind, _json(data), float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] social space save failed")

    async def load_social_spaces(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_social_spaces")
        except Exception:  # noqa: BLE001
            return []
        return [json.loads(row["data"] or "{}") for row in rows]

    async def save_commission(
        self,
        commission_id: str,
        *,
        kind: str,
        status: str,
        progress: float,
        deadline: float,
        reward: float,
        data: dict[str, Any],
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_commissions
                   (id, kind, status, progress, deadline, reward, data, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status=excluded.status,
                       progress=excluded.progress, deadline=excluded.deadline,
                       reward=excluded.reward, data=excluded.data,
                       updated_at=excluded.updated_at""",
                (
                    commission_id,
                    kind,
                    status,
                    progress,
                    deadline,
                    reward,
                    _json(data),
                    float(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] commission save failed")

    async def load_commissions(self) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_commissions")
        except Exception:  # noqa: BLE001
            return []
        return [json.loads(row["data"] or "{}") for row in rows]

    # ------------------------------------------------------------- knowledge

    async def save_knowledge(
        self, key: str, *, known: bool, source: str, learned_at: float, data: dict[str, Any]
    ) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_knowledge (key, known, source, learned_at, data)
                   VALUES (?, ?, ?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET known=excluded.known,
                       source=excluded.source, learned_at=excluded.learned_at,
                       data=excluded.data""",
                (key, 1 if known else 0, source, learned_at, _json(data)),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] knowledge save failed")

    async def load_knowledge(self) -> dict[str, dict[str, Any]]:
        if not self.available:
            return {}
        try:
            rows = await self._db.fetchall("SELECT * FROM sandbox_knowledge")
        except Exception:  # noqa: BLE001
            return {}
        return {row["key"]: dict(row) for row in rows}

    # ------------------------------------------------------------ runtime kv

    # ------------------------------------------------- experiences (Phase 4)

    @property
    def database(self) -> Any:
        """The shared Database handle (memory foundation reuses the same store)."""
        return self._db

    async def save_experience(self, record: Any) -> str:
        """One ExperienceRecord row; idempotent per (character, episode) (§10.1 §7).

        Returns ``"inserted" | "existing" | "unavailable"``. The idempotency
        guarantee is the database's unique index on ``(character_id,
        episode_key)`` — the read-back afterwards only *reports* which side of
        it this call landed on, it never decides whether to write.
        """
        if not self.available:
            return "unavailable"
        episode_key = str(getattr(record, "episode_key", "") or "")
        try:
            await self._db.execute(
                """INSERT INTO sandbox_experiences
                       (id, character_id, kind, summary, importance, location, actors,
                        source_event_ids, causation_id, correlation_id, action_id,
                        interaction_type, external_source, metadata, created_at,
                        episode_key)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT DO NOTHING""",
                (
                    record.id,
                    record.character_id,
                    record.kind.value if hasattr(record.kind, "value") else str(record.kind),
                    record.summary,
                    float(record.importance),
                    record.location,
                    _json_dumps(record.actors),
                    _json_dumps(record.source_event_ids),
                    record.causation_id,
                    record.correlation_id,
                    record.action_id,
                    record.interaction_type,
                    record.external_source,
                    _json_dumps(record.metadata),
                    float(record.timestamp),
                    episode_key or None,
                ),
            )
        except Exception:  # noqa: BLE001 - memory bookkeeping must never break the world
            logger.debug("[Sandbox.Store] experience write failed", exc_info=True)
            return "unavailable"
        if not episode_key:
            # no episode identity: the id is the only key, so this call either
            # wrote its own row or found it already there
            return "inserted"
        row = await self._db.fetchone(
            "SELECT id FROM sandbox_experiences WHERE character_id = ? AND episode_key = ?",
            (record.character_id, episode_key),
        )
        if row is None:
            return "unavailable"
        return "inserted" if str(row["id"]) == str(record.id) else "existing"

    async def recent_experiences(
        self, *, character_id: str = "", limit: int = 20, min_importance: float = 0.0
    ) -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT id, character_id, kind, summary, importance, location, actors,"
                " source_event_ids, correlation_id, created_at, metadata, episode_key"
                " FROM sandbox_experiences"
                " WHERE character_id = ? AND importance >= ?"
                " ORDER BY created_at DESC LIMIT ?",
                (character_id, float(min_importance), int(limit)),
            )
        except Exception:  # noqa: BLE001 - pre-migration database has no table
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            for field in ("actors", "source_event_ids", "metadata"):
                try:
                    data[field] = _json_loads(
                        data.get(field), default={} if field == "metadata" else None
                    )
                except ValueError:
                    data[field] = [] if field != "metadata" else {}
            result.append(data)
        return result

    async def count_experiences(self, *, character_id: str = "") -> int:
        if not self.available:
            return 0
        try:
            row = await self._db.fetchone(
                "SELECT COUNT(*) AS n FROM sandbox_experiences WHERE character_id = ?",
                (character_id,),
            )
        except Exception:  # noqa: BLE001
            return 0
        return int(row["n"]) if row else 0

    # ------------------------------------------------- goals (Phase 7)

    async def save_goal(self, goal: Any) -> bool:
        """Persist one goal; False means "not written" (caller keeps it dirty)."""
        if not self.available:
            return False
        try:
            await self._db.execute(
                """INSERT INTO sandbox_goals
                       (goal_id, character_id, kind, status, priority, reason, source,
                        source_event_id, causation_id, correlation_id, target_entity,
                        target_space, target_item, target_project, metadata, progress,
                        current_step, retry_count, last_attempt_at, next_eligible_at,
                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(goal_id) DO UPDATE SET
                       status=excluded.status, priority=excluded.priority,
                       progress=excluded.progress, current_step=excluded.current_step,
                       retry_count=excluded.retry_count,
                       last_attempt_at=excluded.last_attempt_at,
                       next_eligible_at=excluded.next_eligible_at,
                       metadata=excluded.metadata, updated_at=excluded.updated_at""",
                (
                    goal.goal_id,
                    goal.character_id,
                    goal.kind.value if hasattr(goal.kind, "value") else str(goal.kind),
                    goal.status.value if hasattr(goal.status, "value") else str(goal.status),
                    float(goal.priority),
                    goal.reason,
                    goal.source.value if hasattr(goal.source, "value") else str(goal.source),
                    goal.source_event_id,
                    goal.causation_id,
                    goal.correlation_id,
                    goal.target_entity,
                    goal.target_space,
                    goal.target_item,
                    goal.target_project,
                    _json_dumps(goal.metadata),
                    float(goal.progress),
                    _json_dumps(
                        goal.current_step.model_dump(mode="json") if goal.current_step else {}
                    ),
                    int(goal.retry_count),
                    float(goal.last_attempt_at),
                    float(goal.next_eligible_at),
                    float(goal.created_at),
                    float(goal.updated_at),
                ),
            )
        except Exception:  # noqa: BLE001 - goal bookkeeping never breaks the world
            logger.debug("[Sandbox.Store] goal write failed", exc_info=True)
            return False
        return True

    async def list_goals(self, *, character_id: str = "") -> list[dict[str, Any]]:
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM sandbox_goals WHERE character_id = ?",
                (character_id,),
            )
        except Exception:  # noqa: BLE001 - pre-migration database has no table
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            for field in ("metadata", "current_step"):
                try:
                    data[field] = _json_loads(data.get(field), default={})
                except ValueError:
                    data[field] = {}
            result.append(data)
        return result

    async def save_commitment(self, commitment: Any) -> bool:
        """Persist one social commitment (Phase 9 §28); False = not written."""
        if not self.available:
            return False
        try:
            await self._db.execute(
                """INSERT INTO sandbox_commitments
                       (commitment_id, character_id, person_id, kind, status, strength,
                        description, revision, priority, target_action, target_activity,
                        time_hint, earliest_at, latest_at, due_at, created_at, updated_at,
                        activated_at, resolved_at, source_interaction_id, source_event_id,
                        correlation_id, causation_id, metadata)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(commitment_id) DO UPDATE SET
                       status=excluded.status, strength=excluded.strength,
                       description=excluded.description, revision=excluded.revision,
                       priority=excluded.priority, target_action=excluded.target_action,
                       target_activity=excluded.target_activity,
                       time_hint=excluded.time_hint, earliest_at=excluded.earliest_at,
                       latest_at=excluded.latest_at, due_at=excluded.due_at,
                       updated_at=excluded.updated_at, activated_at=excluded.activated_at,
                       resolved_at=excluded.resolved_at,
                       source_interaction_id=excluded.source_interaction_id,
                       metadata=excluded.metadata""",
                (
                    commitment.commitment_id,
                    commitment.character_id,
                    commitment.person_id,
                    commitment.kind.value,
                    commitment.status.value,
                    commitment.strength.value,
                    commitment.description,
                    int(commitment.revision),
                    float(commitment.priority),
                    commitment.target_action,
                    commitment.target_activity,
                    commitment.time_hint,
                    float(commitment.earliest_at),
                    float(commitment.latest_at),
                    float(commitment.due_at),
                    float(commitment.created_at),
                    float(commitment.updated_at),
                    float(commitment.activated_at),
                    float(commitment.resolved_at),
                    commitment.source_interaction_id,
                    commitment.source_event_id,
                    commitment.correlation_id,
                    commitment.causation_id,
                    _json_dumps(commitment.metadata),
                ),
            )
        except Exception:  # noqa: BLE001 - commitment bookkeeping never breaks the world
            logger.debug("[Sandbox.Store] commitment write failed", exc_info=True)
            return False
        return True

    async def list_commitments(self, *, character_id: str = "") -> list[dict[str, Any]]:
        """Reload this character's commitments (§29); other characters' stay out."""
        if not self.available:
            return []
        try:
            rows = await self._db.fetchall(
                "SELECT * FROM sandbox_commitments WHERE character_id = ?",
                (character_id,),
            )
        except Exception:  # noqa: BLE001 - pre-migration database has no table
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            try:
                data["metadata"] = _json_loads(data.get("metadata"), default={})
            except ValueError:
                data["metadata"] = {}
            result.append(data)
        return result

    async def state_get(self, key: str) -> str:
        if not self.available:
            return ""
        try:
            row = await self._db.fetchone("SELECT value FROM sandbox_state WHERE key = ?", (key,))
        except Exception:  # noqa: BLE001
            return ""
        return str(row["value"]) if row is not None else ""

    async def state_set(self, key: str, value: str) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO sandbox_state (key, value, updated_at) VALUES (?, ?, ?)
                   ON CONFLICT(key) DO UPDATE SET value=excluded.value,
                       updated_at=excluded.updated_at""",
                (key, value, float(self._clock())),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] state set failed: %s", key)

    # ----------------------------------------------------------------- bible

    async def save_bible(self, bible: CharacterBible) -> None:
        if not self.available:
            return
        try:
            await self._db.execute(
                """INSERT INTO character_bible
                  (version, source_hash, compiled, coverage, report, created_at)
                  VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    bible.version,
                    bible.source_hash,
                    _json(bible.model_dump(mode="json")),
                    _json(bible.coverage),
                    _json({"unresolved": bible.unresolved, "conflicts": bible.conflicts}),
                    float(self._clock()),
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception("[Sandbox] bible save failed")

    async def latest_bible(self) -> CharacterBible | None:
        if not self.available:
            return None
        try:
            row = await self._db.fetchone(
                "SELECT compiled FROM character_bible ORDER BY id DESC LIMIT 1"
            )
        except Exception:  # noqa: BLE001
            return None
        if row is None:
            return None
        try:
            return CharacterBible.model_validate(json.loads(row["compiled"]))
        except (TypeError, ValueError):
            return None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)
