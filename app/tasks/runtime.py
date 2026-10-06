"""TaskRuntime —— 通用多步骤任务编排（Phase 5A）。

它只认识：**工具 / 参数 / 风险 / 授权 / action_id / 结果 / 任务状态**。
它永远不知道：挖方块的包、Pathfinder 内部、Mineflayer 对象、容器窗口（§一百零二）。

架构边界（§四/§五）：LLM → **TaskRuntime** → Agent Bridge / Tool Loop → Policy →
Confirmation → Service → ActionRuntime → Mineflayer。TaskRuntime **绝不**直接碰 Mineflayer
或 runtime HTTP —— 所有实际动作都通过注入的 ``invoke`` 回调走已有工具链路。

核心保证：

* **状态机是显式的**（``ALLOWED_TASK_TRANSITIONS``）：非法转移直接拒绝；
* **计划冻结**：用户确认的是 ``plan_hash`` + 每一步的**模板参数**指纹；任何改动 →
  授权失效（``AUTHORIZATION``）而不是偷偷执行；
* **绝不用 TASK 冒充 USER**：MEDIUM/LOW 步骤的放行来自已确认的步骤授权，
  而不是伪造 ``TurnOrigin.USER``；
* **异步动作靠 action_id 绑定恢复**：只认 ``task_id + step_id + action_id`` 三者同时匹配的
  事件，别的任务的完成事件绝不会唤醒本任务（§二十四/§二十五）；
* **世界修改动作不自动重试**（§二十二/§五十八）：SAFE 最多 2 次并记账，LOW/MEDIUM 0 次；
* **不提供 rollback**（§三十五）：Minecraft 世界修改不是事务，失败只能 停/暂停/重规划/用户介入。
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.tasks.models import (
    ALLOWED_TASK_TRANSITIONS,
    TASK_CANCELLED,
    TASK_CONFIRMATION_REQUIRED,
    TASK_CREATED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_PLAN_READY,
    TASK_REPLANNING,
    TASK_RESUMED,
    TASK_STARTED,
    TASK_STEP_FAILED,
    TASK_STEP_STARTED,
    TASK_STEP_SUCCEEDED,
    TASK_STEP_WAITING,
    TASK_SUCCEEDED,
    ExpectedFinalState,
    StepState,
    TaskAuthorization,
    TaskFailure,
    TaskPlan,
    TaskRecord,
    TaskState,
    TaskStep,
    arguments_hash,
    new_task_id,
)
from app.tasks.validation import (
    MAX_ACTION_STEPS,
    MAX_STEPS,
    resolve_arguments,
    summarize_plan,
    validate_plan,
)

SAFE_RISKS = frozenset({"SAFE"})


@dataclass
class TaskConfig:
    """第一期的保守上限（§七十/§九十七：只有这几个旋钮）。"""

    ttl_seconds: float = 600.0
    max_steps: int = MAX_STEPS
    max_action_steps: int = MAX_ACTION_STEPS
    max_replans: int = 2
    no_progress_limit: int = 3
    safe_retries: int = 2


@dataclass
class TaskInvocation:
    """一次工具调用的结果（编排层只认识这个形状）。"""

    ok: bool = False
    status: str = ""
    action_id: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    summary: str = ""
    error: str = ""
    code: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def detached(self) -> bool:
        """持续型动作：启动即返回，终态要等 action 事件。"""
        return bool(self.action_id) and self.status.upper() == "RUNNING"


class PlanConfirmation(Protocol):
    """计划确认门（生产实现复用 Minecraft 的 ConfirmationStore，§四十）。"""

    async def request(
        self,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        risk: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        summary: str,
    ) -> str: ...

    async def consume(
        self,
        confirmation_id: str,
        *,
        task_id: str,
        session_id: str,
        user_id: str,
        plan_hash: str,
        arguments: Mapping[str, Any],
        origin: str,
    ) -> tuple[bool, str]: ...

    async def cancel(self, confirmation_id: str) -> None: ...


class TaskBusy(RuntimeError):
    """同一 session 已经有前台 Task（§二十六）。"""

    code = "task.busy"

    def __init__(self, existing: TaskRecord) -> None:
        RuntimeError.__init__(self, "task.busy")
        self.existing = existing


class TaskAuthorizationError(RuntimeError):
    """计划/步骤授权不匹配（§十二/§十七/§十八）。"""

    def __init__(self, message: str, *, code: str = "task.authorization_mismatch") -> None:
        super().__init__(message)
        self.code = code


#: 工具/动作错误码 → 任务失败分类（§三十三）
FAILURE_BY_CODE: dict[str, TaskFailure] = {
    "action.busy": TaskFailure.BUSY,
    "minecraft.action_busy": TaskFailure.BUSY,
    "task.busy": TaskFailure.BUSY,
    "action.not_online": TaskFailure.OFFLINE,
    "minecraft.offline": TaskFailure.OFFLINE,
    "minecraft.not_connected": TaskFailure.OFFLINE,
    "minecraft.disabled": TaskFailure.OFFLINE,
    "minecraft.runtime_down": TaskFailure.OFFLINE,
    "minecraft.task_authorization_missing": TaskFailure.AUTHORIZATION,
    "minecraft.task_authorization_mismatch": TaskFailure.AUTHORIZATION,
    "minecraft.action_not_allowed": TaskFailure.AUTHORIZATION,
    "minecraft.confirmation_required": TaskFailure.AUTHORIZATION,
    "minecraft.confirmation_expired": TaskFailure.AUTHORIZATION,
    "minecraft.confirmation_mismatch": TaskFailure.AUTHORIZATION,
    "block.not_found": TaskFailure.TARGET_LOST,
    "minecraft.block_not_found": TaskFailure.TARGET_LOST,
    "minecraft.block_unavailable": TaskFailure.TARGET_LOST,
    "item_entity.not_found": TaskFailure.TARGET_LOST,
    "minecraft.item_entity_not_found": TaskFailure.TARGET_LOST,
    "player.not_found": TaskFailure.TARGET_LOST,
    "minecraft.player_not_found": TaskFailure.TARGET_LOST,
    "player.lost": TaskFailure.TARGET_LOST,
    "minecraft.player_lost": TaskFailure.TARGET_LOST,
    "pickup.target_lost": TaskFailure.TARGET_LOST,
    "minecraft.pickup_target_lost": TaskFailure.TARGET_LOST,
    "block.changed": TaskFailure.WORLD_CHANGED,
    "minecraft.block_changed": TaskFailure.WORLD_CHANGED,
    "item_entity.changed": TaskFailure.WORLD_CHANGED,
    "minecraft.item_entity_changed": TaskFailure.WORLD_CHANGED,
    "pickup.target_too_far": TaskFailure.WORLD_CHANGED,
    "minecraft.pickup_target_too_far": TaskFailure.WORLD_CHANGED,
    "action.invalid": TaskFailure.VALIDATION,
    "minecraft.action_invalid": TaskFailure.VALIDATION,
    "recipe.not_found": TaskFailure.VALIDATION,
    "material.insufficient": TaskFailure.VALIDATION,
}


def classify_failure(code: str, *, event: str = "") -> TaskFailure:
    """把错误码/事件映射成失败分类（不认识的一律 ``ACTION_FAILED``，绝不猜）。"""
    key = str(code or "")
    if key in FAILURE_BY_CODE:
        return FAILURE_BY_CODE[key]
    if event == "minecraft.action.timeout":
        return TaskFailure.TIMEOUT
    if event == "minecraft.action.cancelled":
        return TaskFailure.CANCELLED
    if event == "minecraft.action.failed":
        return TaskFailure.ACTION_FAILED
    return TaskFailure.ACTION_FAILED


class TaskRuntime:
    """多步骤任务的生命期管理（状态 / 调度 / 等待 / 恢复 / 取消 / 校验）。"""

    def __init__(
        self,
        *,
        store: Any,
        invoke: Callable[..., Awaitable[TaskInvocation]],
        confirmations: PlanConfirmation,
        config: TaskConfig | None = None,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        clock: Callable[[], float] = time.time,
        risk_of: Callable[[str], str] | None = None,
        is_registered: Callable[[str], bool] | None = None,
        schema_of: Callable[[str], Mapping[str, Any] | None] | None = None,
        validate_arguments: (
            Callable[[Mapping[str, Any], Mapping[str, Any]], list[str]] | None
        ) = None,
        world_facts: Callable[[], Awaitable[dict[str, Any]]] | None = None,
        label_of: Callable[[TaskStep], str] | None = None,
        logger: Any = None,
    ) -> None:
        self._store = store
        self._invoke = invoke
        self._confirmations = confirmations
        self.config = config or TaskConfig()
        self._publish_fn = publish or (lambda _event, _payload: None)
        self._clock = clock
        self._risk_of = risk_of or (lambda _tool: "")
        self._is_registered = is_registered or (lambda _tool: False)
        self._schema_of = schema_of or (lambda _tool: None)
        self._validate_arguments = validate_arguments
        self._world_facts = world_facts
        self._label_of = label_of
        self._log = logger

    # ------------------------------------------------------------ 步骤授权

    async def authorize_step(
        self,
        *,
        task_id: str,
        step_id: str,
        tool: str,
        arguments: Mapping[str, Any],
        plan_hash: str,
    ) -> bool:
        """bridge 的步骤授权校验（§十七/§十八）：**fail-closed**。

        放行的条件（缺一不可）：
        1. 任务存在、未进终态；
        2. 计划授权存在且没过期；
        3. 传来的 ``plan_hash`` 与当前冻结计划一致；
        4. ``step_id`` 在计划里、``tool`` 一致；
        5. 将要执行的参数**恰好等于**"冻结计划 + 已观察到的结果"确定性地解析出来的那份
           （带引用的步骤也走这条 —— 参数只允许沿着声明过的路径解析，绝不静默换目标）。

        这是 TaskRuntime 唯一对外的"放行"入口；WebUI / SYSTEM 回合永远拿不到 TASK 事实，
        因此**无法自己造一条任务授权**（§八十七 case 6）。
        """
        record = await self._store.load(str(task_id))
        if record is None or record.state.terminal:
            return False
        if record.authorization is None or not record.authorization.valid(now=self._clock()):
            return False
        # 确认时的那份计划 = 现在这份计划（否则说明计划被改过 → 授权作废）
        if record.authorization.plan_hash != record.plan_hash:
            return False
        if record.plan_hash != str(plan_hash):
            return False
        step = record.step(str(step_id))
        if step is None or step.tool != str(tool):
            return False
        try:
            expected = resolve_arguments(step, self._results_of(record))
        except (KeyError, IndexError, ValueError, TypeError):
            return False
        return arguments_hash(expected) == arguments_hash(arguments)

    # ------------------------------------------------------------ 读

    async def get(self, task_id: str) -> TaskRecord | None:
        return await self._store.load(task_id)

    async def active(self, session_id: str) -> TaskRecord | None:
        return await self._store.active(session_id)

    async def current(self, session_id: str | None = None) -> TaskRecord | None:
        """当前的活动任务（WebUI/聊天用）：给了会话就按会话找，没给就找最近那个没结束的。

        第一期一个会话同时只有一个前台任务（§二十六），所以"最近的非终态"不会有歧义。
        """
        if session_id:
            return await self._store.active(session_id)
        for record in await self._store.list_recent(20):
            if not record.state.terminal:
                return record
        return None

    async def checkpoints(self, task_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return await self._store.checkpoints(task_id, limit)

    # ------------------------------------------------------------ 生命周期

    async def create_task(
        self,
        objective: str,
        *,
        session_id: str,
        user_id: str,
        origin: str,
        plan: TaskPlan,
        observations: list[dict[str, Any]] | None = None,
    ) -> TaskRecord:
        """创建一个 Task（PLANNING → 校验 → PENDING_CONFIRMATION + 请求计划确认）。

        同一 session 只允许一个前台 Task（§二十六）：已有就抛 :class:`TaskBusy`。
        """
        existing = await self._store.active(session_id)
        if existing is not None:
            raise TaskBusy(existing)
        if observations:
            plan.observations = [dict(item) for item in observations]
        record = TaskRecord(
            task_id=new_task_id(),
            session_id=session_id,
            user_id=user_id,
            origin=str(origin),
            objective=objective,
            plan=plan,
            state=TaskState.PLANNING,
            expires_at=self._clock() + float(self.config.ttl_seconds),
        )
        await self._save(record, event=TASK_CREATED)
        problems = self._validate_plan(record.plan)
        if problems:
            record.state = TaskState.FAILED
            record.failure = TaskFailure.VALIDATION.value
            record.message = "；".join(problems)[:400]
            await self._save(record, event=TASK_FAILED)
            return record
        await self._publish(TASK_PLAN_READY, self._event_payload(record))
        record.state = TaskState.PENDING_CONFIRMATION
        record.confirmation_id = await self._confirmations.request(
            task_id=record.task_id,
            session_id=session_id,
            user_id=user_id,
            risk=self._plan_risk(record.plan),
            plan_hash=record.plan_hash,
            arguments=self._confirmation_arguments(record),
            summary=self.summary_of(record),
        )
        await self._save(record, event=TASK_CONFIRMATION_REQUIRED)
        return record

    async def confirm_and_start(
        self, task_id: str, *, user_id: str, session_id: str, origin: str
    ) -> TaskRecord:
        """**只有真正的用户回合**能确认并启动。

        §八十七：INITIATIVE / BACKGROUND / SYSTEM / WebUI 一律不能消费计划授权。
        """
        record = await self._require(task_id)
        if record.state is not TaskState.PENDING_CONFIRMATION:
            raise TaskAuthorizationError(
                f"任务当前状态是 {record.state.value}，不能确认",
                code="task.not_awaiting_confirmation",
            )
        if not self._is_user_turn(origin):
            raise TaskAuthorizationError(
                "计划确认必须来自真实的用户回合", code="task.confirmation_not_user_turn"
            )
        if str(user_id) != record.user_id or str(session_id) != record.session_id:
            raise TaskAuthorizationError(
                "确认必须来自发起这个任务的人与会话", code="task.confirmation_wrong_owner"
            )
        ok, code = await self._confirmations.consume(
            record.confirmation_id,
            task_id=record.task_id,
            session_id=session_id,
            user_id=user_id,
            plan_hash=record.plan_hash,
            arguments=self._confirmation_arguments(record),
            origin=origin,
        )
        if not ok:
            record.message = code
            if code in {"minecraft.confirmation_expired", "minecraft.confirmation_mismatch"}:
                # §六十二：过期/不匹配 → 重新挂一条，等用户再确认一次（绝不偷偷继续）
                record.confirmation_id = await self._confirmations.request(
                    task_id=record.task_id,
                    session_id=session_id,
                    user_id=user_id,
                    risk=self._plan_risk(record.plan),
                    plan_hash=record.plan_hash,
                    arguments=self._confirmation_arguments(record),
                    summary=self.summary_of(record),
                )
                await self._save(record, event=TASK_CONFIRMATION_REQUIRED)
            return record
        now = self._clock()
        record.authorization = TaskAuthorization(
            task_id=record.task_id,
            user_id=user_id,
            session_id=session_id,
            plan_hash=record.plan_hash,
            approved_at=now,
            expires_at=now + float(self.config.ttl_seconds),
        )
        record.expires_at = record.authorization.expires_at
        await self._enter(record, TaskState.RUNNING)
        await self._publish(TASK_STARTED, self._event_payload(record))
        record.result["started_inventory"] = await self._inventory_snapshot()
        await self._save(record, event=TASK_STARTED)
        return await self.drive(task_id)

    async def drive(self, task_id: str) -> TaskRecord:
        """推进任务：执行下一个步骤，直到需要等待动作 / 结束。"""
        record = await self._require(task_id)
        guard = 0
        while True:
            guard += 1
            if guard > self.config.max_steps + 2:
                return await self._fail(record, TaskFailure.INTERNAL, "任务推进超出内部步数上限")
            if record.state is TaskState.PAUSED:
                # 暂停中的任务绝不再往下走一步（resume 会先把它切回 RUNNING）
                return record
            if record.state.terminal or record.state is TaskState.PENDING_CONFIRMATION:
                return record
            if record.state is TaskState.WAITING_ACTION:
                return record
            if await self._expire_if_due(record):
                return record
            step = record.next_step()
            if step is None:
                return await self._finish(record)
            if record.state is not TaskState.RUNNING:
                await self._enter(record, TaskState.RUNNING)
            outcome = await self._run_step(record, step)
            if outcome == "wait":
                return record
            if record.state.terminal or record.state is TaskState.PAUSED:
                return record

    # ------------------------------------------------------------ 步骤执行

    async def _run_step(self, record: TaskRecord, step: TaskStep) -> str:
        risk = step.risk or self._risk_of(step.tool)
        step.risk = risk
        # 1) 解析引用（§四十七）：取不到就是"目标丢了"，绝不静默换目标
        try:
            resolved = resolve_arguments(step, self._results_of(record))
        except (KeyError, IndexError, ValueError, TypeError) as exc:
            step.state = StepState.FAILED
            step.failure = TaskFailure.TARGET_LOST.value
            step.message = f"参数引用的结果取不到：{exc}"
            await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
            await self._pause_after_failure(record, TaskFailure.TARGET_LOST, step)
            return "stop"
        step.resolved_arguments = resolved
        problems = self._validate_resolved(step)
        if problems:
            step.state = StepState.FAILED
            step.failure = TaskFailure.VALIDATION.value
            step.message = "；".join(problems)[:400]
            await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
            await self._fail(record, TaskFailure.VALIDATION, step.message, step=step)
            return "stop"

        # 2) 步骤授权必须与冻结计划完全一致（§十七/§十八）
        grant = record.step_authorization(step)
        if step.tool != grant.tool or step.template_hash != grant.arguments_hash:
            await self._fail(
                record,
                TaskFailure.AUTHORIZATION,
                f"步骤 {step.step_id} 的参数与已确认的计划不一致",
                step=step,
            )
            return "stop"
        if record.authorization is not None and grant.plan_hash != record.plan_hash:
            # 计划在用户确认之后被改过（某一步的参数/工具/顺序被动过）：
            # 冻结计划一旦确认就不可静默修改（§十二/§十三）——包括 SAFE 步骤，
            # 因为计划是一整份被确认的东西。
            await self._fail(
                record,
                TaskFailure.AUTHORIZATION,
                "计划在确认之后被改动过（plan_hash 对不上），需要重新确认",
                step=step,
            )
            return "stop"
        expired = risk not in SAFE_RISKS and (
            record.authorization is None or not record.authorization.valid(now=self._clock())
        )
        if expired:
            record.state = TaskState.PAUSED
            record.failure = TaskFailure.AUTHORIZATION.value
            record.message = "任务授权已过期，需要重新确认"
            await self._save(record, event=TASK_PAUSED, step_id=step.step_id)
            return "stop"

        # 3) no-progress 检测（§七十二/§七十三）
        progress_key = f"{step.tool}:{step.template_hash}"
        if progress_key == record.last_progress_key:
            record.no_progress += 1
        else:
            record.last_progress_key = progress_key
            record.no_progress = 0
        if record.no_progress >= self.config.no_progress_limit:
            record.state = TaskState.PAUSED
            record.failure = TaskFailure.STALLED.value
            record.message = f"连续 {record.no_progress} 次没有进展（{step.tool}），先停下"
            await self._save(record, event=TASK_PAUSED, step_id=step.step_id)
            return "stop"

        # 4) 执行（SAFE 可以有限重试；LOW/MEDIUM 绝不自动重试）
        attempts_allowed = self.config.safe_retries + 1 if risk in SAFE_RISKS else 1
        step.state = StepState.RUNNING
        step.started_at = self._clock()
        # 重新开始这一步（恢复/重试）时清掉上一次的失败痕迹：否则一次成功/取消的步骤
        # 会带着旧的 failure=CANCELLED 留在结果里（审计会误读）。
        step.failure = ""
        step.error = ""
        step.message = ""
        await self._publish(TASK_STEP_STARTED, self._event_payload(record, step))
        invocation = TaskInvocation()
        # 注意：attempts 只统计**这一次执行**里的重试（恢复/重跑一个被取消的步骤时重新开始计数），
        # 否则一个曾经跑过一次的步骤会被"历史 attempts"卡死、永远不再真正调用工具。
        attempt = 0
        while attempt < attempts_allowed:
            attempt += 1
            step.attempts = attempt
            invocation = await self._invoke_step(record, step, grant)
            if invocation.ok:
                break
            if risk not in SAFE_RISKS or not self._retryable(invocation):
                break
            if attempt < attempts_allowed:
                self._log_info(
                    "task step retry",
                    task_id=record.task_id,
                    step=step.step_id,
                    tool=step.tool,
                    attempt=attempt,
                    reason=invocation.code or invocation.error,
                )
        step.finished_at = self._clock()
        return await self._settle(record, step, invocation)

    async def _settle(self, record: TaskRecord, step: TaskStep, invocation: TaskInvocation) -> str:
        if invocation.detached:
            step.state = StepState.WAITING_ACTION
            step.action_id = invocation.action_id
            step.status = invocation.status
            step.summary = invocation.summary or step.summary
            record.pending_action_id = invocation.action_id
            record.pending_step_id = step.step_id
            await self._enter(record, TaskState.WAITING_ACTION)
            await self._publish(TASK_STEP_WAITING, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_WAITING, step_id=step.step_id)
            return "wait"

        if invocation.ok:
            step.state = StepState.SUCCEEDED
            step.status = invocation.status or "SUCCEEDED"
            step.result = dict(invocation.result)
            step.summary = invocation.summary or step.summary
            await self._publish(TASK_STEP_SUCCEEDED, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_SUCCEEDED, step_id=step.step_id)
            return "continue"

        step.state = StepState.FAILED
        step.status = invocation.status or "FAILED"
        step.error = invocation.error
        failure = classify_failure(invocation.code, event=invocation.status)
        step.failure = failure.value
        step.message = invocation.error or invocation.code
        await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
        await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
        await self._pause_after_failure(record, failure, step)
        return "stop"

    def _validate_resolved(self, step: TaskStep) -> list[str]:
        """解析出最终参数之后再按 schema 校验一次（引用也要过这一关）。"""
        if self._validate_arguments is None:
            return []
        schema = self._schema_of(step.tool)
        if not schema:
            return []
        return [
            f"{step.tool} 的参数不合 schema（{issue}）"
            for issue in self._validate_arguments(schema, dict(step.effective_arguments))
        ]

    async def _invoke_step(self, record: TaskRecord, step: TaskStep, grant: Any) -> TaskInvocation:
        arguments = step.effective_arguments
        try:
            return await self._invoke(
                step.tool,
                arguments,
                task_id=record.task_id,
                step_id=step.step_id,
                plan_hash=record.plan_hash,
                risk=step.risk,
                authorization=grant,
            )
        except Exception as exc:  # noqa: BLE001 - 编排层绝不把异常抛给调用方
            if self._log is not None:
                self._log.exception("[Task] step invocation crashed tool=%s", step.tool)
            return TaskInvocation(
                ok=False, error=f"调用 {step.tool} 失败：{exc}", code="task.internal"
            )

    def _retryable(self, invocation: TaskInvocation) -> bool:
        """SAFE 查询的**瞬态**失败可以重试（§二十三）。"""
        retryable = {TaskFailure.OFFLINE, TaskFailure.BUSY, TaskFailure.INTERNAL}
        return classify_failure(invocation.code) in retryable

    async def _pause_after_failure(
        self, record: TaskRecord, failure: TaskFailure, step: TaskStep
    ) -> None:
        """LOW/MEDIUM 失败 → PAUSED（不自动做新的世界修改，§三十四）。"""
        if failure is TaskFailure.CANCELLED:
            await self._cancel_record(record, reason="action cancelled")
            return
        record.state = TaskState.PAUSED
        record.failure = failure.value
        record.failed_step = step.step_id
        record.message = step.message[:400]
        await self._save(record, event=TASK_PAUSED, step_id=step.step_id)

    # ------------------------------------------------------------ action 事件

    async def on_action_event(
        self,
        *,
        action_id: str,
        event: str,
        status: str = "",
        result: Mapping[str, Any] | None = None,
        error: str = "",
        code: str = "",
    ) -> TaskRecord | None:
        """异步动作的终态 → 恢复对应 Task 的对应 step（严格绑 action_id，§二十四/§二十五）。"""
        if not action_id:
            return None
        records = await self._store.list_recent(50)
        target = next(
            (item for item in records if item.pending_action_id == str(action_id)),
            None,
        )
        if target is None:
            return None
        step = target.step(target.pending_step_id) if target.pending_step_id else None
        if step is None:
            return None
        terminal = str(event).rsplit(".", 1)[-1]
        step.finished_at = self._clock()
        step.status = status or terminal.upper()
        step.result = dict(result or {})
        target.pending_action_id = ""
        target.pending_step_id = ""
        if terminal == "completed":
            step.state = StepState.SUCCEEDED
            await self._publish(TASK_STEP_SUCCEEDED, self._event_payload(target, step))
            await self._save(target, event=TASK_STEP_SUCCEEDED, step_id=step.step_id)
        else:
            failure = classify_failure(code, event=str(event))
            step.state = StepState.CANCELLED if terminal == "cancelled" else StepState.FAILED
            step.failure = failure.value
            step.error = error
            step.message = error or code
            await self._publish(TASK_STEP_FAILED, self._event_payload(target, step))
            await self._save(target, event=TASK_STEP_FAILED, step_id=step.step_id)
            if target.pause_requested:
                target.pause_requested = False
                await self._enter(target, TaskState.PAUSED)
                await self._save(target, event=TASK_PAUSED, step_id=step.step_id)
                return target
            await self._pause_after_failure(target, failure, step)
            return target
        if target.pause_requested:
            # §二十九：暂停等当前动作**自然结束**，绝不在动作中间硬切状态
            target.pause_requested = False
            await self._enter(target, TaskState.PAUSED)
            await self._save(target, event=TASK_PAUSED, step_id=step.step_id)
            return target
        # 动作结束了：先把状态切回 RUNNING **并落盘**再继续下一步。
        # drive() 会从存储重新 load（那是 checkpoint 的真身），只改内存对象不落盘的话
        # 它会读到"还在 WAITING_ACTION"的旧快照，然后原地返回、任务永远停在等动作。
        await self._enter(target, TaskState.RUNNING)
        await self._save(target)
        return await self.drive(target.task_id)

    # ------------------------------------------------------------ 暂停/恢复/取消/过期

    async def pause(
        self, task_id: str, *, reason: str = "user", stop_action: bool = True
    ) -> TaskRecord:
        """暂停（§二十九）：没有前台动作就立刻停；有就等它自然结束（或按需先 stop）。"""
        record = await self._require(task_id)
        if record.state.terminal:
            return record
        if record.pending_action_id:
            record.pause_requested = True
            record.message = f"等当前动作结束后暂停（{reason}）"
            if stop_action:
                await self._stop_action()
            await self._save(record, event=TASK_PAUSED)
            return record
        record.pause_requested = False
        await self._enter(record, TaskState.PAUSED)
        record.message = f"已暂停（{reason}）"
        await self._save(record, event=TASK_PAUSED)
        return record

    async def resume(
        self,
        task_id: str,
        *,
        user_id: str,
        session_id: str,
        origin: str,
        non_user_ok: bool = False,
    ) -> TaskRecord:
        """恢复（§三十/§六十四）：重新校验授权时效与世界事实，绝不直接继续旧动作。

        恢复**不是**授权：它不会签出任何新的世界操作许可，只是把一份仍然有效的授权
        接着用完。因此：

        * ``origin`` 是真实用户回合 → 完整恢复；授权过期时重新挂确认（等用户再说一次）。
        * ``non_user_ok=True``（WebUI 的「继续」按钮）→ 只允许**仍在有效期内**的授权继续；
          过期时如实拒绝（``task.resume_requires_user``），绝不替用户重新授权（§八十七 6）。
        """
        is_user = self._is_user_turn(origin)
        if not is_user and not non_user_ok:
            raise TaskAuthorizationError(
                "恢复任务必须来自用户回合", code="task.resume_not_user_turn"
            )
        record = await self._require(task_id)
        if record.state is TaskState.PENDING_CONFIRMATION:
            return record
        if record.state.terminal:
            raise TaskAuthorizationError(
                f"任务已经结束（{record.state.value}）", code="task.already_finished"
            )
        if record.authorization is None or not record.authorization.valid(now=self._clock()):
            if not is_user:
                # WebUI/后台只能"继续"一份还有效的授权，不能自己造一份新的
                raise TaskAuthorizationError(
                    "任务授权已过期，需要在对话里由用户重新确认",
                    code="task.resume_requires_user",
                )
            # §三十：MEDIUM 授权过期 → 重新确认（不偷偷继续）
            record.authorization = None
            await self._enter(record, TaskState.PENDING_CONFIRMATION)
            record.confirmation_id = await self._confirmations.request(
                task_id=record.task_id,
                session_id=record.session_id,
                user_id=record.user_id,
                risk=self._plan_risk(record.plan),
                plan_hash=record.plan_hash,
                arguments=self._confirmation_arguments(record),
                summary=self.summary_of(record),
            )
            record.message = "授权已过期，请重新确认计划"
            await self._save(record, event=TASK_CONFIRMATION_REQUIRED)
            return record
        facts = await self._world_facts_snapshot()
        if facts and facts.get("online") is False:
            record.message = "罐头不在世界里，先恢复不了"
            await self._save(record, event=TASK_PAUSED)
            return record
        step = record.next_step()
        if step is not None and step.action_id and step.state is StepState.WAITING_ACTION:
            # 被暂停时那个动作已经结束了：不能假装它还在（§六十四/§七十七）
            step.state = StepState.FAILED
            step.failure = TaskFailure.RUNTIME_RESTART.value
            step.message = "恢复时发现旧动作已经失效，需要重新规划"
            record.resume_note = "旧动作失效"
            await self._enter(record, TaskState.REPLANNING)
            await self._publish(TASK_REPLANNING, self._event_payload(record, step))
            await self._save(record, event=TASK_REPLANNING, step_id=step.step_id)
            return record
        record.pause_requested = False
        record.failure = ""
        await self._enter(record, TaskState.RUNNING)
        await self._publish(TASK_RESUMED, self._event_payload(record))
        await self._save(record, event=TASK_RESUMED)
        return await self.drive(task_id)

    async def cancel(self, task_id: str, *, reason: str = "user cancel") -> TaskRecord:
        """取消（§二十七/§二十八）：立刻终态 + 经已有 minecraft_stop 停掉前台动作。"""
        record = await self._require(task_id)
        if record.state.terminal:
            return record
        await self._stop_action()
        step = record.step(record.pending_step_id) if record.pending_step_id else None
        if step is not None and step.state is StepState.WAITING_ACTION:
            step.state = StepState.CANCELLED
            step.message = reason
        record.pending_action_id = ""
        record.pending_step_id = ""
        return await self._cancel_record(record, reason=reason)

    async def expire(self, task_id: str, *, reason: str = "task ttl") -> TaskRecord:
        record = await self._require(task_id)
        if record.state.terminal:
            return record
        await self._stop_action()
        record.message = reason
        record.state = TaskState.EXPIRED
        record.updated_at = self._clock()
        await self._save(record, event=TASK_EXPIRED)
        await self._publish(TASK_EXPIRED, self._event_payload(record))
        return record

    async def tick(self, now: float | None = None) -> list[TaskRecord]:
        """周期收尾：到点过期的任务（§三十一/§六十三）。"""
        moment = self._clock() if now is None else now
        expired: list[TaskRecord] = []
        for record in await self._store.list_recent(50):
            if record.state.terminal or record.expires_at <= 0:
                continue
            if moment >= record.expires_at:
                expired.append(await self.expire(record.task_id))
        return expired

    # ------------------------------------------------------------ 内部

    async def _finish(self, record: TaskRecord) -> TaskRecord:
        """所有 required 步骤完成后：用 SAFE 读**重新验证**最终状态（§八十二/§八十三）。"""
        expected = record.plan.expected_final_state
        verification: dict[str, Any] = {"checked": False}
        if not expected.empty:
            after = await self._inventory_snapshot()
            before = record.result.get("started_inventory")
            verification = self._verify_inventory(expected, before, after)
            if not verification.get("ok"):
                record.state = TaskState.FAILED
                record.failure = TaskFailure.VERIFICATION.value
                record.message = verification.get("message", "最终校验没通过")
                record.verification = verification
                await self._save(record, event=TASK_FAILED)
                await self._publish(TASK_FAILED, self._event_payload(record))
                return record
        record.verification = verification
        record.state = TaskState.SUCCEEDED
        record.updated_at = self._clock()
        record.result["completed_steps"] = record.progress()["completed"]
        record.result["summary"] = self._success_summary(record, verification)
        await self._save(record, event=TASK_SUCCEEDED)
        await self._publish(TASK_SUCCEEDED, self._event_payload(record))
        return record

    def _verify_inventory(
        self,
        expected: ExpectedFinalState,
        before: Any,
        after: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        counts_before = self._counts(before)
        counts_after = self._counts(after)
        delta: dict[str, int] = {}
        ok = True
        for name, minimum in expected.inventory_delta.items():
            gained = counts_after.get(name, 0) - counts_before.get(name, 0)
            delta[name] = gained
            if gained < minimum:
                ok = False
        return {
            "checked": True,
            "ok": ok,
            "inventory_delta": delta,
            "expected": dict(expected.inventory_delta),
            "message": "" if ok else f"背包最终状态与预期不符（{delta}）",
        }

    @staticmethod
    def _counts(snapshot: Any) -> dict[str, int]:
        if not isinstance(snapshot, Mapping):
            return {}
        items = snapshot.get("items")
        counts: dict[str, int] = {}
        if isinstance(items, list):
            for row in items:
                if isinstance(row, Mapping) and row.get("name"):
                    counts[str(row["name"])] = int(row.get("count") or 0)
        return counts

    async def _inventory_snapshot(self) -> dict[str, Any]:
        try:
            invocation = await self._invoke(
                "minecraft_inventory",
                {},
                task_id="",
                step_id="",
                plan_hash="",
                risk="SAFE",
                authorization=None,
            )
        except Exception:  # noqa: BLE001 - 读不到背包不该让编排层崩
            return {}
        if not invocation.ok:
            return {}
        result = invocation.result
        if isinstance(result, Mapping) and isinstance(result.get("result"), Mapping):
            result = result["result"]
        return dict(result) if isinstance(result, Mapping) else {}

    async def _stop_action(self) -> None:
        """经**已有** minecraft_stop 停掉前台动作（绝不直接操纵 Mineflayer，§二十七）。"""
        try:
            await self._invoke(
                "minecraft_stop",
                {},
                task_id="",
                step_id="",
                plan_hash="",
                risk="SAFE",
                authorization=None,
            )
        except Exception:  # noqa: BLE001 - 停止失败也不改变任务终态
            if self._log is not None:
                self._log.warning("[Task] minecraft_stop failed while cancelling")

    async def _expire_if_due(self, record: TaskRecord) -> bool:
        if record.expires_at and self._clock() >= record.expires_at:
            await self.expire(record.task_id)
            return True
        return False

    async def _cancel_record(self, record: TaskRecord, *, reason: str) -> TaskRecord:
        record.state = TaskState.CANCELLED
        record.message = reason
        record.updated_at = self._clock()
        await self._save(record, event=TASK_CANCELLED)
        await self._publish(TASK_CANCELLED, self._event_payload(record))
        return record

    async def _fail(
        self,
        record: TaskRecord,
        failure: TaskFailure,
        message: str,
        *,
        step: TaskStep | None = None,
    ) -> TaskRecord:
        record.state = TaskState.FAILED
        record.failure = failure.value
        record.message = message[:400]
        record.failed_step = step.step_id if step is not None else ""
        await self._save(record, event=TASK_FAILED, step_id=record.failed_step)
        await self._publish(TASK_FAILED, self._event_payload(record, step))
        return record

    async def _enter(self, record: TaskRecord, state: TaskState) -> None:
        """显式状态机：非法转移直接拒绝（不"看起来对"就放行）。"""
        if state is record.state:
            return
        allowed = ALLOWED_TASK_TRANSITIONS.get(record.state, frozenset())
        if state not in allowed:
            raise TaskAuthorizationError(
                f"非法状态转移：{record.state.value} → {state.value}",
                code="task.invalid_transition",
            )
        record.state = state
        record.updated_at = self._clock()

    async def _require(self, task_id: str) -> TaskRecord:
        record = await self._store.load(task_id)
        if record is None:
            raise TaskAuthorizationError(f"找不到任务 {task_id}", code="task.not_found")
        return record

    async def _save(self, record: TaskRecord, *, event: str = "", step_id: str = "") -> None:
        record.updated_at = self._clock()
        await self._store.save(record, event=event, step_id=step_id)

    async def _publish(self, event: str, payload: dict[str, Any]) -> None:
        try:
            self._publish_fn(event, payload)
        except Exception:  # noqa: BLE001 - 事件总线出问题不该让任务挂掉
            if self._log is not None:
                self._log.warning("[Task] publish failed event=%s", event)

    def _log_info(self, message: str, **fields: Any) -> None:
        if self._log is not None:
            self._log.info("[Task] %s %s", message, fields)

    def _results_of(self, record: TaskRecord) -> dict[str, dict[str, Any]]:
        results: dict[str, dict[str, Any]] = {}
        for step in record.steps:
            if step.state is StepState.SUCCEEDED:
                results[step.step_id] = dict(step.result)
        return results

    def _confirmation_arguments(self, record: TaskRecord) -> dict[str, Any]:
        """确认指纹覆盖的东西：整份冻结计划（工具 + 最终参数 + 期望终态）。"""
        return {"plan_hash": record.plan_hash, "plan": record.plan.hash_payload()}

    def _plan_risk(self, plan: TaskPlan) -> str:
        order = ["SAFE", "LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"]
        risk = "SAFE"
        for step in plan.steps:
            value = step.risk or self._risk_of(step.tool)
            if order.index(value) > order.index(risk):
                risk = value
        return risk

    def summary_of(self, record: TaskRecord) -> str:
        return summarize_plan(record.plan, label_of=self._label_of)

    def _validate_plan(self, plan: TaskPlan) -> list[str]:
        return validate_plan(
            plan,
            risk_of=self._risk_of,
            is_registered=self._is_registered,
            schema_of=self._schema_of,
            validate_arguments=self._validate_arguments,
            online=True,
            max_steps=self.config.max_steps,
        )

    async def _world_facts_snapshot(self) -> dict[str, Any]:
        if self._world_facts is None:
            return {}
        try:
            return dict(await self._world_facts())
        except Exception:  # noqa: BLE001
            return {}

    @staticmethod
    def _is_user_turn(origin: Any) -> bool:
        value = getattr(origin, "value", origin)
        return str(value) == "user"

    def _fallback_summary(self, record: TaskRecord) -> str:
        progress = record.progress()
        if record.state is TaskState.SUCCEEDED:
            return str(record.result.get("summary") or "任务完成")
        if record.state is TaskState.PAUSED:
            return f"任务已暂停（{record.message or record.failure}）"
        if record.state.terminal:
            return f"任务结束：{record.state.value}（{record.message or record.failure}）"
        return f"任务进行中 {progress['completed']}/{progress['total']}"

    def snapshot_payload(self, record: TaskRecord) -> dict[str, Any]:
        """给 WebUI / API / LLM 的只读投影（绝不包含任何 raw 世界状态，§七十九）。"""
        progress = record.progress()
        step = record.step(record.pending_step_id) if record.pending_step_id else None
        current = step or record.next_step()
        return {
            "task_id": record.task_id,
            "session_id": record.session_id,
            "origin": record.origin,
            "objective": record.objective,
            "state": record.state.value,
            "progress": progress,
            "current_step": (
                {
                    "step_id": current.step_id,
                    "tool": current.tool,
                    "risk": current.risk,
                    "state": current.state.value,
                    "arguments": current.effective_arguments,
                }
                if current is not None
                else None
            ),
            "current_action": record.pending_action_id or None,
            "plan": {
                "plan_hash": record.plan_hash,
                "steps": [
                    {
                        "step_id": item.step_id,
                        "tool": item.tool,
                        "risk": item.risk,
                        "state": item.state.value,
                        "label": (self._label_of or (lambda s: s.tool))(item),
                    }
                    for item in record.plan.steps
                ],
                "expected_final_state": record.plan.expected_final_state.to_payload(),
            },
            "confirmation_required": record.state is TaskState.PENDING_CONFIRMATION,
            "confirmation_id": record.confirmation_id or None,
            "last_result": (
                {
                    "tool": current.tool,
                    "status": current.status,
                    "summary": current.summary,
                    "result": current.result,
                }
                if current is not None and current.status
                else None
            ),
            "failure": (
                {"reason": record.failure, "step": record.failed_step, "message": record.message}
                if record.failure
                else None
            ),
            "verification": record.verification,
            "result": record.result,
            "summary": self._fallback_summary(record),
            "replans": record.replans,
            "expires_at": record.expires_at,
            "rollback_supported": record.rollback_supported,
            "updated_at": record.updated_at,
        }

    def context_line(self, record: TaskRecord, *, limit: int = 400) -> str:
        """给模型的当前任务上下文（§八十一：240~400 字符，绝不塞整个 Task JSON）。"""
        progress = record.progress()
        remaining = [
            (self._label_of or (lambda s: s.tool))(item)
            for item in record.plan.steps
            if item.state is StepState.PENDING
        ]
        parts = [
            f"任务({record.task_id})：{record.state.value}",
            f"目标：{record.objective}",
            f"进度：{progress['completed']}/{progress['total']}",
        ]
        if record.message:
            parts.append(f"说明：{record.message}")
        if remaining:
            parts.append(f"还没做：{'；'.join(remaining[:3])}")
        line = " | ".join(parts)
        return line[:limit]

    def _success_summary(self, record: TaskRecord, verification: Mapping[str, Any]) -> str:
        delta = verification.get("inventory_delta") if isinstance(verification, Mapping) else None
        if isinstance(delta, Mapping) and delta:
            gained = "、".join(f"{name} ×{count}" for name, count in delta.items() if count)
            if gained:
                return f"任务完成：{gained}"
        completed = record.progress()["completed"]
        return f"任务完成（{completed} 个步骤）"

    async def replan(self, task_id: str, plan: TaskPlan, *, reason: str = "replan") -> TaskRecord:
        """重规划（§三十七）：只允许 SAFE 观察 + 新的计划，且**任何**新 LOW/MEDIUM 都要重新确认。"""
        record = await self._require(task_id)
        if record.state.terminal:
            raise TaskAuthorizationError("任务已经结束，不能重规划", code="task.already_finished")
        if record.replans >= self.config.max_replans:
            return await self._fail(
                record, TaskFailure.INTERNAL, f"重规划次数超过上限（{self.config.max_replans}）"
            )
        problems = self._validate_plan(plan)
        if problems:
            return await self._fail(record, TaskFailure.VALIDATION, "；".join(problems))
        record.replans += 1
        record.plan = plan
        record.current_step = 0
        record.authorization = None
        record.failure = ""
        record.pause_requested = False
        record.pending_action_id = ""
        record.pending_step_id = ""
        await self._enter(record, TaskState.REPLANNING)
        await self._publish(TASK_REPLANNING, self._event_payload(record))
        await self._save(record, event=TASK_REPLANNING)
        await self._enter(record, TaskState.PENDING_CONFIRMATION)
        record.confirmation_id = await self._confirmations.request(
            task_id=record.task_id,
            session_id=record.session_id,
            user_id=record.user_id,
            risk=self._plan_risk(record.plan),
            plan_hash=record.plan_hash,
            arguments=self._confirmation_arguments(record),
            summary=self.summary_of(record),
        )
        record.message = f"已重新规划（{reason}），等用户确认新计划"
        await self._save(record, event=TASK_CONFIRMATION_REQUIRED)
        return record

    def _event_payload(self, record: TaskRecord, step: TaskStep | None = None) -> dict[str, Any]:
        """Task 事件载荷（§五十一：task_id / step_id / state / timestamp，不含 raw 世界状态）。"""
        return {
            "task_id": record.task_id,
            "session_id": record.session_id,
            "step_id": step.step_id if step is not None else record.pending_step_id,
            "state": record.state.value,
            "step_state": step.state.value if step is not None else "",
            "tool": step.tool if step is not None else "",
            "failure": record.failure,
            "message": record.message,
            "timestamp": self._clock(),
        }


__all__ = [
    "FAILURE_BY_CODE",
    "PlanConfirmation",
    "TaskAuthorizationError",
    "TaskBusy",
    "TaskConfig",
    "TaskInvocation",
    "TaskRuntime",
    "classify_failure",
]
