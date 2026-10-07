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

from app.tasks.models import TaskRecord, TaskState


class TaskStore(Protocol):
    """TaskRuntime 需要的最小存储面（测试用内存实现，生产用 SQLite）。"""

    async def save(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None: ...

    async def load(self, task_id: str) -> TaskRecord | None: ...

    async def active(self, session_id: str) -> TaskRecord | None: ...

    async def list_recent(self, limit: int = 20) -> list[TaskRecord]: ...

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
