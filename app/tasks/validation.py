"""Plan 的确定性校验与参数解析（Phase 5A §四十六/§四十七/§四十八/§六十九）。

**模型说自己"计划安全"不算数** —— 生成计划之后必须由这里做纯函数式的校验：
工具是否注册、参数是否符合 schema、risk 是否认识、是否有不允许的复合工具、
是否引用了不存在的前置结果、步骤数是否越界。校验不通过就不给确认。

另外两件事也在这里（都必须是确定性的、可审计的）：

* ``resolve_arguments``：把参数里的 ``{"from_step": ..., "path": ...}`` 引用**解析成最终值**，
  并保存下来 —— 用户确认的必须是"到底要去哪个坐标"，不是"走到刚才找到的那个地方"；
* ``summarize_plan``：计划确认摘要（列出全部将执行的动作，§六十七）。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.tasks.models import (
    MAX_EXPLORE_LEGS,
    MAX_EXPLORE_ROUTE_DISTANCE,
    MIN_LEG_SEPARATION,
    ArgumentReference,
    TaskPlan,
    TaskStep,
    canonical_arguments,
    canonical_item_name,
    finite_number,
)
from app.tasks.resource_contract import RESOURCE_SPECS
from app.tasks.resource_intent import parse_collection_request

#: 计划里明确**禁止**的名字（复合工具/黑盒）：Phase 5A 不允许出现
FORBIDDEN_TOOL_PARTS = ("gather", "collect", "auto_mine", "mining_loop", "task_", "_batch")

#: 第一期的硬上限（§七十）
MAX_STEPS = 16
MAX_ACTION_STEPS = 8

#: Phase 7F.3：有界多段路线里移动段用的工具名（第一版只允许 LOW 的 move_to）。
MOVE_TOOL = "minecraft_move_to"

#: Phase 7F.3：路点距离的数值容差（规划期会把坐标四舍五入到 0.1 格）。
_LEG_DISTANCE_TOLERANCE = 0.35


def _leg_target_of(leg: Mapping[str, Any]) -> dict[str, float] | None:
    """从一个路点条目里读出**有限**目标坐标（读不出 → None，fail-closed）。"""
    raw = leg.get("position_within")
    if not isinstance(raw, Mapping):
        return None
    target: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        number = finite_number(raw.get(axis))
        if number is None:
            return None
        target[axis] = number
    radius = finite_number(raw.get("radius"))
    if radius is None or radius <= 0:
        return None
    target["radius"] = radius
    return target


def _planar_distance(a: Mapping[str, float], b: Mapping[str, float]) -> float:
    """水平距离（与探索目标选择、门禁脚本同口径：只看 x/z）。"""
    dx = float(a["x"]) - float(b["x"])
    dz = float(a["z"]) - float(b["z"])
    return (dx * dx + dz * dz) ** 0.5


def _route_problems(plan: TaskPlan) -> list[str]:
    """有界多段路线（Phase 7F.3）的确定性校验：段数 / 路点 / 顺序 / 去重 / 总距离。

    路线是**冻结计划**的一部分，所以这里校验的就是用户到底批准了什么：
    段数超上限、路点非法或重复、路点顺序与步骤顺序不符、总距离超预算 ——
    一律**拒绝整份计划**。绝不在这里静默裁剪：缩短只能发生在**规划期**
    （那时还没有任何授权）。也绝不把路线结构损坏当成没有路线
    （那等于把 fail-closed 变成 fail-open，见 ExpectedFinalState.route_invalid）。
    """
    expected = plan.expected_final_state
    if expected.route_invalid:
        reason = str((expected.route or {}).get("reason") or "结构不合法")
        return [f"路线不合法：{reason}"]
    legs = expected.legs()
    if not legs:
        return []

    problems: list[str] = []
    if len(legs) > MAX_EXPLORE_LEGS:
        problems.append(f"路线段数 {len(legs)} 超过上限 {MAX_EXPLORE_LEGS}")

    route = expected.route or {}
    budget = finite_number(route.get("budget"))
    if budget is None or budget <= 0:
        problems.append("路线缺少合法的距离预算")
        budget = None
    elif budget > MAX_EXPLORE_ROUTE_DISTANCE + _LEG_DISTANCE_TOLERANCE:
        problems.append(f"路线距离预算 {budget:.1f} 超过上限 {MAX_EXPLORE_ROUTE_DISTANCE:.1f}")
        budget = None

    start_raw = route.get("start")
    start: dict[str, float] | None = None
    if isinstance(start_raw, Mapping):
        candidate: dict[str, float] = {}
        for axis in ("x", "y", "z"):
            number = finite_number(start_raw.get(axis))
            if number is None:
                candidate = {}
                break
            candidate[axis] = number
        start = candidate or None
    if start is None:
        problems.append("路线缺少合法的规划起点坐标")

    step_positions = {step.step_id: index for index, step in enumerate(plan.steps, start=1)}
    chosen: list[dict[str, float]] = []
    total = 0.0
    previous_position = 0
    previous_target: dict[str, float] | None = start
    for leg in legs:
        index = int(leg.get("index") or 0)
        step_id = str(leg.get("step_id") or "")
        position = step_positions.get(step_id)
        if position is None:
            problems.append(f"路线第 {index} 段指向不存在的步骤（{step_id or '-'}）")
            continue
        if position <= previous_position:
            problems.append(f"路线第 {index} 段的步骤顺序不对（{step_id} 不在上一段之后）")
        previous_position = position
        step = plan.step(step_id)
        if step is None or step.tool != MOVE_TOOL:
            tool = step.tool if step is not None else "-"
            problems.append(f"路线第 {index} 段不是移动步（{step_id} → {tool}）")
        target = _leg_target_of(leg)
        if target is None:
            problems.append(f"路线第 {index} 段的目标坐标或半径不合法")
            continue
        for other in chosen:
            if _planar_distance(target, other) < MIN_LEG_SEPARATION:
                problems.append(f"路线第 {index} 段与前面的路点重复（间距过小）")
                break
        chosen.append(target)
        if previous_target is not None:
            actual = _planar_distance(previous_target, target)
            total += actual
            declared = finite_number(leg.get("distance"))
            if declared is None or abs(declared - actual) > _LEG_DISTANCE_TOLERANCE:
                problems.append(
                    f"路线第 {index} 段的距离与坐标不一致（声明 {leg.get('distance')!r}，"
                    f"实际 {actual:.1f}）"
                )
        previous_target = target

    if start is not None and budget is not None and total > budget + _LEG_DISTANCE_TOLERANCE:
        problems.append(f"路线总距离 {total:.1f} 超过预算 {budget:.1f}")

    final = expected.position_within
    if (
        final is not None
        and chosen
        and _planar_distance(chosen[-1], final) > _LEG_DISTANCE_TOLERANCE
    ):
        problems.append("路线最后一段与期望终态位置不一致")
    return problems


def validate_plan(
    plan: TaskPlan,
    *,
    risk_of: Callable[[str], str],
    is_registered: Callable[[str], bool],
    schema_of: Callable[[str], Mapping[str, Any] | None] = lambda _tool: None,
    validate_arguments: Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]] | None = None,
    online: bool = True,
    max_steps: int = MAX_STEPS,
    max_action_steps: int = MAX_ACTION_STEPS,
) -> list[str]:
    """返回问题列表（空 = 通过）。纯函数，不访问网络/世界。"""
    problems: list[str] = []
    if not plan.objective.strip():
        problems.append("计划缺少 objective")
    if not plan.steps:
        problems.append("计划里没有任何步骤")
    if len(plan.steps) > max_steps:
        problems.append(f"步骤数 {len(plan.steps)} 超过上限 {max_steps}")
    action_risks = {"LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"}
    action_steps = [step for step in plan.steps if risk_of(step.tool) in action_risks]
    if len(action_steps) > max_action_steps:
        problems.append(f"动作步骤数 {len(action_steps)} 超过上限 {max_action_steps}")

    seen_ids: set[str] = set()
    for index, step in enumerate(plan.steps, start=1):
        prefix = f"step{index}"
        if not step.step_id:
            problems.append(f"{prefix}: 缺少 step_id")
        if step.step_id in seen_ids:
            problems.append(f"{prefix}: step_id 重复（{step.step_id}）")
        seen_ids.add(step.step_id)
        if not step.tool:
            problems.append(f"{prefix}: 缺少 tool")
            continue
        lowered = step.tool.lower()
        if any(part in lowered for part in FORBIDDEN_TOOL_PARTS):
            problems.append(f"{prefix}: 不允许的复合工具名（{step.tool}）")
        if not is_registered(step.tool):
            problems.append(f"{prefix}: 工具未注册（{step.tool}）")
            continue
        risk = risk_of(step.tool)
        if not risk:
            problems.append(f"{prefix}: 工具没有风险等级（{step.tool}）")
        elif step.risk and step.risk != risk:
            problems.append(f"{prefix}: risk 与工具不符（{step.risk} != {risk}）")
        if risk in {"LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"} and not online:
            problems.append(f"{prefix}: 不在世界里，动作步骤无法执行（{step.tool}）")
        for dependency in step.depends_on:
            if dependency not in seen_ids:
                problems.append(f"{prefix}: depends_on 指向不存在或后面的步骤（{dependency}）")
        for key, reference in step.references.items():
            if reference.from_step not in seen_ids:
                problems.append(
                    f"{prefix}: 参数 {key} 引用了不存在或后面的步骤（{reference.from_step}）"
                )
            if not reference.path:
                problems.append(f"{prefix}: 参数 {key} 的引用缺少 path")
        schema = schema_of(step.tool)
        if schema and validate_arguments is not None:
            # 带引用的参数（例如"捡刚刚挖出来的那个掉落物"）在确认时还不知道最终值，
            # 所以这里先只校验**能确定的那部分**；解析出来的最终值在执行前再校验一次。
            checkable = {
                key: value
                for key, value in canonical_arguments(step.arguments).items()
                if key not in step.references
            }
            required = [name for name in (schema.get("required") or [])]
            missing_reference = [name for name in required if name in step.references]
            if not missing_reference:
                for issue in validate_arguments(schema, checkable):
                    problems.append(f"{prefix}: 参数不合 schema（{issue}）")
    problems.extend(_route_problems(plan))
    problems.extend(collection_plan_problems(plan))
    return problems


def collection_plan_problems(
    plan: TaskPlan, *, objective: str | None = None, required: bool = False
) -> list[str]:
    """Reject legacy/model collection plans that lack the R3 evidence graph.

    Atomic break-only and crafting plans keep their own contracts. Collection
    plans cannot substitute inventory gain or a nearby entity for this break.
    """
    digs = [step for step in plan.steps if step.tool == "minecraft_dig"]
    pickups = [step for step in plan.steps if step.tool == "minecraft_pickup_item"]
    text = plan.objective if objective is None else objective
    request = parse_collection_request(text)
    promised = request is not None and any(
        word in text.lower()
        for word in ("采集", "收集", "捡", "带回来", "拿回来", "collect", "gather", "harvest")
    )
    if not (
        required
        or promised
        or digs
        and (
            pickups
            or plan.expected_final_state.inventory_delta
            or any(step.arguments.get("require_collection") for step in digs)
        )
    ):
        return []
    if not digs:
        return ["resource.plan.collection_steps_required"]
    problems: list[str] = []
    if request is None or request.problem:
        problems.append("resource.plan.intent_unresolved")
    expected: dict[str, int] = {}
    allowed = {
        "minecraft_move_to",
        "minecraft_equip",
        "minecraft_dig",
        "minecraft_dropped_items",
        "minecraft_pickup_item",
        "minecraft_inventory",
    }
    if any(step.tool not in allowed for step in plan.steps):
        problems.append("resource.plan.unexpected_action")
    for dig in digs:
        args = dig.arguments
        block = canonical_item_name(args.get("expected_block"))
        spec = RESOURCE_SPECS.get(block)
        if spec is None or (request is not None and block not in request.candidates):
            problems.append("resource.plan.target_mismatch")
            continue
        if args.get("require_collection") is not True:
            problems.append("resource.plan.collection_proof_required")
        tool = isinstance(args.get("expected_tool"), str) and bool(args["expected_tool"].strip())
        empty = args.get("expected_empty_hand") is True
        if tool == empty:
            problems.append("resource.plan.hand_not_frozen")
        expected[spec.drop] = expected.get(spec.drop, 0) + 1
        matching = []
        for pickup in pickups:
            refs = pickup.references
            paths = {
                "entity_id": "attribution.server_collection.drops.0.entity_id",
                "entity_uuid": "attribution.server_collection.drops.0.uuid",
                "expected_break_action": "attribution.action_id",
            }
            if all(
                key in refs and refs[key].from_step == dig.step_id and refs[key].path == path
                for key, path in paths.items()
            ):
                matching.append(pickup)
        if len(matching) != 1:
            problems.append("resource.plan.exact_pickup_parent_required")
        elif (
            plan.steps.index(matching[0]) <= plan.steps.index(dig)
            or canonical_item_name(matching[0].arguments.get("expected_item")) != spec.drop
        ):
            problems.append("resource.plan.pickup_mismatch")
    if len(pickups) != len(digs):
        problems.append("resource.plan.pickup_count_mismatch")
    if expected != {
        canonical_item_name(name): count
        for name, count in plan.expected_final_state.inventory_delta.items()
    }:
        problems.append("resource.plan.inventory_contract_mismatch")
    blocks = {canonical_item_name(step.arguments.get("expected_block")) for step in digs}
    spec = RESOURCE_SPECS.get(next(iter(blocks))) if len(blocks) == 1 else None
    if spec is None or len(digs) > spec.max_count or (request and len(digs) > request.count):
        problems.append("resource.plan.quantity_or_family_mismatch")
    if len(digs) == 2:
        a, b = (step.arguments for step in digs)
        if (
            a.get("x") != b.get("x")
            or a.get("z") != b.get("z")
            or finite_number(a.get("y")) is None
            or finite_number(b.get("y")) is None
            or abs(a["y"] - b["y"]) != 1
        ):
            problems.append("resource.plan.log_column_unproven")
    if not plan.steps or plan.steps[-1].tool != "minecraft_inventory":
        problems.append("resource.plan.final_inventory_required")
    return problems


def read_path(payload: Any, path: str) -> Any:
    """按 ``a.0.b`` 形式在结果里取值（支持 dict 与 list 下标）。"""
    current = payload
    for part in str(path or "").split("."):
        if part == "":
            continue
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(part)
            current = current[part]
        elif isinstance(current, (list, tuple)):
            index = int(part)
            current = current[index]
        else:
            raise KeyError(part)
    return current


def resolve_arguments(
    step: TaskStep,
    results: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    """把模板参数里的引用解析成最终值（§四十七/§四十九）。

    ``results`` 是"已完成步骤 → 它的结构化结果"；引用取不到就抛 ``KeyError``
    （TaskRuntime 会把它分类成 ``TARGET_LOST`` 并停下来 —— 绝不静默换目标）。
    """
    resolved: dict[str, Any] = {}
    for key, value in step.arguments.items():
        reference = step.references.get(key)
        if reference is None:
            resolved[key] = value
            continue
        source = results.get(reference.from_step)
        if source is None:
            raise KeyError(f"{reference.from_step} 还没有结果")
        resolved[key] = read_path(source, reference.path)
    return canonical_arguments(resolved)


def collect_references(arguments: Mapping[str, Any]) -> dict[str, ArgumentReference]:
    """从参数里找出 ``{"from_step": ..., "path": ...}`` 形式的引用。"""
    references: dict[str, ArgumentReference] = {}
    for key, value in arguments.items():
        if (
            isinstance(value, Mapping)
            and "from_step" in value
            and "path" in value
            and len(value) <= 3
        ):
            references[str(key)] = ArgumentReference.from_payload(value)
    return references


def summarize_plan(plan: TaskPlan, *, label_of: Callable[[TaskStep], str] | None = None) -> str:
    """给用户看的计划摘要（§六十七：必须列出全部将执行的世界修改/低风险动作）。"""
    lines = [f"任务：{plan.objective}", "", "将执行："]
    request = parse_collection_request(plan.objective)
    targets = sum(step.tool == "minecraft_dig" for step in plan.steps)
    if request is not None and not request.problem and 0 < targets < request.count:
        lines[1:1] = [
            f"本次只能验证 {targets} 个采集目标，原请求为 {request.count} 个；"
            f"确认只执行这 {targets} 个。"
        ]
    for index, step in enumerate(plan.steps, start=1):
        label = (label_of or default_step_label)(step)
        lines.append(f"{index}. {label}")
    expected = plan.expected_final_state
    if not expected.empty:
        wants = "、".join(f"{name} ×{count}" for name, count in expected.inventory_delta.items())
        lines.append("")
        lines.append(f"完成标准：背包里至少多出 {wants}")
    return "\n".join(lines)


def default_step_label(step: TaskStep) -> str:
    """把一步翻译成一句人话（工具 + 关键参数），不泄露内部细节。"""
    arguments = step.effective_arguments
    tool = step.tool
    if tool == "minecraft_find_blocks":
        names = arguments.get("block_names") or []
        wanted = "、".join(str(name) for name in names)
        radius = arguments.get("max_distance", 16)
        return f"在 {radius} 格范围内寻找 {wanted}"
    if tool == "minecraft_dig_capability":
        return (
            f"确认 ({arguments.get('x')},{arguments.get('y')},{arguments.get('z')}) 现在挖不挖得动"
        )
    if tool == "minecraft_equip":
        return f"把 {arguments.get('item')} 拿到手里"
    if tool == "minecraft_move_to":
        return f"走到 ({arguments.get('x')},{arguments.get('y')},{arguments.get('z')}) 附近"
    if tool == "minecraft_dig":
        where = f"({arguments.get('x')},{arguments.get('y')},{arguments.get('z')})"
        return f"挖掉 {where} 的 {arguments.get('expected_block')}"
    if tool == "minecraft_dropped_items":
        return "看看附近的地上有什么掉落物"
    if tool == "minecraft_pickup_item":
        item = arguments.get("expected_item")
        if isinstance(item, Mapping):
            # 引用形态（还没解析）：只说明"用哪一步的结果"，不假装知道具体物品
            source_step = str(item.get("from_step") or "上一步")
            return f"拾取 {source_step} 找到的掉落物"
        return f"捡起刚掉出来的 {item}"
    if tool == "minecraft_place":
        item = canonical_item_name(arguments.get("expected_item"))
        return f"在 ({arguments.get('x')},{arguments.get('y')},{arguments.get('z')}) 放一个 {item}"
    if tool == "minecraft_attack":
        return f"攻击 {arguments.get('expected_type')} 一次"
    if tool == "minecraft_combat_verify":
        return "重新读实体列表，确认目标已经消失"
    if tool == "minecraft_inventory":
        return "重新读一次背包"
    return tool


def plan_step_labels(plan: TaskPlan) -> Sequence[str]:
    return [default_step_label(step) for step in plan.steps]
