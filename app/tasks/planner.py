"""计划生成（Phase 5A §四十四-§四十九/§六十八）。

**计划与执行分离**：planner 只生成计划，绝不执行任何世界动作；执行是 TaskRuntime 的事。
而且计划分两阶段（§六十八）：

* **Phase A（观察）**：只允许 SAFE 工具（``find_blocks`` / ``dig_capability`` / ``inventory`` …），
  用它们把"要去哪里、挖什么、拿什么工具"这类事实**先查清楚**；
  用它们把「要去哪里、挖什么、拿什么工具」这类事实**先查清楚**；

这样用户确认的是"知道目标在哪里之后，接下来具体要做什么"，而不是"某某未知目标，自动选择"。

两条实现路径：

* :class:`ModelTaskPlanner`：用主模型生成**结构化 JSON**（绝不解析自由文本）；
* :func:`plan_resource_task`：确定性的 Minecraft 资源任务模板（真机 smoke / 演示 / 测试用，
  不依赖模型是否可用）。
"""

from __future__ import annotations

import math
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tasks.craft_budget import DEFAULT_CRAFT_BUDGET, CraftBudget
from app.tasks.craft_plan import CraftNode, CraftPlanError, RecipeSpec, build_craft_plan
from app.tasks.models import (
    DEFAULT_ARRIVE_RADIUS,
    MAX_EXPLORE_LEGS,
    MAX_EXPLORE_ROUTE_DISTANCE,
    MIN_LEG_SEPARATION,
    ExpectedFinalState,
    TaskPlan,
    TaskStep,
    canonical_item_name,
    finite_number,
)
from app.tasks.validation import collect_references
from app.tasks.workbench import WorkbenchObservation, WorkbenchStatus, observe_workbench

#: Phase A 只允许这些工具（§二十/§六十八）
SAFE_OBSERVATION_TOOLS = frozenset(
    {
        "minecraft_world",
        "minecraft_inventory",
        "minecraft_find_blocks",
        "minecraft_dig_capability",
        "minecraft_dropped_items",
        "minecraft_recipe_lookup",
        "minecraft_container_inspect",
    }
)

#: 挖叶子/木头这类用斧头，石头/矿物用镐 —— 只是"计划里写明要换哪把"，
#: 不是"运行时自动挑最好的工具"（§四十二：那是后续 Tool Capability Knowledge）
_TOOL_SUFFIX_BY_BLOCK = {"log": "_axe", "wood": "_axe", "planks": "_axe"}


@dataclass
class Observation:
    """一次 SAFE 观察（审计用：模型当时看到的事实）。"""

    tool: str
    arguments: dict[str, Any]
    result: dict[str, Any] = field(default_factory=dict)
    summary: str = ""

    def to_payload(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "arguments": dict(self.arguments),
            "result": dict(self.result),
            "summary": self.summary,
        }


@dataclass
class PlannedTask:
    """规划结果：冻结计划 + 观察记录。"""

    objective: str
    plan: TaskPlan
    observations: list[Observation] = field(default_factory=list)


class ObservationFailed(RuntimeError):
    """Phase A 的 SAFE 观察失败（例如附近没有目标）—— 不生成动作计划，如实告诉用户。"""


ObserveFn = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


def _safe_observe(observe: ObserveFn, tool: str, arguments: Mapping[str, Any]) -> Awaitable[Any]:
    if tool not in SAFE_OBSERVATION_TOOLS:
        raise ObservationFailed(f"{tool} 不是允许的观察工具（观察阶段只能用 SAFE）")
    return observe(tool, arguments)


def _result_of(invocation: Any) -> dict[str, Any]:
    result = getattr(invocation, "result", None)
    if isinstance(result, Mapping) and isinstance(result.get("result"), Mapping):
        return dict(result["result"])
    return dict(result) if isinstance(result, Mapping) else {}


def _self_position(view: Mapping[str, Any]) -> dict[str, Any]:
    """从世界视图里取罐头自己的坐标（两种形状都认：工具扁平化结果 / service 原始视图）。"""
    position = view.get("position")
    if isinstance(position, Mapping):
        return dict(position)
    semantic = view.get("semantic")
    if isinstance(semantic, Mapping):
        self_state = semantic.get("self")
        if isinstance(self_state, Mapping) and isinstance(self_state.get("position"), Mapping):
            return dict(self_state["position"])
    return {}


def _reachable_height(
    match: Mapping[str, Any], bot_y: float | None, *, headroom: float = 1.0
) -> bool:
    """只挑"罐头现在够得着的那一截"：不选比她脚下（+1 格）更高的候选。

    这是**规划期**的取舍，不是运行时偷偷换目标：树干上半截要去爬树/垫方块才够得到，
    写进计划只会让用户的确认变成一次必然失败的移动。够不着就如实说没有可挖的目标，
    让用户自己决定（§十八：计划必须有限、明确、可审计）。
    """
    if bot_y is None:
        return True
    position = match.get("position")
    y = position.get("y") if isinstance(position, Mapping) else None
    if not isinstance(y, (int, float)):
        return True
    return float(y) <= float(bot_y) + headroom


def coverage_message(coverage: str, *, radius: int, target: str) -> str:
    """coverage 三态对应的**用户可见**结论（绝不把 PARTIAL/UNKNOWN 说成「附近没有」）。"""
    coverage = str(coverage or "UNKNOWN").upper()
    if coverage == "COMPLETE":
        return f"在本次完整检查的 {radius} 格范围内没有找到 {target}"
    if coverage == "PARTIAL":
        return (
            f"我还没能确认这 {radius} 格是否都检查到了，"
            f"所以不能确定这里有没有 {target}。"
            "可以先移动到一个已加载的区域再重新观察。"
        )
    return (
        f"我现在无法确认这 {radius} 格的观察覆盖范围，"
        f"所以不能确定这里有没有 {target}。"
        "可以先移动到一个已加载的区域再重新观察。"
    )


def observation_coverage(payload: Mapping[str, Any] | None) -> tuple[str, str]:
    """读 ``find_blocks`` 的观察覆盖状态（缺省 UNKNOWN，绝不默认 COMPLETE）。

    返回 ``(coverage, coverage_reason)``；旧响应没有 ``observation`` 字段一律按 UNKNOWN 处理 ——
    「运行时没说覆盖完整」不等于「覆盖完整」。
    """
    if not isinstance(payload, Mapping):
        return "UNKNOWN", "observation_missing"
    observation = payload.get("observation")
    if not isinstance(observation, Mapping):
        return "UNKNOWN", "observation_missing"
    coverage = str(observation.get("coverage") or "UNKNOWN").upper()
    if coverage not in ("COMPLETE", "PARTIAL", "UNKNOWN"):
        return "UNKNOWN", f"coverage_invalid:{coverage}"
    return coverage, str(observation.get("coverage_reason") or "")


