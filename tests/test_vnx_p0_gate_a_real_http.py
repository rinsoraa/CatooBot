"""VNX-1 P0 Gate A: real HTTP late Action ID E2.

Python TaskRuntime -> MinecraftService -> RuntimeClient -> proxy -> real Node runtime.
The proxy holds only the /minecraft/follow_player response, proving Node accepted
the action before Python learns its Action ID.

Event semantics
---------------
The proxy is the only component that can tell "Node accepted the world action" from
"Node rejected it". A single follow_accepted flag used to be raised for *every* follow
response, so a rejection such as
HTTP 404 {"ok": false, "error": {"code": "player.not_found"}}
was recorded as an accepted action; gate B then asserted action_a and failed with
assert ''. The proxy now exposes two distinct, assertable signals:

* follow_response_received -- any follow response arrived (accepted *or* rejected);
* follow_action_accepted  -- a *valid acceptance*: HTTP 200, ok true and a
  non-empty action_id.

Every response is recorded in follow_responses (HTTP status, business error code,
redacted action-id prefix, redacted stop-request-id prefix) so a rejected response
fails a test with actionable evidence instead of an empty string. A rejection is
never converted into success.

Wait semantics
--------------
Startup and readiness waits are explicit, polled and diagnosable (_wait +
_harness_timeout); no bare sleep is used to "give the harness time". The single
fixture knob CATOOBOT_HARNESS_TIMEOUT_SCALE stretches *fixture readiness* budgets
only. Production deadlines (runtime/client request timeout, STOP budget, bounded
shutdown) never read it, and no assertion on a real elapsed time is relaxed.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import signal
import socket
import subprocess
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import aiohttp
from aiohttp import web

from app.config.settings import DatabaseConfig, MinecraftConfig
from app.database.database import Database
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.service import MinecraftService
from app.integrations.minecraft.session_attestation import SessionAttestationClient
from app.integrations.minecraft.task_adapter import MinecraftTaskInvoker
from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
from app.tasks.models import ExpectedFinalState, TaskPlan, TaskStep
from app.tasks.runtime import TaskConfig, TaskRuntime
from app.tasks.store import SqliteTaskStore
from tests.test_task_turn import FakeConfirmations
from tests.test_vnx_p0_attested_follow_e2 import TOKEN as HMAC_TOKEN
from tests.test_vnx_p0_attested_follow_e2 import _start
from tests.test_vnx_p0_session_authorization_e2 import (
    SESSION,
    HybridService,
)

ROOT = Path(__file__).resolve().parents[1]
HARNESS = ROOT / "minecraft_runtime" / "test" / "gate_a_harness.js"
BRIDGE_TOKEN = "gate-a-token"

#: Fixture-readiness knob. It scales *startup/readiness* budgets of this harness
#: (node ready, controller ACTIVE, entity visible, follow accepted) and nothing else.
READINESS_SCALE_ENV = "CATOOBOT_HARNESS_TIMEOUT_SCALE"

#: Prefix length kept when an opaque identifier is echoed into evidence/logs.
_REDACT_KEEP = 8


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


# --------------------------------------------------------------------------- waits
def _readiness_scale() -> float:
    """Fixture-readiness multiplier (>= 1.0, default 1.0 = live deadlines)."""
    raw = (os.environ.get(READINESS_SCALE_ENV) or "1").strip() or "1"
    try:
        value = float(raw)
    except ValueError:
        return 1.0
    return value if value >= 1.0 else 1.0


def _harness_timeout(base: float) -> float:
    """Readiness budget for base seconds (fixture startup/readiness only).

    Never use this for a product deadline: request timeouts, the STOP budget and the
    bounded-shutdown budget are real values and must stay unscaled.
    """
    return float(base) * _readiness_scale()


async def _wait(
    condition: Callable[[], Any],
    label: str,
    timeout: float = 20.0,
    *,
    detail: Callable[[], Any] | None = None,
) -> None:
    """Poll condition until it holds; fail with a diagnosable message otherwise.

    timeout is a fixture-readiness budget and is stretched by
    CATOOBOT_HARNESS_TIMEOUT_SCALE. condition may be sync or async.
    """
    budget = _harness_timeout(timeout)
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        value = condition()
        if inspect.isawaitable(value):
            value = await value
        if value:
            return
        await asyncio.sleep(0.05)
    extra = ""
    if detail is not None:
        try:
            extra = "; " + json.dumps(detail(), ensure_ascii=False, sort_keys=True, default=str)
        except Exception:  # noqa: BLE001 - diagnostics must never mask the failure
            extra = ""
    raise AssertionError(f"readiness timeout after {budget:.1f}s: {label}{extra}")


def _redact_id(value: Any, *, keep: int = _REDACT_KEEP) -> str:
    """Leading characters of an opaque id plus its length (never the whole value)."""
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= keep:
        return text
    return f"{text[:keep]}...(len={len(text)})"


def _tail_id(value: Any, keep: int = 6) -> str:
    """Trailing characters of an opaque id (task ids are correlated by tail)."""
    text = str(value or "")
    if not text:
        return ""
    return text[-keep:] if len(text) > keep else text


def _phase(name: str, **fields: Any) -> None:
    """CI-friendly progress marker; every field is already redacted by the caller."""
    payload = json.dumps(fields, ensure_ascii=False, sort_keys=True, default=str)
    print(f"PHASE {name} {payload}".rstrip(), flush=True)


def _status_evidence(payload: Any) -> dict[str, Any]:
    """Redacted runtime status: the controller owner token is never included."""
    if not isinstance(payload, dict):
        return {}
    controller = payload.get("controller") if isinstance(payload.get("controller"), dict) else {}
    action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
    return {
        "status": payload.get("status"),
        "session_id": _redact_id(payload.get("session_id")),
        "runtime_instance": _redact_id(payload.get("runtime_instance")),
        "controller": {
            "state": controller.get("state"),
            "controller_id": _redact_id(controller.get("controller_id")),
            "owner_epoch": controller.get("owner_epoch"),
        },
        "action": {
            "status": action.get("status"),
            "action_id_prefix": _redact_id(action.get("action_id")),
            "active_count": action.get("active_count"),
        },
    }


def _task_evidence(record: Any) -> dict[str, Any]:
    """Redacted task record summary for failure messages."""
    if record is None:
        return {}
    steps = list(getattr(record, "steps", None) or [])
    step = steps[0] if steps else None
    return {
        "task_id_tail": _tail_id(getattr(record, "task_id", "")),
        "state": getattr(getattr(record, "state", None), "value", None),
        "pending_action_id_prefix": _redact_id(getattr(record, "pending_action_id", "")),
        "step_dispatch_state": getattr(step, "dispatch_state", None),
        "step_state": getattr(step, "state", None),
        "failure": getattr(record, "failure", None),
        "step_error": getattr(step, "error", None),
        "step_code": getattr(step, "code", None),
        "message": str(getattr(record, "message", "") or "")[:160],
    }


class Proxy:
    """HTTP proxy in front of the Node runtime that can hold/lose selected responses.

    Follow-plane signals (see the module docstring):

    * follow_response_received -- any follow response arrived;
    * follow_action_accepted  -- only a valid acceptance (HTTP 200, ok true,
      non-empty action_id);
    * follow_responses        -- assertable record of every response seen.

    accepted_body / accepted_action_ids are only populated by valid acceptances, so
    "action accepted" can never be claimed from a rejection.
    """

    def __init__(self, upstream_port: int, follow_path: str = "/minecraft/follow_player") -> None:
        self.upstream = f"http://127.0.0.1:{upstream_port}"
        self.follow_path = follow_path
        self.app = web.Application()
        self.app.router.add_route("*", "/{tail:.*}", self._handle)
        self.runner: Any = None
        self.session: Any = None
        self.port = 0
        # Follow plane: two distinct events plus an assertable record.
        self.follow_response_received = asyncio.Event()
        self.follow_action_accepted = asyncio.Event()
        self.release_follow = asyncio.Event()
        self.accepted_body: dict[str, Any] = {}
        self.accepted_action_ids: list[str] = []
        self.follow_responses: list[dict[str, Any]] = []
        # Every request the proxy saw (method/path/upstream status/latency only, never
        # headers or bodies) - the diagnostic trail when NO follow response arrives.
        self.request_log: list[dict[str, Any]] = []
        self.lose_follow_response = False
        # Stop plane (unchanged semantics).
        self.forwarded_stop_targets: list[str | None] = []
        self.hold_stop_target = ""
        self.stop_requested = asyncio.Event()
        self.release_stop = asyncio.Event()
        self.stop_requests: list[str | None] = []
        self.stop_request_id_values: list[str | None] = []
        self.stop_request_records: list[dict[str, Any]] = []
        self.stop_forwarded: list[str | None] = []
        self.stop_responses: list[dict[str, Any]] = []
        self.hold_stop_response_target = ""
        self.drop_stop_response_target = ""
        self.drop_stop_request_target = ""
        self.drop_stop_request_seen = asyncio.Event()
        self.stop_response_dropped = asyncio.Event()
        self.stop_response_received = asyncio.Event()
        self.release_stop_response = asyncio.Event()

    async def start(self) -> None:
        # shutdown_timeout: a handler parked on release_*/sleep(3600) must not make
        # cleanup() wait its 60 s default - that was pure fixture latency.
        self.runner = aiohttp.web.AppRunner(self.app, shutdown_timeout=2.0)
        await self.runner.setup()
        site = aiohttp.web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = int(site._server.sockets[0].getsockname()[1])  # noqa: SLF001
        self.session = aiohttp.ClientSession()

    async def close(self) -> None:
        # Unblock held handlers so cleanup() is prompt even when a test ends while a
        # response is still being held (the events are test scaffolding, not asserts).
        self.release_follow.set()
        self.release_stop.set()
        self.release_stop_response.set()
        if self.session is not None:
            await self.session.close()
        if self.runner is not None:
            await self.runner.cleanup()

    # ------------------------------------------------------------- follow plane
    def _record_follow_response(self, status: int, payload: Any) -> dict[str, Any]:
        """Record one follow response and raise the matching event(s)."""
        body: dict[str, Any] = payload if isinstance(payload, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else {}
        action_id = str(body.get("action_id") or "")
        record: dict[str, Any] = {
            "path": self.follow_path,
            "http_status": int(status),
            "ok": bool(body.get("ok")),
            "accepted": False,
            "action_id_prefix": _redact_id(action_id),
            "action_id_present": bool(action_id),
            "error_code": str(error.get("code") or ""),
            "error_message": str(error.get("message") or "")[:200],
            "stop_request_id_prefix": _redact_id(body.get("stop_request_id")),
            "body_keys": sorted(str(key) for key in body),
        }
        # A valid acceptance is HTTP 200 AND business ok AND a real action id.
        record["accepted"] = record["http_status"] == 200 and record["ok"] and bool(action_id)
        self.follow_responses.append(record)
        self.follow_response_received.set()
        if record["accepted"]:
            self.accepted_body = body
            self.accepted_action_ids.append(action_id)
            self.follow_action_accepted.set()
        return record

    def follow_diagnostics(self) -> dict[str, Any]:
        """Redacted view of the follow plane for failure messages."""
        return {
            "responses": list(self.follow_responses),
            "accepted_count": len(self.accepted_action_ids),
            "accepted_action_ids": [_redact_id(value) for value in self.accepted_action_ids],
            "rejected_count": sum(1 for row in self.follow_responses if not row["accepted"]),
            "release_follow_set": self.release_follow.is_set(),
            "recent_requests": self.request_diagnostics(),
        }

    def stop_diagnostics(self) -> dict[str, Any]:
        """Redacted view of the stop plane for failure messages."""
        return {
            "stop_request_records": list(self.stop_request_records),
            "forwarded_targets": [_redact_id(value) for value in self.forwarded_stop_targets],
            "response_bodies": len(self.stop_responses),
        }

    # ------------------------------------------------------------------ plumbing
    async def _handle(self, request: aiohttp.web.Request) -> aiohttp.web.StreamResponse:
        entry: dict[str, Any] = {
            "method": request.method,
            "path": request.path,
            "started": time.monotonic(),
        }
        self.request_log.append(entry)
        if len(self.request_log) > 200:
            del self.request_log[0]
        try:
            return await self._forward(request, entry)
        except Exception as exc:  # noqa: BLE001 - record, then let aiohttp surface it
            entry["error"] = f"{type(exc).__name__}: {exc}"[:160]
            raise

    def request_diagnostics(self) -> list[dict[str, Any]]:
        """Last requests seen by the proxy (no headers, no bodies)."""
        return [
            {
                "method": row.get("method"),
                "path": row.get("path"),
                "upstream_status": row.get("upstream_status"),
                "elapsed_ms": row.get("elapsed_ms"),
                "error": row.get("error"),
            }
            for row in self.request_log[-12:]
        ]

    async def _forward(
        self, request: aiohttp.web.Request, entry: dict[str, Any]
    ) -> aiohttp.web.StreamResponse:
        body = await request.read()
        headers = {
            key: value
            for key, value in request.headers.items()
            if key.lower() in {"authorization", "content-type", "x-catoo-controller-token"}
        }
        if request.path == "/minecraft/stop":
            try:
                parsed_stop = json.loads(body.decode("utf-8")) if body else {}
            except Exception:  # noqa: BLE001
                parsed_stop = {}
            stop_target = parsed_stop.get("action_id")
            stop_request_id = parsed_stop.get("stop_request_id")
            self.stop_requests.append(stop_target)
            self.stop_request_id_values.append(stop_request_id)
            self.stop_request_records.append(
                {
                    "action_id_prefix": _redact_id(stop_target),
                    "stop_request_id_prefix": _redact_id(stop_request_id),
                    "targeted": stop_target is not None,
                }
            )
            if self.drop_stop_request_target and stop_target == self.drop_stop_request_target:
                self.drop_stop_request_seen.set()
                request.transport.close()
                await asyncio.sleep(3600)
            if self.hold_stop_target and stop_target == self.hold_stop_target:
                self.stop_requested.set()
                await self.release_stop.wait()
            self.stop_forwarded.append(stop_target)
        async with self.session.request(
            request.method,
            self.upstream + request.path_qs,
            data=body or None,
            headers=headers,
        ) as upstream:
            payload = await upstream.read()
            entry["upstream_status"] = upstream.status
            entry["elapsed_ms"] = round((time.monotonic() - entry["started"]) * 1000, 1)
            if request.path == self.follow_path and request.method == "POST":
                try:
                    parsed_follow: Any = json.loads(payload.decode("utf-8"))
                except Exception:  # noqa: BLE001 - a malformed body is still a response
                    parsed_follow = {}
                self._record_follow_response(upstream.status, parsed_follow)
                if self.lose_follow_response:
                    request.transport.close()
                    await asyncio.sleep(3600)
                await self.release_follow.wait()
            if request.path == "/minecraft/stop":
                self.forwarded_stop_targets.append(
                    self.stop_forwarded[-1] if self.stop_forwarded else None
                )
                try:
                    self.stop_responses.append(json.loads(payload.decode("utf-8")))
                except Exception:  # noqa: BLE001
                    self.stop_responses.append({})
                if (
                    self.hold_stop_response_target
                    and self.stop_forwarded[-1] == self.hold_stop_response_target
                ):
                    self.stop_response_received.set()
                    await self.release_stop_response.wait()
                if (
                    self.drop_stop_response_target
                    and self.stop_forwarded[-1] == self.drop_stop_response_target
                ):
                    self.stop_response_dropped.set()
                    request.transport.close()
                    await asyncio.sleep(3600)
            return aiohttp.web.Response(
                status=upstream.status,
                body=payload,
                content_type=upstream.content_type or "application/json",
            )


async def _wait_follow_accepted(
    proxy: Proxy,
    *,
    label: str,
    timeout: float = 60.0,
    task_probe: Callable[[], Any] | None = None,
) -> str:
    """Wait for a *valid* follow acceptance and return its action id.

    A rejected response is never treated as accepted: if only a rejection arrives the
    wait fails with the recorded HTTP status and business error code. task_probe
    (usually "lambda: runtime.get(task_id)") adds the task record to the failure
    message - a dispatch refused *before* the HTTP call shows up as "no response".
    """
    try:
        await _wait(
            lambda: proxy.follow_action_accepted.is_set(),
            label,
            timeout,
            detail=proxy.follow_diagnostics,
        )
    except AssertionError as exc:
        extra = ""
        if task_probe is not None:
            try:
                record = task_probe()
                if inspect.isawaitable(record):
                    record = await record
                extra = "; task=" + json.dumps(_task_evidence(record), ensure_ascii=False)
            except Exception:  # noqa: BLE001 - diagnostics must never mask the failure
                extra = ""
        raise AssertionError(f"{exc}{extra}") from None
    action_id = str(proxy.accepted_body.get("action_id") or "")
    assert action_id, (
        f"{label}: accepted response carries no action_id: {proxy.follow_diagnostics()}"
    )
    last = proxy.follow_responses[-1] if proxy.follow_responses else {}
    assert last.get("accepted") is True, (
        f"{label}: last follow response is not an acceptance: {last}"
    )
    assert last.get("http_status") == 200, f"{label}: acceptance is not HTTP 200: {last}"
    return action_id


class CallbackReceiver:
    def __init__(self, service: MinecraftService) -> None:
        self.service = service
        self.app = aiohttp.web.Application()
        self.app.router.add_post("/events", self._handle)
        self.runner: Any = None
        self.port = 0

    async def start(self) -> None:
        self.runner = aiohttp.web.AppRunner(self.app)
        await self.runner.setup()
        site = aiohttp.web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        self.port = int(site._server.sockets[0].getsockname()[1])  # noqa: SLF001

    async def close(self) -> None:
        if self.runner is not None:
            await self.runner.cleanup()

    async def _handle(self, request: aiohttp.web.Request) -> aiohttp.web.Response:
        payload = await request.json()
        await self.service.receive_event(payload)
        return aiohttp.web.json_response({"ok": True})


HARNESS_CONTROLLER_ID = ""

#: Process-group ids of harnesses started with start_new_session (POSIX only).
#: Only groups we created are ever signalled - never killpg of an unknown pid.
_HARNESS_PGIDS: dict[int, int] = {}


async def _start_harness(
    callback_port: int, runtime_port: int, server_port: int
) -> tuple[subprocess.Popen[bytes], dict[str, Any], int]:
    global HARNESS_CONTROLLER_ID
    cfg = {
        "runtimePort": runtime_port,
        "serverPort": server_port,
        "callbackUrl": f"http://127.0.0.1:{callback_port}/events",
        "controllerId": HARNESS_CONTROLLER_ID or "gate-a-harness",
        "sharedController": bool(HARNESS_CONTROLLER_ID),
    }
    HARNESS_CONTROLLER_ID = ""
    spawn_kwargs: dict[str, Any] = {}
    if os.name != "nt":
        # Own session/process group so the spawned Node runtime can be reaped with it
        # (see _kill_tree). Windows uses taskkill /T instead.
        spawn_kwargs["start_new_session"] = True
    proc = subprocess.Popen(
        ["node", str(HARNESS), json.dumps(cfg)],
        cwd=str(ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        **spawn_kwargs,
    )
    if os.name != "nt":
        try:
            _HARNESS_PGIDS[proc.pid] = os.getpgid(proc.pid)
        except OSError:  # pragma: no cover - process already gone
            pass
    assert proc.stdout is not None
    deadline = time.monotonic() + _harness_timeout(120)
    try:
        while time.monotonic() < deadline:
            remaining = max(1.0, deadline - time.monotonic())
            line = await asyncio.wait_for(asyncio.to_thread(proc.stdout.readline), remaining)
            if not line:
                break
            if line.startswith(b"READY"):
                return proc, json.loads(line.split(b" ", 1)[1]), runtime_port
    except TimeoutError:
        pass
    _kill_tree(proc)
    stderr = b""
    if proc.stderr is not None:
        try:
            stderr = proc.stderr.read() or b""
        except Exception:  # noqa: BLE001
            stderr = b""
    raise AssertionError(
        "harness did not become READY within "
        + f"{_harness_timeout(120):.0f}s: "
        + stderr.decode("utf-8", errors="replace")[-2000:]
    )


def _kill_tree(proc: subprocess.Popen[bytes]) -> None:
    """Terminate the harness *and* the Node runtime it spawned, on every platform.

    Windows: taskkill /T /F walks the tree. POSIX: the harness runs in its own
    session, so killpg(SIGKILL) reaps the runtime child as well - a bare proc.kill()
    (the previous fallback) left the runtime listening, which silently invalidated
    every "runtime unreachable" precondition.
    """
    if os.name == "nt":
        if proc.poll() is None:
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    check=False,
                    timeout=_harness_timeout(30),
                )
            except (OSError, subprocess.SubprocessError):
                try:
                    proc.kill()
                except OSError:  # pragma: no cover - already gone
                    pass
    else:
        pgid = _HARNESS_PGIDS.get(proc.pid)
        if pgid is None and proc.poll() is None:
            try:
                pgid = os.getpgid(proc.pid)
            except OSError:
                pgid = None
        if pgid is not None:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
            except OSError:  # pragma: no cover - defensive
                try:
                    proc.kill()
                except OSError:
                    pass
        else:  # pragma: no cover - neither tracked nor alive
            try:
                proc.kill()
            except OSError:
                pass
    try:
        proc.wait(timeout=_harness_timeout(10))
    except (subprocess.TimeoutExpired, OSError):  # pragma: no cover - slow reap
        try:
            proc.kill()
        except OSError:
            pass
    _HARNESS_PGIDS.pop(proc.pid, None)


def _port_open(port: int, *, timeout: float = 0.25) -> bool:
    """True when something still accepts TCP connections on 127.0.0.1:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(timeout)
        return sock.connect_ex(("127.0.0.1", int(port))) == 0


