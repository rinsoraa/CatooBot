"""Phase 8G G1：工作台感知（只读、有界、可解释）。

数据来源全部是**既有** SAFE 只读能力，不新增观察能力、不改世界、不派发动作：

* ``minecraft_find_blocks``：真实世界里 ``crafting_table`` 的方块坐标（有界半径/条数）；
* ``minecraft_world``：当前维度、位置与观测新鲜度（``captured_at`` / ``age_seconds``）。

约束（任务书 §3.2）：

1. 搜索必须有半径上限；
2. 只凭坐标**不**声称可达 —— 距离按运行时自己的口径（眼睛 → 方块中心）算，
   超过交互上限即 ``UNREACHABLE``（执行期 runtime 还会**实时**重判一次）；
3. 找不到 ≠ 没能力找（``NOT_FOUND`` vs ``UNKNOWN``/``ERROR``）；
4. 观测过期或维度变化 → ``STALE``，绝不把旧快照当成当前世界状态；
5. 全程零世界修改、零动作、零任务（纯观察）。

执行期还有第二道真实校验：``minecraft_craft`` 在 runtime 里会**实时**重读那个坐标的方块
（air → ``table.missing``、不是工作台 → ``table.invalid``、太远 → ``table.too_far``），
所以即使计划里的定位过期，也不可能对着错误的方块合成 —— 只会如实失败。
"""

from __future__ import annotations

import math
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

#: 观察函数形状：``(tool, arguments) -> 调用结果``（与 planner 的 ``ObserveFn`` 同构；
#: 这里**自己定义**，避免与 planner 互相导入）。
ObserveFn = Callable[[str, Mapping[str, Any]], Awaitable[Any]]

#: 唯一被接受的工作台方块名（与 runtime 的 ``CRAFTING_TABLE_BLOCK`` 同口径）。
WORKBENCH_BLOCK = "crafting_table"

#: 工作台的交互距离上限（眼睛 → 方块中心），与 runtime 的
#: ``MC_CRAFTING_TABLE_MAX_DISTANCE`` 默认值一致；超过就是 ``UNREACHABLE``。
WORKBENCH_MAX_DISTANCE = 5.0

#: 一次查询最多看几个候选（有界）。
WORKBENCH_MAX_CANDIDATES = 8

#: 站立玩家的眼睛高度（方块坐标下的近似值；只用于**距离口径**，不用于任何动作）。
EYE_HEIGHT = 1.62


class WorkbenchStatus(StrEnum):
    """工作台状态（任务书 §3.1 的六种语义，一个都不能省）。"""

    AVAILABLE = "AVAILABLE"
    NOT_FOUND = "NOT_FOUND"
    UNREACHABLE = "UNREACHABLE"
    STALE = "STALE"
    UNKNOWN = "UNKNOWN"
    ERROR = "ERROR"


@dataclass(frozen=True)
class WorkbenchObservation:
    """一次工作台观察的**全部**结论（含失败路径，便于如实回复用户）。"""

    status: WorkbenchStatus
    reason: str = ""
    detail: str = ""
    position: tuple[int, int, int] | None = None
    dimension: str = ""
    distance: float | None = None
    observed_at: float | None = None
    age_seconds: float | None = None
    candidates: int = 0
    source: str = ""

    @property
    def usable(self) -> bool:
        """只有 ``AVAILABLE`` 才能用于**执行**（其余一律不能冒充可用）。"""
        return self.status is WorkbenchStatus.AVAILABLE

    def crafting_table_argument(self) -> dict[str, int] | None:
        """给 ``minecraft_craft`` 的 ``crafting_table`` 参数（不可用时为 ``None``）。"""
        if not self.usable or self.position is None:
            return None
        x, y, z = self.position
        return {"x": int(x), "y": int(y), "z": int(z)}

    def to_payload(self) -> dict[str, Any]:
        """写进计划 checks / 任务结果的审计快照（绝不包含原始世界数据）。"""
        payload: dict[str, Any] = {
            "status": str(self.status),
            "reason": self.reason,
            "source": self.source,
            "candidates": int(self.candidates),
        }
        if self.position is not None:
            payload["position"] = {
                "x": self.position[0],
                "y": self.position[1],
                "z": self.position[2],
            }
        if self.dimension:
            payload["dimension"] = self.dimension
        if self.distance is not None:
            payload["distance"] = round(float(self.distance), 3)
        if self.age_seconds is not None:
            payload["age_seconds"] = round(float(self.age_seconds), 3)
        if self.detail:
            payload["detail"] = self.detail[:120]
        return payload

    def explain(self) -> str:
        """给用户的一句话解释（如实说明为什么不能做）。"""
        if self.status is WorkbenchStatus.AVAILABLE:
            return "附近有可用的工作台"
        if self.status is WorkbenchStatus.NOT_FOUND:
            return f"搜索范围内没有找到工作台（{self.detail or '-'}）"
        if self.status is WorkbenchStatus.UNREACHABLE:
            return f"找到了工作台但够不到（{self.detail or '-'}）"
        if self.status is WorkbenchStatus.STALE:
            return f"工作台定位已过期（{self.detail or '-'}）"
        if self.status is WorkbenchStatus.ERROR:
            return f"查工作台时出错（{self.detail or '-'}）"
        return f"无法确认工作台状态（{self.detail or '-'}）"


