"""A1 real Node E2 through the Python RuntimeClient (two independent controllers)."""

from __future__ import annotations

import tests.test_vnx_p0_gate_a_real_http as ga
from app.integrations.minecraft.runtime_client import MinecraftRuntimeClient, MinecraftRuntimeError


async def test_two_python_controllers_are_fenced_by_real_node(tmp_path) -> None:
    callback = ga.CallbackReceiver(None)  # type: ignore[arg-type]
    await callback.start()
    runtime_port = ga._free_port()
    server_port = ga._free_port()
    harness = None
    try:
        harness, _info, _ = await ga._start_harness(callback.port, runtime_port, server_port)
        client_a = MinecraftRuntimeClient(
            f"http://127.0.0.1:{runtime_port}", token_getter=lambda: ga.BRIDGE_TOKEN
        )
        client_b = MinecraftRuntimeClient(
            f"http://127.0.0.1:{runtime_port}", token_getter=lambda: ga.BRIDGE_TOKEN
        )
        try:
            claim_a = await client_a.claim_controller("py-A")
            assert claim_a["owner_token"] and claim_a["owner_epoch"] >= 1
            try:
                await client_b.claim_controller("py-B")
            except MinecraftRuntimeError as exc:
                assert exc.code == "controller.owned" and exc.status == 409
            else:
                raise AssertionError("B claim must be rejected")
            await client_a.chat("A")
            try:
                await client_b.chat("B")
            except MinecraftRuntimeError as exc:
                assert exc.code == "controller.fenced" and exc.status == 403
            else:
                raise AssertionError("B write must be fenced")
            await client_a.release_controller()
            claim_b = await client_b.claim_controller("py-B")
            assert claim_b["owner_epoch"] > claim_a["owner_epoch"]
            try:
                await client_a.chat("stale")
            except MinecraftRuntimeError as exc:
                assert exc.code == "controller.fenced"
            else:
                raise AssertionError("stale A write must be fenced")
            # STOP and read-only remain available without ownership.
            stop = await client_b.stop()
            assert stop["ok"] is not False
            status = await client_b.status()
            assert status["status"] == "ONLINE"
        finally:
            await client_a.close()
            await client_b.close()
    finally:
        if harness is not None:
            ga._kill_tree(harness)
            import asyncio

            await asyncio.to_thread(harness.wait, 10)
        await callback.close()
