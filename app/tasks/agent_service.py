"""Phase 7D §四/§五/§六：AgentPlan 的编排层。

两条来源、两种确认次数（修订 1 —— **这条是 7D 的门面，不能含糊**）：

* **USER**：AgentPlan 与待确认任务**同时**建立；同一次「确认」同时代表冻结计划与其任务；
  执行授权沿用既有 ``confirm_and_start``，零改动。
* **LIFE**：只保存 AgentPlan（READY_FOR_APPROVAL），**不建 Task**；第一道门「批准」→
  才建待确认任务；第二道门「确认」→ 执行。两道门都走既有机制，绝不绕过。

副作用只有三种：写 ``task_agent_plans``/``behavior_events``；调 TaskRuntime 的
``create_task``（只在"用户批准之后"）；SAFE 观察（规划期）。**没有**任何直接执行入口 ——
步骤的实际执行永远经过 TaskRuntime → Policy → ActionRuntime。
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

from app.tasks.agent_plan import (
    DEFAULT_PLAN_TTL_SECONDS,
    DEFAULT_REPLAN_BUDGET,
    AgentPlan,
    PlanSource,
    PlanStatus,
    PlanTarget,
    plan_fingerprint,
    plan_time_bucket,
)
from app.tasks.agent_planner import (
    BoundedAgentPlanner,
    PlanningOutcome,
)
from app.tasks.proposal import ProposalStatus, TaskProposal
from app.tasks.proposal_service import (  # noqa: F401 - 语义复用
    NO_TARGET,
    TargetResolution,
    TaskProposalService,
)

#: 一次 pass 最多处理多少条 LIFE 提案（bounded）
MAX_LIFE_PLANS_PER_PASS = 3
#: WebUI / 上下文只读展示条数
VIEW_LIMIT = 10

#: LIFE 计划批准的关键词（修订 1：明确区分【批准计划】与【确认执行】）
APPROVE_PLAN_COMMANDS: tuple[str, ...] = ("批准", "批准计划", "同意这个计划")


def is_approve_plan_command(text: str) -> bool:
    message = str(text or "").strip()
    return any(
        message == keyword or message.startswith(keyword) for keyword in APPROVE_PLAN_COMMANDS
    )


class AgentPlanService:
    """计划编排器：USER/LIFE 路由 + LIFE 批准 + 预算 + 只读视图。"""

    def __init__(
        self,
        *,
        store: Any,
        planner: BoundedAgentPlanner | None = None,
        task_runtime: Any = None,
        proposal_service: TaskProposalService | None = None,
        observe: Any = None,
        character_id: str = "",
        plan_ttl_seconds: float = DEFAULT_PLAN_TTL_SECONDS,
        replan_budget: int = DEFAULT_REPLAN_BUDGET,
        view_limit: int = VIEW_LIMIT,
        clock: Callable[[], float] = time.time,
        logger: Any = None,
    ) -> None:
        self.store = store
        self.planner = planner or BoundedAgentPlanner()
        self.runtime = task_runtime
        self.proposals = proposal_service
        self.observe = observe
        self.character_id = str(character_id or "")
        self.plan_ttl_seconds = float(plan_ttl_seconds)
        self.replan_budget = int(replan_budget)
        self.view_limit = max(1, int(view_limit))
        self._clock = clock
        self._log = logger
        self.created_count = 0
        self.merged_count = 0
        self.degraded_reason = ""

    # ------------------------------------------------------------ 只读

    def _now(self, now: float | None = None) -> float:
        return float(now if now is not None else self._clock())

    async def plan_for_task(self, task_id: str) -> AgentPlan | None:
        """task_id → 关联的 AgentPlan（确认前身份复核用；没有就 None）。"""
        if not str(task_id):
            return None
        for plan in await self.recent(50):
            if plan.task_id == str(task_id):
                return plan
        return None

    async def recent(self, limit: int | None = None) -> list[AgentPlan]:
        return await self.store.recent(max(1, int(limit if limit is not None else self.view_limit)))

    async def current(self) -> AgentPlan | None:
        for plan in await self.recent(50):
            if plan.open:
                return plan
        return None

    def to_view(self, plan: AgentPlan) -> dict[str, Any]:
        """单份计划只读投影（§九）。**执行状态实时读 Task**（修订 3），这里不存。"""
        target = plan.target.to_payload()
        return {
            "plan_id": plan.plan_id,
            "source": plan.source,
            "objective": plan.objective,
            "status": plan.status,
            "proposal_id": plan.proposal_id,
            "intent_id": plan.intent_id,
            "task_id": plan.task_id,
            "target": {
                "status": target.get("status"),
                "player_name": target.get("player_name"),
                "server_id": target.get("server_id"),
                "uuid_suffix": str(target.get("player_uuid") or "")[-4:],
                "reason": target.get("reason"),
            },
            "version": plan.version,
            "steps": [
                {
                    "step_id": step.get("step_id"),
                    "tool": step.get("tool"),
                    "risk": step.get("risk"),
                }
                for step in ((plan.plan or {}).get("steps") or [])
            ],
            "checks": [dict(item) for item in plan.checks],
            "risk_summary": dict(plan.risk_summary),
            "reason": plan.reason,
            "replans": plan.replans,
            "replan_budget": plan.replan_budget,
            "approver_user_id": plan.approver_user_id,
            "created_at": plan.created_at,
            "expires_at": plan.expires_at,
        }

    def _task_state(self, task_id: str, task_reader: Any = None) -> str:
        """修订 3：执行状态实时读 TaskRuntime（只读），读不到就空串（WebUI 如实显示）。"""
        if not task_id:
            return ""
        runtime = self.runtime
        getter = getattr(runtime, "get", None) if runtime is not None else None
        if not callable(getter):
            return ""
        try:
            from app.tasks.models import TaskRecord  # noqa: F401 - 仅文档性引用

            return ""  # 同步上下文里不做 IO；异步视图在 view_async() 里填
        except Exception:  # noqa: BLE001
            return ""

    async def view(self, *, task_reader: Any = None) -> dict[str, Any]:
        """只读视图（§九）。``task_reader(task_id) -> TaskRecord|None`` 由装配方注入
        （异步），把每份计划关联任务的**实时状态**并排展示（修订 3）。"""
        rows = await self.recent(self.view_limit)
        states: dict[str, str] = {}
        if task_reader is not None:
            for item in rows:
                if not item.task_id:
                    continue
                try:
                    record = await task_reader(item.task_id)
                except Exception:  # noqa: BLE001 - 读不到就如实空着
                    record = None
                states[item.task_id] = str(
                    getattr(getattr(record, "state", None), "value", "") or ""
                )
        plans = []
        for item in rows:
            view = self.to_view(item)
            view["task_state"] = states.get(item.task_id, "")
            plans.append(view)
        return {
            "enabled": True,
            "execution_layer": "TASK_RUNTIME",  # 执行永远在既有链（§一）
            "plans": plans,
        }

    # ------------------------------------------------------------ USER 路径（一次确认）

    async def record_user_plan(
        self,
        *,
        objective: str,
        user_id: str,
        session_id: str,
        task_id: str,
        planned: Any,
        checks: tuple[dict[str, Any], ...] = (),
        now: float | None = None,
    ) -> AgentPlan | None:
        """USER 请求的计划记账（修订 1：与待确认任务**同时**建立，批准状态由 Task 派生）。

        ``planned`` 是 planner 的 :class:`PlanningResult` 或 5A 的 :class:`PlannedTask`。
        失败只吞掉 —— 记账绝不影响既有任务链。
        """
        moment = self._now(now)
        plan_payload = self._plan_payload(planned)
        plan_hash = str(getattr(getattr(planned, "plan", None), "hash", "") or "")
        record = AgentPlan(
            plan_id="",
            source=PlanSource.USER.value,
            objective=" ".join(str(objective or "").split())[:200],
            status=PlanStatus.LINKED.value if task_id else PlanStatus.READY_FOR_APPROVAL.value,
            task_id=str(task_id or ""),
            initiator=str(user_id),
            target=PlanTarget(),  # 资源目标不需要指定玩家；跟随目标在 plan_from_follow 里存
            plan=plan_payload,
            plan_hash=plan_hash,
            version=1,
            checks=tuple(checks),
            risk_summary=dict(getattr(planned, "risk_summary", {}) or {}),
            replan_budget=self.replan_budget,
            fingerprint=plan_fingerprint(
                source=PlanSource.USER,
                objective=objective,
                bucket=plan_time_bucket(moment),
            ),
            created_at=moment,
            expires_at=moment + self.plan_ttl_seconds,
            updated_at=moment,
        )
        return await self._create(record, now=moment)

    async def plan_follow_from_user(
        self,
        *,
        objective: str,
        user_id: str,
        session_id: str,
        target: PlanTarget,
        now: float | None = None,
    ) -> dict[str, Any]:
        """「跟着我」这条链（§八场景 B）：规划 →（同一次批准语义下）建待确认任务。

        返回 ``{"action": ..., "reply": ..., "plan": AgentPlan|None, "record": Task|None}``。
        """
        moment = self._now(now)
        result = await self.planner.plan(objective, target=target, observe=self.observe)
        if not result.ready or result.plan is None:
            return {
                "action": "plan_rejected",
                "reply": f"现在排不了这件事：{result.reason}",
                "outcome": result.outcome.value,
                "plan": None,
                "record": None,
            }
        if self.runtime is None:
            return {
                "action": "plan_rejected",
                "reply": "任务系统现在不可用。",
                "outcome": result.outcome.value,
                "plan": None,
                "record": None,
            }
        try:
            record = await self.runtime.create_task(
                result.plan.plan.objective,
                session_id=str(session_id),
                user_id=str(user_id),
                origin="user",
                plan=result.plan.plan,
                observations=result.plan.observations,
                source="qq",
            )
        except Exception as exc:  # noqa: BLE001 - 任务忙等既有异常照既有话术处理
            return {
                "action": "busy" if type(exc).__name__ == "TaskBusy" else "create_failed",
                "reply": "我手上还有一件事没做完，先做完这个再说。"
                if type(exc).__name__ == "TaskBusy"
                else "任务建立失败，等会儿再试。",
                "outcome": result.outcome.value,
                "plan": None,
                "record": None,
            }
        agent_plan = await self._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.LINKED.value,
                task_id=record.task_id,
                initiator=str(user_id),
                target=target,
                plan=result.plan.plan.to_payload(),
                plan_hash=str(record.plan_hash),
                version=1,
                checks=result.checks,
                risk_summary=dict(result.risk_summary),
                replan_budget=self.replan_budget,
                fingerprint=plan_fingerprint(
                    source=PlanSource.USER,
                    objective=objective,
                    target_key=f"{target.status}:{target.player_uuid or ''}",
                    bucket=plan_time_bucket(moment),
                ),
                created_at=moment,
                expires_at=moment + self.plan_ttl_seconds,
                updated_at=moment,
            ),
            now=moment,
        )
        summary = self.runtime.summary_of(record)
        return {
            "action": "created",
            "reply": summary,
            "outcome": result.outcome.value,
            "plan": agent_plan,
            "record": record,
        }

    # ------------------------------------------------------------ LIFE 路径（两道门）

    async def plan_from_proposal(
        self, proposal: TaskProposal, *, now: float | None = None
    ) -> dict[str, Any]:
        """LIFE 提案 → AgentPlan（**只规划**，不建 Task —— 修订 1 的第一道门之前）。"""
        moment = self._now(now)
        status = str(getattr(proposal, "status", "") or "")
        if status not in {
            ProposalStatus.READY_FOR_FUTURE_EXECUTION.value,
            ProposalStatus.NEEDS_USER_APPROVAL.value,
        }:
            return {"action": "skipped", "reason": f"proposal_{status.lower() or 'unknown'}"}
        if proposal.terminal or proposal.expired_at(moment):
            return {"action": "skipped", "reason": "proposal_not_open"}
        objective = str(proposal.objective or "")
        if not objective:
            return {"action": "skipped", "reason": "empty_objective"}
        target = self._target_of_proposal(proposal)
        result = await self.planner.plan(objective, target=target, observe=self.observe)
        status_map = {
            PlanningOutcome.READY_FOR_APPROVAL: PlanStatus.READY_FOR_APPROVAL,
            PlanningOutcome.NEEDS_MORE_INFORMATION: PlanStatus.NEEDS_MORE_INFORMATION,
            PlanningOutcome.UNSUPPORTED: PlanStatus.UNSUPPORTED,
            PlanningOutcome.BLOCKED_BY_POLICY: PlanStatus.BLOCKED_BY_POLICY,
            PlanningOutcome.BLOCKED_BY_PRECONDITION: PlanStatus.BLOCKED_BY_PRECONDITION,
        }
        plan_status = status_map.get(result.outcome, PlanStatus.NEEDS_MORE_INFORMATION)
        record = await self._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective=" ".join(objective.split())[:200],
                status=plan_status,
                proposal_id=str(proposal.proposal_id),
                intent_id=str(proposal.intent_id),
                initiator=str(proposal.initiator or self.character_id),
                target=target,
                plan=result.plan.plan.to_payload() if result.plan is not None else {},
                plan_hash=self._hash_of(result),
                version=1,
                checks=result.checks,
                risk_summary=dict(result.risk_summary),
                reason=result.reason,
                replan_budget=self.replan_budget,
                fingerprint=plan_fingerprint(
                    source=PlanSource.LIFE,
                    objective=objective,
                    target_key=f"{target.status}:{target.player_uuid or ''}",
                    proposal_id=str(proposal.proposal_id),
                    bucket=plan_time_bucket(moment),
                ),
                created_at=moment,
                expires_at=moment + self.plan_ttl_seconds,
                updated_at=moment,
            ),
            now=moment,
        )
        return {"action": "planned", "plan": record, "outcome": result.outcome.value}

    async def approve(
        self,
        *,
        plan_id: str = "",
        user_id: str,
        session_id: str,
        now: float | None = None,
    ) -> dict[str, Any]:
        """LIFE 第一道门（修订 1）：用户「批准」→ 建**待确认**任务（不执行）。

        * 只批准 ``READY_FOR_APPROVAL`` 的 LIFE 计划（不给空 plan_id 就取最近一份）；
        * 批准者身份记进计划，随后的 ``create_task`` 用**批准者**建任务 ——
          第二道门的 ``confirm_and_start`` 天然要求同一人同一会话（既有校验）。
        """
        moment = self._now(now)
        plan = await self.store.get(str(plan_id)) if plan_id else await self.current()
        if plan is None or plan.source != PlanSource.LIFE.value:
            return {"action": "not_found", "reply": "", "plan": None, "record": None}
        if plan.status != PlanStatus.READY_FOR_APPROVAL.value:
            return {
                "action": "not_approvable",
                "reply": f"这份计划现在的状态是 {plan.status}，不能批准。",
                "plan": plan,
                "record": None,
            }
        if not (plan.plan or {}).get("steps"):
            return {
                "action": "not_approvable",
                "reply": "这份计划还没有可执行的步骤，不能批准。",
                "plan": plan,
                "record": None,
            }
        if self.runtime is None:
            return {
                "action": "degraded",
                "reply": "任务系统现在不可用。",
                "plan": plan,
                "record": None,
            }
        from app.tasks.models import TaskPlan

        try:
            task_plan = TaskPlan.from_payload(dict(plan.plan))
        except Exception:  # noqa: BLE001 - 坏计划不猜
            return {
                "action": "corrupt",
                "reply": "这份计划的数据读不出来，不能批准。",
                "plan": plan,
                "record": None,
            }
        try:
            record = await self.runtime.create_task(
                task_plan.objective,
                session_id=str(session_id),
                user_id=str(user_id),
                origin="user",
                plan=task_plan,
                observations=task_plan.observations,
                source="qq_life_plan",
            )
        except Exception as exc:  # noqa: BLE001
            busy = type(exc).__name__ == "TaskBusy"
            return {
                "action": "busy" if busy else "create_failed",
                "reply": "我手上还有一件事没做完，先做完这个再说。"
                if busy
                else "任务建立失败，等会儿再试。",
                "plan": plan,
                "record": None,
            }
        approved = await self.store.update_status(
            plan.plan_id, PlanStatus.LINKED, reason=f"approved_by:{user_id}", now=moment
        )
        if approved is not None:
            payload = approved.to_payload()
            payload["task_id"] = record.task_id
            payload["approver_user_id"] = str(user_id)
            payload["approver_session_id"] = str(session_id)
            payload["approved_at"] = moment
            updated = AgentPlan.from_payload(payload)
            approved = await self.store.replace_plan(updated) or approved
            await self.store.log_event(
                plan_id=approved.plan_id,
                event="agentplan.approved",
                reason=f"approver={user_id} task={record.task_id}",
                at=moment,
                detail={"gate": "plan_approval", "next_gate": "execution_confirmation"},
            )
        return {
            "action": "approved",
            "plan": approved,
            "record": record,
            # 文案明确这是第一道门（修订 1：让用户知道现在站在哪道门前）
            "reply": (
                "计划已批准（第 1/2 步）。任务已建立，还需要你回复「确认」才会真正开始。\n"
                + self.runtime.summary_of(record)
            ),
        }

    async def handle_qq(self, *, text: str, user_id: str, session_id: str) -> dict[str, Any] | None:
        """QQ 消息入口：只认 LIFE 计划的「批准」（返回 None = 不归计划管，交给任务/聊天）。"""
        if not is_approve_plan_command(text):
            return None
        pending = [
            item
            for item in await self.store.open_plans()
            if item.source == PlanSource.LIFE.value
            and item.status == PlanStatus.READY_FOR_APPROVAL.value
        ]
        if not pending:
            return None  # 没有待批准计划 → 让「批准」变成普通聊天（与 5A 的确认同哲学）
        newest = max(pending, key=lambda item: item.created_at)
        return await self.approve(plan_id=newest.plan_id, user_id=user_id, session_id=session_id)

    # ------------------------------------------------------------ 生命周期 / 预算

    async def expire_due(self, *, now: float | None = None) -> list[AgentPlan]:
        moment = self._now(now)
        expired: list[AgentPlan] = []
        for plan in await self.store.open_plans():
            if not plan.expired_at(moment):
                continue
            updated = await self.store.update_status(
                plan.plan_id, PlanStatus.EXPIRED, reason="plan_ttl", now=moment
            )
            if updated is None:
                continue
            expired.append(updated)
            await self.store.log_event(
                plan_id=updated.plan_id, event="agentplan.expired", reason="plan_ttl", at=moment
            )
        return expired

    async def recover(self, *, now: float | None = None) -> dict[str, Any]:
        """重启恢复（§六）：只处理过期与统计，**绝不**复活终态、绝不自动重规划。"""
        moment = self._now(now)
        try:
            expired = await self.expire_due(now=moment)
            rows = await self.recent(50)
        except Exception as exc:  # noqa: BLE001 - 恢复失败只降级
            self.degraded_reason = f"{type(exc).__name__}"
            return {"action": "degraded", "expired": 0, "open": 0, "reason": "error"}
        return {
            "action": "ok",
            "expired": len(expired),
            "open": sum(1 for item in rows if item.open),
            "recent": len(rows),
            "reason": "",
        }

    async def plan_open_life_proposals(
        self, proposals: list[TaskProposal], *, now: float | None = None
    ) -> dict[str, Any]:
        """调度器入口：把**新建的 LIFE 提案**变成计划（一次最多 MAX_LIFE_PLANS_PER_PASS 条）。"""
        moment = self._now(now)
        planned: list[AgentPlan] = []
        skipped: list[str] = []
        for proposal in list(proposals)[:MAX_LIFE_PLANS_PER_PASS]:
            try:
                out = await self.plan_from_proposal(proposal, now=moment)
            except Exception:  # noqa: BLE001 - 单条失败不影响其余
                if self._log is not None:
                    self._log.exception("[AgentPlan] LIFE 规划失败（忽略）")
                skipped.append("error")
                continue
            if out.get("action") == "planned" and out.get("plan") is not None:
                planned.append(out["plan"])
            else:
                skipped.append(str(out.get("reason") or ""))
        await self.expire_due(now=moment)
        return {"action": "planned" if planned else "noop", "planned": planned, "skipped": skipped}

    def replan_budget_left(self, plan: AgentPlan) -> int:
        return max(0, int(plan.replan_budget) - int(plan.replans))

    # ------------------------------------------------------------ 内部

    async def _create(self, plan: AgentPlan, *, now: float) -> AgentPlan | None:
        stored, created = await self.store.create(plan)
        event = "agentplan.created" if created else "agentplan.deduplicated"
        if created:
            self.created_count += 1
        else:
            self.merged_count += 1
        await self.store.log_event(
            plan_id=stored.plan_id,
            event=event,
            reason=str(stored.reason or stored.status),
            at=now,
            detail={
                "source": stored.source,
                "status": stored.status,
                "proposal_id": stored.proposal_id,
                "task_id": stored.task_id,
                "outcome_reason": stored.reason,
            },
        )
        if self._log is not None:
            self._log.info(
                "[AgentPlan] action=%s id=%s source=%s status=%s task=%s proposal=%s",
                "created" if created else "merged",
                stored.plan_id,
                stored.source,
                stored.status,
                stored.task_id or "-",
                stored.proposal_id or "-",
            )
        return stored

    def _plan_payload(self, planned: Any) -> dict[str, Any]:
        plan = getattr(planned, "plan", None)
        to_payload = getattr(plan, "to_payload", None)
        return dict(to_payload()) if callable(to_payload) else {}

    def _hash_of(self, result: Any) -> str:
        plan = getattr(result, "plan", None)
        task_plan = getattr(plan, "plan", None)
        return str(getattr(task_plan, "hash", "") or "")

    def _target_of_proposal(self, proposal: TaskProposal) -> PlanTarget:
        target = dict(getattr(proposal, "target", {}) or {})
        status = str(target.get("status") or "")
        if status == "VERIFIED" and not target.get("player_name"):
            # 不需要指定玩家的目标（no_player_target）
            return PlanTarget(status="VERIFIED", reason="no_player_target")
        return PlanTarget(
            status=status or "MISSING",
            server_id=str(target.get("server_id") or ""),
            player_uuid=str(target.get("player_uuid") or ""),
            player_name=str(target.get("player_name") or ""),
            reason=str(target.get("reason") or ""),
        )
