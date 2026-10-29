"""P1-F1 / A3-FU2: bounded, honest controller release during shutdown.

Fault injection is a *local* aiohttp fake runtime: no Node runtime, no Minecraft
server, no AI provider. Every scenario pins one of the four distinguishable
outcomes, because "the shutdown returned" is not the same fact as "the release
happened":

    RELEASED  -- the runtime explicitly confirmed the release
    REJECTED  -- the runtime explicitly refused (4xx business error)
    TIMEOUT   -- no response arrived inside the bounded budget
    UNKNOWN   -- the request may have been delivered; the outcome is unknowable

The invariant under test is not "shutdown is fast". It is "shutdown stays inside
its real time budget AND never fabricates a released receipt".
"""

from __future__ import annotations

import asyncio
import contextlib
import socket
import time
from typing import Any

import pytest
from aiohttp import web

from app.config.settings import MinecraftConfig
from app.integrations.minecraft import runtime_client as rc
from app.integrations.minecraft.runtime_client import MinecraftRuntimeClient, MinecraftRuntimeError
from app.integrations.minecraft.service import MinecraftService

#: Contract from the P0 shutdown tests: a real shutdown must stay well under this.
SHUTDOWN_CONTRACT_SECONDS = 20.0


def _dead_port() -> int:
    """A loopback port with nobody listening: connection refused, not silent."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _config(port: int, *, request_timeout: float = 5.0) -> MinecraftConfig:
    return MinecraftConfig(
        enabled=True,
        auto_start_runtime=False,
        runtime_port=port,
        request_timeout_seconds=request_timeout,
        external_bridge_token="p1f1-bridge-token",
        external_callback_url="http://127.0.0.1:9/minecraft/events",
        external_callback_token="p1f1-cb",
        perception_enabled=False,
    )


class FakeRuntime:
    """Minimal aiohttp fake runtime: controller ownership state machine + faults.

    Modes (transport behaviour of POST /minecraft/controller/release):

        normal                     normal reply
        all_silent                 same, and /minecraft/disconnect never answers either
        release_silent             accept the connection, never answer
        release_drop_after_read    read the request, then drop without answering
        release_partial_then_abort apply the release, then cut the response body
        release_rejected           explicit 4xx business refusal
        release_not_released       explicit 2xx with released=false
        release_error_500          explicit 5xx
    """

    def __init__(self) -> None:
        self.mode = "normal"
        self.state = "UNCLAIMED"
        self.controller_id = ""
        self.token = ""
        self.epoch = 0
        self.actions_active = 0
        self.claim_calls = 0
        self.release_calls = 0
        self.stop_calls = 0
        self.stop_gate: asyncio.Event | None = None
        self.stop_seen = asyncio.Event()
        self._silent: set[asyncio.Task[Any]] = set()
        self._runner: web.AppRunner | None = None
        self._sock: socket.socket | None = None
        self.port = 0

    # ------------------------------------------------------------- lifecycle

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/minecraft/controller/claim", self._claim)
        app.router.add_post("/minecraft/controller/release", self._release)
        app.router.add_post("/minecraft/stop", self._stop)
        app.router.add_post("/minecraft/disconnect", self._disconnect)
        app.router.add_get("/minecraft/health", self._health)
        app.router.add_get("/minecraft/status", self._status)
        self._sock = socket.socket()
        self._sock.bind(("127.0.0.1", 0))
        self._sock.listen(128)
        self._sock.setblocking(False)
        self.port = int(self._sock.getsockname()[1])
        self._runner = web.AppRunner(app, access_log=None, shutdown_timeout=0.2)
        await self._runner.setup()
        await web.SockSite(self._runner, self._sock).start()

    async def close(self) -> None:
        for task in list(self._silent):
            task.cancel()
        if self._silent:
            await asyncio.gather(*self._silent, return_exceptions=True)
            self._silent.clear()
        if self._stop_gate_open() is False and self.stop_gate is not None:
            self.stop_gate.set()
        if self._runner is not None:
            with contextlib.suppress(Exception):
                await self._runner.cleanup()
            self._runner = None
        if self._sock is not None:
            with contextlib.suppress(Exception):
                self._sock.close()
            self._sock = None

    def _stop_gate_open(self) -> bool:
        return bool(self.stop_gate is not None and self.stop_gate.is_set())

    # --------------------------------------------------------------- helpers

    def _view(self) -> dict[str, Any]:
        return {
            "ok": True,
            "state": self.state,
            "controller_id": self.controller_id,
            "owner_epoch": self.epoch,
            "owner_expires_at": time.time() + 60.0 if self.state == "ACTIVE" else 0.0,
        }

    @staticmethod
    def _error(status: int, code: str) -> web.Response:
        return web.json_response(
            {"ok": False, "error": {"code": code, "message": code}}, status=status
        )

    async def _never_respond(self) -> None:
        """Hold the connection open without answering (a silent peer)."""
        task = asyncio.current_task()
        if task is not None:
            self._silent.add(task)
        try:
            await asyncio.sleep(3600)
        finally:
            if task is not None:
                self._silent.discard(task)

    def _business_release(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """The real runtime's release semantics (runtime.js controllerRelease)."""
        if self.state != "ACTIVE":
            return 200, {**self._view(), "released": False}
        if (
            str(body.get("controller_id") or "") != self.controller_id
            or int(body.get("owner_epoch") or 0) != self.epoch
            or str(body.get("owner_token") or "") != self.token
        ):
            return 403, {"ok": False, "error": {"code": "controller.release_forbidden"}}
        if self.actions_active:
            return 409, {"ok": False, "error": {"code": "controller.actions_pending"}}
        self.state = "REVOKED"
        self.token = ""
        return 200, {**self._view(), "released": True}

    # -------------------------------------------------------------- handlers

    async def _claim(self, request: web.Request) -> web.Response:
        self.claim_calls += 1
        body = await request.json()
        controller_id = str(body.get("controller_id") or "")
        if self.state == "ACTIVE" and self.controller_id != controller_id:
            return self._error(409, "controller.owned")
        if self.state != "ACTIVE" and self.actions_active:
            return self._error(409, "controller.actions_pending")
        self.controller_id = controller_id
        self.epoch += 1
        self.token = f"tok-{self.epoch}"
        self.state = "ACTIVE"
        return web.json_response({**self._view(), "owner_token": self.token})

    async def _release(self, request: web.Request) -> web.Response:
        self.release_calls += 1
        body = await request.json()
        if self.mode in {"release_silent", "all_silent"}:
            await self._never_respond()
        if self.mode == "release_drop_after_read":
            request.transport.abort()
            await self._never_respond()
        if self.mode == "release_partial_then_abort":
            status, payload = self._business_release(body)
            assert status == 200 and payload["released"] is True
            response = web.StreamResponse(status=200, headers={"Content-Type": "application/json"})
            await response.prepare(request)
            await response.write(b'{"ok": true, "released": tr')
            request.transport.abort()
            await self._never_respond()
        if self.mode == "release_rejected":
            return self._error(403, "controller.release_forbidden")
        if self.mode == "release_not_released":
            return web.json_response({**self._view(), "released": False})
        if self.mode == "release_error_500":
            return self._error(500, "internal.error")
        status, payload = self._business_release(body)
        return web.json_response(payload, status=status)

    async def _stop(self, request: web.Request) -> web.Response:
        await request.json()
        self.stop_calls += 1
        self.stop_seen.set()
        if self.stop_gate is not None:
            await self.stop_gate.wait()
        return web.json_response({"ok": True, "status": "STOPPED"})

    async def _disconnect(self, request: web.Request) -> web.Response:
        await request.json()
        if self.mode == "all_silent":
            await self._never_respond()
        return web.json_response({"ok": True, "status": "DISCONNECTED"})

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "status": "ONLINE"})

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response({"ok": True, "status": "ONLINE", "runtime_instance": "fake"})


