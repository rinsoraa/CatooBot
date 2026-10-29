"""Phase 8G：合成预算的**唯一**来源（集中配置，避免散落的硬编码上限互相冲突）。

任务书 §5.3 给出保守初始上限；这些数值还要受**既有执行器**的硬上限约束
（``validation.MAX_STEPS`` / ``validation.MAX_ACTION_STEPS``）—— 若底层更严格，
**以更严格者为准**，本模块把这条关系写进代码而不是写进注释。
"""

from __future__ import annotations

from dataclasses import dataclass

from app.tasks.validation import MAX_ACTION_STEPS, MAX_STEPS


@dataclass(frozen=True)
class CraftBudget:
    """一个合成计划的全部上限（不可变；测试可以构造更小的预算）。"""

    #: 合成依赖深度（层级，不是步骤数）：最多 4 级。
    max_depth: int = 4
    #: 合成步骤数上限（还会被运行时动作步上限进一步收紧）。
    max_craft_steps: int = 12
    #: 单个目标物品的最大请求数量。
    max_per_item: int = 16
    #: 单个目标物品最多几刀（保留 8F 的 ``CRAFT_MAX_BATCHES`` 语义作为默认值）。
    max_target_batches: int = 4
    #: 整个计划的总输入材料数量（所有步骤实际消耗的材料总量）。
    max_total_inputs: int = 256
    #: Phase 8H H1：候选搜索的**集中**状态上限。一个搜索状态 =
    #: 对某个物品的某一刀配方的一次候选尝试；
    #: 超限一律明确拒绝（search_budget_exceeded），绝不静默退化成按 recipe_id 字母序选择后继续制作。
    max_recipe_search_states: int = 256
    #: 工作台搜索半径（方块，真实世界查询用）。
    workbench_search_radius: int = 16
    #: 工作台观测的最大有效期（秒）：超过 → ``STALE``，绝不当成当前状态。
    workbench_ttl_seconds: float = 30.0
    #: 允许在计划里放一个**显式**的接近步骤（``minecraft_move_to``，LOW 风险）。
    #: 执行器**绝不**自己走过去；要靠近就必须在冻结计划里写明这一步。
    allow_approach: bool = True

    @property
    def effective_max_craft_steps(self) -> int:
        """真正可用的合成步骤数：任务书预算与既有动作步上限取更严格者。

        ``allow_approach`` 打开时**预留一格**给工作台的显式接近步（它是 LOW 动作步，
        同样占用 ``MAX_ACTION_STEPS``）—— 宁可少做一刀，也绝不让"到位 + 合成"超出执行器上限。
        """
        reserve = 1 if self.allow_approach else 0
        return max(1, min(self.max_craft_steps, MAX_ACTION_STEPS - reserve))

    @property
    def effective_max_steps(self) -> int:
        """真正的计划步数上限：``MAX_STEPS`` 里留一步给末尾的真实背包复核。"""
        return max(1, MAX_STEPS - 1)

    def describe(self) -> dict[str, int]:
        """审计用的预算快照（写进计划 checks，便于事后核对）。"""
        return {
            "max_depth": self.max_depth,
            "max_craft_steps": self.effective_max_craft_steps,
            "max_per_item": self.max_per_item,
            "max_target_batches": self.max_target_batches,
            "max_total_inputs": self.max_total_inputs,
            "max_steps": self.effective_max_steps,
            "workbench_search_radius": self.workbench_search_radius,
        }


#: 本阶段唯一的默认预算实例。
DEFAULT_CRAFT_BUDGET = CraftBudget()
