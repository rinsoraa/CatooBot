"""VNX-1 P0 AS7 Gate B: exact actor-ref revocation + real Node cross-session Stop."""

from __future__ import annotations

import json
import logging
from types import SimpleNamespace
from typing import Any

from app.core.bot import Bot
from app.tasks.models import TaskState
from app.tasks.runtime import strict_stop_evidence_valid
from tests.test_vnx_p0_attested_follow_e2 import _identity, _wire
from tests.test_vnx_p0_session_authorization_e2 import _create

BINDING_A = {
    "actor_session_ref": "ps_session_a",
    "player_uuid": "uuid-tester",
    "server_scope": "mc.test:25565",
    "identity_assurance": "server_attested",
}
BINDING_B = {
    "actor_session_ref": "ps_session_b",
    "player_uuid": "uuid-tester",
    "server_scope": "mc.test:25565",
    "identity_assurance": "server_attested",
}


async def test_late_revocation_a_skips_newer_b_and_cancels_old_a() -> None:
    identity = _identity()
    runtime, _, _ = _wire(identity)
    old = await _create(runtime, BINDING_A, session_id="mc:rev-a")
    new = await _create(runtime, BINDING_B, session_id="mc:rev-b")
    loaded_old = await runtime.get(old.task_id)
    loaded_new = await runtime.get(new.task_id)
    assert loaded_old is not None and loaded_new is not None
    loaded_old.state = TaskState.WAITING_ACTION
    loaded_old.updated_at = 10.0
    await runtime._save(loaded_old)  # noqa: SLF001
    loaded_new.state = TaskState.PAUSED
    loaded_new.updated_at = 20.0
    await runtime._save(loaded_new)  # noqa: SLF001
    fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("gate-b"))
    await Bot._revoke_attested_tasks(  # noqa: SLF001
        fake_bot,
        {"username": "Alice", "actor_session_ref": "ps_session_a"},
    )
    final_old = await runtime.get(old.task_id)
    final_new = await runtime.get(new.task_id)
    assert final_old is not None and final_new is not None
    assert final_old.state is TaskState.CANCELLED
    assert final_new.state is TaskState.PAUSED


