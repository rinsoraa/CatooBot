"""P2 修复轮 E2：接收端字段/关联校验、幂等、统计口径（纯单元 + 本地 HTTP）。"""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.integrations.minecraft.attribution_shadow import (
    ShadowLedger,
    ShadowServer,
    sign,
    validate_event,
    verify,
)


def event(**overrides: object) -> dict:
    payload = {
        "event_id": "e-1",
        "reservation_id": "r-1",
        "action_id": "act_1",
        "session_ref": "s-1",
        "session_seq": 3,
        "player_uuid": "uuid-bot",
        "dimension": "minecraft:overworld",
        "pos": "1,2,3",
        "block_before": "minecraft:oak_log",
        "block_after": "minecraft:air",
        "shadow": "SELF_CONFIRMED",
        "reason": "unique_match",
    }
    payload.update(overrides)
    return payload


def test_sign_and_verify_roundtrip() -> None:
    body = b'{"a":1}'
    assert verify(body, "token", sign(body, "token")) is True
    assert verify(body, "token", "deadbeef") is False
    assert verify(body, "token", None) is False
    assert verify(body, "", sign(body, "")) is False


def test_valid_self_confirmed_requires_all_correlation_fields() -> None:
    state, reason = validate_event(event())
    assert state == "SELF_CONFIRMED" and reason == "verified_correlation"


@pytest.mark.parametrize(
    "field",
    ["reservation_id", "session_ref", "session_seq", "player_uuid", "dimension", "action_id"],
)
def test_missing_correlation_field_downgrades_to_unknown(field: str) -> None:
    state, reason = validate_event(event(**{field: ""}))
    assert state == "UNKNOWN"
    assert reason.startswith("unverified_correlation:")


def test_missing_required_field_is_rejected() -> None:
    state, reason = validate_event(event(pos=""))
    assert state == "REJECTED" and reason == "missing_field:pos"


def test_no_world_change_downgrades() -> None:
    state, reason = validate_event(event(block_after="minecraft:oak_log"))
    assert state == "UNKNOWN" and reason == "no_world_change"


def test_non_self_states_pass_through_but_unescaped() -> None:
    assert validate_event(event(shadow="UNREGISTERED_BREAK", reason="no_reservation")) == (
        "UNREGISTERED_BREAK",
        "no_reservation",
    )
    assert validate_event(event(shadow="WAT"))[0] == "UNKNOWN"


def test_ledger_idempotent_and_stats() -> None:
    ledger = ShadowLedger()
    first = ledger.record(event())
    second = ledger.record(event())
    assert first["consumed"] is True
    assert second["consumed"] is False and second["reason"] == "duplicate_event"
    snapshot = ledger.snapshot()
    assert snapshot["total_events"] == 1
    assert snapshot["duplicate_events"] == 1
    assert snapshot["strict_match_rate"] == 1.0


def test_ledger_downgrade_is_counted() -> None:
    ledger = ShadowLedger()
    ledger.record(event(reservation_id=""))
    snapshot = ledger.snapshot()
    assert snapshot["downgraded"] == 1
    assert snapshot["shadow_states"] == {"UNKNOWN": 1}
    assert snapshot["strict_match_rate"] == 0.0


def test_unregistered_and_unknown_never_count_as_strict() -> None:
    ledger = ShadowLedger()
    ledger.record(event(event_id="a", shadow="UNREGISTERED_BREAK", reason="no_reservation"))
    ledger.record(event(event_id="b", shadow="UNKNOWN", reason="target_or_identity_mismatch"))
    snapshot = ledger.snapshot()
    assert snapshot["shadow_states"] == {"UNREGISTERED_BREAK": 1, "UNKNOWN": 1}
    assert snapshot["strict_match_rate"] == 0.0
    assert snapshot["unknown_ratio"] == 0.5


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
    url = f"http://127.0.0.1:{server.port}"
    status, body = _post(url, event())
    assert status == 200 and body["consumed"] is True and body["state"] == "SELF_CONFIRMED"
    with urllib.request.urlopen(url + "/attribution/stats", timeout=3) as response:
        stats = json.loads(response.read().decode("utf-8"))
    assert stats["total_events"] == 1


def test_server_rejects_bad_signature(server: ShadowServer) -> None:
    url = f"http://127.0.0.1:{server.port}"
    status, body = _post(url, event(), signature="00")
    assert status == 401 and body["ok"] is False
    assert server.ledger.snapshot()["rejected_signature"] == 1
    assert server.ledger.snapshot()["total_events"] == 0


def test_server_is_loopback_only(server: ShadowServer) -> None:
    assert server._server is not None  # noqa: SLF001
    assert server._server.server_address[0] == "127.0.0.1"  # noqa: SLF001
