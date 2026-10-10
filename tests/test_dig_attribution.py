"""Phase 7D Follow-up：挖掘结果归因（世界效果 vs 执行归属）的收口测试。

覆盖任务书 §8 的判定矩阵在 **Python 侧**的部分：

* 归因载荷的读法（``dig_attribution`` / ``self_dig_confirmed`` / 原因码）；
* 技能资格门：``minecraft_dig`` 的正向样本必须有 ``SELF_CONFIRMED`` 归因
  （缺失 / 别人的 / 说不清 / 串了 action_id → ``dig_attribution_unproven:<step_id>``）；
* **效果证据不等于归属证据**：``block_absent`` / ``inventory_delta`` 齐全也不能把
  "别人挖掉的"记成罐头亲手挖的；
* 外部/歧义只留 ``AMBIGUOUS`` 证据：既不计正向、也不计反例（技能不会因此被惩罚）；
* 幂等：同一条记录重复处理只留一行证据，计数不翻倍；
* 记忆桥：只有 ``SELF_CONFIRMED`` 的挖掘步骤才写 "她亲手挖过" 的 RESOURCE 事实，
  并且**逐挖掘步骤独立判定**；
* 归因随既有动作终态事件落到 ``TaskStep.result``，重复/迟到的终态事件不覆盖、不重复记账。
"""

from __future__ import annotations

import json
from typing import Any

from app.tasks.models import StepState, TaskPlan, TaskState
from app.tasks.skill import EvidenceVerdict
from app.tasks.skill_learning import (
    dig_attribution,
    dig_attribution_reason,
    self_dig_confirmed,
)
from app.tasks.skill_service import SkillService
from app.tasks.skill_store import InMemorySkillStore
from tests.skill_fakes import (
    BLOCK,
    DIG_ATTRIBUTION_PRESETS,
    DROP,
    POSITION,
    FakeObserve,
    make_service,
    resource_plan,
    resource_record,
)

DIG_STEP_ID = "step_3"  # equip → move_to → dig


def dig_result(attribution: Any) -> dict[str, Any]:
    return {
        "position": dict(POSITION),
        "block_before": BLOCK,
        "block_after": "air",
        "attribution": attribution,
    }


class Step:
    """最小的"步骤"替身：只需要 ``result`` / ``action_id``（读归因只认这两个字段）。"""

    def __init__(self, result: Any, *, action_id: str = "act_dig_1") -> None:
        self.result = result
        self.action_id = action_id


# ------------------------------------------------------------------ 载荷读法