async def _wait_ports_closed(ports: dict[str, int], *, label: str) -> dict[str, Any]:
    """Assert every fixture port was released; name the ports that were not."""
    budget = _harness_timeout(15.0)
    started = time.monotonic()
    deadline = started + budget
    still_open = {name: port for name, port in ports.items() if port and _port_open(port)}
    while still_open and time.monotonic() < deadline:
        await asyncio.sleep(0.1)
        still_open = {name: port for name, port in ports.items() if port and _port_open(port)}
    elapsed = time.monotonic() - started
    if still_open:
        detail = ", ".join(f"{name}={port}" for name, port in sorted(still_open.items()))
        raise AssertionError(
            f"{label}: fixture ports still open after {elapsed:.1f}s (subprocess tree not "
            + f"reaped): {detail}"
        )
    return {
        "elapsed_seconds": round(elapsed, 3),
        "ports_closed": sorted(name for name, port in ports.items() if port),
    }


async def _stop_harness(
    harness: subprocess.Popen[bytes],
    *,
    runtime_port: int | None = None,
    server_port: int | None = None,
    control_port: int | None = None,
    label: str = "harness shutdown",
) -> dict[str, Any]:
    """Kill the harness tree, then prove runtime/server/control ports were released."""
    _kill_tree(harness)
    try:
        await asyncio.to_thread(harness.wait, _harness_timeout(10))
    except Exception:  # noqa: BLE001 - reap is best effort, the port check is the proof
        pass
    ports: dict[str, int] = {}
    if runtime_port:
        ports["runtime"] = int(runtime_port)
    if server_port:
        ports["server"] = int(server_port)
    if control_port:
        ports["control"] = int(control_port)
    return await _wait_ports_closed(ports, label=label)


