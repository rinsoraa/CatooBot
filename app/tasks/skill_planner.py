"""Phase 7E §7.5：把"适用技能"接进既有规划流程的**最小包装**（不建第二套规划器）。

它做两件事，别的什么都不做：

1. 只在**资源类**目标上问一次技能服务（``detect_shape(objective) == "resource"``，
   与基规划器同一个判定函数）；
2. 技能给出计划候选 → 包成与基规划器**同形**的 ``PlanningResult``
   （``outcome=READY_FOR_APPROVAL``，带一条 ``skill_reuse`` 审计 check）后返回。

技能不给候选、不适用、检索/物化异常 —— 一律**原样回退**基规划器，行为逐字不变。
这一层没有执行入口：它返回的计划候选仍然要走既有
``TaskProposal → AgentPlan →（批准/确认）→ TaskRuntime → validate_plan → Policy`` 链。
"""

from __future__ import annotations

from typing import Any

from app.tasks.agent_planner import (
    BoundedAgentPlanner,
    PlanningOutcome,
    PlanningResult,
    PlanTarget,
    detect_shape,
)

#: 审计里标记"这一份计划来自技能复用"
SKILL_REUSE_REASON = "skill_plan_ready"


class SkillAwarePlanner:
    """鸭子类型的规划器：``AgentPlanService(planner=SkillAwarePlanner(...))`` 即可接入。"""

    def __init__(self, base: BoundedAgentPlanner | None = None, *, skills: Any = None) -> None:
        self.base = base or BoundedAgentPlanner()
        self.skills = skills

    #: 基规划器的旋钮对既有调用方保持可见（审计/装配读它们）
    @property
    def risk_table(self) -> Any:
        return self.base.risk_table

    @property
    def allow_medium(self) -> bool:
        return self.base.allow_medium

    async def plan(
        self,
        objective: str,
        *,
        target: PlanTarget | None = None,
        observe: Any = None,
    ) -> PlanningResult:
        suggestion = await self._suggest(objective)
        if suggestion is not None:
            return PlanningResult(
                outcome=PlanningOutcome.READY_FOR_APPROVAL,
                reason=SKILL_REUSE_REASON,
                plan=suggestion,
                target=target or PlanTarget(),
                checks=(
                    {
                        "check": "skill_reuse",
                        "ok": True,
                        "detail": {
                            "objective": " ".join(str(objective or "").split())[:120],
                            "steps": len(suggestion.plan.steps),
                        },
                    },
                ),
                risk_summary={
                    "classes": sorted({step.risk for step in suggestion.plan.steps}),
                    "max_risk": max((step.risk for step in suggestion.plan.steps), default="SAFE"),
                    "would_require_plan_confirmation": True,
                    "source": "procedural_skill",
                },
            )
        return await self.base.plan(objective, target=target, observe=observe)

    async def _suggest(self, objective: str) -> Any:
        if self.skills is None:
            return None
        if detect_shape(objective) != "resource":
            # 跟随 / 未知目标不套用技能（技能是**资源类方法**；未知目标仍走能力目录判定）
            return None
        try:
            suggestion = await self.skills.suggest(objective)
        except Exception:  # noqa: BLE001 - 技能异常 = 回退既有规划器
            return None
        if suggestion is None:
            return None
        # 基规划器的风险闸门对技能计划**同样生效**（allow_medium=false 时 MEDIUM 技能不许
        # 被当成"可执行候选"塞进来）——不通过就交给基规划器给出它自己的 BLOCKED_BY_POLICY。
        blocked = self.base._policy_blocked(suggestion)  # noqa: SLF001 - 只读预检，复用同一闸门
        if blocked:
            return None
        return suggestion


__all__ = ["SKILL_REUSE_REASON", "SkillAwarePlanner"]