async def _claimed_service(port: int, *, request_timeout: float = 5.0) -> MinecraftService:
    """A service that really holds controller ownership (claimed over HTTP)."""
    service = MinecraftService(None, _config(port, request_timeout=request_timeout))
    await service._client.claim_controller(service._controller_id)
    assert service._client.owner_state == "ACTIVE"
    service._controller_state = "ACTIVE"
    service._started = True
    return service


async def _stopped_report(service: MinecraftService) -> tuple[dict[str, Any], float]:
    started = time.monotonic()
    await service.stop()
    return dict(service.controller_release), time.monotonic() - started


# ---------------------------------------------------------------- scenario 1


async def test_normal_release_records_a_real_receipt() -> None:
    runtime = FakeRuntime()
    await runtime.start()
    service = await _claimed_service(runtime.port)
    try:
        report, elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_RELEASED
        assert report["released"] is True
        assert service.controller_released() is True
        assert report["attempted"] is True
        assert report["code"] == ""
        assert runtime.release_calls == 1
        assert runtime.state == "REVOKED"
        assert service._client.release_confirmed is True
        assert service._client.owner_state == "REVOKED"
        assert service._client.owner_token == ""
        assert report["elapsed"] < 1.0, "a live runtime must not consume the budget"
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS
    finally:
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 2