async def _observer_control(
    info: dict[str, Any], action: str, name: str, **extra: str
) -> dict[str, Any]:
    url = f"http://127.0.0.1:{info['controlPort']}/observer/{action}"
    params = {"name": name, "token": "gate-a-token", **extra}
    async with (
        aiohttp.ClientSession() as session,
        session.get(url, params=params, timeout=aiohttp.ClientTimeout(total=30)) as response,
    ):
        payload = await response.json()
        assert response.status == 200, payload
        return payload


async def _service_for(
    proxy_port: int, callback_url: str, *, request_timeout_seconds: float = 60
) -> MinecraftService:
    config = MinecraftConfig(
        enabled=True,
        auto_start_runtime=False,
        runtime_port=proxy_port,
        request_timeout_seconds=request_timeout_seconds,
        external_bridge_token=BRIDGE_TOKEN,
        external_callback_url=callback_url,
        external_callback_token="cb",
        perception_enabled=True,
    )
    config.agent.trusted_players = ["Tester"]
    service = MinecraftService(None, config)
    await service.start()
    global HARNESS_CONTROLLER_ID
    HARNESS_CONTROLLER_ID = service._controller_id  # noqa: SLF001
    return service


async def _set_client_request_budget(service: MinecraftService, total: float) -> float:
    """Set the runtime client's per-request budget, returning the previous total.

    Used by the STOP-timeout tests: the 1 s budget is the *STOP-phase* contract, but
    imposing it on the Follow dispatch made the setup race it (a cold
    /minecraft/health probe can exceed 1 s on a loaded runner). Those tests therefore
    dispatch the Follow with the normal budget, narrow the budget for the STOP phase,
    and restore it afterwards.
    """
    client = service._client  # noqa: SLF001 - the transport budget is fixture config
    # NB: must not use client.close() here. close() is the *shutdown* signal and the
    # post-close guard deliberately rejects further I/O, so narrowing the budget through
    # close() silently disabled every later request: the STOP then never left Python and
    # the tests simply waited out their whole budget (A3-FU3 F1/F2).
    return await client.set_request_timeout(float(total))


