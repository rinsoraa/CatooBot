"""P2 影子接收端 E2：认证、幂等、统计、状态归一（纯单元，不触网/不碰产品终态）。"""

from __future__ import annotations

import json
import urllib.request

import pytest

from app.integrations.minecraft.attribution_shadow import (
    ShadowLedger,
    ShadowServer,
    sign,
    verify,
)


def test_sign_and_verify_roundtrip() -> None:
    body = b'{"a":1}'
    assert verify(body, "token", sign(body, "token")) is True
    assert verify(body, "token", "deadbeef") is False
    assert verify(body, "token", None) is False
    assert verify(body, "", sign(body, "")) is False


def test_ledger_consumes_once_and_counts_duplicates() -> None:
    ledger = ShadowLedger()
    payload = {"event_id": "e1", "shadow": "SELF_CONFIRMED", "reason": "unique_match"}
    first = ledger.record(payload)
    second = ledger.record(payload)
    assert first["consumed"] is True
    assert second["consumed"] is False and second["reason"] == "duplicate_event"
    snapshot = ledger.snapshot()
    assert snapshot["duplicate_events"] == 1
    assert snapshot["total_events"] == 1


def test_ledger_normalizes_unknown_states() -> None:
    ledger = ShadowLedger()
    ledger.record({"event_id": "e2", "shadow": "WHATEVER"})
    assert ledger.snapshot()["shadow_states"] == {"UNKNOWN": 1}


def test_ledger_statistics_match_rate_and_unknown_ratio() -> None:
    ledger = ShadowLedger()
    ledger.record({"event_id": "a", "shadow": "SELF_CONFIRMED"})
    ledger.record({"event_id": "b", "shadow": "SELF_CONFIRMED"})
    ledger.record({"event_id": "c", "shadow": "UNKNOWN"})
    ledger.record({"event_id": "d", "shadow": "UNREGISTERED_BREAK"})
    snapshot = ledger.snapshot()
    assert snapshot["total_events"] == 4
    assert snapshot["match_rate"] == round(2 / 4, 4)
    assert snapshot["unknown_ratio"] == round(1 / 4, 4)


def test_unknown_is_never_counted_as_external_or_self() -> None:
    ledger = ShadowLedger()
    ledger.record({"event_id": "x", "shadow": "UNKNOWN", "reason": "no_valid_session"})
    snapshot = ledger.snapshot()
    assert snapshot["shadow_states"].get("UNKNOWN") == 1
    assert "SELF_CONFIRMED" not in snapshot["shadow_states"]
    assert "EXTERNAL_CONFIRMED" not in snapshot["shadow_states"]


@pytest.fixture()
def server() -> ShadowServer:
    instance = ShadowServer(token="secret", port=0)
    instance.start()
    yield instance
    instance.stop()


def _post(
    url: str, payload: dict, token: str = "secret", signature: str | None = None
) -> tuple[int, dict]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url + "/attribution/event",
        data=body,
        headers={"Content-Type": "application/json", "X-Signature": signature or sign(body, token)},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def test_server_accepts_signed_event_and_exposes_stats(server: ShadowServer) -> None:
    port = server.port
    url = f"http://127.0.0.1:{port}"
    status, body = _post(url, {"event_id": "s1", "shadow": "SELF_CONFIRMED"})
    assert status == 200 and body["consumed"] is True
    with urllib.request.urlopen(url + "/attribution/stats", timeout=3) as response:
        stats = json.loads(response.read().decode("utf-8"))
    assert stats["total_events"] == 1


def test_server_rejects_bad_signature(server: ShadowServer) -> None:
    url = f"http://127.0.0.1:{server.port}"
    status, body = _post(url, {"event_id": "bad"}, signature="00")
    assert status == 401 and body["ok"] is False
    assert server.ledger.snapshot()["rejected_signature"] == 1
    assert server.ledger.snapshot()["total_events"] == 0


def test_server_is_loopback_only(server: ShadowServer) -> None:
    assert server._server is not None  # noqa: SLF001
    assert server._server.server_address[0] == "127.0.0.1"  # noqa: SLF001
