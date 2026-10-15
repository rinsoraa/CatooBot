"""Phase 8G G3：受限多步合成 DAG 的**构建与库存模拟**（纯逻辑，不做任何 I/O）。

为什么不能"递归展开配方"就算完（任务书 §5.1）：配方展开会踩三类真实的坑 ——
多个分支重复消费同一份库存、中间产物既被消耗又被产出、以及无界展开。
所以这里做的是**带库存模拟的需求求解**：

1. 深度优先地满足需求：先用**当前库存**抵扣，再决定要合成多少（``batches = ceil(缺口/q)``）；
2. 同名需求天然合并 —— 后来者先吃前面剩下的库存，绝不重复承诺同一份材料；
3. 每个节点登记**真实配方与生产/消耗**，节点顺序就是依赖优先的拓扑序；
4. 循环配方用"正在展开栈"检测，深度/步骤数/单目标数量/总输入量四道预算全部硬检查；
5. 结束时把模拟后的库存与初始库存相减，得到**净增**与**净耗**（同一物品两边都有时只算净值）。

配方**只能**来自调用方传入的真实查询（``lookup``），本模块既不猜配方也不改世界。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field

from app.tasks.craft_budget import CraftBudget

#: 稳定原因码（与 Phase 8F 的既有原因码保持同名，便于日志与回复连续）。
CRAFT_PLAN_MATERIAL = "insufficient_material"
CRAFT_PLAN_NO_RECIPE = "no_recipe"
CRAFT_PLAN_TABLE = "crafting_table_required"
CRAFT_PLAN_COUNT = "count_out_of_bounds"
CRAFT_PLAN_DEPTH = "depth_exceeded"
CRAFT_PLAN_STEPS = "step_budget_exceeded"
CRAFT_PLAN_INPUTS = "input_budget_exceeded"
CRAFT_PLAN_CYCLE = "recipe_cycle"
CRAFT_PLAN_EMPTY = "empty_item"


class CraftPlanError(Exception):
    """构建失败：带**稳定原因码**（``reason``），调用方据此如实回复用户。"""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}:{detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class RecipeSpec:
    """一份**真实查询**得到的配方（G3 只需要这些字段）。"""

    item: str
    recipe_id: str
    per_craft: int
    ingredients: tuple[tuple[str, int], ...]
    requires_table: bool = False


@dataclass(frozen=True)
class CraftNode:
    """DAG 里的一个合成操作（一个配方 × ``batches`` 刀）。"""

    item: str
    recipe: RecipeSpec
    batches: int
    depth: int

    @property
    def produced(self) -> int:
        return self.recipe.per_craft * self.batches

    def consumed(self) -> dict[str, int]:
        return {name: qty * self.batches for name, qty in self.recipe.ingredients}


@dataclass(frozen=True)
class CraftPlan:
    """冻结的合成计划（拓扑序节点 + 库存模拟结果 + 真实后置条件）。"""

    target: str
    requested: int
    nodes: tuple[CraftNode, ...]
    inventory_before: dict[str, int]
    projected: dict[str, int]
    inventory_delta: dict[str, int]
    inventory_consumed: dict[str, int]
    requires_table: bool
    depth: int
    craft_steps: int
    total_inputs: int
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: 目标数量被预算夹取过（必须由回复层**明确披露**，绝不静默少做）。
    capped: bool = False
    #: 实际打算做的目标数量（夹取之后；``requested`` 仍是用户原话里的数量）。
    planned_amount: int = 0

    def produced_of(self, item: str) -> int:
        return sum(node.produced for node in self.nodes if node.item == item)

    def consumed_of(self, item: str) -> int:
        return sum(node.consumed().get(item, 0) for node in self.nodes)

    def describe(self) -> dict[str, object]:
        """给 checks/审计用的结构化摘要（不泄露原始世界数据）。"""
        return {
            "target": self.target,
            "requested": self.requested,
            "planned_amount": self.planned_amount,
            "capped": self.capped,
            "craft_steps": self.craft_steps,
            "batches": self.craft_steps,
            "depth": self.depth,
            "requires_table": self.requires_table,
            "total_inputs": self.total_inputs,
            "nodes": [
                {
                    "item": node.item,
                    "recipe_id": node.recipe.recipe_id,
                    "batches": node.batches,
                    "depth": node.depth,
                }
                for node in self.nodes
            ],
        }


async def build_craft_plan(
    target: str,
    *,
    requested: int,
    inventory: Mapping[str, int],
    lookup: Callable[[str], Awaitable[RecipeSpec | None]],
    budget: CraftBudget,
    allow_table: bool = True,
    canonicalize: Callable[[str], str] | None = None,
) -> CraftPlan:
    """把"做 N 个 target"解成一个**依赖优先的拓扑序列**（带真实库存模拟）。

    抛 :class:`CraftPlanError`（稳定原因码）表示**不能做** —— 绝不返回一个"大概能做"的计划。
    """
    normalize = canonicalize or (lambda name: str(name or "").strip().lower())
    root = normalize(target)
    if not root:
        raise CraftPlanError(CRAFT_PLAN_EMPTY)
    requested_amount = max(0, int(requested or 0))
    have: dict[str, int] = {}
    for name, count in dict(inventory).items():
        key = normalize(name)
        if not key:
            continue
        try:
            have[key] = have.get(key, 0) + max(0, int(count))
        except (TypeError, ValueError):
            continue
    inventory_before = dict(have)
    nodes: list[CraftNode] = []
    visiting: set[str] = set()
    state = {"requires_table": False, "total_inputs": 0}
    cache: dict[str, RecipeSpec | None] = {}

    async def recipe_for(item: str) -> RecipeSpec | None:
        if item not in cache:
            cache[item] = await lookup(item)
        return cache[item]

    async def need(
        item: str, amount: int, depth: int, *, use_stock: bool = True, consume: bool = True
    ) -> None:
        if amount <= 0:
            return
        # 1) 先用**当前库存**抵扣（合并同名需求的关键：后来者能吃到前面剩下的）。
        #    目标物品本身不用库存抵扣：用户说"做 N 个"就是要**再做 N 个**，
        #    否则"我已经有 4 根木棍"会让计划变成 0 步（那是拒绝，不是回答）。
        if use_stock:
            taken = min(amount, max(0, have.get(item, 0)))
            if taken:
                have[item] = have.get(item, 0) - taken
                amount -= taken
            if amount <= 0:
                return
        if depth > budget.max_depth:
            raise CraftPlanError(CRAFT_PLAN_DEPTH, f"{item}@{depth}>{budget.max_depth}")
        if item in visiting:
            raise CraftPlanError(CRAFT_PLAN_CYCLE, item)
        recipe = await recipe_for(item)
        if recipe is None or not recipe.ingredients:
            raise CraftPlanError(CRAFT_PLAN_MATERIAL, f"{item}(缺 {amount})")
        if recipe.requires_table:
            if not allow_table:
                raise CraftPlanError(CRAFT_PLAN_TABLE, item)
            state["requires_table"] = True
        per_craft = max(1, int(recipe.per_craft))
        batches = -(-amount // per_craft)
        visiting.add(item)
        try:
            for name, qty in recipe.ingredients:
                await need(normalize(name), int(qty) * batches, depth + 1)
        finally:
            visiting.discard(item)
        # 由本计划造出来的东西只有**剩余部分**留在库存里：父节点要用的那份已经被消耗掉了
        # （少扣这一笔会把「同一物品既是材料又是产物」的净变化算错 —— 真实缺陷）。
        leftover = per_craft * batches - (amount if consume else 0)
        have[item] = have.get(item, 0) + max(0, leftover)
        nodes.append(CraftNode(item=item, recipe=recipe, batches=batches, depth=depth))

    root_recipe = await recipe_for(root)
    if root_recipe is None:
        raise CraftPlanError(CRAFT_PLAN_NO_RECIPE, root)
    # 数量预算在这里**夹取**（不是拒绝）：夹取必须由回复层明确披露（``capped``），
    # 绝不静默少做。上限 = min(用户要的, 单目标上限, 每刀产出 × 目标刀数上限)。
    per_craft_root = max(1, int(root_recipe.per_craft))
    amount_limit = min(budget.max_per_item, per_craft_root * budget.max_target_batches)
    asked = requested_amount if requested_amount > 0 else 1
    planned_amount = min(asked, amount_limit)
    capped = planned_amount < asked
    # 没给数量 → 采用**已定义的默认值**：一次真实合成（产出量来自真实配方）
    await need(root, planned_amount, 1, use_stock=False, consume=False)

    craft_steps = sum(node.batches for node in nodes)
    if craft_steps <= 0:
        raise CraftPlanError(CRAFT_PLAN_NO_RECIPE, root)
    if craft_steps > budget.effective_max_craft_steps:
        raise CraftPlanError(CRAFT_PLAN_STEPS, f"{craft_steps}>{budget.effective_max_craft_steps}")
    depth_reached = max((node.depth for node in nodes), default=0)
    total_inputs = sum(
        sum(qty for _name, qty in node.recipe.ingredients) * node.batches for node in nodes
    )
    if total_inputs > budget.max_total_inputs:
        raise CraftPlanError(CRAFT_PLAN_INPUTS, f"{total_inputs}>{budget.max_total_inputs}")
    net = {
        name: have.get(name, 0) - inventory_before.get(name, 0)
        for name in set(have) | set(inventory_before)
    }
    delta = {name: value for name, value in sorted(net.items()) if value > 0}
    consumed = {name: -value for name, value in sorted(net.items()) if value < 0}
    target_produced = sum(node.produced for node in nodes if node.item == root)
    if target_produced < planned_amount:
        raise CraftPlanError(CRAFT_PLAN_MATERIAL, f"{root}(只做出 {target_produced})")
    return CraftPlan(
        target=root,
        requested=requested_amount,
        nodes=tuple(nodes),
        inventory_before=inventory_before,
        projected=dict(have),
        inventory_delta=delta,
        inventory_consumed=consumed,
        requires_table=bool(state["requires_table"]),
        capped=capped,
        planned_amount=planned_amount,
        depth=depth_reached,
        craft_steps=craft_steps,
        total_inputs=total_inputs,
        notes=tuple(_notes_of(nodes)),
    )


def _notes_of(nodes: Sequence[CraftNode]) -> list[str]:
    """审计备注：多级 / 多配方 / 需要工作台时各记一条（便于事后核对）。"""
    notes: list[str] = []
    if len({node.item for node in nodes}) > 1:
        notes.append("multi_step")
    if any(node.recipe.requires_table for node in nodes):
        notes.append("requires_workbench")
    if any(node.batches > 1 for node in nodes):
        notes.append("multi_batch")
    return notes