async def _runtime_for(service: MinecraftService) -> TaskRuntime:
    runtime = TaskRuntime(
        store=SqliteTaskStore(service_database),
        invoke=lambda *a, **k: None,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: "LOW",
        is_registered=lambda tool: (
            tool
            in {
                "minecraft_follow_player",
                "minecraft_move_to",
            }
        ),
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )
    if not getattr(service, "username", ""):
        service.username = "GuanTou"  # type: ignore[attr-defined]
    hybrid = HybridService(service, service)
    bridge = MinecraftAgentBridge(hybrid)
    bridge.set_task_authorizer(runtime.authorize_step)
    service.agent = bridge  # type: ignore[attr-defined]
    runtime._invoke = MinecraftTaskInvoker(bridge, task_reader=runtime.get)  # noqa: SLF001
    return runtime


# --------------------------------------------------------------- readiness gates
async def _wait_runtime_ready(
    service: MinecraftService, *, timeout: float = 60.0
) -> dict[str, Any]:
    """Barrier: runtime ONLINE, controller ACTIVE and owned by us, action plane idle.

    Uses the runtime's *own* status view (not the local mirror) so the barrier proves
    the state a Follow dispatch actually depends on.
    """
    latest: dict[str, Any] = {}

    async def _ready() -> bool:
        try:
            payload = await service._client.status()  # noqa: SLF001
        except Exception:  # noqa: BLE001 - readiness probe
            latest.clear()
            return False
        latest["status"] = payload
        if not isinstance(payload, dict) or str(payload.get("status") or "") != "ONLINE":
            return False
        controller = (
            payload.get("controller") if isinstance(payload.get("controller"), dict) else {}
        )
        if str(controller.get("state") or "") != "ACTIVE":
            return False
        if str(controller.get("controller_id") or "") != str(service._controller_id):  # noqa: SLF001
            return False
        action = payload.get("action") if isinstance(payload.get("action"), dict) else {}
        if int(action.get("active_count") or 0) != 0:
            return False
        # Dispatch also evaluates Python-side state: a live controller credential and the
        # local ONLINE mirror (MinecraftService.follow_player refuses with
        # "not in the world" while the mirror is not ONLINE, even though the runtime is
        # reachable). Waiting for the same view the dispatch checks keeps this barrier
        # honest instead of racing the event/poll loop that fills the mirror.
        if str(service._mirror.get("status") or "") != "ONLINE":  # noqa: SLF001
            # Refresh the mirror through the supported service path instead of waiting for
            # the next poll tick; the dispatch gate reads this mirror, not the runtime.
            await service.status()
            if str(service._mirror.get("status") or "") != "ONLINE":  # noqa: SLF001
                return False
        return bool(service.controller_ready())

    await _wait(
        _ready,
        "runtime ONLINE + controller ACTIVE (owned by this service) + idle action plane",
        timeout,
        detail=lambda: _status_evidence(latest.get("status")),
    )
    return latest.get("status") or {}