async def plan_resource_task(
    objective: str,
    *,
    observe: ObserveFn,
    block_name: str,
    drop_item: str | None = None,
    max_distance: int = 16,
    equip_tool: str | None = None,
    inventory: Mapping[str, Any] | None = None,
) -> PlannedTask:
    """确定性资源任务模板（§六十六）：找 →（需要就）换工具 → 走 → 挖 → 捡 → 复核。

    只有 SAFE 观察会在这里执行（find_blocks / dig_capability / inventory）；
    其它步骤只是**写进计划**，等用户确认后由 TaskRuntime 执行。
    """
    block = canonical_item_name(block_name)
    drop = canonical_item_name(drop_item or block)
    observations: list[Observation] = []

    found = await _safe_observe(
        observe,
        "minecraft_find_blocks",
        {"block_names": [block], "max_distance": max_distance, "max_results": 8},
    )
    found_result = _result_of(found)
    observations.append(
        Observation(
            tool="minecraft_find_blocks",
            arguments={"block_names": [block], "max_distance": max_distance, "max_results": 8},
            result=found_result,
            summary=getattr(found, "summary", ""),
        )
    )
    matches = found_result.get("matches") or []
    if not found_result.get("ok"):
        raise ObservationFailed(f"附近 {max_distance} 格内没有找到 {block}")
    if not matches:
        # R1-G6：空命中只有在**覆盖完整**时才能解释成「范围内确实没有」。
        coverage, why = observation_coverage(found_result)
        prefix = "coverage_complete" if coverage == "COMPLETE" else "coverage_incomplete"
        raise ObservationFailed(
            f"{prefix}:{coverage}:{why}:"
            + coverage_message(coverage, radius=int(max_distance), target=block)
        )
    world_view = await _safe_observe(observe, "minecraft_world", {})
    world_result = _result_of(world_view)
    observations.append(
        Observation(
            tool="minecraft_world",
            arguments={},
            result=world_result,
            summary=getattr(world_view, "summary", ""),
        )
    )
    self_position = _self_position(world_result)
    bot_y = self_position.get("y")
    bot_y = float(bot_y) if isinstance(bot_y, (int, float)) else None

    # matches 已由 runtime 按"到目标的方块格距离"排好序；这里只在**可够到的候选**里取最近的那个
    candidates = [match for match in matches if _reachable_height(match, bot_y)] or matches
    target = candidates[0]
    position = target.get("position") or {}
    if not all(key in position for key in ("x", "y", "z")):
        raise ObservationFailed("找到的候选没有可用坐标")

    inventory_view = dict(inventory or {})
    if not inventory_view:
        inv = await _safe_observe(observe, "minecraft_inventory", {})
        inventory_view = _result_of(inv)
        observations.append(
            Observation(
                tool="minecraft_inventory",
                arguments={},
                result=inventory_view,
                summary=getattr(inv, "summary", ""),
            )
        )

    probe = await _safe_observe(
        observe,
        "minecraft_dig_capability",
        {"x": int(position["x"]), "y": int(position["y"]), "z": int(position["z"])},
    )
    probe_result = _result_of(probe)
    observations.append(
        Observation(
            tool="minecraft_dig_capability",
            arguments={
                "x": target.get("position", {}).get("x"),
                "y": position.get("y"),
                "z": position.get("z"),
            },
            result=probe_result,
            summary=getattr(probe, "summary", ""),
        )
    )

    steps: list[TaskStep] = []
    index = 0

    def add(tool: str, arguments: dict[str, Any], risk: str) -> TaskStep:
        nonlocal index
        index += 1
        step = TaskStep(
            step_id=f"step_{index}",
            tool=tool,
            arguments=arguments,
            risk=risk,
            references=collect_references(arguments),
        )
        steps.append(step)
        return step

    # 要不要走过去，由**运行时的实时读数**决定（§二十：能挖得动就先别动）：
    # dig_capability 的 can_dig 就是 mineflayer 的 canDigBlock（含"眼睛够不够得着"），
    # 说能挖就不用再写一步必然失败的 move_to（例如罐头就站在树干旁边）；
    # 只有 reason=too_far 才是"走过去就够得着"，其余原因（空气/挖不动/读不到）如实失败。
    can_dig = bool(probe_result.get("can_dig"))
    probe_reason = probe_result.get("reason")
    if not can_dig and probe_reason and probe_reason != "too_far":
        raise ObservationFailed(
            f"目标 ({position['x']},{position['y']},{position['z']}) 现在挖不了：{probe_reason}"
        )

    # 工具：只有"现在挖不动"或者主手为空时才把换工具写进计划（而且要写明具体是哪一把）
    held = inventory_view.get("held_item") or {}
    held_name = canonical_item_name(held.get("name")) if isinstance(held, Mapping) else ""
    needs_tool = not can_dig or not held_name
    chosen_tool = ""
    if needs_tool:
        if equip_tool:
            chosen_tool = canonical_item_name(equip_tool)
        else:
            suffix = next(
                (value for key, value in _TOOL_SUFFIX_BY_BLOCK.items() if block.endswith(key)), ""
            )
            if suffix:
                available = [
                    canonical_item_name(row.get("name"))
                    for row in (inventory_view.get("items") or [])
                    if isinstance(row, Mapping) and row.get("name")
                ]
                chosen_tool = next((name for name in available if name.endswith(suffix)), "")
        if chosen_tool:
            add("minecraft_equip", {"item": chosen_tool}, "MEDIUM")

    if not can_dig:
        add(
            "minecraft_move_to",
            {"x": int(position["x"]), "y": int(position["y"]), "z": int(position["z"])},
            "LOW",
        )
    add(
        "minecraft_dig",
        {
            "x": int(position["x"]),
            "y": int(position["y"]),
            "z": int(position["z"]),
            "expected_block": block,
            **({"expected_tool": chosen_tool} if chosen_tool else {}),
        },
        "MEDIUM",
    )
    add("minecraft_dropped_items", {}, "SAFE")
    drop_step_id = steps[-1].step_id
    # entity_id 只能是"动作之后才存在"的事实（掉落物实体刚被挖出来）→ 用引用；
    # 但 expected_item 是**计划时就知道的**（我们要捡的就是 drop）→ 写成常量。
    # 这样即使最近的那个掉落物不是目标（别人扔的鸡蛋…），pickup 也会因为
    # expected_item 不符而失败，而不是闷头捡错东西（fail-closed，§四十一）。
    add(
        "minecraft_pickup_item",
        {
            "entity_id": {"from_step": drop_step_id, "path": "items.0.entity_id"},
            "expected_item": drop,
        },
        "MEDIUM",
    )
    add("minecraft_inventory", {}, "SAFE")

    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=ExpectedFinalState(inventory_delta={drop: 1}),
        observations=[item.to_payload() for item in observations],
    )
    return PlannedTask(objective=objective, plan=plan, observations=observations)


# ---------------------------------------------------------------- wood gathering（Phase 8D）

#: 木材采集的硬上限（与 app.tasks.intent.WOOD_MAX_TARGETS 一致）。
#: 这里再钉一次，是为了让**规划层自己**证明「预算不随用户请求扩大」。
WOOD_MAX_TARGETS = 2

