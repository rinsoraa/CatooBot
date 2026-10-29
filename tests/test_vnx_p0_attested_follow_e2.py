"""VNX-1 P0 AS7-ATTESTED-MVP: attested Follow E2 (Python HMAC server)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.service import MinecraftService
from app.integrations.minecraft.session_attestation import (
    AttestationError,
    SessionAttestationClient,
)
from app.integrations.minecraft.task_adapter import MinecraftTaskInvoker
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_vnx_p0_session_authorization_e2 import (
    SESSION,
    HybridService,
    _create,
    _identity,
    _runtime,
)

TOKEN = "mvp-token"


class ControlledClient:
    """Wraps the real attestation client with per-call barriers for race tests."""

    def __init__(self, inner: SessionAttestationClient) -> None:
        self.inner = inner
        self.gates: list[asyncio.Event] = []
        self.paused: set[int] = set()

    async def fetch_async(self, *, name: str, player_uuid: str) -> Any:
        index = len(self.gates)
        gate = asyncio.Event()
        self.gates.append(gate)
        if index in self.paused:
            await gate.wait()
        return await self.inner.fetch_async(name=name, player_uuid=player_uuid)


SCOPE = "mc.test:25565"


class _Handler(BaseHTTPRequestHandler):
    ref = "server-ref-1"
    boot = "boot-1"
    force_uuid: str | None = None
    calls: list[tuple[str, str]] = []
    allowed_names: set[str] | None = None
    players = {
        "Bob": ("uuid-B", "server-ref-2"),
    }

    def do_GET(self) -> None:  # noqa: N802
        q = parse_qs(urlparse(self.path).query)
        nonce = (q.get("nonce") or [""])[0]
        name = (q.get("name") or [""])[0]
        self.calls.append((name, nonce))
        if self.allowed_names is not None and name not in self.allowed_names:
            self.send_response(404)
            self.end_headers()
            return
        sig = hmac.new(TOKEN.encode(), f"{name}\n{nonce}".encode(), hashlib.sha256).hexdigest()
        if self.headers.get("X-Signature") != sig:
            self.send_response(401)
            self.end_headers()
            return
        uuid, ref = self.players.get(name, (self.force_uuid or "uuid-A", self.ref))
        body = json.dumps(
            {
                "ok": True,
                "schema": 1,
                "nonce": nonce,
                "session_ref": ref,
                "session_seq": 1,
                "player_uuid": uuid,
                "server_scope": SCOPE,
                "boot_id": self.boot,
            }
        ).encode()
        self.send_response(200)
        self.send_header("X-Signature", hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest())
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


def _start() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_port}"


async def _event(
    service: MinecraftService, name: str, *, session: str = "runtime-1", ts: float = 1.0
) -> None:
    await service.receive_event(
        {
            "event": name,
            "session_id": session,
            "timestamp": ts,
            "username": "Alice",
            "uuid": "uuid-A",
        }
    )


def _wire(identity: MinecraftService) -> tuple[Any, HybridService, FakeMinecraftService]:
    action = FakeMinecraftService()
    action.config.agent.trusted_players = ["Alice"]
    hybrid = HybridService(identity, action)
    bridge = MinecraftAgentBridge(hybrid)
    runtime = _runtime()
    bridge.set_task_authorizer(runtime.authorize_step)
    runtime._invoke = MinecraftTaskInvoker(bridge, task_reader=runtime.get)  # noqa: SLF001
    return runtime, hybrid, action


async def test_attested_legal_follow_full_chain() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        assert binding["identity_assurance"] == "server_attested"
        assert identity.current_attested_binding("Alice")
        runtime, _, action = _wire(identity)
        record = await _create(runtime, binding)
        assert action.action_calls("follow_player") == []
        await runtime.confirm_and_start(
            record.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_unbound_mc_player_is_identified_but_still_needs_authorization() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        assert binding["player_uuid"] and binding["server_scope"] == SCOPE
        assert "qq" not in str(binding).lower()
        runtime, _, action = _wire(identity)
        record = await _create(runtime, binding)
        assert action.action_calls("follow_player") == []
        await runtime.confirm_and_start(
            record.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_reconnect_invalidates_old_task() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        old_binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        old_task = await _create(runtime, old_binding)
        await _event(identity, "minecraft.player_left", ts=2.0)
        _Handler.ref = "server-ref-2"
        await _event(identity, "minecraft.player_joined", ts=3.0)
        new_binding = await identity.attest_player_session("Alice")
        assert new_binding["server_session_ref"] != old_binding["server_session_ref"]
        out = await runtime.confirm_and_start(
            old_task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
        new_task = await _create(runtime, new_binding, session_id="minecraft:mc.test:25565:Alice:2")
        await runtime.confirm_and_start(
            new_task.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_continuous_online_over_300_seconds() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        now = [1.0]
        identity._player_session_clock = lambda: now[0]  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        # Part-way through the TTL, re-attest the same server session.
        now[0] += 200.0
        refreshed = await identity.attest_player_session("Alice")
        assert refreshed["server_session_ref"] == binding["server_session_ref"]
        assert refreshed["actor_session_ref"] == binding["actor_session_ref"]
        # Total elapsed > 300s, but the binding stays valid after re-attestation.
        now[0] += 150.0
        assert identity.player_session_is_fresh("Alice") is True
        record = await _create(runtime, binding)
        await runtime.confirm_and_start(
            record.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_expiry_without_reattest() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        now = [1.0]
        identity._player_session_clock = lambda: now[0]  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        await identity.attest_player_session("Alice")
        now[0] += identity.session_max_age_seconds + 1
        assert identity.player_session_is_fresh("Alice") is False
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_attested_runtime_down_rejoin_requires_new_proof() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        record = await _create(runtime, binding)
        await runtime.confirm_and_start(
            record.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
        # Settle the first action so the new session is fenced only by identity, not by
        # a stale outstanding dispatch.
        old = await runtime.get(record.task_id)
        assert old is not None
        await runtime.on_action_event(
            action_id=old.steps[0].action_id,
            event="minecraft.action.cancelled",
            status="CANCELLED",
            detail={"reason": "stop"},
            runtime_instance=old.steps[0].dispatch_runtime_instance,
        )
        identity._mark_runtime_down("test")  # noqa: SLF001
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-2",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
        assert identity.player_session_binding("Alice") == {}
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_is_fresh("Alice") is False
        # New task must not inherit the old attestation before re-verification.
        no_binding = identity.player_session_binding("Alice")
        assert no_binding == {}
        assert len(action.action_calls("follow_player")) == 1
        # Re-attest and a new task becomes legal.
        new_binding = await identity.attest_player_session("Alice")
        new_task = await _create(runtime, new_binding, session_id="minecraft:mc.test:25565:Alice:2")
        await runtime.confirm_and_start(
            new_task.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 2
    finally:
        server.shutdown()


async def test_event_only_task_cannot_inherit_attested_upgrade() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        # EVENT_ONLY: plain join binding creates Task A.
        await _event(identity, "minecraft.player_joined")
        event_binding = identity.player_session_binding("Alice")
        assert event_binding["identity_assurance"].startswith("server_observed")
        runtime, _, action = _wire(identity)
        task_a = await _create(runtime, event_binding)
        # Upgrade to SERVER_ATTESTED without attesting: Task A must lose access.
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        assert identity.player_session_binding("Alice") == {}
        out = await runtime.confirm_and_start(
            task_a.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
        # Mode switch cleared the local session; a new join + attestation is required
        # before any new task can be created.
        await _event(identity, "minecraft.player_joined", ts=2.0)
        binding = await identity.attest_player_session("Alice")
        assert binding["identity_assurance"] == "server_attested"
        # New Task B is legal under the new attested binding.
        task_b = await _create(runtime, binding, session_id="minecraft:mc.test:25565:Alice:2")
        await runtime.confirm_and_start(
            task_b.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_task_cannot_inherit_event_only_downgrade() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task_a = await _create(runtime, binding)
        # Downgrade to EVENT_ONLY: attested state must be revoked, not silently kept.
        identity.configure_attestation(None, required=False)
        assert identity._attested == {}  # noqa: SLF001
        assert identity.player_session_binding("Alice") == {}
        out = await runtime.confirm_and_start(
            task_a.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
    finally:
        server.shutdown()


async def test_runtime_instance_change_revokes_attestation() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        # Simulate runtime instance change via a full disconnect + new session join.
        await identity.receive_event(
            {
                "event": "minecraft.disconnected",
                "session_id": "runtime-1",
                "timestamp": 2.0,
                "reason": "restart",
            }
        )
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_ref("Alice") == ""
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-2",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
        # Runtime ONLINE again does not revive the old task or attestation.
        assert identity.current_attested_binding("Alice") == {}
        out = await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
    finally:
        server.shutdown()


async def test_old_spawned_cannot_restore_attested_authority() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        identity._mark_runtime_down("test")  # noqa: SLF001
        # Runtime 2 becomes current.
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-2",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        # Late old-runtime spawned must not flip runtime_down or restore attestation.
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-1",
                "timestamp": 3.0,
                "username": "Catodayo",
            }
        )
        assert identity.current_attested_binding("Alice") == {}
        out = await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
    finally:
        server.shutdown()


async def test_attestation_scope_must_match_current_minecraft_server() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        # Client is configured for a different trusted scope than the current game server.
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope="other.host:25565")
        )
        await _event(identity, "minecraft.player_joined")
        # The attestation itself succeeds for its own scope, but must not be usable
        # for the current mc.test connection.
        try:
            await identity.attest_player_session("Alice")
            raise AssertionError("scope mismatch must be rejected")
        except RuntimeError as exc:
            assert "scope mismatch" in str(exc)
        assert identity.player_session_binding("Alice") == {}
        assert identity.player_session_is_fresh("Alice") is False
    finally:
        server.shutdown()


async def _race_identity(url: str) -> tuple[Any, ControlledClient]:
    identity = _identity()
    controlled = ControlledClient(
        SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
    )
    identity.configure_attestation(controlled)
    await _event(identity, "minecraft.player_joined")
    return identity, controlled


async def test_race02_player_left_during_query() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        task = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        await _event(identity, "minecraft.player_left", ts=2.0)
        controlled.gates[0].set()
        try:
            await task
            raise AssertionError("stale result must be dropped")
        except RuntimeError as exc:
            assert "superseded" in str(exc) or "ambiguous" in str(exc)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_race03_same_uuid_rejoin_during_query() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        task = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        await _event(identity, "minecraft.player_left", ts=2.0)
        await _event(identity, "minecraft.player_joined", ts=3.0)
        controlled.gates[0].set()
        try:
            await task
            raise AssertionError("stale result must be dropped")
        except RuntimeError as exc:
            assert "superseded" in str(exc) or "ambiguous" in str(exc)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_race04_runtime_session_changes_during_query() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        task = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        identity._mirror["session_id"] = "runtime-2"  # noqa: SLF001
        controlled.gates[0].set()
        try:
            await task
            raise AssertionError("stale result must be dropped")
        except RuntimeError as exc:
            assert "superseded" in str(exc)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_race05_cancelled_query_late_return_does_not_update() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        task = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        # The late thread result must never write attested state.
        controlled.gates[0].set()
        await asyncio.sleep(0.05)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_race06_out_of_order_queries() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        first = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        second = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        # The second query starts later, so the first result is stale by generation.
        controlled.gates[0].set()
        await asyncio.sleep(0.05)
        binding = await second
        assert (
            identity.current_attested_binding("Alice")["server_session_ref"]
            == binding["server_session_ref"]
        )
        try:
            await first
            raise AssertionError("older result must be dropped")
        except RuntimeError as exc:
            assert "superseded" in str(exc)
    finally:
        server.shutdown()


async def test_race07_two_players_do_not_cross_bindings() -> None:
    server, url = _start()
    _Handler.force_uuid = None
    try:
        identity, _controlled = await _race_identity(url)
        await identity.receive_event(
            {
                "event": "minecraft.player_joined",
                "session_id": "runtime-1",
                "timestamp": 2.0,
                "username": "Bob",
                "uuid": "uuid-B",
            }
        )
        # Both players hold their own valid signed sessions.
        bob = await identity.attest_player_session("Bob")
        alice = await identity.attest_player_session("Alice")
        assert bob["player_uuid"] == "uuid-B"
        assert alice["player_uuid"] == "uuid-A"
        assert bob["server_session_ref"] != alice["server_session_ref"]
        assert identity.current_attested_binding("Alice")["player_uuid"] == "uuid-A"
        assert identity.current_attested_binding("Bob")["player_uuid"] == "uuid-B"
        # A validly signed response carrying another player's UUID is rejected.
        identity.invalidate_attested_session("Alice", "test")
        _Handler.force_uuid = "uuid-B"
        with pytest.raises(AttestationError, match="uuid"):
            await identity.attest_player_session("Alice")
        assert identity.current_attested_binding("Alice") == {}
        assert identity.current_attested_binding("Bob")["player_uuid"] == "uuid-B"
    finally:
        _Handler.force_uuid = None
        server.shutdown()


async def test_race08_boot_change_during_query() -> None:
    server, url = _start()
    try:
        identity, controlled = await _race_identity(url)
        controlled.paused.add(0)
        task = asyncio.create_task(identity.attest_player_session("Alice"))
        await asyncio.sleep(0)
        # Simulate server boot change by clearing the binding mid-query.
        identity.invalidate_attested_session("Alice", "boot_change")
        controlled.gates[0].set()
        try:
            await task
            raise AssertionError("stale result must be dropped")
        except RuntimeError as exc:
            assert "superseded" in str(exc)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_attested_chat_capture_full_chain() -> None:
    from app.integrations.minecraft.chat_bridge import MinecraftChatBridge
    from tests.test_minecraft_chat_bridge import FakeBot

    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        bot = FakeBot(None, None)
        bot.minecraft = identity
        chat = MinecraftChatBridge(bot, identity)  # type: ignore[arg-type]
        # Before verification the production admission path must fail closed.
        assert chat._capture_actor_session("Alice") is None
        binding = await identity.attest_player_session("Alice")
        captured = chat._capture_actor_session("Alice")
        assert captured is not None
        assert captured["actor_session_ref"] == binding["actor_session_ref"]
        assert captured["player_uuid"] == binding["player_uuid"]
        assert captured["server_scope"] == binding["server_scope"]
        assert captured["identity_assurance"] == "server_attested"
        runtime, _, action = _wire(identity)
        record = await _create(runtime, captured)
        assert action.action_calls("follow_player") == []
        await runtime.confirm_and_start(
            record.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
        # Runtime down: old task cannot dispatch and admission fails again.
        identity._mark_runtime_down("test")  # noqa: SLF001
        task2 = await _create(runtime, captured, session_id="minecraft:mc.test:25565:Alice:2")
        out = await runtime.confirm_and_start(
            task2.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert out.failure == "AUTHORIZATION"
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_verified_runtime_instance_change_revokes_attestation() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity._note_runtime_instance({"runtime_instance": "A"})  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        # Trusted status response reports a NEW runtime instance; no disconnected event.
        identity._note_runtime_instance({"runtime_instance": "B"})  # noqa: SLF001
        assert identity.runtime_instance() == "B"
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_ref("Alice") == ""
        assert identity.player_session_is_fresh("Alice") is False
        out = await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
        # A new connection + proof under instance B works; the old task stays dead.
        await identity.receive_event(
            {
                "event": "minecraft.disconnected",
                "session_id": "runtime-1",
                "timestamp": 2.0,
                "reason": "restart",
            }
        )
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-1",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", ts=3.0)
        new_binding = await identity.attest_player_session("Alice")
        new_task = await _create(runtime, new_binding, session_id="minecraft:mc.test:25565:Alice:2")
        await runtime.confirm_and_start(
            new_task.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_late_old_spawned_does_not_revert_current_runtime_session() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined", ts=1.0)
        old_binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        old_task = await _create(runtime, old_binding)
        await identity.receive_event(
            {
                "event": "minecraft.disconnected",
                "session_id": "runtime-1",
                "timestamp": 2.0,
                "reason": "closed",
            }
        )
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-2",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
        new_binding = await identity.attest_player_session("Alice")
        new_task = await _create(runtime, new_binding, session_id="minecraft:mc.test:25565:Alice:2")
        # Late spawned from the closed runtime must not revert the mirror or identity.
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-1",
                "timestamp": 4.0,
                "username": "Catodayo",
            }
        )
        assert identity._mirror["session_id"] == "runtime-2"  # noqa: SLF001
        assert (
            identity.current_attested_binding("Alice")["server_session_ref"]
            == new_binding["server_session_ref"]
        )
        out = await runtime.confirm_and_start(
            old_task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
        await runtime.confirm_and_start(
            new_task.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_cache_presence_is_not_sufficient_for_authorization() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        assert identity._attested  # noqa: SLF001 - cache is present...
        identity._attested["Alice"]["actor_session_ref"] = "ps_forged"  # noqa: SLF001
        # ...but a forged cached ref must not authorize anything.
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_ref("Alice") == ""
        assert identity.player_session_is_fresh("Alice") is False
        out = await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
    finally:
        server.shutdown()


async def test_attested_binding_rejects_local_ref_or_runtime_mismatch() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        await identity.attest_player_session("Alice")
        identity._attested["Alice"]["runtime_session_id"] = "runtime-other"  # noqa: SLF001
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_ref("Alice") == ""
        assert identity.player_session_is_fresh("Alice") is False
    finally:
        server.shutdown()


async def test_late_old_connection_events_do_not_touch_current_runtime() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined", ts=1.0)
        await identity.receive_event(
            {
                "event": "minecraft.disconnected",
                "session_id": "runtime-1",
                "timestamp": 2.0,
                "reason": "closed",
            }
        )
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "runtime-2",
                "timestamp": 2.5,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
        binding = await identity.attest_player_session("Alice")
        for name, extra in (
            ("minecraft.connected", {"username": "Catodayo"}),
            ("minecraft.kicked", {"reason": "old"}),
            ("minecraft.disconnected", {"reason": "old"}),
            ("minecraft.player_joined", {"username": "Alice", "uuid": "uuid-A"}),
        ):
            await identity.receive_event(
                {"event": name, "session_id": "runtime-1", "timestamp": 4.0, **extra}
            )
        await identity.receive_event(
            {
                "event": "minecraft.player_left",
                "session_id": "runtime-1",
                "timestamp": 4.5,
                "username": "Alice",
                "uuid": "uuid-A",
            }
        )
        assert identity._mirror["session_id"] == "runtime-2"  # noqa: SLF001
        assert identity._mirror["status"] == "ONLINE"  # noqa: SLF001
        assert (
            identity.current_attested_binding("Alice")["server_session_ref"]
            == binding["server_session_ref"]
        )
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


class _StatusClient:
    async def status(self) -> dict[str, Any]:
        return {
            "ok": True,
            "runtime_instance": "B",
            "session_id": "session-B",
            "status": "ONLINE",
            "host": "mc.test",
            "port": 25565,
        }


async def test_runtime_instance_b_new_session_recovers_without_old_disconnect() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity._note_runtime_instance({"runtime_instance": "A"})  # noqa: SLF001
        identity._mirror["session_id"] = "session-A"  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined", session="session-A", ts=1.0)
        old_binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        old_task = await _create(runtime, old_binding)
        # Trusted status/health response shows instance B; no disconnected for A.
        identity._client = _StatusClient()  # type: ignore[assignment]
        await identity.status()
        assert identity.runtime_instance() == "B"
        assert identity._mirror["session_id"] == "session-B"  # noqa: SLF001
        assert identity.current_attested_binding("Alice") == {}
        # B's first spawned must be accepted even though A never disconnected.
        await identity.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "session-B",
                "timestamp": 2.0,
                "username": "Catodayo",
            }
        )
        await _event(identity, "minecraft.player_joined", session="session-B", ts=3.0)
        new_binding = await identity.attest_player_session("Alice")
        assert new_binding["runtime_session_id"] == "session-B"
        assert new_binding["actor_session_ref"] != old_binding["actor_session_ref"]
        out = await runtime.confirm_and_start(
            old_task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert out.failure == "AUTHORIZATION"
        assert action.action_calls("follow_player") == []
        new_task = await _create(runtime, new_binding, session_id="minecraft:mc.test:25565:Alice:2")
        await runtime.confirm_and_start(
            new_task.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attested_auto_refresh_keeps_online_player_authorized() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        now = [1.0]
        identity._player_session_clock = lambda: now[0]  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE),
            refresh_interval_seconds=10.0,
            proof_max_age_seconds=300.0,
        )
        await _event(identity, "minecraft.player_joined")
        first = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        # Simulate two refresh periods totaling >300s with the same server session.
        now[0] += 200.0
        await identity._refresh_current_attested_players()  # noqa: SLF001
        assert (
            identity.current_attested_binding("Alice")["actor_session_ref"]
            == first["actor_session_ref"]
        )
        now[0] += 200.0
        await identity._refresh_current_attested_players()  # noqa: SLF001
        assert identity.player_session_is_fresh("Alice") is True
        task = await _create(runtime, first)
        await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
        # A changed server session is a definitive denial, not a refreshable proof.
        _Handler.ref = "server-ref-2"
        try:
            await identity._refresh_current_attested_players()  # noqa: SLF001
        except RuntimeError:
            pass
        assert identity.current_attested_binding("Alice") == {}
        stale = await _create(runtime, first, session_id="minecraft:mc.test:25565:Alice:2")
        out = await runtime.confirm_and_start(
            stale.task_id,
            user_id="Alice",
            session_id="minecraft:mc.test:25565:Alice:2",
            origin="user",
        )
        assert out.failure == "AUTHORIZATION"
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_attestation_refresh_scheduler_start_stop() -> None:
    server, url = _start()
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE),
            refresh_interval_seconds=5.0,
        )
        await _event(identity, "minecraft.player_joined")
        identity._start_attestation_refresh()  # noqa: SLF001
        task = identity._attest_refresh_task  # noqa: SLF001
        assert task is not None and not task.done()
        identity._start_attestation_refresh()  # noqa: SLF001 - idempotent
        assert identity._attest_refresh_task is task  # noqa: SLF001
        identity._stop_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0)
        assert task.cancelled() or task.done()
    finally:
        server.shutdown()


async def test_refresh_loop_schedules_multiple_real_proofs() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    _Handler.calls.clear()
    try:
        identity = _identity()
        now = [1.0]
        identity._player_session_clock = lambda: now[0]  # noqa: SLF001
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE),
            refresh_interval_seconds=0.05,
            proof_max_age_seconds=300.0,
        )
        await _event(identity, "minecraft.player_joined")
        first = await identity.attest_player_session("Alice")
        now[0] += identity.session_max_age_seconds + 10.0
        identity._start_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0.25)
        identity._stop_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0.02)
        alice_calls = [call for call in _Handler.calls if call[0] == "Alice"]
        assert len(alice_calls) >= 3, _Handler.calls
        assert len({nonce for _, nonce in alice_calls}) == len(alice_calls)
        assert identity.player_session_is_fresh("Alice") is True
        assert (
            identity.current_attested_binding("Alice")["actor_session_ref"]
            == first["actor_session_ref"]
        )
        runtime, _, action = _wire(identity)
        task = await _create(runtime, first)
        await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_auto_refresh_uses_authoritative_player_name_case() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    _Handler.calls.clear()
    _Handler.allowed_names = {"Alice"}
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE),
            refresh_interval_seconds=0.05,
        )
        await identity.receive_event(
            {
                "event": "minecraft.player_joined",
                "session_id": "runtime-1",
                "timestamp": 1.0,
                "username": "Alice",
                "uuid": "uuid-A",
            }
        )
        await identity.attest_player_session("Alice")
        # Simulate Java's exact-name lookup: lowercase must not match.
        _Handler.force_uuid = None
        identity._player_session_names[next(iter(identity._session_keys_for("Alice")))] = "Alice"  # noqa: SLF001
        identity._start_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0.15)
        identity._stop_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0.02)
        names = [name for name, _ in _Handler.calls]
        assert names and all(name == "Alice" for name in names), names
        assert identity.current_attested_binding("Alice") != {}
    finally:
        _Handler.allowed_names = None
        server.shutdown()


async def test_running_follow_revoked_after_attested_session_change() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1

        async def _cancel_revoked(snapshot: dict[str, Any]) -> None:
            username = str(snapshot["username"])
            record = await runtime.active_for_user(username, limit=20)
            while record is not None and record.actor_session_ref == snapshot["actor_session_ref"]:
                await runtime.cancel(record.task_id, reason="attested_session_invalidated")
                record = await runtime.active_for_user(username, limit=20)

        identity.on_attested_identity_revoked = lambda snapshot: asyncio.create_task(
            _cancel_revoked(snapshot)
        )
        identity._revoke_all_identity("test")  # noqa: SLF001
        await asyncio.sleep(0.15)
        record = await runtime.get(task.task_id)
        assert record is not None and record.state.value == "CANCELLED"
        # A new session cannot be terminated by the old revocation path.
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_service_stop_cancels_attestation_refresh_task() -> None:
    server, url = _start()
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE),
            refresh_interval_seconds=5.0,
        )
        await _event(identity, "minecraft.player_joined")
        await identity.attest_player_session("Alice")
        identity._start_attestation_refresh()  # noqa: SLF001
        task = identity._attest_refresh_task  # noqa: SLF001
        assert task is not None and not task.done()
        await identity.stop()
        assert task.done() or task.cancelled()
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_cleanup_during_attestation_does_not_commit_late_proof() -> None:
    server, url = _start()
    try:
        identity = _identity()
        controlled = ControlledClient(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        identity.configure_attestation(controlled, refresh_interval_seconds=0.05)
        await _event(identity, "minecraft.player_joined")
        controlled.paused.add(0)
        identity._start_attestation_refresh()  # noqa: SLF001
        await asyncio.sleep(0.1)
        await identity.stop()
        controlled.gates[0].set()
        await asyncio.sleep(0.05)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_first_join_auto_attests_without_manual_call() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.auto_attest_on_join = True
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        for _ in range(50):
            if identity.current_attested_binding("Alice"):
                break
            await asyncio.sleep(0.02)
        assert identity.current_attested_binding("Alice")
        assert identity.player_session_binding("Alice")["identity_assurance"] == "server_attested"
    finally:
        server.shutdown()


async def test_first_join_pending_proof_denies_write() -> None:
    server, url = _start()
    try:
        identity = _identity()
        controlled = ControlledClient(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        identity.auto_attest_on_join = True
        identity.configure_attestation(controlled)
        controlled.paused.add(0)
        await _event(identity, "minecraft.player_joined")
        await asyncio.sleep(0.02)
        assert identity.player_session_binding("Alice") == {}
        controlled.gates[0].set()
        for _ in range(50):
            if identity.current_attested_binding("Alice"):
                break
            await asyncio.sleep(0.02)
        assert identity.current_attested_binding("Alice")
    finally:
        server.shutdown()


async def test_first_join_invalid_proof_fails_closed() -> None:
    server, url = _start()
    _Handler.force_uuid = "uuid-B"
    try:
        identity = _identity()
        identity.auto_attest_on_join = True
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        await asyncio.sleep(0.15)
        assert identity.current_attested_binding("Alice") == {}
        assert identity.player_session_binding("Alice") == {}
    finally:
        _Handler.force_uuid = None
        server.shutdown()


async def test_join_leave_during_bootstrap_discards_late_result() -> None:
    server, url = _start()
    try:
        identity = _identity()
        controlled = ControlledClient(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        identity.auto_attest_on_join = True
        identity.configure_attestation(controlled)
        controlled.paused.add(0)
        await _event(identity, "minecraft.player_joined")
        await asyncio.sleep(0.02)
        await _event(identity, "minecraft.player_left", ts=2.0)
        controlled.gates[0].set()
        await asyncio.sleep(0.1)
        assert identity.current_attested_binding("Alice") == {}
    finally:
        server.shutdown()


async def test_duplicate_join_does_not_spawn_duplicate_proof() -> None:
    server, url = _start()
    _Handler.calls.clear()
    try:
        identity = _identity()
        controlled = ControlledClient(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        identity.auto_attest_on_join = True
        identity.configure_attestation(controlled)
        controlled.paused.add(0)
        await _event(identity, "minecraft.player_joined", ts=1.0)
        await _event(identity, "minecraft.player_joined", ts=2.0)
        await asyncio.sleep(0.05)
        assert len(controlled.gates) == 1
        assert len(identity._attest_bootstrap_tasks) == 1  # noqa: SLF001
        controlled.gates[0].set()
        await asyncio.sleep(0.1)
    finally:
        server.shutdown()


async def test_player_left_revokes_running_follow() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        binding = await identity.attest_player_session("Alice")
        runtime, _, action = _wire(identity)
        task = await _create(runtime, binding)
        await runtime.confirm_and_start(
            task.task_id, user_id="Alice", session_id=SESSION, origin="user"
        )
        assert len(action.action_calls("follow_player")) == 1

        async def _cancel_revoked(snapshot: dict[str, Any]) -> None:
            record = await runtime.active_for_user(str(snapshot["username"]), limit=20)
            if record is not None and record.actor_session_ref == snapshot["actor_session_ref"]:
                await runtime.cancel(record.task_id, reason="attested_session_invalidated")

        identity.on_attested_identity_revoked = lambda snapshot: asyncio.create_task(
            _cancel_revoked(snapshot)
        )
        await _event(identity, "minecraft.player_left", ts=2.0)
        await asyncio.sleep(0.15)
        record = await runtime.get(task.task_id)
        assert record is not None and record.state.value == "CANCELLED"
        assert len(action.action_calls("follow_player")) == 1
    finally:
        server.shutdown()


async def test_duplicate_revocation_is_idempotent() -> None:
    server, url = _start()
    _Handler.ref = "server-ref-1"
    try:
        identity = _identity()
        identity.configure_attestation(
            SessionAttestationClient(base_url=url, token=TOKEN, server_scope=SCOPE)
        )
        await _event(identity, "minecraft.player_joined")
        await identity.attest_player_session("Alice")
        snapshots: list[dict[str, Any]] = []
        identity.on_attested_identity_revoked = snapshots.append
        assert identity._revoke_attested_for("Alice", "first") is True  # noqa: SLF001
        assert identity._revoke_attested_for("Alice", "second") is False  # noqa: SLF001
        assert len(snapshots) == 1
        assert snapshots[0]["reason"] == "first"
    finally:
        server.shutdown()
