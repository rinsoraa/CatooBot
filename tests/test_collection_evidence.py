"""R3 采集证据消费者的确定性矩阵（纯消费者 + 真实 TaskRuntime）。

覆盖任务书要求的门禁：

* 12 个受支持目标 ID 的完整 break/drop/pickup 图全部被接受；
* 缺失 / 错类型 / 未知版本 / 目标 / actor / session / action / reservation /
  event 错配 / 旧实体 / 重复贡献 / 错物品 / 错数量 / 库存不净增 / 部分完成 /
  恢复缓存否定 / 终态与取消不复活，每一类都有**稳定原因码**的确定性用例；
* 真实 TaskRuntime 集成断言：证据图不完整时**零后续派发**、任务停在非成功；
* 非采集的原子破坏（P3 语义）完全不受影响（checked=False, ok=True）；
* 消费端联动：技能资格与记忆都不接受伪成功，缺证据记为不可判定。
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

import pytest

from app.tasks.collection_evidence import collection_evidence, is_collection_task
from app.tasks.models import (
    ExpectedFinalState,
    StepState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.resource_contract import RESOURCE_SPECS
from tests.collection_facts import RUNTIME_INSTANCE
from tests.collection_facts import dig_attribution as collection_attribution
from tests.skill_fakes import (
    BLOCK,
    DROP,
    POSITION,
    FakeObserve,
    build_task_runtime,
    make_service,
    resource_plan,
    resource_record,
)

#: 消费者返回的稳定原因码前缀。
REASON = "collection.evidence."


def _record(block: str = BLOCK, drop: str = DROP, **kwargs: Any) -> Any:
    return resource_record(block=block, drop=drop, **kwargs)


def _step(record: Any, tool: str) -> Any:
    return next(step for step in record.steps if step.tool == tool)


def _attribution(record: Any) -> dict[str, Any]:
    return _step(record, "minecraft_dig").result["attribution"]


def _pickup_result(record: Any) -> dict[str, Any]:
    return _step(record, "minecraft_pickup_item").result


def _reason(record: Any, **kwargs: Any) -> str:
    verdict = collection_evidence(record, **kwargs)
    assert verdict["checked"] is True
    assert verdict["ok"] is False
    assert verdict["indeterminate"] is True
    return str(verdict["reason"])


# ------------------------------------------------------------------ 12 ID 正向


@pytest.mark.parametrize(
    ("block", "drop"),
    [(spec.block, spec.drop) for spec in RESOURCE_SPECS.values()],
    ids=[spec.block for spec in RESOURCE_SPECS.values()],
)
def test_every_supported_target_id_has_a_complete_graph(block: str, drop: str) -> None:
    record = _record(block=block, drop=drop)
    assert is_collection_task(record) is True
    verdict = collection_evidence(record)
    assert verdict == {"checked": True, "ok": True, "verified_pickups": 1}


def test_generic_wood_task_is_a_collection_task() -> None:
    record = _record()
    assert is_collection_task(record) is True
    assert collection_evidence(record)["ok"] is True


# ------------------------------------------------------------------ 负向矩阵

MUTATIONS: dict[str, tuple[Callable[[Any], None], str]] = {}


def mutation(name: str, reason: str) -> Callable[[Callable[[Any], None]], Callable[[Any], None]]:
    def register(fn: Callable[[Any], None]) -> Callable[[Any], None]:
        MUTATIONS[name] = (fn, REASON + reason)
        return fn

    return register


@mutation("schema_2", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(schema=2)


@mutation("schema_unknown", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(schema=99)


@mutation("missing_version", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).pop("schema")


@mutation("strict_self_proof_false", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(strict_self_proof=False)


@mutation("strict_self_proof_string", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(strict_self_proof="true")


@mutation("legacy_confirm_basis", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(confirm_basis="dig_lifecycle_timing")


@mutation("world_effect_not_removed", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(world_effect="BLOCK_REMAINS")


@mutation("external_attribution", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).update(attribution="EXTERNAL_INDICATED")


@mutation("missing_mc_session_id", "break_attribution")
def _(record: Any) -> None:
    _attribution(record).pop("session_id")


@mutation("missing_dispatch_instance", "break_attribution")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").dispatch_runtime_instance = ""


@mutation("action_id_not_on_step", "break_attribution")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").action_id = ""


@mutation("target_x_changed", "target")
def _(record: Any) -> None:
    _attribution(record)["target"]["x"] += 1


@mutation("result_position_changed", "target")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").result["position"]["y"] += 1


@mutation("planned_args_changed", "target")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").arguments["x"] += 1


@mutation("proof_schema_unknown", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["schema"] = 2


@mutation("proof_pos_changed", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["pos"] = "0,0,0"


@mutation("proof_missing", "break_proof")
def _(record: Any) -> None:
    _attribution(record).pop("server_proof")


@mutation("proof_actor_uuid", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["player_uuid"] = "not-a-uuid"


@mutation("proof_session_seq_bool", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["session_seq"] = True


@mutation("proof_reservation_missing", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["reservation_id"] = ""


@mutation("proof_block_after", "break_proof")
def _(record: Any) -> None:
    _attribution(record)["server_proof"]["block_after"] = "minecraft:stone"


@mutation("result_block_before", "break_proof")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").result["block_before"] = "stone"


@mutation("dimension_unknown", "break_proof")
def _(record: Any) -> None:
    _attribution(record).update(dimension="minecraft:moon")


@mutation("collection_protocol", "drop_correlation")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["collection_protocol"] = "catoobot.collection.v2"


@mutation("break_event_mismatch", "drop_correlation")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["break_event_id"] = (
        "00000000-0000-4000-8000-000000000000"
    )


@mutation("collection_pos_changed", "drop_correlation")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["pos"] = "1,2,3"


@mutation("collection_session_seq_changed", "drop_correlation")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["session_seq"] += 1


@mutation("two_drops", "drop_count")
def _(record: Any) -> None:
    drops = _attribution(record)["server_collection"]["drops"]
    drops.append(dict(drops[0], uuid="22222222-2222-4222-8222-222222222222", entity_id=221))


@mutation("no_drops", "drop_count")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["drops"] = []


@mutation("drop_count_two", "drop_identity")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["drops"][0]["count"] = 2


@mutation("drop_item_mismatch", "drop_identity")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["drops"][0]["item"] = "minecraft:oak_planks"


@mutation("drop_entity_bool", "drop_identity")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["drops"][0]["entity_id"] = True


@mutation("drop_uuid_malformed", "drop_identity")
def _(record: Any) -> None:
    _attribution(record)["server_collection"]["drops"][0]["uuid"] = "not-a-uuid"


@mutation("pickup_break_action_mismatch", "pickup_correlation")
def _(record: Any) -> None:
    _pickup_result(record)["server_attribution"]["break_action_id"] = "act_other"


@mutation("pickup_connection_mismatch", "pickup_correlation")
def _(record: Any) -> None:
    _pickup_result(record)["server_attribution"]["session_id"] = "mc_other"


@mutation("pickup_instance_mismatch", "pickup_correlation")
def _(record: Any) -> None:
    _step(record, "minecraft_pickup_item").dispatch_runtime_instance = "rt_other"


@mutation("pickup_missing_attribution", "pickup_correlation")
def _(record: Any) -> None:
    _pickup_result(record).pop("server_attribution")


@mutation("pickup_parent_missing", "pickup_parent")
def _(record: Any) -> None:
    _step(record, "minecraft_pickup_item").references["entity_id"].from_step = "step_2"


@mutation("pickup_entity_mismatch", "pickup_identity")
def _(record: Any) -> None:
    _step(record, "minecraft_pickup_item").resolved_arguments["entity_id"] = 999


@mutation("pickup_item_mismatch", "pickup_identity")
def _(record: Any) -> None:
    _pickup_result(record)["item"]["name"] = "oak_planks"


@mutation("pickup_count_two", "pickup_identity")
def _(record: Any) -> None:
    _pickup_result(record)["server_attribution"]["count"] = 2


@mutation("pickup_item_count_before", "pickup_identity")
def _(record: Any) -> None:
    _pickup_result(record)["item"]["count_before"] = 2


@mutation("inventory_not_increased", "pickup_inventory")
def _(record: Any) -> None:
    payload = _pickup_result(record)
    payload["inventory_before"] = 1
    payload["inventory_after"] = 1
    payload["server_attribution"]["inventory_before"] = 1
    payload["server_attribution"]["inventory_after"] = 1


@mutation("not_collected", "pickup_inventory")
def _(record: Any) -> None:
    _pickup_result(record)["collected"] = False


@mutation("dig_not_succeeded", "step_not_succeeded")
def _(record: Any) -> None:
    _step(record, "minecraft_dig").state = StepState.FAILED


@mutation("cancelled_pickup", "step_not_succeeded")
def _(record: Any) -> None:
    _step(record, "minecraft_pickup_item").state = StepState.CANCELLED


@pytest.mark.parametrize("name", sorted(MUTATIONS))
def test_counterexample_is_rejected_with_a_stable_reason(name: str) -> None:
    mutate, expected = MUTATIONS[name]
    record = _record()
    mutate(record)
    assert _reason(record) == expected


# ------------------------------------------------------------------ 图级规则


def test_missing_pickup_is_not_a_success_at_the_terminal_check() -> None:
    plan = resource_plan()
    plan.steps = [step for step in plan.steps if step.tool != "minecraft_pickup_item"]
    record = _record(plan=plan)
    assert _reason(record) == REASON + "pickup_missing"


def test_second_pickup_of_the_same_drop_is_a_repeated_contribution() -> None:
    record = _record()
    pickup = _step(record, "minecraft_pickup_item")
    duplicate = copy.deepcopy(pickup)
    duplicate.step_id = "step_5b"
    index = record.steps.index(pickup)
    record.steps.insert(index + 1, duplicate)
    assert _reason(record) == REASON + "repeated_pickup"


def test_pickup_reusing_the_break_action_identity_is_rejected() -> None:
    record = _record()
    dig = _step(record, "minecraft_dig")
    pickup = _step(record, "minecraft_pickup_item")
    pickup.action_id = dig.action_id
    pickup.result["server_attribution"]["action_id"] = dig.action_id
    assert _reason(record) == REASON + "reused_identity"


def test_completed_prefix_is_accepted_before_the_pickup_runs() -> None:
    record = _record()
    pickup = _step(record, "minecraft_pickup_item")
    pickup.state = StepState.PENDING
    pickup.result = {}
    verdict = collection_evidence(record, before_step_id=pickup.step_id)
    assert verdict == {"checked": True, "ok": True, "verified_pickups": 0}


def test_stale_cached_verification_never_skips_the_recheck() -> None:
    record = _record()
    record.result["collection_verification"] = {"checked": True, "ok": True}
    _attribution(record)["server_proof"]["player_uuid"] = "not-a-uuid"
    assert _reason(record) == REASON + "break_proof"


def test_cancelled_collection_task_never_revives() -> None:
    record = _record()
    record.state = TaskState.CANCELLED
    _step(record, "minecraft_pickup_item").state = StepState.CANCELLED
    assert _reason(record) == REASON + "step_not_succeeded"
    assert collection_evidence(record)["ok"] is False


def test_non_collection_task_is_a_strict_no_op() -> None:
    plan = TaskPlan(
        objective="挖一块石头（不采集）",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_dig",
                arguments={**POSITION, "expected_block": BLOCK},
                risk="MEDIUM",
                state=StepState.SUCCEEDED,
            )
        ],
        expected_final_state=ExpectedFinalState(),
    )
    record = resource_record(plan=plan)
    assert is_collection_task(record) is False
    assert collection_evidence(record) == {"checked": False, "ok": True}


# ------------------------------------------------------------------ 真实运行时集成


def _controlled_runtime(calls: list[str]) -> Any:
    """真 TaskRuntime：dig/pickup 是持续型动作，其余同步成功；记录每次派发的工具名。"""

    from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
    from app.tasks.store import InMemoryTaskStore
    from tests.skill_fakes import RISKS, SCHEMAS, TOOLS, FakeConfirmations

    async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(tool)
        if tool == "minecraft_dig":
            return TaskInvocation(
                ok=True, status="RUNNING", action_id="act_dig_1", runtime_instance=RUNTIME_INSTANCE
            )
        if tool == "minecraft_pickup_item":
            return TaskInvocation(
                ok=True, status="RUNNING", action_id="act_pick_1", runtime_instance=RUNTIME_INSTANCE
            )
        if tool == "minecraft_inventory":
            return TaskInvocation(
                ok=True, status="SUCCEEDED", result={"items": [], "held_item": None}
            )
        return TaskInvocation(ok=True, status="SUCCEEDED", result={})

    return TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=FakeConfirmations(),
        config=TaskConfig(ttl_seconds=600.0),
        risk_of=lambda tool: str(RISKS.get(tool, "")),
        is_registered=lambda tool: tool in TOOLS,
        schema_of=SCHEMAS.get,
    )


async def test_real_runtime_completes_a_fully_evidenced_collection_task() -> None:
    runtime = build_task_runtime()
    record = await runtime.create_task(
        "去挖一块橡木并捡回来",
        session_id="private:1",
        user_id="1",
        origin="user",
        plan=resource_plan(),
    )
    done = await runtime.confirm_and_start(
        record.task_id, user_id="1", session_id="private:1", origin="user"
    )
    assert done.state is TaskState.SUCCEEDED
    dig = next(step for step in done.steps if step.tool == "minecraft_dig")
    pickup = next(step for step in done.steps if step.tool == "minecraft_pickup_item")
    assert dig.result["attribution"]["schema"] == 3
    assert pickup.result["server_attribution"]["break_action_id"] == dig.action_id
    assert collection_evidence(done)["ok"] is True


async def test_incomplete_drop_graph_stops_before_any_later_dispatch() -> None:
    calls: list[str] = []
    runtime = _controlled_runtime(calls)
    record = await runtime.create_task(
        "去挖一块橡木并捡回来",
        session_id="private:1",
        user_id="1",
        origin="user",
        plan=resource_plan(),
    )
    started = await runtime.confirm_and_start(
        record.task_id, user_id="1", session_id="private:1", origin="user"
    )
    assert started.state is TaskState.WAITING_ACTION
    action_id = started.pending_action_id
    payload = collection_attribution(position=POSITION, block=BLOCK, action_id=action_id, item=DROP)
    payload["server_collection"]["drops"][0]["item"] = "minecraft:oak_planks"
    await runtime.on_action_event(
        action_id=action_id,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={
            "position": dict(POSITION),
            "block_before": BLOCK,
            "block_after": "air",
            "attribution": payload,
        },
    )
    final = await runtime.get(record.task_id)
    assert final is not None
    assert final.state is TaskState.FAILED
    assert final.failure == TaskFailure.VERIFICATION.value
    assert "minecraft_dropped_items" not in calls
    assert "minecraft_pickup_item" not in calls
    assert final.result["collection_verification"]["reason"] == REASON + "drop_identity"


async def test_atomic_non_collection_dig_task_is_untouched() -> None:
    calls: list[str] = []
    runtime = _controlled_runtime(calls)
    plan = TaskPlan(
        objective="挖一块石头",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_dig",
                arguments={**POSITION, "expected_block": BLOCK},
                risk="MEDIUM",
            )
        ],
        expected_final_state=ExpectedFinalState(),
    )
    record = await runtime.create_task(
        "挖一块石头", session_id="private:1", user_id="1", origin="user", plan=plan
    )
    started = await runtime.confirm_and_start(
        record.task_id, user_id="1", session_id="private:1", origin="user"
    )
    assert started.state is TaskState.WAITING_ACTION
    legacy = {
        "kind": "dig_attribution",
        "schema": 2,
        "action_id": started.pending_action_id,
        "target": dict(POSITION),
        "dimension": "minecraft:overworld",
        "world_effect": "BLOCK_REMOVED",
        "attribution": "SELF_CONFIRMED",
        "assurance": "STRICT",
        "confirm_basis": "self_break_progress",
        "reason_code": "self_break_progress_observed",
        "strict_self_proof": True,
    }
    await runtime.on_action_event(
        action_id=started.pending_action_id,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result={
            "position": dict(POSITION),
            "block_before": BLOCK,
            "block_after": "air",
            "attribution": legacy,
        },
    )
    final = await runtime.get(record.task_id)
    assert final is not None
    assert final.state is TaskState.SUCCEEDED
    assert final.result.get("collection_verification") is None


# ------------------------------------------------------------------ 消费端联动


async def test_skill_eligibility_rejects_pseudo_success() -> None:
    record = _record()
    _attribution(record)["server_collection"]["drops"][0]["item"] = "minecraft:oak_planks"
    service = make_service(observe=FakeObserve())
    out = await service.on_task_finished(record)
    assert out["action"] == "rejected"
    assert out["reason"] == "collection_evidence_unproven:step_3"
    assert out["verdict"] == "AMBIGUOUS"


async def test_missing_drop_graph_is_indeterminate_not_a_counterexample() -> None:
    record = _record()
    _pickup_result(record).pop("server_attribution")
    service = make_service(observe=FakeObserve())
    out = await service.on_task_finished(record)
    assert out["action"] == "rejected"
    assert out["reason"] == "collection_evidence_unproven:step_5"
    assert out["verdict"] == "AMBIGUOUS"
    assert service.ambiguous_count == 1


async def test_legitimate_evidence_still_feeds_the_existing_learning_flow() -> None:
    service = make_service(observe=FakeObserve())
    first = await service.on_task_finished(_record())
    second = await service.on_task_finished(_record(task_id="task_skill_2"))
    assert first["action"] in {"candidate", "learned"}
    assert second["action"] in {"candidate", "learned", "promoted"}