#: 同一个树干组里相邻原木的竖直间隔**必须正好是 1 格**（证明「同一根树干」的可观察事实）。
WOOD_GROUP_Y_STEP = 1


@dataclass(frozen=True)
class _WoodTarget:
    """一个已被真实观察确认的原木方块（Phase 8D：坐标 + 方块 id 都要冻结进计划）。"""

    block: str
    x: int
    y: int
    z: int
    distance: float


def _wood_match_target(match: Mapping[str, Any]) -> _WoodTarget | None:
    """把 find_blocks 的一条命中投影成目标（字段缺失就丢弃，绝不猜）。

    真实字段形状（test_minecraft_find_blocks_tool.py:222-226）：
    ``{"block": {"name": ...}, "position": {"x","y","z"}, "distance": ...}``。
    """
    position = match.get("position")
    block = match.get("block")
    name = block.get("name") if isinstance(block, Mapping) else None
    if not isinstance(position, Mapping) or not isinstance(name, str) or not name:
        return None
    x, y, z = position.get("x"), position.get("y"), position.get("z")
    if not isinstance(x, (int, float)):
        return None
    if not isinstance(y, (int, float)):
        return None
    if not isinstance(z, (int, float)):
        return None
    distance = match.get("distance")
    return _WoodTarget(
        block=canonical_item_name(name),
        x=int(x),
        y=int(y),
        z=int(z),
        distance=float(distance) if isinstance(distance, (int, float)) else float("inf"),
    )


def _wood_group(targets: list[_WoodTarget], count: int) -> list[_WoodTarget] | None:
    """挑出一组**可证明属于同一根树干**的原木（确定性；证明不了就返回 None）。

    只用 find_blocks 真实给出的事实（方块 id + 坐标），规则：

    * 按**精确 (x, z) 列**分组 —— 树干是竖直的，换一列就是另一棵树；
    * 同一组必须是**同一个方块 id**（不把橡木和云杉混成同一棵）；
    * 从最低那格往上必须**逐格相邻**（|Δy| == 1），中间跳格就证明不了连续树干；
    * 列间排序：连续段更长优先 → 更近优先 → (x, z) 字典序（完全可复现）；
    * 凑不满 count 就取它能证明的那几个（数量取三者最小值，§5.3）——
      一个都证明不了返回 None，调用方 fail-closed，**绝不按距离猜**。
    """
    columns: dict[tuple[int, int, str], list[_WoodTarget]] = {}
    for target in targets:
        columns.setdefault((target.x, target.z, target.block), []).append(target)
    runs: list[list[_WoodTarget]] = []
    for items in columns.values():
        run: list[_WoodTarget] = []
        for target in sorted(items, key=lambda item: item.y):
            if run and target.y - run[-1].y != WOOD_GROUP_Y_STEP:
                break
            run.append(target)
        if run:
            runs.append(run)
    if not runs:
        return None
    runs.sort(key=lambda run: (-len(run), run[0].distance, run[0].x, run[0].z))
    best = runs[0]
    return best[: max(1, min(int(count), len(best), WOOD_MAX_TARGETS))]


async def plan_wood_task(
    objective: str,
    *,
    observe: ObserveFn,
    candidates: tuple[str, ...],
    count: int = 1,
    max_distance: int = 16,
) -> PlannedTask:
    """有界木材采集模板（Phase 8D §2.2/§5.3）。

    流程：**找 → 证明同一组树干 → 逐目标 走/挖/捡 → 背包复核**。

    硬约束（都在这里、不靠注释承诺）：

    * 最多 WOOD_MAX_TARGETS 个原木方块，**不随用户请求扩大**；
    * 目标只能来自 minecraft_find_blocks 的**真实观察**，且必须能证明同列相邻；
    * 证明不了就 ObservationFailed（no_wood_target / wood_target_group_unproven），绝不猜；
    * 每个目标冻结自己的**坐标 + expected_block**；掉落物引用**它自己那一步**；
    * 成功证据 = ExpectedFinalState(inventory_delta={掉落物: 个数})（真实背包增量）。

    原木不需要工具（徒手可破），因此这里**不写 equip 步骤**，动作步数 ≤ 6（预算 8）。
    """
    names = [canonical_item_name(name) for name in candidates if str(name or "").strip()]
    if not names:
        raise ObservationFailed("no_wood_target：木材支持矩阵为空，没有可观察的原木种类")
    wanted = max(1, min(int(count), WOOD_MAX_TARGETS))
    observations: list[Observation] = []

    found_args = {"block_names": list(names), "max_distance": max_distance, "max_results": 16}
    found = await _safe_observe(observe, "minecraft_find_blocks", found_args)
    found_result = _result_of(found)
    observations.append(
        Observation(
            tool="minecraft_find_blocks",
            arguments=found_args,
            result=found_result,
            summary=getattr(found, "summary", ""),
        )
    )
    if not found_result.get("ok"):
        raise ObservationFailed("no_wood_target：找方块的观察失败（无法确认附近有没有原木）")
    targets = [
        target
        for target in (_wood_match_target(match) for match in (found_result.get("matches") or []))
        if target is not None
    ]
    if not targets:
        coverage, why = observation_coverage(found_result)
        prefix = "coverage_complete" if coverage == "COMPLETE" else "coverage_incomplete"
        raise ObservationFailed(
            f"{prefix}:{coverage}:{why}:no_wood_target:"
            + coverage_message(coverage, radius=int(max_distance), target="原木")
        )

    world_view = await _safe_observe(observe, "minecraft_world", {})
    world_result = _result_of(world_view)
    observations.append(
        Observation(
            tool="minecraft_world",
            arguments={},
            result=world_result,
            summary=getattr(world_view, "summary", ""),
        )
    )
    self_position = _self_position(world_result)
    bot_y = self_position.get("y")
    bot_y = float(bot_y) if isinstance(bot_y, (int, float)) else None

    reachable = [
        target for target in targets if _reachable_height({"position": {"y": target.y}}, bot_y)
    ] or targets
    chosen = _wood_group(reachable, wanted)
    if chosen is None:
        raise ObservationFailed(
            "wood_target_group_unproven：附近的原木证明不了属于同一根树干（同列且逐格相邻）"
        )

    steps: list[TaskStep] = []
    index = 0

    def add(tool: str, arguments: dict[str, Any], risk: str) -> TaskStep:
        nonlocal index
        index += 1
        step = TaskStep(
            step_id=f"step_{index}",
            tool=tool,
            arguments=arguments,
            risk=risk,
            references=collect_references(arguments),
        )
        steps.append(step)
        return step

    drops: dict[str, int] = {}
    for target in chosen:
        probe_args = {"x": target.x, "y": target.y, "z": target.z}
        probe = await _safe_observe(observe, "minecraft_dig_capability", probe_args)
        probe_result = _result_of(probe)
        observations.append(
            Observation(
                tool="minecraft_dig_capability",
                arguments=probe_args,
                result=probe_result,
                summary=getattr(probe, "summary", ""),
            )
        )
        can_dig = bool(probe_result.get("can_dig"))
        reason = probe_result.get("reason")
        if not can_dig and reason and reason != "too_far":
            raise ObservationFailed(f"目标 ({target.x},{target.y},{target.z}) 现在挖不了：{reason}")
        if not can_dig:
            add(
                "minecraft_move_to",
                {"x": target.x, "y": target.y, "z": target.z},
                "LOW",
            )
        add(
            "minecraft_dig",
            {
                "x": target.x,
                "y": target.y,
                "z": target.z,
                "expected_block": target.block,
            },
            "MEDIUM",
        )
        add("minecraft_dropped_items", {}, "SAFE")
        drop_step_id = steps[-1].step_id
        add(
            "minecraft_pickup_item",
            {
                "entity_id": {"from_step": drop_step_id, "path": "items.0.entity_id"},
                "expected_item": target.block,
            },
            "MEDIUM",
        )
        drops[target.block] = drops.get(target.block, 0) + 1
    add("minecraft_inventory", {}, "SAFE")

    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=ExpectedFinalState(inventory_delta=dict(drops)),
        observations=[item.to_payload() for item in observations],
    )
    return PlannedTask(objective=objective, plan=plan, observations=observations)