async def test_connection_refused_is_definitely_not_released() -> None:
    # Ownership exists, then the whole runtime process disappears.
    service = MinecraftService(None, _config(_dead_port(), request_timeout=60.0))
    service._client.controller_id = "ctrl-refused"
    service._client.owner_token = "tok-refused"
    service._client.owner_state = "ACTIVE"
    service._controller_state = "ACTIVE"
    service._started = True
    report, elapsed = await _stopped_report(service)
    assert report["outcome"] == rc.RELEASE_NOT_DELIVERED
    assert report["released"] is False
    assert service.controller_released() is False
    assert report["code"] == "runtime.unreachable"
    # Uncertainty is preserved: state UNKNOWN, ownership token kept as evidence.
    assert service._client.owner_state == "UNKNOWN"
    assert service._client.owner_token == "tok-refused"
    assert elapsed < SHUTDOWN_CONTRACT_SECONDS
    assert report["elapsed"] < SHUTDOWN_CONTRACT_SECONDS


# ---------------------------------------------------------------- scenario 3


async def test_silent_runtime_release_is_bounded_and_unknown() -> None:
    """The A3-FU1 killer: port accepts connections (60s request budget) but never replies."""
    runtime = FakeRuntime()
    runtime.mode = "release_silent"
    await runtime.start()
    service = await _claimed_service(runtime.port, request_timeout=60.0)
    try:
        report, elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_TIMEOUT
        assert report["released"] is False
        assert service.controller_released() is False
        assert report["budget"] == MinecraftService.CONTROLLER_RELEASE_TIMEOUT_SECONDS
        assert report["elapsed"] >= 2.9, "must really wait the bounded budget"
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS, "silent runtime must not blow the contract"
        assert elapsed < 10.0, "shutdown budget is 3s disconnect + 3s release"
        # Never fabricate a receipt: the owner token stays as uncertainty evidence.
        assert service._client.owner_state == "UNKNOWN"
        assert service._client.owner_token, "uncertainty must not be erased into released"
        assert service._client.release_confirmed is False
        assert runtime.release_calls == 1
        assert runtime.state == "ACTIVE"
    finally:
        await service.stop()
        await runtime.close()


async def test_fully_silent_runtime_still_fits_the_shutdown_contract() -> None:
    """Both shutdown calls hang: 3s bounded disconnect + 3s bounded release."""
    runtime = FakeRuntime()
    runtime.mode = "all_silent"
    await runtime.start()
    service = await _claimed_service(runtime.port, request_timeout=60.0)
    try:
        report, elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_TIMEOUT
        assert report["released"] is False
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS, "two bounded calls must still fit 20s"
        assert elapsed < 8.0, "3s disconnect + 3s release, no unbounded tail"
        assert service.controller_released() is False
        assert service._client.owner_state == "UNKNOWN"
    finally:
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 4


