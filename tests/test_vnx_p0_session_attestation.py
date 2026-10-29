"""VNX-1 P0 AS7-ATTESTATION-IMPL: Python HMAC session client tests."""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest

from app.integrations.minecraft.session_attestation import (
    AttestationError,
    SessionAttestationClient,
)

TOKEN = "test-token"
SCOPE = "127.0.0.1:25565"


def _sign(body: bytes) -> str:
    return hmac.new(TOKEN.encode(), body, hashlib.sha256).hexdigest()


class _Handler(BaseHTTPRequestHandler):
    mode = "ok"

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        nonce = (query.get("nonce") or [""])[0]
        name = (query.get("name") or [""])[0]
        expected = hmac.new(TOKEN.encode(), f"{name}\n{nonce}".encode(), hashlib.sha256).hexdigest()
        if self.headers.get("X-Signature") != expected:
            self.send_response(401)
            self.end_headers()
            return
        payload: dict[str, Any] = {
            "ok": True,
            "schema": 1,
            "nonce": nonce,
            "session_ref": "server-ref-1",
            "session_seq": 7,
            "player_uuid": "uuid-A",
            "server_scope": SCOPE,
            "boot_id": "boot-1",
        }
        if self.mode == "bad_signature":
            body = json.dumps(payload).encode()
            self.send_response(200)
            self.send_header("X-Signature", "bad")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.mode == "bad_nonce":
            payload["nonce"] = "other"
        if self.mode == "bad_uuid":
            payload["player_uuid"] = "uuid-B"
        if self.mode == "missing_boot":
            payload.pop("boot_id")
        body = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("X-Signature", _sign(body))
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return None


@pytest.fixture()
def endpoint() -> Any:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=2)


def _client(endpoint: str) -> SessionAttestationClient:
    return SessionAttestationClient(base_url=endpoint, token=TOKEN, server_scope=SCOPE)


def test_proto01_valid_signature_and_session(endpoint: str) -> None:
    _Handler.mode = "ok"
    attested = _client(endpoint).fetch(name="Alice", player_uuid="uuid-A")
    assert attested.session_ref == "server-ref-1"
    assert attested.boot_id == "boot-1"
    assert attested.server_scope == SCOPE


@pytest.mark.parametrize(
    ("mode", "reason"),
    [
        ("bad_signature", "signature"),
        ("bad_nonce", "nonce"),
        ("bad_uuid", "uuid"),
        ("missing_boot", "boot_id"),
    ],
)
def test_proto_negative_cases(endpoint: str, mode: str, reason: str) -> None:
    _Handler.mode = mode
    with pytest.raises(AttestationError, match=reason):
        _client(endpoint).fetch(name="Alice", player_uuid="uuid-A")


def test_non_loopback_is_rejected() -> None:
    with pytest.raises(ValueError):
        SessionAttestationClient(
            base_url="http://example.com:25565", token=TOKEN, server_scope=SCOPE
        )


async def test_async_fetch_matches_sync(endpoint: str) -> None:
    _Handler.mode = "ok"
    attested = await _client(endpoint).fetch_async(name="Alice", player_uuid="uuid-A")
    assert attested.session_ref == "server-ref-1"