# ---------------------------------------------------------------- crafting（Phase 8F）

#: 单个目标物品一次任务最多几个合成批次（1 批次 = 1 次真实的 ``minecraft_craft`` 调用）。
CRAFT_MAX_BATCHES = 4

#: 合成链最多几级（目标物品 + 最多一级前置，例如 原木 → 木板 → 木棍）。
CRAFT_MAX_STAGES = 2

#: 稳定原因码（回复层据此如实说明「为什么没做」，不解析自由文本）。
CRAFT_REASON_INVENTORY = "inventory_unavailable"
CRAFT_REASON_NO_RECIPE = "no_recipe"
CRAFT_REASON_TABLE = "crafting_table_required"
CRAFT_REASON_MATERIAL = "insufficient_material"
CRAFT_REASON_BOUNDS = "count_out_of_bounds"
CRAFT_REASON_WORKBENCH = "workbench_unavailable"

#: 工作台接近步的到达半径（格）：到得够近（< 交互上限 5）就停，绝不"踩"在工作台上。
WORKBENCH_APPROACH_RADIUS = 2.0


@dataclass(frozen=True)
class _CraftRecipe:
    """一份**真实查询**得到的配方（只保留计算需要的字段）。"""

    item: str
    recipe_id: str
    per_craft: int
    ingredients: tuple[tuple[str, int], ...]
    #: Phase 8G：这个配方是否**必须**在 3×3 工作台上做（来自真实查询的 requires_table）。
    requires_table: bool = False


@dataclass(frozen=True)
class _CraftStage:
    """一级合成：做 ``batches`` 次，产出 ``per_craft * batches``。"""

    recipe: _CraftRecipe
    batches: int

    @property
    def produced(self) -> int:
        return self.recipe.per_craft * self.batches

    def consumed(self) -> dict[str, int]:
        return {name: qty * self.batches for name, qty in self.recipe.ingredients}


def inventory_counts(result: Mapping[str, Any]) -> dict[str, int] | None:
    """真实背包快照 → 物品计数（``None`` = 读不到 —— **绝不**当成空背包）。

    快照形状与既有验证器 ``TaskRuntime._counts`` 同口径：``{"items": [{"name", "count"}…]}``。
    """
    items = result.get("items")
    if not isinstance(items, list):
        return None
    counts: dict[str, int] = {}
    for row in items:
        if not isinstance(row, Mapping):
            continue
        name = canonical_item_name(row.get("name"))
        if not name:
            continue
        try:
            counts[name] = counts.get(name, 0) + int(row.get("count") or 0)
        except (TypeError, ValueError):
            continue
    return counts


def _craft_recipe_from_row(row: Mapping[str, Any], item: str) -> _CraftRecipe | None:
    """单行真实配方 -> _CraftRecipe（缺字段就返回 None，绝不猜）。"""
    recipe_id = str(row.get("recipe_id") or "")
    raw_result = row.get("result")
    result_row = raw_result if isinstance(raw_result, Mapping) else {}
    try:
        per_craft = int(result_row.get("count_per_craft") or 1)
    except (TypeError, ValueError):
        per_craft = 1
    per_craft = max(1, per_craft)
    ingredients: list[tuple[str, int]] = []
    raw_ingredients = row.get("ingredients")
    for ingredient in raw_ingredients if isinstance(raw_ingredients, list) else []:
        if not isinstance(ingredient, Mapping):
            continue
        name = canonical_item_name(ingredient.get("name"))
        try:
            qty = int(ingredient.get("count") or 0)
        except (TypeError, ValueError):
            qty = 0
        if name and qty > 0:
            ingredients.append((name, qty))
    if not recipe_id or not ingredients:
        return None
    return _CraftRecipe(
        item=item,
        recipe_id=recipe_id,
        per_craft=per_craft,
        ingredients=tuple(ingredients),
        requires_table=bool(row.get("requires_table")),
    )


def craft_candidates(
    result: Mapping[str, Any], item: str, *, allow_table: bool = False
) -> tuple[tuple[_CraftRecipe, ...], str]:
    """从**真实** minecraft_recipe_lookup 结果里取出**全部**可用候选配方。

    返回 (候选, 原因码)：候选非空时原因码为空串；候选为空时给稳定原因码。
    候选顺序是确定性的（真实 available -> 不需要工作台 -> recipe_id），但它**只是尝试顺序**：
    最终选哪一份由 build_craft_plan 用完整 DAG 可行性 + 任务书 §2.5 优先级决定，
    绝不在候选集合上提前锁定（8H H1 修复的真实缺陷）。
    """
    raw_recipes = result.get("recipes")
    rows = [
        row
        for row in (raw_recipes if isinstance(raw_recipes, list) else [])
        if isinstance(row, Mapping)
    ]
    if not rows:
        # 运行时的四态语义：status=crafting_table_required 明确表示「这个配方需要 3x3 工作台」，
        # 绝不能降级成 no_recipe（否则需要工作台的配方会被当成根本没有配方）。
        if str(result.get("status") or "") == CRAFT_REASON_TABLE:
            return (), CRAFT_REASON_TABLE
        return (), CRAFT_REASON_NO_RECIPE
    # Phase 8G：allow_table=False（8F 语义）时只认 2x2；True 时也认工作台配方。
    usable = [row for row in rows if allow_table or not bool(row.get("requires_table"))]
    if not usable:
        return (), CRAFT_REASON_TABLE
    # 真实的 available = 运行时按**当前真实背包**判定「材料够做一次」（可信事实，但**不等于**
    # 「在受限 DAG 内不可实现」—— 前置材料还能由真实子配方做出来）。
    usable.sort(
        key=lambda row: (
            not bool(row.get("available")),
            bool(row.get("requires_table")),
            str(row.get("recipe_id") or ""),
        )
    )
    candidates: list[_CraftRecipe] = []
    seen: set[str] = set()
    for row in usable:
        recipe = _craft_recipe_from_row(row, item)
        if recipe is None or recipe.recipe_id in seen:
            continue
        seen.add(recipe.recipe_id)
        candidates.append(recipe)
    if not candidates:
        return (), CRAFT_REASON_NO_RECIPE
    return tuple(candidates), ""