async def _wait_player_entity_visible(
    service: MinecraftService,
    name: str,
    *,
    timeout: float = 60.0,
    placement: dict[str, Any] | None = None,
) -> None:
    """Barrier: the runtime itself lists name with a live entity.

    runtime.js rejects follow_player with player.not_found (HTTP 404) unless
    bot.players[name].entity exists, and buildWorldSnapshot lists exactly the players
    that satisfy that condition - so this waits on the runtime's own precondition
    instead of guessing with a sleep.
    """

    seen: dict[str, Any] = {}

    async def _visible() -> bool:
        try:
            snapshot = await service._client.world_snapshot()  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 - readiness probe
            seen["error"] = type(exc).__name__
            return False
        if not isinstance(snapshot, dict):
            seen["error"] = "non-dict snapshot"
            return False
        players = [
            str(player.get("username") or "")
            for player in (snapshot.get("players") or [])
            if isinstance(player, dict)
        ]
        seen["snapshot_online"] = bool(snapshot.get("online"))
        seen["snapshot_players"] = players
        return bool(snapshot.get("online")) and name in players

    await _wait(
        _visible,
        f"player entity visible to the runtime: {name}",
        timeout,
        detail=lambda: {
            "player": name,
            "session_keys": len(service._session_keys_for(name)),  # noqa: SLF001
            "mirror_status": service._mirror.get("status"),  # noqa: SLF001
            "runtime_down": bool(getattr(service, "_runtime_down", False)),
            "last_event": dict(service._last_event or {}).get("event"),  # noqa: SLF001
            "placement": placement,
            **seen,
        },
    )


