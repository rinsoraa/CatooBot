"""Task Runtime 的 checkpoint 存储（Phase 5A §九/§十）。

复用**同一个** SQLite 库（迁移 27），不建第二套存储：

* ``agent_task_runs``：一个 Task 一行 = 当前完整 checkpoint（JSON payload）；
* ``agent_task_checkpoints``：append-only 的转移日志（谁在什么时候把 Task 变成了什么）。

跨进程重启后 Task 本身读得回来；但**正在跑的 Minecraft action 早已失效** ——
那由 :class:`TaskRuntime` 显式处理成 ``RUNTIME_RESTART``，绝不假装 action 还在。
"""

from __future__ import annotations

import json
import time
from typing import Any, Protocol

from app.tasks.models import DispatchState, TaskRecord, TaskState


class TaskStore(Protocol):
    """TaskRuntime 需要的最小存储面（测试用内存实现，生产用 SQLite）。"""

    async def save(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None: ...

    async def load(self, task_id: str) -> TaskRecord | None: ...

    async def active(self, session_id: str) -> TaskRecord | None: ...

    async def list_recent(self, limit: int = 20) -> list[TaskRecord]: ...

    async def list_pending(self, limit: int | None = None) -> list[TaskRecord]: ...

    async def find_pending_by_action(self, action_id: str) -> list[TaskRecord]: ...

    async def find_by_action(
        self, action_id: str, *, include_terminal: bool = False
    ) -> list[TaskRecord]: ...

    async def list_outstanding(self, limit: int | None = None) -> list[TaskRecord]: ...

    async def has_conflicting_outstanding(self, *, ignore_task_id: str = "") -> bool: ...

    async def checkpoints(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]: ...


class InMemoryTaskStore:
    """内存实现（单测/无数据库场景）。**不是**生产存储 —— 重启即丢。"""

    def __init__(self, *, clock: Any = time.time) -> None:
        self._records: dict[str, TaskRecord] = {}
        self._log: list[dict[str, Any]] = []
        self._clock = clock

    async def save(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        # 存的是 payload 副本（与 SQLite 同语义）：如果直接存对象引用，load 会拿到调用方
        # 手里那个可变对象，"改了内存没落盘"这类次序 bug 在单测里就永远暴露不出来。
        self._records[record.task_id] = _copy(record)
        self._log.append(
            {
                "task_id": record.task_id,
                "state": record.state.value,
                "step_id": step_id,
                "event": event,
                "detail": detail or record.message,
                "created_at": self._clock(),
            }
        )

    async def load(self, task_id: str) -> TaskRecord | None:
        found = self._records.get(task_id)
        return _copy(found) if found is not None else None

    async def active(self, session_id: str) -> TaskRecord | None:
        candidates = [
            record
            for record in self._records.values()
            if record.session_id == session_id and not record.state.terminal
        ]
        if not candidates:
            return None
        candidates.sort(key=lambda item: item.updated_at, reverse=True)
        return _copy(candidates[0])

    async def list_recent(self, limit: int = 20) -> list[TaskRecord]:
        records = sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)
        return [_copy(item) for item in records[:limit]]

    async def list_pending(self, limit: int | None = None) -> list[TaskRecord]:
        """**所有**非终态任务（Phase 7F.5 §四）—— 不是"最近 N 条"。"""
        records = sorted(
            (item for item in self._records.values() if not item.state.terminal),
            key=lambda item: item.updated_at,
            reverse=True,
        )
        if limit is not None:
            records = records[: int(limit)]
        return [_copy(item) for item in records]

    async def find_pending_by_action(self, action_id: str) -> list[TaskRecord]:
        """按 action_id 找出**所有**仍在等它的未决任务（多匹配 = 歧义）。"""
        return await self.find_by_action(action_id, include_terminal=False)

    async def find_by_action(
        self, action_id: str, *, include_terminal: bool = False
    ) -> list[TaskRecord]:
        """按 action_id 找记录（Phase 7F.5.3），可选是否包含**终态**任务。

        终态任务也必须有事件匹配能力：取消/过期**之后**才被 Runtime 接受的动作会落在
        终态记录上（TaskRuntime._record_settlement_under_pause）。如果这里把它们排除，
        那条动作的终态事件就永远匹配不到 —— 动作会脱离生命周期管理。
        """
        wanted = str(action_id or "")
        if not wanted:
            return []
        items = [
            item
            for item in self._records.values()
            if str(item.pending_action_id or "") == wanted
            and (include_terminal or not item.state.terminal)
        ]
        items.sort(key=lambda item: item.updated_at, reverse=True)
        return [_copy(item) for item in items]

    async def list_outstanding(self, limit: int | None = None) -> list[TaskRecord]:
        """**有在途派发**的记录（任何状态，含终态）—— Phase 7F.5.3。

        「终态 + 仍未结算的动作」必须仍被生命周期看到：取消/过期之后才被 Runtime 接受的动作
        就落在终态记录上，用 list_pending（排除终态）会把它漏掉。
        """
        records = [
            item
            for item in self._records.values()
            if any(
                step.dispatch_state
                in {
                    DispatchState.INTENT.value,
                    DispatchState.ACCEPTED.value,
                    DispatchState.AMBIGUOUS.value,
                }
                for step in item.steps
            )
        ]
        records.sort(key=lambda item: item.updated_at, reverse=True)
        if limit is not None:
            records = records[: int(limit)]
        return [_copy(item) for item in records]

    async def has_conflicting_outstanding(self, *, ignore_task_id: str = "") -> bool:
        records = await self.list_outstanding(None)
        return any(record.task_id != ignore_task_id for record in records)

    async def checkpoints(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = [row for row in self._log if row["task_id"] == task_id]
        return rows[-limit:]


def _copy(record: TaskRecord) -> TaskRecord:
    """深拷贝（走一次 JSON 往返，与 SQLite 存储同样的信息损失面）。"""
    return TaskRecord.from_payload(json.loads(json.dumps(record.to_payload(), default=str)))


class SqliteTaskStore:
    """生产存储：同一个 SQLite 库里的两张表（迁移 27）。"""

    def __init__(self, database: Any, *, clock: Any = time.time) -> None:
        self._db = database
        self._clock = clock

    async def admit_dispatch_intent(
        self,
        record: TaskRecord,
        *,
        expected_generation: int,
        event: str = "",
        step_id: str = "",
        now: float | None = None,
    ) -> bool:
        """Atomically check the SQLite world-write fence and persist a new INTENT.

        This closes the read-then-write admission race across independent TaskRuntime
        objects, SQLite connections and local processes. It does *not* constitute a
        durable owner lease or a fencing token enforced by the Node runtime.
        """
        task_id = str(record.task_id)
        target = record.step(step_id)
        if target is None or target.dispatch_state != DispatchState.INTENT.value:
            return False
        if (
            record.current_step < 0
            or record.current_step >= len(record.steps)
            or record.steps[record.current_step].step_id != step_id
        ):
            return False
        if target.dispatch_generation != expected_generation + 1 or not target.dispatch_key:
            return False

        def _reserve(conn: Any) -> bool:
            current_row = conn.execute(
                "SELECT payload FROM agent_task_runs WHERE task_id = ?", (task_id,)
            ).fetchone()
            if current_row is None:
                return False
            current = TaskRecord.from_payload(json.loads(current_row["payload"]))
            current_step = current.step(step_id)
            if (
                current.state is not TaskState.RUNNING
                or current.pause_requested
                or current.replan_required
                or current_step is None
                or current_step.dispatch_generation != expected_generation
                or current_step.dispatch_state
                in {
                    DispatchState.INTENT.value,
                    DispatchState.ACCEPTED.value,
                    DispatchState.AMBIGUOUS.value,
                }
                or current.plan_hash != record.plan_hash
                or current.plan_version != record.plan_version
                or current.current_step > record.current_step
                or any(
                    previous.dispatch_state != DispatchState.SETTLED.value
                    for previous in current.steps[current.current_step : record.current_step]
                )
                or current.authorization is None
                or current.authorization != record.authorization
                or not current.authorization.valid(now=self._clock() if now is None else now)
            ):
                return False

            candidates = conn.execute(
                "SELECT task_id, payload FROM agent_task_runs WHERE"
                " payload LIKE ? OR payload LIKE ? OR payload LIKE ?",
                (
                    f'%"dispatch_state": "{DispatchState.INTENT.value}"%',
                    f'%"dispatch_state": "{DispatchState.ACCEPTED.value}"%',
                    f'%"dispatch_state": "{DispatchState.AMBIGUOUS.value}"%',
                ),
            ).fetchall()
            unresolved = {
                DispatchState.INTENT.value,
                DispatchState.ACCEPTED.value,
                DispatchState.AMBIGUOUS.value,
            }
            for row in candidates:
                competing = TaskRecord.from_payload(json.loads(row["payload"]))
                if any(
                    step.dispatch_state in unresolved
                    for step in competing.steps
                    if competing.task_id != task_id or step.step_id != step_id
                ):
                    return False

            payload = json.dumps(record.to_payload(), ensure_ascii=False, default=str)
            conn.execute(
                "UPDATE agent_task_runs SET state = ?, current_step = ?,"
                " updated_at = ?, expires_at = ?, payload = ? WHERE task_id = ?",
                (
                    record.state.value,
                    record.current_step,
                    record.updated_at,
                    record.expires_at,
                    payload,
                    task_id,
                ),
            )
            if event:
                conn.execute(
                    "INSERT INTO agent_task_checkpoints"
                    " (task_id, state, step_id, event, detail, created_at)"
                    " VALUES (?, ?, ?, ?, ?, ?)",
                    (
                        task_id,
                        record.state.value,
                        step_id,
                        event,
                        record.message[:400],
                        self._clock(),
                    ),
                )
            return True

        # Immediate writer acquisition is required: two independent connections
        # must not both observe the world-write slot as empty.
        return bool(await self._db.run_in_transaction(_reserve, immediate=True))

    async def save(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        payload = json.dumps(record.to_payload(), ensure_ascii=False, default=str)
        await self._db.execute(
            "INSERT INTO agent_task_runs"
            " (task_id, session_id, user_id, origin, state, objective, plan_hash,"
            "  current_step, created_at, updated_at, expires_at, payload)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(task_id) DO UPDATE SET"
            "  state=excluded.state, current_step=excluded.current_step,"
            "  updated_at=excluded.updated_at, expires_at=excluded.expires_at,"
            "  payload=excluded.payload",
            (
                record.task_id,
                record.session_id,
                record.user_id,
                record.origin,
                record.state.value,
                record.objective,
                record.plan_hash,
                record.current_step,
                record.created_at,
                record.updated_at,
                record.expires_at,
                payload,
            ),
        )
        if event:
            await self._db.execute(
                "INSERT INTO agent_task_checkpoints"
                " (task_id, state, step_id, event, detail, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                (
                    record.task_id,
                    record.state.value,
                    step_id,
                    event,
                    (detail or record.message)[:400],
                    self._clock(),
                ),
            )

    async def load(self, task_id: str) -> TaskRecord | None:
        row = await self._db.fetchone(
            "SELECT payload FROM agent_task_runs WHERE task_id = ?", (task_id,)
        )
        if row is None:
            return None
        return TaskRecord.from_payload(json.loads(row["payload"]))

    async def active(self, session_id: str) -> TaskRecord | None:
        rows = await self._db.fetchall(
            "SELECT payload FROM agent_task_runs WHERE session_id = ?"
            " ORDER BY updated_at DESC LIMIT 20",
            (session_id,),
        )
        for row in rows:
            record = TaskRecord.from_payload(json.loads(row["payload"]))
            if not record.state.terminal:
                return record
        return None

    async def list_recent(self, limit: int = 20) -> list[TaskRecord]:
        rows = await self._db.fetchall(
            "SELECT payload FROM agent_task_runs ORDER BY updated_at DESC LIMIT ?", (int(limit),)
        )
        return [TaskRecord.from_payload(json.loads(row["payload"])) for row in rows]

    async def list_pending(self, limit: int | None = None) -> list[TaskRecord]:
        """**所有**非终态任务（Phase 7F.5 §四）：未决动作索引必须能从持久化记录重建。

        用**真实的 state 列**过滤成完整集合（不是"最近 N 条"），再在 Python 侧解析
        payload —— 因此不依赖内存索引、不需要 json_extract、更不需要新表或迁移。
        """
        sql = (
            "SELECT payload FROM agent_task_runs WHERE state NOT IN (?, ?, ?, ?)"
            " ORDER BY updated_at DESC"
        )
        params: list[Any] = [item.value for item in terminal_states()]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = await self._db.fetchall(sql, tuple(params))
        return [TaskRecord.from_payload(json.loads(row["payload"])) for row in rows]

    async def find_pending_by_action(self, action_id: str) -> list[TaskRecord]:
        """按 action_id 找出**所有**仍在等它的未决任务（多匹配 = 歧义，绝不取第一个）。"""
        return await self.find_by_action(action_id, include_terminal=False)

    async def find_by_action(
        self, action_id: str, *, include_terminal: bool = False
    ) -> list[TaskRecord]:
        """按 action_id 找记录（Phase 7F.5.3），可选包含终态任务。

        pending_action_id 只存在于 payload 里（表没有这一列），所以这里先用 SQL 的 LIKE
        做一次**廉价文本预筛**、再在 Python 侧**精确比对**。action_id 只含 [a-z0-9_]、
        不含 LIKE 通配符，因此预筛不会漏匹配。这样既不需要新列、不需要迁移，也能覆盖
        终态行 —— 终态任务同样必须有事件匹配能力，否则它身上的真实动作会脱离生命周期。
        """
        wanted = str(action_id or "")
        if not wanted:
            return []
        if not include_terminal:
            return [
                record
                for record in await self.list_pending()
                if str(record.pending_action_id or "") == wanted
            ]
        rows = await self._db.fetchall(
            "SELECT payload FROM agent_task_runs WHERE payload LIKE ? ORDER BY updated_at DESC",
            (f"%{wanted}%",),
        )
        return [
            record
            for record in (TaskRecord.from_payload(json.loads(row["payload"])) for row in rows)
            if str(record.pending_action_id or "") == wanted
        ]

    async def list_outstanding(self, limit: int | None = None) -> list[TaskRecord]:
        """**有在途派发**的记录（任何状态，含终态）—— Phase 7F.5.3。

        同样用 SQL 的 LIKE 预筛 payload（dispatch_state 只存在于 payload 里），再在 Python
        侧精确过滤：既不需要新列/迁移，也能把「终态 + 仍未结算的动作」纳入生命周期管理。
        """
        sql = (
            "SELECT payload FROM agent_task_runs WHERE payload LIKE ? OR payload LIKE ?"
            " OR payload LIKE ? ORDER BY updated_at DESC"
        )
        params: list[Any] = [
            f'%"dispatch_state": "{DispatchState.INTENT.value}"%',
            f'%"dispatch_state": "{DispatchState.ACCEPTED.value}"%',
            f'%"dispatch_state": "{DispatchState.AMBIGUOUS.value}"%',
        ]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = await self._db.fetchall(sql, tuple(params))
        return [
            record
            for record in (TaskRecord.from_payload(json.loads(row["payload"])) for row in rows)
            if any(
                step.dispatch_state
                in {
                    DispatchState.INTENT.value,
                    DispatchState.ACCEPTED.value,
                    DispatchState.AMBIGUOUS.value,
                }
                for step in record.steps
            )
        ]

    async def has_conflicting_outstanding(self, *, ignore_task_id: str = "") -> bool:
        records = await self.list_outstanding(None)
        return any(record.task_id != ignore_task_id for record in records)

    async def checkpoints(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = await self._db.fetchall(
            "SELECT state, step_id, event, detail, created_at FROM agent_task_checkpoints"
            " WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, int(limit)),
        )
        return list(reversed(rows))


def terminal_states() -> tuple[TaskState, ...]:
    return (
        TaskState.SUCCEEDED,
        TaskState.FAILED,
        TaskState.CANCELLED,
        TaskState.EXPIRED,
    )