class TestAttributionPayload:
    def test_reads_a_well_formed_payload(self) -> None:
        payload = dig_attribution(Step(dig_result(DIG_ATTRIBUTION_PRESETS["self"])))
        assert payload is not None
        assert payload["schema"] == 2
        assert payload["world_effect"] == "BLOCK_REMOVED"
        assert payload["attribution"] == "SELF_CONFIRMED"
        assert payload["assurance"] == "STRICT"
        assert payload["confirm_basis"] == "self_break_progress"
        assert payload["strict_self_proof"] is True

    def test_tolerates_json_text(self) -> None:
        """历史/落库形态可能是 JSON 文本（payload 与整份 result 都要认）。"""
        step = Step(json.dumps(dig_result(json.dumps(DIG_ATTRIBUTION_PRESETS["self"]))))
        payload = dig_attribution(step)
        assert payload is not None and payload["attribution"] == "SELF_CONFIRMED"

    def test_missing_or_broken_payload_is_none(self) -> None:
        assert dig_attribution(Step({})) is None
        assert dig_attribution(Step({"attribution": None})) is None
        assert dig_attribution(Step(dig_result("不是 JSON"))) is None
        assert dig_attribution(Step(dig_result({"world_effect": "BLOCK_REMOVED"}))) is None
        assert dig_attribution(Step(dig_result({"attribution": "SELF_CONFIRMED"}))) is None
        assert dig_attribution(Step(None)) is None
        assert dig_attribution(Step("不是 JSON")) is None
        assert dig_attribution(object()) is None

    def test_self_dig_confirmed_needs_strict_evidence(self) -> None:
        assert self_dig_confirmed(Step(dig_result(DIG_ATTRIBUTION_PRESETS["self"]))) is True
        for name in (
            "external",
            "ambiguous",
            "conflict",
            "remains",
            "unknown_effect",
            "missing",
            "self_inferred",
            "legacy_self",
            "strict_not_bool",
            "bad_schema",
        ):
            step = Step(dig_result(DIG_ATTRIBUTION_PRESETS[name]))
            assert self_dig_confirmed(step) is False, name

    def test_lifecycle_inference_is_not_strict_self_confirmation(self) -> None:
        """``dig_lifecycle_timing``（本地运行时按时序推断）是 SELF_INFERRED，**不是**严格自证。"""
        step = Step(dig_result(DIG_ATTRIBUTION_PRESETS["self_inferred"]))
        assert self_dig_confirmed(step) is False
        # 子原因是**推断**本身的原因码（如实留痕），而不是被误当成严格自证
        assert dig_attribution_reason(step)[0] == "self_dig_completed_at_expected_time"
        # 审计字段仍然如实保留
        assert dig_attribution(step)["attribution"] == "SELF_INFERRED"
        assert dig_attribution(step)["assurance"] == "INFERRED"

    def test_legacy_strict_self_proof_true_is_rejected(self) -> None:
        """历史过宽契约：SELF_CONFIRMED + 时序依据 + strict_self_proof=True —— 必须被拦。"""
        step = Step(dig_result(DIG_ATTRIBUTION_PRESETS["legacy_self"]))
        assert self_dig_confirmed(step) is False
        assert dig_attribution_reason(step)[0] == "confirm_basis_not_strict"

    def test_strict_proof_type_conversion_is_rejected(self) -> None:
        """``strict_self_proof`` 必须是真布尔；字符串"true"不能靠类型转换通过。"""
        step = Step(dig_result(DIG_ATTRIBUTION_PRESETS["strict_not_bool"]))
        assert dig_attribution(step) is None
        assert self_dig_confirmed(step) is False

    def test_unknown_schema_fails_closed(self) -> None:
        step = Step(dig_result(DIG_ATTRIBUTION_PRESETS["bad_schema"]))
        assert dig_attribution(step) is None
        assert self_dig_confirmed(step) is False

    def test_reason_codes_are_stable(self) -> None:
        assert dig_attribution_reason(Step({}, action_id="act_dig_1")) == ("missing", {})
        reason, detail = dig_attribution_reason(
            Step(dig_result(DIG_ATTRIBUTION_PRESETS["external"]))
        )
        assert reason == "external_break_progress_observed"
        assert detail["attribution"] == "EXTERNAL_INDICATED"
        assert dig_attribution_reason(Step(dig_result(DIG_ATTRIBUTION_PRESETS["remains"])))[0] == (
            "world_effect_not_removed"
        )

    def test_action_id_mismatch_is_not_attribution(self) -> None:
        """归因载荷必须精确属于这一步：串号/缺失 → 无法归因（fail-closed）。"""
        payload = {**DIG_ATTRIBUTION_PRESETS["self"], "action_id": "act_other"}
        step = Step(dig_result(payload), action_id="act_dig_1")
        assert self_dig_confirmed(step) is False
        reason, detail = dig_attribution_reason(step)
        assert reason == "action_id_mismatch"
        assert detail["step_action_id"] == "act_dig_1"
        # 步骤没有 action_id（无从绑定）→ fail-closed
        assert self_dig_confirmed(Step(dig_result(payload), action_id="")) is False
        assert dig_attribution_reason(Step(dig_result(payload), action_id=""))[0] == (
            "action_id_missing"
        )
        # 载荷没有 action_id → fail-closed（不能凭空使用）
        bare = {k: v for k, v in DIG_ATTRIBUTION_PRESETS["self"].items() if k != "action_id"}
        assert self_dig_confirmed(Step(dig_result(bare), action_id="act_dig_1")) is False
        assert dig_attribution_reason(Step(dig_result(bare), action_id="act_dig_1"))[0] == (
            "action_id_mismatch"
        )

    def test_missing_identity_fields_fail_closed(self) -> None:
        """载荷必须完整（target 坐标等关键身份字段缺失 → fail-closed）。"""
        for name in ("no_target", "no_world_effect", "no_attribution"):
            payload = {**DIG_ATTRIBUTION_PRESETS["self"]}
            if name == "no_target":
                payload.pop("target")
            elif name == "no_world_effect":
                payload.pop("world_effect")
            else:
                payload.pop("attribution")
            assert dig_attribution(Step(dig_result(payload))) is None, name


