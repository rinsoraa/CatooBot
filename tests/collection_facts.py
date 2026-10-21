"""Canonical collection-evidence fixtures (schema 3) for tests.

The shapes here are copied field-for-field from the 24 real-server normalized
product records in data/r3/protocol-20261020/new-01/product-results.json:
a signed session, a schema-3 server_block_break proof, exactly ONE server drop,
and a pickup whose resolved arguments and server_attribution point back at that
same drop.

Nothing here takes a shortcut the product does not take. A test that wants a
rejection mutates exactly one field of these dictionaries; a test that wants a
success uses them as-is. Conversation identity (task session / user) stays
separate from the Minecraft connection identity on purpose.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

COLLECTION_PROTOCOL = "catoobot.collection.v1"
DIMENSION = "minecraft:overworld"

#: The signed Minecraft connection identity shared by the break and the pickup.
SESSION_ID = "mc_fixture_0001"
SESSION_REF = "e8ce7777-692c-480d-82b2-bc81aa2e0813"
SESSION_SEQ = 1
PLAYER_UUID = "62c3af8a-e005-3312-9813-dda3bdf67214"
RUNTIME_INSTANCE = "rt_fixture_0001"
RESERVATION_ID = "f1fd8f75-b0e6-4051-9db2-743fed0607bc"
BREAK_EVENT_ID = "e5e46547-721b-40c6-8f71-1af28783bcee"
DROP_EVENT_ID = "48a17ff5-052b-40e5-9139-bae52bdc9ed3"
DROP_UUID = "8178f677-e990-4420-9c0e-97deb01d25d7"
DROP_ENTITY_ID = 220
BOOKING_ID = "d75d9d7c-2dff-42ff-9313-dbbff4665a50"

AXES = ("x", "y", "z")


def minecraft_name(name: Any) -> str:
    """Namespaced form (product payloads carry minecraft:<id> for blocks/items)."""

    clean = str(name or "").strip().lower()
    return clean if clean.startswith("minecraft:") else "minecraft:" + clean


def bare_name(name: Any) -> str:
    """Bare form (task arguments and canonical_item_name semantics)."""

    clean = str(name or "").strip().lower()
    return clean.removeprefix("minecraft:")


def _position(position: Mapping[str, Any]) -> dict[str, int]:
    return {axis: int(position[axis]) for axis in AXES}


def _pos_text(position: Mapping[str, Any]) -> str:
    spot = _position(position)
    return ",".join(str(spot[axis]) for axis in AXES)


def break_proof(
    *,
    position: Mapping[str, Any],
    block: str,
    action_id: str,
    event_id: str = BREAK_EVENT_ID,
    reservation_id: str = RESERVATION_ID,
    session_ref: str = SESSION_REF,
    session_seq: int = SESSION_SEQ,
    player_uuid: str = PLAYER_UUID,
    dimension: str = DIMENSION,
) -> dict[str, Any]:
    """Server block-break proof (server_proof, schema 1)."""

    spot = _position(position)
    return {
        "schema": 1,
        "source": "server_block_break",
        "event_id": event_id,
        "reservation_id": reservation_id,
        "action_id": action_id,
        "session_ref": session_ref,
        "session_seq": session_seq,
        "player_uuid": player_uuid,
        "dimension": dimension,
        "pos": _pos_text(spot),
        "block_before": minecraft_name(block),
        "block_after": "minecraft:air",
    }


def drop_evidence(
    *,
    position: Mapping[str, Any],
    block: str,
    action_id: str,
    item: str,
    drop_uuid: str = DROP_UUID,
    entity_id: int = DROP_ENTITY_ID,
    count: int = 1,
    event_id: str = DROP_EVENT_ID,
    break_event_id: str = BREAK_EVENT_ID,
    reservation_id: str = RESERVATION_ID,
    session_ref: str = SESSION_REF,
    session_seq: int = SESSION_SEQ,
    player_uuid: str = PLAYER_UUID,
    dimension: str = DIMENSION,
) -> dict[str, Any]:
    """Server drop evidence (server_collection, schema 1, exactly one drop)."""

    spot = _position(position)
    return {
        "schema": 1,
        "source": "server_drop_evidence",
        "collection_protocol": COLLECTION_PROTOCOL,
        "event_id": event_id,
        "break_event_id": break_event_id,
        "reservation_id": reservation_id,
        "action_id": action_id,
        "session_ref": session_ref,
        "session_seq": session_seq,
        "player_uuid": player_uuid,
        "dimension": dimension,
        "pos": _pos_text(spot),
        "block_before": minecraft_name(block),
        "drops": [
            {
                "uuid": drop_uuid,
                "entity_id": entity_id,
                "item": minecraft_name(item),
                "count": count,
            }
        ],
    }


def dig_attribution(
    *,
    position: Mapping[str, Any],
    block: str,
    action_id: str,
    item: str | None = None,
    drop_uuid: str = DROP_UUID,
    entity_id: int = DROP_ENTITY_ID,
    session_id: str = SESSION_ID,
    dimension: str = DIMENSION,
    **overrides: Any,
) -> dict[str, Any]:
    """Complete schema-3 dig attribution (strict self proof + break + drop graph)."""

    spot = _position(position)
    payload: dict[str, Any] = {
        "kind": "dig_attribution",
        "schema": 3,
        "action_id": action_id,
        "session_id": session_id,
        "target": spot,
        "dimension": dimension,
        "world_effect": "BLOCK_REMOVED",
        "attribution": "SELF_CONFIRMED",
        "assurance": "STRICT",
        "confirm_basis": "server_block_break",
        "strict_self_proof": True,
        "reason_code": "unique_authenticated_server_event",
        "server_proof": break_proof(
            position=spot, block=block, action_id=action_id, dimension=dimension
        ),
        "server_collection": drop_evidence(
            position=spot,
            block=block,
            action_id=action_id,
            item=item or block,
            drop_uuid=drop_uuid,
            entity_id=entity_id,
            dimension=dimension,
        ),
    }
    payload.update(overrides)
    return payload


def pickup_arguments(*, attribution: Mapping[str, Any], item: str) -> dict[str, Any]:
    """Resolved pickup arguments (the shape the product runtime hands to the tool)."""

    collection = attribution["server_collection"]
    drop = collection["drops"][0]
    return {
        "entity_id": drop["entity_id"],
        "entity_uuid": drop["uuid"],
        "expected_break_action": attribution["action_id"],
        "expected_item": bare_name(item),
    }


def pickup_result(
    *,
    attribution: Mapping[str, Any],
    action_id: str,
    item: str,
    inventory_before: int = 0,
    booking_id: str = BOOKING_ID,
    entity_id: int | None = None,
    drop_uuid: str | None = None,
) -> dict[str, Any]:
    """Pickup result: top-level inventory numbers + attribution tied to that break."""

    collection = attribution["server_collection"]
    proof = attribution["server_proof"]
    drop = collection["drops"][0]
    picked_entity = drop["entity_id"] if entity_id is None else entity_id
    picked_uuid = drop["uuid"] if drop_uuid is None else drop_uuid
    inventory_after = inventory_before + int(drop["count"])
    return {
        "entity_id": picked_entity,
        "entity_uuid": picked_uuid,
        "item": {"name": bare_name(item), "count_before": int(drop["count"])},
        "distance_start": 3.1,
        "distance_collected": None,
        "inventory_before": inventory_before,
        "inventory_after": inventory_after,
        "collected_count": int(drop["count"]),
        "collected": True,
        "server_attribution": {
            "kind": "server_drop_pickup",
            "schema": 1,
            "action_id": action_id,
            "session_id": attribution["session_id"],
            "collection_protocol": COLLECTION_PROTOCOL,
            "session_ref": proof["session_ref"],
            "session_seq": proof["session_seq"],
            "player_uuid": proof["player_uuid"],
            "dimension": proof["dimension"],
            "drop_uuid": picked_uuid,
            "item": drop["item"],
            "count": int(drop["count"]),
            "booking_id": booking_id,
            "break_event_id": proof["event_id"],
            "break_reservation_id": proof["reservation_id"],
            "break_action_id": attribution["action_id"],
            "inventory_before": inventory_before,
            "inventory_after": inventory_after,
            "strict_self_proof": True,
            "reason_code": "exact_spawn_pickup_and_inventory_delta",
        },
    }
