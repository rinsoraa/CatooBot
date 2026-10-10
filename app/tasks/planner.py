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

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tasks.models import (
    DEFAULT_ARRIVE_RADIUS,
    ExpectedFinalState,
    TaskPlan,
    TaskStep,
    canonical_item_name,
)
from app.tasks.validation import collect_references

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
    if not found_result.get("ok") or not matches:
        raise ObservationFailed(f"附近 {max_distance} 格内没有找到 {block}")
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


# ---------------------------------------------------------------- exploration（Phase 7F.1）

#: 有界探索的默认硬上限：目标点距离当前坐标不超过这个值（格）。
#: 复用既有 move_to.max_distance 的思路，不新增一批配置旋钮。
EXPLORE_DEFAULT_DISTANCE = 24.0

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


async def plan_exploration_task(
    objective: str,
    *,
    observe: ObserveFn,
    max_distance: float = EXPLORE_DEFAULT_DISTANCE,
    arrive_radius: float = DEFAULT_ARRIVE_RADIUS,
) -> PlannedTask:
    """确定性探索任务模板（Phase 7F.1 §四）：看世界 → 选有界目标 → 走过去 → 复核到达。

    只有 SAFE 观察（minecraft_world）在**规划期**执行；move_to 只是写进计划，
    等两道门（批准计划 + 确认执行）都过了才由 TaskRuntime 执行。到达与否由
    **重新读到的世界坐标**判定，绝不由动作返回值自述。第一版只用 SAFE + LOW（MOVE），
    不碰任何世界修改能力。
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

    target = _explore_target(world_result, sx, sy, sz, float(max_distance))
    if target is None:  # pragma: no cover - 兜底方向永远有值
        raise ObservationFailed("世界视图里没有可用的探索方向或目标")

    steps = [
        TaskStep(
            step_id="step_1",
            tool="minecraft_move_to",
            arguments={"x": int(target["x"]), "y": int(target["y"]), "z": int(target["z"])},
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
    ]
    plan = TaskPlan(
        objective=objective,
        steps=steps,
        expected_final_state=ExpectedFinalState(
            position_within={
                "x": float(target["x"]),
                "y": float(target["y"]),
                "z": float(target["z"]),
                "radius": float(arrive_radius),
            }
        ),
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