async def _move_observer_near(
    info: dict[str, Any] | None,
    name: str,
    anchor: str = "GuanTou",
    *,
    rejoin: bool = False,
) -> dict[str, Any]:
    """Bring a harness-spawned observer into the bot's entity-tracking range.

    The fake server places observers at the world spawn, which is occasionally outside
    the bot's view; runtime.js then rejects Follow with player.not_found. The harness
    control plane exposes /observer/move_near for exactly this fixture problem, so a
    distant fake player is relocated instead of silently weakening the Follow contract.
    """
    if not info or not info.get("controlPort"):
        return {"ok": False, "code": "no_control_plane"}
    extra: dict[str, str] = {"anchor": anchor}
    if rejoin:
        extra["rejoin"] = "1"
    try:
        payload = await _observer_control(info, "move_near", name, **extra)
    except Exception as exc:  # noqa: BLE001 - not every name is a managed observer
        return {"ok": False, "code": "control_error", "error": type(exc).__name__}
    return payload if isinstance(payload, dict) else {"ok": False, "code": "non_dict"}


async def _wait_harness_ready(
    service: MinecraftService,
    *,
    players: tuple[str, ...] = ("Tester",),
    info: dict[str, Any] | None = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """Full readiness barrier: runtime/controller/action-plane, then players.

    Observers are relocated next to the bot *before* waiting: the fake server spawns them
    at the world spawn, which can be outside the bot's entity-tracking range, and
    runtime.js then rejects Follow with player.not_found. Placement is fixture setup (the
    harness control plane provides /observer/move_near for it); the assertion itself
    stays "the runtime lists the player with a live entity".
    """
    status = await _wait_runtime_ready(service, timeout=timeout)
    placement: dict[str, dict[str, Any]] = {}
    for player in players:
        placement[player] = await _move_observer_near(info, player)
    for player in players:
        try:
            await _wait_player_entity_visible(
                service, player, timeout=min(20.0, timeout), placement=placement.get(player)
            )
        except AssertionError:
            # Second attempt: server-authoritative placement PLUS a rejoin, because a
            # client that connects after this player already joined never receives its
            # entity-spawn packet (flying-squid does not replay existing players), so no
            # amount of extra waiting or distance-fixing alone can make it visible.
            placement[player] = await _move_observer_near(info, player, rejoin=True)
            await _wait_player_entity_visible(
                service, player, timeout=timeout, placement=placement.get(player)
            )
    return status


service_database: Database


async def test_real_http_late_action_id_after_revocation(tmp_path: Any) -> None:
    global service_database
    hmac_server, hmac_url = _start()
    service_database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-a.db'}"))
    await service_database.connect()
    callback: CallbackReceiver | None = None
    proxy: Proxy | None = None
    harness: subprocess.Popen[bytes] | None = None
    service: MinecraftService | None = None
    coordinator: MinecraftTaskCoordinator | None = None
    harness_info: dict[str, Any] = {}
    runtime_port = 0
    server_port = 0
    try:
        holder: dict[str, MinecraftService] = {}

        class _Receiver(CallbackReceiver):
            async def _handle(self, request: web.Request) -> web.Response:
                payload = await request.json()
                await holder["service"].receive_event(payload)
                return web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = _free_port()
        server_port = _free_port()
        proxy = Proxy(runtime_port)
        await proxy.start()
        _phase("service-start")
        service = await _service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        _phase("service-started")
        import tests.test_vnx_p0_attested_follow_e2 as e2

        scope = f"127.0.0.1:{server_port}"
        e2.SCOPE = scope
        service.configure_attestation(
            SessionAttestationClient(base_url=hmac_url, token=HMAC_TOKEN, server_scope=scope)
        )
        _phase("harness-start")
        harness, harness_info, _ = await _start_harness(callback.port, runtime_port, server_port)
        _phase(
            "harness-ready",
            runtime_port=runtime_port,
            server_port=server_port,
            control_port=harness_info.get("controlPort"),
        )
        status = await _wait_harness_ready(service, players=("Tester",), info=harness_info)
        _phase("ready", status=_status_evidence(status))
        from tests.test_vnx_p0_attested_follow_e2 import _Handler

        _Handler.force_uuid = service.player_uuid("Tester") or None
        binding_a = await service.attest_player_session("Tester")
        _phase("attested-a", actor_session_ref=_redact_id(binding_a.get("actor_session_ref")))
        assert binding_a["identity_assurance"] == "server_attested"
        runtime = await _runtime_for(service)
        coordinator = MinecraftTaskCoordinator(runtime, service)
        coordinator.start()
        runtime_positions["value"] = dict(service._mirror.get("position") or {})  # noqa: SLF001
        task_a = await _follow_task(runtime, binding_a, session_id=SESSION, target="Tester")
        start_task = asyncio.create_task(
            runtime.confirm_and_start(
                task_a.task_id, user_id="Tester", session_id=SESSION, origin="user"
            )
        )
        _phase("task-a-start", task_id_tail=_tail_id(task_a.task_id))
        try:
            action_a = await _wait_follow_accepted(proxy, label="Follow A accepted", timeout=60)
        except AssertionError as exc:
            record = await runtime.get(task_a.task_id)
            raise AssertionError(
                f"{exc}; task={json.dumps(_task_evidence(record), ensure_ascii=False)}"
            ) from None
        _phase("follow-accepted", action_id_prefix=_redact_id(action_a))
        await _wait(lambda: _task_state(runtime, task_a.task_id, "RUNNING"), "Task A RUNNING")
        _phase("cancel-a")
        await runtime.cancel(task_a.task_id, reason="attested_session_invalidated")
        _phase("cancelled-a")
        assert await _task_state(runtime, task_a.task_id, "CANCELLED")
        # Session B: revoke old local proof, rejoin, re-attest.
        tester_uuid = service.player_uuid("Tester")
        await service.receive_event(
            {
                "event": "minecraft.player_left",
                "session_id": service._mirror.get("session_id", "runtime-1"),
                "timestamp": 10.0,
                "username": "Tester",
                "uuid": tester_uuid,
            }
        )  # noqa: SLF001
        await service.receive_event(
            {
                "event": "minecraft.player_joined",
                "session_id": service._mirror.get("session_id", "runtime-1"),
                "timestamp": 11.0,
                "username": "Tester",
                "uuid": tester_uuid,
            }
        )  # noqa: SLF001
        await _wait_player_entity_visible(service, "Tester")
        binding_b = await service.attest_player_session("Tester")
        _phase("attested-b", actor_session_ref=_redact_id(binding_b.get("actor_session_ref")))
        assert binding_b["actor_session_ref"] != binding_a["actor_session_ref"]
        task_b = await _follow_task(runtime, binding_b, session_id="mc:session-b", target="Tester")
        blocked = await runtime.confirm_and_start(
            task_b.task_id, user_id="Tester", session_id="mc:session-b", origin="user"
        )
        assert blocked.state.value != "WAITING_ACTION"
        _phase("b-blocked", state=blocked.state.value)
        _phase("release")
        proxy.release_follow.set()
        await asyncio.wait_for(start_task, timeout=30)
        await _wait(lambda: _action_settled(runtime, task_a.task_id), "Action A settled")
        _phase("settled-a")
        assert proxy.forwarded_stop_targets[-1] == action_a
        assert None not in proxy.forwarded_stop_targets
        fresh = await _follow_task(runtime, binding_b, session_id="mc:session-b2", target="Tester")
        await runtime.confirm_and_start(
            fresh.task_id, user_id="Tester", session_id="mc:session-b2", origin="user"
        )
        assert len([t for t in proxy.forwarded_stop_targets if t == action_a]) == 1
        await service_database.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-a.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task_a.task_id)
        assert old is not None
        assert old.state.value == "CANCELLED"
        step = old.steps[0]
        assert step.action_id == action_a
        assert step.dispatch_state == "SETTLED"
        assert step.dispatch_generation >= 1
        await reopened.close()
    finally:
        if coordinator is not None:
            await coordinator.stop()
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await _stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int(harness_info.get("controlPort") or 0) or None,
                label="gate A harness shutdown",
            )
        if callback is not None:
            await callback.close()
        import tests.test_vnx_p0_attested_follow_e2 as _e2mod

        _e2mod.SCOPE = "mc.test:25565"
        _e2mod._Handler.force_uuid = None
        hmac_server.shutdown()


