"""Phase 8G G3/G5：多步 DAG 的**逐步**后置验证、取消与向后兼容（真实哨兵，不看回复文本）。"""

from __future__ import annotations

from typing import Any

from app.tasks.models import ExpectedFinalState, StepState, TaskPlan, TaskState, TaskStep
from tests import test_task_runtime as harness
from tests.test_phase8f_craft_execution import (
    CraftWorld,
    _craft_plan,
)
from tests.test_task_runtime import create_confirmed, make_runtime

OAK_LOG = "oak_log"
PLANKS = "oak_planks"
STICK = "stick"


class FlakyWorld(CraftWorld):
    """第一刀真的生效；从第二刀开始工具**自称**成功但背包不再变化。"""

    def __init__(self, counts: dict[str, int], *, effective_crafts: int = 1) -> None:
        super().__init__(counts)
        self.effective_crafts = effective_crafts

    def _apply(self, effect: tuple[str, int, dict[str, int]]) -> None:
        if self.craft_count > self.effective_crafts:
            return
        super()._apply(effect)


class CancelAfterOneWorld(CraftWorld):
    """第一刀成功后立刻请求取消：验证取消在**步骤之间**生效，后面的步骤一步都不走。"""

    def __init__(self, counts: dict[str, int]) -> None:
        super().__init__(counts)
        self.runtime: Any = None

    async def __call__(self, tool: str, arguments: Any, **kwargs: Any) -> Any:
        result = await super().__call__(tool, arguments, **kwargs)
        if tool == "minecraft_craft" and self.craft_count == 1 and self.runtime is not None:
            await self.runtime.cancel(self.task_id, reason="测试：执行中取消")
        return result


async def test_every_dag_step_gets_its_own_verified_postcondition(monkeypatch: Any) -> None:
    """§7.1/§5.4：每一步都用**真实背包前后快照**验证，证据落在那一步与任务上。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1})
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is TaskState.SUCCEEDED
    verifications = dict(record.result["step_verifications"])
    assert set(verifications) == {"step_1", "step_2"}
    assert all(item["ok"] is True for item in verifications.values())
    assert verifications["step_1"]["inventory_delta"] == {PLANKS: 4}
    assert verifications["step_1"]["inventory_consumed"] == {OAK_LOG: 1}
    assert verifications["step_2"]["inventory_delta"] == {STICK: 4}
    assert verifications["step_2"]["inventory_consumed"] == {PLANKS: 2}
    assert set(record.result["inventory_baselines"]) == {"step_1", "step_2"}
    assert record.verification["ok"] is True, "计划级最终验证也仍然在"
    for step_id in ("step_1", "step_2"):
        audited = record.plan.step(step_id)
        assert audited is not None
        assert audited.result["step_verification"]["ok"] is True, "证据落在那一步上"


async def test_a_later_step_that_lies_is_caught_at_that_step(monkeypatch: Any) -> None:
    """§7.2：第二刀的产物没增加 → 在**第二步**就失败，末尾复核步绝不派发。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = FlakyWorld({OAK_LOG: 1}, effective_crafts=1)
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is TaskState.FAILED
    assert record.verification["ok"] is False
    assert record.verification["step_id"] == "step_2"
    assert record.failed_step == "step_2"
    assert record.result["step_verifications"]["step_1"]["ok"] is True
    assert "step_3" not in record.result["step_verifications"]
    assert plan.step("step_3").state is StepState.PENDING, "失败之后不许再派发任何步骤"
    assert len(world.crafts()) == 2


async def test_cancel_between_steps_stops_the_remaining_dag(monkeypatch: Any) -> None:
    """§8：执行中取消必须**及时**生效 —— 后面的步骤一步都不走。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CancelAfterOneWorld({OAK_LOG: 1})
    runtime, _, _, _ = make_runtime(invoke=world)
    world.runtime = runtime
    record = await create_confirmed(runtime, plan=plan)
    assert record.state is TaskState.CANCELLED
    assert len(world.crafts()) == 1, "取消之后不许再合成"
    assert plan.step("step_2").state is StepState.PENDING
    assert "step_2" not in record.result.get("step_verifications", {})


async def test_missing_inventory_baseline_is_fail_closed(monkeypatch: Any) -> None:
    """缺"派发前真实快照"时**绝不**假装验证过（fail-closed），后面的步骤也不许走。"""
    monkeypatch.setitem(harness.REGISTRY, "minecraft_craft", "MEDIUM")
    plan = await _craft_plan({OAK_LOG: 1})
    world = CraftWorld({OAK_LOG: 1})
    runtime, _, _, _ = make_runtime(invoke=world)
    record = await runtime.create_task(
        plan.objective,
        session_id="private:10001",
        user_id="10001",
        origin="user",
        plan=plan,
    )
    record.state = TaskState.RUNNING
    record.steps[0].state = StepState.SUCCEEDED
    record.result["inventory_baselines"] = {}
    allowed = await runtime._ensure_steps_verified(record, before_step=record.steps[1])
    assert allowed is False
    assert record.state is TaskState.FAILED
    assert record.failed_step == "step_1"
    assert "基线" in record.message


def test_steps_without_expectations_serialize_exactly_as_before() -> None:
    """向后兼容：没有逐步后置条件的步骤**不进** payload（旧计划 plan_hash 逐字节不变）。"""
    step = TaskStep(step_id="step_1", tool="minecraft_inventory", arguments={}, risk="SAFE")
    payload = step.to_payload()
    assert "expected_state" not in payload
    assert TaskStep.from_payload(payload).expected_state is None
    with_expectation = TaskStep(
        step_id="step_1",
        tool="minecraft_craft",
        arguments={"recipe_id": "stick*4=oak_planks*2"},
        risk="MEDIUM",
        expected_state=ExpectedFinalState(
            inventory_delta={STICK: 4}, inventory_consumed={PLANKS: 2}
        ),
    )
    rich = with_expectation.to_payload()
    assert rich["expected_state"]["inventory_delta"] == {STICK: 4}
    restored = TaskStep.from_payload(rich)
    assert restored.expected_state is not None
    assert restored.expected_state.inventory_consumed == {PLANKS: 2}
    assert restored.to_payload() == rich


def test_step_expectations_participate_in_the_plan_hash() -> None:
    """逐步后置条件进 plan_hash ⟹ 进授权指纹（改它就必须重新确认）。"""

    def build(with_expectation: bool) -> TaskPlan:
        steps = [
            TaskStep(
                step_id="step_1",
                tool="minecraft_craft",
                arguments={"recipe_id": "stick*4=oak_planks*2"},
                risk="MEDIUM",
                expected_state=(
                    ExpectedFinalState(inventory_delta={STICK: 4}) if with_expectation else None
                ),
            )
        ]
        return TaskPlan(
            objective="做木棍",
            steps=steps,
            expected_final_state=ExpectedFinalState(inventory_delta={STICK: 4}),
        )

    assert build(True).plan_hash != build(False).plan_hash
    round_trip = TaskPlan.from_payload(build(True).to_payload())
    assert round_trip.plan_hash == build(True).plan_hash
