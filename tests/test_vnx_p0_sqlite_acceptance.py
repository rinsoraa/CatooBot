"""VNX-1 P0 AS5: real SqliteTaskStore acceptance for task persistence and recovery."""

from __future__ import annotations

from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.tasks.models import TaskRecord, TaskState
from app.tasks.runtime import TaskConfig, TaskRuntime
from app.tasks.store import SqliteTaskStore
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_task_runtime import simple_plan
from tests.test_task_turn import FakeConfirmations

BINDING = {
    "actor_session_ref": "ps_sqlite_1",
    "player_uuid": "uuid-sqlite-1",
    "server_scope": "127.0.0.1:25565",
    "identity_assurance": "server_observed_uuid",
    "captured_at": 1.0,
}


def _runtime(store: Any, calls: list[str] | None = None) -> TaskRuntime:
    async def invoke(tool: str, arguments: Any, **kwargs: Any) -> Any:
        if calls is not None:
            calls.append(str(tool))
        from app.tasks.runtime import TaskInvocation

        return TaskInvocation(ok=True, status="RUNNING", action_id="act_sqlite")

    return TaskRuntime(
        store=store,
        invoke=invoke,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: {
            "minecraft_find_blocks": "SAFE",
            "minecraft_move_to": "LOW",
            "minecraft_dig": "MEDIUM",
            "minecraft_inventory": "SAFE",
        }.get(tool, "SAFE"),
        is_registered=lambda tool: (
            tool
            in {
                "minecraft_find_blocks",
                "minecraft_move_to",
                "minecraft_dig",
                "minecraft_inventory",
            }
        ),
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )


async def _open(tmp_path: Any, name: str) -> tuple[Database, SqliteTaskStore]:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))
    await database.connect()
    return database, SqliteTaskStore(database)


async def _allow(**_kwargs: Any) -> bool:
    return True


async def _confirmed(runtime: TaskRuntime, *, binding: Any = BINDING) -> Any:
    record = await runtime.create_task(
        simple_plan().objective,
        session_id="minecraft:127.0.0.1:25565:Steve",
        user_id="Steve",
        origin="user",
        plan=simple_plan(),
        source="minecraft_chat",
        actor_session=binding,
    )
    record = await runtime.confirm_and_start(
        record.task_id,
        user_id="Steve",
        session_id="minecraft:127.0.0.1:25565:Steve",
        origin="user",
    )
    assert record.state is TaskState.WAITING_ACTION, record.state
    return record


def _service(*, trusted: list[str] | None = None, ref: str = "ps_sqlite_1") -> Any:
    service = FakeMinecraftService()
    service.config.agent.trusted_players = list(trusted or [])
    service.player_session_ref = lambda _u: ref
    service.player_uuid = lambda _u: "uuid-sqlite-1"
    service.server_scope = lambda: "127.0.0.1:25565"
    return service


async def test_db01_actor_session_binding_survives_sqlite_roundtrip(tmp_path: Any) -> None:
    database, store = await _open(tmp_path, "db01.db")
    try:
        runtime = _runtime(store)
        record = await runtime.create_task(
            simple_plan().objective,
            session_id="minecraft:127.0.0.1:25565:Steve",
            user_id="Steve",
            origin="user",
            plan=simple_plan(),
            source="minecraft_chat",
            actor_session=BINDING,
        )
        task_id = record.task_id
        await database.close()
    finally:
        try:
            await database.close()
        except Exception:
            pass
    database2, store2 = await _open(tmp_path, "db01.db")
    try:
        loaded = await store2.load(task_id)
        assert loaded is not None
        assert loaded.actor_verified is True
        assert loaded.actor_session_ref == BINDING["actor_session_ref"]
        assert loaded.actor_player_uuid == BINDING["player_uuid"]
        assert loaded.actor_server_scope == BINDING["server_scope"]
        assert loaded.actor_identity_assurance == "server_observed_uuid"
        assert loaded.user_id == "Steve"
        assert loaded.source == "minecraft_chat"
        assert loaded.plan_hash
    finally:
        await database2.close()


async def test_db02_legacy_task_record_cannot_regain_write_permission(tmp_path: Any) -> None:
    database, store = await _open(tmp_path, "db02.db")
    try:
        runtime = _runtime(store)
        fresh = await _confirmed(runtime)
        raw = fresh.to_payload()
        for key in (
            "actor_verified",
            "actor_session_ref",
            "actor_player_uuid",
            "actor_server_scope",
            "actor_identity_assurance",
            "actor_captured_at",
        ):
            raw.pop(key, None)
        old = TaskRecord.from_payload(raw)
        await store.save(old)
        task_id = old.task_id
        await database.close()
    finally:
        try:
            await database.close()
        except Exception:
            pass
    database2, store2 = await _open(tmp_path, "db02.db")
    try:
        loaded = await store2.load(task_id)
        assert loaded is not None
        assert loaded.actor_verified is False
        grant = loaded.step_authorization(loaded.steps[1])
        bridge = MinecraftAgentBridge(_service(trusted=["Steve"]))
        bridge.set_task_authorizer(_allow)
        result = await bridge.invoke_task_step(
            loaded.steps[1].tool,
            loaded.steps[1].effective_arguments,
            task_id=loaded.task_id,
            step_id=loaded.steps[1].step_id,
            plan_hash=loaded.plan_hash,
            risk=loaded.steps[1].risk,
            authorization=grant,
            call=lambda _svc: _raise("world write"),
        )
        assert result.success is False
        assert result.error_type == "minecraft.task_authorization_missing"
    finally:
        await database2.close()