runtime_positions: dict[str, Any] = {}


async def _move_task(
    runtime: TaskRuntime,
    binding: dict[str, Any],
    *,
    session_id: str,
    x: float,
    y: float,
    z: float,
    user_id: str = "Tester",
) -> Any:
    plan = TaskPlan(
        objective=f"move to {x},{y},{z}",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={"x": x, "y": y, "z": z},
                risk="LOW",
            )
        ],
        expected_final_state=ExpectedFinalState(),
    )
    return await runtime.create_task(
        f"move to {x},{y},{z}",
        session_id=session_id,
        user_id=user_id,
        origin="user",
        plan=plan,
        source="minecraft_chat",
        actor_session=binding,
    )


async def _follow_task(
    runtime: TaskRuntime,
    binding: dict[str, Any],
    *,
    session_id: str,
    target: str,
    user_id: str = "Tester",
) -> Any:
    plan = TaskPlan(
        objective="follow " + target,
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_follow_player",
                arguments={"username": target, "distance": 2.5},
                risk="LOW",
            )
        ],
        expected_final_state=ExpectedFinalState(),
    )
    return await runtime.create_task(
        "follow " + target,
        session_id=session_id,
        user_id=user_id,
        origin="user",
        plan=plan,
        source="minecraft_chat",
        actor_session=binding,
    )


