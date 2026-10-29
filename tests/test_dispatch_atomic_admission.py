"""Cross-connection SQLite dispatch admission: a conflicting INTENT must never be minted twice."""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import DispatchState, StepState, dispatch_key_of
from app.tasks.store import SqliteTaskStore
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    armed_record,
    make_runtime,
    single_action_plan,
)


class BarrierSqliteStore(SqliteTaskStore):
    def __init__(self, database: Any, *, gate: dict[str, Any]) -> None:
        super().__init__(database)
        self.gate = gate
        self.first_check = True

    async def has_conflicting_outstanding(self, *, ignore_task_id: str = "") -> bool:
        found = await super().has_conflicting_outstanding(ignore_task_id=ignore_task_id)
        if self.first_check:
            self.first_check = False
            self.gate["arrivals"] += 1
            if self.gate["arrivals"] == 2:
                self.gate["ready"].set()
            await asyncio.wait_for(self.gate["ready"].wait(), timeout=10)
        return found


async def test_two_taskruntimes_share_single_atomic_dispatch_slot(tmp_path: Any) -> None:
    url = f"sqlite:///{tmp_path / 'two-runtimes.db'}"
    db_a, db_b = Database(DatabaseConfig(url=url)), Database(DatabaseConfig(url=url))
    await db_a.connect()
    await db_b.connect()
    try:
        gate: dict[str, Any] = {"arrivals": 0, "ready": asyncio.Event()}
        store_a = BarrierSqliteStore(db_a, gate=gate)
        store_b = BarrierSqliteStore(db_b, gate=gate)
        invoke_a, invoke_b = DispatchDouble(mode="detached"), DispatchDouble(mode="detached")
        runtime_a = make_runtime(invoke_a, store=store_a)
        runtime_b = make_runtime(invoke_b, store=store_b)
        task_a = await armed_record(runtime_a, store_a, plan=single_action_plan(x=11), session="A")
        task_b = await armed_record(runtime_b, store_b, plan=single_action_plan(x=22), session="B")
        await asyncio.wait_for(
            asyncio.gather(runtime_a.drive(task_a.task_id), runtime_b.drive(task_b.task_id)),
            timeout=15,
        )
        assert gate["arrivals"] == 2
        assert invoke_a.action_count + invoke_b.action_count == 1
        rows = [await store_a.load(task_a.task_id), await store_b.load(task_b.task_id)]
        assert (
            sum(int(row.steps[0].dispatch_state == DispatchState.ACCEPTED.value) for row in rows)
            == 1
        )
        checkpoints = (await store_a.checkpoints(task_a.task_id)) + (
            await store_b.checkpoints(task_b.task_id)
        )
        assert sum(int(c["event"] == "task.step_started") for c in checkpoints) == 1
    finally:
        await db_b.close()
        await db_a.close()


async def test_two_taskruntimes_cannot_retire_other_controllers_same_task_intent(
    tmp_path: Any,
) -> None:
    url = f"sqlite:///{tmp_path / 'same-task.db'}"
    db_a, db_b = Database(DatabaseConfig(url=url)), Database(DatabaseConfig(url=url))
    await db_a.connect()
    await db_b.connect()
    try:
        gate: dict[str, Any] = {"arrivals": 0, "ready": asyncio.Event()}
        store_a = BarrierSqliteStore(db_a, gate=gate)
        store_b = BarrierSqliteStore(db_b, gate=gate)
        invoke_a, invoke_b = DispatchDouble(mode="detached"), DispatchDouble(mode="detached")
        runtime_a, runtime_b = (
            make_runtime(invoke_a, store=store_a),
            make_runtime(invoke_b, store=store_b),
        )
        task = await armed_record(runtime_a, store_a, plan=single_action_plan(), session="A")
        await asyncio.wait_for(
            asyncio.gather(runtime_a.drive(task.task_id), runtime_b.drive(task.task_id)),
            timeout=15,
        )
        assert invoke_a.action_count + invoke_b.action_count == 1
        final = await store_a.load(task.task_id)
        assert final.steps[0].dispatch_state == DispatchState.ACCEPTED.value
        assert final.steps[0].dispatch_generation == 1
        assert final.pending_action_id
        checkpoints = await store_a.checkpoints(task.task_id)
        assert sum(int(c["event"] == "task.step_started") for c in checkpoints) == 1
    finally:
        await db_b.close()
        await db_a.close()


