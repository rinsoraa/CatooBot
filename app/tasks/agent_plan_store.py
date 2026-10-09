"""Phase 7D §二/修订 3：AgentPlan 的持久化（**一张表**，执行状态绝不落这里）。

* 新表只有 ``task_agent_plans``（迁移 33）；
* 幂等：``fingerprint``（来源+目标+proposal+时间桶）唯一索引 + ``INSERT OR IGNORE``；
* 终态不可覆盖（与 7C/7A 同一纪律）；
* **执行状态不在这里**：步骤的运行/成败读关联 Task 的 checkpoint/事件（`task_id` 只是引用）。
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.tasks.agent_plan import (
    OPEN_PLAN_STATUSES,
    AgentPlan,
    PlanStatus,
    day_text,
    plan_id_for,
    plan_transition_allowed,
)

#: 审计里属于本阶段的类型前缀
PLAN_EVENT_PREFIX = "agentplan."
#: 审计里的作用域键
AGENT_PLAN_SCOPE = "agent_plan"

_OPEN_STATUSES = tuple(status.value for status in OPEN_PLAN_STATUSES)


class AgentPlanStore(Protocol):
    async def create(self, plan: AgentPlan) -> tuple[AgentPlan, bool]: ...
    async def get(self, plan_id: str) -> AgentPlan | None: ...
    async def recent(self, limit: int = 20) -> list[AgentPlan]: ...
    async def open_plans(self) -> list[AgentPlan]: ...
    async def update_status(
        self, plan_id: str, status: PlanStatus, *, reason: str = "", now: float | None = None
    ) -> AgentPlan | None: ...
    async def replace_plan(self, plan: AgentPlan) -> AgentPlan | None: ...
    async def log_event(
        self,
        *,
        plan_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool: ...
    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]: ...


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


class InMemoryAgentPlanStore:
    """测试用（行为与 SQLite 实现一致）。"""

    def __init__(self) -> None:
        self._rows: dict[str, AgentPlan] = {}
        self._order: list[str] = []
        self._events: list[dict[str, Any]] = []
        self._seq: dict[str, int] = {}

    async def create(self, plan: AgentPlan) -> tuple[AgentPlan, bool]:
        key = plan.fingerprint or plan.plan_id
        for existing in self._rows.values():
            if (existing.fingerprint or existing.plan_id) == key:
                return existing, False  # 同一个指纹只留一行
        day = day_text(plan.created_at or 0.0)
        self._seq[day] = self._seq.get(day, 0) + 1
        stored = AgentPlan.from_payload(
            {**plan.to_payload(), "plan_id": plan_id_for(day, self._seq[day])}
        )
        self._rows[stored.plan_id] = stored
        self._order.append(stored.plan_id)
        return stored, True

    async def get(self, plan_id: str) -> AgentPlan | None:
        return self._rows.get(str(plan_id))

    async def recent(self, limit: int = 20) -> list[AgentPlan]:
        rows = [self._rows[key] for key in reversed(self._order)]
        return rows[: max(1, int(limit))]

    async def open_plans(self) -> list[AgentPlan]:
        return [item for item in await self.recent(200) if item.open]

    async def update_status(
        self, plan_id: str, status: PlanStatus, *, reason: str = "", now: float | None = None
    ) -> AgentPlan | None:
        current = self._rows.get(str(plan_id))
        if current is None or not plan_transition_allowed(PlanStatus(current.status), status):
            return None  # 终态不可覆盖
        payload = current.to_payload()
        payload["status"] = status.value
        payload["reason"] = str(reason or current.reason)
        payload["updated_at"] = float(now if now is not None else time.time())
        updated = AgentPlan.from_payload(payload)
        self._rows[updated.plan_id] = updated
        return updated

    async def replace_plan(self, plan: AgentPlan) -> AgentPlan | None:
        """重规划：整体替换（新版本 + 新指纹相关的字段由 service 负责），不改 plan_id。"""
        current = self._rows.get(str(plan.plan_id))
        if current is None:
            return None
        self._rows[str(plan.plan_id)] = plan
        return plan

    async def log_event(
        self,
        *,
        plan_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        self._events.append(
            {
                "type": str(event),
                "scope_key": AGENT_PLAN_SCOPE,
                "reason": str(reason),
                "detail": _dump({"plan_id": str(plan_id), **(detail or {})}),
                "created_at": int(at if at is not None else time.time()),
            }
        )
        return True

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [
            item for item in reversed(self._events) if item["type"].startswith(PLAN_EVENT_PREFIX)
        ]
        return rows[: max(1, int(limit))]


_INSERT_SQL = (
    "INSERT OR IGNORE INTO task_agent_plans (plan_id, source, objective, status, proposal_id,"
    " intent_id, task_id, initiator, approver_user_id, approver_session_id, approved_at,"
    " target, plan, plan_hash, version, history, checks, risk_summary, reason, replans,"
    " replan_budget, replan_reason, fingerprint, created_at, expires_at, updated_at, payload)"
    " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
)


class SqliteAgentPlanStore:
    """生产实现：``task_agent_plans`` 表 + 复用 ``behavior_events`` 做审计。"""

    def __init__(self, database: Any, *, logger: Any = None) -> None:
        self._db = database
        self._log = logger

    async def create(self, plan: AgentPlan) -> tuple[AgentPlan, bool]:
        payload = plan.to_payload()
        fingerprint = str(plan.fingerprint or "") or str(plan.plan_id)

        def _run(conn: Any) -> str:
            day = day_text(plan.created_at or time.time())
            prefix = f"{plan_id_for(day, 0).rsplit('-', 1)[0]}-"
            row = conn.execute(
                "SELECT MAX(CAST(SUBSTR(plan_id, ?) AS INTEGER)) AS seq FROM task_agent_plans"
                " WHERE plan_id LIKE ?",
                (len(prefix) + 1, f"{prefix}%"),
            ).fetchone()
            sequence = int((row[0] if row else 0) or 0) + 1
            plan_id = plan_id_for(day, sequence)
            payload["plan_id"] = plan_id
            cursor = conn.execute(
                _INSERT_SQL,
                (
                    plan_id,
                    payload["source"],
                    payload["objective"],
                    payload["status"],
                    payload["proposal_id"],
                    payload["intent_id"],
                    payload["task_id"],
                    payload["initiator"],
                    payload["approver_user_id"],
                    payload["approver_session_id"],
                    float(payload["approved_at"]),
                    _dump(payload["target"]),
                    _dump(payload["plan"]),
                    payload["plan_hash"],
                    int(payload["version"]),
                    _dump(payload["history"]),
                    _dump(payload["checks"]),
                    _dump(payload["risk_summary"]),
                    payload["reason"],
                    int(payload["replans"]),
                    int(payload["replan_budget"]),
                    payload["replan_reason"],
                    fingerprint,
                    float(payload["created_at"]),
                    float(payload["expires_at"]),
                    float(payload["updated_at"]),
                    _dump(payload),
                ),
            )
            return plan_id if cursor.rowcount else ""

        created_id = str(await self._db.run_in_transaction(_run) or "")
        if created_id:
            stored = await self.get(created_id)
            if stored is not None:
                return stored, True
        existing = await self._find_by_fingerprint(fingerprint)
        if existing is not None:
            return existing, False
        raise RuntimeError("agent_plan create failed")

    async def _find_by_fingerprint(self, fingerprint: str) -> AgentPlan | None:
        row = await self._db.fetchone(
            "SELECT payload FROM task_agent_plans WHERE fingerprint = ?"
            " ORDER BY created_at DESC LIMIT 1",
            (str(fingerprint),),
        )
        return _from_row(row)

    async def get(self, plan_id: str) -> AgentPlan | None:
        row = await self._db.fetchone(
            "SELECT payload FROM task_agent_plans WHERE plan_id = ?", (str(plan_id),)
        )
        return _from_row(row)

    async def recent(self, limit: int = 20) -> list[AgentPlan]:
        rows = await self._db.fetchall(
            "SELECT payload FROM task_agent_plans ORDER BY created_at DESC, id DESC LIMIT ?",
            (max(1, int(limit)),),
        )
        return [item for item in (_from_row(row) for row in rows) if item is not None]

    async def open_plans(self) -> list[AgentPlan]:
        placeholders = ",".join("?" for _ in _OPEN_STATUSES)
        rows = await self._db.fetchall(
            "SELECT payload FROM task_agent_plans WHERE status IN"
            f" ({placeholders}) ORDER BY created_at DESC, id DESC",
            _OPEN_STATUSES,
        )
        return [item for item in (_from_row(row) for row in rows) if item is not None]

    async def update_status(
        self, plan_id: str, status: PlanStatus, *, reason: str = "", now: float | None = None
    ) -> AgentPlan | None:
        current = await self.get(plan_id)
        if current is None or not plan_transition_allowed(PlanStatus(current.status), status):
            return None  # 终态不可覆盖（重启恢复也不行）
        moment = float(now if now is not None else time.time())
        payload = current.to_payload()
        payload["status"] = status.value
        payload["reason"] = str(reason or current.reason)
        payload["updated_at"] = moment

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "UPDATE task_agent_plans SET status = ?, reason = ?, updated_at = ?, payload = ?"
                " WHERE plan_id = ? AND status = ?",
                (
                    status.value,
                    payload["reason"],
                    moment,
                    _dump(payload),
                    str(plan_id),
                    PlanStatus(current.status).value,
                ),
            )
            return bool(cursor.rowcount)

        if not await self._db.run_in_transaction(_run):
            return None
        return await self.get(plan_id)

    async def replace_plan(self, plan: AgentPlan) -> AgentPlan | None:
        current = await self.get(plan.plan_id)
        if current is None:
            return None
        payload = plan.to_payload()

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "UPDATE task_agent_plans SET status = ?, task_id = ?, approver_user_id = ?,"
                " approver_session_id = ?, approved_at = ?, target = ?, plan = ?,"
                " plan_hash = ?, version = ?, history = ?, checks = ?, risk_summary = ?,"
                " reason = ?, replans = ?, replan_reason = ?, fingerprint = ?,"
                " expires_at = ?, updated_at = ?, payload = ? WHERE plan_id = ?",
                (
                    payload["status"],
                    payload["task_id"],
                    payload["approver_user_id"],
                    payload["approver_session_id"],
                    float(payload["approved_at"]),
                    _dump(payload["target"]),
                    _dump(payload["plan"]),
                    payload["plan_hash"],
                    int(payload["version"]),
                    _dump(payload["history"]),
                    _dump(payload["checks"]),
                    _dump(payload["risk_summary"]),
                    payload["reason"],
                    int(payload["replans"]),
                    payload["replan_reason"],
                    payload["fingerprint"],
                    float(payload["expires_at"]),
                    float(payload["updated_at"]),
                    _dump(payload),
                    str(plan.plan_id),
                ),
            )
            return bool(cursor.rowcount)

        if not await self._db.run_in_transaction(_run):
            return None
        return await self.get(plan.plan_id)

    async def log_event(
        self,
        *,
        plan_id: str,
        event: str,
        reason: str = "",
        at: float | None = None,
        detail: dict[str, Any] | None = None,
    ) -> bool:
        moment = int(at if at is not None else time.time())
        payload = _dump({"plan_id": str(plan_id), **(detail or {})})

        def _run(conn: Any) -> bool:
            cursor = conn.execute(
                "INSERT INTO behavior_events (type, scope_key, reason, detail, status, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (str(event), AGENT_PLAN_SCOPE, str(reason), payload, "agent_plan", moment),
            )
            return bool(cursor.rowcount)

        return bool(await self._db.run_in_transaction(_run))

    async def recent_events(self, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT type, reason, detail, created_at FROM behavior_events"
            " WHERE scope_key = ? AND type LIKE ? ORDER BY id DESC LIMIT ?",
            (AGENT_PLAN_SCOPE, f"{PLAN_EVENT_PREFIX}%", max(1, int(limit))),
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


def _from_row(row: Any) -> AgentPlan | None:
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
    return AgentPlan.from_payload(data)
