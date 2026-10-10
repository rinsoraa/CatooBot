"""Phase 7D §三：受限规划器 —— 把目标变成**有限、可检查**的步骤序列。

只能从**当前已注册、实际支持**的能力中选操作（任务书 §三）：

* 虚构工具/参数 → 校验器直接拒绝（复用 5A 的 `validate_plan`）；
* UNKNOWN/UNSUPPORTED 绝不升级（复用 7C 的能力目录与缺口四值）；
* 目标做不了 → 给**可审计**的 `PlanningOutcome`（六值，绝不硬编一个"看起来能跑"的计划）；
* 目标实体只来自可信身份桥（修订 2），坐标只来自 SAFE 观察；
* 这里**绝不执行**任何世界动作 —— SAFE 观察是唯一的例外（§五 的"真实状态"输入）。

导入方向：本模块 → turn → planner（turn 不 import 本模块；服务经鸭子类型注入）。
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.integrations.minecraft.agent import ACTION_RISK
from app.tasks.agent_plan import PlanTarget
from app.tasks.planner import (
    ObservationFailed,
    PlannedTask,
    plan_exploration_task,
    plan_resource_task,
)
from app.tasks.turn import DROP_OVERRIDES, block_for

#: §五：SAFE 观察的签名（与 TaskTurnHandler 用的同一条既有工具通道）
ObserveFn = Callable[[str, Mapping[str, Any]], Awaitable[Any]]


class PlanningOutcome(str, Enum):  # noqa: UP042
    """规划结论（任务书 §三 的六值；**可审计**，绝不"看起来能跑"）。"""

    #: 计划已冻结，等用户批准（USER：与待确认任务同建；LIFE：只挂起）
    READY_FOR_APPROVAL = "READY_FOR_APPROVAL"
    NEEDS_MORE_INFORMATION = "NEEDS_MORE_INFORMATION"
    UNSUPPORTED = "UNSUPPORTED"
    BLOCKED_BY_POLICY = "BLOCKED_BY_POLICY"
    BLOCKED_BY_PRECONDITION = "BLOCKED_BY_PRECONDITION"
    #: 世界/授权变了，旧计划作废（由执行链触发重规划时用；规划器本身不返回它）
    REPLAN_REQUIRED = "REPLAN_REQUIRED"


@dataclass
class PlanningResult:
    """一次受限规划的全部可审计输出（§九：输入、结构化决策、检查结果）。"""

    outcome: PlanningOutcome
    reason: str = ""
    plan: PlannedTask | None = None
    target: PlanTarget = field(default_factory=PlanTarget)
    checks: tuple[dict[str, Any], ...] = ()
    risk_summary: dict[str, Any] = field(default_factory=dict)

    @property
    def ready(self) -> bool:
        return self.outcome is PlanningOutcome.READY_FOR_APPROVAL


#: 跟随意图（§八场景 B；与 7C 的 PLAYER_TARGET_KEYWORDS 同词表 —— 复用，不另立）
FOLLOW_KEYWORDS: tuple[str, ...] = (
    "跟着",
    "跟住",
    "跟随",
    "跟我",
    "跟过来",
    "来我身边",
    "带我去",
    "带过来",
)

#: 跟随是**持续型**动作：它的期限来自 minecraft.action.follow_player.timeout（默认 120s），
#: 与授权 TTL（60s）/ 任务 TTL（600s）是**三层不同的期限** —— 计划里如实展示这一层。
FOLLOW_TIMEOUT_FALLBACK_SECONDS = 120.0

#: Phase 7F.1：有界探索意图的确定性关键词（与 7A 的 EXPLORE_KEYWORDS 同词表 —— 复用，不另立）。
#: 7A 的 EXPLORATION 意图由这些词从记忆里生成，所以同一份词表能把它的目标文本路由到探索模板。
EXPLORE_KEYWORDS: tuple[str, ...] = (
    "探索",
    "去看看",
    "远行",
    "explore",
    "探险",
    "出去转转",
    "转转",
)

#: 有界探索的默认上限（与 planner.EXPLORE_DEFAULT_DISTANCE 保持一致；这里只做展示）
EXPLORE_DEFAULT_DISTANCE = 24.0


def detect_shape(objective: str) -> str:
    """确定性形状判定：follow / resource / explore / unknown（绝不靠模型猜）。"""
    text = str(objective or "").strip()
    if not text:
        return "unknown"
    if any(keyword in text for keyword in FOLLOW_KEYWORDS):
        return "follow"
    if block_for(text):
        return "resource"
    if any(keyword.lower() in text.lower() for keyword in EXPLORE_KEYWORDS):
        return "explore"
    return "unknown"


def _risk_summary_of(plan: PlannedTask, risk_table: Mapping[str, str]) -> dict[str, Any]:
    classes = sorted({str(risk_table.get(step.tool, "SAFE")) for step in plan.plan.steps})
    return {
        "classes": classes,
        "max_risk": next(
            (c for c in ("DESTRUCTIVE", "HIGH", "MEDIUM", "LOW", "SAFE") if c in classes), "SAFE"
        ),
        # 5A 的规矩：**整份冻结计划**都要用户确认一次（与风险等级无关）——这里如实写 True
        "would_require_plan_confirmation": True,
    }


class BoundedAgentPlanner:
    """确定性 + 模板化的规划器（不做自由生成；模型路径留给既有 ModelTaskPlanner）。"""

    def __init__(
        self,
        *,
        risk_table: Mapping[str, str] | None = None,
        allow_safe: bool = True,
        allow_low: bool = True,
        allow_medium: bool = False,
        follow_timeout_seconds: float = FOLLOW_TIMEOUT_FALLBACK_SECONDS,
    ) -> None:
        self.risk_table = dict(risk_table or ACTION_RISK)
        self.allow_safe = bool(allow_safe)
        self.allow_low = bool(allow_low)
        self.allow_medium = bool(allow_medium)
        self.follow_timeout_seconds = float(follow_timeout_seconds)

    # ------------------------------------------------------------ 入口

    async def plan(
        self,
        objective: str,
        *,
        target: PlanTarget | None = None,
        observe: ObserveFn | None = None,
    ) -> PlanningResult:
        """按形状路由（follow / resource / explore / unknown），产出六值结论之一。"""
        shape = detect_shape(objective)
        if shape == "follow":
            return self._plan_follow(objective, target or PlanTarget())
        if shape == "resource":
            return await self._plan_resource(objective, observe)
        if shape == "explore":
            return await self._plan_explore(objective, observe)
        return self._plan_unknown(objective)

    # ------------------------------------------------------------ follow（修订 2）

    def _plan_follow(self, objective: str, target: PlanTarget) -> PlanningResult:
        from app.tasks.models import TaskPlan, TaskStep

        checks: list[dict[str, Any]] = []
        # 1) 目标必须来自可信身份桥（§八/修订 2）——昵称猜测在这里就死掉
        if not target.verified or not target.player_name:
            checks.append({"check": "identity", "ok": False, "detail": target.to_payload()})
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_PRECONDITION
                if target.status in {"MISSING", "REVOKED", "CONFLICT"}
                else PlanningOutcome.NEEDS_MORE_INFORMATION,
                reason=f"target_{str(target.status).lower()}",
                target=target,
                checks=tuple(checks),
            )
        checks.append(
            {
                "check": "identity",
                "ok": True,
                "detail": {
                    "source": "verified_identity_link",
                    "player_name": target.player_name,
                    "server_id": target.server_id,
                    "uuid_suffix": target.player_uuid[-4:],
                },
            }
        )
        # 2) Policy 开关预检（只是**提前**如实说；真正的放行仍在执行链）
        if not self.allow_low:
            checks.append({"check": "risk_flag", "ok": False, "detail": {"allow_low": False}})
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_POLICY,
                reason="allow_low_disabled",
                target=target,
                checks=tuple(checks),
            )
        checks.append({"check": "risk_flag", "ok": True, "detail": {"allow_low": True}})
        # 3) 单步冻结计划；期限三层如实写进计划（执行瞬间 Policy 还会查 nearby_players）
        step = TaskStep(
            step_id="step_1",
            tool="minecraft_follow_player",
            arguments={"username": target.player_name},
            risk="LOW",
        )
        plan = TaskPlan(
            objective=" ".join(objective.split())[:120],
            steps=[step],
            observations=[
                {
                    "tool": "identity_link",
                    "arguments": {},
                    "result": target.to_payload(),
                    "summary": f"目标 {target.player_name} 来自 VERIFIED 身份绑定",
                }
            ],
        )
        planned = PlannedTask(objective=plan.objective, plan=plan)
        return PlanningResult(
            outcome=PlanningOutcome.READY_FOR_APPROVAL,
            reason="follow_plan_ready",
            plan=planned,
            target=target,
            checks=tuple(checks),
            risk_summary={
                "classes": ["LOW"],
                "max_risk": "LOW",
                "would_require_confirmation": True,
                "duration_limit_seconds": self.follow_timeout_seconds,
                "duration_note": (
                    f"跟随最长 {self.follow_timeout_seconds:.0f} 秒（动作超时）；"
                    "确认授权 60s 只管派发新步骤；任务总时限 600s"
                ),
            },
        )

    # ------------------------------------------------- explore（Phase 7F.1 有界自主探索）

    async def _plan_explore(self, objective: str, observe: ObserveFn | None) -> PlanningResult:
        """有界探索：只生成一个 SAFE 观察 + LOW 移动的**有限**计划，绝不在此执行世界动作。"""
        checks: list[dict[str, Any]] = [
            {"check": "shape", "ok": True, "detail": {"shape": "explore"}}
        ]
        if observe is None:
            checks.append({"check": "observe", "ok": False, "detail": {}})
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_PRECONDITION,
                reason="observe_unavailable",
                checks=tuple(checks),
            )
        # 探索只用 SAFE 读 + LOW 移动；任一被关掉就如实说做不了（绝不偷偷放宽）
        if not (self.allow_safe and self.allow_low):
            checks.append(
                {
                    "check": "risk_flag",
                    "ok": False,
                    "detail": {"allow_safe": self.allow_safe, "allow_low": self.allow_low},
                }
            )
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_POLICY,
                reason="explore_requires_safe_and_low",
                checks=tuple(checks),
            )
        try:
            planned = await plan_exploration_task(objective, observe=observe)
        except ObservationFailed as exc:
            checks.append({"check": "observe", "ok": False, "detail": {"error": str(exc)[:200]}})
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_PRECONDITION,
                reason=str(exc)[:200],
                checks=tuple(checks),
            )
        except Exception as exc:  # noqa: BLE001 - 规划失败绝不假装可执行
            checks.append(
                {"check": "plan", "ok": False, "detail": {"error": f"{type(exc).__name__}"}}
            )
            return PlanningResult(
                outcome=PlanningOutcome.NEEDS_MORE_INFORMATION,
                reason=f"planning_failed:{type(exc).__name__}",
                checks=tuple(checks),
            )
        blocked = self._policy_blocked(planned)
        if blocked is not None:
            checks.append({"check": "risk_flags", "ok": False, "detail": {"reason": blocked}})
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_POLICY,
                reason=blocked,
                plan=planned,
                risk_summary=_risk_summary_of(planned, self.risk_table),
                checks=tuple(checks),
            )
        checks.append({"check": "plan", "ok": True, "detail": {"steps": len(planned.plan.steps)}})
        return PlanningResult(
            outcome=PlanningOutcome.READY_FOR_APPROVAL,
            reason="explore_plan_ready",
            plan=planned,
            target=PlanTarget(status="VERIFIED", reason="no_player_target"),
            risk_summary=_risk_summary_of(planned, self.risk_table),
            checks=tuple(checks),
        )

    # ------------------------------------------------------------ resource（复用 5A 模板）

    async def _plan_resource(self, objective: str, observe: ObserveFn | None) -> PlanningResult:
        block = block_for(objective)
        if not block:  # detect_shape 已保证，这里只是护栏
            return self._plan_unknown(objective)
        if observe is None:
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_PRECONDITION,
                reason="observe_unavailable",
                checks=({"check": "observe", "ok": False, "detail": {}},),
            )
        try:
            planned = await plan_resource_task(
                objective,
                observe=observe,
                block_name=block,
                drop_item=DROP_OVERRIDES.get(block, block),
            )
        except ObservationFailed as exc:
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_PRECONDITION,
                reason=str(exc)[:200],
                checks=({"check": "observe", "ok": False, "detail": {"error": str(exc)[:200]}},),
            )
        except Exception as exc:  # noqa: BLE001 - 规划失败绝不假装可执行
            return PlanningResult(
                outcome=PlanningOutcome.NEEDS_MORE_INFORMATION,
                reason=f"planning_failed:{type(exc).__name__}",
                checks=({"check": "plan", "ok": False, "detail": {"error": str(exc)[:200]}},),
            )
        blocked = self._policy_blocked(planned)
        if blocked is not None:
            return PlanningResult(
                outcome=PlanningOutcome.BLOCKED_BY_POLICY,
                reason=blocked,
                plan=planned,
                risk_summary=_risk_summary_of(planned, self.risk_table),
                checks=({"check": "risk_flags", "ok": False, "detail": {"reason": blocked}},),
            )
        return PlanningResult(
            outcome=PlanningOutcome.READY_FOR_APPROVAL,
            reason="resource_plan_ready",
            plan=planned,
            risk_summary=_risk_summary_of(planned, self.risk_table),
            checks=({"check": "plan", "ok": True, "detail": {"steps": len(planned.plan.steps)}},),
        )

    def _policy_blocked(self, planned: PlannedTask) -> str | None:
        """计划里出现"当前配置不允许"的风险等级 → BLOCKED_BY_POLICY（提前如实说）。"""
        for step in planned.plan.steps:
            risk = str(self.risk_table.get(step.tool, "SAFE"))
            allowed = {
                "SAFE": self.allow_safe,
                "LOW": self.allow_low,
                "MEDIUM": self.allow_medium,
                "HIGH": False,
                "DESTRUCTIVE": False,
            }.get(risk, False)
            if not allowed:
                return f"{step.tool}:{risk}"
        return None

    # ------------------------------------------------------------ unknown（§三：可审计地做不了）

    def _plan_unknown(self, objective: str) -> PlanningResult:
        from app.tasks.capabilities import (
            capability_catalog,
            needs_design_information,
            required_capabilities,
        )

        wanted = list(required_capabilities(objective))
        checks: list[dict[str, Any]] = [
            {"check": "shape", "ok": False, "detail": {"shape": "unknown"}}
        ]
        if not wanted:
            # 目标太模糊 / 纯聊天 → 信息不足（绝不编一个计划出来）
            return PlanningResult(
                outcome=PlanningOutcome.NEEDS_MORE_INFORMATION,
                reason="objective_too_vague"
                if not needs_design_information(objective)
                else "needs_design",
                checks=tuple(checks),
            )
        catalog = capability_catalog()
        gaps = [
            {"capability_id": name, "gap": "SUPPORTED", "risk_class": catalog[name].risk_class}
            for name in wanted
            if name in catalog
        ]
        missing = [name for name in wanted if name not in catalog]
        if missing:
            checks.append({"check": "capabilities", "ok": False, "detail": {"missing": missing}})
            return PlanningResult(
                outcome=PlanningOutcome.UNSUPPORTED,
                reason="missing_capabilities",
                checks=tuple(checks),
                risk_summary={"gaps": gaps, "missing": missing},
            )
        # 能力都有、但本规划器没有对应模板 → 退回提案层（§六：能力不足不现场造工具）
        checks.append({"check": "template", "ok": False, "detail": {"capabilities": wanted}})
        return PlanningResult(
            outcome=PlanningOutcome.UNSUPPORTED,
            reason="no_plan_template",
            checks=tuple(checks),
            risk_summary={"gaps": gaps},
        )
