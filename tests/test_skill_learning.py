"""Phase 7E 学习资格门（E1–E7）：真实记录 → 资格判定 → 结构化技能（纯函数级）。"""

from __future__ import annotations

import pytest

from app.tasks.models import PlanStatus, StepState, TaskState
from app.tasks.skill import (
    SKILL_SCHEMA_VERSION,
    EvidenceVerdict,
    SkillStatus,
    skill_fingerprint,
    skill_transition_allowed,
)
from app.tasks.skill_learning import (
    POSITION_SLOT,
    LearningInput,
    normalize_steps,
    qualify,
)
from tests.skill_fakes import (
    BLOCK,
    CHARACTER,
    DIG_MS,
    DROP,
    POSITION,
    RISKS,
    SERVER,
    TOOLS,
    resource_record,
    tools_snapshot,
)


def learning_input(record: object, **overrides: object) -> LearningInput:
    snapshot = tools_snapshot()
    base: dict[str, object] = {
        "record": record,
        "character_id": CHARACTER,
        "server_id": SERVER,
        "tools_signature": snapshot.signature,
        "registered_tools": frozenset(TOOLS),
        "risk_table": dict(RISKS),
        "postconditions": {},
        "task_effect_verified": True,
        "dig_expected_ms": {"step_3": DIG_MS},
        "method_class": f"resource:{BLOCK}:{DROP}",
        "objective": "去挖一块橡木并捡回来",
    }
    base.update(overrides)
    return LearningInput(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------- E1 证据入口


class TestEvidenceEntry:
    async def test_qualified_from_a_real_success_record(self) -> None:
        result = qualify(learning_input(resource_record()))
        assert result.ok, result.reason_code
        assert result.reason_code == "QUALIFIED"
        assert result.verdict is EvidenceVerdict.POSITIVE
        assert [step.tool for step in result.steps] == [
            "minecraft_equip",
            "minecraft_move_to",
            "minecraft_dig",
            "minecraft_dropped_items",
            "minecraft_pickup_item",
            "minecraft_inventory",
        ]
        assert result.max_risk == "MEDIUM"
        assert result.success_criteria  # 有"目标已实现"的判据，而不是只有工具返回 ok

    async def test_record_without_steps_cannot_teach(self) -> None:
        record = resource_record()
        record.plan.steps = []
        result = qualify(learning_input(record))
        assert not result.ok and result.reason_code == "no_steps"

    async def test_missing_state_field_is_unknown_not_success(self) -> None:
        record = resource_record()
        record.state = "NOT_A_STATE"  # type: ignore[assignment]
        result = qualify(learning_input(record))
        assert not result.ok and result.reason_code == "unknown_state"


# ---------------------------------------------------------------- E2 成功资格门


class TestSuccessGate:
    @pytest.mark.parametrize(
        "state",
        [
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
            TaskState.PAUSED,
            TaskState.WAITING_ACTION,
            TaskState.WAITING_USER,
            TaskState.PENDING_CONFIRMATION,
            TaskState.REPLANNING,
            TaskState.PLANNING,
            TaskState.RUNNING,
        ],
    )
    async def test_only_succeeded_promotes(self, state: TaskState) -> None:
        record = resource_record(state=state)
        result = qualify(learning_input(record))
        assert not result.ok, f"{state} 绝不能晋升"
        assert result.verdict is EvidenceVerdict.REJECTED
        expected = "not_terminal" if not state.terminal else f"not_succeeded:{state.value}"
        assert result.reason_code == expected

    async def test_step_not_run_blocks_learning(self) -> None:
        record = resource_record(step_states={"step_3": StepState.WAITING_ACTION})
        result = qualify(learning_input(record))
        assert not result.ok
        assert result.reason_code == "step_not_run:step_3:WAITING_ACTION"

    async def test_detached_pending_step_is_not_a_method(self) -> None:
        record = resource_record()
        record.steps[2].state = StepState.PENDING  # dig 从没执行过
        result = qualify(learning_input(record))
        assert not result.ok and result.reason_code.startswith("step_not_run:step_3")

    async def test_unknown_tool_is_rejected(self) -> None:
        record = resource_record()
        record.steps[0].tool = "minecraft_teleport_made_up"
        result = qualify(learning_input(record))
        assert not result.ok and "unsupported_step" in result.reason_code

    async def test_risk_mismatch_is_rejected(self) -> None:
        record = resource_record()
        record.steps[2].risk = "SAFE"  # 与当前风险表不符
        result = qualify(learning_input(record))
        assert not result.ok and result.reason_code == "risk_mismatch:minecraft_dig"


# ---------------------------------------------------------------- E3 dig 歧义隔离


class TestDigAmbiguityQuarantine:
    async def test_world_change_without_postcondition_is_ambiguous(self) -> None:
        """改动世界的步骤没有独立后置条件 → 歧义（保留来源、不计正向）。"""

        result = qualify(learning_input(resource_record(), task_effect_verified=False))
        assert not result.ok
        assert result.verdict is EvidenceVerdict.AMBIGUOUS
        assert result.reason_code == "world_change_unverified:step_3", "第一个没背书的改动步骤"

    async def test_place_always_needs_a_step_level_postcondition(self) -> None:
        """``minecraft_place`` 的效果不在背包里 → 没有步骤级验证器就判歧义（不靠 ok 冒充）。"""

        from app.tasks.models import TaskStep

        record = resource_record()
        record.plan.steps.insert(
            1,
            TaskStep(
                step_id="step_place",
                tool="minecraft_place",
                arguments={
                    "x": 1,
                    "y": 2,
                    "z": 3,
                    "face": "up",
                    "expected_item": "minecraft:dirt",
                },
                risk="MEDIUM",
            ),
        )
        for step in record.plan.steps:
            step.state = StepState.SUCCEEDED
        result = qualify(
            learning_input(
                record,
                registered_tools=frozenset(TOOLS) | {"minecraft_place"},
                risk_table={**RISKS, "minecraft_place": "MEDIUM"},
                task_effect_verified=True,
            )
        )
        assert not result.ok
        assert result.verdict is EvidenceVerdict.AMBIGUOUS
        assert result.reason_code == "world_change_unverified:step_place"

    async def test_dig_disappeared_too_fast_is_ambiguous(self) -> None:
        """实测时长不到预期一半 → 方块很可能在挖到之前就被别人移除。"""

        record = resource_record(dig_elapsed_ms=DIG_MS * 0.3)
        result = qualify(learning_input(record))
        assert not result.ok
        assert result.verdict is EvidenceVerdict.AMBIGUOUS
        assert result.reason_code == "ambiguous_world_change:step_3"
        assert result.detail["ratio"] < 0.5

    async def test_dig_matching_expected_duration_is_fine(self) -> None:
        record = resource_record(dig_elapsed_ms=DIG_MS * 0.9)
        result = qualify(learning_input(record))
        assert result.ok, result.reason_code

    async def test_ambiguous_case_from_7d2_is_not_success(self) -> None:
        """7D.2 真机那一轮的形态：dig 假成功 + 掉落物没进背包 → 任务不是 SUCCEEDED。

        真实链路上"别人移除方块"会导致拾取失败 → 任务 FAILED → 资格门直接拒绝；
        这条测试把它钉住（不把它记成"罐头掌握了挖掘方法"）。
        """

        record = resource_record(
            state=TaskState.FAILED,
            verification={},
            step_states={"step_4": StepState.FAILED},
            dig_elapsed_ms=DIG_MS * 0.78,
        )
        result = qualify(learning_input(record, task_effect_verified=False))
        assert not result.ok
        assert result.verdict is EvidenceVerdict.REJECTED
        assert result.reason_code.startswith("not_succeeded")

    async def test_unverified_world_change_is_never_positive(self) -> None:
        result = qualify(learning_input(resource_record(), task_effect_verified=False))
        assert not result.ok
        assert result.verdict is EvidenceVerdict.AMBIGUOUS


# ---------------------------------------------------------------- E4 最终计划版本


class TestFinalPlanVersion:
    async def test_replan_only_takes_the_successful_final_version(self) -> None:
        record = resource_record(superseded_first=True, replans=1)
        assert record.plan_version == 2
        result = qualify(learning_input(record))
        assert result.ok, result.reason_code
        # 模板里只有最终版本的步骤；第 1 版那个失败的 dig 不在其中
        assert [step.step_id for step in result.steps] == [
            step.step_id for step in record.plan.steps
        ]
        assert "step_1" in [step.step_id for step in result.steps]
        assert result.steps[0].tool == "minecraft_equip"

    async def test_final_plan_not_completed_is_rejected(self) -> None:
        record = resource_record(plan_status=PlanStatus.SUPERSEDED.value)
        result = qualify(learning_input(record))
        assert not result.ok and result.reason_code == "final_plan_not_completed"

    async def test_normalize_keeps_references_and_replaces_positions(self) -> None:
        steps, slots = normalize_steps(resource_record())
        dig = next(step for step in steps if step.tool == "minecraft_dig")
        assert dig.arguments.get("$slot") == POSITION_SLOT
        assert not {"x", "y", "z"} & set(dig.arguments)
        pickup = next(step for step in steps if step.tool == "minecraft_pickup_item")
        assert pickup.arguments["entity_id"] == {"from_step": "step_4", "path": "items.0.entity_id"}
        assert [slot.name for slot in slots] == [POSITION_SLOT]


# ---------------------------------------------------------------- E6/E7 结构与去重


class TestStructureAndDedup:
    async def test_fingerprint_is_position_independent(self) -> None:
        left = qualify(learning_input(resource_record(position={"x": -993, "y": 81, "z": 646})))
        right = qualify(learning_input(resource_record(position={"x": 128, "y": 64, "z": -40})))
        assert left.ok and right.ok
        assert left.fingerprint == right.fingerprint, "位置是一次性值，不该进指纹"

    async def test_fingerprint_changes_with_the_method(self) -> None:
        oak = qualify(learning_input(resource_record()))
        iron = qualify(
            learning_input(
                resource_record(block="iron_ore", drop="raw_iron"),
                method_class="resource:iron_ore:raw_iron",
            )
        )
        assert oak.fingerprint != iron.fingerprint

    def test_fingerprint_depends_on_tools_signature(self) -> None:
        steps, _ = normalize_steps(resource_record())
        first = skill_fingerprint(
            character_id=CHARACTER,
            server_id=SERVER,
            tools_signature="a",
            objective_pattern=f"resource:{BLOCK}:{DROP}",
            steps=steps,
        )
        second = skill_fingerprint(
            character_id=CHARACTER,
            server_id=SERVER,
            tools_signature="b",
            objective_pattern=f"resource:{BLOCK}:{DROP}",
            steps=steps,
        )
        assert first != second, "工具契约变了就是另一种方法"

    def test_only_active_is_reusable_and_terminals_have_no_exit(self) -> None:
        from app.tasks.skill import TERMINAL_SKILL_STATUSES, ProceduralSkill

        assert ProceduralSkill(status=SkillStatus.ACTIVE.value).reusable is True
        assert ProceduralSkill(status=SkillStatus.CANDIDATE.value).reusable is False
        for status in TERMINAL_SKILL_STATUSES:
            assert not skill_transition_allowed(status, SkillStatus.ACTIVE)

    def test_template_carries_no_one_off_values(self) -> None:
        record = resource_record()
        result = qualify(learning_input(record))
        assert result.ok
        blob = str([step.arguments for step in result.steps])
        assert str(POSITION["x"]) not in blob and str(POSITION["z"]) not in blob
        assert "2731431246" not in blob and "private:2731431246" not in blob
        assert SKILL_SCHEMA_VERSION >= 1  # 结构版本显式存在（旧版本技能会被判待重新验证）
