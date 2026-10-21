"""Validate authenticated runtime projections against the current collection graph.

Transport authentication belongs to the bridge and Node verifier. This pure
consumer neither invents facts nor claims to recheck signatures it did not receive.
Conversation ownership remains separate from the Minecraft connection identity.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

from app.tasks.models import StepState, canonical_item_name
from app.tasks.resource_contract import RESOURCE_SPECS

COLLECTION_PROTOCOL = "catoobot.collection.v1"
_TOKEN = re.compile(r"[\x21-\x7e]{1,128}\Z")
_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z", re.I)
_AIR = {"minecraft:air", "minecraft:cave_air", "minecraft:void_air"}
_DIMENSIONS = {"minecraft:overworld", "minecraft:the_nether", "minecraft:the_end"}


def _integer(value: Any, minimum: int = 0, maximum: int = 2**31 - 1) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _token(value: Any) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _uuid(value: Any) -> bool:
    return isinstance(value, str) and _UUID.fullmatch(value) is not None


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _arguments(step: Any) -> Mapping[str, Any]:
    return _mapping(getattr(step, "effective_arguments", None))


def _steps(record: Any) -> list[Any]:
    return list(getattr(record, "steps", ()) or ())


def is_collection_task(record: Any) -> bool:
    steps = _steps(record)
    digs = [step for step in steps if step.tool == "minecraft_dig"]
    return bool(digs) and (
        any(_arguments(step).get("require_collection") is True for step in digs)
        or any(step.tool == "minecraft_pickup_item" for step in steps)
        or bool(
            getattr(getattr(record, "plan", None), "expected_final_state", None)
            and record.plan.expected_final_state.inventory_delta
        )
    )


def _break_reason(step: Any) -> str:
    args = _arguments(step)
    result = _mapping(getattr(step, "result", None))
    attr = _mapping(result.get("attribution"))
    proof = _mapping(attr.get("server_proof"))
    collection = _mapping(attr.get("server_collection"))
    block = canonical_item_name(args.get("expected_block"))
    spec = RESOURCE_SPECS.get(block)
    if args.get("require_collection") is not True or spec is None:
        return "contract"
    if (
        not _integer(attr.get("schema"), 3, 3)
        or attr.get("kind") != "dig_attribution"
        or attr.get("world_effect") != "BLOCK_REMOVED"
        or attr.get("attribution") != "SELF_CONFIRMED"
        or attr.get("strict_self_proof") is not True
        or attr.get("assurance") != "STRICT"
        or attr.get("confirm_basis") != "server_block_break"
        or not _token(getattr(step, "action_id", None))
        or attr.get("action_id") != step.action_id
        or not _token(attr.get("session_id"))
        or not _token(getattr(step, "dispatch_runtime_instance", None))
    ):
        return "break_attribution"
    target = _mapping(attr.get("target"))
    position = _mapping(result.get("position"))
    for axis in ("x", "y", "z"):
        lo, hi = (-512, 2048) if axis == "y" else (-30_000_000, 30_000_000)
        if (
            not _integer(args.get(axis), lo, hi)
            or not _integer(target.get(axis), lo, hi)
            or target[axis] != args[axis]
            or not _integer(position.get(axis), lo, hi)
            or position[axis] != args[axis]
        ):
            return "target"
    if (
        not _integer(proof.get("schema"), 1, 1)
        or proof.get("source") != "server_block_break"
        or proof.get("action_id") != step.action_id
        or any(not _token(proof.get(key)) for key in ("event_id", "reservation_id", "session_ref"))
        or not _integer(proof.get("session_seq"), 1, 2**53 - 1)
        or not _uuid(proof.get("player_uuid"))
        or attr.get("dimension") not in _DIMENSIONS
        or proof.get("dimension") != attr.get("dimension")
        or proof.get("pos") != ",".join(str(args[axis]) for axis in ("x", "y", "z"))
        or proof.get("block_before") != "minecraft:" + block
        or proof.get("block_after") not in _AIR
        or canonical_item_name(result.get("block_before")) != block
        or "minecraft:" + canonical_item_name(result.get("block_after")) not in _AIR
    ):
        return "break_proof"
    if (
        not _integer(collection.get("schema"), 1, 1)
        or collection.get("source") != "server_drop_evidence"
        or collection.get("collection_protocol") != COLLECTION_PROTOCOL
        or not _token(collection.get("event_id"))
        or collection.get("break_event_id") != proof["event_id"]
        or any(
            collection.get(key) != proof.get(key)
            for key in (
                "reservation_id",
                "action_id",
                "session_ref",
                "player_uuid",
                "dimension",
                "pos",
                "block_before",
            )
        )
        or not _integer(collection.get("session_seq"), 1, 2**53 - 1)
        or collection["session_seq"] != proof["session_seq"]
    ):
        return "drop_correlation"
    drops = collection.get("drops")
    if not isinstance(drops, list) or len(drops) != 1:
        return "drop_count"
    drop = _mapping(drops[0])
    if (
        not _uuid(drop.get("uuid"))
        or not _integer(drop.get("entity_id"))
        or not _integer(drop.get("count"), 1, 1)
        or drop.get("item") != "minecraft:" + spec.drop
    ):
        return "drop_identity"
    return ""


def _pickup_reason(step: Any, parent: Any) -> str:
    args = _arguments(step)
    result = _mapping(getattr(step, "result", None))
    picked = _mapping(result.get("server_attribution"))
    attr = parent.result["attribution"]
    proof, collection = attr["server_proof"], attr["server_collection"]
    drop = collection["drops"][0]
    if (
        not _integer(picked.get("schema"), 1, 1)
        or picked.get("kind") != "server_drop_pickup"
        or picked.get("collection_protocol") != COLLECTION_PROTOCOL
        or picked.get("strict_self_proof") is not True
        or not _token(getattr(step, "action_id", None))
        or picked.get("action_id") != step.action_id
        or picked.get("session_id") != attr["session_id"]
        or not _token(picked.get("booking_id"))
        or step.dispatch_runtime_instance != parent.dispatch_runtime_instance
        or picked.get("break_action_id") != parent.action_id
        or picked.get("break_event_id") != proof["event_id"]
        or picked.get("break_reservation_id") != proof["reservation_id"]
        or any(
            picked.get(key) != proof.get(key) for key in ("session_ref", "player_uuid", "dimension")
        )
        or not _integer(picked.get("session_seq"), 1, 2**53 - 1)
        or picked["session_seq"] != proof["session_seq"]
    ):
        return "pickup_correlation"
    if (
        args.get("expected_break_action") != parent.action_id
        or not _integer(args.get("entity_id"))
        or args["entity_id"] != drop["entity_id"]
        or args.get("entity_uuid") != drop["uuid"]
        or "minecraft:" + canonical_item_name(args.get("expected_item")) != drop["item"]
        or not _integer(result.get("entity_id"))
        or result["entity_id"] != drop["entity_id"]
        or result.get("entity_uuid") != drop["uuid"]
        or picked.get("drop_uuid") != drop["uuid"]
        or picked.get("item") != drop["item"]
        or "minecraft:" + canonical_item_name(_mapping(result.get("item")).get("name"))
        != drop["item"]
        or not _integer(_mapping(result.get("item")).get("count_before"), 1, 1)
        or not _integer(picked.get("count"), 1, 1)
    ):
        return "pickup_identity"
    if (
        result.get("collected") is not True
        or not _integer(result.get("collected_count"), 1, 1)
        or not all(
            _integer(picked.get(key)) and _integer(result.get(key)) and picked[key] == result[key]
            for key in ("inventory_before", "inventory_after")
        )
        or picked["inventory_after"] - picked["inventory_before"] != 1
    ):
        return "pickup_inventory"
    return ""


def collection_evidence(record: Any, *, before_step_id: str | None = None) -> dict[str, Any]:
    """Recheck every completed prefix, or the whole graph when finishing/learning.

    A prior cached verification is deliberately ignored. No world reads, retry,
    authorization, state transitions, or learning side effects occur here.
    """
    if not is_collection_task(record):
        return {"checked": False, "ok": True}
    steps = _steps(record)
    parents: dict[str, Any] = {}
    consumed: set[str] = set()
    identities: set[tuple[str, Any]] = set()
    connection: tuple[Any, ...] | None = None
    count = 0

    def failure(step: Any, reason: str) -> dict[str, Any]:
        return {
            "checked": True,
            "ok": False,
            "indeterminate": True,
            "reason": "collection.evidence." + reason,
            "step_id": step.step_id,
            "message": "采集来源证据无法完整核对，已停止后续执行",
        }

    for step in steps:
        if step.step_id == before_step_id:
            break
        if step.tool not in ("minecraft_dig", "minecraft_pickup_item"):
            continue
        if getattr(step, "state", None) is not StepState.SUCCEEDED:
            return failure(step, "step_not_succeeded")
        if step.tool == "minecraft_dig":
            reason = _break_reason(step)
            if reason:
                return failure(step, reason)
            attr = step.result["attribution"]
            proof, drop = attr["server_proof"], attr["server_collection"]["drops"][0]
            current = (
                attr["session_id"],
                proof["session_ref"],
                proof["session_seq"],
                proof["player_uuid"],
                proof["dimension"],
                step.dispatch_runtime_instance,
            )
            if connection is not None and current != connection:
                return failure(step, "connection_changed")
            connection = current
            for key, value in (
                ("action", step.action_id),
                ("reservation", proof["reservation_id"]),
                ("event", proof["event_id"]),
                ("event", attr["server_collection"]["event_id"]),
                ("drop", drop["uuid"]),
                ("entity", drop["entity_id"]),
            ):
                if (key, value) in identities:
                    return failure(step, "reused_identity")
                identities.add((key, value))
            parents[step.step_id] = step
        else:
            refs = getattr(step, "references", {})
            paths = {
                "entity_id": "attribution.server_collection.drops.0.entity_id",
                "entity_uuid": "attribution.server_collection.drops.0.uuid",
                "expected_break_action": "attribution.action_id",
            }
            ref = refs.get("expected_break_action")
            parent_id = getattr(ref, "from_step", "")
            if parent_id not in parents or any(
                key not in refs or refs[key].from_step != parent_id or refs[key].path != path
                for key, path in paths.items()
            ):
                return failure(step, "pickup_parent")
            if parent_id in consumed:
                return failure(step, "repeated_pickup")
            reason = _pickup_reason(step, parents[parent_id])
            if reason:
                return failure(step, reason)
            booking = step.result["server_attribution"]["booking_id"]
            if ("booking", booking) in identities or ("action", step.action_id) in identities:
                return failure(step, "reused_identity")
            identities.update({("booking", booking), ("action", step.action_id)})
            consumed.add(parent_id)
            count += 1
    if before_step_id is None and set(parents) != consumed:
        return failure(next(iter(parents.values())), "pickup_missing")
    return {"checked": True, "ok": True, "verified_pickups": count}
