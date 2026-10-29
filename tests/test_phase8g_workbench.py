"""Phase 8G G1：工作台感知（任务书 §3.3 的十项测试要求）。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from app.tasks.workbench import (
    WORKBENCH_MAX_DISTANCE,
    WorkbenchObservation,
    WorkbenchStatus,
    interaction_distance,
    observe_workbench,
)


@dataclass
class _Invocation:
    """观察调用的最小替身（只有 ``result`` 被读取）。"""

    result: Any


class _FakeObserve:
    """记录每一次观察调用；只提供**只读**世界能力（任何写动作都会被断言抓到）。"""

    def __init__(
        self,
        *,
        world: Any = None,
        blocks: Any = None,
        world_error: Exception | None = None,
        blocks_error: Exception | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._world = world
        self._blocks = blocks
        self._world_error = world_error
        self._blocks_error = blocks_error

    async def __call__(self, tool: str, arguments: Any) -> _Invocation:
        self.calls.append((tool, dict(arguments)))
        if tool == "minecraft_world":
            if self._world_error is not None:
                raise self._world_error
            return _Invocation(result=self._world)
        if tool == "minecraft_find_blocks":
            if self._blocks_error is not None:
                raise self._blocks_error
            return _Invocation(result=self._blocks)
        raise AssertionError(f"门禁不允许观察这个工具：{tool}")

    @property
    def tools(self) -> set[str]:
        return {tool for tool, _args in self.calls}


def _world(
    *,
    x: float = 0.0,
    y: float = 64.0,
    z: float = 0.0,
    age: float = 0.5,
    dimension: str = "overworld",
    available: bool = True,
    online: bool = True,
    with_position: bool = True,
) -> dict[str, Any]:
    state: dict[str, Any] = {"dimension": dimension}
    if with_position:
        state["position"] = {"x": x, "y": y, "z": z}
    return {
        "available": available,
        "online": online,
        "age_seconds": age,
        "semantic": {"self": state},  # 真实世界视图的键名（pydantic alias）
    }


def _blocks(*positions: tuple[int, int, int], name: str = "crafting_table") -> dict[str, Any]:
    return {
        "matches": [
            {"block": {"name": name}, "position": {"x": px, "y": py, "z": pz}}
            for px, py, pz in positions
        ]
    }


async def test_available_when_a_table_is_within_reach() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks((2, 64, 0)))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.AVAILABLE
    assert found.usable is True
    assert found.position == (2, 64, 0)
    assert found.distance is not None and found.distance < WORKBENCH_MAX_DISTANCE
    assert found.dimension == "overworld"
    assert found.crafting_table_argument() == {"x": 2, "y": 64, "z": 0}
    # §3.3 第 10 项：只读检查绝不触发动作/任务/世界修改
    assert observe.tools == {"minecraft_world", "minecraft_find_blocks"}


async def test_not_found_is_distinct_from_no_capability() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks())
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.NOT_FOUND
    assert found.reason == "workbench_not_found_in_range"
    assert found.usable is False
    assert found.crafting_table_argument() is None
    assert "16" in found.explain()


async def test_too_far_is_unreachable_not_available() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks((10, 64, 0)))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.UNREACHABLE
    assert found.reason == "workbench_too_far"
    assert found.crafting_table_argument() is None
    assert found.distance is not None and found.distance > WORKBENCH_MAX_DISTANCE


async def test_stale_world_view_stops_before_searching_blocks() -> None:
    observe = _FakeObserve(world=_world(age=99.0), blocks=_blocks((2, 64, 0)))
    found = await observe_workbench(observe, radius=16, ttl_seconds=30.0)
    assert found.status is WorkbenchStatus.STALE
    assert found.reason == "world_stale"
    # 过期快照**不**用来找方块（不把旧世界当成当前世界）
    assert "minecraft_find_blocks" not in observe.tools


async def test_offline_world_is_unknown() -> None:
    observe = _FakeObserve(world=_world(online=False), blocks=_blocks((2, 64, 0)))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.UNKNOWN
    assert found.reason == "world_offline"
    assert found.usable is False


async def test_world_read_failure_is_error() -> None:
    observe = _FakeObserve(world_error=RuntimeError("runtime down"))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.ERROR
    assert found.reason == "world_read_failed"


async def test_find_blocks_failure_and_error_payload_are_errors() -> None:
    failing = _FakeObserve(world=_world(), blocks_error=RuntimeError("boom"))
    raised = await observe_workbench(failing, radius=16)
    assert raised.status is WorkbenchStatus.ERROR
    assert raised.reason == "find_blocks_failed"
    refused = _FakeObserve(world=_world(), blocks={"ok": False, "error": "runtime down"})
    refused_result = await observe_workbench(refused, radius=16)
    assert refused_result.status is WorkbenchStatus.ERROR
    assert refused_result.reason == "find_blocks_error"


async def test_missing_block_coordinates_are_unknown() -> None:
    observe = _FakeObserve(
        world=_world(), blocks={"matches": [{"block": {"name": "crafting_table"}}]}
    )
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.UNKNOWN
    assert found.reason == "missing_position"


async def test_missing_matches_shape_is_unknown() -> None:
    observe = _FakeObserve(world=_world(), blocks={"found": 0})
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.UNKNOWN
    assert found.reason == "find_blocks_shape"


async def test_non_table_blocks_are_not_accepted_by_name() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks((2, 64, 0), name="furnace"))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.NOT_FOUND


async def test_missing_self_position_is_unknown() -> None:
    observe = _FakeObserve(world=_world(with_position=False), blocks=_blocks((2, 64, 0)))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.UNKNOWN
    assert found.reason == "missing_self_position"


async def test_nearest_candidate_wins_deterministically() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks((5, 64, 0), (2, 64, 0), (3, 64, 0)))
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.AVAILABLE
    assert found.position == (2, 64, 0)
    assert found.candidates == 3


async def test_search_radius_is_bounded_and_passed_through() -> None:
    observe = _FakeObserve(world=_world(), blocks=_blocks())
    await observe_workbench(observe, radius=12, max_candidates=4)
    args = dict(observe.calls[-1][1])
    assert args["block_names"] == ["crafting_table"]
    assert args["max_distance"] == 12
    assert args["max_results"] == 4
    zero = _FakeObserve(world=_world(), blocks=_blocks())
    await observe_workbench(zero, radius=0)
    assert dict(zero.calls[-1][1])["max_distance"] == 1


async def test_position_or_dimension_change_alters_the_verdict() -> None:
    near = _FakeObserve(world=_world(x=0.0), blocks=_blocks((2, 64, 0)))
    assert (await observe_workbench(near, radius=16)).status is WorkbenchStatus.AVAILABLE
    moved = _FakeObserve(world=_world(x=40.0), blocks=_blocks((2, 64, 0)))
    moved_result = await observe_workbench(moved, radius=64)
    assert moved_result.status is WorkbenchStatus.UNREACHABLE
    # 维度只作为**事实**回传（执行期由 runtime 按真实方块判定，绝不靠旧定位）
    other = _FakeObserve(world=_world(dimension="the_nether"), blocks=_blocks((2, 64, 0)))
    other_result = await observe_workbench(other, radius=16)
    assert other_result.dimension == "the_nether"


def test_interaction_distance_matches_the_runtime_convention() -> None:
    # 眼睛 → 方块中心：站在方块正上方一格时约 1.62 高差
    assert interaction_distance({"x": 0.0, "y": 64.0, "z": 0.0}, (0, 64, 0)) == pytest.approx(
        1.3245, abs=1e-3
    )
    assert interaction_distance({}, (0, 64, 0)) == float("inf")


def test_payload_snapshot_is_auditable_and_bounded() -> None:
    observation = WorkbenchObservation(
        status=WorkbenchStatus.AVAILABLE,
        reason="workbench_available",
        position=(1, 2, 3),
        dimension="overworld",
        distance=1.23456,
        candidates=2,
        source="minecraft_find_blocks",
        detail="x" * 400,
    )
    payload = observation.to_payload()
    assert payload["status"] == "AVAILABLE"
    assert payload["position"] == {"x": 1, "y": 2, "z": 3}
    assert payload["distance"] == 1.235
    assert len(str(payload["detail"])) <= 120


async def test_legacy_self_state_key_is_still_accepted() -> None:
    """兼容形状：替身/旧数据用 self_state 键也必须能读出位置。"""
    observe = _FakeObserve(
        world={
            "available": True,
            "online": True,
            "age_seconds": 0.5,
            "semantic": {
                "self_state": {
                    "dimension": "overworld",
                    "position": {"x": 0.0, "y": 64.0, "z": 0.0},
                }
            },
        },
        blocks=_blocks((2, 64, 0)),
    )
    found = await observe_workbench(observe, radius=16)
    assert found.status is WorkbenchStatus.AVAILABLE
    assert found.position == (2, 64, 0)