def craft_recipe_of(
    result: Mapping[str, Any], item: str, *, allow_table: bool = False
) -> tuple[_CraftRecipe | None, str]:
    """兼容包装（8F/8G 调用方与既有测试）：只取候选集合里的第一份。"""
    candidates, reason = craft_candidates(result, item, allow_table=allow_table)
    if not candidates:
        return None, reason
    return candidates[0], ""


def _recipe_spec(recipe: _CraftRecipe) -> RecipeSpec:
    # 真实配方 -> DAG 需要的字段（只搬运，不改写）。
    return RecipeSpec(
        item=recipe.item,
        recipe_id=recipe.recipe_id,
        per_craft=recipe.per_craft,
        ingredients=recipe.ingredients,
        requires_table=recipe.requires_table,
    )


async def plan_craft_task(
    objective: str,
    *,
    observe: ObserveFn,
    item: str,
    count: int = 0,
    budget: CraftBudget = DEFAULT_CRAFT_BUDGET,
) -> PlannedTask:
    """有界合成模板（Phase 8G §4/§5）：真实背包 + 真实配方 + 工作台感知 + 多步 DAG。

    流程：**真实读背包 → 真实查配方（按需递归）→ 库存模拟与预算检查 → 工作台感知 →
    冻结拓扑序步骤（每步带真实后置条件）→ 末尾重读背包**。

    硬约束（都在这里、不靠注释承诺）：

    * 配方只来自真实 ``recipe_lookup``（``recipe_id`` 逐字冻结），绝不猜配方/材料/产出量；
    * 多步由 :func:`app.tasks.craft_plan.build_craft_plan` 求解：合并同名需求、库存模拟、
      深度/步骤/单目标数量/总输入四道预算全部硬检查（以既有执行器更严格者为准）；
    * 需要工作台的配方**只用已经存在且可达的工作台**：找不到/够不到/过期/未知一律如实拒绝，
      绝不自动放置、破坏、挖掘或搬动任何方块（§1.2/§4.3）；
    * 够不到但**发现**了工作台时，只放一个**显式**的 LOW 接近步（``minecraft_move_to``；
      执行器绝不自己走过去），由既有确认门一并授权；
    * 产出超过用户要求时只多做**必要的整数刀**，并由回复层如实说明（绝不静默多耗材料）；
    * 成功证据 = 真实背包重读：计划级（产物净增 + 材料净耗）+ **逐步**（每步自己的前后快照）。
    """
    target = canonical_item_name(item)
    if not target:
        raise ObservationFailed(f"{CRAFT_REASON_NO_RECIPE}:empty_item")
    observations: list[Observation] = []

    inventory = await _safe_observe(observe, "minecraft_inventory", {})
    inventory_result = _result_of(inventory)
    observations.append(
        Observation(
            tool="minecraft_inventory",
            arguments={},
            result=inventory_result,
            summary=getattr(inventory, "summary", ""),
        )
    )
    counts = inventory_counts(inventory_result)
    if counts is None:
        raise ObservationFailed(f"{CRAFT_REASON_INVENTORY}:背包读不回来，无法确认材料")

    workbench: WorkbenchObservation | None = None

    async def _query(item_name: str, table: dict[str, int] | None) -> dict[str, Any]:
        # 一次真实配方查询（记录观察）。table 非空时带上工作台坐标。
        args: dict[str, Any] = {"item": item_name}
        if table:
            args["crafting_table"] = dict(table)
        invocation = await _safe_observe(observe, "minecraft_recipe_lookup", args)
        payload = _result_of(invocation)
        observations.append(
            Observation(
                tool="minecraft_recipe_lookup",
                arguments=args,
                result=payload,
                summary=getattr(invocation, "summary", ""),
            )
        )
        return payload

    async def _workbench_once() -> WorkbenchObservation:
        # 真实观察工作台（只做一次；六态原样返回，绝不假设存在）。
        nonlocal workbench
        if workbench is None:
            workbench = await observe_workbench(
                observe,
                radius=budget.workbench_search_radius,
                ttl_seconds=budget.workbench_ttl_seconds,
            )
            observations.append(
                Observation(
                    tool="craft_workbench",
                    arguments={"radius": budget.workbench_search_radius},
                    result=workbench.to_payload(),
                    summary=workbench.explain(),
                )
            )
        return workbench

    async def candidates(name: str) -> tuple[RecipeSpec, ...]:
        # 真实配方查询（记录观察），返回**全部**可用候选（2x2 与 3x3 都保留，绝不提前锁定一份）。
        # 3x3 配方在**不带工作台**时，运行时按设计只回 crafting_table_required 且不带配方；
        # 所以这里必须先用真实观察确认一张工作台，再拿那张桌子的坐标重查一次。
        result = await _query(name, None)
        if result.get("ok", True) is False:
            return ()
        found, reason = craft_candidates(result, name, allow_table=True)
        if found:
            return tuple(_recipe_spec(recipe) for recipe in found)
        if reason != CRAFT_REASON_TABLE:
            return ()
        observed = await _workbench_once()
        table = observed.crafting_table_argument()
        if not observed.usable or table is None:
            raise ObservationFailed(
                f"{CRAFT_REASON_TABLE}:{observed.status}:{observed.reason}:{observed.explain()}"
            )
        recalled = await _query(name, table)
        if recalled.get("ok", True) is False:
            return ()
        found, _reason = craft_candidates(recalled, name, allow_table=True)
        return tuple(_recipe_spec(recipe) for recipe in found)

    try:
        built = await build_craft_plan(
            target,
            requested=count,
            inventory=counts,
            lookup=candidates,
            budget=budget,
            canonicalize=canonical_item_name,
        )
    except CraftPlanError as exc:
        raise ObservationFailed(f"{exc.reason}:{exc.detail}") from None

    table_argument: dict[str, int] = {}
    approach_position: tuple[int, int, int] | None = None
    if built.requires_table:
        workbench = await _workbench_once()
        if workbench.usable:
            table_argument = dict(workbench.crafting_table_argument() or {})
        elif (
            workbench.status is WorkbenchStatus.UNREACHABLE
            and workbench.reason == "workbench_too_far"
            and budget.allow_approach
            and workbench.position is not None
        ):
            # 够不到但**确实存在**：只放一个显式的接近步（LOW），到位后由 runtime 实时复核。
            approach_position = workbench.position
            table_argument = {
                "x": int(approach_position[0]),
                "y": int(approach_position[1]),
                "z": int(approach_position[2]),
            }
        else:
            raise ObservationFailed(
                f"{CRAFT_REASON_TABLE}:{workbench.status}:{workbench.reason}:{workbench.explain()}"
            )

    steps: list[TaskStep] = []
    index = 0

    def add(tool: str, arguments: dict[str, Any], risk: str, **extra: Any) -> TaskStep:
        nonlocal index
        index += 1
        step = TaskStep(
            step_id=f"step_{index}",
            tool=tool,
            arguments=arguments,
            risk=risk,
            references=collect_references(arguments),
            **extra,
        )
        steps.append(step)
        return step

    approach_id = ""
    if approach_position is not None:
        approach_id = add(
            "minecraft_move_to",
            {
                "x": int(approach_position[0]),
                "y": int(approach_position[1]),
                "z": int(approach_position[2]),
            },
            "LOW",
            expected_state=ExpectedFinalState(
                position_within={
                    "x": float(approach_position[0]),
                    "y": float(approach_position[1]),
                    "z": float(approach_position[2]),
                    "radius": float(WORKBENCH_APPROACH_RADIUS),
                }
            ),
        ).step_id

    # 每批次一个**真实**动作步：参数只有冻结的 recipe_id（+ 需要工作台时的明确坐标）。
    # 依赖来自**真实的材料生产者**（没有生产者就是在初始库存里，不产生依赖）。
    producer: dict[str, str] = {}
    for node in built.nodes:
        deps: list[str] = []
        for name, _qty in node.recipe.ingredients:
            producer_id = producer.get(canonical_item_name(name))
            if producer_id and producer_id not in deps:
                deps.append(producer_id)
        if node.recipe.requires_table and approach_id and approach_id not in deps:
            deps.append(approach_id)
        arguments: dict[str, Any] = {"recipe_id": node.recipe.recipe_id}
        if node.recipe.requires_table and table_argument:
            arguments["crafting_table"] = dict(table_argument)
        last_id = ""
        for _batch in range(node.batches):
            step_deps = tuple([*deps, last_id] if last_id else deps)
            last_id = add(
                "minecraft_craft",
                dict(arguments),
                "MEDIUM",
                depends_on=step_deps,
                expected_state=step_expectation(node),
            ).step_id
        producer[canonical_item_name(node.item)] = last_id
    add("minecraft_inventory", {}, "SAFE")

    observations.append(
        Observation(
            tool="craft_plan",
            arguments={},
            result=built.describe(),
            summary=(
                f"合成计划：{len(built.nodes)} 个配方 / {built.craft_steps} 刀 / 深度 {built.depth}"
            ),
        )
    )
    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=ExpectedFinalState(
            inventory_delta=dict(built.inventory_delta),
            inventory_consumed=dict(built.inventory_consumed),
        ),
        observations=[item_.to_payload() for item_ in observations],
    )
    return PlannedTask(objective=objective, plan=plan, observations=observations)


