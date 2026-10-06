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
    ArgumentReference,
    TaskPlan,
    TaskStep,
    canonical_arguments,
)

#: 计划里明确**禁止**的名字（复合工具/黑盒）：Phase 5A 不允许出现
FORBIDDEN_TOOL_PARTS = ("gather", "collect", "auto_mine", "mining_loop", "task_", "_batch")

#: 第一期的硬上限（§七十）
MAX_STEPS = 16
MAX_ACTION_STEPS = 8


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
    if tool == "minecraft_inventory":
        return "重新读一次背包"
    return tool


def plan_step_labels(plan: TaskPlan) -> Sequence[str]:
    return [default_step_label(step) for step in plan.steps]