def _payload_of(invocation: Any) -> dict[str, Any]:
    """从观察调用结果里取**语义载荷**（与 planner 同口径，读不出就空字典）。"""
    raw = getattr(invocation, "result", None)
    if isinstance(raw, Mapping):
        inner = raw.get("result")
        if isinstance(inner, Mapping):
            return dict(inner)
        return dict(raw)
    return {}


def interaction_distance(position: Mapping[str, Any], block: tuple[int, int, int]) -> float:
    """眼睛 → 方块中心 的欧氏距离（与 runtime 的 ``table.too_far`` 同口径）。"""
    px = _number(position.get("x"))
    py = _number(position.get("y"))
    pz = _number(position.get("z"))
    if px is None or py is None or pz is None:
        return math.inf
    dx = (px + 0.0) - (block[0] + 0.5)
    dy = (py + EYE_HEIGHT) - (block[1] + 0.5)
    dz = (pz + 0.0) - (block[2] + 0.5)
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def _number(value: Any) -> float | None:
    """宽松取数（拿不到就是 ``None``；绝不把未知当成 0）。"""
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _self_state(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any] | None, str]:
    """世界视图里的「自己」：位置与维度（读不出就是 ``(None, '')``）。"""
    semantic = payload.get("semantic")
    if not isinstance(semantic, Mapping):
        return None, ""
    # 真实世界视图的语义键是 self（pydantic 字段 self_state 的 alias）；
    # 兼容 self_state 形状（替身/旧数据），最后才退化到 semantic 本身。
    state = semantic.get("self")
    if not isinstance(state, Mapping):
        state = semantic.get("self_state")
    if not isinstance(state, Mapping):
        state = semantic
    dimension = str(state.get("dimension") or "")
    return state, dimension


def _candidates(payload: Mapping[str, Any]) -> tuple[list[tuple[int, int, int]], str]:
    """从 ``find_blocks`` 结果里取工作台坐标；返回 ``(坐标, 问题原因码)``。"""
    raw = payload.get("matches")
    if not isinstance(raw, list):
        return [], "missing_matches"
    found: list[tuple[int, int, int]] = []
    for row in raw:
        if not isinstance(row, Mapping):
            continue
        block = row.get("block")
        name = ""
        if isinstance(block, Mapping):
            name = str(block.get("name") or "")
        elif isinstance(block, str):
            name = block
        if name and name != WORKBENCH_BLOCK:
            # 真实数据说这个方块不是工作台 → 不认（绝不按"看起来像"接受）
            continue
        position = row.get("position")
        if not isinstance(position, Mapping):
            return [], "missing_position"
        coords = tuple(_number(position.get(axis)) for axis in ("x", "y", "z"))
        if any(value is None for value in coords):
            return [], "missing_position"
        found.append((int(coords[0]), int(coords[1]), int(coords[2])))  # type: ignore[arg-type]
    return found, ""


