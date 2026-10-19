from __future__ import annotations

from copy import deepcopy
from types import SimpleNamespace

import pytest

from app.tasks.skill_learning import dig_attribution, self_dig_confirmed


def step():
    arguments = {"x": 1, "y": 64, "z": 3, "expected_block": "oak_log"}
    payload = {
        "kind": "dig_attribution",
        "schema": 3,
        "action_id": "act_rt_test",
        "session_id": "mc_test",
        "target": {"x": 1, "y": 64, "z": 3},
        "dimension": "minecraft:overworld",
        "world_effect": "BLOCK_REMOVED",
        "attribution": "SELF_CONFIRMED",
        "assurance": "STRICT",
        "confirm_basis": "server_block_break",
        "strict_self_proof": True,
        "server_proof": {
            "schema": 1,
            "source": "server_block_break",
            "event_id": "event-1",
            "reservation_id": "reservation-1",
            "action_id": "act_rt_test",
            "session_ref": "ref-1",
            "session_seq": 1,
            "player_uuid": "uuid-1",
            "dimension": "minecraft:overworld",
            "pos": "1,64,3",
            "block_before": "minecraft:oak_log",
            "block_after": "minecraft:air",
        },
    }
    return SimpleNamespace(
        action_id="act_rt_test",
        arguments=arguments,
        result={"block_before": "oak_log", "block_after": "air", "attribution": payload},
    )


def test_complete_server_proof_is_eligible_but_does_not_assert_inventory():
    current = step()
    parsed = dig_attribution(current)
    assert parsed and parsed["server_proof"]["event_id"] == "event-1"
    assert self_dig_confirmed(current)
    assert "inventory_delta" not in parsed


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", True),
        ("source", "timing"),
        ("event_id", ""),
        ("reservation_id", None),
        ("session_ref", ""),
        ("player_uuid", 4),
        ("session_seq", True),
        ("session_seq", 0),
        ("action_id", "wrong"),
        ("dimension", "minecraft:the_nether"),
        ("pos", "2,64,3"),
        ("block_before", "minecraft:stone"),
        ("block_after", "minecraft:water"),
    ],
)
def test_incomplete_or_mismatched_server_fields_fail_closed(field, value):
    current = step()
    current.result["attribution"]["server_proof"][field] = value
    assert not self_dig_confirmed(current)


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema", 4),
        ("session_id", ""),
        ("assurance", "INFERRED"),
        ("confirm_basis", "dig_lifecycle_timing"),
        ("strict_self_proof", "true"),
        ("dimension", "custom:world"),
        ("target", {"x": True, "y": 64, "z": 3}),
    ],
)
def test_payload_does_not_promote_loose_or_legacy_evidence(field, value):
    current = step()
    current.result["attribution"][field] = value
    assert not self_dig_confirmed(current)


def test_current_step_target_and_action_must_match_frozen_proof():
    original = step()
    for field, value in (("x", 2), ("x", True), ("x", 1.0), ("expected_block", "stone")):
        current = deepcopy(original)
        current.arguments[field] = value
        assert not self_dig_confirmed(current)
    original.action_id = "other_action"
    assert not self_dig_confirmed(original)


def test_resolved_arguments_are_the_execution_target_for_template_steps():
    current = step()
    current.effective_arguments = dict(current.arguments)
    current.arguments = {"x": {"ref": "step_1.x"}}
    assert self_dig_confirmed(current)
