"""VNX-1 P0 AS7-STOP-WIRE-FU1: action-scoped stop plumbing."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.task_adapter import _stop
from tests.test_vnx_p0_attested_follow_e2 import _create, _wire


class _StopService:
    def __init__(self) -> None:
        self.calls: list[str | None] = []

    async def stop_action(
        self, action_id: str | None = None, stop_request_id: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(action_id)
        return {
            "ok": True,
            "cancelled": [action_id] if action_id else [],
            "targeted": bool(action_id),
        }


async def test_minecraft_stop_adapter_forwards_action_id() -> None:
    service = _StopService()
    result = await _stop(service, {"action_id": "Action-A"})
    assert service.calls == ["Action-A"]
    assert result["targeted"] is True
    assert result["cancelled"] == ["Action-A"]


async def test_minecraft_stop_adapter_global_has_no_action_id() -> None:
    service = _StopService()
    result = await _stop(service, {})
    assert service.calls == [None]
    assert result["targeted"] is False


async def test_minecraft_stop_adapter_rejects_bad_action_id() -> None:
    service = _StopService()
    with pytest.raises(ValueError):
        await _stop(service, {"action_id": ""})
    assert service.calls == []


async def test_matching_stop_terminal_confirms_action() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _identity

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_test",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-A"
    step.dispatch_state = "ACCEPTED"
    step.dispatch_generation = 1
    loaded.pending_action_id = "act-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    action_id = step.action_id
    instance = step.dispatch_runtime_instance
    assert action_id
    cancelled = await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    assert cancelled.state.value == "CANCELLED"
    await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
        runtime_instance=instance,
    )
    final = await runtime.get(task.task_id)
    detail = final.recovery["detail"]
    assert detail["stop_requested"] is True
    assert detail["stop_confirmed"] is True
    assert detail["stop_evidence_source"] == "terminal_event"
    assert final.state.value == "CANCELLED"


async def test_real_node_cancel_reason_reaches_taskruntime() -> None:
    import asyncio

    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from tests.test_vnx_p0_attested_follow_e2 import _identity

    seen: list[dict[str, Any]] = []

    class _Runtime:
        async def on_action_event(self, **kwargs: Any) -> None:
            seen.append(kwargs)

    identity = _identity()
    coordinator = MinecraftTaskCoordinator(_Runtime(), identity)  # type: ignore[arg-type]
    identity.add_listener(coordinator._on_service_event)  # noqa: SLF001
    await identity.receive_event(
        {
            "event": "minecraft.action.cancelled",
            "session_id": "runtime-1",
            "timestamp": 1.0,
            "action_id": "act-A",
            "status": "CANCELLED",
            "reason": "stop",
            "runtime_instance": "rt-1",
        }
    )
    for _ in range(20):
        if seen:
            break
        await asyncio.sleep(0.01)
    assert seen
    assert seen[0]["detail"]["reason"] == "stop"
    assert seen[0]["runtime_instance"] == "rt-1"


async def test_stop_confirmed_survives_sqlite_reload(tmp_path: Any) -> None:
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.tasks.runtime import TaskConfig, TaskRuntime
    from app.tasks.store import SqliteTaskStore
    from tests.test_task_turn import FakeConfirmations
    from tests.test_vnx_p0_attested_follow_e2 import _create

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'stop.db'}"))
    await database.connect()
    store = SqliteTaskStore(database)
    runtime = TaskRuntime(
        store=store,
        invoke=lambda *a, **k: None,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: "LOW",
        is_registered=lambda tool: tool == "minecraft_follow_player",
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )
    binding = {
        "actor_session_ref": "ps_test",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-SQL-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-SQL-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    await runtime.on_action_event(
        action_id="act-SQL-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
    )
    await database.close()
    database2 = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'stop.db'}"))
    await database2.connect()
    store2 = SqliteTaskStore(database2)
    reopened = await store2.load(task.task_id)
    assert reopened is not None
    assert reopened.state.value == "CANCELLED"
    detail = reopened.recovery["detail"]
    assert detail["stop_confirmed"] is True
    assert detail["action_id"] == "act-SQL-A"
    assert detail["stop_evidence_source"] == "terminal_event"
    await database2.close()


async def test_cancelled_dispatch_intent_blocks_new_world_action() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, action = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    old = await _create(runtime, binding, session_id="mc:old")
    loaded = await runtime.get(old.task_id)
    assert loaded is not None
    loaded.steps[0].dispatch_state = "INTENT"
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(old.task_id, reason="attested_session_invalidated")
    new = await _create(runtime, binding, session_id="mc:new")
    result = await runtime.confirm_and_start(
        new.task_id, user_id="Alice", session_id="mc:new", origin="user"
    )
    assert result.state.value != "WAITING_ACTION"
    assert action.action_calls("follow_player") == []


async def test_late_accept_scoped_stop_then_unblocks() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, action = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    old = await _create(runtime, binding, session_id="mc:old")
    loaded = await runtime.get(old.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-fence-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-fence-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(old.task_id, reason="attested_session_invalidated")
    blocked = await _create(runtime, binding, session_id="mc:blocked")
    result = await runtime.confirm_and_start(
        blocked.task_id, user_id="Alice", session_id="mc:blocked", origin="user"
    )
    assert result.state.value != "WAITING_ACTION"
    assert action.action_calls("follow_player") == []
    await runtime.on_action_event(
        action_id="act-fence-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
    )
    fresh = await _create(runtime, binding, session_id="mc:fresh")
    await runtime.confirm_and_start(
        fresh.task_id, user_id="Alice", session_id="mc:fresh", origin="user"
    )
    assert len(action.action_calls("follow_player")) == 1


async def test_ambiguous_external_dispatch_blocks_new_world_action() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, action = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    old = await _create(runtime, binding, session_id="mc:amb")
    loaded = await runtime.get(old.task_id)
    assert loaded is not None
    loaded.steps[0].dispatch_state = "AMBIGUOUS"
    await runtime._save(loaded)  # noqa: SLF001
    new = await _create(runtime, binding, session_id="mc:after-amb")
    result = await runtime.confirm_and_start(
        new.task_id, user_id="Alice", session_id="mc:after-amb", origin="user"
    )
    assert result.state.value != "WAITING_ACTION"
    assert action.action_calls("follow_player") == []


async def test_two_tasks_concurrent_world_dispatch_only_one_admitted() -> None:
    import asyncio

    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, action = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task_a = await _create(runtime, binding, session_id="mc:con-a")
    task_b = await _create(runtime, binding, session_id="mc:con-b")
    await asyncio.gather(
        runtime.confirm_and_start(
            task_a.task_id, user_id="Alice", session_id="mc:con-a", origin="user"
        ),
        runtime.confirm_and_start(
            task_b.task_id, user_id="Alice", session_id="mc:con-b", origin="user"
        ),
    )
    assert len(action.action_calls("follow_player")) == 1


async def test_stop_intent_without_target_cannot_confirm() -> None:
    from app.tasks.runtime import TaskConfig, TaskRuntime
    from app.tasks.store import InMemoryTaskStore
    from tests.test_task_turn import FakeConfirmations
    from tests.test_vnx_p0_attested_follow_e2 import _create

    runtime = TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=lambda *a, **k: None,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: "LOW",
        is_registered=lambda tool: tool == "minecraft_follow_player",
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    await runtime.cancel(task.task_id, reason="cancelled-before-action-id")
    await runtime.on_action_event(
        action_id="some-other-action",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    detail = stored.recovery.get("detail") or {}
    assert detail.get("stop_confirmed") is not True


async def test_revoked_session_a_delayed_stop_preserves_session_b() -> None:
    import asyncio

    from tests.test_vnx_p0_attested_follow_e2 import (
        SCOPE,
        TOKEN,
        SessionAttestationClient,
        _create,
        _event,
        _Handler,
        _identity,
        _start,
        _wire,
    )

    server, url = _start()
    _Handler.ref = "server-ref-1"
    _Handler.force_uuid = None
    identity = _identity()
    identity.configure_attestation(
        SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
    )
    runtime, _, action = _wire(identity)
    gate = asyncio.Event()
    stop_calls: list[str | None] = []

    async def delayed_stop(
        action_id: str | None = None, stop_request_id: str | None = None
    ) -> dict[str, Any]:
        stop_calls.append(action_id)
        await gate.wait()
        return {
            "ok": True,
            "cancelled": [action_id] if action_id else [],
            "targeted": bool(action_id),
        }

    action.stop_action = delayed_stop  # type: ignore[assignment]
    await _event(identity, "minecraft.player_joined")
    binding_a = await identity.attest_player_session("Alice")
    task_a = await _create(runtime, binding_a, session_id="mc:session-a")
    await runtime.confirm_and_start(
        task_a.task_id, user_id="Alice", session_id="mc:session-a", origin="user"
    )
    assert len(action.action_calls("follow_player")) == 1
    record_a = await runtime.get(task_a.task_id)
    assert record_a is not None
    action_a = record_a.steps[0].action_id
    assert action_a

    async def cancel_revoked(snapshot: dict[str, Any]) -> None:
        rec = await runtime.active_for_user(str(snapshot["username"]), limit=20)
        if rec is not None and rec.actor_session_ref == snapshot["actor_session_ref"]:
            await runtime.cancel(rec.task_id, reason="attested_session_invalidated")

    identity.on_attested_identity_revoked = lambda snapshot: asyncio.create_task(
        cancel_revoked(snapshot)
    )
    identity._revoke_all_identity("test")  # noqa: SLF001
    for _ in range(100):
        if stop_calls:
            break
        await asyncio.sleep(0.01)
    assert stop_calls == [action_a]
    # Session B establishes new proof while Stop A is still blocked.
    await _event(identity, "minecraft.player_joined", ts=3.0)
    binding_b = await identity.attest_player_session("Alice")
    assert binding_b["actor_session_ref"] != binding_a["actor_session_ref"]
    blocked = await _create(runtime, binding_b, session_id="mc:session-b")
    result = await runtime.confirm_and_start(
        blocked.task_id, user_id="Alice", session_id="mc:session-b", origin="user"
    )
    assert result.state.value != "WAITING_ACTION"
    assert len(action.action_calls("follow_player")) == 1
    # Release Stop A, then settle Action A with a matching terminal.
    gate.set()
    await asyncio.sleep(0.05)
    await runtime.on_action_event(
        action_id=action_a,
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
        runtime_instance=record_a.steps[0].dispatch_runtime_instance,
    )
    fresh = await _create(runtime, binding_b, session_id="mc:session-b2")
    await runtime.confirm_and_start(
        fresh.task_id, user_id="Alice", session_id="mc:session-b2", origin="user"
    )
    assert len(action.action_calls("follow_player")) == 2
    assert stop_calls == [action_a]


async def test_sqlite_fence_detects_all_conflicts_beyond_limit_two(tmp_path: Any) -> None:
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.tasks.runtime import TaskConfig, TaskRuntime
    from app.tasks.store import SqliteTaskStore
    from tests.test_task_turn import FakeConfirmations
    from tests.test_vnx_p0_attested_follow_e2 import _create

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'fence.db'}"))
    await database.connect()
    store = SqliteTaskStore(database)
    runtime = TaskRuntime(
        store=store,
        invoke=lambda *a, **k: None,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: "LOW",
        is_registered=lambda tool: tool == "minecraft_follow_player",
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    states = ["", "", "INTENT", "ACCEPTED", "AMBIGUOUS", "ACCEPTED"]
    for index, dispatch_state in enumerate(states):
        record = await _create(runtime, binding, session_id=f"mc:db-{index}")
        if dispatch_state:
            loaded = await runtime.get(record.task_id)
            assert loaded is not None
            loaded.steps[0].dispatch_state = dispatch_state
            if dispatch_state in {"ACCEPTED", "INTENT"}:
                loaded.pending_action_id = f"act-db-{index}"
                loaded.pending_step_id = loaded.steps[0].step_id
            await runtime._save(loaded)  # noqa: SLF001
    assert await store.has_conflicting_outstanding() is True
    await database.close()
    database2 = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'fence.db'}"))
    await database2.connect()
    store2 = SqliteTaskStore(database2)
    assert await store2.has_conflicting_outstanding() is True
    records = await store2.list_outstanding(None)
    assert len(records) >= 4
    await database2.close()


async def test_cleanup_awaits_bootstrap_and_refresh_and_is_idempotent() -> None:
    import asyncio

    from tests.test_vnx_p0_attested_follow_e2 import (
        SCOPE,
        TOKEN,
        ControlledClient,
        SessionAttestationClient,
        _event,
        _identity,
        _start,
    )

    server, url = _start()
    try:
        identity = _identity()
        controlled = ControlledClient(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        identity.auto_attest_on_join = True
        identity.configure_attestation(controlled, refresh_interval_seconds=0.05)
        controlled.paused.add(0)
        await _event(identity, "minecraft.player_joined")
        await asyncio.sleep(0.05)
        bootstrap = list(identity._attest_bootstrap_tasks.values())  # noqa: SLF001
        await identity.stop()
        assert all(task.done() for task in bootstrap)
        assert identity.current_attested_binding("Alice") == {}
        await identity.stop()  # idempotent
    finally:
        server.shutdown()


async def test_stop_negative_matrix_wrong_action_and_completed() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-neg-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-neg-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    # Wrong action id: no match, no confirmation.
    await runtime.on_action_event(
        action_id="act-other",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("stop_confirmed") is not True
    # Completed action: termination fact, but not caused by the scoped stop.
    await runtime.on_action_event(
        action_id="act-neg-A",
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    detail = stored.recovery["detail"]
    assert detail.get("stop_confirmed") is not True
    assert detail.get("stop_terminal_event", "").endswith("completed")


async def test_wrong_runtime_instance_cannot_confirm_scoped_stop() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-runtime-A"
    step.dispatch_state = "ACCEPTED"
    step.dispatch_runtime_instance = "rt-A"
    step.dispatch_identity_tracked = True
    loaded.pending_action_id = "act-runtime-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    await runtime.on_action_event(
        action_id="act-runtime-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop"},
        runtime_instance="rt-B",
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    detail = stored.recovery.get("detail") or {}
    assert detail.get("stop_confirmed") is not True


async def test_stop_request_id_negative_matrix() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-causal-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-causal-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    stored = await runtime.get(task.task_id)
    assert stored is not None
    request_id = str(stored.recovery["detail"].get("stop_request_id") or "")
    assert request_id
    # Wrong request id: legacy stop_confirmed may be true, strict causality must not.
    await runtime.on_action_event(
        action_id="act-causal-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop", "stop_request_id": "stop_wrong"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    detail = stored.recovery["detail"]
    assert detail.get("stop_confirmed") is True
    assert detail.get("scoped_stop_confirmed") is False


async def test_natural_completion_is_not_attributed_to_scoped_stop() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-natural-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-natural-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    stored = await runtime.get(task.task_id)
    assert stored is not None
    request_id = str(stored.recovery["detail"].get("stop_request_id") or "")
    assert request_id
    await runtime.on_action_event(
        action_id="act-natural-A",
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={"ok": True},
        detail={"stop_request_id": request_id},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    detail = stored.recovery["detail"]
    assert detail.get("stop_confirmed") is False
    assert detail.get("scoped_stop_confirmed") is not True


async def test_stop_terminal_before_http_response_confirms_after_reconciliation() -> None:
    import asyncio

    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, action = _wire(identity)
    gate = asyncio.Event()

    async def delayed_stop(
        action_id: str | None = None, stop_request_id: str | None = None
    ) -> dict[str, Any]:
        await gate.wait()
        return {"ok": True, "cancelled": [action_id] if action_id else [], "targeted": True}

    action.stop_action = delayed_stop  # type: ignore[assignment]
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-order-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-order-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    cancel_task = asyncio.create_task(
        runtime.cancel(task.task_id, reason="attested_session_invalidated")
    )
    for _ in range(100):
        stored = await runtime.get(task.task_id)
        if stored and stored.recovery.get("detail", {}).get("stop_request_id"):
            break
        await asyncio.sleep(0.01)
    stored = await runtime.get(task.task_id)
    assert stored is not None
    request_id = str(stored.recovery["detail"]["stop_request_id"])
    # Terminal arrives before the HTTP stop response is delivered.
    await runtime.on_action_event(
        action_id="act-order-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={
            "reason": "stop",
            "stop_request_id": request_id,
            "stop_scope": "scoped",
        },
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is True
    gate.set()
    await asyncio.wait_for(cancel_task, timeout=10)
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is True


async def test_duplicate_terminal_does_not_regress_strict_confirmation() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-dup-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-dup-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    stored = await runtime.get(task.task_id)
    assert stored is not None
    request_id = str(stored.recovery["detail"].get("stop_request_id") or "")
    event = {
        "action_id": "act-dup-A",
        "event": "minecraft.action.cancelled",
        "status": "CANCELLED",
        "detail": {
            "reason": "stop",
            "stop_request_id": request_id,
            "stop_scope": "scoped",
        },
    }
    await runtime.on_action_event(**event)
    first = await runtime.get(task.task_id)
    assert first is not None
    assert first.recovery["detail"].get("scoped_stop_confirmed") is True
    await runtime.on_action_event(**event)
    second = await runtime.get(task.task_id)
    assert second is not None
    assert second.recovery["detail"].get("scoped_stop_confirmed") is True
    assert second.steps[0].dispatch_state == "SETTLED"


async def test_late_error_and_conflicting_terminal_do_not_regress_confirmation() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-mono-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-mono-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    stored = await runtime.get(task.task_id)
    assert stored is not None
    request_id = str(stored.recovery["detail"].get("stop_request_id") or "")
    await runtime.on_action_event(
        action_id="act-mono-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={
            "reason": "stop",
            "stop_request_id": request_id,
            "stop_scope": "scoped",
        },
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is True
    # Late HTTP error merges must not erase strict confirmation.
    await runtime._merge_stop_evidence(  # noqa: SLF001
        task.task_id, reason="late_http_error", note="timeout after terminal"
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is True
    # Conflicting replay cannot rewrite the confirmed identity.
    await runtime.on_action_event(
        action_id="act-mono-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop", "stop_request_id": "s1.YWN0LW1vbm8tQQ.bad"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is True


async def test_strict_scope_matrix_rejects_legacy_global_and_malformed() -> None:
    from app.tasks.runtime import strict_stop_evidence_valid
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-scope-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-scope-A"
    loaded.pending_step_id = step.step_id
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.cancel(task.task_id, reason="attested_session_invalidated")
    stored = await runtime.get(task.task_id)
    assert stored is not None
    valid_id = str(stored.recovery["detail"]["stop_request_id"])
    await runtime.on_action_event(
        action_id="act-scope-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop", "stop_request_id": valid_id, "stop_scope": "global"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is not True
    assert (
        strict_stop_evidence_valid(
            {
                "scoped_stop_confirmed": True,
                "stop_request_id": "stop_legacy",
                "stop_target_action_id": "act-scope-A",
                "stop_scope": "scoped",
            }
        )
        is False
    )
    assert (
        strict_stop_evidence_valid(
            {
                "scoped_stop_confirmed": True,
                "stop_request_id": "s1.not-base64!.x",
                "stop_target_action_id": "act-scope-A",
                "stop_scope": "scoped",
            }
        )
        is False
    )


async def test_legacy_stop_id_matching_terminal_is_not_strict_confirmation() -> None:
    from tests.test_vnx_p0_attested_follow_e2 import _create, _identity, _wire

    identity = _identity()
    runtime, _, _ = _wire(identity)
    binding = {
        "actor_session_ref": "ps_a",
        "player_uuid": "uuid-A",
        "server_scope": "mc.test:25565",
        "identity_assurance": "server_attested",
    }
    task = await _create(runtime, binding)
    loaded = await runtime.get(task.task_id)
    assert loaded is not None
    step = loaded.steps[0]
    step.action_id = "act-legacy-A"
    step.dispatch_state = "ACCEPTED"
    loaded.pending_action_id = "act-legacy-A"
    loaded.pending_step_id = step.step_id
    raw = loaded.recovery if isinstance(loaded.recovery, dict) else {}
    detail = dict(raw.get("detail") or {})
    detail.update(
        {
            "stop_requested": True,
            "stop_request_id": "stop_legacy",
            "stop_target_action_id": "act-legacy-A",
            "stop_scope": "scoped",
        }
    )
    loaded.recovery = {
        "reason": "cancel",
        "outcome": "UNKNOWN",
        "at": raw.get("at"),
        "detail": detail,
    }
    await runtime._save(loaded)  # noqa: SLF001
    await runtime.on_action_event(
        action_id="act-legacy-A",
        event="minecraft.action.cancelled",
        status="CANCELLED",
        detail={"reason": "stop", "stop_request_id": "stop_legacy", "stop_scope": "scoped"},
    )
    stored = await runtime.get(task.task_id)
    assert stored is not None
    assert stored.recovery["detail"].get("scoped_stop_confirmed") is not True
