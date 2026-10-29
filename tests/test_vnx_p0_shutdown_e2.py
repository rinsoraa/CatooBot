"""A2 real Node shutdown E2: running Follow is cancelled/UNKNOWN, stop is idempotent."""

from __future__ import annotations

from typing import Any

import pytest

import tests.test_vnx_p0_gate_a_real_http as ga
from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.integrations.minecraft.runtime_client import MinecraftRuntimeClient, MinecraftRuntimeError
from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator


def _receiver(holder: dict[str, Any]) -> ga.CallbackReceiver:
    class _Receiver(ga.CallbackReceiver):
        async def _handle(self, request: Any) -> Any:
            await holder["service"].receive_event(await request.json())
            return ga.web.json_response({"ok": True})

    return _Receiver(None)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_shutdown_with_running_follow_is_bounded_and_idempotent(tmp_path: Any) -> None:
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'shutdown.db'}"))
    await db.connect()
    ga.service_database = db
    callback = harness = service = coordinator = None
    holder: dict[str, Any] = {}
    try:
        callback = _receiver(holder)
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        service = await ga._service_for(runtime_port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        harness, info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        # A3-FU3 F4: the mirror-side "player visible" check is weaker than what Follow
        # actually needs - runtime.js rejects follow_player unless bot.players[name].entity
        # exists. Waiting only on the mirror let this test dispatch a Follow that the
        # runtime answered with player.not_found, so the task never reached WAITING_ACTION.
        # The runtime-side barrier also proves mirror ONLINE and controller ACTIVE; the
        # explicit session wait keeps the binding precondition obvious.
        await ga._wait_harness_ready(service, players=("Tester",), info=info)
        await ga._wait(lambda: ga._has_player(service, "Tester"), "Tester join")
        binding = service.player_session_binding("Tester")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:shutdown", target="Tester")
        await runtime.confirm_and_start(
            task.task_id, user_id="Tester", session_id="mc:shutdown", origin="user"
        )
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        await coordinator.begin_shutdown(timeout=3.0)
        fresh = await runtime.get(task.task_id)
        assert fresh is not None and fresh.state.terminal
        assert "shutdown" in str(fresh.recovery).lower() or fresh.pending_action_id
        await coordinator.stop()
        assert service.controller_ready() is False
        client_b = MinecraftRuntimeClient(
            f"http://127.0.0.1:{runtime_port}", token_getter=lambda: ga.BRIDGE_TOKEN
        )
        try:
            # S3/S8: during shutdown the owner still exists and pending action evidence
            # is retained; another controller must not be able to take over yet.
            try:
                await client_b.claim_controller("during-shutdown-B")
            except MinecraftRuntimeError as exc:
                assert exc.status == 409
            else:
                raise AssertionError("B must not claim while A still owns shutdown")
            await service.stop()
            assert service._client.owner_state in {"REVOKED", "UNKNOWN"}  # noqa: SLF001
            await service.stop()  # S6 idempotent
            claim_b = await client_b.claim_controller("after-shutdown-B")
            assert claim_b["state"] == "ACTIVE"
        finally:
            await client_b.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if harness is not None:
            ga._kill_tree(harness)
            import asyncio

            await asyncio.to_thread(harness.wait, 10)
        if callback is not None:
            await callback.close()
        await db.close()


@pytest.mark.asyncio
async def test_shutdown_with_runtime_unreachable_is_bounded_and_unknown(tmp_path: Any) -> None:
    import time as _time

    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'shutdown-down.db'}"))
    await db.connect()
    ga.service_database = db
    callback = harness = service = coordinator = None
    holder: dict[str, Any] = {}
    try:
        callback = _receiver(holder)
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        service = await ga._service_for(runtime_port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        harness, _info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait(lambda: ga._online(service), "service ONLINE")
        await ga._wait(lambda: ga._has_player(service, "Tester"), "Tester join")
        binding = service.player_session_binding("Tester")
        await ga._wait(lambda: ga._player_visible(service, "Tester"), "Tester visible")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        task = await ga._follow_task(runtime, binding, session_id="mc:down", target="Tester")
        await runtime.confirm_and_start(
            task.task_id, user_id="Tester", session_id="mc:down", origin="user"
        )
        await ga._wait(lambda: ga._task_state(runtime, task.task_id, "WAITING_ACTION"), "waiting")
        ga._kill_tree(harness)
        harness = None
        started = _time.monotonic()
        await coordinator.begin_shutdown(timeout=1.0)
        await service.stop()
        elapsed = _time.monotonic() - started
        fresh = await runtime.get(task.task_id)
        assert fresh is not None and fresh.state.terminal
        assert fresh.pending_action_id or "UNKNOWN" in str(fresh.recovery)
        assert elapsed < 20.0, "shutdown must be bounded when runtime is gone"
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if harness is not None:
            ga._kill_tree(harness)
            import asyncio

            await asyncio.to_thread(harness.wait, 10)
        if callback is not None:
            await callback.close()
        await db.close()


@pytest.mark.asyncio
async def test_normal_shutdown_without_inflight_actions(tmp_path: Any) -> None:
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'shutdown-idle.db'}"))
    await db.connect()
    ga.service_database = db
    callback = harness = service = None
    holder: dict[str, Any] = {}
    try:
        callback = _receiver(holder)
        await callback.start()
        runtime_port = ga._free_port()
        server_port = ga._free_port()
        service = await ga._service_for(runtime_port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        harness, _info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        await ga._wait(lambda: ga._online(service), "service ONLINE")
        runtime = await ga._runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        await coordinator.begin_shutdown(timeout=1.0)
        await coordinator.stop()
        await service.stop()
        await service.stop()
    finally:
        if service is not None:
            await service.stop()
        if harness is not None:
            ga._kill_tree(harness)
            import asyncio

            await asyncio.to_thread(harness.wait, 10)
        if callback is not None:
            await callback.close()
        await db.close()