async def _raise(message: str) -> Any:
    raise AssertionError(message)


async def test_db03_revoked_or_stale_session_cannot_resume_after_restart(
    tmp_path: Any,
) -> None:
    # A. trusted revoked
    database, store = await _open(tmp_path, "db03a.db")
    try:
        runtime = _runtime(store)
        record = await _confirmed(runtime)
        task_id = record.task_id
        await database.close()
    finally:
        try:
            await database.close()
        except Exception:
            pass
    database2, store2 = await _open(tmp_path, "db03a.db")
    try:
        runtime2 = _runtime(store2)
        loaded = await runtime2.get(task_id)
        assert loaded is not None
        grant = loaded.step_authorization(loaded.steps[1])
        bridge = MinecraftAgentBridge(_service(trusted=[]))
        bridge.set_task_authorizer(_allow)
        result = await bridge.invoke_task_step(
            loaded.steps[1].tool,
            loaded.steps[1].effective_arguments,
            task_id=loaded.task_id,
            step_id=loaded.steps[1].step_id,
            plan_hash=loaded.plan_hash,
            risk=loaded.steps[1].risk,
            authorization=grant,
            call=lambda _svc: _raise("world write"),
        )
        assert result.success is False
        assert result.error_type == "minecraft.user_not_trusted"
    finally:
        await database2.close()


async def test_db03b_stale_session_cannot_resume_after_restart(tmp_path: Any) -> None:
    # B. trusted unchanged, but the original player session ref is stale.
    database, store = await _open(tmp_path, "db03b.db")
    try:
        runtime = _runtime(store)
        record = await _confirmed(runtime)
        task_id = record.task_id
        await database.close()
    finally:
        try:
            await database.close()
        except Exception:
            pass
    database2, store2 = await _open(tmp_path, "db03b.db")
    try:
        runtime2 = _runtime(store2)
        loaded = await runtime2.get(task_id)
        assert loaded is not None
        grant = loaded.step_authorization(loaded.steps[1])
        bridge = MinecraftAgentBridge(_service(trusted=["Steve"], ref="ps_sqlite_NEW"))
        bridge.set_task_authorizer(_allow)
        result = await bridge.invoke_task_step(
            loaded.steps[1].tool,
            loaded.steps[1].effective_arguments,
            task_id=loaded.task_id,
            step_id=loaded.steps[1].step_id,
            plan_hash=loaded.plan_hash,
            risk=loaded.steps[1].risk,
            authorization=grant,
            call=lambda _svc: _raise("world write"),
        )
        assert result.success is False
        assert result.error_type == "minecraft.task_authorization_missing"
        assert result.data["error"]["message"].startswith("玩家会话绑定缺失或已变化")
    finally:
        await database2.close()


async def test_db04_cancelled_task_is_not_replayed_after_sqlite_recovery(
    tmp_path: Any,
) -> None:
    calls: list[str] = []
    database, store = await _open(tmp_path, "db04.db")
    try:
        runtime = _runtime(store, calls)
        record = await _confirmed(runtime)
        task_id = record.task_id
        action_id = record.pending_action_id
        assert action_id == "act_sqlite"
        dispatch_before = len(calls)
        await database.close()
    finally:
        try:
            await database.close()
        except Exception:
            pass
    database2, store2 = await _open(tmp_path, "db04.db")
    try:
        runtime2 = _runtime(store2, calls)
        cancelled = await runtime2.cancel(task_id, reason="db04 cancel")
        assert cancelled.state is TaskState.CANCELLED
        dispatch_before = len(calls)
        await database2.close()
    finally:
        try:
            await database2.close()
        except Exception:
            pass
    database3, store3 = await _open(tmp_path, "db04.db")
    try:
        runtime3 = _runtime(store3, calls)
        first = await runtime3.on_action_event(
            action_id=action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        duplicate = await runtime3.on_action_event(
            action_id=action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        unrelated = await runtime3.on_action_event(
            action_id="act_unrelated",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        loaded = await runtime3.get(task_id)
        assert loaded is not None
        assert loaded.state is TaskState.CANCELLED
        assert first is None or first.state is TaskState.CANCELLED
        assert duplicate is None or duplicate.state is TaskState.CANCELLED
        assert unrelated is None
        assert len(calls) == dispatch_before
    finally:
        await database3.close()