def step_expectation(node: CraftNode) -> ExpectedFinalState:
    """一步自己的**真实**后置条件：产物净增 + 材料净耗（同一物品两边都有时只写净值）。"""
    net: dict[str, int] = {canonical_item_name(node.item): max(1, int(node.recipe.per_craft))}
    for name, qty in node.recipe.ingredients:
        key = canonical_item_name(name)
        net[key] = net.get(key, 0) - int(qty)
    delta = {name: value for name, value in net.items() if value > 0}
    consumed = {name: -value for name, value in net.items() if value < 0}
    return ExpectedFinalState(inventory_delta=delta, inventory_consumed=consumed)


def craft_plan_summary(planned: PlannedTask) -> dict[str, Any]:
    """取回计划的 DAG / 工作台摘要（规划层与回复层共用；取不到就是空字典）。

    只从**计划里真实记录的观察**里读，绝不重新推导一遍（那是第二份真相）。
    """
    summary: dict[str, Any] = {}
    workbench: dict[str, Any] = {}
    for payload in planned.plan.observations:
        if not isinstance(payload, Mapping):
            continue
        tool = str(payload.get("tool") or "")
        result = payload.get("result")
        if tool == "craft_plan" and isinstance(result, Mapping):
            summary = dict(result)
        elif tool == "craft_workbench" and isinstance(result, Mapping):
            workbench = dict(result)
    if workbench:
        summary["workbench"] = workbench
    return summary


# ---------------------------------------------------------------- exploration（Phase 7F.1）

#: 有界探索的默认硬上限：**整条路线**的总长度不超过这个值（格）。
#: 复用既有 move_to.max_distance 的思路，不新增一批配置旋钮。
#: Phase 7F.3：这是**路线总预算**，不是每段预算 —— 多段共享同一份 24 格，
#: 绝不把单段预算变成每段预算（那是 3 倍的授权扩张）。
EXPLORE_DEFAULT_DISTANCE = MAX_EXPLORE_ROUTE_DISTANCE

#: Phase 7F.3：一条路线最多几段移动（与计划校验共用同一个常量，绝不各写一份）。
EXPLORE_MAX_LEGS = MAX_EXPLORE_LEGS

#: Phase 7F.3：单段太近就不算探索（与既有 POI 选择口径一致的 4 格）。
MIN_LEG_DISTANCE = 4.0

#: 罗盘方向 → (dx, dz) 单位向量（Minecraft：-Z = north，+X = east）。
_COMPASS_VECTORS: dict[str, tuple[int, int]] = {
    "north": (0, -1),
    "south": (0, 1),
    "east": (1, 0),
    "west": (-1, 0),
}


def _explore_target(
    view: Mapping[str, Any], sx: float, sy: float, sz: float, max_distance: float
) -> dict[str, float] | None:
    """从**真实世界视图**里挑一个有界探索坐标：优先可达的兴趣点，其次最开阔的罗盘方向。

    只读世界事实，不猜、不随机；坐标只在预算范围内取。
    """
    semantic = view.get("semantic")
    # 1) 兴趣点：世界视图里最近、且在预算范围内的 POI 坐标
    pois = semantic.get("points_of_interest") if isinstance(semantic, Mapping) else None
    best: dict[str, float] | None = None
    best_dist = float(max_distance) + 1.0
    if isinstance(pois, list):
        for item in pois:
            if not isinstance(item, Mapping):
                continue
            pos = item.get("pos")
            if not isinstance(pos, Mapping):
                continue
            try:
                tx = float(pos["x"])
                ty = float(pos["y"])
                tz = float(pos["z"])
            except (KeyError, TypeError, ValueError):
                continue
            dist = ((tx - sx) ** 2 + (tz - sz) ** 2) ** 0.5
            # 太近（就在脚边）不算"探索"；太远超出有界预算也不算
            if 4.0 <= dist <= float(max_distance) and dist < best_dist:
                best, best_dist = {"x": tx, "y": ty, "z": tz}, dist
    if best is not None:
        return best

    # 2) 兜底：从 terrain 聚合里挑**最开阔**的罗盘方向，向前走有界的一步
    terrain = semantic.get("terrain") if isinstance(semantic, Mapping) else None
    openness: dict[str, float] = {}
    if isinstance(terrain, list):
        for item in terrain:
            if not isinstance(item, Mapping):
                continue
            direction = item.get("direction")
            distance = item.get("distance")
            if isinstance(direction, str) and isinstance(distance, (int, float)):
                openness[direction] = max(openness.get(direction, 0.0), float(distance))
    direction = ""
    if openness:
        direction = max(openness, key=lambda key: (openness[key], key))
    if direction not in _COMPASS_VECTORS:
        direction = "north"
    step = max(4.0, min(float(max_distance), openness.get(direction, float(max_distance)) - 1.0))
    dx, dz = _COMPASS_VECTORS[direction]
    return {
        "x": round(sx + dx * step, 1),
        "y": sy,
        "z": round(sz + dz * step, 1),
    }