@pytest.mark.parametrize("mode", ["release_drop_after_read", "release_partial_then_abort"])
async def test_delivered_but_lost_response_stays_unknown(mode: str) -> None:
    runtime = FakeRuntime()
    runtime.mode = mode
    await runtime.start()
    service = await _claimed_service(runtime.port, request_timeout=60.0)
    try:
        report, elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_UNKNOWN
        assert report["released"] is False
        assert service.controller_released() is False
        assert service._client.owner_state == "UNKNOWN"
        assert service._client.owner_token, "token must survive an ambiguous release"
        assert service._client.release_confirmed is False
        assert runtime.release_calls == 1
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS
        if mode == "release_partial_then_abort":
            # The runtime DID release us; the reply was cut on the way back.
            # We still may not claim success -- that would be a fabricated receipt.
            assert runtime.state == "REVOKED"
            assert report["released"] is False
        else:
            # Read-then-drop: the runtime never answered, so it must still own it.
            assert runtime.state == "ACTIVE"
    finally:
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 5b/5c


async def test_business_rejection_is_not_a_release() -> None:
    runtime = FakeRuntime()
    runtime.mode = "release_rejected"
    await runtime.start()
    service = await _claimed_service(runtime.port)
    try:
        report, elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_REJECTED
        assert report["code"] == "controller.release_forbidden"
        assert report["status"] == 403
        assert report["released"] is False
        assert service.controller_released() is False
        # The runtime says we are not its owner: local write authority is fenced.
        assert service._client.owner_state == "REVOKED"
        assert report["elapsed"] < 1.0
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS
        assert runtime.state == "ACTIVE", "the fake runtime still owns itself"
    finally:
        await service.stop()
        await runtime.close()


async def test_actions_pending_rejection_keeps_ownership_but_is_not_released() -> None:
    runtime = FakeRuntime()
    await runtime.start()
    service = await _claimed_service(runtime.port)
    runtime.actions_active = 1  # in-flight action blocks the release (real runtime rule)
    try:
        report, _elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_REJECTED
        assert report["code"] == "controller.actions_pending"
        assert report["released"] is False
        assert service.controller_released() is False
        # Still the owner: we must not mark ourselves fenced either.
        assert service._client.owner_state == "ACTIVE"
    finally:
        await service.stop()
        await runtime.close()


async def test_not_released_receipt_is_not_success() -> None:
    runtime = FakeRuntime()
    runtime.mode = "release_not_released"
    await runtime.start()
    service = await _claimed_service(runtime.port)
    try:
        runtime.state = "EXPIRED"  # another owner path: nothing left to release
        report, _elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_NOT_RELEASED
        assert report["released"] is False
        assert service.controller_released() is False
        assert runtime.release_calls == 1
    finally:
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 6


async def test_second_controller_cannot_take_over_before_release() -> None:
    runtime = FakeRuntime()
    await runtime.start()
    service = await _claimed_service(runtime.port)
    client_b = MinecraftRuntimeClient(
        f"http://127.0.0.1:{runtime.port}", token_getter=lambda: "p1f1-bridge-token"
    )
    ghost = MinecraftService(None, _config(runtime.port))
    try:
        epoch_a = runtime.epoch
        with pytest.raises(MinecraftRuntimeError) as claim_error:
            await client_b.claim_controller("controller-B")
        assert claim_error.value.status == 409
        assert claim_error.value.code == "controller.owned"
        assert runtime.controller_id == service._controller_id

        report, _elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_RELEASED
        assert runtime.release_calls == 1

        claim_b = await client_b.claim_controller("controller-B")
        assert claim_b["state"] == "ACTIVE"
        assert claim_b["owner_epoch"] > epoch_a

        # A service that never held ownership must not release someone else's.
        ghost_report, _ = await _stopped_report(ghost)
        assert ghost_report["outcome"] == rc.RELEASE_SKIPPED
        assert ghost_report["attempted"] is False
        assert ghost_report["released"] is False
        assert runtime.release_calls == 1, "ghost must not send a release request"
        assert runtime.controller_id == "controller-B"
        assert runtime.state == "ACTIVE"
    finally:
        await client_b.close()
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 5


