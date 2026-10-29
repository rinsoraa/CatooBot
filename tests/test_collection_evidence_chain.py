"""R3 G2：采集证据链的跨代 / 恢复 / 重放 / 竞争收口（真实 TaskRuntime + 真实 SQLite）。

本文件补的是上一检查点（纯消费者字段级矩阵）之外的部分：

* 跨代：重规划后旧计划的 dig 证据与旧 pickup 事件都不能为新计划提供成功；
* 恢复：真实 SQLite 持久化记录 + 旧"验证成功"缓存，恢复后仍以当前证据重算；
* 重放：重复的 dig / pickup 终态事件只结算一次，且不能改写已落库的来源证据；
* 竞争：pickup 在途时取消，迟到终态事件不得复活终态、不得产生正向学习；
* 结构性一致性：全仓只有 app/tasks/collection_evidence.py 定义采集证据判决，
  Node 侧只有 minecraft_runtime/strict_attribution.js 消费服务端采集证据。
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.collection_evidence import collection_evidence
from app.tasks.models import StepState, TaskState
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore, SqliteTaskStore
from tests.collection_facts import RUNTIME_INSTANCE, dig_attribution, pickup_result
from tests.skill_fakes import (
    BLOCK,
    DROP,
    POSITION,
    RISKS,
    SCHEMAS,
    TOOLS,
    FakeConfirmations,
    FakeObserve,
    make_service,
    resource_plan,
    resource_record,
)

REASON = "collection.evidence."
OBJECTIVE = "去挖一块橡木并捡回来"


def _dig_payload(action_id: str, *, item: str = DROP) -> dict[str, Any]:
    return dig_attribution(position=POSITION, block=BLOCK, action_id=action_id, item=item)


def _dig_result(action_id: str, *, item: str = DROP) -> dict[str, Any]:
    return {
        "position": dict(POSITION),
        "block_before": BLOCK,
        "block_after": "air",
        "attribution": _dig_payload(action_id, item=item),
    }


def _pickup_payload(action_id: str, *, dig_action: str, item: str = DROP) -> dict[str, Any]:
    attribution = _dig_payload(dig_action, item=item)
    return pickup_result(attribution=attribution, action_id=action_id, item=item)


class _Sequence:
    """按派发顺序给出稳定的动作 id（真实 runtime 里由外部动作通道生成）。"""

    def __init__(self) -> None:
        self.dig = 0
        self.pickup = 0

    def next_dig(self) -> str:
        self.dig += 1
        return f"act_dig_{self.dig}"

    def next_pickup(self) -> str:
        self.pickup += 1
        return f"act_pick_{self.pickup}"


def _runtime(calls: list[str], *, store: Any = None) -> TaskRuntime:
    sequence = _Sequence()
    inventory_reads = {"n": 0}

    async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        calls.append(tool)
        if tool == "minecraft_dig":
            return TaskInvocation(
                ok=True,
                status="RUNNING",
                action_id=sequence.next_dig(),
                runtime_instance=RUNTIME_INSTANCE,
            )
        if tool == "minecraft_pickup_item":
            return TaskInvocation(
                ok=True,
                status="RUNNING",
                action_id=sequence.next_pickup(),
                runtime_instance=RUNTIME_INSTANCE,
            )
        if tool == "minecraft_inventory":
            inventory_reads["n"] += 1
            items = [] if inventory_reads["n"] == 1 else [{"name": DROP, "count": 1}]
            return TaskInvocation(
                ok=True, status="SUCCEEDED", result={"items": items, "held_item": None}
            )
        return TaskInvocation(ok=True, status="SUCCEEDED", result={})

    return TaskRuntime(
        store=store if store is not None else InMemoryTaskStore(),
        invoke=invoke,
        confirmations=FakeConfirmations(),
        config=TaskConfig(ttl_seconds=600.0),
        risk_of=lambda tool: str(RISKS.get(tool, "")),
        is_registered=lambda tool: tool in TOOLS,
        schema_of=SCHEMAS.get,
    )


async def _start_collection(runtime: TaskRuntime, *, session: str = "private:1") -> Any:
    record = await runtime.create_task(
        OBJECTIVE, session_id=session, user_id="1", origin="user", plan=resource_plan()
    )
    return await runtime.confirm_and_start(
        record.task_id, user_id="1", session_id=session, origin="user"
    )


async def _to_pickup(runtime: TaskRuntime, started: Any) -> Any:
    """把任务推进到"pickup 已派发、等在途终态"的位置。"""

    dig_action = started.pending_action_id
    assert dig_action, "dig 应该已经作为持续型动作派发"
    record = await runtime.on_action_event(
        action_id=dig_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=_dig_result(dig_action),
    )
    assert record is not None
    assert record.state is TaskState.WAITING_ACTION
    assert record.pending_action_id and record.pending_action_id.startswith("act_pick_")
    return record


# ------------------------------------------------------------------ 跨代


async def test_replan_cannot_reuse_the_previous_plans_dig_evidence() -> None:
    calls: list[str] = []
    runtime = _runtime(calls)
    started = await _start_collection(runtime)
    old_dig = started.pending_action_id
    record = await _to_pickup(runtime, started)
    before_calls = list(calls)
    old_pickup = record.pending_action_id
    old_hash = record.plan_hash

    new_plan = resource_plan(position={"x": -900, "y": 70, "z": 600})
    replanned = await runtime.replan(record.task_id, new_plan, reason="user_request")
    # 新计划挂起等确认（重规划已完成、新确认尚未发生）
    assert replanned.state is TaskState.PENDING_CONFIRMATION
    assert replanned.plan_hash != old_hash
    assert replanned.pending_action_id == ""
    assert all(step.state is StepState.PENDING for step in replanned.steps)
    # 重规划本身不产生新的世界动作
    assert calls == before_calls

    # 旧计划的证据不能为新计划提供成功：新计划的 dig 还一步没跑
    verdict = collection_evidence(replanned)
    assert verdict["checked"] is True
    assert verdict["ok"] is False
    assert verdict["reason"] == REASON + "step_not_succeeded"

    # 旧 pickup 的迟到终态事件也不能认领新计划的任何步骤
    stale = await runtime.on_action_event(
        action_id=str(old_pickup),
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=_pickup_payload(str(old_pickup), dig_action=str(old_dig)),
    )
    after_stale = await runtime.get(record.task_id)
    assert after_stale is not None
    assert after_stale.state is TaskState.PENDING_CONFIRMATION
    assert stale is None or stale.state is TaskState.PENDING_CONFIRMATION
    assert calls == before_calls
    assert collection_evidence(after_stale)["ok"] is False

    # 新计划重新确认后必须重新挖、重新等新的动作 id
    restarted = await runtime.confirm_and_start(
        record.task_id, user_id="1", session_id="private:1", origin="user"
    )
    assert restarted.pending_action_id and restarted.pending_action_id != old_dig
    assert restarted.pending_action_id.startswith("act_dig_2")


# ------------------------------------------------------------------ 恢复（真实 SQLite）


async def test_restart_replay_recomputes_from_persisted_evidence(tmp_path: Any) -> None:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'collection_chain.db'}"))
    await database.connect()
    try:
        store = SqliteTaskStore(database)
        record = resource_record()
        # 旧的"已验证"缓存必须只作审计，不能跳过复核
        record.result["collection_verification"] = {"checked": True, "ok": True}
        await store.save(record)

        reloaded = await SqliteTaskStore(database).load(record.task_id)
        assert reloaded is not None
        assert collection_evidence(reloaded)["ok"] is True

        dig = next(step for step in reloaded.steps if step.tool == "minecraft_dig")
        dig.result["attribution"]["server_collection"]["drops"][0]["item"] = "minecraft:oak_planks"
        await SqliteTaskStore(database).save(reloaded)

        again = await SqliteTaskStore(database).load(record.task_id)
        assert again is not None
        verdict = collection_evidence(again)
        assert verdict["ok"] is False
        assert verdict["reason"] == REASON + "drop_identity"

        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(again)
        assert out["action"] == "rejected"
        assert str(out["reason"]).startswith("collection_evidence_unproven")
        assert out["verdict"] == "AMBIGUOUS"
    finally:
        await database.close()


# ------------------------------------------------------------------ 重放


async def test_duplicate_pickup_event_settles_once() -> None:
    calls: list[str] = []
    runtime = _runtime(calls)
    started = await _start_collection(runtime)
    dig_action = started.pending_action_id
    record = await _to_pickup(runtime, started)
    pickup_action = str(record.pending_action_id)
    payload = _pickup_payload(pickup_action, dig_action=str(dig_action))

    first = await runtime.on_action_event(
        action_id=pickup_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=payload,
    )
    assert first is not None
    assert first.state is TaskState.SUCCEEDED
    count_after_first = len(calls)

    second = await runtime.on_action_event(
        action_id=pickup_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=payload,
    )
    assert second is None
    assert len(calls) == count_after_first

    final = await runtime.get(record.task_id)
    assert final is not None and final.state is TaskState.SUCCEEDED
    verdict = collection_evidence(final)
    assert verdict["ok"] is True and verdict["verified_pickups"] == 1


async def test_duplicate_dig_event_cannot_restamp_the_break_evidence() -> None:
    calls: list[str] = []
    runtime = _runtime(calls)
    started = await _start_collection(runtime)
    dig_action = str(started.pending_action_id)
    record = await _to_pickup(runtime, started)

    # 重复的 dig 终态事件带着被篡改的掉落：不得覆盖已落库的真实来源证据
    duplicate = await runtime.on_action_event(
        action_id=dig_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=_dig_result(dig_action, item="oak_planks"),
    )
    after = await runtime.get(record.task_id)
    assert after is not None
    dig = next(step for step in after.steps if step.tool == "minecraft_dig")
    assert dig.result["attribution"]["server_collection"]["drops"][0]["item"] == "minecraft:oak_log"
    assert duplicate is None or duplicate.state is TaskState.WAITING_ACTION

    pickup_action = str(after.pending_action_id)
    finished = await runtime.on_action_event(
        action_id=pickup_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=_pickup_payload(pickup_action, dig_action=dig_action),
    )
    assert finished is not None
    assert finished.state is TaskState.SUCCEEDED
    assert collection_evidence(finished)["ok"] is True


# ------------------------------------------------------------------ 竞争


async def test_cancel_while_pickup_in_flight_is_never_revived_by_the_late_event() -> None:
    calls: list[str] = []
    runtime = _runtime(calls)
    started = await _start_collection(runtime)
    dig_action = str(started.pending_action_id)
    record = await _to_pickup(runtime, started)
    pickup_action = str(record.pending_action_id)

    cancelled = await runtime.cancel(record.task_id, reason="user cancel")
    assert cancelled.state is TaskState.CANCELLED
    count_after_cancel = len(calls)

    late = await runtime.on_action_event(
        action_id=pickup_action,
        event="minecraft.action.completed",
        status="SUCCEEDED",
        result=_pickup_payload(pickup_action, dig_action=dig_action),
    )
    after = await runtime.get(record.task_id)
    assert after is not None
    assert after.state is TaskState.CANCELLED
    assert late is None or late.state is TaskState.CANCELLED
    # 迟到事件不得再触发任何世界动作
    assert len(calls) == count_after_cancel

    service = make_service(observe=FakeObserve())
    out = await service.on_task_finished(after)
    assert out["action"] == "rejected"
    assert str(out["reason"]).startswith("not_succeeded")


# ------------------------------------------------------------------ 结构性一致性


def _app_root() -> Path:
    return Path(__file__).resolve().parents[1]


def test_only_one_module_defines_the_collection_verdict() -> None:
    root = _app_root()
    hits = sorted(
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "app").rglob("*.py")
        if REASON in path.read_text(encoding="utf-8")
    )
    # 只有消费者自己产出判定文本；runtime 只在"沿用既有语义"的常量里点名两个原因码
    assert hits == ["app/tasks/collection_evidence.py", "app/tasks/runtime.py"]
    runtime_text = (root / "app/tasks/runtime.py").read_text(encoding="utf-8")
    assert runtime_text.count(REASON) == 2
    assert "COLLECTION_DEFERRED_REASONS = frozenset(" in runtime_text
    definers = [
        str(path.relative_to(root)).replace("\\", "/")
        for path in (root / "app").rglob("*.py")
        if "def is_collection_task" in path.read_text(encoding="utf-8")
    ]
    assert definers == ["app/tasks/collection_evidence.py"]


def test_all_runtime_consumers_import_the_single_verdict() -> None:
    root = _app_root()
    consumers = (
        "app/tasks/runtime.py",
        "app/tasks/skill_learning.py",
        "app/integrations/minecraft/memory_bridge.py",
    )
    for rel in consumers:
        text = (root / rel).read_text(encoding="utf-8")
        assert "from app.tasks.collection_evidence import collection_evidence" in text, rel


def test_node_has_exactly_one_collection_evidence_consumer() -> None:
    root = _app_root()
    runtime_dir = root / "minecraft_runtime"
    markers = ("server_collection", "server_proof")
    consumers = sorted(
        path.name
        for path in runtime_dir.glob("*.js")
        if any(marker in path.read_text(encoding="utf-8") for marker in markers)
    )
    assert consumers == ["strict_attribution.js"]
    node_tests = (runtime_dir / "test" / "strict_attribution.test.js").read_text(encoding="utf-8")
    for marker in ("collection_protocol", "drop_uuid", "pickup-reserve"):
        assert marker in node_tests, marker
    # Node 的通道层只搬运载荷，不自行裁决采集来源
    runtime_js = (runtime_dir / "runtime.js").read_text(encoding="utf-8")
    assert "server_proof" not in runtime_js


def test_collection_verdict_does_not_read_a_cached_success() -> None:
    record = resource_record()
    record.result["collection_verification"] = {"checked": True, "ok": True}
    dig = next(step for step in record.steps if step.tool == "minecraft_dig")
    dig.result["attribution"]["server_proof"]["pos"] = "0,0,0"
    verdict = collection_evidence(record)
    assert verdict["ok"] is False
    assert verdict["reason"] == REASON + "break_proof"
    assert isinstance(verdict, Mapping)