async def observe_workbench(
    observe: ObserveFn,
    *,
    radius: int,
    ttl_seconds: float = 30.0,
    now: float | None = None,
    max_candidates: int = WORKBENCH_MAX_CANDIDATES,
) -> WorkbenchObservation:
    """有界地观察工作台（纯只读）。

    任何失败都变成**明确状态**（``ERROR`` / ``UNKNOWN`` / ``NOT_FOUND``），
    绝不返回「大概能用」—— 调用方只看 :attr:`WorkbenchObservation.usable`。
    """
    stamp = float(now if now is not None else time.time())
    bounded_radius = max(1, int(radius))
    try:
        world = await observe("minecraft_world", {})
    except Exception as exc:  # noqa: BLE001 - 观察失败本身就是结论
        return WorkbenchObservation(
            status=WorkbenchStatus.ERROR,
            reason="world_read_failed",
            detail=type(exc).__name__,
            observed_at=stamp,
            source="minecraft_world",
        )
    world_payload = _payload_of(world)
    if not world_payload:
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason="world_unavailable",
            observed_at=stamp,
            source="minecraft_world",
        )
    if world_payload.get("available") is False or world_payload.get("online") is False:
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason="world_offline",
            detail=str(world_payload.get("reason") or "")[:80],
            observed_at=stamp,
            source="minecraft_world",
        )
    state, dimension = _self_state(world_payload)
    age = _number(world_payload.get("age_seconds"))
    if age is not None and age > float(ttl_seconds):
        return WorkbenchObservation(
            status=WorkbenchStatus.STALE,
            reason="world_stale",
            detail=f"世界视图已过期 {age:.1f}s > {float(ttl_seconds):.1f}s",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_world",
        )
    try:
        invocation = await observe(
            "minecraft_find_blocks",
            {
                "block_names": [WORKBENCH_BLOCK],
                "max_distance": bounded_radius,
                "max_results": max(1, int(max_candidates)),
            },
        )
    except Exception as exc:  # noqa: BLE001
        return WorkbenchObservation(
            status=WorkbenchStatus.ERROR,
            reason="find_blocks_failed",
            detail=type(exc).__name__,
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    payload = _payload_of(invocation)
    if not payload:
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason="find_blocks_unavailable",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    if payload.get("ok") is False:
        return WorkbenchObservation(
            status=WorkbenchStatus.ERROR,
            reason="find_blocks_error",
            detail=str(payload.get("error") or payload.get("message") or "")[:80],
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    coords, problem = _candidates(payload)
    if problem == "missing_matches":
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason="find_blocks_shape",
            detail="结果里没有 matches",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    if problem:
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason=problem,
            detail="方块坐标数据缺失",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    if not coords:
        return WorkbenchObservation(
            status=WorkbenchStatus.NOT_FOUND,
            reason="workbench_not_found_in_range",
            detail=f"半径 {bounded_radius} 格内没有 crafting_table",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    if state is None or not isinstance(state.get("position"), Mapping):
        return WorkbenchObservation(
            status=WorkbenchStatus.UNKNOWN,
            reason="missing_self_position",
            detail="拿不到罐头自己的位置，无法判断可达性",
            dimension=dimension,
            age_seconds=age,
            observed_at=stamp,
            source="minecraft_find_blocks",
        )
    position = state["position"]
    unique = sorted(set(coords))
    ranked = sorted(
        ((interaction_distance(position, item), item) for item in unique),
        key=lambda pair: (pair[0], pair[1]),
    )
    distance, chosen = ranked[0]
    if distance > WORKBENCH_MAX_DISTANCE:
        return WorkbenchObservation(
            status=WorkbenchStatus.UNREACHABLE,
            reason="workbench_too_far",
            detail=f"最近的 {chosen} 距离 {distance:.2f} > {WORKBENCH_MAX_DISTANCE:.1f}",
            position=chosen,
            dimension=dimension,
            distance=distance,
            age_seconds=age,
            observed_at=stamp,
            candidates=len(unique),
            source="minecraft_find_blocks",
        )
    return WorkbenchObservation(
        status=WorkbenchStatus.AVAILABLE,
        reason="workbench_available",
        position=chosen,
        dimension=dimension,
        distance=distance,
        age_seconds=age,
        observed_at=stamp,
        candidates=len(unique),
        source="minecraft_find_blocks",
    )