# ------------------------------------------------------------------ 技能资格门


class TestSkillGateRequiresAttribution:
    async def test_self_attribution_learns_a_candidate(self) -> None:
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="self"))
        assert out["action"] == "learned" and out["status"] == "CANDIDATE"

    async def test_external_dig_is_rejected_as_unproven(self) -> None:
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="external"))
        assert out["action"] == "rejected"
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"
        assert out["verdict"] == EvidenceVerdict.AMBIGUOUS.value
        assert service.learned_count == 0

    async def test_missing_payload_is_rejected(self) -> None:
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="missing"))
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"
        assert out["verdict"] == EvidenceVerdict.AMBIGUOUS.value

    async def test_ambiguous_payload_is_rejected(self) -> None:
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="ambiguous"))
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"
        # 原因码里保留本地运行时的稳定子原因（可审计）
        evidence = await service.store.evidence_for_task("task_skill_1")
        assert (
            evidence and evidence[0].detail["reason"] == "block_removed_before_self_dig_completion"
        )

    async def test_block_still_present_is_rejected(self) -> None:
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="remains"))
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"
        evidence = await service.store.evidence_for_task("task_skill_1")
        assert evidence and evidence[0].detail["reason"] == "world_effect_not_removed"

    async def test_effect_evidence_alone_is_not_attribution(self) -> None:
        """§四：``inventory_delta`` / ``block_absent`` 只证明世界变了，不证明是谁弄掉的。"""

        # 背包对得上（inventory_delta）、新鲜后置条件也齐 —— 只有归属是"别人的"
        service = make_service(observe=FakeObserve(find_matches=1))
        record = resource_record(
            dig_attribution="external",
            verification={"inventory_delta": {DROP: 1}},
        )
        assert record.verification["inventory_delta"] == {DROP: 1}
        out = await service.on_task_finished(record)
        assert out["action"] == "rejected"
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"

    async def test_action_id_mismatch_is_rejected(self) -> None:
        service = make_service(observe=FakeObserve())
        payload = {**DIG_ATTRIBUTION_PRESETS["self"], "action_id": "act_other"}
        out = await service.on_task_finished(resource_record(dig_attribution=payload))
        assert out["reason"] == f"dig_attribution_unproven:{DIG_STEP_ID}"
        evidence = await service.store.evidence_for_task("task_skill_1")
        assert evidence and evidence[0].detail["reason"] == "action_id_mismatch"

    async def test_ambiguous_is_not_a_counterexample(self) -> None:
        """外部/歧义样本只留 AMBIGUOUS 证据：绝不当反例惩罚技能（§四）。"""

        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(resource_record(dig_attribution="external"))
        assert out["action"] == "rejected"
        assert service.rejected_count == 0
        assert service.ambiguous_count == 1
        evidence = await service.store.evidence_for_task("task_skill_1")
        assert evidence and evidence[0].verdict == EvidenceVerdict.AMBIGUOUS.value

    async def test_short_duration_signal_still_holds_as_auxiliary(self) -> None:
        """时序信号降级为辅助，但没被删掉：自证 + 明显短于预期 → 仍然拦下（留 AMBIGUOUS）。"""

        service = make_service(observe=FakeObserve())
        record = resource_record(dig_attribution="self", dig_elapsed_ms=10.0)  # 预期 600ms
        out = await service.on_task_finished(record)
        assert out["action"] == "rejected"
        assert out["reason"] == f"ambiguous_world_change:{DIG_STEP_ID}"

    async def test_replay_of_the_same_verdict_stays_idempotent(self) -> None:
        store = InMemorySkillStore()
        service = make_service(store=store, observe=FakeObserve())
        first = await service.on_task_finished(resource_record(dig_attribution="external"))
        second = await service.on_task_finished(resource_record(dig_attribution="external"))
        assert first["action"] == "rejected" and second["action"] == "duplicate"
        assert service.ambiguous_count == 1  # 只累计一次
        evidence = await store.evidence_for_task("task_skill_1")
        assert len(evidence) == 1


