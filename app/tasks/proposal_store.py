"""Phase 7C §十：TaskProposal 的持久化（**一张新表**，历史复用既有审计）。

**复用优先**：

* 提案本体 → 新表 ``task_proposals``（迁移 32）；
* 提案历史 → **复用既有的 append-only 审计表** ``behavior_events``（与 7A 的
  ``initiative.`` 同一张表、不同前缀 ``proposal.``），所以**没有第二张历史表**；
* 幂等（§十）：``fingerprint``（来源+目标+时间桶）唯一索引 + ``INSERT OR IGNORE``
  —— "进程刚写完提案就崩了"也不会出现第二条；
* **终态不可覆盖**（§十）：``REJECTED/EXPIRED/CANCELLED`` 更新一律被拒（含重启恢复）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.tasks.proposal import (
    OPEN_PROPOSAL_STATUSES,
    ProposalStatus,
    TaskProposal,
    proposal_transition_allowed,
)

#: 审计里的作用域键（提案与角色无关，所以不用 character_id）
PROPOSAL_SCOPE = "proposal"
#: 还开着的状态（用于 open_proposals 的 SQL）
_OPEN_STATUSES = tuple(status.value for status in OPEN_PROPOSAL_STATUSES)

#: 审计表里属于本阶段的类型前缀（与 7A 的 ``initiative.`` 区分开）
PROPOSAL_EVENT_PREFIX = "proposal."
#: 提案号前缀
PROPOSAL_ID_PREFIX = "TP"


def proposal_id_for(day: str, sequence: int) -> str:
    return f"{PROPOSAL_ID_PREFIX}-{str(day).replace('-', '')}-{int(sequence):03d}"


def day_text(now: float) -> str:
    return time.strftime("%Y%m%d", time.localtime(float(now)))


class TaskProposalStore(Protocol):
    """只读 + 追加式的最小接口（测试用 InMemory 实现，生产用 SQLite 实现）。"""

    async def create(self, proposal: TaskProposal) -> tuple[TaskProposal, bool]: ...

    async def get(self, proposal_id: str) -> TaskProposal | None: ...

    async def recent(self, limit: int = 20) -> list[TaskProposal]: ...

    async def open_proposals(self) -> list[TaskProposal]: ...

    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        *,
        reason: str = "",
        now: float | None = None,
    ) -> TaskProposal | None: ...

    async def log_event(
        self,
        *,
        proposal_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool: ...

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------- 内存实现


class InMemoryTaskProposalStore:
    """测试用（没有数据库、没有副作用，行为与 SQLite 实现一致）。"""

    def __init__(self) -> None:
        self._rows: dict[str, TaskProposal] = {}
        self._order: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {}
        self.created_count = 0

    async def create(self, proposal: TaskProposal) -> tuple[TaskProposal, bool]:
        key = proposal.fingerprint or proposal.proposal_id
        for existing in self._rows.values():
            if (existing.fingerprint or existing.proposal_id) == key:
                return existing, False  # §十：同一个指纹只留一行
        day = day_text(proposal.created_at or 0.0)
        self._seq[day] = self._seq.get(day, 0) + 1
        stored = TaskProposal.from_payload(
            {**proposal.to_payload(), "proposal_id": proposal_id_for(day, self._seq[day])}
        )
        self._rows[stored.proposal_id] = stored
        self._order.append(stored.proposal_id)
        self.created_count += 1
        return stored, True

    async def get(self, proposal_id: str) -> TaskProposal | None:
        return self._rows.get(str(proposal_id))

    async def recent(self, limit: int = 20) -> list[TaskProposal]:
        rows = [self._rows[key] for key in reversed(self._order)]
        return rows[: max(1, int(limit))]

    async def open_proposals(self) -> list[TaskProposal]:
        return [item for item in await self.recent(200) if item.open]

    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        *,
        reason: str = "",
        now: float | None = None,
    ) -> TaskProposal | None:
        current = self._rows.get(str(proposal_id))
        if current is None or not proposal_transition_allowed(
            ProposalStatus(current.status), status
        ):
            return None  # §十：终态不可覆盖
        payload = current.to_payload()
        payload["status"] = status.value
        payload["reason"] = str(reason or current.reason)
        payload["updated_at"] = float(now if now is not None else time.time())
        updated = TaskProposal.from_payload(payload)
        self._rows[updated.proposal_id] = updated
        return updated

    async def log_event(
        self,
        *,
        proposal_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        self._events.append(
            {
                "type": str(event),
                "scope_key": "proposal",
                "reason": str(reason),
                "detail": json.dumps(
                    {"proposal_id": str(proposal_id), **(detail or {})}, ensure_ascii=False
                ),
                "created_at": int(at if at is not None else time.time()),
            }
        )
        return True

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [
            item
            for item in reversed(self._events)
            if item["type"].startswith(PROPOSAL_EVENT_PREFIX)
        ]
        return rows[: max(1, int(limit))]


# ---------------------------------------------------------------- SQLite 实现

_INSERT_SQL = (
    "INSERT OR IGNORE INTO task_proposals (proposal_id, source, objective, status, intent_id,"
    " initiator, target, required_capabilities, risk_summary, feasibility, suggestions, reason,"
    " fingerprint, created_at, expires_at, updated_at, payload)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class SqliteTaskProposalStore:
    """生产实现：``task_proposals`` 表 + 复用 ``behavior_events`` 做历史。"""

    def __init__(self, database: Any, *, logger: Any = None) -> None:
        self._db = database
        self._log = logger

    async def create(self, proposal: TaskProposal) -> tuple[TaskProposal, bool]:
        payload = proposal.to_payload()
        fingerprint = str(proposal.fingerprint or "") or str(proposal.proposal_id)

        def _run(conn: Any) -> str:
            day = day_text(proposal.created_at or time.time())
            prefix = f"{PROPOSAL_ID_PREFIX}-{day}-"
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(proposal_id, ?) AS INTEGER)) AS seq FROM task_proposals"
                " WHERE proposal_id LIKE ?",
                (len(prefix) + 1, f"{prefix}%"),
            ).fetchone()
            sequence = int((row[0] if row else 0) or 0) + 1
            proposal_id = proposal_id_for(day, sequence)
            # ★ id 是在**这里**才定的（调用方给的是空 id）：payload 列必须同步写回，
            # 否则读回来的提案 proposal_id 是空串 —— 那样 get()/update_status() 会静默找不到行。
            payload["proposal_id"] = proposal_id
            cursor = conn.execute(
                _INSERT_SQL,
                (
                    proposal_id,
                    payload["source"],
                    payload["objective"],
                    payload["status"],
                    payload["intent_id"],
                    payload["initiator"],
                    _dump(payload["target"]),
                    _dump(payload["required_capabilities"]),
                    _dump(payload["risk_summary"]),
                    payload["feasibility"],
                    _dump(payload["suggestions"]),
                    payload["reason"],
                    fingerprint,
                    float(payload["created_at"]),
                    float(payload["expires_at"]),
                    float(payload["updated_at"]),
                    _dump(payload),
                ),
            )
            return proposal_id if cursor.rowcount else ""

        created_id = str(await self._db.run_in_transaction(_run) or "")
        if created_id:
            stored = await self.get(created_id)
            if stored is not None:
                return stored, True
        existing = await self._find_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False  # §十：幂等 —— 同一个指纹绝不产生第二条
        raise RuntimeError("task_proposal create failed")

    async def _find_by_fingerprint(self, fingerprint: str) -> TaskProposal | None:
        row = await self._db.fetchone(
            "SELECT payload FROM task_proposals WHERE fingerprint = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (str(fingerprint),),
        )
        return _from_row(row)

    async def get(self, proposal_id: str) -> TaskProposal | None:
        row = await self._db.fetchone(
            "SELECT payload FROM task_proposals WHERE proposal_id = ?", (str(proposal_id),)
        )
        return _from_row(row)

    async def recent(self, limit: int = 20) -> list[TaskProposal]:
        rows = await self._db.fetchall(
            "SELECT payload FROM task_proposals ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [item for item in (_from_row(row) for row in rows) if item is not None]

    async def open_proposals(self) -> list[TaskProposal]:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        rows = await self._db.fetchall(
            "SELECT payload FROM task_proposals WHERE status IN"
            f" ({placeholders}) ORDER BY created_at DESC, id DESC",
            _OPEN_STATUSES,
        )
        return [item for item in (_from_row(row) for row in rows) if item is not None]

    async def update_status(
        self,
        proposal_id: str,
        status: ProposalStatus,
        *,
        reason: str = "",
        now: float | None = None,
    ) -> TaskProposal | None:
        current = await self.get(proposal_id)
        if current is None or not proposal_transition_allowed(
            ProposalStatus(current.status), status
        ):
            return None  # §十：终态不可覆盖（重启恢复也不行）
        moment = float(now if now is not None else time.time())
        payload = current.to_payload()
        payload["status"] = status.value
        payload["reason"] = str(reason or current.reason)
        payload["updated_at"] = moment

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "UPDATE task_proposals SET status = ?, reason = ?, updated_at = ?, payload = ?"
                " WHERE proposal_id = ? AND status = ?",
                (
                    status.value,
                    payload["reason"],
                    moment,
                    _dump(payload),
                    str(proposal_id),
                    ProposalStatus(current.status).value,
                ),
            )
            return bool(cursor.rowcount)

        if not await self._db.run_in_transaction(_run):
            return None
        return await self.get(proposal_id)

    async def log_event(
        self,
        *,
        proposal_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        moment = int(at if at is not None else time.time())
        payload = _dump({"proposal_id": str(proposal_id), **(detail or {})})

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT INTO behavior_events (type, scope_key, reason, detail, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(event), PROPOSAL_SCOPE, str(reason), payload, "proposal", moment),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT type, reason, detail, created_at FROM behavior_events"
            " WHERE scope_key = ? AND type LIKE ? ORDER BY id DESC LIMIT ?",
            (PROPOSAL_SCOPE, f"{PROPOSAL_EVENT_PREFIX}%", max(1, int(limit))),
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


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _from_row(row: Any) -> TaskProposal | None:
    """数据库行 → 提案（坏数据不猜：解析不了就当没有）。"""
    if row is None:
        return None
    payload = row.get("payload") if isinstance(row, dict) else None
    if not isinstance(payload, str) or not payload:
        return None
    try:
        data = json.loads(payload)
    except (TypeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return TaskProposal.from_payload(data)
