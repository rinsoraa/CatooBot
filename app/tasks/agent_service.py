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
#: 7D.2 §1.2.5：跟随预留租约（秒）。PLANNING 记录在这个时长内 = "另一请求正在处理中"
#: （并发让路）；超过它 = 崩溃遗留，终结后才允许有界重试。上限始终 ≤ plan_ttl_seconds。
DEFAULT_RESERVE_LEASE_SECONDS = 120.0

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
        reserve_lease_seconds: float = DEFAULT_RESERVE_LEASE_SECONDS,
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
        #: 7D.2 §1.2.5：预留租约 —— PLANNING 记录在这个时长内视为"另一请求正在处理"，
        #: 并发请求让路；超过它 = 崩溃遗留，可终结后重试（上限受 plan_ttl 约束）。
        self.reserve_lease_seconds = max(
            1.0, min(float(reserve_lease_seconds), float(plan_ttl_seconds))
        )
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

        7D.1 P1-1：任务**已经建了**才走到这里，所以指纹必须绑死 task_id（每个任务一份
        计划，永不去重合并）—— 否则同桶重复请求会把新任务挂到旧计划的 task_id 上。
        记账失败 → **安全补偿**：取消这个还没有任何计划关联的待确认任务（它还没执行过
        任何动作），并留审计；绝不留下一个"没有可信计划关联"的孤儿任务。
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
            target=PlanTarget(),  # 资源目标不需要指定玩家；跟随目标在 plan_follow_from_user 里存
            plan=plan_payload,
            plan_hash=plan_hash,
            version=1,
            checks=tuple(checks),
            risk_summary=dict(getattr(planned, "risk_summary", {}) or {}),
            replan_budget=self.replan_budget,
            fingerprint=plan_fingerprint(
                source=PlanSource.USER,
                objective=objective,
                target_key=f"task:{task_id}" if task_id else "",
                bucket=plan_time_bucket(moment),
            ),
            created_at=moment,
            expires_at=moment + self.plan_ttl_seconds,
            updated_at=moment,
        )
        stored, created = await self._create(record, now=moment)
        if not created and str(task_id):
            # 指纹合并 = 这份计划描述的是**别的**任务 → 新任务成了孤儿 → 补偿取消
            await self._compensate_orphan_task(
                str(task_id), reason="agent_plan_fingerprint_merged", now=moment
            )
        return stored

    async def plan_follow_from_user(
        self,
        *,
        objective: str,
        user_id: str,
        session_id: str,
        target: PlanTarget,
        now: float | None = None,
    ) -> dict[str, Any]:
        """「跟着我」这条链（§八场景 B）：规划 → 建待确认任务。

        7D.1 P1-1 **reserve-then-link**：先原子占用指纹（AgentPlan 以 PLANNING 落盘，
        唯一索引保证同桶同目标只有一个占用者），**任务建立成功后**才落 LINKED 并绑
        task_id。顺序反了会出现"任务已建但计划合并到旧行"的孤儿 —— 真机基线的缺陷。

        * 占用失败（拿到的别人/旧记录）：读旧记录关联 Task 的实际状态 —— 还开着就
          如实返回"已有任务"，终态就按**重试序号**生成新指纹重试一次（有界、确定）。
        * 任务建立失败/返回非待确认状态 → 计划补偿为 CANCELLED（LINKED/CANCELLED
          均可从 PLANNING 到达），绝不留下说不出来历的半完成记录。
        * 任务建好但计划关联（replace_plan）失败 → 取消**尚未执行**的待确认任务。

        返回 ``{"action": ..., "reply": ..., "plan": ..., "record": ...}``。
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
        base = plan_fingerprint(
            source=PlanSource.USER,
            objective=objective,
            # 7D.2 §1.2.6：server_id 也进指纹 —— 不同服务器的同名/同 UUID 目标绝共用一份计划
            target_key=f"{target.status}:{target.player_uuid or ''}@{target.server_id or ''}",
            bucket=plan_time_bucket(moment),
        )
        stem = base.rsplit("|", 1)[0]
        reserved, created = await self._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.PLANNING.value,  # 占用位：任务还没建
                initiator=str(user_id),
                target=target,
                plan=result.plan.plan.to_payload(),
                version=1,
                checks=result.checks,
                risk_summary=dict(result.risk_summary),
                replan_budget=self.replan_budget,
                fingerprint=base,
                created_at=moment,
                expires_at=moment + self.plan_ttl_seconds,
                updated_at=moment,
            ),
            now=moment,
        )
        if not created:
            # 同指纹已有记录：看它关联 Task 的**实际状态**决定让路还是重试
            verdict, reserved = await self._follow_retry_or_yield(
                reserved,
                stem=stem,
                objective=objective,
                user_id=user_id,
                target=target,
                result=result,
                moment=moment,
            )
            if verdict is not None:
                return verdict
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
            await self._compensate_reserved_plan(
                reserved, reason=f"task_create_failed:{type(exc).__name__}", now=moment
            )
            return {
                "action": "busy" if type(exc).__name__ == "TaskBusy" else "create_failed",
                "reply": "我手上还有一件事没做完，先做完这个再说。"
                if type(exc).__name__ == "TaskBusy"
                else "任务建立失败，等会儿再试。",
                "outcome": result.outcome.value,
                "plan": reserved,
                "record": None,
            }
        state = str(getattr(getattr(record, "state", None), "value", "") or "")
        if state != "PENDING_CONFIRMATION":
            # create_task 校验失败时返回 FAILED 记录而不是抛异常 —— 不是成功，安全补偿
            await self._compensate_reserved_plan(
                reserved, reason=f"task_unexpected_state:{state or 'unknown'}", now=moment
            )
            return {
                "action": "create_failed",
                "reply": "任务建立失败，等会儿再试。",
                "outcome": result.outcome.value,
                "plan": reserved,
                "record": record,
            }
        linked = await self._link_reserved_plan(
            reserved,
            task_id=str(record.task_id),
            plan_hash=str(record.plan_hash),
            now=moment,
        )
        if linked is None:
            # 关联持久化失败 → 取消**尚未执行**的待确认任务（安全补偿），不宣称成功
            await self._compensate_orphan_task(
                str(record.task_id), reason="agent_plan_link_failed", now=moment
            )
            return {
                "action": "create_failed",
                "reply": "任务建立失败，等会儿再试。",
                "outcome": result.outcome.value,
                "plan": reserved,
                "record": record,
            }
        summary = self.runtime.summary_of(record)
        return {
            "action": "created",
            "reply": summary,
            "outcome": result.outcome.value,
            "plan": linked,
            "record": record,
        }

    async def _follow_retry_or_yield(
        self,
        reserved: AgentPlan,
        *,
        stem: str,
        objective: str,
        user_id: str,
        target: PlanTarget,
        result: Any,
        moment: float,
    ) -> tuple[dict[str, Any] | None, AgentPlan]:
        """指纹已被占用时的有界重试/让路（7D.1 §1.2.2/§1.2.3）。

        返回 ``(verdict, reserve)``：``verdict`` 非 None = 让路（调用方直接返回）；
        ``verdict`` 为 None = 已用**重试序号**（同词干行数，确定性、有界）占到了新指纹，
        ``reserve`` 是新的占用记录，调用方继续建任务。
        """
        existing_task = str(reserved.task_id or "")
        # 7D.2 P1-1：**正在预留中的记录（PLANNING 且无 task_id）= 另一请求可能正在
        # 创建任务**。此时让路（明确"处理中"），绝不立即生成 |rN 新任务 —— 否则
        # 两个并发请求（尤其不同 session，TaskRuntime 的同会话互斥拦不住）各建一份任务。
        if not existing_task and reserved.status == PlanStatus.PLANNING.value:
            reserved_age = max(0.0, float(moment) - float(reserved.created_at))
            if reserved_age <= self.reserve_lease_seconds:
                return (
                    {
                        "action": "already_planned",
                        "reply": "这个请求我正在处理中，稍等一下再看结果。",
                        "outcome": result.outcome.value,
                        "plan": reserved,
                        "record": None,
                        "in_flight": True,
                    },
                    reserved,
                )
            # lease 过期 = 崩溃遗留的预留（recover 也会清，这里是请求路径上的兜底）
            # → 先终结它，再走有界重试
            await self._compensate_reserved_plan(
                reserved, reason="reserve_lease_expired", now=moment
            )
        if existing_task and self.runtime is not None:
            getter = getattr(self.runtime, "get", None)
            if callable(getter):
                try:
                    old_record = await getter(existing_task)
                except Exception:
                    # 7D.2 §1.2.4：读不到任务状态 ≠ 任务已终态 —— 保守让路，不猜
                    return (
                        {
                            "action": "already_planned",
                            "reply": (
                                f"这件事我已经在办了（任务 {existing_task}，状态暂时读不到）；"
                                "先处理那一件，或者等它结束再说一次。"
                            ),
                            "outcome": result.outcome.value,
                            "plan": reserved,
                            "record": None,
                        },
                        reserved,
                    )
                state = str(getattr(getattr(old_record, "state", None), "value", "") or "")
                if state and not self._task_state_is_terminal(state):
                    return (
                        {
                            "action": "already_planned",
                            "reply": (
                                f"这件事我已经在办了（任务 {existing_task}，状态 {state}）；"
                                "先处理那一件，或者等它结束再说一次。"
                            ),
                            "outcome": result.outcome.value,
                            "plan": reserved,
                            "record": old_record,
                        },
                        reserved,
                    )
        # 旧计划已终态 / Task 已终态 / 没有关联 → 有界重试：新指纹 = 词干 + 重试序号
        attempts = int(await self.store.count_by_stem(stem))
        if attempts > self.replan_budget + 2:  # 有界：超过预算不再重试（防爆）
            return (
                {
                    "action": "already_planned",
                    "reply": "这个请求短时间内重复太多次了，先歇一会儿再说。",
                    "outcome": result.outcome.value,
                    "plan": reserved,
                    "record": None,
                },
                reserved,
            )
        retry_fp = f"{stem}|r{attempts}"
        retry, created = await self._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.PLANNING.value,
                initiator=str(user_id),
                target=target,
                plan=result.plan.plan.to_payload(),
                version=1,
                checks=result.checks,
                risk_summary=dict(result.risk_summary),
                replan_budget=self.replan_budget,
                fingerprint=retry_fp,
                created_at=moment,
                expires_at=moment + self.plan_ttl_seconds,
                updated_at=moment,
            ),
            now=moment,
        )
        if created:
            return None, retry
        # 重试指纹也被占（并发）：让路给那份
        return {
            "action": "already_planned",
            "reply": "这件事我已经在办了；先处理那一件，或者等它结束再说一次。",
            "outcome": result.outcome.value,
            "plan": retry,
            "record": None,
        }, reserved

    @staticmethod
    def _task_state_is_terminal(state: str) -> bool:
        from app.tasks.models import TaskState

        try:
            return TaskState(state).terminal
        except ValueError:
            return False

    async def _link_reserved_plan(
        self, reserved: AgentPlan, *, task_id: str, plan_hash: str, now: float
    ) -> AgentPlan | None:
        """占用成功后把计划落到 LINKED（带 task_id / plan_hash / 审计）。"""
        payload = reserved.to_payload()
        payload["status"] = PlanStatus.LINKED.value
        payload["task_id"] = str(task_id)
        payload["plan_hash"] = str(plan_hash)
        payload["updated_at"] = float(now)
        linked = AgentPlan.from_payload(payload)
        try:
            updated = await self.store.replace_plan(linked)
        except Exception as exc:  # noqa: BLE001 - 存储故障 = 关联失败 → 调用方补偿
            if self._log is not None:
                self._log.exception("[AgentPlan] link 持久化失败 task=%s", task_id)
            self.degraded_reason = f"{type(exc).__name__}"
            return None
        if updated is None:
            return None
        await self.store.log_event(
            plan_id=updated.plan_id,
            event="agentplan.linked",
            reason=f"task={task_id}",
            at=now,
            detail={"task_id": str(task_id), "plan_hash": str(plan_hash)},
        )
        if self._log is not None:
            self._log.info("[AgentPlan] linked id=%s task=%s", updated.plan_id, task_id)
        return updated

    async def _compensate_reserved_plan(
        self, reserved: AgentPlan, *, reason: str, now: float
    ) -> None:
        """占用后任务建立失败 → 计划补偿为 CANCELLED（可审计，绝不留半完成）。"""
        cancelled = await self.store.update_status(
            reserved.plan_id, PlanStatus.CANCELLED, reason=reason, now=now
        )
        if cancelled is None:
            # 已被并发改掉（终态不可覆盖）→ 尊重现状，只留审计
            reason = f"{reason} (already_{reserved.status})"
        await self.store.log_event(
            plan_id=reserved.plan_id,
            event="agentplan.compensated",
            reason=reason,
            at=now,
            detail={"kind": "reserved_plan"},
        )
        if self._log is not None:
            self._log.warning("[AgentPlan] compensated plan=%s reason=%s", reserved.plan_id, reason)

    async def _compensate_orphan_task(self, task_id: str, *, reason: str, now: float) -> None:
        """取消一个还没有可信计划关联的待确认任务（它没有执行过任何动作）。"""
        cancel = getattr(self.runtime, "cancel", None) if self.runtime is not None else None
        ok = False
        if callable(cancel):
            try:
                await cancel(task_id, reason=f"agent_plan:{reason}")
                ok = True
            except Exception:  # noqa: BLE001 - 取消失败也必须留审计
                if self._log is not None:
                    self._log.exception("[AgentPlan] 孤儿任务取消失败 task=%s", task_id)
        await self.store.log_event(
            plan_id="-",
            event="agentplan.orphan_task",
            reason=f"{reason} cancelled={ok}",
            at=now,
            detail={"task_id": str(task_id)},
        )
        if self._log is not None:
            self._log.warning(
                "[AgentPlan] orphan task compensated task=%s cancelled=%s reason=%s",
                task_id,
                ok,
                reason,
            )

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
        stored_plan, _created = await self._create(
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
                    # 7D.2 §1.2.6：server_id 也进指纹（不同服务器绝不共用一份计划）
                    target_key=(
                        f"{target.status}:{target.player_uuid or ''}@{target.server_id or ''}"
                    ),
                    proposal_id=str(proposal.proposal_id),
                    bucket=plan_time_bucket(moment),
                ),
                created_at=moment,
                expires_at=moment + self.plan_ttl_seconds,
                updated_at=moment,
            ),
            now=moment,
        )
        return {"action": "planned", "plan": stored_plan, "outcome": result.outcome.value}

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
        # 7D.1 P1-3：批准入口**直接**检查过期（expire_due 只是清理，不是安全边界）。
        if plan.expired_at(moment):
            expired = await self.store.update_status(
                plan.plan_id, PlanStatus.EXPIRED, reason="approved_after_ttl", now=moment
            )
            await self.store.log_event(
                plan_id=plan.plan_id,
                event="agentplan.compensated",
                reason="approve_on_expired_plan",
                at=moment,
                detail={"gate": "plan_approval"},
            )
            return {
                "action": "not_approvable",
                "reply": "这份计划已经过期了，不能批准。",
                "plan": expired or plan,
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
        # 原子占用（7D.1 P1-3）：READY_FOR_APPROVAL → APPROVED 只有一个并发请求能成功，
        # 且占用条件里带过期检查 —— 并发批准绝不会建出两份 Task。
        occupied = await self.store.occupy_for_approval(plan.plan_id, now=moment)
        if occupied is None:
            return {
                "action": "not_approvable",
                "reply": "这份计划刚刚被处理过（已批准或已过期），不能重复批准。",
                "plan": await self.store.get(plan.plan_id) or plan,
                "record": None,
            }
        plan = occupied
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
            # 占用后建任务失败 → 占用状态安全终结（绝不留 APPROVED 半完成记录）
            await self._finalize_occupied_plan(
                plan,
                status=PlanStatus.CANCELLED,
                reason=f"task_create_failed:{type(exc).__name__}",
                now=moment,
            )
            return {
                "action": "busy" if busy else "create_failed",
                "reply": "我手上还有一件事没做完，先做完这个再说。"
                if busy
                else "任务建立失败，等会儿再试。",
                "plan": plan,
                "record": None,
            }
        state = str(getattr(getattr(record, "state", None), "value", "") or "")
        if state != "PENDING_CONFIRMATION":
            # create_task 校验失败返回 FAILED 记录 —— 不是成功，安全补偿
            await self._finalize_occupied_plan(
                plan,
                status=PlanStatus.CANCELLED,
                reason=f"task_unexpected_state:{state}",
                now=moment,
            )
            return {
                "action": "create_failed",
                "reply": "任务建立失败，等会儿再试。",
                "plan": plan,
                "record": record,
            }
        # 关联更新必须有明确结果：失败 → 取消待确认任务（安全补偿），不宣称批准完成
        payload = plan.to_payload()
        payload["task_id"] = record.task_id
        payload["approver_user_id"] = str(user_id)
        payload["approver_session_id"] = str(session_id)
        payload["approved_at"] = moment
        payload["status"] = PlanStatus.LINKED.value
        payload["reason"] = f"approved_by:{user_id}"
        linked = AgentPlan.from_payload(payload)
        approved = await self.store.replace_plan(linked)
        if approved is None:
            await self._compensate_orphan_task(
                str(record.task_id), reason="agent_plan_link_failed", now=moment
            )
            return {
                "action": "create_failed",
                "reply": "任务建立失败，等会儿再试。",
                "plan": plan,
                "record": record,
            }
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
            # 文案明确这是第一道门（修订 1），并带上被批准计划的可识别摘要
            # （7D.1 §3.2.9：不能含糊地批准用户没看过的计划）
            "reply": (
                f"计划已批准（第 1/2 步）：{approved.objective}\n"
                "任务已建立，还需要你回复「确认」才会真正开始。\n" + self.runtime.summary_of(record)
            ),
        }

    async def _finalize_occupied_plan(
        self, plan: AgentPlan, *, status: PlanStatus, reason: str, now: float
    ) -> None:
        """占用（APPROVED）之后的失败补偿：落到明确的终态并留审计。

        APPROVED 没有到 CANCELLED 的状态机出口，所以这里用 ``replace_plan`` 整行落账
        —— 这是**补偿**，不是状态机转移；原因必须写清。
        """
        payload = plan.to_payload()
        payload["status"] = status.value
        payload["reason"] = reason
        payload["updated_at"] = float(now)
        await self.store.replace_plan(AgentPlan.from_payload(payload))
        await self.store.log_event(
            plan_id=plan.plan_id,
            event="agentplan.compensated",
            reason=reason,
            at=now,
            detail={"from": PlanStatus.APPROVED.value, "to": status.value},
        )
        if self._log is not None:
            self._log.warning(
                "[AgentPlan] occupied plan compensated id=%s to=%s reason=%s",
                plan.plan_id,
                status.value,
                reason,
            )

    async def handle_qq(self, *, text: str, user_id: str, session_id: str) -> dict[str, Any] | None:
        """QQ 消息入口：只认 LIFE 计划的「批准」（返回 None = 不归计划管，交给任务/聊天）。"""
        if not is_approve_plan_command(text):
            return None
        moment = self._now()
        # 7D.1 §3.2.2：选择候选时就排除已过期计划（expire_due 只是清理，不是安全边界）
        pending = [
            item
            for item in await self.store.open_plans()
            if item.source == PlanSource.LIFE.value
            and item.status == PlanStatus.READY_FOR_APPROVAL.value
            and not item.expired_at(moment)
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
        """重启恢复（§六）：过期清理 + 孤儿 PLANNING/APPROVED 补偿；绝不复活终态。

        三个崩溃边界（7D.1 P1-1 + 7D.2 P1-2）：

        * reserve（PLANNING 落盘）与 link（建任务 + 落 LINKED）之间中断
          → 没有 task_id 的 PLANNING 记录。任务**不存在**（还没建）→ CANCELLED；
        * 批准占用（APPROVED）与"建任务 + 关联"之间中断
          → 没有 task_id 的 APPROVED 记录。它是**未完成的批准占用**：
          - 不创建 Task（自动恢复执行 = 越权）；不回退成 READY_FOR_APPROVAL（会被再次批准）；
          - 补偿为 CANCELLED（可审计），原因写明是"批准占用未完成"；
        * 关联**半写**（任务确实建了、列说 APPROVED + 有 task_id，payload 还是 READY）
          → 查得到任务就**修复**为 LINKED（补 task_id；不取消 —— 它可能正等着用户确认），
          查不到才与真孤儿一样补偿 CANCELLED。

        判据以**行级列事实**为准（``state_rows``）：payload 是读取视图，``status`` /
        ``task_id`` 列才是 CAS 与唯一索引的判定所在，也是崩溃留下的痕迹。恢复动作前
        先确认 payload 不是终结态（幂等：第二遍 PLANNING/APPROVED 已不在 → 零动作）。
        """
        moment = self._now(now)
        try:
            expired = await self.expire_due(now=moment)
            rows = await self.store.state_rows(limit=50)
            orphans = 0
            open_count = 0
            for row in rows:
                plan_id = str(row.get("plan_id") or "")
                col_status = str(row.get("status") or "")
                col_task = str(row.get("task_id") or "")
                # payload 视图（可能滞后于列：半写态就是这种）
                plan = await self.store.get(plan_id)
                if plan is None:
                    # payload 读不出来 → 不做无信息的破坏性补偿（不猜）
                    continue
                if plan.open:
                    open_count += 1
                if plan.terminal or plan.status == PlanStatus.LINKED.value:
                    continue  # 终态 / 已关联的计划不碰（重启恢复也不覆盖）
                if col_status == PlanStatus.PLANNING.value and not col_task:
                    settled = await self.store.update_status(
                        plan_id,
                        PlanStatus.CANCELLED,
                        reason="orphan_planning_recovered",
                        now=moment,
                    )
                    kind = "reserved_plan"
                    reason = "orphan_planning_recovered"
                elif col_status == PlanStatus.APPROVED.value:
                    # 7D.2 P1-2：批准占用未完成（占用成功但任务还没建/还没关联）。
                    # APPROVED 没有到 CANCELLED 的状态机出口 —— 用 replace_plan 整行落账
                    # （补偿/修复不是状态机转移，原因必须写清），并验证任务确实不存在。
                    settled = await self._recover_orphan_approval(
                        plan, column_task_id=col_task, now=moment
                    )
                    repaired = settled is not None and settled.status == PlanStatus.LINKED.value
                    kind = "half_written_link" if repaired else "approved_occupation"
                    reason = (
                        "half_written_link_repaired" if repaired else "orphan_approval_recovered"
                    )
                else:
                    continue
                if settled is not None:
                    orphans += 1
                    await self.store.log_event(
                        plan_id=plan_id,
                        event="agentplan.compensated",
                        reason=reason,
                        at=moment,
                        detail={"kind": kind, "task_id": col_task},
                    )
        except Exception as exc:  # noqa: BLE001 - 恢复失败只降级
            self.degraded_reason = f"{type(exc).__name__}"
            return {"action": "degraded", "expired": 0, "open": 0, "reason": "error"}
        return {
            "action": "ok",
            "expired": len(expired),
            "orphans": orphans,
            "open": open_count,
            "recent": len(rows),
            "reason": "",
        }

    async def _recover_orphan_approval(
        self, plan: AgentPlan, *, column_task_id: str, now: float
    ) -> AgentPlan | None:
        """APPROVED 无 task_id 的恢复补偿（7D.2 P1-2 §2.2.5）。

        ``column_task_id`` 是**列**里的 task_id（payload 可能没写）。有 task_id 时先查
        任务是否真实存在（读任务失败 → 保守返回 None，不补偿）：存在 → 半写关联，修复成
        LINKED；不存在/无 task_id → 真孤儿，补偿 CANCELLED。
        """
        task_id = str(column_task_id or plan.task_id or "")
        if task_id and self.runtime is not None:
            getter = getattr(self.runtime, "get", None)
            if callable(getter):
                try:
                    record = await getter(task_id)
                except Exception:  # noqa: BLE001 - 读不到任务 → 保守当作存在，不补偿
                    return None
                if record is not None:
                    # 半写状态：任务确实建了，只是关联的 payload 没落上 → 修复关联，
                    # 不取消（任务可能正等着用户确认）。
                    payload = plan.to_payload()
                    payload["task_id"] = str(getattr(record, "task_id", "") or task_id)
                    payload["status"] = PlanStatus.LINKED.value
                    payload["updated_at"] = float(now)
                    return await self.store.replace_plan(AgentPlan.from_payload(payload))
        payload = plan.to_payload()
        payload["status"] = PlanStatus.CANCELLED.value
        payload["reason"] = "orphan_approval_recovered"
        payload["updated_at"] = float(now)
        return await self.store.replace_plan(AgentPlan.from_payload(payload))

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

    async def _create(self, plan: AgentPlan, *, now: float) -> tuple[AgentPlan, bool]:
        """落盘（原子占用）；返回 ``(计划, 是否新建)`` —— 合并 = 拿到的是已有记录。"""
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
        return stored, created

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