# ------------------------------------------------------------------ 记忆桥（逐挖掘步骤）


class TestMemoryBridgeGatesOnAttribution:
    async def test_only_self_attributed_dig_steps_become_facts(self, tmp_path: Any) -> None:
        from app.memory.minecraft.model import FactSource, MinecraftMemoryKind
        from tests.minecraft_memory_fakes import build_bridge
        from tests.test_minecraft_memory_recovery import dig_step, task_record

        bridge, database, _manager, _service = await build_bridge(tmp_path)
        external = {**DIG_ATTRIBUTION_PRESETS["external"], "action_id": "act_dig_2"}
        await bridge.on_task_finished(
            task_record(
                steps=[
                    dig_step("step_1"),  # 亲手挖的 → 记
                    # 别人挖掉的 → 不记
                    dig_step("step_2", x=200, attribution=external, action_id="act_dig_2"),
                    dig_step("step_3", x=300, attribution="missing"),  # 没有归因 → 不记
                ]
            )
        )
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        resources = [f for f in facts if f.kind is MinecraftMemoryKind.RESOURCE]
        assert len(resources) == 1
        assert resources[0].position == {"x": 100, "y": 64, "z": 100}
        assert resources[0].source is FactSource.TASK_RESULT
        # 任务经验本身照写（"做过什么"仍然成立）
        assert any(f.kind is MinecraftMemoryKind.TASK for f in facts)
        await database.close()

    async def test_external_only_dig_leaves_no_world_fact(self, tmp_path: Any) -> None:
        from app.memory.minecraft.model import MinecraftMemoryKind
        from tests.minecraft_memory_fakes import build_bridge
        from tests.test_minecraft_memory_recovery import dig_step, task_record

        bridge, database, _manager, _service = await build_bridge(tmp_path)
        payload = {**DIG_ATTRIBUTION_PRESETS["external"], "action_id": "act_dig_2"}
        await bridge.on_task_finished(
            task_record(steps=[dig_step("step_1", attribution=payload, action_id="act_dig_2")])
        )
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        assert [f.kind for f in facts] == [MinecraftMemoryKind.TASK]
        await database.close()

    async def test_unfinished_dig_step_is_not_remembered(self, tmp_path: Any) -> None:
        """步骤状态不是 SUCCEEDED → 即便归因凑巧是自证也不写（三层要求同时成立）。"""

        from app.memory.minecraft.model import MinecraftMemoryKind
        from tests.minecraft_memory_fakes import build_bridge
        from tests.test_minecraft_memory_recovery import dig_step, task_record

        bridge, database, _manager, _service = await build_bridge(tmp_path)
        await bridge.on_task_finished(
            task_record(steps=[dig_step("step_1", state=StepState.FAILED)])
        )
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        assert [f.kind for f in facts] == [MinecraftMemoryKind.TASK]
        await database.close()


# ------------------------------------------------------------------ 归因随动作终态落库