def _planar(ax: float, az: float, bx: float, bz: float) -> float:
    """水平距离（与探索目标选择、计划校验同口径：只看 x/z）。"""
    dx = ax - bx
    dz = az - bz
    return (dx * dx + dz * dz) ** 0.5


def _cell(value: Any) -> int:
    """把真实航点坐标取整成**方块格**（floor，负数同样向下）。"""
    return math.floor(float(value))


def _explore_waypoints(
    view: Mapping[str, Any],
    sx: float,
    sy: float,
    sz: float,
    max_distance: float,
    legs: int,
) -> list[dict[str, float]]:
    """确定性地生成一条**有界**路线（Phase 7F.3）。

    规则（全部确定性、可审计，只读真实世界视图）：

    * 第 1 段 = :func:`_explore_target` 的结果 —— 与 Phase 7F.1 **完全相同**；
    * 第 2..N 段只从真实 POI 里挑：坐标必须有限、与**所有**已选路点相距 >= 4 格
      （彼此有意义、不重复），且与上一段的间距也在预算内；
    * 候选按 (距离, x, z) 全序排序后逐个贪心选取 —— 同一个世界视图必然得到同一条路线；
    * 候选不够就**明确缩短路线**（返回更少的段），绝不重复路点、绝不编造坐标、
      绝不因为想要 3 段就放宽任何一条规则。

    返回的每一项是 ``{"x", "y", "z", "distance"}``，``distance`` 是**这一段本身**
    的长度（从上一段，第 1 段从起点算），会被写进冻结计划供校验与审计。
    """
    first = _explore_target(view, sx, sy, sz, max_distance)
    if first is None:  # pragma: no cover - 兜底方向永远有值
        return []
    route: list[dict[str, float]] = [
        {
            "x": float(first["x"]),
            "y": float(first["y"]),
            "z": float(first["z"]),
            "distance": _planar(float(first["x"]), float(first["z"]), sx, sz),
        }
    ]
    total = route[0]["distance"]
    limit = max(1, min(int(legs), EXPLORE_MAX_LEGS))
    if limit == 1 or total > max_distance:
        return route

    # 候选：真实 POI、坐标有限、与起点相距 >= MIN_LEG_DISTANCE 且不超单段预算
    candidates: list[tuple[float, float, float, dict[str, float]]] = []
    semantic = view.get("semantic")
    pois = semantic.get("points_of_interest") if isinstance(semantic, Mapping) else None
    if isinstance(pois, list):
        for item in pois:
            if not isinstance(item, Mapping):
                continue
            pos = item.get("pos")
            if not isinstance(pos, Mapping):
                continue
            tx = finite_number(pos.get("x"))
            ty = finite_number(pos.get("y"))
            tz = finite_number(pos.get("z"))
            if tx is None or ty is None or tz is None:
                continue
            distance = _planar(tx, tz, sx, sz)
            if distance < MIN_LEG_DISTANCE or distance > max_distance:
                continue
            candidates.append(
                (round(distance, 3), round(tx, 3), round(tz, 3), {"x": tx, "y": ty, "z": tz})
            )
    candidates.sort(key=lambda entry: (entry[0], entry[1], entry[2]))

    while len(route) < limit:
        previous = route[-1]
        picked: tuple[dict[str, float], float] | None = None
        for _, _, _, point in candidates:
            if any(
                _planar(point["x"], point["z"], chosen["x"], chosen["z"]) < MIN_LEG_SEPARATION
                for chosen in route
            ):
                continue
            step_distance = _planar(point["x"], point["z"], previous["x"], previous["z"])
            if step_distance < MIN_LEG_DISTANCE:
                continue
            if total + step_distance > max_distance + 1e-9:
                continue
            picked = (point, step_distance)
            break
        if picked is None:
            # 观察不足以安全规划更多段 -> 明确缩短路线（绝不凑段数）
            break
        point, step_distance = picked
        route.append({"x": point["x"], "y": point["y"], "z": point["z"], "distance": step_distance})
        total += step_distance
    return route