async def test_real_node_delayed_stop_a_preserves_session_b(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore
    from tests.test_vnx_p0_gate_a_real_http import (
        _free_port,
        _runtime_for,
        _service_for,
        _start_harness,
        _wait,
    )

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-b.db'}"))
    await db.connect()
    ga.service_database = db
    callback = None
    proxy = None
    harness = None
    service = None
    coordinator = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            events: list[dict[str, Any]] = []

            async def _handle(self, request: Any) -> Any:
                payload = await request.json()
                self.events.append(payload)
                await holder["service"].receive_event(payload)
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = _free_port()
        server_port = _free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await _service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await _start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding_a = await service.attest_player_session("Tester")
        runtime = await _runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task_a = await ga._follow_task(runtime, binding_a, session_id="mc:b-a", target="Tester")
        start_a = asyncio.create_task(
            runtime.confirm_and_start(
                task_a.task_id, user_id="Tester", session_id="mc:b-a", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy,
            label="Follow accepted",
            timeout=60,
            task_probe=lambda: runtime.get(task_a.task_id),
        )
        proxy.release_follow.set()
        await asyncio.wait_for(start_a, timeout=30)
        await _wait(lambda: ga._task_state(runtime, task_a.task_id, "WAITING_ACTION"), "A waiting")
        # Production revocation handler must find Task A by actor ref.
        proxy.hold_stop_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("gate-b"))
        revoke_task = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding_a["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_requested.is_set, "stop request forwarded", 30)
        assert proxy.stop_requests[-1] == action_a
        record_a = await runtime.get(task_a.task_id)
        assert record_a is not None
        assert record_a.state.value == "CANCELLED"
        # Session B establishes a distinct attested actor ref.
        tester_uuid = service.player_uuid("Tester")
        assert tester_uuid
        sid = service._mirror.get("session_id")  # noqa: SLF001
        await service.receive_event(
            {
                "event": "minecraft.player_left",
                "session_id": sid,
                "timestamp": 20.0,
                "username": "Tester",
                "uuid": tester_uuid,
            }
        )
        await service.receive_event(
            {
                "event": "minecraft.player_joined",
                "session_id": sid,
                "timestamp": 21.0,
                "username": "Tester",
                "uuid": tester_uuid,
            }
        )
        binding_b = await service.attest_player_session("Tester")
        assert binding_b["actor_session_ref"] != binding_a["actor_session_ref"]
        task_b_blocked = await ga._follow_task(
            runtime, binding_b, session_id="mc:b-blocked", target="Tester"
        )
        blocked = await runtime.confirm_and_start(
            task_b_blocked.task_id, user_id="Tester", session_id="mc:b-blocked", origin="user"
        )
        assert blocked.state.value != "WAITING_ACTION"
        assert len(proxy.accepted_action_ids) == 1
        # Release Stop A; Node must only cancel Action A.
        proxy.release_stop.set()
        await asyncio.wait_for(revoke_task, timeout=30)
        await _wait(lambda: ga._action_settled(runtime, task_a.task_id), "A settled")
        # Session B fresh task executes real Follow B.
        task_b = await ga._follow_task(runtime, binding_b, session_id="mc:b-b", target="Tester")
        start_b = asyncio.create_task(
            runtime.confirm_and_start(
                task_b.task_id, user_id="Tester", session_id="mc:b-b", origin="user"
            )
        )

        async def _second_accepted() -> bool:
            return len(proxy.accepted_action_ids) >= 2

        await _wait(_second_accepted, "Follow B accepted")
        await asyncio.wait_for(start_b, timeout=30)
        action_b = proxy.accepted_action_ids[-1]
        assert action_b != action_a
        await _wait(lambda: ga._task_state(runtime, task_b.task_id, "WAITING_ACTION"), "B waiting")
        status_b = await service._client.status()  # noqa: SLF001
        assert status_b["action"]["status"] == "RUNNING"
        assert status_b["action"]["action_id"] == action_b
        # Late Stop A is targeted/no-match and must not touch Action B.
        proxy.hold_stop_target = ""
        late = await service._client.stop(action_a)  # noqa: SLF001
        assert late.get("targeted") is True
        assert late.get("cancelled") == []
        dup = await service._client.stop(action_a)  # noqa: SLF001
        assert dup.get("targeted") is True
        assert dup.get("cancelled") == []
        status_after = await service._client.status()  # noqa: SLF001
        assert status_after["action"]["action_id"] == action_b
        assert status_after["action"]["status"] == "RUNNING"
        # SQLite full identity evidence reload.
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-b.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task_a.task_id)
        new = await store.load(task_b.task_id)
        assert old is not None and new is not None
        assert old.state.value == "CANCELLED"
        assert old.actor_session_ref == binding_a["actor_session_ref"]
        assert old.steps[0].action_id == action_a
        assert old.steps[0].dispatch_generation >= 1
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("scoped_stop_confirmed") is True
        assert strict_stop_evidence_valid(detail) is True
        assert detail.get("stop_target_action_id") == action_a
        assert new.actor_session_ref == binding_b["actor_session_ref"]
        assert new.steps[0].action_id == action_b
        assert action_a != action_b
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_terminal_before_http_response(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-order.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:c2", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:c2", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        proxy.hold_stop_response_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_response_received.is_set, "stop response received", 60)
        assert not proxy.release_stop_response.is_set()

        async def _strict() -> bool:
            record = await runtime.get(task.task_id)
            if record is None:
                return False
            return (record.recovery.get("detail") or {}).get("scoped_stop_confirmed") is True

        await ga._wait(_strict, "strict confirmation before response", timeout=30)
        proxy.release_stop_response.set()
        await asyncio.wait_for(revoke, timeout=30)
        assert await _strict()
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-order.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_target_action_id") == action_a
        assert detail.get("scoped_stop_confirmed") is True
        assert strict_stop_evidence_valid(detail) is True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_http_response_before_terminal_delivery(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-order2.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Holder(ga.CallbackReceiver):
            hold = False
            held: list[dict[str, Any]] = []
            held_event = asyncio.Event()
            release_event = asyncio.Event()

            async def _handle(self, request: Any) -> Any:
                payload = await request.json()
                if self.hold and payload.get("event") == "minecraft.action.cancelled":
                    self.held.append(payload)
                    self.held_event.set()
                    await self.release_event.wait()
                await holder["service"].receive_event(payload)
                return ga.web.json_response({"ok": True})

        callback = _Holder(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:c2b", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:c2b", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        callback.hold = True
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2b"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(callback.held_event.is_set, "terminal delivered", 30)
        await asyncio.wait_for(revoke, timeout=30)

        async def _strict() -> bool:
            record = await runtime.get(task.task_id)
            if record is None:
                return False
            return (record.recovery.get("detail") or {}).get("scoped_stop_confirmed") is True

        assert not await _strict()
        callback.release_event.set()
        await ga._wait(_strict, "strict after terminal", timeout=30)
        # Replay the exact captured Node terminal (test-injected duplicate).
        await service.receive_event(callback.held[0])
        await ga._wait(_strict, "strict survives duplicate terminal", timeout=5)
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-order2.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_target_action_id") == action_a
        assert detail.get("scoped_stop_confirmed") is True
        assert strict_stop_evidence_valid(detail) is True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_response_lost_after_terminal(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-lost.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:lost-stop", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:lost-stop", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        proxy.drop_stop_response_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2lost"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_response_dropped.is_set, "stop response dropped", 60)

        async def _strict() -> bool:
            record = await runtime.get(task.task_id)
            if record is None:
                return False
            return (record.recovery.get("detail") or {}).get("scoped_stop_confirmed") is True

        await ga._wait(_strict, "strict from real terminal despite lost response", timeout=30)
        assert proxy.stop_response_dropped.is_set()
        assert len(proxy.stop_responses) >= 1
        revoke.cancel()
        try:
            await revoke
        except asyncio.CancelledError:
            pass
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-lost.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_target_action_id") == action_a
        assert detail.get("scoped_stop_confirmed") is True
        assert strict_stop_evidence_valid(detail) is True
        assert len(proxy.stop_responses) >= 1
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_request_dropped_before_forward(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-drop.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:drop-stop", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:drop-stop", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        proxy.drop_stop_request_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2drop"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.drop_stop_request_seen.is_set, "stop request dropped", 30)
        await asyncio.wait_for(revoke, timeout=30)
        record = await runtime.get(task.task_id)
        assert record is not None
        detail = record.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_confirmed") is not True
        assert detail.get("scoped_stop_confirmed") is not True
        status = await service._client.status()  # noqa: SLF001
        assert status["action"]["status"] == "RUNNING"
        assert status["action"]["action_id"] == action_a
        blocked = await ga._follow_task(runtime, binding, session_id="mc:drop-b", target="Tester")
        result = await runtime.confirm_and_start(
            blocked.task_id, user_id="Tester", session_id="mc:drop-b", origin="user"
        )
        assert result.state.value != "WAITING_ACTION"
        assert len(proxy.accepted_action_ids) == 1
        # Cleanup through the control plane; not part of original stop evidence.
        proxy.drop_stop_request_target = ""
        await service._client.stop(action_a)  # noqa: SLF001
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-drop.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("scoped_stop_confirmed") is not True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_http_timeout_after_acceptance(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-timeout.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        # The 1 s budget under test belongs to the STOP phase; the Follow dispatch runs
        # with the normal budget (see the narrowing step below).
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:timeout", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:timeout", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        # Narrow the client budget for the STOP phase only (1.0 s = the contract under
        # test), then restore it so later read-only calls keep the normal budget.
        previous_budget = await ga._set_client_request_budget(service, 1.0)
        proxy.hold_stop_response_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2timeout"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_response_received.is_set, "stop response received", 60)

        async def _strict() -> bool:
            record = await runtime.get(task.task_id)
            if record is None:
                return False
            return (record.recovery.get("detail") or {}).get("scoped_stop_confirmed") is True

        await ga._wait(_strict, "strict from terminal", timeout=30)
        # Client timeout must end the revocation coroutine naturally, without test cancel().
        await asyncio.wait_for(revoke, timeout=15)
        assert await _strict()
        proxy.release_stop_response.set()
        await ga._set_client_request_budget(service, previous_budget)
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-timeout.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_target_action_id") == action_a
        assert detail.get("scoped_stop_confirmed") is True
        assert strict_stop_evidence_valid(detail) is True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_stop_http_timeout_before_forward(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-timeout-b.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        # The 1 s budget under test belongs to the STOP phase; the Follow dispatch runs
        # with the normal budget (see the narrowing step below).
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:timeout-b", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:timeout-b", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        # Narrow the client budget for the STOP phase only (1.0 s = the contract under
        # test), then restore it so later read-only calls keep the normal budget.
        previous_budget = await ga._set_client_request_budget(service, 1.0)
        proxy.hold_stop_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("c2timeoutb"))
        revoke = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_requested.is_set, "stop request forwarded", 30)
        await asyncio.wait_for(revoke, timeout=15)
        await ga._set_client_request_budget(service, previous_budget)
        record = await runtime.get(task.task_id)
        assert record is not None
        detail = record.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("stop_confirmed") is not True
        assert detail.get("scoped_stop_confirmed") is not True
        status = await service._client.status()  # noqa: SLF001
        assert status["action"]["status"] == "RUNNING"
        assert status["action"]["action_id"] == action_a
        blocked = await ga._follow_task(
            runtime, binding, session_id="mc:timeout-b2", target="Tester"
        )
        result = await runtime.confirm_and_start(
            blocked.task_id, user_id="Tester", session_id="mc:timeout-b2", origin="user"
        )
        assert result.state.value != "WAITING_ACTION"
        assert len(proxy.accepted_action_ids) == 1
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c2-timeout-b.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        detail = old.recovery.get("detail") or {}
        assert detail.get("stop_request_id")
        assert detail.get("scoped_stop_confirmed") is not True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_global_stop_cannot_confirm_pending_scoped_stop(tmp_path: Any) -> None:
    import asyncio
    from types import SimpleNamespace

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.runtime import strict_stop_evidence_valid
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2a-global.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:e2a", target="Tester")
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:e2a", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        proxy.hold_stop_target = action_a
        fake_bot = SimpleNamespace(tasks=runtime, log=logging.getLogger("e2a"))
        scoped = asyncio.create_task(
            Bot._revoke_attested_tasks(  # noqa: SLF001
                fake_bot,
                {"username": "Tester", "actor_session_ref": binding["actor_session_ref"]},
            )
        )
        await ga._wait(proxy.stop_requested.is_set, "stop request forwarded", 30)
        stored = await runtime.get(task.task_id)
        assert stored is not None
        scoped_id = str(stored.recovery["detail"].get("stop_request_id") or "")
        assert scoped_id.startswith("s1.")
        # Independent control plane: targetless Global Stop.
        global_response = await service._client.stop()  # noqa: SLF001
        assert global_response.get("stop_scope") == "global"
        assert "stop_request_id" not in global_response

        async def _global_applied() -> bool:
            current = await runtime.get(task.task_id)
            if current is None:
                return False
            return (current.recovery.get("detail") or {}).get("stop_scope") == "global"

        await ga._wait(_global_applied, "global terminal applied", timeout=30)
        record = await runtime.get(task.task_id)
        assert record is not None
        detail = record.recovery.get("detail") or {}
        assert detail.get("stop_scope") == "global"
        assert "stop_request_id" not in detail or not detail.get("stop_request_id")
        assert detail.get("scoped_stop_confirmed") is not True
        assert strict_stop_evidence_valid(detail) is False
        proxy.release_stop.set()
        await asyncio.wait_for(scoped, timeout=30)
        final = await runtime.get(task.task_id)
        assert final is not None
        final_detail = final.recovery.get("detail") or {}
        assert final_detail.get("scoped_stop_confirmed") is not True
        assert strict_stop_evidence_valid(final_detail) is False
        status = await service._client.status()  # noqa: SLF001
        assert status["action"]["active_count"] == 0
        assert len(proxy.stop_requests) == 2
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2a-global.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        old_detail = old.recovery.get("detail") or {}
        assert old_detail.get("stop_scope") == "global"
        assert old_detail.get("scoped_stop_confirmed") is not True
        assert strict_stop_evidence_valid(old_detail) is False
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_follow_target_lost_before_stop(tmp_path: Any) -> None:
    import asyncio

    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2b-lost.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = info = service = coordinator = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            events: list[dict[str, Any]] = []

            async def _handle(self, request: Any) -> Any:
                payload = await request.json()
                self.events.append(payload)
                await holder["service"].receive_event(payload)
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        service.config.agent.trusted_players = ["Tester", "TargetB"]
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        assert info.get("controlPort")
        spawned = await ga._observer_control(info, "spawn", "TargetB")
        assert spawned["name"] == "TargetB"
        # Readiness is explicit: the runtime must report ONLINE with our controller and
        # list both players as live entities before a Follow can legitimately be accepted
        # (runtime.js rejects player.not_found otherwise).
        await ga._wait_harness_ready(service, players=("Tester", "TargetB"), info=info)
        e2._Handler.force_uuid = service.player_uuid("TargetB") or None
        binding = await service.attest_player_session("TargetB")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(
            runtime, binding, session_id="mc:e2b", target="Tester", user_id="TargetB"
        )
        starter = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="TargetB", session_id="mc:e2b", origin="user"
            )
        )
        action_a = await ga._wait_follow_accepted(
            proxy, label="Follow accepted", timeout=60, task_probe=lambda: runtime.get(task.task_id)
        )
        proxy.release_follow.set()
        await asyncio.wait_for(starter, timeout=30)
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        state_before = await service._client.status()  # noqa: SLF001
        assert state_before["action"]["status"] == "RUNNING"
        closed = await ga._observer_control(info, "close", "Tester")
        assert closed["state"] == "closed"

        async def _settled() -> bool:
            current = await runtime.get(task.task_id)
            if current is None:
                return False
            return current.steps[0].dispatch_state == "SETTLED"

        await ga._wait(_settled, "follow target lost terminal", timeout=30)
        record = await runtime.get(task.task_id)
        assert record is not None
        detail = record.recovery.get("detail") or {}
        assert detail.get("terminal_event") == "minecraft.action.failed"
        assert detail.get("terminal_status") == "FAILED"
        assert detail.get("terminal_reason") == "player.lost"
        assert detail.get("terminal_code") == "player.lost"
        assert detail.get("terminal_action_id") == action_a
        assert detail.get("scoped_stop_confirmed") is not True
        assert strict_stop_evidence_valid(detail) is False
        assert service.player_uuid("TargetB")
        status = await service._client.status()  # noqa: SLF001
        assert status["action"]["active_count"] == 0
        late = await service._client.stop(action_a)  # noqa: SLF001
        assert late.get("cancelled") == []
        final = await runtime.get(task.task_id)
        assert final is not None
        final_detail = final.recovery.get("detail") or {}
        assert final_detail.get("scoped_stop_confirmed") is not True
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2b-lost.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        old_detail = old.recovery.get("detail") or {}
        assert "player.lost" in json.dumps(old_detail, ensure_ascii=False)
        assert old_detail.get("scoped_stop_confirmed") is not True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()


async def test_real_node_move_to_completes_before_late_stop(tmp_path: Any) -> None:
    import tests.test_vnx_p0_attested_follow_e2 as e2
    import tests.test_vnx_p0_gate_a_real_http as ga
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.integrations.minecraft.session_attestation import SessionAttestationClient
    from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
    from app.tasks.runtime import strict_stop_evidence_valid
    from app.tasks.store import SqliteTaskStore

    hmac_server, hmac_url = e2._start()
    e2._Handler.force_uuid = None
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2b-move.db'}"))
    await db.connect()
    ga.service_database = db
    callback = proxy = harness = service = coordinator = info = None
    try:
        holder: dict[str, Any] = {}

        class _Receiver(ga.CallbackReceiver):
            async def _handle(self, request: Any) -> Any:
                await holder["service"].receive_event(await request.json())
                return ga.web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        proxy = ga.Proxy(runtime_port)
        await proxy.start()
        service = await ga._service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=e2.TOKEN, server_scope=scope)
        )
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        e2._Handler.force_uuid = service.player_uuid("Tester") or None
        binding = await service.attest_player_session("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        status_before = await service._client.status()  # noqa: SLF001
        position = dict(status_before.get("position") or {})
        assert {"x", "y", "z"} <= set(position)
        target_x = float(position["x"]) + 3.0
        target_y = float(position["y"])
        target_z = float(position["z"])
        task = await ga._move_task(
            runtime, binding, session_id="mc:move", x=target_x, y=target_y, z=target_z
        )
        await runtime.confirm_and_start(
            task.task_id, user_id="Tester", session_id="mc:move", origin="user"
        )

        async def _completed() -> bool:
            current = await runtime.get(task.task_id)
            if current is None:
                return False
            step = current.steps[0]
            return step.state == "SUCCEEDED" and step.dispatch_state == "SETTLED"

        await ga._wait(_completed, "move_to completed", timeout=60)
        record = await runtime.get(task.task_id)
        assert record is not None
        action_a = str(record.steps[0].action_id or "")
        assert action_a
        detail = record.recovery.get("detail") or {}
        assert detail.get("terminal_event") == "minecraft.action.completed"
        assert (
            detail.get("terminal_status") == "COMPLETED"
            or detail.get("terminal_status") == "SUCCEEDED"
        )
        assert detail.get("scoped_stop_confirmed") is not True
        assert strict_stop_evidence_valid(detail) is False
        status = await service._client.status()  # noqa: SLF001
        assert status["action"]["active_count"] == 0
        late = await service._client.stop(action_a)  # noqa: SLF001
        assert late.get("cancelled") == []
        final = await runtime.get(task.task_id)
        assert final is not None
        final_detail = final.recovery.get("detail") or {}
        assert final_detail.get("scoped_stop_confirmed") is not True
        assert final.steps[0].state == "SUCCEEDED"
        await coordinator.stop()
        coordinator = None
        await service.stop()
        service = None
        await db.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'c3-e2b-move.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        assert old.steps[0].state == "SUCCEEDED"
        old_detail = old.recovery.get("detail") or {}
        assert old_detail.get("terminal_event") == "minecraft.action.completed"
        assert old_detail.get("scoped_stop_confirmed") is not True
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await ga._stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int((info or {}).get("controlPort") or 0) or None,
                label="gate B harness shutdown",
            )
        if callback is not None:
            await callback.close()
        e2.SCOPE = "mc.test:25565"
        e2._Handler.force_uuid = None
        hmac_server.shutdown()
