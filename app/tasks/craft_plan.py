"""Phase 8G G3 + Phase 8H H1：受限多步合成 DAG 的**构建、候选搜索与库存模拟**
（纯逻辑，无 I/O）。

为什么不能"递归展开配方"就算完（8G 任务书 §5.1）：配方展开会踩三类真实的坑 ——
多个分支重复消费同一份库存、
中间产物既被消耗又被产出、以及无界展开。所以这里做的是**带库存模拟的需求求解**：

1. 深度优先地满足需求：先用**当前库存**抵扣，再决定要合成多少（batches = ceil(缺口/q)）；
2. 同名需求天然合并 —— 后来者先吃前面剩下的库存，绝不重复承诺同一份材料；
3. 每个节点登记**真实配方与生产/消耗**，节点顺序就是依赖优先的拓扑序；
4. 循环配方用"正在展开栈"检测，深度/步骤数/单目标数量/总输入量四道预算全部硬检查；
5. 结束时把模拟后的库存与初始库存相减，得到**净增**与**净耗**（同一物品两边都有时只算净值）。

Phase 8H H1 增量：**同一个物品可以有多份真实候选配方**（例如木镐有 11 个树种/竹子变体）。
当所有候选都 available=false 时，旧实现按 recipe_id 字母序锁定第一个（会选到金合欢/竹子），
而"当前不可立即制作"**不等于**"在受限 DAG 内不可实现" —— 橡木木板可以由橡木原木做出来。
所以这里改成**有界、确定性的候选搜索**：

* 候选集合**只能**来自调用方传入的真实查询（lookup），本模块不猜配方、不拼 recipe_id；
* 候选按固定顺序尝试，配合库存/节点快照与回滚，找到**完整可行**的路径才返回；
* 搜索状态（一次候选尝试）有**集中预算**上限；超限一律明确拒绝（稳定原因码），
  绝不静默退化成字母序选择后继续制作；
* 循环、重复子问题（无配方/成环/超深这类**与库存无关**的失败）会记忆化剪枝；
* 根目标的多份候选各自完整求值后，按任务书 §2.5 的确定性优先级选一份，
  并把它的每一步真实 recipe_id 冻结进计划。

配方只能来自调用方传入的真实查询，本模块既不猜配方也不改世界。
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
#: Phase 8H H1：候选搜索预算超限（集中上限，绝不静默退化）。
CRAFT_PLAN_SEARCH = "search_budget_exceeded"


class CraftPlanError(Exception):
    """构建失败：带**稳定原因码**（reason），调用方据此如实回复用户。"""

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
    """DAG 里的一个合成操作（一个配方 × batches 刀）。"""

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
    #: 实际打算做的目标数量（夹取之后；requested 仍是用户原话里的数量）。
    planned_amount: int = 0

    def produced_of(self, item: str) -> int:
        return sum(node.produced for node in self.nodes if node.item == item)

    def consumed_of(self, item: str) -> int:
        return sum(node.consumed().get(item, 0) for node in self.nodes)

    @property
    def recipe_ids(self) -> tuple[str, ...]:
        """规范化的完整配方 ID 序列（稳定决胜规则用；节点已是拓扑序）。"""
        return tuple(node.recipe.recipe_id for node in self.nodes)

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


def _as_candidates(payload: object) -> tuple[RecipeSpec, ...]:
    """把 lookup 的返回值规范成**去重、稳定顺序**的候选元组。"""
    if payload is None:
        return ()
    if isinstance(payload, RecipeSpec):
        return (payload,)
    if isinstance(payload, (str, bytes)) or not isinstance(payload, Sequence):
        return ()
    seen: set[str] = set()
    out: list[RecipeSpec] = []
    for item in payload:
        if not isinstance(item, RecipeSpec):
            continue
        if not item.recipe_id or not item.ingredients or item.recipe_id in seen:
            continue
        seen.add(item.recipe_id)
        out.append(item)
    return tuple(out)


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


def _plan_key(plan: CraftPlan) -> tuple:
    """任务书 §2.5 的**确定性**候选优先级（越小越优先）。

    只对**完整可行**的计划排序：完整可行性已在搜索阶段保证（不可行的候选根本不会产出计划）。

    1. 未被夹取（能完成全部目标）优先；
    2. 合成刀数更少（少生成不必要的依赖链）；
    3. 总输入量更少（少消耗、少浪费）；
    4. 前置条件更少（同等条件优先不需要工作台的 2×2）；
    5. 规范化完整 recipe_id 序列（稳定决胜）。
    """
    return (
        bool(plan.capped),
        plan.craft_steps,
        plan.total_inputs,
        bool(plan.requires_table),
        plan.recipe_ids,
    )


#: 全部候选都不可行时，用于挑「最有诊断价值」的失败原因（确定性）。
_ERROR_PRIORITY = (
    CRAFT_PLAN_MATERIAL,
    CRAFT_PLAN_TABLE,
    CRAFT_PLAN_SEARCH,
    CRAFT_PLAN_NO_RECIPE,
    CRAFT_PLAN_DEPTH,
    CRAFT_PLAN_STEPS,
    CRAFT_PLAN_INPUTS,
    CRAFT_PLAN_CYCLE,
    CRAFT_PLAN_COUNT,
)


def _worst_to_report(errors: Sequence[CraftPlanError]) -> CraftPlanError:
    """挑一个**真实**的失败原因上报：优先材料不足（带缺什么缺多少），再按固定优先级。"""
    ranked = sorted(
        errors,
        key=lambda exc: (
            _ERROR_PRIORITY.index(exc.reason)
            if exc.reason in _ERROR_PRIORITY
            else len(_ERROR_PRIORITY),
            exc.detail,
        ),
    )
    return ranked[0] if ranked else CraftPlanError(CRAFT_PLAN_NO_RECIPE)


async def build_craft_plan(
    target: str,
    *,
    requested: int,
    inventory: Mapping[str, int],
    lookup: Callable[[str], Awaitable[RecipeSpec | Sequence[RecipeSpec] | None]],
    budget: CraftBudget,
    allow_table: bool = True,
    canonicalize: Callable[[str], str] | None = None,
) -> CraftPlan:
    """把"做 N 个 target"解成一个**依赖优先的拓扑序列**（带真实库存模拟与有界候选搜索）。

    lookup 可以返回**多份**真实候选（同一物品的多个变体）；返回单份或 None 时行为与 8G 一致。
    抛 CraftPlanError（稳定原因码）表示**不能做** —— 绝不返回一个"大概能做"的计划。
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

    # ---- 集中式搜索预算（§2.4）：一个"搜索状态" = 对某个物品的某一刀配方的一次候选尝试。
    search_states = 0
    candidate_cache: dict[str, tuple[RecipeSpec, ...]] = {}
    #: 与库存无关的失败（没有候选 / 成环 / 超深）可以安全记忆化剪枝。
    impossible: dict[tuple[str, int], tuple[str, str]] = {}

    async def candidates_for(item: str) -> tuple[RecipeSpec, ...]:
        if item not in candidate_cache:
            candidate_cache[item] = _as_candidates(await lookup(item))
        return candidate_cache[item]

    class _Attempt:
        """一次候选尝试的上下文（携带快照，失败即回滚）。"""

        def __init__(self) -> None:
            self.stock = dict(have)
            self.requires_table = False
            self.total_inputs = 0
            self.nodes: list[CraftNode] = []
            self.visiting: set[str] = set()

    async def fill(
        attempt: _Attempt,
        item: str,
        amount: int,
        depth: int,
        *,
        use_stock: bool,
        consume: bool,
    ) -> None:
        """用**某一个**候选把 amount 个 item 的需求满足掉；失败抛 CraftPlanError。"""
        nonlocal search_states
        if amount <= 0:
            return
        if use_stock:
            taken = min(amount, max(0, attempt.stock.get(item, 0)))
            if taken:
                attempt.stock[item] = attempt.stock.get(item, 0) - taken
                amount -= taken
            if amount <= 0:
                return
        if depth > budget.max_depth:
            raise CraftPlanError(CRAFT_PLAN_DEPTH, f"{item}@{depth}>{budget.max_depth}")
        if item in attempt.visiting:
            raise CraftPlanError(CRAFT_PLAN_CYCLE, item)
        memo = impossible.get((item, amount))
        if memo is not None:
            # Phase 8I：缓存里必须连**真实缺失数量**一起复述 —— 只缓存原因会让
            # 复述路径丢掉 (缺 N)，而缺料诊断与资源交接提案正是靠它工作的。
            memo_reason, memo_detail = memo
            raise CraftPlanError(memo_reason, memo_detail)
        candidates = await candidates_for(item)
        if not candidates:
            # 基础材料（没有配方）在**需求求解**语境里就是「缺这个」—— 旧语义如此，
            # 用户要看到的是缺什么、缺多少（no_recipe 只用于**目标本身**没有配方）。
            detail = f"{item}(缺 {amount})"
            impossible[(item, amount)] = (CRAFT_PLAN_MATERIAL, detail)
            raise CraftPlanError(CRAFT_PLAN_MATERIAL, detail)
        errors: list[CraftPlanError] = []
        for recipe in candidates:
            if recipe.requires_table and not allow_table:
                errors.append(CraftPlanError(CRAFT_PLAN_TABLE, item))
                continue
            search_states += 1
            if search_states > budget.max_recipe_search_states:
                raise CraftPlanError(
                    CRAFT_PLAN_SEARCH,
                    f"{search_states}>{budget.max_recipe_search_states}",
                )
            snapshot_stock = dict(attempt.stock)
            snapshot_len = len(attempt.nodes)
            snapshot_table = attempt.requires_table
            snapshot_inputs = attempt.total_inputs
            attempt.visiting.add(item)
            try:
                per_craft = max(1, int(recipe.per_craft))
                batches = -(-amount // per_craft)
                if recipe.requires_table:
                    attempt.requires_table = True
                for name, qty in recipe.ingredients:
                    await fill(
                        attempt,
                        normalize(name),
                        int(qty) * batches,
                        depth + 1,
                        use_stock=True,
                        consume=True,
                    )
            except CraftPlanError as exc:
                attempt.stock = snapshot_stock
                del attempt.nodes[snapshot_len:]
                attempt.requires_table = snapshot_table
                attempt.total_inputs = snapshot_inputs
                attempt.visiting.discard(item)
                errors.append(exc)
                if exc.reason == CRAFT_PLAN_SEARCH:
                    raise
                continue
            attempt.visiting.discard(item)
            # 由本计划造出来的东西只有**剩余部分**留在库存里：父节点要用的那份已经被消耗掉了
            # （少扣这一笔会把「同一物品既是材料又是产物」的净变化算错 —— 真实缺陷）。
            leftover = per_craft * batches - (amount if consume else 0)
            attempt.stock[item] = attempt.stock.get(item, 0) + max(0, leftover)
            attempt.total_inputs += sum(qty for _n, qty in recipe.ingredients) * batches
            attempt.nodes.append(CraftNode(item=item, recipe=recipe, batches=batches, depth=depth))
            return
        raise _worst_to_report(errors)

    async def build_with(root_recipe: RecipeSpec) -> CraftPlan:
        """用**指定的**根候选构建整条计划（子物品仍做候选搜索）。"""
        attempt = _Attempt()
        attempt.stock = dict(inventory_before)
        per_craft_root = max(1, int(root_recipe.per_craft))
        amount_limit = min(budget.max_per_item, per_craft_root * budget.max_target_batches)
        asked = requested_amount if requested_amount > 0 else 1
        planned_amount = min(asked, amount_limit)
        capped = planned_amount < asked
        # 根候选是"选定"的，所以这里只有一个候选参与搜索。
        await _fill_with(attempt, root, root_recipe, planned_amount)
        nodes = tuple(attempt.nodes)
        craft_steps = sum(node.batches for node in nodes)
        if craft_steps <= 0:
            raise CraftPlanError(CRAFT_PLAN_NO_RECIPE, root)
        if craft_steps > budget.effective_max_craft_steps:
            raise CraftPlanError(
                CRAFT_PLAN_STEPS, f"{craft_steps}>{budget.effective_max_craft_steps}"
            )
        total_inputs = sum(
            sum(qty for _name, qty in node.recipe.ingredients) * node.batches for node in nodes
        )
        if total_inputs > budget.max_total_inputs:
            raise CraftPlanError(CRAFT_PLAN_INPUTS, f"{total_inputs}>{budget.max_total_inputs}")
        net = {
            name: attempt.stock.get(name, 0) - inventory_before.get(name, 0)
            for name in set(attempt.stock) | set(inventory_before)
        }
        delta = {name: value for name, value in sorted(net.items()) if value > 0}
        consumed = {name: -value for name, value in sorted(net.items()) if value < 0}
        return CraftPlan(
            target=root,
            requested=requested_amount,
            nodes=nodes,
            inventory_before=dict(inventory_before),
            projected=dict(sorted(attempt.stock.items())),
            inventory_delta=delta,
            inventory_consumed=consumed,
            requires_table=attempt.requires_table,
            depth=max((node.depth for node in nodes), default=0),
            craft_steps=craft_steps,
            total_inputs=total_inputs,
            notes=tuple(_notes_of(nodes)),
            capped=capped,
            planned_amount=planned_amount,
        )

    async def _fill_with(attempt: _Attempt, item: str, recipe: RecipeSpec, amount: int) -> None:
        """根候选固定时的入口（不参与候选循环，直接展开它的材料）。"""
        nonlocal search_states
        per_craft = max(1, int(recipe.per_craft))
        batches = -(-amount // per_craft)
        if recipe.requires_table:
            if not allow_table:
                raise CraftPlanError(CRAFT_PLAN_TABLE, item)
            attempt.requires_table = True
        attempt.visiting.add(item)
        try:
            for name, qty in recipe.ingredients:
                await fill(
                    attempt,
                    normalize(name),
                    int(qty) * batches,
                    2,
                    use_stock=True,
                    consume=True,
                )
        finally:
            attempt.visiting.discard(item)
        # 根目标是用户**要的东西**（不是被谁消耗掉的中间产物）：产出的每一份都留在库存里，
        # 所以这里不扣 amount（与 8G 的 need(root, ..., consume=False) 语义一致）。
        leftover = per_craft * batches
        attempt.stock[item] = attempt.stock.get(item, 0) + max(0, leftover)
        attempt.total_inputs += sum(qty for _n, qty in recipe.ingredients) * batches
        attempt.nodes.append(CraftNode(item=item, recipe=recipe, batches=batches, depth=1))

    root_candidates = await candidates_for(root)
    if not root_candidates:
        raise CraftPlanError(CRAFT_PLAN_NO_RECIPE, root)
    plans: list[CraftPlan] = []
    errors: list[CraftPlanError] = []
    for candidate in root_candidates:
        search_states += 1
        if search_states > budget.max_recipe_search_states:
            raise CraftPlanError(
                CRAFT_PLAN_SEARCH,
                f"{search_states}>{budget.max_recipe_search_states}",
            )
        if candidate.requires_table and not allow_table:
            # allow_table=False（8F 语义）时工作台配方不可用：如实记下原因，
            # 不能因为它被跳过就把整个目标报成 no_recipe。
            errors.append(CraftPlanError(CRAFT_PLAN_TABLE, root))
            continue
        try:
            plans.append(await build_with(candidate))
        except CraftPlanError as exc:
            if exc.reason == CRAFT_PLAN_SEARCH:
                raise
            errors.append(exc)
    if not plans:
        raise _worst_to_report(errors)
    plans.sort(key=_plan_key)
    return plans[0]
