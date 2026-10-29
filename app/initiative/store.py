"""Phase 7A §二十三/§二十四/§四十六：LifeIntent 的持久化。

**复用优先**（§二十三/§六十六）：

* 意图本体 → 新表 ``life_intents``（迁移 31；现有 ``initiative_state`` 是**聊天**主动性的
  计数器，语义不同，不能塞）；
* 意图历史 → **复用既有的 append-only 审计表** ``behavior_events``（§二十四 要的
  ``id/timestamp/reason/source`` 它都有），所以**没有第二张历史表**；
* 幂等（§四十六）：``character_id`` + 语义键 + 时间桶构成的指纹上建了唯一索引，
  ``INSERT OR IGNORE`` —— "进程刚写完 intent 就崩了"也不会出现第二条。
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.initiative.model import (
    LifeIntent,
    LifeIntentStatus,
    intent_transition_allowed,
)

#: 审计表里属于本阶段的类型前缀（与聊天主动性的 initiative_* 区分开）
INTENT_EVENT_PREFIX = "initiative."

#: 一条意图的稳定 id 前缀
INTENT_ID_PREFIX = "INT"


def intent_id_for(day: str, sequence: int) -> str:
    return f"{INTENT_ID_PREFIX}-{str(day).replace('-', '')}-{int(sequence):03d}"


def day_text(now: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now)))


class LifeIntentStore(Protocol):
    """只读 + 追加式的最小接口（测试用 InMemory 实现，生产用 SQLite）。"""

    async def create(self, intent: LifeIntent) -> tuple[LifeIntent, bool]: ...

    async def get(self, intent_id: str) -> LifeIntent | None: ...

    async def recent(self, character_id: str, limit: int = 10) -> list[LifeIntent]: ...

    async def open_intents(self, character_id: str) -> list[LifeIntent]: ...

    async def update_status(
        self,
        intent_id: str,
        status: LifeIntentStatus,
        *,
        reason: str = "",
        suppression_reason: str = "",
        resolution_reason: str = "",
        now: float | None = None,
    ) -> LifeIntent | None: ...

    async def log_event(
        self,
        *,
        character_id: str,
        intent_id: str,
        event: str,
        reason: str = "",
        source: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool: ...

    async def recent_events(
        self, character_id: str, *, limit: int = 20
    ) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------- 内存实现


class InMemoryLifeIntentStore:
    """测试用（没有数据库、没有副作用，行为与 SQLite 实现一致）。"""

    def __init__(self) -> None:
        self._rows: dict[str, LifeIntent] = {}
        self._order: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {}
        self.created_count = 0

    async def create(self, intent: LifeIntent) -> tuple[LifeIntent, bool]:
        key = intent.fingerprint or intent.intent_id
        for existing in self._rows.values():
            if (existing.fingerprint or existing.intent_id) == key:
                return existing, False  # §四十六：幂等，不生成第二条
        day = day_text(intent.created_at or 0.0)
        self._seq[day] = self._seq.get(day, 0) + 1
        stored = LifeIntent.from_payload(
            {**intent.to_payload(), "intent_id": intent_id_for(day, self._seq[day])}
        )
        self._rows[stored.intent_id] = stored
        self._order.append(stored.intent_id)
        self.created_count += 1
        return stored, True

    async def get(self, intent_id: str) -> LifeIntent | None:
        return self._rows.get(str(intent_id))

    async def recent(self, character_id: str, limit: int = 10) -> list[LifeIntent]:
        rows = [
            self._rows[key]
            for key in reversed(self._order)
            if self._rows[key].character_id == str(character_id)
        ]
        return rows[: max(1, int(limit))]

    async def open_intents(self, character_id: str) -> list[LifeIntent]:
        return [item for item in await self.recent(character_id, 200) if item.open]

    async def update_status(
        self,
        intent_id: str,
        status: LifeIntentStatus,
        *,
        reason: str = "",
        suppression_reason: str = "",
        resolution_reason: str = "",
        now: float | None = None,
    ) -> LifeIntent | None:
        current = self._rows.get(str(intent_id))
        if current is None or not intent_transition_allowed(current.status, status):
            return None
        updated = current.with_status(
            status,
            reason=reason,
            suppression_reason=suppression_reason,
            resolution_reason=resolution_reason,
        )
        self._rows[updated.intent_id] = updated
        return updated

    async def log_event(
        self,
        *,
        character_id: str,
        intent_id: str,
        event: str,
        reason: str = "",
        source: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        self._events.append(
            {
                "type": str(event),
                "scope_key": str(character_id),
                "reason": str(reason),
                "detail": json.dumps(
                    {"intent_id": str(intent_id), **(detail or {})}, ensure_ascii=False
                ),
                "source": str(source),
                "created_at": int(at if at is not None else time.time()),
            }
        )
        return True

    async def recent_events(self, character_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [
            item
            for item in reversed(self._events)
            if item["scope_key"] == str(character_id)
            and item["type"].startswith(INTENT_EVENT_PREFIX)
        ]
        return rows[: max(1, int(limit))]


# ---------------------------------------------------------------- SQLite 实现


class SqliteLifeIntentStore:
    """生产实现：``life_intents`` 表 + 复用 ``behavior_events`` 做历史。"""

    def __init__(self, database: Any, *, logger: Any = None) -> None:
        self._db = database
        self._log = logger

    async def create(self, intent: LifeIntent) -> tuple[LifeIntent, bool]:
        payload = intent.to_payload()
        fingerprint = str(intent.fingerprint or "") or str(intent.intent_id)

        def _run(conn: Any) -> str:
            day = day_text(intent.created_at or time.time())
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(intent_id, ?) AS INTEGER)) AS seq FROM life_intents"
                " WHERE intent_id LIKE ?",
                (len(f"{INTENT_ID_PREFIX}-{day}-") + 1, f"{INTENT_ID_PREFIX}-{day}-%"),
            ).fetchone()
            sequence = int((row[0] if row else 0) or 0) + 1
            intent_id = intent_id_for(day, sequence)
            cursor = conn.execute(
                "INSERT OR IGNORE INTO life_intents (intent_id, character_id, intent_type, title,"
                " description, source, origin, priority, confidence, created_at, expires_at,"
                " related_activity, related_goal, related_memory, related_player, related_task,"
                " status, suppression_reason, resolution_reason, fingerprint, execution_class,"
                " tags, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    intent_id,
                    payload["character_id"],
                    payload["intent_type"],
                    payload["title"],
                    payload["description"],
                    payload["source"],
                    payload["origin"],
                    float(payload["priority"]),
                    float(payload["confidence"]),
                    float(payload["created_at"]),
                    float(payload["expires_at"]),
                    payload["related_activity"],
                    payload["related_goal"],
                    payload["related_memory"],
                    payload["related_player"],
                    payload["related_task"],
                    payload["status"],
                    payload["suppression_reason"],
                    payload["resolution_reason"],
                    fingerprint,
                    payload["execution_class"],
                    json.dumps(list(payload["tags"]), ensure_ascii=False),
                    float(payload["created_at"]),
                ),
            )
            return intent_id if cursor.rowcount else ""

        created_id = str(await self._db.run_in_transaction(_run) or "")
        if created_id:
            stored = await self.get(created_id)
            if stored is not None:
                return stored, True
        existing = await self._find_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False  # §四十六：幂等 —— 同一个指纹绝不产生第二条
        raise RuntimeError("life_intent create failed")

    async def get(self, intent_id: str) -> LifeIntent | None:
        row = await self._db.fetchone(
            "SELECT * FROM life_intents WHERE intent_id = ?", (str(intent_id),)
        )
        return _from_row(row) if row is not None else None

    async def _find_by_fingerprint(self, fingerprint: str) -> LifeIntent | None:
        row = await self._db.fetchone(
            "SELECT * FROM life_intents WHERE fingerprint = ? ORDER BY created_at DESC LIMIT 1",
            (str(fingerprint),),
        )
        return _from_row(row) if row is not None else None

    async def recent(self, character_id: str, limit: int = 10) -> list[LifeIntent]:
        rows = await self._db.fetchall(
            "SELECT * FROM life_intents WHERE character_id = ?"
            " ORDER BY created_at DESC, id DESC LIMIT ?",
            (str(character_id), max(1, int(limit))),
        )
        return [_from_row(row) for row in rows]

    async def open_intents(self, character_id: str) -> list[LifeIntent]:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        rows = await self._db.fetchall(
            "SELECT * FROM life_intents WHERE character_id = ? AND status IN"
            f" ({placeholders}) ORDER BY created_at DESC, id DESC",
            (str(character_id), *_OPEN_STATUSES),
        )
        return [_from_row(row) for row in rows]

    async def update_status(
        self,
        intent_id: str,
        status: LifeIntentStatus,
        *,
        reason: str = "",
        suppression_reason: str = "",
        resolution_reason: str = "",
        now: float | None = None,
    ) -> LifeIntent | None:
        current = await self.get(intent_id)
        if current is None or not intent_transition_allowed(current.status, status):
            return None
        moment = float(now if now is not None else time.time())

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "UPDATE life_intents SET status = ?, suppression_reason = ?, resolution_reason = ?,"
                " updated_at = ? WHERE intent_id = ? AND status = ?",
                (
                    status.value,
                    suppression_reason or (reason if status is LifeIntentStatus.SUPPRESSED else ""),
                    resolution_reason
                    or (
                        reason
                        if status in (LifeIntentStatus.RESOLVED, LifeIntentStatus.CANCELLED)
                        else ""
                    ),
                    moment,
                    str(intent_id),
                    current.status.value,
                ),
            )
            return bool(cursor.rowcount)

        if not await self._db.run_in_transaction(_run):
            return None
        return await self.get(intent_id)

    async def log_event(
        self,
        *,
        character_id: str,
        intent_id: str,
        event: str,
        reason: str = "",
        source: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        moment = int(at if at is not None else time.time())
        payload = json.dumps({"intent_id": str(intent_id), **(detail or {})}, ensure_ascii=False)

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT INTO behavior_events (type, scope_key, reason, detail, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    str(event),
                    str(character_id),
                    str(reason),
                    payload,
                    str(source or "done"),
                    moment,
                ),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def recent_events(self, character_id: str, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT type, reason, detail, status, created_at FROM behavior_events"
            " WHERE scope_key = ? AND type LIKE ? ORDER BY id DESC LIMIT ?",
            (str(character_id), f"{INTENT_EVENT_PREFIX}%", max(1, int(limit))),
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            raw = item.get("detail")
            if isinstance(raw, str) and raw:
                try:
                    item["detail"] = json.loads(raw)
                except (TypeError, ValueError):
                    item["detail"] = {}
            out.append(item)
        return out


_OPEN_STATUSES = (
    LifeIntentStatus.PROPOSED.value,
    LifeIntentStatus.SUPPRESSED.value,
)


def _from_row(row: dict[str, Any]) -> LifeIntent:
    """数据库行 → LifeIntent（JSON 列就地解析；坏数据不猜）。"""
    payload = dict(row)
    raw = payload.get("tags")
    if isinstance(raw, str):
        try:
            payload["tags"] = json.loads(raw)
        except (TypeError, ValueError):
            payload["tags"] = []
    payload.pop("id", None)
    payload.pop("updated_at", None)
    return LifeIntent.from_payload(payload)