async def test_concurrent_bridge_stop_and_shutdown_converge_once() -> None:
    runtime = FakeRuntime()
    runtime.stop_gate = asyncio.Event()
    await runtime.start()
    service = await _claimed_service(runtime.port)
    stop_task: asyncio.Task[Any] | None = None
    try:
        service.begin_shutdown()
        stop_task = asyncio.create_task(service._client.stop())
        await asyncio.wait_for(runtime.stop_seen.wait(), 5.0)

        started = time.monotonic()
        shutdown_task = asyncio.create_task(service.stop())
        await asyncio.sleep(0.05)  # shutdown is racing the in-flight STOP
        runtime.stop_gate.set()
        stop_result, shutdown_result = await asyncio.wait_for(
            asyncio.gather(stop_task, shutdown_task, return_exceptions=True), 20.0
        )
        elapsed = time.monotonic() - started

        assert shutdown_result is None, "shutdown must not raise"
        # The transport teardown may cut the racing STOP; it must never hang or
        # corrupt the controller state, and both must converge to one release.
        if isinstance(stop_result, BaseException):
            assert isinstance(stop_result, (MinecraftRuntimeError, asyncio.CancelledError))
        assert runtime.stop_calls == 1, "the STOP really reached the runtime"
        assert runtime.release_calls == 1, "exactly one release attempt"
        assert runtime.state == "REVOKED"
        assert service.controller_release["outcome"] == rc.RELEASE_RELEASED
        assert service.controller_released() is True
        assert elapsed < SHUTDOWN_CONTRACT_SECONDS

        # Idempotent repeat after the race: same result, no second release.
        again = await service.stop()
        assert again is None
        assert runtime.release_calls == 1
        assert service.controller_release["outcome"] == rc.RELEASE_RELEASED
    finally:
        if runtime.stop_gate is not None:
            runtime.stop_gate.set()
        if stop_task is not None and not stop_task.done():
            stop_task.cancel()
            with contextlib.suppress(BaseException):
                await stop_task
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 8


async def test_repeated_stop_is_idempotent_and_does_not_re_release() -> None:
    runtime = FakeRuntime()
    await runtime.start()
    service = await _claimed_service(runtime.port)
    try:
        first, _elapsed = await _stopped_report(service)
        for _ in range(2):
            started = time.monotonic()
            await service.stop()
            assert time.monotonic() - started < 0.5, "repeat stop must be free"
            service.begin_shutdown()
        assert runtime.release_calls == 1
        assert service.controller_release == first
        assert runtime.state == "REVOKED"
    finally:
        await service.stop()
        await runtime.close()


# ---------------------------------------------------------------- scenario 7


async def test_unknown_release_fails_closed_for_downstream() -> None:
    runtime = FakeRuntime()
    runtime.mode = "release_silent"
    await runtime.start()
    service = await _claimed_service(runtime.port, request_timeout=60.0)
    try:
        report, _elapsed = await _stopped_report(service)
        assert report["outcome"] == rc.RELEASE_TIMEOUT
        assert report["released"] is False
        assert report["owner_state"] == "UNKNOWN"
        # Even if the shutdown freeze were lifted, UNKNOWN must stay fail-closed:
        # downstream may not read "no receipt" as "released".
        service._shutting_down = False
        assert service.controller_ready() is False
        assert service._client.controller_ready() is False
        assert service.controller_released() is False
    finally:
        await service.stop()
        await runtime.close()


# ------------------------------------------------------- budget / no patching


def test_release_budget_is_independent_of_the_request_budget() -> None:
    service = MinecraftService(None, _config(25580, request_timeout=60.0))
    assert service._release_budget() == 3.0
    service = MinecraftService(None, _config(25580, request_timeout=15.0))
    assert service._release_budget() == 3.0
    service = MinecraftService(None, _config(25580, request_timeout=1.0))
    assert service._release_budget() == 1.0
    service = MinecraftService(None, _config(25580, request_timeout=0.01))
    assert service._release_budget() == 0.1


async def test_bounded_release_never_patches_the_global_event_loop() -> None:
    runtime = FakeRuntime()
    runtime.mode = "release_silent"
    await runtime.start()
    service = await _claimed_service(runtime.port, request_timeout=60.0)
    wait_for_before = asyncio.wait_for
    sleep_before = asyncio.sleep
    try:
        await service.stop()
        # A3-FU1 deleted the process-wide asyncio.wait_for patch; it must not return.
        assert asyncio.wait_for is wait_for_before
        assert asyncio.sleep is sleep_before
    finally:
        await service.stop()
        await runtime.close()