async def test_sqlite_atomic_intent_reservation_two_controllers(tmp_path: Any) -> None:
    url = f"sqlite:///{tmp_path / 'owners.db'}"
    db_a, db_b = Database(DatabaseConfig(url=url)), Database(DatabaseConfig(url=url))
    await db_a.connect()
    await db_b.connect()
    try:
        store_a, store_b = SqliteTaskStore(db_a), SqliteTaskStore(db_b)
        runtime_a = make_runtime(DispatchDouble(), store=store_a)
        runtime_b = make_runtime(DispatchDouble(), store=store_b)
        initial_a = await armed_record(
            runtime_a, store_a, plan=single_action_plan(x=11), session="A"
        )
        initial_b = await armed_record(
            runtime_b, store_b, plan=single_action_plan(x=22), session="B"
        )
        records = [initial_a, initial_b]
        for item in records:
            step = item.steps[0]
            step.dispatch_generation = 1
            step.dispatch_key = dispatch_key_of(item.task_id, item.plan_hash, step.step_id, 1)
            step.dispatch_state = DispatchState.INTENT.value
            step.dispatch_at = time.time()

        async def admit(store: SqliteTaskStore, record: Any) -> bool:
            return await store.admit_dispatch_intent(
                record,
                expected_generation=0,
                event="task.step_started",
                step_id=record.steps[0].step_id,
            )

        accepted = await asyncio.gather(admit(store_a, records[0]), admit(store_b, records[1]))
        assert sorted(accepted) == [False, True]
        on_disk = [await store_a.load(records[0].task_id), await store_b.load(records[1].task_id)]
        assert (
            sum(int(item.steps[0].dispatch_state == DispatchState.INTENT.value) for item in on_disk)
            == 1
        )
        winner = 0 if accepted[0] else 1
        loser = 1 - winner
        assert on_disk[loser].steps[0].dispatch_generation == 0
        assert not on_disk[loser].steps[0].dispatch_key

        # A stale controller cannot replace a newer generation, even after a conflict is settled.
        win_store = [store_a, store_b][winner]
        settled = on_disk[winner]
        settled.steps[0].dispatch_state = DispatchState.SETTLED.value
        await win_store.save(settled)
        assert not await admit(win_store, settled)
        lose_store = [store_a, store_b][loser]
        assert await admit(lose_store, records[loser])
    finally:
        await db_b.close()
        await db_a.close()


async def test_atomic_admission_allows_only_settled_prefix_advance(tmp_path: Any) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'prefix.db'}"))
    await database.connect()
    try:
        store = SqliteTaskStore(database)
        runtime = make_runtime(DispatchDouble(), store=store)
        original = await armed_record(runtime, store, plan=single_action_plan())
        candidate = await store.load(original.task_id)
        assert candidate is not None
        candidate.current_step = 1
        candidate.steps[1].dispatch_generation = 1
        candidate.steps[1].dispatch_state = DispatchState.INTENT.value
        candidate.steps[1].dispatch_key = dispatch_key_of(
            candidate.task_id, candidate.plan_hash, candidate.steps[1].step_id, 1
        )
        assert not await store.admit_dispatch_intent(
            candidate, expected_generation=0, step_id=candidate.steps[1].step_id
        )
        original.steps[0].state = StepState.SUCCEEDED
        original.steps[0].dispatch_state = DispatchState.SETTLED.value
        await store.save(original)
        assert await store.admit_dispatch_intent(
            candidate, expected_generation=0, step_id=candidate.steps[1].step_id
        )
    finally:
        await database.close()
