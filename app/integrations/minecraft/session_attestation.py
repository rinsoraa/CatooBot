"""Read-only HMAC session attestation client (loopback only)."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import http.client
import json
import secrets
from dataclasses import dataclass
from urllib.parse import urlparse


class AttestationError(RuntimeError):
    """Raised when a session attestation cannot be trusted."""


@dataclass(frozen=True)
class AttestedSession:
    schema: int
    nonce: str
    session_ref: str
    session_seq: int
    player_uuid: str
    server_scope: str
    boot_id: str
    raw: bytes


def _sign(body: bytes, token: str) -> str:
    return hmac.new(token.encode("utf-8"), body, hashlib.sha256).hexdigest()


def _verify(body: bytes, token: str, signature: str | None) -> bool:
    if not signature:
        return False
    return hmac.compare_digest(_sign(body, token), signature)


class SessionAttestationClient:
    """GET /session with request/response HMAC over raw bytes; fail-closed."""

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        server_scope: str,
        timeout: float = 2.0,
        max_response_bytes: int = 4096,
    ) -> None:
        parsed = urlparse(base_url)
        if parsed.scheme != "http" or parsed.hostname != "127.0.0.1":
            raise ValueError("attestation endpoint must be http 127.0.0.1")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("attestation endpoint must not contain credentials/query/fragment")
        if not parsed.port or not token or not server_scope:
            raise ValueError("attestation endpoint/token/scope required")
        self._host = "127.0.0.1"
        self._port = int(parsed.port)
        self._path = parsed.path.rstrip("/") or ""
        self._token = token
        self._scope = server_scope
        self._timeout = float(timeout)
        self._max = int(max_response_bytes)

    async def fetch_async(self, *, name: str, player_uuid: str) -> AttestedSession:
        return await asyncio.to_thread(self.fetch, name=name, player_uuid=player_uuid)

    def fetch(self, *, name: str, player_uuid: str) -> AttestedSession:
        if not __import__("re").fullmatch(r"[A-Za-z0-9_]{1,16}", name):
            raise AttestationError("invalid player name")
        nonce = secrets.token_hex(16)
        path = f"{self._path}/session?name={name}&nonce={nonce}"
        headers = {"X-Signature": _sign(f"{name}\n{nonce}".encode(), self._token)}
        conn = http.client.HTTPConnection(self._host, self._port, timeout=self._timeout)
        try:
            conn.request("GET", path, headers=headers)
            response = conn.getresponse()
            body = response.read(self._max + 1)
            signature = response.getheader("X-Signature")
        except Exception as exc:  # noqa: BLE001
            raise AttestationError("attestation transport failed") from exc
        finally:
            conn.close()
        if response.status != 200:
            raise AttestationError(f"attestation status {response.status}")
        if len(body) > self._max:
            raise AttestationError("attestation response too large")
        if not _verify(body, self._token, signature):
            raise AttestationError("attestation response signature invalid")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise AttestationError("attestation response not json") from exc
        if not isinstance(payload, dict):
            raise AttestationError("attestation response not object")
        if payload.get("schema") != 1:
            raise AttestationError("attestation schema")
        if payload.get("ok") is not True:
            raise AttestationError("attestation not ok")
        if payload.get("nonce") != nonce:
            raise AttestationError("attestation nonce mismatch")
        if payload.get("player_uuid") != player_uuid:
            raise AttestationError("attestation uuid mismatch")
        if payload.get("server_scope") != self._scope:
            raise AttestationError("attestation scope mismatch")
        session_ref = payload.get("session_ref")
        session_seq = payload.get("session_seq")
        boot_id = payload.get("boot_id")
        if not isinstance(session_ref, str) or not session_ref or len(session_ref) > 128:
            raise AttestationError("attestation session_ref")
        if not isinstance(session_seq, int) or isinstance(session_seq, bool) or session_seq <= 0:
            raise AttestationError("attestation session_seq")
        if not isinstance(boot_id, str) or not boot_id:
            raise AttestationError("attestation boot_id")
        return AttestedSession(
            schema=1,
            nonce=nonce,
            session_ref=session_ref,
            session_seq=session_seq,
            player_uuid=player_uuid,
            server_scope=self._scope,
            boot_id=boot_id,
            raw=body,
        )
