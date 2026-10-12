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

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app.tasks.models import (
    ALLOWED_TASK_TRANSITIONS,
    TASK_AUTHORIZATION_EXPIRED,
    TASK_CANCELLED,
    TASK_CONFIRMATION_REQUIRED,
    TASK_CREATED,
    TASK_EXPIRED,
    TASK_FAILED,
    TASK_PAUSED,
    TASK_PLAN_READY,
    TASK_RECOVERED,
    TASK_REPLANNING,
    TASK_RESUMED,
    TASK_STARTED,
    TASK_STEP_FAILED,
    TASK_STEP_STARTED,
    TASK_STEP_SUCCEEDED,
    TASK_STEP_WAITING,
    TASK_SUCCEEDED,
    DispatchState,
    ExpectedFinalState,
    PlanStatus,
    ReconcileOutcome,
    ReplanReason,
    StepState,
    TaskAuthorization,
    TaskFailure,
    TaskPlan,
    TaskRecord,
    TaskState,
    TaskStep,
    arguments_hash,
    canonical_item_name,
    dispatch_key_of,
    finite_number,
    new_task_id,
)
from app.tasks.validation import (
    MAX_ACTION_STEPS,
    MAX_STEPS,
    resolve_arguments,
    summarize_plan,
    validate_plan,
)

log = logging.getLogger("CatooBot.Tasks")

SAFE_RISKS = frozenset({"SAFE"})

#: Phase 7F.1 §八十三：位置后置条件读的是**感知层**世界视图（near 层按固定间隔轮询一次，
#: 默认约 1s）。刚到达时缓存可能还没追上真实位置，因此对同一份 SAFE 世界读做**有界重试**
#: 等待收敛；到期仍在半径外就判失败（fail-closed）——既不放行"还没到"，也不因缓存延迟
#: 错杀真实到达。
POSITION_VERIFY_ATTEMPTS = 6
POSITION_VERIFY_INTERVAL_SECONDS = 0.6


@dataclass
class TaskConfig:
    """第一期的保守上限（§七十/§九十七：只有这几个旋钮）。"""

    ttl_seconds: float = 600.0
    max_steps: int = MAX_STEPS
    max_action_steps: int = MAX_ACTION_STEPS
    max_replans: int = 2
    no_progress_limit: int = 3
    safe_retries: int = 2
    #: 7F.1 §八十三：位置后置条件的**有界**等待预算（读感知层视图，容忍一个轮询周期）。
    position_verify_attempts: int = POSITION_VERIFY_ATTEMPTS
    position_verify_interval_seconds: float = POSITION_VERIFY_INTERVAL_SECONDS
    #: 5A.1 §十/§十一：一份计划授权的有效期（≠ 任务总时长）。生产上取
    #: minecraft.agent.confirmation.ttl_seconds；测试/真机 expiry 场景用极短值。
    authorization_ttl_seconds: float = 60.0
    #: Phase 7F.5.3 §二窗口 6：先到动作事件的有界暂存容量，以及"已结算 action_id"环的大小
    #: （两者都是有界内存结构：既不会无限增长，也不承诺进程崩溃后仍然保留）。
    max_deferred_action_events: int = 50
    max_settled_action_ids: int = 200
    #: Phase 7F.5.3 Follow-up 2 §三：每个任务被推迟到锁外的 SAFE stop 请求上限（有界队列）。
    max_pending_stop_requests: int = 8
    #: 锁内消费早到终态事件后需要"出锁后续跑"的任务上限（同样是进程内有界结构）。
    max_pending_drives: int = 16


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
    #: Phase 7F.4：**没有拿到 runtime 的答复**（传输异常/超时）—— 外部可能已经接受了
    #: 这次请求。这种失败绝不能被当成"动作没执行"，也不能被普通 ``resume()`` 重发。
    ambiguous: bool = False
    #: Phase 7F.5：**接受这次动作的 Runtime 实例**（由桥接随事件/状态给出）。
    #: 只用于关联唯一性，不是幂等键。
    runtime_instance: str = ""

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


def _reconcile(outcome: ReconcileOutcome, **detail: Any) -> dict[str, Any]:
    """对账结论 + 事实明细（只读；绝不包含"接下来要做什么"的决定）。"""
    return {"outcome": outcome.value, "detail": detail}


