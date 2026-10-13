"""Phase 8D：有界木材规划模板 + 规划期策略闸的确定性测试（不依赖模型、不用 sleep）。"""

from __future__ import annotations

from typing import Any

import pytest

from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome, detect_shape
from app.tasks.planner import ObservationFailed, plan_wood_task

OAK = "minecraft:oak_log"
SPRUCE = "minecraft:spruce_log"


class FakeObserve:
    """SAFE 观察替身：按工具名返回预设结果（形状与真实 invocation 一致）。"""

    def __init__(
        self, *, matches: list[dict[str, Any]], can_dig: bool = True, reason: str = ""
    ) -> None:
        self.matches = matches
        self.can_dig = can_dig
        self.reason = reason
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, dict(arguments)))
        payload: dict[str, Any]
        if tool == "minecraft_find_blocks":
            payload = {"ok": True, "matches": list(self.matches)}
        elif tool == "minecraft_world":
            payload = {"position": {"x": 0.0, "y": 64.0, "z": 0.0}}
        elif tool == "minecraft_dig_capability":
            payload = {"can_dig": self.can_dig, "reason": self.reason}
        else:
            payload = {}
        return type("R", (), {"result": payload, "summary": tool})()


def _match(block: str, x: int, y: int, z: int, distance: float = 3.0) -> dict[str, Any]:
    return {
        "block": {"name": block},
        "position": {"x": x, "y": y, "z": z},
        "distance": distance,
    }


async def _plan(
    matches: list[dict[str, Any]],
    *,
    count: int = 2,
    can_dig: bool = True,
    reason: str = "",
) -> Any:
    observe = FakeObserve(matches=matches, can_dig=can_dig, reason=reason)
    return await plan_wood_task(
        "帮我砍两根橡木原木带回来",
        observe=observe,
        candidates=(OAK,),
        count=count,
    )


async def test_two_adjacent_logs_in_one_column_are_frozen_per_target() -> None:
    """约束 3：坐标与 expected_block 逐目标冻结；掉落物引用自己那一步；delta 为 n。"""
    plan = await _plan([_match(OAK, 10, 64, 5), _match(OAK, 10, 65, 5)])
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 2
    frozen = [(s.arguments["x"], s.arguments["y"], s.arguments["z"]) for s in digs]
    assert frozen == [(10, 64, 5), (10, 65, 5)]
    assert all(s.arguments["expected_block"] == "oak_log" for s in digs)
    assert all(s.risk == "MEDIUM" for s in digs)
    picks = [s for s in plan.plan.steps if s.tool == "minecraft_pickup_item"]
    assert len(picks) == 2
    assert all(s.arguments["expected_item"] == "oak_log" for s in picks)
    for pick in picks:
        ref = pick.arguments["entity_id"]["from_step"]
        source = next(s for s in plan.plan.steps if s.step_id == ref)
        assert source.tool == "minecraft_dropped_items"
    assert plan.plan.expected_final_state.inventory_delta == {"oak_log": 2}
    assert plan.plan.steps[-1].tool == "minecraft_inventory"
    actions = [s for s in plan.plan.steps if s.risk != "SAFE"]
    assert len(actions) <= 8


async def test_vertical_gap_never_fabricates_the_second_target() -> None:
    """约束 2 + §5.3：同列跳格只能证明下面那一格 → 只计划 1 个，绝不假称整棵树的 2 根。"""
    plan = await _plan([_match(OAK, 10, 64, 5), _match(OAK, 10, 66, 5)])
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 1
    assert digs[0].arguments["y"] == 64
    assert plan.plan.expected_final_state.inventory_delta == {"oak_log": 1}


async def test_truncation_is_visible_before_confirmation() -> None:
    """§5.3 行 160：请求数量被截断必须在确认前明确显示（写进 checks，回复层据此说明）。"""
    observe = FakeObserve(matches=[_match(OAK, 10, 64, 5), _match(OAK, 10, 66, 5)])
    result = await _planner(allow_medium=True).plan(
        "帮我砍两根橡木原木带回来", observe=observe, shape_hint="wood"
    )
    assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
    detail = result.checks[-1]["detail"]
    assert detail["requested"] == 2
    assert detail["targets"] == 1
    assert detail["truncated"] is True
    assert detail["reason"] == "wood_target_group_unproven"


async def test_separate_columns_cannot_be_proven_as_one_tree() -> None:
    """约束 2：不同 (x,z) 列各自只证明 1 个 → 数量取最小值，不硬凑 2 个。"""
    plan = await _plan([_match(OAK, 10, 64, 5, 4.0), _match(OAK, 20, 64, 9, 2.0)])
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 1
    assert plan.plan.expected_final_state.inventory_delta == {"oak_log": 1}