async def test_real_http_lost_follow_response_remains_unknown(tmp_path: Any) -> None:
    global service_database
    service_database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-a-lost.db'}"))
    await service_database.connect()
    callback: CallbackReceiver | None = None
    proxy: Proxy | None = None
    harness: subprocess.Popen[bytes] | None = None
    service: MinecraftService | None = None
    harness_info: dict[str, Any] = {}
    runtime_port = 0
    server_port = 0
    try:
        holder: dict[str, MinecraftService] = {}

        class _Receiver(CallbackReceiver):
            async def _handle(self, request: web.Request) -> web.Response:
                await holder["service"].receive_event(await request.json())
                return web.json_response({"ok": True})

        callback = _Receiver(None)  # type: ignore[arg-type]
        await callback.start()
        runtime_port = _free_port()
        server_port = _free_port()
        proxy = Proxy(runtime_port)
        await proxy.start()
        service = await _service_for(proxy.port, f"http://127.0.0.1:{callback.port}/events")
        holder["service"] = service
        service._mirror["host"] = "127.0.0.1"  # noqa: SLF001
        service._mirror["port"] = server_port  # noqa: SLF001
        harness, harness_info, _ = await _start_harness(callback.port, runtime_port, server_port)
        status = await _wait_harness_ready(service, players=("Tester",), info=harness_info)
        _phase("ready-lost", status=_status_evidence(status))
        binding = service.player_session_binding("Tester")
        runtime = await _runtime_for(service)
        task = await _follow_task(runtime, binding, session_id="mc:lost", target="Tester")
        proxy.lose_follow_response = True
        start_task = asyncio.create_task(
            runtime.confirm_and_start(
                task.task_id, user_id="Tester", session_id="mc:lost", origin="user"
            )
        )
        # This case *intentionally* drops the response towards Python. The proxy still
        # observes what Node answered, so assert that directly - "action accepted by
        # Node" must not be claimed from Python's point of view here.
        await _wait(
            lambda: proxy.follow_response_received.is_set(),
            "follow response observed by the proxy",
            30,
            detail=proxy.follow_diagnostics,
        )
        observed = proxy.follow_responses[-1]
        assert observed["http_status"] == 200, f"Node must accept the action: {observed}"
        assert observed["ok"] is True, f"Node must answer ok=true: {observed}"
        assert observed["action_id_present"] is True, f"Node must return an action_id: {observed}"
        action_a = str(proxy.accepted_body.get("action_id") or "")
        assert action_a, proxy.follow_diagnostics()
        # The acceptance must stay UNKNOWN to Python: the request cannot complete while
        # the proxy swallows the response (40 s is an observation window, not a product
        # deadline, so it is deliberately not scaled by the fixture knob).
        try:
            await asyncio.wait_for(start_task, timeout=40)
        except TimeoutError:
            start_task.cancel()
        record = await runtime.get(task.task_id)
        assert record is not None
        assert record.state.value in {"PAUSED", "FAILED"}, record.state.value
        assert record.steps[0].dispatch_state in {"AMBIGUOUS", "INTENT", "ACCEPTED"}
        assert record.recovery.get("detail", {}).get("stop_confirmed") is not True
        blocked = await _follow_task(runtime, binding, session_id="mc:lost-b", target="Tester")
        result = await runtime.confirm_and_start(
            blocked.task_id, user_id="Tester", session_id="mc:lost-b", origin="user"
        )
        assert result.state.value != "WAITING_ACTION"
        await service_database.close()
        reopened = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'gate-a-lost.db'}"))
        await reopened.connect()
        store = SqliteTaskStore(reopened)
        old = await store.load(task.task_id)
        assert old is not None
        assert old.steps[0].dispatch_state in {"AMBIGUOUS", "INTENT", "ACCEPTED"}
        assert old.recovery.get("detail", {}).get("stop_confirmed") is not True
        await reopened.close()
        # Cleanup the still-running real Node action through the control plane.
        try:
            await service._client.stop(action_a)  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
    finally:
        if service is not None:
            await service.stop()
        if proxy is not None:
            await proxy.close()
        if harness is not None:
            await _stop_harness(
                harness,
                runtime_port=runtime_port,
                server_port=server_port,
                control_port=int(harness_info.get("controlPort") or 0) or None,
                label="gate A lost-response harness shutdown",
            )
        if callback is not None:
            await callback.close()


async def _nearby_visible(service: MinecraftService, name: str) -> bool:
    bridge = getattr(service, "agent", None)
    if bridge is None:
        return False
    try:
        facts = bridge.world_facts()
    except Exception:  # noqa: BLE001
        return False
    return name in list(facts.get("nearby_players") or [])


async def _player_visible(service: MinecraftService, name: str) -> bool:
    if service._session_keys_for(name):  # noqa: SLF001
        return True
    try:
        snapshot = await service._client.world_snapshot()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        return False
    return name in json.dumps(snapshot, ensure_ascii=False)


async def _follow_ready(service: MinecraftService, name: str) -> bool:
    bridge = getattr(service, "agent", None)
    if bridge is None:
        return False
    try:
        facts = bridge.world_facts()
    except Exception:  # noqa: BLE001
        return False
    return bool(facts.get("online"))


async def _online(service: MinecraftService) -> bool:
    return (
        str(service._mirror.get("status") or "") == "ONLINE"  # noqa: SLF001
        and service.controller_ready()
    )


async def _has_player(service: MinecraftService, name: str) -> bool:
    return len(service._session_keys_for(name)) == 1  # noqa: SLF001


async def _task_state(runtime: TaskRuntime, task_id: str, expected: str) -> bool:
    record = await runtime.get(task_id)
    return record is not None and record.state.value == expected


async def _action_settled(runtime: TaskRuntime, task_id: str) -> bool:
    record = await runtime.get(task_id)
    if record is None:
        return False
    return record.steps[0].dispatch_state == "SETTLED"