class _LifecycleLock:
    """按任务串行化「生命周期转换 ↔ 结算落盘」（Phase 7F.5.3 Follow-up §三）。

    为什么需要它：`cancel()` / `expire()` 的旧顺序是「读快照 → await stop → 按旧快照算在途
    → 整份保存」。stop 是长 await，期间 `_settle()` 完全可能把**真实 action_id / pending
    关联**写进 store，而随后那次整份保存会把它覆盖回旧快照 —— 动作在外面跑，记录里却再也
    找不到关联。

    可重入：同一个 asyncio 任务再次进入不自锁（stop 等控制调用可能在同一调用链上回到生命周期
    读写），跨协程仍然严格互斥。**绝不在持有它的时候发起外部调用**（见 cancel/expire 的三段式）。

    Phase 7F.5.3 Follow-up 2 §三：可重入意味着嵌套调用"出锁"时并没有真的释放锁 —— 最外层才是
    真正的释放点。所以需要外部调用（SAFE stop）的路径一律只**排队**，由最外层退出锁之后统一
    执行（见 _defer_stop / _flush_pending_stops）。
    """

    __slots__ = ("_lock", "_owner", "_depth")

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._owner: asyncio.Task[Any] | None = None
        self._depth = 0

    @property
    def idle(self) -> bool:
        """没人持有、也没人排队（只用于有界回收）。"""
        return self._depth == 0 and self._owner is None and not self._lock.locked()

    def owned_by_current(self) -> bool:
        """当前 asyncio 任务是否**已经**持有这把锁（可重入判定，Follow-up 2 §三）。

        用来决定「谁来执行被推迟的 SAFE stop」：已经在锁内的嵌套调用只排队，最外层退出锁后
        统一 flush —— 否则嵌套路径会在锁**仍然被外层持有**的情况下去做外部调用。
        """
        current = asyncio.current_task()
        return current is not None and self._owner is current

    async def __aenter__(self) -> _LifecycleLock:
        current = asyncio.current_task()
        if current is not None and self._owner is current:
            self._depth += 1
            return self
        await self._lock.acquire()
        self._owner = current
        self._depth = 1
        return self

    async def __aexit__(self, *_exc: object) -> None:
        self._depth -= 1
        if self._depth > 0:
            return
        self._depth = 0
        self._owner = None
        self._lock.release()


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
        runtime_instance_of: Callable[[], str] | None = None,
        identity_probe: Callable[[], Awaitable[str]] | None = None,
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
        self._runtime_instance_of = runtime_instance_of
        self._identity_probe = identity_probe
        self._label_of = label_of
        self._log = logger
        #: Phase 7F.5.2 §三.3：身份变化对账的**再入保护**。对账内部会走 SAFE 控制面
        #: （minecraft_stop → 健康检查 → 可能再次观测到实例变化），所以回调必须能重入而
        #: 不递归等待。这里只打标记、**绝不等待**：无锁、无等待 ⇒ 结构上不可能死锁。
        self._identity_reconcile_inflight = False
        #: Phase 7F.5.3 §二窗口 6：**先到的动作终态事件**暂存区（action_id -> 事件内容）。
        #: 事件可能比 action_id 落盘更早到达（HTTP 响应仍在途）；匹配不到时先存下来，
        #: 等这一步真正拿到 action_id 之后立刻消费掉 —— 绝不静默丢弃。有界（FIFO 淘汰最旧），
        #: 与 Node 侧事件队列同样只保证"进程存活期间不丢"，这一点在文档里如实写明。
        self._deferred_action_events: dict[str, dict[str, Any]] = {}
        self._deferred_action_order: list[str] = []
        #: 已结算过的 action_id（有界）：重复终态事件必须是**幂等空操作** —— 既不改变任何
        #: 状态，也不该被误当成"未知事件"塞进暂存区。
        self._settled_action_ids: list[str] = []
        #: Phase 7F.5.3 Follow-up §三：**按任务**串行化「生命周期转换 ↔ 结算落盘」。
        #: 旧实现是「读快照 → await stop → 按旧快照算在途 → 整份保存」，stop 期间 _settle()
        #: 写进去的真实 action_id / pending 关联会被旧快照整份覆盖掉。现在 cancel()/expire()/
        #: _settle()/_pause_ambiguous_dispatch() 的读-改-写都在同一把锁里，绝不整份覆盖
        #: 比快照更新的证据。锁表有界（见 _lifecycle）。
        self._lifecycle_locks: dict[str, _LifecycleLock] = {}
        # Phase 7F.5.3 Follow-up 2 §三：被**推迟到锁外**的 SAFE stop（每任务一个有界队列）。
        # 锁内只排队；最外层退出锁之后才真的调用 minecraft_stop，再回锁内只合并停止证据。
        self._pending_stops: dict[str, list[str]] = {}
        #: 锁内消费了早到终态事件 → 出锁后要续跑的任务（有界）。
        self._pending_drives: list[str] = []

    def runtime_instance(self) -> str:
        """当前 Runtime 实例标识（Phase 7F.5）。拿不到就返回空串。

        空串只出现在两种情况：**没装配身份提供者**（纯单测替身，见 _resolve_dispatch_identity）
        或者 Runtime 还没给过一次成功的身份响应。装配了提供者的生产运行期里，
        派发**之前**会主动探测，拿不到就 fail-closed，绝不"默默降级成只按 action_id 关联"。
        """
        provider = self._runtime_instance_of
        if provider is None:
            return ""
        try:
            return str(provider() or "")
        except Exception:  # noqa: BLE001 - 拿不到实例不该影响派发
            return ""

    async def _resolve_dispatch_identity(self, record: TaskRecord, step: TaskStep) -> str | None:
        """派发**之前**确定「这次动作归哪个 Runtime 实例」（Phase 7F.5.1 §三.1/§四）。

        返回值有三种含义：

        * 实例字符串 —— 可信身份，直接写进这一代次的派发意图；
        * 空串 —— 没装配身份提供者（纯单测替身，沿用旧行为），或者探测失败
          （Runtime 不可达：按既有传输层语义处理，这里不另立政策）；
        * None —— 已经按安全策略处理过了（fail-closed 暂停），**绝不能再派发**。

        「Runtime 可达但不报告身份」必须在这里拦住：不能把新任务默默降级成只按
        action_id 关联 —— 那样的事件事后无法可靠验证来源（§四）。
        """
        if self._runtime_instance_of is None:
            return ""
        current = self.runtime_instance()
        if not current and self._identity_probe is not None:
            try:
                current = str(await self._identity_probe() or "")
            except Exception:  # noqa: BLE001 - 不可达：交给传输层按既有语义报告
                return ""
            if not current:
                await self._pause_ambiguous_dispatch(
                    record,
                    step,
                    message=(
                        "Runtime 可达但没有报告实例身份，无法在只按 action_id 关联的前提下"
                        f"安全派发 {step.tool}。已安全停下，请人工核对。"
                    ),
                    reason="runtime_instance_unavailable",
                )
                return None
        expected = str(step.dispatch_runtime_instance or "")
        if expected and current and expected != current:
            await self._pause_ambiguous_dispatch(
                record,
                step,
                message=(
                    f"派发前发现 Runtime 实例已更换（这一步记录的是 {expected}，"
                    f"当前是 {current}）：为不把动作发给错误的实例，已安全停下，请人工核对。"
                ),
                reason="runtime_instance_changed_before_dispatch",
            )
            return None
        return current

    async def _dispatch_precheck(self, record: TaskRecord, step: TaskStep, *, risk: str) -> bool:
        """外部动作发出**之前**的最后一道复核（Phase 7F.5.1 §三.4 / 7F.5.2 §二.1）。

        实例变化/取消/重规划/暂停的对账直接改**持久化记录**，而编排层手里的 record 可能还是
        旧快照。所以这里从 store 重新 load，用持久化真相判断这一步是否仍然被允许派发：
        任何一项对不上就**不派发**（返回 False），既不猜、也不重新派发 —— 该做的对账
        已经由对账方做完了。
        """
        return await self._dispatch_guard(record, step, risk=risk, expect_intent=True)

    async def _dispatch_allowed(
        self, record: TaskRecord, step: TaskStep, *, risk: str, expect_intent: bool
    ) -> tuple[str, TaskRecord]:
        """派发资格的统一谓词（Phase 7F.5.2 §二.1）。

        返回「(原因码, 持久化快照)」：原因码为空串表示**允许派发**，否则给出拒绝原因。
        判定**全部基于持久化真相**：别的协程（pause/cancel/expire/身份对账）改的是 store
        里的那一份，编排层手里的对象可能是旧快照 —— 拿旧快照判断就「看不见」暂停。

        expect_intent 区分两个窗口：

        * False：派发意图**还没落盘**（落盘前复核）；
        * True：派发意图**已经落盘**（真正发出外部请求之前的最后一道复核）。
        """
        fresh = await self._require(record.task_id)
        # 1) **只有 RUNNING 明确允许派发**。终态、PAUSED、等确认、等动作、重规划、PLANNING、
        #    WAITING_USER 一律拒绝 —— PAUSED 是 7F.5.2 补上的缺口：pause() 在没有前台动作
        #    时会把任务直接持久化成 PAUSED，而编排层手里的 record 还是 RUNNING。
        if fresh.state is not TaskState.RUNNING:
            return "task.not_running", fresh
        if fresh.replan_required:
            # 需要重新规划 = 旧计划已失效：新计划 + 新确认之前一步世界动作都不许走。
            return "task.replan_required", fresh
        if fresh.pause_requested:
            # 用户已经请求暂停：这一次动作绝不发出（外部动作计数必须为零，§三.1）。
            return "task.pause_requested", fresh
        fresh_step = fresh.step(step.step_id)
        if fresh_step is None:
            return "step.missing", fresh
        if expect_intent:
            if fresh_step.dispatch_state != DispatchState.INTENT.value:
                # 已经被对账判成歧义（AMBIGUOUS），或者状态已被推进。
                return "step.not_intent", fresh
        elif fresh_step.dispatch_unresolved:
            # 上一次派发的结果还悬着（INTENT/AMBIGUOUS）：**绝不再开一代**，也绝不在同一轮里
            # 把代次再加一次（§二.2）。
            return "step.dispatch_unresolved", fresh
        if fresh_step.dispatch_generation != step.dispatch_generation:
            return "step.generation_changed", fresh
        # 2) 授权仍然有效，而且仍然绑在**同一版**冻结计划上（§二.1）。
        if risk not in SAFE_RISKS:
            auth = fresh.authorization
            if auth is None:
                # 授权已被对账清掉：这一步不能再动世界。
                return "authorization.missing", fresh
            if not auth.valid(now=self._clock()):
                return "authorization.expired", fresh
            if auth.plan_hash != fresh.plan_hash:
                return "authorization.plan_changed", fresh
            if int(auth.plan_version or 0) != int(fresh.plan_version):
                return "authorization.plan_version_changed", fresh
        # 3) 身份：这次动作要发给**记录的**那个实例，而且它必须仍是当前实例（§二.1）。
        current = self.runtime_instance()
        recorded = str(fresh_step.dispatch_runtime_instance or "")
        if recorded and current and recorded != current:
            return "runtime.instance_changed", fresh
        return "", fresh

    async def _dispatch_guard(
        self, record: TaskRecord, step: TaskStep, *, risk: str, expect_intent: bool
    ) -> bool:
        """复核派发资格：允许 → True；拒绝 → False（必要时先做受控歧义暂停）。"""
        reason, fresh = await self._dispatch_allowed(
            record, step, risk=risk, expect_intent=expect_intent
        )
        if not reason:
            return True
        if reason == "runtime.instance_changed":
            current = self.runtime_instance()
            fresh_step = fresh.step(step.step_id)
            recorded = str(fresh_step.dispatch_runtime_instance or "") if fresh_step else ""
            self._adopt_lifecycle(record, fresh)
            await self._pause_ambiguous_dispatch(
                record,
                step,
                message=(
                    f"派发前发现 Runtime 实例已更换（记录的是 {recorded}，当前是 {current}）："
                    "为不把动作发给错误的实例，已安全停下，请人工核对。"
                ),
                reason="runtime_instance_changed_before_dispatch",
            )
            return False
        self._log_info(
            "dispatch refused",
            task_id=record.task_id,
            step=step.step_id,
            reason=reason,
        )
        return False

    def _adopt_lifecycle(self, record: TaskRecord, fresh: TaskRecord) -> None:
        """把持久化真相里的**生命周期字段**取回内存对象（Phase 7F.5.2 §二.2）。

        别的协程改的是 store 里的那一份，编排层手里的对象可能是旧快照；带着旧快照去
        _save 就会把持久化的 PAUSED 覆盖回 RUNNING。这里先把真相取回来，保证之后任何一次
        落盘写的都是与 store 一致的状态。
        """
        record.state = fresh.state
        record.authorization = fresh.authorization
        record.authorization_expired_at = fresh.authorization_expired_at
        record.replan_required = fresh.replan_required
        record.replan_reason = fresh.replan_reason
        record.pause_requested = fresh.pause_requested
        record.pending_action_id = fresh.pending_action_id
        record.pending_step_id = fresh.pending_step_id
        record.failure = fresh.failure
        record.failed_step = fresh.failed_step
        record.message = fresh.message
        record.recovery = fresh.recovery
        record.updated_at = fresh.updated_at

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
        # Phase 7F.5.2 §二.3：**只有明确允许执行的 RUNNING 才放行**。已经暂停（或等确认/
        # 等动作/重规划）的任务绝不能因为「授权对象还在有效期内」就把步骤放出去 ——
        # 用户暂停与派发交错时，安全行为必须是可解释、可测试的。
        if record.state is not TaskState.RUNNING:
            return False
        if record.authorization is None or not record.authorization.valid(now=self._clock()):
            return False
        # 确认时的那份计划 = 现在这份计划（否则说明计划被改过 → 授权作废）
        if record.authorization.plan_hash != record.plan_hash:
            return False
        # 5A.1 §四十四：授权必须批的是**当前这一版**计划。
        # 重新规划后 plan_version 会前进，旧确认（哪怕 task_id/user 都一样）一律不生效。
        if int(record.authorization.plan_version or 0) != int(record.plan_version):
            return False
        if record.replan_required or record.state is TaskState.REPLANNING:
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

    async def active_for_user(self, user_id: str, *, limit: int = 20) -> TaskRecord | None:
        """这个用户在**任意会话**里还有没有没结束的任务（Phase 5B §十七：先别开第二个）。"""
        wanted = str(user_id)
        for record in await self._store.list_recent(int(limit)):
            if record.state.terminal:
                continue
            if str(record.user_id) == wanted:
                return record
        return None

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
        source: str = "",
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
            source=str(source or ""),
            expires_at=self._clock() + float(self.config.ttl_seconds),
        )
        # 5A.1 §七：计划版本历史从第 1 版开始（旧计划永不覆盖，只 mark superseded）
        record.record_plan(
            now=record.created_at,
            summary=self.summary_of(record) if not self._validate_plan(record.plan) else "",
            status=PlanStatus.PENDING_CONFIRMATION.value,
            reason="",
        )
        await self._save(
            record,
            event=TASK_CREATED,
            detail=(
                f"source={record.source or '-'} user_id={record.user_id}"
                f" session_id={record.session_id} plan_version={record.plan_version}"
            ),
        )
        await self._publish(TASK_CREATED, self._event_payload(record))
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
        await self._publish(TASK_CONFIRMATION_REQUIRED, self._event_payload(record))
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
                await self._publish(TASK_CONFIRMATION_REQUIRED, self._event_payload(record))
            return record
        now = self._clock()
        # 5A.1 §十/§十一：授权有效期独立于任务总时长（expires_at 记的是任务上限，不覆盖它）
        record.authorization = TaskAuthorization(
            task_id=record.task_id,
            user_id=user_id,
            session_id=session_id,
            plan_hash=record.plan_hash,
            plan_version=record.plan_version,
            approved_at=now,
            expires_at=now + float(self.config.authorization_ttl_seconds),
        )
        record.authorization_expired_at = 0.0
        record.replan_required = False
        record.mark_plan_confirmed(now=now)
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
            # Phase 7F.5.2 §二.2：**每一轮都以持久化真相为准**。pause()/cancel()/expire()/
            # 身份对账改的都是 store 里的那一份记录，而循环手里这个对象可能还是旧快照 ——
            # 拿旧快照判断状态就「看不见」刚刚发生的暂停，会继续往下派发，甚至把持久化的
            # PAUSED 覆盖回 RUNNING。
            fresh = await self._require(task_id)
            # last_progress_key 是 TaskRecord 里**唯一不参与序列化**的字段（既有不对称）：
            # 重新 load 会让它回到空串，no-progress 检测就永远数不到"连续两次同一个动作"。
            # 它本来就是"这一次推进里上一步是什么"的内存标记，按原语义带过去。
            if record.last_progress_key and not fresh.last_progress_key:
                fresh.last_progress_key = record.last_progress_key
            record = fresh
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
            if record.state is TaskState.REPLANNING:
                # 5A.1 §五/§二十二：重规划期间只允许 SAFE 观察 —— 在拿到**新计划 + 新确认**
                # 之前一步世界动作都不许走（Planner 会走 replan() 把状态推到等确认）。
                return record
            if await self._expire_if_due(record):
                return record
            step = record.next_step()
            if step is None:
                return await self._finish(record)
            # Phase 7F.3：执行下一步之前，把所有**已走完但还没验证**的段先验证掉。
            # 放在这里（而不是"结算后立即验证"）是为了同时覆盖崩溃恢复：进程可能在
            # "动作已结算"与"到达验证"之间死掉，恢复后 drive 直接从下一步继续 ——
            # 若不在这里补验证，下一段就会在**没有上一段到达证据**的情况下开始走。
            if not await self._ensure_legs_verified(record, before_step=step):
                return record
            if step.dispatch_unresolved:
                # Phase 7F.4：这一步的**派发结果未知**（已落盘意图但无法确认外部是否接受）
                # -> 绝不重新派发（fail-closed）。这是最后一道防线：即使有人绕过
                # recover()/resume() 的守卫直接 drive()，也不可能把同一个动作再发一次。
                await self._pause_ambiguous_dispatch(
                    record,
                    step,
                    message=(
                        "这一步的外部动作结果未知（可能已经执行），已安全停下；"
                        f"请人工核对关联键 {step.dispatch_key or '-'} 后再决定，绝不自动重发"
                    ),
                )
                return record
            if record.state is not TaskState.RUNNING:
                await self._enter(record, TaskState.RUNNING)
            outcome = await self._run_step(record, step)
            if outcome == "wait":
                return record
            if outcome == "continue":
                continue
            if outcome == "blocked":
                # 派发在**发出外部动作之前**被拦下（Phase 7F.5.2 §二.2）：执行流程立即停止，
                # 并且绝不用手里这个旧快照去覆盖持久化的 PAUSED/终态。
                return await self._settle_blocked(record, step)
            # 其余 "stop" 一律以**持久化真相**返回：状态的改动可能来自别的协程
            # （pause/cancel/expire/身份对账），而这里手里的对象是旧快照。
            return await self._require(task_id)

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
            # 5A.1 §十-§十二：授权到期 → 安全边界（这一步还没开始）→ 回"等重新确认"。
            # 绝不偷偷继续，也绝不在动作中间硬切状态。
            await self._expire_authorization(record, step=step)
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
            # Phase 7F.4：**先把派发意图落盘，再调用工具**。外部副作用一旦真的发生，
            # 崩溃之后至少能知道"这一次派发可能已经发生"；否则持久化记录里那一步还是
            # PENDING、action_id 为空，恢复时就会把它当成"没执行"而重新派发（旧缺口）。
            # 代次 +1 且来自持久化记录，所以重启**不会**凭空产生一个新的键。
            # 新一次尝试 = 全新关联：清掉上一次的 action 关联与结果，避免审计里出现
            # "这一次派发"却带着"上一次的 action_id"（真实 action_id 关联必须是真关联）。
            step.action_id = ""
            step.status = ""
            step.result = {}
            # Phase 7F.5.1 §三.1：**先把可信身份定下来，再落盘派发意图**。Runtime 可达却
            # 报不出实例时，这里已经 fail-closed（返回 None），绝不带着未知归属发动作。
            identity = await self._resolve_dispatch_identity(record, step)
            if identity is None:
                return "stop"
            # Phase 7F.5.2 §二.1/§二.2：**落盘派发意图之前**先按持久化真相复核这一步还有没有
            # 资格派发。关键：这次复核返回之后到 _save 之间**没有任何 await**，所以 pause()
            # 不可能挤在「复核通过」与「写意图」之间 —— 持久化的 PAUSED 绝不会被内存里的
            # 旧快照覆盖回 RUNNING。
            if not await self._dispatch_guard(record, step, risk=risk, expect_intent=False):
                return "blocked"
            step.dispatch_generation += 1
            step.dispatch_key = dispatch_key_of(
                record.task_id, record.plan_hash, step.step_id, step.dispatch_generation
            )
            step.dispatch_state = DispatchState.INTENT.value
            step.dispatch_at = self._clock()
            # 记下**派发时**的运行时实例（来自最近一次成功的身份响应）—— 它让
            # "这次派发后来归谁" 可判定，而不是只能猜。dispatch_identity_tracked 则把
            # 「带身份追踪的新格式」与 7F.5.1 之前的旧持久化记录区分开（§四）。
            step.dispatch_runtime_instance = identity or self.runtime_instance()
            # 只有**装配了身份提供者**的运行期才算「处于身份追踪之下」（生产装配一定有）；
            # 纯单测替身（没装 provider）沿用旧行为，不假装自己有身份能力。
            step.dispatch_identity_tracked = self._runtime_instance_of is not None
            await self._save(record, event=TASK_STEP_STARTED, step_id=step.step_id)
            # Phase 7F.5.1 §三.4：落盘之后、**真正发出外部动作之前**用持久化真相复核一次。
            # 实例变化时的对账可能刚刚把这一步判成歧义并清掉授权，而内存里的 record 还是
            # 旧快照 —— 只看内存对象就会把「已被安全停下的动作」继续发出去。
            if not await self._dispatch_precheck(record, step, risk=risk):
                return "blocked"
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

    async def _settle_blocked(self, record: TaskRecord, step: TaskStep) -> TaskRecord:
        """派发被拦下之后，把执行流程与持久化真相对齐（Phase 7F.5.2 §二.2/§二.4）。

        复核是在 _invoke_step **之前**拒绝的，所以这一次派发**确认没有发出**（外部动作计数
        为零）。据此：

        * 把落盘的派发意图如实 retire 掉 —— 它以后**不该**被当成「结果未知」（那是 7F.4
          针对「可能已经发出」的语义，用在这里就是谎报）。代次保持不变：这一代从来不曾真正
          派发过，也绝不因为同一轮重复复核就再 +1；
        * **绝不**把持久化的 PAUSED/终态覆盖回 RUNNING（下面只读真相，不写旧快照）；
        * 用户正是在这个窗口请求了暂停 → 现在如实落到 PAUSED（外部动作计数为零）；
        * 其余情况一律返回持久化真相，让调用方看到的状态与 store 一致。
        """
        fresh = await self._require(record.task_id)
        fresh_step = fresh.step(step.step_id)
        if fresh_step is not None and fresh_step.dispatch_state == DispatchState.INTENT.value:
            fresh_step.dispatch_state = ""
            fresh_step.dispatch_at = 0.0
            if fresh_step.state is StepState.RUNNING:
                fresh_step.state = StepState.PENDING
            await self._save(fresh)
        if (
            fresh.pause_requested
            and fresh.state is TaskState.RUNNING
            and not fresh.pending_action_id
        ):
            fresh.pause_requested = False
            fresh.message = "已暂停（派发在发出外部动作之前被拦下）"
            await self._enter(fresh, TaskState.PAUSED)
            await self._publish(TASK_PAUSED, self._event_payload(fresh))
            await self._save(fresh, event=TASK_PAUSED)
        return fresh

    async def _settle(self, record: TaskRecord, step: TaskStep, invocation: TaskInvocation) -> str:
        # Phase 7F.5.3 Follow-up §三：结算的读-改-写与 cancel()/expire() 同属"生命周期转换"，
        # 必须在同一把按任务锁里串行化 —— 否则取消流程手里的旧快照会整份覆盖掉这里刚写进去的
        # 真实 action_id / pending 关联（可重入：_pause_ambiguous_dispatch 也在这把锁里）。
        # Phase 7F.5.3 Follow-up 2 §三：锁内**绝不**外部调用 —— 需要 SAFE stop 时只排队
        # （_defer_stop），这里确认已出锁之后再统一执行，然后回锁内只合并停止证据。
        lock = self._lifecycle(record.task_id)
        if lock.owned_by_current():
            return await self._settle_locked(record, step, invocation)
        async with lock:
            outcome = await self._settle_locked(record, step, invocation)
        await self._flush_pending_stops(record.task_id)
        if self._consume_pending_drive(record.task_id):
            # 锁内消费了一条**早到的终态事件**（它已经把这步结算掉了）：出锁后必须让 drive()
            # 继续推进，否则任务会停在 RUNNING 而 pending 已清空 —— 既不再等待、也不再前进。
            return "continue"
        return outcome

    async def _settle_locked(
        self, record: TaskRecord, step: TaskStep, invocation: TaskInvocation
    ) -> str:
        # Phase 7F.5.2 §二.2/§三.6：调用期间任务可能已经被暂停/取消/过期/判歧义 —— 那些改动
        # 直接落在 store 里，而这里的 record 还是旧快照。先把真相取回来：既不把持久化的
        # PAUSED 覆盖回 RUNNING，也不制造「任务已暂停、动作却没有记录」的假象。
        persisted = await self._require(record.task_id)
        if persisted.state is not TaskState.RUNNING:
            return await self._record_settlement_under_pause(record, step, invocation, persisted)
        if invocation.detached:
            # Phase 7F.5.1 §四：动作**已经跑到外部**了，必须有可信实例身份才能进入
            # 「等动作」。Runtime 可达却没有实例（或调用期间换了人）时，这条动作的终态
            # 事件事后无法可靠验证来源 —— 不能默默降级成只按 action_id 关联，直接 fail-closed。
            accepted = str(invocation.runtime_instance or step.dispatch_runtime_instance or "")
            if not accepted and self._runtime_instance_of is not None:
                await self._pause_ambiguous_dispatch(
                    record,
                    step,
                    message=(
                        f"{step.tool} 已经发到外部，但没有可用的 Runtime 实例身份："
                        "事后无法可靠验证这条动作的终态来自谁，已安全停下，请人工核对。"
                    ),
                    reason="runtime_instance_unavailable",
                )
                return "stop"
            step.state = StepState.WAITING_ACTION
            # Phase 7F.4：外部**已接受**，action_id 已知 -> ACCEPTED（等真实终态）。
            step.dispatch_state = DispatchState.ACCEPTED.value
            step.dispatch_identity_tracked = self._runtime_instance_of is not None
            # 接受它的实例是**权威**关联（比派发时的期望更准）。
            step.dispatch_runtime_instance = accepted
            step.action_id = invocation.action_id
            step.status = invocation.status
            step.summary = invocation.summary or step.summary
            record.pending_action_id = invocation.action_id
            record.pending_step_id = step.step_id
            await self._enter(record, TaskState.WAITING_ACTION)
            await self._publish(TASK_STEP_WAITING, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_WAITING, step_id=step.step_id)
            # Phase 7F.5.3 §二窗口 6：这条动作的终态事件可能比 action_id 落盘更早到达
            # （HTTP 响应仍在途）—— 现在已经有真实关联，立刻消费它，绝不永久丢失。
            if (
                await self._consume_deferred_action_event(str(invocation.action_id or ""))
                is not None
            ):
                return "stop"
            return "wait"

        if invocation.ok:
            step.state = StepState.SUCCEEDED
            # 同步完成 = 外部终态已知 -> SETTLED。
            step.dispatch_state = DispatchState.SETTLED.value
            step.dispatch_identity_tracked = self._runtime_instance_of is not None
            # 同步完成的动作终态已经由这次调用给定，实例只作审计关联。
            step.dispatch_runtime_instance = (
                invocation.runtime_instance or step.dispatch_runtime_instance
            )
            step.status = invocation.status or "SUCCEEDED"
            step.result = dict(invocation.result)
            step.summary = invocation.summary or step.summary
            await self._publish(TASK_STEP_SUCCEEDED, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_SUCCEEDED, step_id=step.step_id)
            return "continue"

        if invocation.ambiguous:
            # Phase 7F.4：**响应丢失** —— 外部 runtime 可能已经接受了这次请求。
            # 绝不记成普通动作失败（那会让 resume() 把它当成"失败过、可以重试"再发一次），
            # 而是 fail-closed：受控暂停 + 清授权 + 要求人工核对。
            # Phase 7F.5.1：调用期间实例更换（runtime.instance_changed）也走这条路 ——
            # 请求可能确实送到了，所以它同样是「结果未知」，同样不许重发。
            cause = f"（原因：{invocation.error}）" if invocation.error else ""
            await self._pause_ambiguous_dispatch(
                record,
                step,
                message=(
                    f"调用 {step.tool} 没有拿到 runtime 的答复：无法确认它是否已接受这次动作"
                    f"（关联键 {step.dispatch_key or '-'}）。为安全起见不重新派发，请人工核对。"
                    f"{cause}"
                ),
            )
            return "stop"
        step.state = StepState.FAILED
        # 拿到了 runtime 的明确答复（失败原因来自它）-> 外部终态已知 -> SETTLED。
        step.dispatch_state = DispatchState.SETTLED.value
        step.status = invocation.status or "FAILED"
        step.error = invocation.error
        failure = classify_failure(invocation.code, event=invocation.status)
        step.failure = failure.value
        step.message = invocation.error or invocation.code
        await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
        await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
        await self._pause_after_failure(record, failure, step)
        return "stop"

    async def _record_settlement_under_pause(
        self,
        record: TaskRecord,
        step: TaskStep,
        invocation: TaskInvocation,
        persisted: TaskRecord,
    ) -> str:
        """任务在调用期间已被暂停/取消/过期：只把**步骤证据**记实（Phase 7F.5.2 §二.2/§三.6）。

        任务状态一律以持久化真相为准 —— 绝不因为一次调用返回就把 PAUSED/CANCELLED 推回
        RUNNING；同时，**已经跑到外部的动作仍要留下完整关联**（action_id + pending），
        绝不能出现「任务说已暂停、动作却在外面跑且没有记录」的假象。
        """
        self._adopt_lifecycle(record, persisted)
        if invocation.detached:
            step.state = StepState.WAITING_ACTION
            step.dispatch_state = DispatchState.ACCEPTED.value
            step.dispatch_identity_tracked = self._runtime_instance_of is not None
            step.dispatch_runtime_instance = str(
                invocation.runtime_instance or step.dispatch_runtime_instance or ""
            )
            step.action_id = invocation.action_id
            step.status = invocation.status
            step.summary = invocation.summary or step.summary
            record.pending_action_id = invocation.action_id
            record.pending_step_id = step.step_id
            # Phase 7F.5.3 §三.B：拿到**真实 action_id** 之后必须**再停一次**（既有 SAFE
            # 控制面）—— 取消/过期时那一次 stop 可能先于动作创建到达，只靠它停不住这个动作；
            # 同时也绝不把"发过 stop"当成"动作肯定已停止"（停不掉就如实留证据给人工核对）。
            # Follow-up 2 §三：锁内绝不外部调用 —— 关联先落盘，stop 推迟到锁外执行。
            self._defer_stop(str(record.task_id), "settled_under_terminal")
            detail = dict((record.recovery or {}).get("detail") or {})
            detail.update(
                {
                    "dispatch_key": step.dispatch_key,
                    "dispatch_generation": step.dispatch_generation,
                    "step_id": step.step_id,
                    "dispatch_state": step.dispatch_state,
                    "dispatch_reason": str(
                        detail.get("dispatch_reason") or "settled_under_terminal"
                    ),
                    "runtime_instance": step.dispatch_runtime_instance,
                    "action_id": str(invocation.action_id or ""),
                    "pending_action_id": str(record.pending_action_id or ""),
                    "stop_after_accept": True,
                    "stop_after_accept_requests": int(detail.get("stop_after_accept_requests") or 0)
                    + 1,
                    "stop_requested": True,
                    "stop_confirmed": False,
                    "note": (
                        "动作已被 Runtime 接受：已再次经既有 SAFE 控制面请求停止，"
                        "但没有证据表明它已经停止 —— 仍需真实终态事件收敛，请人工核对"
                    ),
                    "at": self._clock(),
                }
            )
            record.recovery = {
                "reason": str((record.recovery or {}).get("reason") or "OUTSTANDING_ACTION"),
                "outcome": ReconcileOutcome.UNKNOWN.value,
                "at": self._clock(),
                "detail": detail,
                "message": "任务已终态，但仍有已被接受、尚未确认终结的动作；已再次请求停止",
            }
            if record.state is TaskState.PAUSED:
                # 等这个动作真正结束后再停：动作有记录、状态不撒谎。
                record.pause_requested = True
                record.message = "已请求暂停：当前动作仍在外部运行，等它结束后再停"
            await self._publish(TASK_STEP_WAITING, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_WAITING, step_id=step.step_id)
            # 这条动作的终态事件可能早于 action_id 落盘到达：现在有真实关联了，立刻消费。
            await self._consume_deferred_action_event(str(invocation.action_id or ""))
            return "stop"
        if invocation.ok:
            step.state = StepState.SUCCEEDED
            step.dispatch_state = DispatchState.SETTLED.value
            step.status = invocation.status or "SUCCEEDED"
            step.result = dict(invocation.result)
            step.summary = invocation.summary or step.summary
            await self._publish(TASK_STEP_SUCCEEDED, self._event_payload(record, step))
            await self._save(record, event=TASK_STEP_SUCCEEDED, step_id=step.step_id)
            return "stop"
        if invocation.ambiguous:
            # 结果**未知**：按既有歧义语义记录证据（终态任务保持终态，见
            # _pause_ambiguous_dispatch），绝不假设请求失败、也绝不自动重发。
            await self._pause_ambiguous_dispatch(
                record,
                step,
                message=(
                    f"调用 {step.tool} 没有拿到 runtime 的答复：无法确认它是否已接受这次动作"
                    f"（关联键 {step.dispatch_key or '-'}）。为安全起见不重新派发，请人工核对。"
                ),
                reason="ambiguous_after_terminal",
            )
            return "stop"
        # 拿到 runtime 的**明确答复**（终态已知）-> SETTLED。
        step.state = StepState.FAILED
        step.dispatch_state = DispatchState.SETTLED.value
        step.status = invocation.status or "FAILED"
        step.error = invocation.error
        step.failure = classify_failure(invocation.code, event=invocation.status).value
        step.message = invocation.error or invocation.code
        await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
        await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
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
            # 没拿到答复 = **可能已经被接受** -> 标成歧义，交给 fail-closed 分支处理。
            # 这里绝不猜"没执行"：猜错就是重复的世界动作。
            return TaskInvocation(
                ok=False,
                error=f"调用 {step.tool} 失败：{exc}",
                code="task.internal",
                ambiguous=True,
            )

    def _retryable(self, invocation: TaskInvocation) -> bool:
        """SAFE 查询的**瞬态**失败可以重试（§二十三）。"""
        retryable = {TaskFailure.OFFLINE, TaskFailure.BUSY, TaskFailure.INTERNAL}
        return classify_failure(invocation.code) in retryable

    async def _pause_after_failure(
        self, record: TaskRecord, failure: TaskFailure, step: TaskStep
    ) -> None:
        """步骤失败 → 停下，**绝不自动做新的世界修改**（§三十四）。

        Phase 5A.1 §四/§八：世界变了 / 目标丢了不是"暂停一下就能继续"的错误 ——
        旧计划的假设已经不成立，必须 superseded 并交给 Planner 出**新计划**（新确认）。
        """
        if failure is TaskFailure.CANCELLED:
            await self._cancel_record(record, reason="action cancelled")
            return
        if failure in {TaskFailure.WORLD_CHANGED, TaskFailure.TARGET_LOST}:
            await self._begin_replanning(
                record,
                reason=failure.value,
                step=step,
                failure=failure.value,
                message=(step.message or self.replan_headline(failure.value))[:400],
            )
            return
        record.state = TaskState.PAUSED
        record.failure = failure.value
        record.failed_step = step.step_id
        record.message = step.message[:400]
        await self._save(record, event=TASK_PAUSED, step_id=step.step_id)

    async def _pause_ambiguous_dispatch(
        self, record: TaskRecord, step: TaskStep, *, message: str, reason: str = ""
    ) -> None:
        # Phase 7F.5.3 Follow-up §三 + Follow-up 2 §一.B/§三：这里有两处必须一起修：
        #   ① 临界区里传入的 record/step 是**调用方的旧快照** —— 锁不等于数据新鲜度，所以进锁后
        #      用最新持久化记录重新定位这一步，并在最新的 generation / dispatch_key / dispatch_state
        #      上做单调性判定；
        #   ② 锁内绝不发起外部调用：SAFE stop 只排队，最外层出锁后统一执行
        #      （_flush_pending_stops）。
        task_id = str(record.task_id)
        lock = self._lifecycle(task_id)
        if lock.owned_by_current():
            # 已经在锁内（例如 _settle() 内部调过来）：可重入锁只有最外层会真正释放，
            # 所以由最外层负责 flush，这里只写证据。
            await self._pause_ambiguous_dispatch_fresh(record, step, message=message, reason=reason)
            return
        async with lock:
            await self._pause_ambiguous_dispatch_fresh(record, step, message=message, reason=reason)
        await self._flush_pending_stops(task_id)

    async def _pause_ambiguous_dispatch_fresh(
        self, record: TaskRecord, step: TaskStep, *, message: str, reason: str = ""
    ) -> None:
        """派发结果**未知** -> 受控暂停 + 人工介入（Phase 7F.4）。

        绝不做两件事：猜「它没执行」、或者重新派发同一个动作。做法是：

        * 这一步标成 FAILED，失败分类是 DISPATCH_AMBIGUOUS（**不是**普通动作失败，
          所以不会被当成「失败过、可以重试」）；
        * 派发状态标成 AMBIGUOUS（resume() 与 drive() 都会因此拒绝重发）；
        * **清掉授权**并置 replan_required：想再动世界就必须有新计划 + 新确认 ——
          这正是「受控暂停 + 人工介入」，而不是让一次 resume 把动作再发一遍；
        * 写进 record.recovery，这样重复恢复扫描不会覆盖掉这个分类（幂等）。

        注意：这**不是** exactly-once。外部动作有没有真的发生，本阶段无法证明；
        能保证的只是**不会因为恢复而再发一次**。
        """
        # ── Follow-up 2 §一.B：锁不等于数据新鲜度 ─────────────────────────────────
        # 用**最新持久化记录**重新定位这一步，并在最新 generation / dispatch_key /
        # dispatch_state 上做单调性判定：陈旧回调绝不允许把新状态倒退。
        latest = await self._store.load(str(record.task_id))
        if latest is not None:
            fresh_step = latest.step(str(step.step_id)) if step.step_id else None
            if fresh_step is None:
                self._log_info(
                    "ambiguous dispatch dropped: step no longer exists",
                    task_id=record.task_id,
                    step=step.step_id,
                )
                return
            if int(fresh_step.dispatch_generation or 0) > int(step.dispatch_generation or 0):
                self._log_info(
                    "ambiguous dispatch dropped: stale generation",
                    task_id=record.task_id,
                    step=step.step_id,
                )
                return
            if (
                fresh_step.dispatch_key
                and step.dispatch_key
                and fresh_step.dispatch_key != step.dispatch_key
            ):
                self._log_info(
                    "ambiguous dispatch dropped: stale dispatch key",
                    task_id=record.task_id,
                    step=step.step_id,
                )
                return
            if (
                fresh_step.dispatch_state == DispatchState.SETTLED.value
                and step.dispatch_state != DispatchState.SETTLED.value
            ):
                # 只有「调用方读到的还在派发中（INTENT/ACCEPTED/AMBIGUOUS），而 store 里已经结算
                # （SETTLED）」才是真陈旧回调。上一代就已 SETTLED 的步骤仍允许被**派发前**的
                # fail-closed 检查拒绝 —— 那时调用方快照本身也是 SETTLED，不是"倒退"。
                self._log_info(
                    "ambiguous dispatch dropped: step already settled",
                    task_id=record.task_id,
                    step=step.step_id,
                )
                return
            # 让**调用方对象**就地变成最新真相，再在它身上按原逻辑改写并保存：否则会写成
            # "store 更新了、调用方手里还是旧快照"，调用方稍后一保存就把新事实覆盖回去。
            step_id = str(step.step_id)
            record.__dict__.update(latest.__dict__)
            adopted = record.step(step_id) if step_id else None
            if adopted is None:
                return
            step = adopted
        # 终态判定一律看**最新记录**：绝不依赖调用方传入的 terminal 布尔值。
        step.state = StepState.FAILED
        step.dispatch_state = DispatchState.AMBIGUOUS.value
        step.failure = TaskFailure.DISPATCH_AMBIGUOUS.value
        step.message = message[:400]
        # Phase 7F.5.3 §三.A：**终态任务不改变终态** —— 用户取消就是取消、过期就是过期，
        # 绝不让一次歧义对账把 CANCELLED/EXPIRED 改写成 PAUSED；但证据照样如实记录。
        outstanding = str(step.action_id or record.pending_action_id or "")
        terminal_record = record.state.terminal
        record.authorization = None
        record.failure = TaskFailure.DISPATCH_AMBIGUOUS.value
        record.failed_step = step.step_id
        record.message = message[:400]
        if terminal_record:
            # 已经拿到**真实 action_id** 的动作必须保留未决关联：否则终态任务上的真实动作会
            # 失去事件匹配能力（find_by_action 需要 pending_action_id），dispatch_state 会
            # 永远停在 AMBIGUOUS、无法收敛。replan_required 对终态任务是惰性的，故不设。
            record.pending_action_id = outstanding
            record.pending_step_id = step.step_id if outstanding else ""
        else:
            record.replan_required = True
            record.replan_reason = TaskFailure.DISPATCH_AMBIGUOUS.value
            record.state = TaskState.PAUSED
            record.pending_action_id = ""
            record.pending_step_id = ""
        record.recovery = {
            "reason": TaskFailure.DISPATCH_AMBIGUOUS.value,
            "outcome": ReconcileOutcome.UNKNOWN.value,
            "at": self._clock(),
            "detail": {
                "dispatch_key": step.dispatch_key,
                "dispatch_generation": step.dispatch_generation,
                "step_id": step.step_id,
                # Phase 7F.5 §五.2：歧义必须留下可人工核对的全套证据。
                "dispatch_reason": str(reason or "ambiguous_dispatch"),
                "runtime_instance": step.dispatch_runtime_instance,
                "action_id": outstanding,
                "pending_action_id": outstanding,
                "terminal_state_preserved": terminal_record,
                "stop_requested": True,
                "stop_confirmed": False,
                "task_state": record.state.value,
                "at": self._clock(),
            },
            "message": message[:400],
        }
        # 受控暂停要**真的停下来**：外部动作可能还在跑（这正是「无法确认」的含义），
        # 所以用既有的 SAFE 控制面尽力停掉它。停不掉也不改变这里的 fail-closed 结论，
        # 更不会因此重新派发任何动作（_stop_action 自己吞异常、不改任务终态）。
        # Follow-up 2 §三：锁内绝不发起外部调用 —— 只排队，最外层出锁后 _flush_pending_stops()
        # 真正调用 minecraft_stop，再回锁内只合并停止证据（连带 stop_requested 已在上面写死）。
        # 既有语义：受控暂停**总是**尽力真停（不看有没有 action_id —— 只有派发意图时，
        # 请求也可能已经外发）。锁内只排队，最外层出锁后真正调用 SAFE 控制面。
        self._defer_stop(str(record.task_id), str(reason or TaskFailure.DISPATCH_AMBIGUOUS.value))
        await self._publish(TASK_STEP_FAILED, self._event_payload(record, step))
        if terminal_record:
            # 终态任务：发它自己的终态事件并落盘步骤级证据，**绝不谎报"暂停"**。
            await self._save(record, event=TASK_STEP_FAILED, step_id=step.step_id)
            return
        await self._publish(TASK_PAUSED, self._event_payload(record, step))
        await self._save(record, event=TASK_PAUSED, step_id=step.step_id)

    def _lifecycle(self, task_id: str) -> _LifecycleLock:
        """取这个任务的生命周期锁（Phase 7F.5.3 Follow-up §三）。

        锁表**有界**：只回收「没被持有、也没人排队」的表项，避免长期运行下无界增长。
        """
        key = str(task_id or "")
        lock = self._lifecycle_locks.get(key)
        if lock is None:
            if len(self._lifecycle_locks) >= 256:
                for stale in [k for k, v in self._lifecycle_locks.items() if v.idle]:
                    self._lifecycle_locks.pop(stale, None)
            lock = _LifecycleLock()
            self._lifecycle_locks[key] = lock
        return lock

    def _defer_drive(self, task_id: str) -> None:
        """登记"出锁后要续跑"（有界、去重）。锁内绝不调用 drive()。"""
        key = str(task_id or "")
        if not key or key in self._pending_drives:
            return
        self._pending_drives.append(key)
        del self._pending_drives[: -self.config.max_pending_drives]

    def _consume_pending_drive(self, task_id: str) -> bool:
        key = str(task_id or "")
        if key in self._pending_drives:
            self._pending_drives.remove(key)
            return True
        return False

    def _defer_stop(self, task_id: str, reason: str) -> None:
        """把一次 SAFE stop **推迟到锁外**执行（Phase 7F.5.3 Follow-up 2 §三）。

        锁内绝不做外部调用：这里只排队，最外层退出锁之后由 _flush_pending_stops() 真正执行。
        队列**有界**（每任务最多 config.max_pending_stop_requests 条），绝不无限增长。
        """
        key = str(task_id or "")
        if not key:
            return
        queue = self._pending_stops.setdefault(key, [])
        queue.append(str(reason or "lifecycle"))
        del queue[: -self.config.max_pending_stop_requests]

    async def _flush_pending_stops(self, task_id: str) -> None:
        """在**锁外**执行被推迟的 minecraft_stop，然后回锁内只合并停止请求证据（Follow-up 2 §三）。

        固定三段式：锁内写「安全的中间状态 + 请求过 stop」的证据 → 这里（确认已出锁）调用既有
        SAFE 控制面 → _merge_stop_evidence 重新读取最新记录并**只合并**停止证据。这样 stop 期间
        _settle()/on_action_event() 写进去的真实 action_id、终态、SETTLED 都不会被覆盖；
        没有真实停止证据时 stop_confirmed 保持 False（绝不谎报"已经停了"）。
        """
        key = str(task_id or "")
        reasons = self._pending_stops.pop(key, [])
        if not reasons:
            return
        await self._stop_action()
        await self._merge_stop_evidence(
            key,
            reason=reasons[-1],
            note=(
                "已在锁外经既有 SAFE 控制面（minecraft_stop）请求停止；没有任何证据表明动作"
                "已经停止，stop_confirmed 保持 False，仍需真实终态事件收敛，请人工核对。"
            ),
        )

    def _mark_inflight_for_terminal(
        self, record: TaskRecord, *, reason: str, prefix: str, mark_step: bool = True
    ) -> None:
        """终态转换前的在途判定（Phase 7F.5.3 §三.A）——**必须对着最新记录做**。

        有真实 action_id → 保留 pending 关联（终态任务照样有事件匹配能力，等真实终态收敛）；
        只有派发意图（还没有 action_id）→ 保留 INTENT 与证据，让 _settle 拿到真实 action_id
        后**再次**走既有 SAFE 控制面停止；确定没有在途派发时，才按原样清空。
        """
        inflight = self._inflight_step(record)
        if inflight is not None and str(inflight.action_id or ""):
            inflight.state = StepState.WAITING_ACTION
            inflight.message = reason
            record.pending_action_id = str(inflight.action_id)
            record.pending_step_id = inflight.step_id
            record.recovery = self._outstanding_action_evidence(
                inflight, reason=f"{prefix}_with_outstanding_action", note=reason
            )
        elif inflight is not None:
            # 外部是否已经接受这次请求**未知**：如实留证据，绝不假装"什么都没发生"。
            inflight.message = reason
            record.recovery = self._outstanding_action_evidence(
                inflight, reason=f"{prefix}_during_dispatch", note=reason
            )
        else:
            step = record.step(record.pending_step_id) if record.pending_step_id else None
            if mark_step and step is not None and step.state is StepState.WAITING_ACTION:
                step.state = StepState.CANCELLED
                step.message = reason
            record.pending_action_id = ""
            record.pending_step_id = ""

    async def _merge_stop_evidence(self, task_id: str, *, reason: str, note: str) -> TaskRecord:
        """stop 之后**重新读取**最新记录并只**合并**停止证据（Phase 7F.5.3 Follow-up §三.1）。

        关键在"只合并"：stop 期间 _settle() 可能已经写进去真实 action_id / pending / ACCEPTED，
        那次写入比取消流程手里的快照**更完整**，必须原样保留，只补"请求过 stop"的证据。
        「请求过 stop」仍然不等于「动作已经停止」：stop_confirmed 保持 False，绝不谎报。

        Follow-up 2 §三：这里**不要求任务已终态** —— 被推迟的 stop 也会走到 PAUSED 等非终态记录上
        （例如歧义暂停路径）。合并只碰 recovery 证据，绝不改 task state，所以对任何状态都安全。
        """
        async with self._lifecycle(task_id):
            latest = await self._store.load(task_id)
            if latest is None:
                raise TaskAuthorizationError(f"找不到任务 {task_id}", code="task.not_found")
            inflight = self._inflight_step(latest)
            step = (
                inflight
                if inflight is not None
                else (latest.step(latest.pending_step_id) if latest.pending_step_id else None)
            )
            raw = latest.recovery if isinstance(latest.recovery, Mapping) else {}
            detail = dict(raw.get("detail") or {}) if isinstance(raw, Mapping) else {}
            if step is None and not detail:
                # 确定没有在途派发：这一下 stop 是空操作，绝不编造没有发生的停止证据。
                return latest
            action_id = str(getattr(step, "action_id", "") or "")
            if step is not None:
                detail["dispatch_key"] = detail.get("dispatch_key") or step.dispatch_key
                detail["dispatch_generation"] = (
                    detail.get("dispatch_generation") or step.dispatch_generation
                )
                detail["step_id"] = detail.get("step_id") or step.step_id
                detail["dispatch_state"] = step.dispatch_state or detail.get("dispatch_state", "")
                detail["runtime_instance"] = (
                    detail.get("runtime_instance") or step.dispatch_runtime_instance
                )
                detail["action_id"] = action_id or str(detail.get("action_id") or "")
                detail["pending_action_id"] = str(
                    latest.pending_action_id or detail.get("action_id") or ""
                )
                detail["stop_after_accept"] = bool(detail.get("stop_after_accept")) or bool(
                    action_id
                )
                detail["stop_after_accept_requests"] = max(
                    int(detail.get("stop_after_accept_requests") or 0),
                    1 if (action_id or detail["stop_after_accept"]) else 0,
                )
            detail["stop_requested"] = True
            detail["stop_confirmed"] = bool(detail.get("stop_confirmed", False))
            detail.setdefault("dispatch_reason", reason)
            detail["note"] = str(note)[:200]
            detail["at"] = self._clock()
            latest.recovery = {
                "reason": str(raw.get("reason") or reason),
                "outcome": str(raw.get("outcome") or ReconcileOutcome.UNKNOWN.value),
                "at": self._clock(),
                "detail": detail,
                "message": str(raw.get("message") or note)[:400],
            }
            latest.updated_at = self._clock()
            await self._save(latest)
            return latest

    def _inflight_step(self, record: TaskRecord) -> TaskStep | None:
        """当前**在途派发**的那一步（INTENT 或 ACCEPTED），没有就返回 None。

        Phase 7F.5.3 §二：取消/过期的判断绝不能只看 pending_action_id 是否为空 ——
        「意图已落盘但还没有 action_id」的窗口里它是空的，而外部请求可能已经在路上。
        """
        for step in record.steps:
            if step.dispatch_state in {
                DispatchState.INTENT.value,
                DispatchState.ACCEPTED.value,
            }:
                return step
        return None

    def _outstanding_action_evidence(
        self, step: TaskStep, *, reason: str, note: str = ""
    ) -> dict[str, Any]:
        """「仍有在途动作、仍需对账/停止」的持久化证据（Phase 7F.5.3 §三.A）。

        复用既有 payload 的 recovery 字段（**不新增 schema、不迁移**）：从持久化记录就能判断
        动作是否已确认终结（dispatch_state=SETTLED）、是否仍需对账（INTENT/ACCEPTED/AMBIGUOUS）、
        以及是否仍需要停止（stop_requested 与 pending_action_id）。
        """
        return {
            "reason": str(reason),
            "outcome": ReconcileOutcome.UNKNOWN.value,
            "at": self._clock(),
            "detail": {
                "dispatch_key": step.dispatch_key,
                "dispatch_generation": step.dispatch_generation,
                "step_id": step.step_id,
                "dispatch_state": step.dispatch_state,
                "dispatch_reason": str(reason),
                "runtime_instance": step.dispatch_runtime_instance,
                "action_id": str(step.action_id or ""),
                "pending_action_id": str(step.action_id or ""),
                "stop_requested": True,
                # 已经拿到真实 action_id = 这次 stop **发生在接受之后**（计数与语义分开记，
                # 便于人工核对"到底停了几次、是不是在动作存在之后停的"）。
                "stop_after_accept": bool(str(step.action_id or "")),
                "stop_after_accept_requests": 1 if str(step.action_id or "") else 0,
                "stop_confirmed": False,
                "note": str(note)[:200],
                "at": self._clock(),
            },
            "message": str(note)[:400],
        }

    # ------------------------------------------- Phase 5A.1：重规划 / 到期 / 恢复

    async def _begin_replanning(
        self,
        record: TaskRecord,
        *,
        reason: str,
        step: TaskStep | None = None,
        failure: str = "",
        message: str = "",
        detail: str = "",
    ) -> TaskRecord:
        """进入 REPLANNING（§四/§九/§二十二）：旧计划 superseded，等新计划 + 新确认。

        在这里做一次**只读**对账（SAFE 查询），只为把"世界现在是什么样"写进审计；
        绝不挑替代目标、绝不产生任何世界动作。
        """
        now = self._clock()
        if record.state is not TaskState.REPLANNING:
            await self._enter(record, TaskState.REPLANNING)
        record.supersede_plan(now=now, reason=reason)
        record.replan_required = True
        record.replan_reason = str(reason)
        record.failure = str(failure or reason)
        if step is not None:
            record.failed_step = step.step_id
        record.message = (message or self.replan_headline(reason))[:400]
        reconcile = await self.reconcile_task(record)
        record.recovery = {
            "reason": str(reason),
            "outcome": str(reconcile.get("outcome") or ReconcileOutcome.UNKNOWN.value),
            "at": now,
            "detail": dict(reconcile.get("detail") or {}),
            "message": record.message,
        }
        await self._publish(TASK_REPLANNING, self._event_payload(record, step))
        await self._save(
            record,
            event=TASK_REPLANNING,
            step_id=step.step_id if step is not None else "",
            detail=detail
            or (
                f"reason={reason} outcome={record.recovery['outcome']}"
                f" plan_version={record.plan_version}"
            ),
        )
        return record

    async def _expire_authorization(
        self,
        record: TaskRecord,
        *,
        step: TaskStep | None = None,
        reason: str = "任务授权已到期",
    ) -> TaskRecord:
        """授权到期（§十-§十二）：安全边界 → PENDING_CONFIRMATION + 新确认条目。

        * 计划本身没有变（``plan_hash`` 不变），所以这**不是**重规划；
        * 只有真实用户回合能重新确认（``confirm_and_start`` 的门一个都没动）；
        * 正在跑的动作不会被强行终止 —— 这个检查只发生在"下一步还没开始"的位置。
        """
        now = self._clock()
        record.authorization = None
        record.authorization_expired_at = now
        record.failure = TaskFailure.AUTHORIZATION.value
        record.message = f"{reason}（计划没有变），需要重新确认"
        if step is not None:
            record.failed_step = step.step_id
            if step.state in {StepState.RUNNING, StepState.WAITING_ACTION}:
                step.state = StepState.FAILED
                step.failure = TaskFailure.AUTHORIZATION.value
                step.message = record.message
        version = record.current_plan_version()
        if version is not None:
            version.status = PlanStatus.PENDING_CONFIRMATION.value
        await self._enter(record, TaskState.PENDING_CONFIRMATION)
        record.confirmation_id = await self._confirmations.request(
            task_id=record.task_id,
            session_id=record.session_id,
            user_id=record.user_id,
            risk=self._plan_risk(record.plan),
            plan_hash=record.plan_hash,
            arguments=self._confirmation_arguments(record),
            summary=self.replan_summary(record, reason=ReplanReason.AUTHORIZATION_EXPIRED.value),
        )
        await self._publish(TASK_AUTHORIZATION_EXPIRED, self._event_payload(record, step))
        await self._save(
            record,
            event=TASK_AUTHORIZATION_EXPIRED,
            step_id=step.step_id if step is not None else "",
            detail=f"authorization expired at {now} plan_version={record.plan_version}",
        )
        await self._publish(TASK_CONFIRMATION_REQUIRED, self._event_payload(record))
        return record

    # ------------------------------------------------------------ 只读对账（§二十一）

    async def _safe_read(
        self, tool: str, arguments: Mapping[str, Any] | None = None
    ) -> TaskInvocation:
        """对账/观察期唯一允许的调用面：SAFE 只读（绝不产生世界动作，§五）。"""
        try:
            return await self._invoke(
                tool,
                dict(arguments or {}),
                task_id="",
                step_id="",
                plan_hash="",
                risk="SAFE",
                authorization=None,
            )
        except Exception:  # noqa: BLE001 - 读不到不是崩溃
            return TaskInvocation(ok=False, error=f"{tool} 读取失败", code="task.internal")

    async def reconcile_task(self, record: TaskRecord) -> dict[str, Any]:
        """对持久化的 Task 与当前真实世界做**有限、只读**的事实对账（§二十一/§二十二）。

        只回答"这一步的目标还在不在、还是不是原来那个东西"，输出
        ``RECONCILED / WORLD_CHANGED / TARGET_LOST / TARGET_ALREADY_DONE / OFFLINE / UNKNOWN``。
        **不做修复、不挑替代目标**：需要新目标时那是 Planner + 用户确认的事。
        """
        world = await self._safe_read("minecraft_world", {})
        if not world.ok:
            code = str(world.code or "")
            if code in {
                "minecraft.offline",
                "minecraft.not_connected",
                "minecraft.disabled",
                "minecraft.runtime_down",
                "action.not_online",
            }:
                return _reconcile(ReconcileOutcome.OFFLINE, code=code)
            return _reconcile(ReconcileOutcome.UNKNOWN, code=code)
        facts = world.result if isinstance(world.result, Mapping) else {}
        if facts.get("online") is False:
            return _reconcile(ReconcileOutcome.OFFLINE, reason="offline")

        step = record.step(record.pending_step_id) if record.pending_step_id else None
        if step is None:
            step = record.next_step()
        if step is None:
            return _reconcile(ReconcileOutcome.RECONCILED, note="no_step")
        return await self._reconcile_step(step)

    async def _reconcile_step(self, step: TaskStep) -> dict[str, Any]:
        """单个步骤的只读事实核对（只认"世界目标"这一类可判定事实）。"""
        arguments = step.effective_arguments
        if step.tool == "minecraft_dig":
            coords = [arguments.get("x"), arguments.get("y"), arguments.get("z")]
            if not all(isinstance(value, int) for value in coords):
                return _reconcile(ReconcileOutcome.UNKNOWN, note="dig_without_coords")
            expected = canonical_item_name(arguments.get("expected_block"))
            probe = await self._safe_read(
                "minecraft_dig_capability",
                {"x": coords[0], "y": coords[1], "z": coords[2]},
            )
            if not probe.ok:
                code = str(probe.code or "")
                if code in {"minecraft.block_unavailable", "minecraft.block_not_found"}:
                    return _reconcile(
                        ReconcileOutcome.TARGET_LOST,
                        code=code,
                        expected=expected,
                        position=coords,
                    )
                return _reconcile(ReconcileOutcome.UNKNOWN, code=code)
            data = probe.result if isinstance(probe.result, Mapping) else {}
            block = data.get("block") if isinstance(data.get("block"), Mapping) else {}
            name = canonical_item_name(block.get("name")) if block else ""
            reason = str(data.get("reason") or "")
            if reason == "air" or not name:
                # 方块已经不在了：可能是上次运行里挖掉的（checkpoint 没来得及记），
                # 也可能是别人挖的。**绝不**再挖一次（§十八/§十九）。
                return _reconcile(
                    ReconcileOutcome.TARGET_ALREADY_DONE, reason="air", expected=expected
                )
            if expected and name != expected:
                return _reconcile(ReconcileOutcome.WORLD_CHANGED, expected=expected, actual=name)
            return _reconcile(ReconcileOutcome.RECONCILED, block=name, reason=reason)
        if step.tool == "minecraft_pickup_item":
            expected = canonical_item_name(arguments.get("expected_item"))
            wanted = arguments.get("entity_id")
            drops = await self._safe_read("minecraft_dropped_items", {})
            if not drops.ok:
                return _reconcile(ReconcileOutcome.UNKNOWN, code=str(drops.code or ""))
            payload = dict(drops.result) if isinstance(drops.result, Mapping) else {}
            raw_rows = payload.get("items")
            rows: list[Any] = list(raw_rows) if isinstance(raw_rows, list) else []
            for row in rows:
                if not isinstance(row, Mapping):
                    continue
                if wanted is not None and row.get("entity_id") == wanted:
                    item = row.get("item") if isinstance(row.get("item"), Mapping) else {}
                    name = canonical_item_name(item.get("name")) if item else ""
                    if expected and name and name != expected:
                        return _reconcile(
                            ReconcileOutcome.WORLD_CHANGED, expected=expected, actual=name
                        )
                    return _reconcile(ReconcileOutcome.RECONCILED, entity_id=wanted)
            return _reconcile(ReconcileOutcome.TARGET_LOST, entity_id=wanted, expected=expected)
        # 其余步骤（move_to / SAFE 读 / equip …）没有"世界目标还在不在"的可判定事实
        return _reconcile(ReconcileOutcome.RECONCILED, tool=step.tool, note="no_fact")

    # ------------------------------------------------------------ 重启恢复（§十三-§二十）

    async def recover(
        self, task_id: str, *, reason: str = ReplanReason.RUNTIME_RESTART.value
    ) -> TaskRecord:
        """进程重启后的安全恢复：旧 action 一律失效，**绝不重复世界动作**。

        * 旧 Node runtime / action 生命周期已经不存在 —— 不查询、不猜、不等待；
        * 先把"正在等动作"的那一步判成失效（``RUNTIME_RESTART``）；
        * 再做一次只读对账，用**世界事实**决定是"世界变了 → REPLANNING"还是"停下等用户"。
        """
        record = await self._require(task_id)
        if record.state.terminal:
            return record
        pending = bool(record.pending_action_id or record.pending_step_id)
        stale_step: TaskStep | None = None
        if pending:
            stale_step = record.step(record.pending_step_id) if record.pending_step_id else None
            if stale_step is not None and stale_step.state is StepState.WAITING_ACTION:
                stale_step.state = StepState.FAILED
                stale_step.failure = TaskFailure.RUNTIME_RESTART.value
                stale_step.message = "进程重启：这一步的动作已经不存在了（不会重做）"
                # §十七/§四十二：只要有一步骤**真的派出去过**，这份旧确认就不能再放行任何
                # 世界动作 —— 必须重新观察 + 新计划 + 新确认（哪怕世界看起来没变）。
                record.authorization = None
                record.replan_required = True
                record.replan_reason = ReplanReason.RUNTIME_RESTART.value
        # Phase 7F.4：**没有 pending_action_id，但某一步的派发意图已经落盘** —— 说明崩溃
        # 发生在"runtime 可能已接受请求"与"写回 action_id"之间。旧实现只看 pending_*，
        # 于是把这种情况当成"什么都没发生过"：授权仍然有效、replan_required 仍是 False，
        # 一次 resume() 就能把同一个动作再发一遍。这里改成 fail-closed。
        unresolved_step = next((item for item in record.steps if item.dispatch_unresolved), None)
        if unresolved_step is not None and not (
            record.recovery and record.state in {TaskState.PAUSED, TaskState.REPLANNING}
        ):
            await self._pause_ambiguous_dispatch(
                record,
                unresolved_step,
                message=(
                    "进程在上一次动作派发期间退出：无法确认外部 runtime 是否已经接受了这次动作"
                    f"（关联键 {unresolved_step.dispatch_key or '-'}）。"
                    "为了不重复世界动作，这里不重新派发 —— 请人工核对后再决定。"
                ),
            )
            await self._publish(TASK_RECOVERED, self._event_payload(record, unresolved_step))
            await self._save(
                record,
                event=TASK_RECOVERED,
                step_id=unresolved_step.step_id,
                detail="recovery reason=dispatch_ambiguous",
            )
            return record
        if (
            not pending
            and record.recovery
            and record.state
            in {TaskState.PAUSED, TaskState.REPLANNING, TaskState.PENDING_CONFIRMATION}
        ):
            # 已经恢复过（幂等）：重复扫描不重复告警、不重复对账
            return record
        record.pending_action_id = ""
        record.pending_step_id = ""
        record.pause_requested = False
        reconcile = await self.reconcile_task(record)
        outcome = str(reconcile.get("outcome") or ReconcileOutcome.UNKNOWN.value)
        now = self._clock()
        record.recovery = {
            "reason": str(reason),
            "outcome": outcome,
            "at": now,
            "detail": dict(reconcile.get("detail") or {}),
        }
        if outcome in {
            ReconcileOutcome.WORLD_CHANGED.value,
            ReconcileOutcome.TARGET_LOST.value,
            ReconcileOutcome.TARGET_ALREADY_DONE.value,
        }:
            await self._begin_replanning(
                record,
                reason=ReplanReason.RUNTIME_RESTART.value,
                step=stale_step,
                failure=outcome,
                message=self.recovery_message(outcome),
            )
        else:
            # 世界事实没变（或者现在读不到世界）：按"这条任务现在停在哪"归位。
            # 注意 **不能**一律切成 PAUSED —— 状态机里 PENDING_CONFIRMATION → PAUSED 是非法
            # 转移；而且语义上"还在等用户确认"的任务重启后本来就该继续等确认，
            # 只是内存里的确认条目已经没了，需要**重新挂一条**（§十一/§六十二）。
            if record.state is TaskState.PENDING_CONFIRMATION:
                record.confirmation_id = await self._confirmations.request(
                    task_id=record.task_id,
                    session_id=record.session_id,
                    user_id=record.user_id,
                    risk=self._plan_risk(record.plan),
                    plan_hash=record.plan_hash,
                    arguments=self._confirmation_arguments(record),
                    summary=self.summary_of(record),
                )
                record.message = "罐头重启过，之前那条确认已经失效了，请重新确认计划。"
                await self._publish(TASK_CONFIRMATION_REQUIRED, self._event_payload(record))
            elif record.state is not TaskState.PAUSED:
                await self._enter(record, TaskState.PAUSED)
                # Phase 6A：重启后发现任务该停在 PAUSED —— 也对外说一声（活动层据此收尾）
                await self._publish(TASK_PAUSED, self._event_payload(record, stale_step))
            if record.state is TaskState.PAUSED:
                record.failure = TaskFailure.RUNTIME_RESTART.value
                record.failed_step = stale_step.step_id if stale_step is not None else ""
                record.message = self.recovery_message(outcome)[:400]
            record.recovery["message"] = record.message
        await self._publish(TASK_RECOVERED, self._event_payload(record, stale_step))
        await self._save(
            record,
            event=TASK_RECOVERED,
            step_id=stale_step.step_id if stale_step is not None else "",
            detail=f"recovery reason={reason} outcome={outcome} plan_version={record.plan_version}",
        )
        self._log_info(
            "task recovered",
            task_id=record.task_id,
            reason=reason,
            outcome=outcome,
            state=record.state.value,
        )
        return record

    async def recover_persisted_tasks(self, *, limit: int | None = None) -> list[TaskRecord]:
        """启动时扫描持久化的非终态任务并逐个安全恢复（§十三）。

        **逐条隔离**：某一条任务的数据让恢复炸了，也绝不影响其它任务，更不能让
        "启动恢复"这件事把整个任务能力带下去（真机上踩过：一条"等确认"的任务
        触发了非法转移，结果 Bot 启动时把 task runtime 整块关掉了）。
        """
        # Phase 7F.5 §四：**完整**的非终态集合（不是"最近 N 条"）——
        # 早于窗口的未决任务同样会被恢复，而不是等 TTL 过期。
        recovered: list[TaskRecord] = []
        for record in await self._store.list_pending(limit):
            if record.state.terminal:
                continue
            try:
                recovered.append(await self.recover(record.task_id))
            except Exception:  # noqa: BLE001 - 单条任务恢复失败不拖垮启动
                if self._log is not None:
                    self._log.exception(
                        "[Task] recovery failed for task=%s (ignored)", record.task_id
                    )
                else:  # pragma: no cover - 没有 logger 时也别炸
                    log.warning("task recovery failed task=%s", record.task_id)
        return recovered

    @staticmethod
    def recovery_message(outcome: str) -> str:
        table = {
            ReconcileOutcome.TARGET_ALREADY_DONE.value: (
                "罐头重启过；对账发现那个目标已经被处理掉了（不会重做）。原计划已停止。"
            ),
            ReconcileOutcome.WORLD_CHANGED.value: (
                "罐头重启过；对账发现世界里的目标和原计划不一样了。原计划已停止。"
            ),
            ReconcileOutcome.TARGET_LOST.value: (
                "罐头重启过；对账发现原计划的目标已经不在了。原计划已停止。"
            ),
            ReconcileOutcome.OFFLINE.value: (
                "罐头重启过，而且现在不在世界里；之前那一步不会重做。等她回到世界再说。"
            ),
        }
        return table.get(str(outcome), "罐头重启过，之前那一步的动作已经不存在了（不会重做）。")

    # ------------------------------------------------------------ action 事件

    async def on_runtime_instance_changed(self, runtime_instance: str) -> list[TaskRecord]:
        """Runtime 实例换了 → 把所有**属于旧实例**的未决动作按 fail-closed 处理（Phase 7F.5 §五）。

        **只在真正拿到新实例身份时调用**（来自一次成功的身份响应）：单次请求失败、单次健康检查
        失败都**不足以**推断实例已换人，也绝不据此结算或重发任何动作。

        我们没有按键查询能力，所以旧实例动作的真实状态一律**无法确认** → 进入明确歧义 + 人工核对；
        **绝不**因为「新 Runtime 已经可用」就重新派发旧动作（§五.2 明确禁止）。
        """
        current = str(runtime_instance or "")
        if not current:
            return []
        if self._identity_reconcile_inflight:
            # Phase 7F.5.2 §三.3：对账内部会走 SAFE 控制面（minecraft_stop → 健康检查 →
            # 可能再次观测到实例变化 → 又回到这里）。这里**只打标记、绝不等待**：重入直接
            # 返回空，既不递归等待 stop，也不与身份观测互相等锁 —— 结构上不存在死锁。
            # 跳过这一轮是安全的：对账本身幂等，而**派发**另有落盘前/落盘后两道身份复核
            # （_dispatch_allowed），安全性并不依赖这个回调跑完。
            self._log_info("identity reconcile re-entered; skipped", runtime_instance=current)
            return []
        self._identity_reconcile_inflight = True
        try:
            affected: list[TaskRecord] = []
            # Phase 7F.5.3：用 list_outstanding（**含终态**）—— 取消/过期之后才被接受的
            # 动作也必须有实例对账，不能被"非终态"这个过滤条件漏掉。
            for record in await self._store.list_outstanding():
                stale = next(
                    (
                        item
                        for item in record.steps
                        if item.dispatch_state
                        in {DispatchState.INTENT.value, DispatchState.ACCEPTED.value}
                        and str(item.dispatch_runtime_instance or "")
                        and str(item.dispatch_runtime_instance) != current
                    ),
                    None,
                )
                if stale is None:
                    continue
                await self._pause_ambiguous_dispatch(
                    record,
                    stale,
                    message=(
                        f"Runtime 实例已更换（这次动作记录的是 {stale.dispatch_runtime_instance}，"
                        f"当前是 {current}）：旧动作的真实状态无法查询，"
                        "为不重复世界动作已安全停下，请人工核对。"
                    ),
                    reason="runtime_instance_changed",
                )
                affected.append(record)
            return affected
        finally:
            self._identity_reconcile_inflight = False

    def _defer_action_event(self, payload: dict[str, Any]) -> None:
        """暂存匹配不到的动作终态事件（Phase 7F.5.3 §二窗口 6）。有界 FIFO，绝不无限增长。

        边界如实说明：与 Node 侧事件队列一样，这是**进程存活期间**的保证 —— 进程崩溃后既没有
        事件、也没有关联，任务仍旧 fail-closed（不会因为丢事件而重发动作）。
        """
        action_id = str(payload.get("action_id") or "")
        if not action_id:
            return
        if action_id in self._settled_action_ids:
            # 已经结算过的重复事件：幂等空操作，不该被当成"未知事件"塞进暂存区。
            return
        if action_id in self._deferred_action_events:
            self._deferred_action_events[action_id] = payload
            return
        self._deferred_action_events[action_id] = payload
        self._deferred_action_order.append(action_id)
        while len(self._deferred_action_order) > self.config.max_deferred_action_events:
            self._deferred_action_events.pop(self._deferred_action_order.pop(0), None)

    async def _consume_deferred_action_event(self, action_id: str) -> TaskRecord | None:
        """拿到真实 action_id 之后立刻消费早到的事件（消费一次即移除）。"""
        wanted = str(action_id or "")
        if not wanted:
            return None
        payload = self._deferred_action_events.pop(wanted, None)
        if payload is None:
            return None
        try:
            self._deferred_action_order.remove(wanted)
        except ValueError:
            pass
        return await self.on_action_event(**payload)

    def _remember_settled_action(self, action_id: str) -> None:
        wanted = str(action_id or "")
        if not wanted:
            return
        self._settled_action_ids.append(wanted)
        del self._settled_action_ids[: -self.config.max_settled_action_ids]

    async def on_action_event(
        self,
        *,
        action_id: str,
        event: str,
        status: str = "",
        result: Mapping[str, Any] | None = None,
        error: str = "",
        code: str = "",
        runtime_instance: str = "",
    ) -> TaskRecord | None:
        """异步动作的终态 → 恢复对应 Task 的对应 step（绑 action_id **与** Runtime 实例）。

        Phase 7F.5 §四 + 7F.5.1 §四：匹配是**完整的未决动作查询**（可从持久化记录重建，
        不受「最近 50 条」限制），并且按下面的矩阵严格判定（不结算一律返回 None）：

        * 一个 action_id 命中**多条**记录 → 全部按**歧义**处理（绝不取第一个匹配项）；
        * 记录有实例、事件实例**相同** → 正常结算；
        * 记录有实例、事件实例**不同** → 不结算（旧实例迟到事件不得结算新实例的动作）；
        * 记录有实例、事件**缺实例** → 不结算：事件来源无法可靠验证（7F.5.1 的修正）；
        * 记录**没有**实例（7F.5.1 之前的旧持久化行）→ 明确界定的兼容路径：按 action_id
          兼容结算，并持久化 dispatch_legacy_fallback 标记，人工核对时能看出走过的回退路径；
        * 匹配不到 → 未知事件，什么都不改（任务状态、授权、派发代次一律不动）。

        Phase 7F.5.3 Follow-up 2 §一.A：事件处理与 cancel()/expire()/_settle() 共用**同一把按任务
        生命周期锁**，而且**锁内重新查询**（锁不等于数据新鲜度）—— 绝不用锁外读到的旧快照整份写回
        覆盖更新的终态 / 真实 action_id。终态任务（CANCELLED/EXPIRED）只收敛步骤证据与 pending
        关联，绝不重新进入 RUNNING/WAITING_ACTION/PAUSED，也绝不 drive()。

        多重匹配时**按 task_id 排序逐把加锁**，且锁内绝不获取第二把任务锁 —— 结构上不存在
        交叉加锁死锁。drive() 一律在**锁外**执行（它可能真的发世界动作，持锁会把取消挡住整个时长）。

        注意：实例标识只解决**关联唯一性**，不是幂等键、也不提供 exactly-once 保证。
        """
        if not action_id:
            return None
        payload: dict[str, Any] = {
            "action_id": str(action_id),
            "event": str(event),
            "status": str(status or ""),
            "result": dict(result or {}),
            "error": str(error or ""),
            "code": str(code or ""),
            "runtime_instance": str(runtime_instance or ""),
        }
        # 先做一次**无锁**的候选发现，只用来决定要对哪些任务加锁；真正的判定在锁内重查。
        candidates = await self._store.find_by_action(str(action_id), include_terminal=True)
        if not candidates:
            # §二窗口 6：事件可能比 action_id 落盘更早到达（HTTP 响应仍在途）。先存下来，
            # 等这一步真正拿到 action_id 之后立刻消费（_consume_deferred_action_event），
            # 绝不静默丢弃；重复事件在暂存区里只保留最新一份。
            self._defer_action_event(payload)
            return None
        if len(candidates) > 1:
            for task_id in sorted({str(item.task_id) for item in candidates}):
                await self._ambiguous_event_for_task(task_id, payload)
            return None
        task_id = str(candidates[0].task_id)
        lock = self._lifecycle(task_id)
        if lock.owned_by_current():
            # 已在锁内（例如 _settle() 消费早到事件时调用）：这里**不能** drive —— 它可能真的
            # 发世界动作，而可重入锁只有最外层才真正释放。登记"待续跑"，由最外层在出锁后
            # 让它以 "continue" 继续（见 _settle 的 wrapper）。
            target, should_drive = await self._on_action_event_locked(task_id, payload)
            if should_drive:
                self._defer_drive(task_id)
            return target
        async with lock:
            target, should_drive = await self._on_action_event_locked(task_id, payload)
        await self._flush_pending_stops(task_id)
        if should_drive or self._consume_pending_drive(task_id):
            # 动作结束了：先把状态切回 RUNNING **并落盘**再继续下一步（已在 _on_action_event_locked
            # 内完成）。drive() 在这里、也就是**锁外**调用：它会从存储重新 load，若期间任务已被
            # 取消/过期，drive() 的终态守卫（record.state.terminal）会直接返回，绝不复活任务。
            return await self.drive(task_id)
        return target

    async def _ambiguous_event_for_task(self, task_id: str, payload: Mapping[str, Any]) -> None:
        """同一个 action_id 命中多条记录时的歧义分支（按 task_id 逐把加锁，绝不嵌套加锁）。"""
        lock = self._lifecycle(task_id)
        if lock.owned_by_current():
            await self._pause_ambiguous_for_action(task_id, payload)
            return
        async with lock:
            await self._pause_ambiguous_for_action(task_id, payload)
        await self._flush_pending_stops(task_id)

    async def _pause_ambiguous_for_action(self, task_id: str, payload: Mapping[str, Any]) -> None:
        record = await self._store.load(str(task_id))
        if record is None:
            return
        step = record.step(record.pending_step_id) if record.pending_step_id else None
        if step is None:
            return
        await self._pause_ambiguous_dispatch(
            record,
            step,
            message=(
                f"同一个 action_id（{payload.get('action_id') or ''}）关联了多条任务记录："
                "无法判断这次终态属于谁，已全部安全停下，请人工核对。"
            ),
            reason="action_id_ambiguous",
        )

    async def _on_action_event_locked(
        self, task_id: str, payload: Mapping[str, Any]
    ) -> tuple[TaskRecord | None, bool]:
        """锁内完成事件的判定与结算；返回 (记录, 是否需要在**锁外**继续 drive())。"""
        action_id = str(payload.get("action_id") or "")
        event = str(payload.get("event") or "")
        status = str(payload.get("status") or "")
        result = payload.get("result") or {}
        error = str(payload.get("error") or "")
        code = str(payload.get("code") or "")
        runtime_instance = str(payload.get("runtime_instance") or "")
        # Follow-up 2 §一.A.2/3：**锁内重新查询**并重新验证匹配数量、步骤与关联事实。
        matches = await self._store.find_by_action(action_id, include_terminal=True)
        if not matches:
            if action_id in self._settled_action_ids:
                # 已结算过的重复事件：幂等空操作（既不重复结算，也不再进暂存区）。
                return None, False
            # 关联在这两步之间消失了（可能正在建立、也可能是未知事件）：留在暂存区，
            # 绝不因为一次正常竞争就把事件**永久丢弃**。
            self._defer_action_event(dict(payload))
            return None, False
        if len(matches) > 1:
            # 锁内绝不获取第二把任务锁（避免交叉加锁死锁）：只处理**本任务**这一条，
            # 其余记录由调用方的逐把加锁分支或后续事件处理。
            for item in matches:
                if str(item.task_id) != str(task_id):
                    continue
                step = item.step(item.pending_step_id) if item.pending_step_id else None
                if step is None:
                    continue
                await self._pause_ambiguous_dispatch(
                    item,
                    step,
                    message=(
                        f"同一个 action_id（{action_id}）关联了多条任务记录："
                        "无法判断这次终态属于谁，已全部安全停下，请人工核对。"
                    ),
                    reason="action_id_ambiguous",
                )
            return None, False
        target = matches[0]
        if str(target.task_id) != str(task_id):
            return None, False
        step = target.step(target.pending_step_id) if target.pending_step_id else None
        if step is None:
            return None, False
        recorded = str(step.dispatch_runtime_instance or "")
        incoming = str(runtime_instance or "")
        if not step.dispatch_identity_tracked:
            # ── 旧格式回退路径（Phase 7F.5.1 §四，明确界定）─────────────────────
            # 这一行是 7F.5.1 之前写下的：它没有身份追踪，唯一可用的关联就是 action_id。
            # 兼容行为到此为止：采纳事件给出的实例（如果有）供审计，并**持久化标记**
            # 这是旧格式回退路径，人工核对时能看出"这一条为什么没有实例校验"。
            step.dispatch_legacy_fallback = True
            if incoming:
                # 事件带身份、记录没带 -> 采纳它并升级成"已追踪"，供后续审计。
                step.dispatch_runtime_instance = incoming
                step.dispatch_identity_tracked = True
        elif not incoming:
            # 记录要求实例、事件却不带实例 -> 来源无法可靠验证 -> **不得**结算。
            await self._pause_ambiguous_dispatch(
                target,
                step,
                message=(
                    "收到不带 Runtime 实例标识的动作事件：无法验证它是否来自本任务记录的"
                    f"实例（记录的是 {recorded or '-'}），不得据此结算，已安全停下，请人工核对。"
                ),
                reason=(
                    "missing_runtime_instance_event" if recorded else "runtime_instance_missing"
                ),
            )
            return None, False
        elif not recorded:
            # 新格式记录却没有实例：这次派发本身就无法归属 -> 同样不得据此结算。
            await self._pause_ambiguous_dispatch(
                target,
                step,
                message=(
                    f"收到实例为 {incoming} 的动作事件，但本任务这一步没有记录实例身份："
                    "无法确认这次终态属于谁，不得据此结算，已安全停下，请人工核对。"
                ),
                reason="runtime_instance_unattributed_event",
            )
            return None, False
        elif recorded != incoming:
            await self._pause_ambiguous_dispatch(
                target,
                step,
                message=(
                    f"收到来自旧 Runtime 实例的事件（事件实例 {incoming}，"
                    f"本任务记录的是 {recorded}）：无法确认当前动作的真实状态，"
                    "已安全停下，请人工核对。"
                ),
                reason="stale_runtime_instance_event",
            )
            return None, False
        terminal = str(event).rsplit(".", 1)[-1]
        step.finished_at = self._clock()
        step.status = status or terminal.upper()
        # Phase 7F.4：外部终态已由事件确认 -> 派发状态收敛为 SETTLED（审计一致性）。
        # 不设这一句，这一步会永远停在 ACCEPTED，人工核对时看不出它其实已经结束了。
        step.dispatch_state = DispatchState.SETTLED.value
        step.result = dict(result or {})
        target.pending_action_id = ""
        target.pending_step_id = ""
        # Phase 7F.5.3 §三.A/§四.8：**终态任务保持终态** —— 取消就是取消、过期就是过期。
        # 终态事件只用来收敛这条动作的步骤证据，绝不把它改写回 RUNNING/WAITING_ACTION/PAUSED，
        # 也绝不重新进入派发或续跑流程。重复事件：结算后 pending 已清空 + 已记入 settled 环，
        # 因此是幂等空操作（不会再进暂存区）。
        terminal_record = target.state.terminal
        self._remember_settled_action(str(action_id))
        if terminal == "completed":
            step.state = StepState.SUCCEEDED
            await self._publish(TASK_STEP_SUCCEEDED, self._event_payload(target, step))
            await self._save(target, event=TASK_STEP_SUCCEEDED, step_id=step.step_id)
            if terminal_record:
                return target, False
        else:
            failure = classify_failure(code, event=str(event))
            step.state = StepState.CANCELLED if terminal == "cancelled" else StepState.FAILED
            step.failure = failure.value
            step.error = error
            step.message = error or code
            await self._publish(TASK_STEP_FAILED, self._event_payload(target, step))
            await self._save(target, event=TASK_STEP_FAILED, step_id=step.step_id)
            if terminal_record:
                # 终态任务的迟到终态事件：只收敛证据，绝不再进入正常派发/续跑。
                return target, False
            if target.pause_requested:
                target.pause_requested = False
                await self._enter(target, TaskState.PAUSED)
                await self._publish(TASK_PAUSED, self._event_payload(target, step))
                await self._save(target, event=TASK_PAUSED, step_id=step.step_id)
                return target, False
            await self._pause_after_failure(target, failure, step)
            return target, False
        if target.pause_requested:
            # §二十九：暂停等当前动作**自然结束**，绝不在动作中间硬切状态
            target.pause_requested = False
            await self._enter(target, TaskState.PAUSED)
            await self._publish(TASK_PAUSED, self._event_payload(target, step))
            await self._save(target, event=TASK_PAUSED, step_id=step.step_id)
            return target, False
        # 动作结束了：先把状态切回 RUNNING **并落盘**，再回到锁外继续（drive 由调用方执行）。
        # drive() 会从存储重新 load（那是 checkpoint 的真身），只改内存对象不落盘的话
        # 它会读到"还在 WAITING_ACTION"的旧快照，然后原地返回、任务永远停在等动作。
        await self._enter(target, TaskState.RUNNING)
        await self._save(target)
        return target, True

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
        inflight = next(
            (item for item in record.steps if item.dispatch_state == DispatchState.INTENT.value),
            None,
        )
        if inflight is not None:
            # Phase 7F.5.2 §三.6：派发意图已经落盘，但**外部结果未知**（可能已经发出去、
            # 也可能还没发出）。此时把任务直接标成「已暂停」就是假象：动作可能仍在外面跑，
            # 而记录里什么都没有。无法确认外部状态 → 如实进入受控暂停 + 人工核对（与 7F.4
            # 的歧义语义完全一致）：清授权、要求重新规划、尽力真停、绝不重发这一步。
            record.pause_requested = False
            await self._pause_ambiguous_dispatch(
                record,
                inflight,
                message=(
                    f"收到暂停（{reason}）时这一步的派发结果未知（可能已经发出）：为不出现"
                    "「任务已暂停而动作仍在跑」的假象，已受控暂停，请人工核对关联键 "
                    f"{inflight.dispatch_key or '-'}。"
                ),
                reason="paused_during_dispatch",
            )
            return record
        record.pause_requested = False
        await self._enter(record, TaskState.PAUSED)
        record.message = f"已暂停（{reason}）"
        # Phase 6A：任务**真的**进入 PAUSED 时要对外说一声 —— 世界活动（ActivityEpisode）靠
        # 这个事件把"执行任务"这条活动收成 INTERRUPTED（§四十二：任务 PAUSED 时活动不能还 ACTIVE）。
        # 注意：只有"真的进了 PAUSED"才发；上面那条"等动作结束后暂停"的延后请求**不发**
        # （那时任务还是 WAITING_ACTION，发出去就是谎报）。
        await self._publish(TASK_PAUSED, self._event_payload(record))
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
        if record.replan_required and record.state is TaskState.PAUSED:
            # §十七：重启/世界变化之后，必须先有新计划 + 新确认，不能拿旧计划接着跑
            raise TaskAuthorizationError(
                "任务需要重新规划（旧动作已失效或世界已变化），请先确认新计划",
                code="task.replan_required",
            )
        # Phase 7F.4：外部派发结果**未知**的步骤绝不能靠 resume() 重发。
        # 语义上必须区分两件事：
        #   * "崩溃后恢复同一次未决派发" -> 拒绝（下面这条，fail-closed）；
        #   * "原动作已经明确终止、用户显式恢复该步骤" -> 仍走既有语义（dispatch_state
        #     已是 SETTLED / ACCEPTED，不会被这条拦住）。
        unresolved = [item for item in record.steps if item.dispatch_unresolved]
        if unresolved:
            raise TaskAuthorizationError(
                "上一次动作派发的结果未知（外部可能已经执行），必须先由人核对，这里不会重新派发",
                code="task.dispatch_unresolved",
            )
        # 5A.1 §十七/§十九：比"授权还新不新"更要紧的是"那一步的世界动作还算不算数"。
        # 先判旧动作（只有真的没有在等事件时才算旧），再判授权。
        step = record.next_step()
        if (
            not record.pending_action_id
            and step is not None
            and step.action_id
            and step.state is StepState.WAITING_ACTION
        ):
            # 被暂停/重启时那个动作已经结束了：不能假装它还在（§六十四/§七十七）
            step.state = StepState.FAILED
            step.failure = TaskFailure.RUNTIME_RESTART.value
            step.message = "恢复时发现旧动作已经失效，需要重新规划"
            record.resume_note = "旧动作失效"
            await self._begin_replanning(
                record,
                reason=ReplanReason.RUNTIME_RESTART.value,
                step=step,
                failure=ReconcileOutcome.WORLD_CHANGED.value,
                message=step.message,
            )
            return record
        if record.authorization is None or not record.authorization.valid(now=self._clock()):
            if not is_user:
                # WebUI/后台只能"继续"一份还有效的授权，不能自己造一份新的
                raise TaskAuthorizationError(
                    "任务授权已过期，需要在对话里由用户重新确认",
                    code="task.resume_requires_user",
                )
            # §三十 + 5A.1 §十二：授权过期 → 回"等重新确认"（计划没变，不重规划）
            return await self._expire_authorization(record)
        facts = await self._world_facts_snapshot()
        if facts and facts.get("online") is False:
            record.message = "罐头不在世界里，先恢复不了"
            await self._save(record, event=TASK_PAUSED)
            return record
        record.pause_requested = False
        record.failure = ""
        await self._enter(record, TaskState.RUNNING)
        await self._publish(TASK_RESUMED, self._event_payload(record))
        await self._save(record, event=TASK_RESUMED)
        return await self.drive(task_id)

    async def cancel(self, task_id: str, *, reason: str = "user cancel") -> TaskRecord:
        """取消（§二十七/§二十八）：**先落盘终态真相，再经已有 minecraft_stop 停掉前台动作**。

        Phase 7F.5.3 Follow-up §三：旧顺序是「读快照 → await stop → 按旧快照算在途 → 整份保存」。
        stop 是长 await，期间 _settle() 完全可能把真实 action_id / pending 关联写进 store，而随后
        那次整份保存会把它覆盖回旧快照 —— 动作在外面跑、记录里却再也找不到关联。现在分三段：

        1. 在**按任务串行化**的临界区里重新读取最新记录，按**最新真相**判定在途并落盘终态；
        2. 出了临界区才调用 stop（外部调用绝不夹在临界区里等，避免与对账/身份观测互相等待）；
        3. stop 之后再进临界区**重新读取**最新记录，只**合并**停止证据 —— 绝不覆盖更新的关联。

        在途判定（Phase 7F.5.3 §三.B）：有真实 action_id → 保留 pending 关联（终态任务照样有
        事件匹配能力，等真实终态收敛）；只有派发意图 → 保留 INTENT 与证据，让 _settle 拿到真实
        action_id 后**再次**走既有 SAFE 控制面停止；确定没有在途派发时才按原样清空。
        """
        async with self._lifecycle(task_id):
            record = await self._require(task_id)
            if record.state.terminal:
                return record
            self._mark_inflight_for_terminal(record, reason=reason, prefix="cancel", mark_step=True)
            await self._cancel_record(record, reason=reason)
        # 终态真相已经落盘 —— 现在才去停，而且是在锁外。
        await self._stop_action()
        merged = await self._merge_stop_evidence(
            task_id, reason="cancel_during_dispatch", note=reason
        )
        # 持锁期间可能还有别的路径排过队（例如 _settle 的「接受后再停一次」）：一并执行。
        await self._flush_pending_stops(task_id)
        return merged

    async def expire(self, task_id: str, *, reason: str = "task ttl") -> TaskRecord:
        """过期（§三十一）：同样不能让在途动作脱离生命周期管理；先落盘终态再在锁外停。

        Phase 7F.5.3 Follow-up §三：顺序与 cancel() 完全一致（读最新 → 落盘终态 → 锁外 stop →
        重新读取并只合并停止证据），绝不拿旧快照覆盖 _settle() 刚写进去的真实 action_id。
        """
        async with self._lifecycle(task_id):
            record = await self._require(task_id)
            if record.state.terminal:
                return record
            self._mark_inflight_for_terminal(
                record, reason=reason, prefix="expired", mark_step=False
            )
            record.state = TaskState.EXPIRED
            record.message = reason
            record.updated_at = self._clock()
            await self._save(record, event=TASK_EXPIRED)
            await self._publish(TASK_EXPIRED, self._event_payload(record))
        await self._stop_action()
        merged = await self._merge_stop_evidence(
            task_id, reason="expired_during_dispatch", note=reason
        )
        # 持锁期间可能还有别的路径排过队：一并执行（同样只在锁外调用 SAFE 控制面）。
        await self._flush_pending_stops(task_id)
        return merged

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

    # ------------------------------------------------- 逐段到达验证（Phase 7F.3）

    def _leg_target_of(self, leg: Mapping[str, Any]) -> dict[str, float] | None:
        """把冻结计划里的一个路点目标规范成 _verify_position_within 认得的形状。"""
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

    def _leg_evidence(
        self,
        record: TaskRecord,
        leg: Mapping[str, Any],
        step: TaskStep | None,
        result: Mapping[str, Any],
    ) -> dict[str, Any]:
        """一段的**可审计**证据：路点编号 / 目标 / 实际坐标 / 距离 / 结果 / 失败原因 / 关联标识。"""
        position = result.get("position_within")
        position = dict(position) if isinstance(position, Mapping) else None
        ok = bool(result.get("ok"))
        return {
            "index": int(leg.get("index") or 0),
            "step_id": str(leg.get("step_id") or ""),
            "task_id": record.task_id,
            "action_id": str(getattr(step, "action_id", "") or ""),
            "target": dict(leg.get("position_within") or {}),
            "position": position,
            "distance": position.get("distance") if position else None,
            "verified": ok,
            "failure": "" if ok else TaskFailure.VERIFICATION.value,
            "message": str(result.get("message") or ""),
            "checked_at": self._clock(),
        }

    async def _fail_leg(
        self,
        record: TaskRecord,
        leg: Mapping[str, Any],
        step: TaskStep | None,
        result: Mapping[str, Any],
    ) -> None:
        """某一段的到达验证没过 → 整任务 FAILED(VERIFICATION)：后面的段一步都不许走。

        与终态校验（_finish）**同一套语义**：同一种证据失败就用同一个失败分类；
        绝不继续走完剩下的路点，也绝不把"没到"写成成功。
        """
        record.leg_verifications.append(self._leg_evidence(record, leg, step, result))
        record.state = TaskState.FAILED
        record.failure = TaskFailure.VERIFICATION.value
        record.failed_step = str(leg.get("step_id") or "")
        record.message = str(result.get("message") or "路线中途段的到达验证没通过")
        await self._save(record, event=TASK_FAILED)
        await self._publish(TASK_FAILED, self._event_payload(record))

    async def _ensure_legs_verified(self, record: TaskRecord, *, before_step: TaskStep) -> bool:
        """执行 before_step 之前，把所有**已走完但未验证**的段验证掉（Phase 7F.3）。

        返回 True = 可以继续执行 before_step；False = 已经因此终止。

        * "走完"的判据是**持久化的** StepState.SUCCEEDED，不是动作返回值，
          也不是"动作进了 RUNNING"；
        * 到达判定复用 7F.1 的 _verify_position_within（重读真实世界 + 有界等待 + fail-closed）；
        * 判据来自**冻结计划**里的逐段后置条件（进 plan_hash ⟹ 进授权指纹）；
        * 已记录过的段不重复验证（幂等：恢复/重放不会重复写证据，也不会重复执行动作）；
        * 单段计划（没有 route）时这里是纯粹的 no-op —— 7F.1/7F.2 行为一字不改。
        """
        legs = record.plan.expected_final_state.legs()
        if not legs:
            return True
        positions = {item.step_id: index for index, item in enumerate(record.steps, start=1)}
        current = positions.get(before_step.step_id, len(record.steps) + 1)
        for leg in legs:
            step_id = str(leg.get("step_id") or "")
            position = positions.get(step_id)
            if position is None or position >= current:
                continue  # 这一段还没走到，等它走完再说
            index = int(leg.get("index") or 0)
            step = record.plan.step(step_id)
            if step is None or step.state is not StepState.SUCCEEDED:
                await self._fail_leg(
                    record,
                    leg,
                    step,
                    {
                        "ok": False,
                        "position_within": None,
                        "message": (
                            f"路线第 {index} 段没有完成"
                            f"（{step.state.value if step is not None else '-'}），"
                            "不能继续后面的路点"
                        ),
                    },
                )
                return False
            if any(
                int(item.get("index") or 0) == index and item.get("verified") is True
                for item in record.leg_verifications
            ):
                continue  # 已经验证过：恢复/重放幂等
            target = self._leg_target_of(leg)
            if target is None:
                await self._fail_leg(
                    record,
                    leg,
                    step,
                    {
                        "ok": False,
                        "position_within": None,
                        "message": f"路线第 {index} 段的目标坐标不合法",
                    },
                )
                return False
            result = await self._verify_position_within(target)
            if result.get("ok"):
                record.leg_verifications.append(self._leg_evidence(record, leg, step, result))
                await self._save(record)
                continue
            await self._fail_leg(record, leg, step, result)
            return False
        return True

    async def _finish(self, record: TaskRecord) -> TaskRecord:
        """所有 required 步骤完成后：用 SAFE 读**重新验证**最终状态（§八十二/§八十三）。"""
        expected = record.plan.expected_final_state
        verification: dict[str, Any] = {"checked": False}
        if not expected.empty:
            verification = await self._verify_final_state(record, expected)
            if not verification.get("ok"):
                record.state = TaskState.FAILED
                record.failure = TaskFailure.VERIFICATION.value
                record.message = verification.get("message", "最终校验没通过")
                record.verification = verification
                await self._save(record, event=TASK_FAILED)
                await self._publish(TASK_FAILED, self._event_payload(record))
                return record
        record.verification = verification
        record.mark_plan_completed()
        record.state = TaskState.SUCCEEDED
        record.updated_at = self._clock()
        record.result["completed_steps"] = record.progress()["completed"]
        record.result["summary"] = self._success_summary(record, verification)
        await self._save(record, event=TASK_SUCCEEDED)
        await self._publish(TASK_SUCCEEDED, self._event_payload(record))
        return record

    async def _verify_final_state(
        self, record: TaskRecord, expected: ExpectedFinalState
    ) -> dict[str, Any]:
        """把全部后置条件（背包增量 + Phase 7F.1 有界探索的到达位置）合并成一份结论。

        每一项都用**新鲜的 SAFE 读**判定，绝不信动作返回值；任何一项不满足 → 整份失败。
        """
        result: dict[str, Any] = {"checked": True}
        ok = True
        messages: list[str] = []
        if expected.inventory_delta:
            after = await self._inventory_snapshot()
            before = record.result.get("started_inventory")
            inventory = self._verify_inventory(expected, before, after)
            result["inventory_delta"] = inventory.get("inventory_delta", {})
            result["expected"] = inventory.get("expected", {})
            if not inventory.get("ok"):
                ok = False
                messages.append(str(inventory.get("message") or "背包最终状态与预期不符"))
        if expected.position_within:
            position = await self._verify_position_within(expected.position_within)
            result["position_within"] = position.get("position_within")
            result["expected_position"] = position.get("expected_position")
            if not position.get("ok"):
                ok = False
                messages.append(str(position.get("message") or "位置最终状态与预期不符"))
        result["ok"] = ok
        result["message"] = "" if ok else "；".join(messages)
        return result

    async def _verify_position_within(self, expected: Mapping[str, Any]) -> dict[str, Any]:
        """有界探索的到达判定：重新读世界坐标，判断是否落在目标半径内（§八十三）。

        读的是感知层世界视图——它由 near 层按固定间隔轮询真实世界得到，刚到达时可能滞后
        最多一个轮询周期。因此这里对**同一份 SAFE 读**做**有界重试**等待感知收敛；到期仍在
        半径外就判失败（fail-closed）：既不放行"还没到"，也不因缓存延迟错杀真实到达。
        """
        target = {
            "x": float(expected["x"]),
            "y": float(expected["y"]),
            "z": float(expected["z"]),
            "radius": float(expected.get("radius", 2.0)),
        }
        attempts = max(1, int(getattr(self.config, "position_verify_attempts", 1)))
        interval = max(0.0, float(getattr(self.config, "position_verify_interval_seconds", 0.0)))
        result: dict[str, Any] = {
            "ok": False,
            "position_within": None,
            "expected_position": target,
            "message": "读不到世界状态（离线/不可用），无法确认是否到达探索目标",
        }
        for attempt in range(attempts):
            result = await self._read_position_once(target)
            if result.get("ok") or attempt == attempts - 1:
                break
            # 只有"读到了坐标但还没进半径"才值得等感知层刷新；离线/无坐标是硬失败，不重试。
            if result.get("position_within") is None:
                break
            await asyncio.sleep(interval)
        return result

    async def _read_position_once(self, target: Mapping[str, Any]) -> dict[str, Any]:
        """读一次 SAFE 世界视图并判定是否落在目标半径内（单次快照，不做等待）。"""
        world = await self._safe_read("minecraft_world", {})
        view = world.result if (world.ok and isinstance(world.result, Mapping)) else {}
        if not isinstance(view, Mapping) or view.get("online") is False:
            return {
                "ok": False,
                "position_within": None,
                "expected_position": target,
                "message": "读不到世界状态（离线/不可用），无法确认是否到达探索目标",
            }
        position = self._self_position_of(view)
        if position is None:
            return {
                "ok": False,
                "position_within": None,
                "expected_position": target,
                "message": "世界视图里没有罐头自己的坐标，无法确认是否到达探索目标",
            }
        dx = position["x"] - target["x"]
        dy = position["y"] - target["y"]
        dz = position["z"] - target["z"]
        distance = (dx * dx + dy * dy + dz * dz) ** 0.5
        ok = distance <= target["radius"]
        return {
            "ok": ok,
            "position_within": {
                "x": round(position["x"], 2),
                "y": round(position["y"], 2),
                "z": round(position["z"], 2),
                "distance": round(distance, 2),
            },
            "expected_position": target,
            "message": ""
            if ok
            else (
                f"最终位置与探索目标不符（距目标 {distance:.1f} 格，"
                f"允许 {target['radius']:.1f} 格）"
            ),
        }

    @staticmethod
    def _self_position_of(view: Mapping[str, Any]) -> dict[str, float] | None:
        """从世界视图里取罐头自己的坐标（扁平化结果 / service 原始语义视图两种形状都认）。"""
        candidate: object
        position = view.get("position")
        if isinstance(position, Mapping):
            candidate = position
        else:
            semantic = view.get("semantic")
            self_state = semantic.get("self") if isinstance(semantic, Mapping) else None
            candidate = self_state.get("position") if isinstance(self_state, Mapping) else None
        if not isinstance(candidate, Mapping):
            return None
        try:
            return {
                "x": float(candidate["x"]),
                "y": float(candidate["y"]),
                "z": float(candidate["z"]),
            }
        except (KeyError, TypeError, ValueError):
            return None

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

    async def _save(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        """**唯一**的 TaskRecord 写入口，并且带「终态吸收」（Phase 7F.5.3 Follow-up 2 §一）。

        为什么放在这一个点上：不是所有转换路径都能持锁完成（_run_step 里有外部调用、
        _finish/_fail/_settle_blocked/pause/resume/recover/replan 各自读-改-写）。但它们**都**
        经过这里。只要在写之前、在**同一把按任务锁内**复核一次持久化状态，就能保证：
        CANCELLED/EXPIRED 之后，任何旧快照都不可能把任务写回非终态。
        """
        task_id = str(record.task_id or "")
        if not task_id:
            record.updated_at = self._clock()
            await self._store.save(record, event=event, step_id=step_id, detail=detail)
            return
        lock = self._lifecycle(task_id)
        if lock.owned_by_current():
            await self._save_absorbing_terminal(record, event=event, step_id=step_id, detail=detail)
            return
        async with lock:
            await self._save_absorbing_terminal(record, event=event, step_id=step_id, detail=detail)

    async def _save_absorbing_terminal(
        self, record: TaskRecord, *, event: str = "", step_id: str = "", detail: str = ""
    ) -> None:
        """写之前复核持久化真相：**已终态就绝不写回非终态**（旧快照绝不复活任务）。

        检查与写入都在同一把按任务锁内完成，因此不存在 TOCTOU 窗口。被吸收时：采纳终态真相
        （状态 / 授权 / pending 关联 / 恢复证据），**保留调用方想写的步骤证据**，并把被丢弃的
        旧证据记进 recovery.detail 供人工核对 —— 绝不静默丢信息。
        """
        latest = await self._store.load(str(record.task_id))
        if latest is not None and latest.state.terminal and not record.state.terminal:
            evidence = record.recovery if isinstance(record.recovery, Mapping) else {}
            self._adopt_lifecycle(record, latest)
            if evidence:
                merged = dict(latest.recovery) if isinstance(latest.recovery, Mapping) else {}
                base = dict(merged.get("detail") or {})
                base["superseded_by_terminal_state"] = True
                base["superseded_evidence"] = dict(evidence)
                merged["detail"] = base
                record.recovery = merged
        record.updated_at = self._clock()
        await self._store.save(record, event=event, step_id=step_id, detail=detail)

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
            "source": record.source,
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
                        # Phase 7F.4：派发状态对外可见（人工核对歧义时要知道关联键）
                        "dispatch_state": item.dispatch_state,
                        "dispatch_generation": item.dispatch_generation,
                        "dispatch_key": item.dispatch_key,
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
            "leg_verifications": [dict(item) for item in record.leg_verifications],
            "result": record.result,
            "summary": self._fallback_summary(record),
            "replans": record.replans,
            "expires_at": record.expires_at,
            "rollback_supported": record.rollback_supported,
            "updated_at": record.updated_at,
            # ---- Phase 5A.1 §三十一：版本历史 / 恢复原因 / 授权时效 ----
            "plan_version": record.plan_version,
            "plan_status": record.plan_status,
            "plan_history": [item.to_payload() for item in record.plan_history],
            "replan_required": record.replan_required,
            "replan_reason": record.replan_reason or None,
            "recovery": dict(record.recovery) or None,
            "authorization": self.authorization_view(record),
            "authorization_expired_at": record.authorization_expired_at or None,
        }

    def authorization_view(self, record: TaskRecord) -> dict[str, Any] | None:
        """授权的只读投影（剩余时间由服务端算，UI 只倒计时 —— 不用客户端时钟）。"""
        authorization = record.authorization
        if authorization is None:
            return None
        now = self._clock()
        return {
            "plan_hash": authorization.plan_hash,
            "plan_version": int(authorization.plan_version or 0),
            "approved_at": authorization.approved_at,
            "expires_at": authorization.expires_at,
            "remaining_seconds": round(authorization.remaining_seconds(now=now), 1),
            "valid": authorization.valid(now=now),
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
        if record.replan_required and record.replan_reason:
            parts.append(f"需要重新规划（{record.replan_reason}）")
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

    async def replan(
        self,
        task_id: str,
        plan: TaskPlan,
        *,
        reason: str = ReplanReason.USER_REQUEST.value,
        observations: list[dict[str, Any]] | None = None,
    ) -> TaskRecord:
        """用一份**新计划**替换旧计划（Phase 5A.1 §四-§九/§二十二-§二十五）。

        第一原则：**旧 Plan 的授权绝不能授权新 Plan**。所以这里一律：

        1. 把旧版本标成 ``SUPERSEDED``（不覆盖、保留审计：谁在什么时候因为什么被取代）；
        2. 清空 ``authorization``（旧确认立刻失效）+ 前进 ``plan_version``；
        3. 只挂一条**新的**确认，摘要里明确写"这是新计划、为什么"（§九/§三十二）；
        4. 在用户确认之前，新计划里的任何 LOW/MEDIUM 都拿不到放行
           （``authorize_step`` 同时查 plan_hash 与 plan_version）。

        新计划必须由调用方完成 SAFE 观察后给出（Planner 的活），本方法不挑目标。
        """
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
        now = self._clock()
        old_hash = record.plan_hash
        if record.state is not TaskState.REPLANNING:
            await self._enter(record, TaskState.REPLANNING)
        record.supersede_plan(now=now, reason=str(reason))
        record.replans += 1
        record.plan = plan
        if observations:
            record.plan.observations = [dict(item) for item in observations]
        record.current_step = 0
        record.authorization = None
        record.replan_required = False
        record.replan_reason = str(reason)
        record.pending_action_id = ""
        record.pending_step_id = ""
        record.pause_requested = False
        record.failure = ""
        record.message = self.replan_headline(reason)
        version = record.record_plan(
            now=now,
            summary=self.summary_of(record),
            status=PlanStatus.PENDING_CONFIRMATION.value,
            reason=str(reason),
        )
        await self._publish(TASK_REPLANNING, self._event_payload(record))
        await self._save(
            record,
            event=TASK_REPLANNING,
            detail=(
                f"replan v{version.version} reason={reason} old={old_hash} new={record.plan_hash}"
            ),
        )
        await self._enter(record, TaskState.PENDING_CONFIRMATION)
        record.confirmation_id = await self._confirmations.request(
            task_id=record.task_id,
            session_id=record.session_id,
            user_id=record.user_id,
            risk=self._plan_risk(record.plan),
            plan_hash=record.plan_hash,
            arguments=self._confirmation_arguments(record),
            summary=self.replan_summary(record, reason=reason),
        )
        record.message = f"已重新规划（第 {version.version} 版，{reason}），等用户确认新计划"
        await self._save(record, event=TASK_CONFIRMATION_REQUIRED)
        await self._publish(TASK_CONFIRMATION_REQUIRED, self._event_payload(record))
        self._log_info(
            "task replanned",
            task_id=record.task_id,
            version=version.version,
            reason=reason,
            old_hash=old_hash,
            new_hash=record.plan_hash,
        )
        return record

    @staticmethod
    def replan_headline(reason: str) -> str:
        """重新规划时给用户看的第一句话（§三十二：不是"任务失败"，也不是"正在自动找替代目标"）。"""
        table = {
            ReplanReason.WORLD_CHANGED.value: "目标已经发生了变化。原计划已停止。",
            ReplanReason.TARGET_LOST.value: "原计划里的目标已经不见了。原计划已停止。",
            ReplanReason.RUNTIME_RESTART.value: (
                "罐头重启过，原来那一步已经不存在了。原计划已停止。"
            ),
            ReplanReason.AUTHORIZATION_EXPIRED.value: (
                "上一条许可到期了（计划没有变），需要你再确认一次。"
            ),
        }
        return table.get(str(reason or ""), "原计划已停止，需要重新规划。")

    def replan_summary(self, record: TaskRecord, *, reason: str = "") -> str:
        """新计划的确认摘要（§九：必须明确这是新计划；§六十七：列出全部将执行的动作）。"""
        body = summarize_plan(record.plan, label_of=self._label_of)
        lines = body.splitlines()
        # summarize_plan 的头两行是"任务：…"+空行（那是旧目标），这里换成版本头
        steps_block = "\n".join(lines[2:]).strip() if len(lines) > 2 else body
        return (
            f"{self.replan_headline(reason)}\n\n"
            f"新的计划（第 {record.plan_version} 版）：\n"
            f"{steps_block}\n\n"
            "需要重新确认。"
        )

    def _event_payload(self, record: TaskRecord, step: TaskStep | None = None) -> dict[str, Any]:
        """Task 事件载荷（§五十一：task_id / step_id / state / timestamp，不含 raw 世界状态）。

        ``event_seq`` 是**单调递增**的序号（跟着 checkpoint 一起落盘）：订阅方
        （QQ / WebUI / 插件）按 ``(task_id, event_seq)`` 幂等去重，绝不靠「这一轮
        应该只有一个事件」这种假设（Phase 5B §十五）。
        """
        record.event_seq = int(record.event_seq) + 1
        return {
            "task_id": record.task_id,
            "event_seq": record.event_seq,
            "source": record.source,
            "session_id": record.session_id,
            "step_id": step.step_id if step is not None else record.pending_step_id,
            "state": record.state.value,
            "step_state": step.state.value if step is not None else "",
            "tool": step.tool if step is not None else "",
            "failure": record.failure,
            "message": record.message,
            "plan_version": record.plan_version,
            "replan_reason": record.replan_reason,
            "recovery_outcome": str((record.recovery or {}).get("outcome") or ""),
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
