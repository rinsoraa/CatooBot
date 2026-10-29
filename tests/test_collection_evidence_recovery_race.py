"""R3.1: persisted collection evidence and deterministic terminal races."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.collection_evidence import collection_evidence
from app.tasks.models import TaskState
from app.tasks.runtime import TaskInvocation
from app.tasks.store import SqliteTaskStore
from tests.minecraft_memory_fakes import build_bridge
from tests.skill_fakes import FakeObserve, make_service, resource_record
from tests.test_collection_evidence_chain import (
    _dig_result,
    _pickup_payload,
    _runtime,
    _start_collection,
    _to_pickup,
)


@pytest.mark.parametrize("terminal", [TaskState.CANCELLED, TaskState.EXPIRED])
@pytest.mark.parametrize("inventory_ok", [True, False])
async def test_terminal_wins_during_final_collection_inventory(
    tmp_path: Any, terminal: TaskState, inventory_ok: bool
) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'race.db'}"))
    await database.connect()
    finishing: asyncio.Task[Any] | None = None
    release = asyncio.Event()
    try:
        store = SqliteTaskStore(database)
        calls: list[str] = []
        runtime = _runtime(calls, store=store)
        events: list[tuple[str, dict[str, Any]]] = []
        runtime._publish_fn = lambda name, payload: events.append((name, payload))
        started = await _start_collection(runtime)
        dig_action = started.pending_action_id
        pickup = await _to_pickup(runtime, started)
        original_invoke = runtime._invoke
        entered = asyncio.Event()
        inventory_reads = 0

        async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
            nonlocal inventory_reads
            result = await original_invoke(tool, arguments, **kwargs)
            if tool == "minecraft_inventory":
                inventory_reads += 1
                # First read is the plan's last SAFE step; second is the
                # independent final inventory verification in _finish.
                if inventory_reads == 2:
                    entered.set()
                    await release.wait()
                    if not inventory_ok:
                        return TaskInvocation(ok=True, status="SUCCEEDED", result={"items": []})
            return result

        runtime._invoke = invoke
        finishing = asyncio.create_task(
            runtime.on_action_event(
                action_id=pickup.pending_action_id,
                event="minecraft.action.completed",
                status="SUCCEEDED",
                result=_pickup_payload(pickup.pending_action_id, dig_action=dig_action),
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        method = runtime.cancel if terminal is TaskState.CANCELLED else runtime.expire
        stopped = await asyncio.wait_for(method(started.task_id), 3)
        assert stopped.state is terminal
        terminal_event_count = len(events)
        action_count = sum(name in {"minecraft_dig", "minecraft_pickup_item"} for name in calls)
        release.set()
        result = await asyncio.wait_for(finishing, 3)
        final = await SqliteTaskStore(database).load(started.task_id)
        assert final is not None
        assert final.state is terminal
        assert result is not None and result.state is terminal
        assert not any(name in {"task.succeeded", "task.failed"} for name, _ in events)
        assert not events[terminal_event_count:]
        assert not any(
            row["event"] in {"task.succeeded", "task.failed"}
            for row in await store.checkpoints(started.task_id)
        )
        assert (
            sum(name in {"minecraft_dig", "minecraft_pickup_item"} for name in calls)
            == action_count
        )
        assert all(version.status != "COMPLETED" for version in final.plan_history)
    finally:
        release.set()
        if finishing is not None:
            await asyncio.gather(finishing, return_exceptions=True)
        await database.close()


@pytest.mark.parametrize("corrupt", [False, True])
async def test_new_runtime_recovery_rechecks_collection_and_downstream_writers(
    tmp_path: Any, corrupt: bool
) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'recovery.db'}"))
    await database.connect()
    try:
        store = SqliteTaskStore(database)
        # Crash checkpoint after all steps settled, before independent final
        # verification. A historical cached verdict must not authorize success.
        record = resource_record(state=TaskState.RUNNING, plan_status="ACTIVE")
        record.result["collection_verification"] = {"checked": True, "ok": True}
        if corrupt:
            dig = next(step for step in record.steps if step.tool == "minecraft_dig")
            dig.result["attribution"]["server_collection"]["drops"][0]["item"] = "oak_planks"
        await store.save(record)
        await database.close()
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'recovery.db'}"))
        await database.connect()

        calls: list[str] = []
        fresh = _runtime(calls, store=SqliteTaskStore(database))
        recovered = await fresh.recover_persisted_tasks()
        assert len(recovered) == 1 and recovered[0].state is TaskState.PAUSED
        pending = await fresh.resume(
            record.task_id, user_id=record.user_id, session_id=record.session_id, origin="user"
        )
        assert pending.state is TaskState.PENDING_CONFIRMATION
        finished = await fresh.confirm_and_start(
            record.task_id,
            user_id=record.user_id,
            session_id=record.session_id,
            origin="user",
        )
        assert finished.state is (TaskState.FAILED if corrupt else TaskState.SUCCEEDED)
        assert not any(name in {"minecraft_dig", "minecraft_pickup_item"} for name in calls)
        assert collection_evidence(finished)["ok"] is not corrupt

        bridge, _, _, _ = await build_bridge(tmp_path, database=database)
        skills = make_service(observe=FakeObserve())
        outcome = await skills.on_task_finished(finished)
        if corrupt:
            assert outcome["action"] == "rejected"
            # Also reject a persisted pseudo-success with the same bad graph:
            # success caches, terminal labels and final inventory are insufficient.
            pseudo = await SqliteTaskStore(database).load(record.task_id)
            assert pseudo is not None
            pseudo.state = TaskState.SUCCEEDED
            pseudo.result["collection_verification"] = {"checked": True, "ok": True}
            pseudo.verification = {"inventory_delta": {"oak_log": 1}}
            before = await bridge.store.all_facts(server_id=bridge.server_id())
            await bridge.on_task_finished(pseudo)
            assert await bridge.store.all_facts(server_id=bridge.server_id()) == before
            fresh_skills = make_service(observe=FakeObserve())
            rejected = await fresh_skills.on_task_finished(pseudo)
            assert rejected["action"] == "rejected"
        else:
            await bridge.on_task_finished(finished)
            facts = await bridge.store.all_facts(server_id=bridge.server_id())
            assert any(fact.kind.value == "TASK" for fact in facts)
            assert outcome["action"] != "rejected"
    finally:
        await database.close()


@pytest.mark.parametrize("terminal", [TaskState.CANCELLED, TaskState.EXPIRED])
async def test_terminal_request_queues_while_pickup_settlement_holds_lock(
    tmp_path: Any, terminal: TaskState
) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'settlement.db'}"))
    await database.connect()
    entered = asyncio.Event()
    release = asyncio.Event()
    requested = asyncio.Event()
    jobs: list[asyncio.Task[Any]] = []

    class GatedStore(SqliteTaskStore):
        async def save(self, record: Any, **kwargs: Any) -> None:
            if not entered.is_set() and any(
                step.tool == "minecraft_pickup_item" and step.result.get("server_attribution")
                for step in record.steps
            ):
                entered.set()
                await release.wait()
            await super().save(record, **kwargs)

    try:
        calls: list[str] = []
        runtime = _runtime(calls, store=GatedStore(database))
        events: list[str] = []
        runtime._publish_fn = lambda name, payload: events.append(name)
        started = await _start_collection(runtime)
        pickup = await _to_pickup(runtime, started)
        jobs.append(
            asyncio.create_task(
                runtime.on_action_event(
                    action_id=pickup.pending_action_id,
                    event="minecraft.action.completed",
                    status="SUCCEEDED",
                    result=_pickup_payload(
                        pickup.pending_action_id, dig_action=started.pending_action_id
                    ),
                )
            )
        )
        await asyncio.wait_for(entered.wait(), 3)

        async def request_terminal() -> Any:
            requested.set()
            method = runtime.cancel if terminal is TaskState.CANCELLED else runtime.expire
            return await method(started.task_id)

        jobs.append(asyncio.create_task(request_terminal()))
        await asyncio.wait_for(requested.wait(), 3)
        assert not jobs[1].done(), "terminal request must wait for the held lifecycle lock"
        release.set()
        await asyncio.wait_for(asyncio.gather(*jobs), 3)
        persisted = await SqliteTaskStore(database).load(started.task_id)
        assert persisted is not None and persisted.state is terminal
        assert "task.succeeded" not in events
        assert calls.count("minecraft_dig") == 1
        assert calls.count("minecraft_pickup_item") == 1
        assert (
            events.count("task.cancelled" if terminal is TaskState.CANCELLED else "task.expired")
            == 1
        )
    finally:
        release.set()
        await asyncio.gather(*jobs, return_exceptions=True)
        await database.close()


@pytest.mark.parametrize("terminal", [TaskState.CANCELLED, TaskState.EXPIRED])
@pytest.mark.parametrize("late_state", [TaskState.SUCCEEDED, TaskState.FAILED])
async def test_old_terminal_writer_cannot_replace_the_first_terminal(
    tmp_path: Any, terminal: TaskState, late_state: TaskState
) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'old-writer.db'}"))
    await database.connect()
    try:
        store = SqliteTaskStore(database)
        runtime = _runtime([], store=store)
        events: list[tuple[str, dict[str, Any]]] = []
        runtime._publish_fn = lambda name, payload: events.append((name, payload))
        started = await _start_collection(runtime)
        stale = await store.load(started.task_id)
        assert stale is not None
        method = runtime.cancel if terminal is TaskState.CANCELLED else runtime.expire
        stopped = await method(started.task_id)
        count = len(events)
        stale.state = late_state
        stale.result["summary"] = "stale success"
        stale.mark_plan_completed()
        event = "task.succeeded" if late_state is TaskState.SUCCEEDED else "task.failed"
        await runtime._save(stale, event=event)
        await runtime._publish(event, runtime._event_payload(stale))
        loaded = await store.load(started.task_id)
        assert loaded is not None and loaded.state is terminal
        assert loaded.pending_action_id == stopped.pending_action_id
        assert loaded.result == stopped.result
        assert not any(row["event"] == event for row in await store.checkpoints(started.task_id))
        assert len(events) == count
        assert all(version.status != "COMPLETED" for version in loaded.plan_history)
    finally:
        await database.close()


@pytest.mark.parametrize("early_action", ["act_previous_pickup", "act_pick_1"])
async def test_early_pickup_cannot_complete_uncommitted_dig(
    tmp_path: Any, early_action: str
) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'early.db'}"))
    await database.connect()
    release = asyncio.Event()
    settling: asyncio.Task[Any] | None = None
    entered = asyncio.Event()

    class GatedStore(SqliteTaskStore):
        async def save(self, record: Any, **kwargs: Any) -> None:
            if not entered.is_set() and any(
                step.tool == "minecraft_dig" and step.result.get("attribution")
                for step in record.steps
            ):
                entered.set()
                await release.wait()
            await super().save(record, **kwargs)

    try:
        calls: list[str] = []
        runtime = _runtime(calls, store=GatedStore(database))
        started = await _start_collection(runtime)
        dig_action = started.pending_action_id
        settling = asyncio.create_task(
            runtime.on_action_event(
                action_id=dig_action,
                event="minecraft.action.completed",
                status="SUCCEEDED",
                result=_dig_result(dig_action),
            )
        )
        await asyncio.wait_for(entered.wait(), 3)
        before = list(calls)
        await runtime.on_action_event(
            action_id=early_action,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result=_pickup_payload(early_action, dig_action="act_previous_dig"),
        )
        persisted = await SqliteTaskStore(database).load(started.task_id)
        assert persisted is not None and persisted.state is TaskState.WAITING_ACTION
        assert collection_evidence(persisted)["ok"] is False
        assert calls == before
        release.set()
        pickup = await asyncio.wait_for(settling, 3)
        assert pickup is not None
        if early_action == "act_pick_1":
            # Even when a future action id happens to match, a different parent
            # break must fail the graph. A later legitimate event cannot revive it.
            assert pickup.state is TaskState.FAILED
            before_late = list(calls)
            await runtime.on_action_event(
                action_id=early_action,
                event="minecraft.action.completed",
                status="SUCCEEDED",
                result=_pickup_payload(early_action, dig_action=dig_action),
            )
            final = await runtime.get(started.task_id)
            assert final is not None and final.state is TaskState.FAILED
            assert calls == before_late
            return
        assert pickup.state is TaskState.WAITING_ACTION
        assert collection_evidence(pickup)["ok"] is False
        finished = await runtime.on_action_event(
            action_id=pickup.pending_action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result=_pickup_payload(pickup.pending_action_id, dig_action=dig_action),
        )
        assert finished is not None and finished.state is TaskState.SUCCEEDED
        assert collection_evidence(finished)["verified_pickups"] == 1
        assert calls.count("minecraft_pickup_item") == 1
    finally:
        release.set()
        if settling is not None:
            await asyncio.gather(settling, return_exceptions=True)
        await database.close()