async def test_different_species_are_never_mixed_into_one_group() -> None:
    """同一列里两种原木不算同一组：只按能证明的那个目标走。"""
    plan = await _plan([_match(OAK, 10, 64, 5, 5.0), _match(SPRUCE, 10, 65, 5, 2.0)])
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 1
    chosen = digs[0].arguments["expected_block"]
    assert chosen in {"oak_log", "spruce_log"}
    assert plan.plan.expected_final_state.inventory_delta == {chosen: 1}


async def test_no_logs_found_is_a_safe_refusal() -> None:
    with pytest.raises(ObservationFailed) as excinfo:
        await _plan([])
    assert "no_wood_target" in str(excinfo.value)


async def test_count_never_exceeds_the_hard_limit() -> None:
    """预算不随请求扩大：即使要 5 根，计划里最多 2 个目标。"""
    matches = [_match(OAK, 10, 64 + index, 5) for index in range(5)]
    plan = await _plan(matches, count=5)
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 2
    assert plan.plan.expected_final_state.inventory_delta == {"oak_log": 2}


async def test_unreachable_high_logs_are_filtered_before_grouping() -> None:
    """复用既有 _reachable_height：只选脚下 +1 格以内够得着的那一节。"""
    plan = await _plan([_match(OAK, 10, 70, 5), _match(OAK, 10, 64, 5)], count=1)
    digs = [s for s in plan.plan.steps if s.tool == "minecraft_dig"]
    assert len(digs) == 1
    assert digs[0].arguments["y"] == 64


async def test_move_to_is_added_only_when_needed() -> None:
    plan = await _plan([_match(OAK, 10, 64, 5)], count=1, can_dig=False, reason="too_far")
    assert plan.plan.steps[0].tool == "minecraft_move_to"
    assert plan.plan.steps[0].risk == "LOW"


async def test_dig_capability_refusal_is_reported_truthfully() -> None:
    """挖不动且不是太远 → 如实失败，不生成计划。"""
    with pytest.raises(ObservationFailed) as excinfo:
        await _plan([_match(OAK, 10, 64, 5)], count=1, can_dig=False, reason="cannot_dig")
    assert "cannot_dig" in str(excinfo.value)


def _planner(*, allow_medium: bool) -> BoundedAgentPlanner:
    return BoundedAgentPlanner(allow_safe=True, allow_low=True, allow_medium=allow_medium)


async def test_medium_disabled_blocks_wood_at_planning_time() -> None:
    """§3.1/§6.1：allow_medium=false → 规划期 BLOCKED_BY_POLICY。"""
    observe = FakeObserve(matches=[_match(OAK, 10, 64, 5), _match(OAK, 10, 65, 5)])
    result = await _planner(allow_medium=False).plan(
        "帮我砍两根橡木原木带回来", observe=observe, shape_hint="wood"
    )
    assert result.outcome is PlanningOutcome.BLOCKED_BY_POLICY
    assert result.reason == "minecraft_dig:MEDIUM"
    assert result.plan is not None


async def test_medium_enabled_makes_the_wood_plan_ready() -> None:
    """§6.1 场景二：隔离配置允许 MEDIUM 时，计划进入既有确认门。"""
    observe = FakeObserve(matches=[_match(OAK, 10, 64, 5), _match(OAK, 10, 65, 5)])
    result = await _planner(allow_medium=True).plan(
        "帮我砍两根橡木原木带回来", observe=observe, shape_hint="wood"
    )
    assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
    assert result.reason == "wood_plan_ready"
    assert result.risk_summary["max_risk"] == "MEDIUM"


async def test_unsupported_wood_is_refused_at_planning_time() -> None:
    observe = FakeObserve(matches=[])
    result = await _planner(allow_medium=True).plan(
        "帮我砍两根竹子带回来", observe=observe, shape_hint="wood"
    )
    assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION
    assert result.reason.startswith("unsupported_species:")
    assert observe.calls == []


async def test_wood_is_selected_by_the_explicit_shape_hint() -> None:
    """约束 1：木材形状由 MC 入口的确定性解析 + shape_hint 选定；全局形状判定逐字不变。"""
    assert detect_shape("帮我砍两棵橡木原木带回来") == "resource"
    assert detect_shape("帮我挖两块圆石带回来") == "resource"
    observe = FakeObserve(matches=[_match(OAK, 10, 64, 5)])
    hinted = await _planner(allow_medium=True).plan(
        "帮我砍两棵橡木原木带回来", observe=observe, shape_hint="wood"
    )
    assert hinted.reason == "wood_plan_ready"
    unhinted = await _planner(allow_medium=True).plan("帮我砍两棵橡木原木带回来", observe=observe)
    assert unhinted.reason != "wood_plan_ready"