def _detached_runtime() -> Any:
    """一套真实 TaskRuntime：挖掘是持续型动作（RUNNING + action_id），其余步骤同步成功。"""

    from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
    from app.tasks.store import InMemoryTaskStore
    from tests.skill_fakes import RISKS, SCHEMAS, TOOLS, FakeConfirmations

    inventory_reads = {"n": 0}

    async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        if tool == "minecraft_dig":
            return TaskInvocation(ok=True, status="RUNNING", action_id="act_dig_1")
        if tool == "minecraft_dropped_items":
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={"items": [{"entity_id": 42, "item": {"name": DROP, "count": 1}}]},
            )
        if tool == "minecraft_inventory":
            inventory_reads["n"] += 1
            items = [] if inventory_reads["n"] == 1 else [{"name": DROP, "count": 1}]
            return TaskInvocation(
                ok=True, status="SUCCEEDED", result={"items": items, "held_item": None}
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


class TestAttributionTravelsThroughActionEvents:
    async def test_result_lands_in_step_and_duplicates_do_not_overwrite(self) -> None:
        runtime = _detached_runtime()
        record = resource_record(dig_attribution="self")
        created = await runtime.create_task(
            record.objective,
            session_id=record.session_id,
            user_id=record.user_id,
            origin="user",
            plan=resource_plan(),
            source="qq",
        )
        started = await runtime.confirm_and_start(
            created.task_id, user_id=record.user_id, session_id=record.session_id, origin="user"
        )
        assert started.state is TaskState.WAITING_ACTION
        action_id = started.pending_action_id
        payload = {**DIG_ATTRIBUTION_PRESETS["self"], "action_id": action_id}

        done = await runtime.on_action_event(
            action_id=action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result=dig_result(payload),
        )
        assert done is not None
        dig_step = next(step for step in done.steps if step.tool == "minecraft_dig")
        assert dig_step.result["attribution"]["attribution"] == "SELF_CONFIRMED"
        assert self_dig_confirmed(dig_step) is True

        # 重复的终态事件（重启恢复 / 重放 / 第二次回调）：不得覆盖、不得重复记账
        replay = await runtime.on_action_event(
            action_id=action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"position": dict(POSITION), "block_after": "air"},
        )
        assert replay is None
        after = await runtime.get(created.task_id)
        assert after is not None
        step_after = next(step for step in after.steps if step.tool == "minecraft_dig")
        assert step_after.result["attribution"]["attribution"] == "SELF_CONFIRMED"
        # 别的 action_id 也绝不来认领这一步
        assert (
            await runtime.on_action_event(
                action_id="act_other", event="minecraft.action.completed", status="SUCCEEDED"
            )
            is None
        )

    async def test_gate_reads_the_attribution_that_travelled(self) -> None:
        """端到端：真实运行时执行 → 归因落库 → 资格门据此判定。"""

        runtime = _detached_runtime()
        record = resource_record(dig_attribution="self")
        created = await runtime.create_task(
            record.objective,
            session_id=record.session_id,
            user_id=record.user_id,
            origin="user",
            plan=resource_plan(),
            source="qq",
        )
        started = await runtime.confirm_and_start(
            created.task_id, user_id=record.user_id, session_id=record.session_id, origin="user"
        )
        payload = {**DIG_ATTRIBUTION_PRESETS["external"], "action_id": started.pending_action_id}
        await runtime.on_action_event(
            action_id=started.pending_action_id,
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result=dig_result(payload),
        )
        # 走完剩下的步骤（掉落物 → 拾取 → 背包复核）
        for _ in range(4):
            current = await runtime.get(created.task_id)
            if current is None or not current.pending_action_id:
                break
            await runtime.on_action_event(
                action_id=current.pending_action_id,
                event="minecraft.action.completed",
                status="SUCCEEDED",
                result={},
            )

        finished = await runtime.get(created.task_id)
        assert finished is not None
        service = make_service(observe=FakeObserve())
        out = await service.on_task_finished(finished)
        # 任务可能成功、也可能在后续步骤停住；只要**没有**被当成"罐头亲手挖过"
        assert out.get("reason") != "QUALIFIED"
        if out["action"] == "rejected":
            assert out["reason"] in {
                f"dig_attribution_unproven:{DIG_STEP_ID}",
                "dig_attribution_unproven:step_5",
            } or out["reason"].startswith("step_not_run")


def test_public_api_is_exported() -> None:
    from app.tasks import skill_learning

    for name in (
        "dig_attribution",
        "dig_attribution_reason",
        "self_dig_confirmed",
        "DIG_TOOL",
        "WORLD_EFFECT_BLOCK_REMOVED",
        "ATTRIBUTION_SELF_CONFIRMED",
        "ATTRIBUTION_EXTERNAL_INDICATED",
        "ATTRIBUTION_AMBIGUOUS",
    ):
        assert name in skill_learning.__all__, name
        assert hasattr(skill_learning, name), name


def test_plan_fixture_is_the_one_under_test() -> None:
    """防止夹具漂移：默认计划里挖掘就是 step_3，且它带真实归因。"""

    record = resource_record()
    plan: TaskPlan = record.plan
    dig_steps = [step for step in plan.steps if step.tool == "minecraft_dig"]
    assert len(dig_steps) == 1 and dig_steps[0].step_id == DIG_STEP_ID
    assert self_dig_confirmed(dig_steps[0]) is True
    assert isinstance(SkillService, type)