async def plan_exploration_task(
    objective: str,
    *,
    observe: ObserveFn,
    max_distance: float = EXPLORE_DEFAULT_DISTANCE,
    arrive_radius: float = DEFAULT_ARRIVE_RADIUS,
    legs: int = 1,
) -> PlannedTask:
    """确定性探索任务模板（Phase 7F.1 §四 / 7F.2）。

    看世界 → 选有界目标 → 走过去 → 复核到达 → 再观察。

    只有 SAFE 观察（minecraft_world）在**规划期**执行；move_to 只是写进计划，
    等两道门（批准计划 + 确认执行）都过了才由 TaskRuntime 执行。到达与否由
    **重新读到的世界坐标**判定，绝不由动作返回值自述。第一版只用 SAFE + LOW（MOVE），
    不碰任何世界修改能力。

    Phase 7F.2：到达后再跑一步 SAFE ``minecraft_world``（step_3），为记忆回流提供
    **执行期**的真实新事实来源；没有这一步，任务只剩"我移动过"，谈不上可确认的新观察。

    Phase 7F.3：``legs`` 请求一条**有界多段**路线（最多 3 段，全部共享同一份总距离预算）。
    ``legs=1``（默认）时输出与 Phase 7F.1/7F.2 **逐字节相同** —— 单段计划不带 ``route``
    字段，所以 plan_hash 与既有授权记录完全一致。多段时每一段的**目标坐标、到达半径、
    距离**都写进冻结计划（``expected_final_state.route``），因此进 plan_hash、进确认指纹，
    批准后任何路点改动都会被既有授权机制检测出来。
    """
    world_view = await _safe_observe(observe, "minecraft_world", {})
    world_result = _result_of(world_view)
    observations = [
        Observation(
            tool="minecraft_world",
            arguments={},
            result=world_result,
            summary=getattr(world_view, "summary", ""),
        )
    ]
    if world_result.get("online") is False or world_result.get("available") is False:
        raise ObservationFailed("罐头现在不在线的世界里，没法出去探索")
    self_position = _self_position(world_result)
    try:
        sx = float(self_position["x"])
        sy = float(self_position["y"])
        sz = float(self_position["z"])
    except (KeyError, TypeError, ValueError):
        raise ObservationFailed("世界视图里没有罐头自己的坐标，无法规划探索目标") from None

    waypoints = _explore_waypoints(world_result, sx, sy, sz, float(max_distance), legs)
    if not waypoints:  # pragma: no cover - 兜底方向永远有值
        raise ObservationFailed("世界视图里没有可用的探索方向或目标")

    if len(waypoints) == 1:
        # Phase 7F.1/7F.2 的单段计划：**逐字节不变**（不带 route 字段 -> plan_hash 不变）
        target = waypoints[0]
        steps = [
            TaskStep(
                step_id="step_1",
                tool="minecraft_move_to",
                arguments={
                    "x": _cell(target["x"]),
                    "y": _cell(target["y"]),
                    "z": _cell(target["z"]),
                },
                risk="LOW",
            ),
            TaskStep(
                step_id="step_2",
                tool="minecraft_look_at",
                arguments={
                    "x": float(target["x"]),
                    "y": float(target["y"]) + 1.5,
                    "z": float(target["z"]),
                },
                risk="SAFE",
            ),
            # Phase 7F.2：到达后做**一次**有界 SAFE 观察，让探索有真实结果可回流
            # （只读 minecraft_world；没有它就永远只有"我移动过"，没有可确认的新事实）。
            TaskStep(
                step_id="step_3",
                tool="minecraft_world",
                arguments={},
                risk="SAFE",
            ),
        ]
        expected = ExpectedFinalState(
            position_within={
                # R1：后置条件必须与 move_to 的目标**同一坐标口径**（整数方块格，floor 取整）——
                # 否则浮点航点配整数动作参数会把一次合法到达判成没到（也修掉负数向零截断）。
                "x": float(_cell(target["x"])),
                "y": float(_cell(target["y"])),
                "z": float(_cell(target["z"])),
                "radius": float(arrive_radius),
            }
        )
    else:
        # Phase 7F.3：多段路线。每一段 = 移动步 + 该段朝向步；逐段后置条件冻结进计划。
        steps = []
        route_legs: list[dict[str, Any]] = []
        for index, waypoint in enumerate(waypoints, start=1):
            move_id = f"step_{2 * index - 1}"
            look_id = f"step_{2 * index}"
            steps.append(
                TaskStep(
                    step_id=move_id,
                    tool="minecraft_move_to",
                    arguments={
                        "x": _cell(waypoint["x"]),
                        "y": _cell(waypoint["y"]),
                        "z": _cell(waypoint["z"]),
                    },
                    risk="LOW",
                )
            )
            steps.append(
                TaskStep(
                    step_id=look_id,
                    tool="minecraft_look_at",
                    arguments={
                        "x": float(waypoint["x"]),
                        "y": float(waypoint["y"]) + 1.5,
                        "z": float(waypoint["z"]),
                    },
                    risk="SAFE",
                )
            )
            route_legs.append(
                {
                    "index": index,
                    "step_id": move_id,
                    "position_within": {
                        "x": float(_cell(waypoint["x"])),
                        "y": float(_cell(waypoint["y"])),
                        "z": float(_cell(waypoint["z"])),
                        "radius": float(arrive_radius),
                    },
                    "distance": float(waypoint["distance"]),
                }
            )
        # 到达后一次有界 SAFE 观察（与单段同样的理由：给记忆回流一份执行期真实新事实）
        steps.append(
            TaskStep(
                step_id=f"step_{2 * len(waypoints) + 1}",
                tool="minecraft_world",
                arguments={},
                risk="SAFE",
            )
        )
        final = waypoints[-1]
        expected = ExpectedFinalState(
            position_within={
                "x": float(_cell(final["x"])),
                "y": float(_cell(final["y"])),
                "z": float(_cell(final["z"])),
                "radius": float(arrive_radius),
            },
            route={
                "start": {"x": sx, "y": sy, "z": sz},
                "budget": float(max_distance),
                "legs": route_legs,
            },
        )
    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=expected,
        observations=[item.to_payload() for item in observations],
    )
    return PlannedTask(objective=objective, plan=plan, observations=observations)


PLAN_SCHEMA_PROMPT = """你是 Minecraft 任务的规划器。只输出 JSON，不要解释。
可用工具（只能选这些）：{tools}
输出格式：
{{"objective": "一句话目标", "steps": [{{"tool": "工具名", "arguments": {{...}}}}],
 "expected_final_state": {{"inventory_delta": {{"物品名": 1}}}}}}
约束：最多 {max_steps} 步；不要发明工具；不要写"自动选择"这种模糊目标；
坐标必须是具体数字；观察类工具（find_blocks / dig_capability / inventory）可以放在最前面。
"""


class ModelTaskPlanner:
    """用主模型生成**结构化 JSON** 计划（§四十四）。计划生成阶段绝不执行任何动作。"""

    def __init__(
        self,
        engine: Any,
        *,
        tools: list[str],
        risk_of: Callable[[str], str],
        max_steps: int = 16,
        logger: Any = None,
    ) -> None:
        self._engine = engine
        self._tools = list(tools)
        self._risk_of = risk_of
        self._max_steps = max_steps
        self._log = logger

    def catalog_prompt(self) -> str:
        return PLAN_SCHEMA_PROMPT.format(tools="、".join(self._tools), max_steps=self._max_steps)

    async def plan(
        self,
        objective: str,
        *,
        observations: list[Observation] | None = None,
        context_note: str = "",
    ) -> TaskPlan:
        """让模型给出结构化计划，然后由 :mod:`app.tasks.validation` 做确定性校验。"""
        import json

        from app.ai.models import ChatMessage

        observed = ""
        if observations:
            observed = "\n已知观察：\n" + "\n".join(
                f"- {item.tool}({item.arguments}) → "
                + json.dumps(item.result, ensure_ascii=False)[:400]
                for item in observations
            )
        messages = [
            ChatMessage(role="system", content=self.catalog_prompt()),
            ChatMessage(
                role="user",
                content=f"目标：{objective}{observed}\n{context_note}".strip(),
            ),
        ]
        response = await self._engine.chat(messages)
        text = str(getattr(response, "content", "") or "").strip()
        payload = _extract_json(text)
        if not isinstance(payload, Mapping):
            raise ObservationFailed("模型没有给出可解析的 JSON 计划")
        steps: list[TaskStep] = []
        raw_steps = payload.get("steps")
        for index, raw in enumerate(raw_steps if isinstance(raw_steps, list) else [], start=1):
            if not isinstance(raw, Mapping):
                continue
            tool = str(raw.get("tool") or "").strip()
            arguments = raw.get("arguments")
            steps.append(
                TaskStep(
                    step_id=f"step_{index}",
                    tool=tool,
                    arguments=dict(arguments) if isinstance(arguments, Mapping) else {},
                    risk=self._risk_of(tool),
                    references=collect_references(
                        arguments if isinstance(arguments, Mapping) else {}
                    ),
                )
            )
        return TaskPlan(
            objective=str(payload.get("objective") or objective),
            steps=steps,
            expected_final_state=ExpectedFinalState.from_payload(
                payload.get("expected_final_state")
            ),
            observations=[item.to_payload() for item in (observations or [])],
        )


def _extract_json(text: str) -> Any:
    """从模型输出里取出第一个 JSON 对象（只做括号配对，绝不"猜"语义）。"""
    import json

    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        return None
    candidate = text[start : end + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return None
