"""通用 Task Runtime 的领域模型（Phase 5A §六/§七/§八/§三十三/§三十八/§四十八）。

这里只有**编排层**的概念：Task / Step / Plan / Authorization / 失败分类。
它不认识 Mineflayer、不碰 HTTP、也不认识任何具体工具 —— 工具名与参数只是字符串与 JSON。

设计要点：

* 状态一律用**显式枚举**（禁止 ``None`` / ``"running"`` / ``"done"`` 这种散落字符串）；
* **Plan 与 Execution 分离**：Plan 是"准备做什么"（冻结、可审计、有 ``plan_hash``），
  Execution 是"现在做到哪里"（``current_step`` + 每个 step 的状态与结果）；
* 每个 step 同时保存 ``template_arguments`` 与 ``resolved_arguments``（§四十八）：
  确认时必须知道"到底要去哪个坐标"，而不是"走到刚才找到的那个地方"；
* 任何 Plan / Step 的变化都会改变 hash → 旧授权作废（§十二/§三十八）。
"""

from __future__ import annotations

import hashlib
import json
import math
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class TaskState(str, Enum):  # noqa: UP042 - 与项目其它面向 JSON 的枚举一致
    """Task 状态机（§六）。"""

    PLANNING = "PLANNING"
    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    RUNNING = "RUNNING"
    WAITING_ACTION = "WAITING_ACTION"
    WAITING_USER = "WAITING_USER"
    PAUSED = "PAUSED"
    REPLANNING = "REPLANNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_TASK_STATES


_TERMINAL_TASK_STATES = frozenset(
    {TaskState.SUCCEEDED, TaskState.FAILED, TaskState.CANCELLED, TaskState.EXPIRED}
)

#: 允许的状态转移（其余一律拒绝 —— 状态机是显式的，不靠"看起来对"）
ALLOWED_TASK_TRANSITIONS: dict[TaskState, frozenset[TaskState]] = {
    TaskState.PLANNING: frozenset(
        {TaskState.PENDING_CONFIRMATION, TaskState.FAILED, TaskState.CANCELLED}
    ),
    TaskState.PENDING_CONFIRMATION: frozenset(
        {
            TaskState.RUNNING,
            # 5A.1：还没确认也可以重新规划（用户改主意 / Planner 有了更好的计划）
            TaskState.REPLANNING,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
            TaskState.FAILED,
        }
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_ACTION,
            TaskState.WAITING_USER,
            # 5A.1 §十二：授权到期时任务停在安全边界 → 回到"等重新确认"
            TaskState.PENDING_CONFIRMATION,
            TaskState.PAUSED,
            TaskState.REPLANNING,
            TaskState.SUCCEEDED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.WAITING_ACTION: frozenset(
        {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.REPLANNING,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.WAITING_USER: frozenset(
        {
            TaskState.RUNNING,
            TaskState.PAUSED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
            TaskState.FAILED,
        }
    ),
    TaskState.PAUSED: frozenset(
        {
            TaskState.RUNNING,
            TaskState.PENDING_CONFIRMATION,
            TaskState.REPLANNING,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
            TaskState.FAILED,
        }
    ),
    TaskState.REPLANNING: frozenset(
        {
            TaskState.PENDING_CONFIRMATION,
            TaskState.PAUSED,
            TaskState.FAILED,
            TaskState.CANCELLED,
            TaskState.EXPIRED,
        }
    ),
    TaskState.SUCCEEDED: frozenset(),
    TaskState.FAILED: frozenset(),
    TaskState.CANCELLED: frozenset(),
    TaskState.EXPIRED: frozenset(),
}


class StepState(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """Step 状态机（§七）。"""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    WAITING_ACTION = "WAITING_ACTION"
    WAITING_CONFIRMATION = "WAITING_CONFIRMATION"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in _TERMINAL_STEP_STATES


_TERMINAL_STEP_STATES = frozenset(
    {StepState.SUCCEEDED, StepState.FAILED, StepState.SKIPPED, StepState.CANCELLED}
)


class DispatchState(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """一次步骤派发的**外部**可见状态（Phase 7F.4）。

    必须区分这四种状态，绝不把"不知道"当成"没执行"：

    * ``INTENT``：派发意图**已持久化**，但还不能证明外部 runtime 是否接受；
    * ``ACCEPTED``：外部已接受，``action_id`` 已知，正在等真实终态；
    * ``AMBIGUOUS``：外部是否接受**存在歧义**（响应丢失 / 恢复时仍不确定）
      → 安全停止 + 人工介入，绝不猜测、绝不重新派发；
    * ``SETTLED``：外部终态已知（成功/失败/取消/超时都算），可以安全继续。

    空串（``""``）= 这一步从未派发过。
    """

    INTENT = "INTENT"
    ACCEPTED = "ACCEPTED"
    AMBIGUOUS = "AMBIGUOUS"
    SETTLED = "SETTLED"


class TaskFailure(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """失败分类（§三十三）——UI / 模型需要知道"到底是哪种失败"。"""

    AUTHORIZATION = "AUTHORIZATION"
    VALIDATION = "VALIDATION"
    TARGET_LOST = "TARGET_LOST"
    ACTION_FAILED = "ACTION_FAILED"
    TIMEOUT = "TIMEOUT"
    CANCELLED = "CANCELLED"
    WORLD_CHANGED = "WORLD_CHANGED"
    OFFLINE = "OFFLINE"
    BUSY = "BUSY"
    INTERNAL = "INTERNAL"
    STALLED = "STALLED"
    RUNTIME_RESTART = "RUNTIME_RESTART"
    VERIFICATION = "VERIFICATION"
    #: Phase 7F.4：外部动作是否**已被接受**存在歧义 —— fail-closed，需人工介入。
    DISPATCH_AMBIGUOUS = "DISPATCH_AMBIGUOUS"


class PlanStatus(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """一份计划的版本状态（Phase 5A.1 §七：不要覆盖旧计划）。"""

    PENDING_CONFIRMATION = "PENDING_CONFIRMATION"
    ACTIVE = "ACTIVE"
    SUPERSEDED = "SUPERSEDED"
    COMPLETED = "COMPLETED"


class ReplanReason(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """为什么重新规划（Phase 5A.1 §八：不要所有情况都写成 REPLANNING）。"""

    TARGET_LOST = "TARGET_LOST"
    WORLD_CHANGED = "WORLD_CHANGED"
    RUNTIME_RESTART = "RUNTIME_RESTART"
    AUTHORIZATION_EXPIRED = "AUTHORIZATION_EXPIRED"
    USER_REQUEST = "USER_REQUEST"


class ReconcileOutcome(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """只读对账的结论（Phase 5A.1 §二十一：有限、只读的事实对账）。"""

    RECONCILED = "RECONCILED"
    WORLD_CHANGED = "WORLD_CHANGED"
    TARGET_LOST = "TARGET_LOST"
    TARGET_ALREADY_DONE = "TARGET_ALREADY_DONE"
    OFFLINE = "OFFLINE"
    UNKNOWN = "UNKNOWN"


class StepAuthorizationStatus(str, Enum):  # noqa: UP042 - 与 TaskState 一致（面向 JSON）
    """步骤授权状态（§十七）。"""

    PENDING = "PENDING"
    AUTHORIZED = "AUTHORIZED"
    CONSUMED = "CONSUMED"
    REVOKED = "REVOKED"

    @property
    def grants(self) -> bool:
        return self is StepAuthorizationStatus.AUTHORIZED


#: Task 事件名（§五十一）
TASK_CREATED = "task.created"
TASK_PLAN_READY = "task.plan_ready"
TASK_CONFIRMATION_REQUIRED = "task.confirmation_required"
TASK_STARTED = "task.started"
TASK_STEP_STARTED = "task.step_started"
TASK_STEP_WAITING = "task.step_waiting"
TASK_STEP_SUCCEEDED = "task.step_succeeded"
TASK_STEP_FAILED = "task.step_failed"
TASK_PAUSED = "task.paused"
TASK_REPLANNING = "task.replanning"
#: Phase 5A.1：进程重启后从 checkpoint 安全恢复（旧 action 一律失效）
TASK_RECOVERED = "task.recovered"
#: Phase 5A.1：授权到期 → 必须重新确认（绝不偷偷继续）
TASK_AUTHORIZATION_EXPIRED = "task.authorization_expired"
TASK_RESUMED = "task.resumed"
TASK_CANCELLED = "task.cancelled"
TASK_SUCCEEDED = "task.succeeded"
TASK_FAILED = "task.failed"
TASK_EXPIRED = "task.expired"

TASK_EVENTS: tuple[str, ...] = (
    TASK_CREATED,
    TASK_PLAN_READY,
    TASK_CONFIRMATION_REQUIRED,
    TASK_STARTED,
    TASK_STEP_STARTED,
    TASK_STEP_WAITING,
    TASK_STEP_SUCCEEDED,
    TASK_STEP_FAILED,
    TASK_PAUSED,
    TASK_REPLANNING,
    TASK_RECOVERED,
    TASK_AUTHORIZATION_EXPIRED,
    TASK_RESUMED,
    TASK_CANCELLED,
    TASK_SUCCEEDED,
    TASK_FAILED,
    TASK_EXPIRED,
)


# ------------------------------------------------------------------ 规范化 / hash


def canonical_json(value: Any) -> str:
    """Canonical JSON（排序键、紧凑分隔、非 ASCII 原样）——hash 的唯一序列化形式。"""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def digest(value: Any, *, length: int = 16) -> str:
    """``sha256(canonical_json(value))`` 的短形式（与确认门同风格）。"""
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:length]


def canonical_item_name(name: Any) -> str:
    """物品/方块名的规范化（§三十九：已 canonicalize 的工具不该产生伪 mismatch）。

    ``minecraft:stone_pickaxe`` 与 ``stone_pickaxe`` 是同一个东西 —— 参数的 canonical
    形式统一成**裸名 + 小写**（与 runtime 的语义投影口径一致）。
    """
    clean = str(name or "").strip().lower()
    return clean.replace("minecraft:", "", 1) if clean.startswith("minecraft:") else clean


def canonical_arguments(arguments: Mapping[str, Any] | None) -> dict[str, Any]:
    """把一步的参数规范化成 canonical 形式（用于 arguments_hash 与授权比对，§三十九）。

    只做**确定性**变换（排序键交给 ``canonical_json``）：
    * 名字类字段（item / block / expected_item / expected_block / expected_tool / tool /
      block_names）统一裸名；
    * 坐标统一成 int（能取整就取整，方块坐标本来就是整数）；
    * 其余原样 —— 绝不猜、绝不做语义等价（例如坐标不加偏移）。
    """
    raw: dict[str, Any] = dict(arguments or {})
    result: dict[str, Any] = {}
    name_keys = {
        "item",
        "tool",
        "expected_item",
        "expected_block",
        "expected_tool",
        "block",
        "recipe_id",
    }
    for key, value in raw.items():
        if key in name_keys and isinstance(value, str):
            result[key] = canonical_item_name(value)
        elif key == "block_names" and isinstance(value, (list, tuple)):
            result[key] = [canonical_item_name(item) for item in value]
        elif key in {"x", "y", "z"} and isinstance(value, float) and float(value).is_integer():
            result[key] = int(value)
        else:
            result[key] = value
    return result


def arguments_hash(arguments: Mapping[str, Any] | None) -> str:
    """一步参数的指纹（canonical 形式）。"""
    return digest(canonical_arguments(arguments), length=16)


# ------------------------------------------------------------------ 数据结构


@dataclass
class ArgumentReference:
    """参数里对前一步结果的引用（§四十七）。"""

    from_step: str
    path: str

    def to_payload(self) -> dict[str, str]:
        return {"from_step": self.from_step, "path": self.path}

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> ArgumentReference:
        return cls(
            from_step=str(payload.get("from_step") or ""),
            path=str(payload.get("path") or ""),
        )


def dispatch_key_of(task_id: str, plan_hash: str, step_id: str, generation: int) -> str:
    """一次**逻辑派发**的稳定关联键（Phase 7F.4）。

    由 ``task_id`` + 冻结 ``plan_hash`` + ``step_id`` + **持久化代次**共同决定：

    * 同代次的重复观察 / 重放 -> **同一个键**（可被识别为同一次派发）；
    * 用户显式发起的新一次执行尝试 -> 代次 +1 -> **新键**（可审计，绝不掩盖重复派发）；
    * 进程重启**不会**产生新键 —— 代次来自持久化记录，不来自内存计数器。

    当前 Minecraft runtime **不支持**客户端自带的幂等键（调用方无法按键去重或查询），
    因此这个键在本阶段的作用是**审计与人工核对的可追溯标识**，不是服务端去重保证；
    真正的安全来自"派发前落盘 + 歧义时 fail-closed"（见 app/tasks/runtime.py）。
    """
    return "dsp_" + digest(
        {
            "task_id": str(task_id),
            "plan_hash": str(plan_hash),
            "step_id": str(step_id),
            "generation": int(generation),
        },
        length=20,
    )


@dataclass
class TaskStep:
    """一步：准备调用的**已有**工具 + 参数（可能是模板，含引用）。"""

    step_id: str
    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    risk: str = ""
    state: StepState = StepState.PENDING
    depends_on: tuple[str, ...] = ()
    references: dict[str, ArgumentReference] = field(default_factory=dict)
    #: 执行前解析出来的**最终**参数（§四十八：审计证据，也是确认过的东西）
    resolved_arguments: dict[str, Any] | None = None
    #: 结果（§五十七）
    action_id: str = ""
    status: str = ""
    summary: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    started_at: float | None = None
    finished_at: float | None = None
    attempts: int = 0
    failure: str = ""
    error: str = ""
    message: str = ""
    #: §三十六：本阶段不做补偿动作（代码结构预留，绝不假装有 rollback）
    compensatable: bool = False
    #: Phase 7F.4：派发的**持久化关联信息** —— 必须在产生外部副作用**之前**落盘。
    #: 取值见 :class:``DispatchState``（空串 = 从未派发）。有它就等于"崩溃后知道
    #: 这一次派发可能已经发生"，没有它就只能靠猜（而那正是要修掉的歧义）。
    dispatch_state: str = ""
    #: 派发代次（0 = 从未派发）。每次**显式**执行尝试 +1；重启**不会**自动 +1。
    dispatch_generation: int = 0
    #: 这一代次的稳定关联键（:func:``dispatch_key_of``），用于审计与人工核对。
    dispatch_key: str = ""
    #: 派发意图落盘时刻（审计用）。
    dispatch_at: float = 0.0
    #: Phase 7F.5：**接受这次动作的 Runtime 实例**（进程生命周期内稳定）。
    #: 它只解决"这条事件属于哪个实例"的关联唯一性，**不是**幂等键、
    #: 也不提供任何服务端去重保证。旧记录（7F.5 之前）为空串 = 未知。
    dispatch_runtime_instance: str = ""

    @property
    def dispatch_unresolved(self) -> bool:
        """这一步的派发结果**未知** —— 绝不允许据此重新派发。"""
        return self.dispatch_state in {
            DispatchState.INTENT.value,
            DispatchState.AMBIGUOUS.value,
        }

    @property
    def effective_arguments(self) -> dict[str, Any]:
        """**执行**时真正要用的参数：解析过的优先（引用已经在 runtime 里解开了）。"""
        if self.resolved_arguments is not None:
            return dict(self.resolved_arguments)
        return dict(self.arguments)

    @property
    def template_hash(self) -> str:
        """授权比对用的指纹：**用户确认的那份模板**（§十二/§四十七）。

        带引用的步骤（例如"捡刚刚挖出来的那个掉落物"）在执行前才知道 entity_id；
        用户确认的是"按这条引用去取"，所以授权绑模板，解析结果另存审计
        （``resolved_arguments``），并且解析只允许沿着声明过的路径走。
        """
        return arguments_hash(self.arguments)

    @property
    def effective_hash(self) -> str:
        """解析后参数的指纹（审计用；不含引用时与 :attr:`template_hash` 相同）。"""
        return arguments_hash(self.effective_arguments)

    def to_payload(self) -> dict[str, Any]:
        return {
            "step_id": self.step_id,
            "tool": self.tool,
            "risk": self.risk,
            "state": self.state.value,
            "depends_on": list(self.depends_on),
            "template_arguments": dict(self.arguments),
            "resolved_arguments": (
                dict(self.resolved_arguments) if self.resolved_arguments else None
            ),
            "arguments_hash": self.effective_hash,
            "references": {key: ref.to_payload() for key, ref in self.references.items()},
            "action_id": self.action_id,
            "status": self.status,
            "summary": self.summary,
            "result": dict(self.result),
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "attempts": self.attempts,
            "failure": self.failure,
            "error": self.error,
            "message": self.message,
            "compensatable": self.compensatable,
            "dispatch_state": self.dispatch_state,
            "dispatch_generation": self.dispatch_generation,
            "dispatch_key": self.dispatch_key,
            "dispatch_at": self.dispatch_at,
            "dispatch_runtime_instance": self.dispatch_runtime_instance,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskStep:
        raw_resolved = payload.get("resolved_arguments")
        references: dict[str, ArgumentReference] = {}
        raw_references = payload.get("references")
        if isinstance(raw_references, Mapping):
            references = {
                str(key): ArgumentReference.from_payload(value)
                for key, value in raw_references.items()
                if isinstance(value, Mapping)
            }
        return cls(
            step_id=str(payload.get("step_id") or ""),
            tool=str(payload.get("tool") or ""),
            arguments=dict(payload.get("template_arguments") or {}),
            risk=str(payload.get("risk") or ""),
            state=_enum_or(StepState, payload.get("state"), StepState.PENDING),
            depends_on=tuple(str(item) for item in (payload.get("depends_on") or ())),
            references=references,
            resolved_arguments=dict(raw_resolved) if isinstance(raw_resolved, Mapping) else None,
            action_id=str(payload.get("action_id") or ""),
            status=str(payload.get("status") or ""),
            summary=str(payload.get("summary") or ""),
            result=dict(payload.get("result") or {}),
            started_at=payload.get("started_at"),
            finished_at=payload.get("finished_at"),
            attempts=int(payload.get("attempts") or 0),
            failure=str(payload.get("failure") or ""),
            error=str(payload.get("error") or ""),
            message=str(payload.get("message") or ""),
            compensatable=bool(payload.get("compensatable", False)),
            dispatch_state=str(payload.get("dispatch_state") or ""),
            dispatch_generation=int(payload.get("dispatch_generation") or 0),
            dispatch_key=str(payload.get("dispatch_key") or ""),
            dispatch_at=float(payload.get("dispatch_at") or 0.0),
            dispatch_runtime_instance=str(payload.get("dispatch_runtime_instance") or ""),
        )


@dataclass
class PlanVersion:
    """一份冻结计划的历史记录（Phase 5A.1 §七：不要覆盖旧 Plan）。

    ``reason`` 只对被取代的版本有意义：它是"为什么这一版不再有效"
    （``WORLD_CHANGED`` / ``RUNTIME_RESTART`` / …），审计与 UI 都靠它解释。
    """

    version: int
    plan_hash: str
    created_at: float
    summary: str = ""
    steps: list[dict[str, Any]] = field(default_factory=list)
    confirmed_at: float = 0.0
    superseded_at: float = 0.0
    reason: str = ""
    status: str = PlanStatus.PENDING_CONFIRMATION.value

    def to_payload(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "plan_hash": self.plan_hash,
            "created_at": self.created_at,
            "summary": self.summary,
            "steps": [dict(item) for item in self.steps],
            "confirmed_at": self.confirmed_at,
            "superseded_at": self.superseded_at,
            "reason": self.reason,
            "status": self.status,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> PlanVersion:
        raw_steps = payload.get("steps")
        return cls(
            version=int(payload.get("version") or 0),
            plan_hash=str(payload.get("plan_hash") or ""),
            created_at=float(payload.get("created_at") or 0.0),
            summary=str(payload.get("summary") or ""),
            steps=[dict(item) for item in (raw_steps or []) if isinstance(item, Mapping)],
            confirmed_at=float(payload.get("confirmed_at") or 0.0),
            superseded_at=float(payload.get("superseded_at") or 0.0),
            reason=str(payload.get("reason") or ""),
            status=str(payload.get("status") or PlanStatus.PENDING_CONFIRMATION.value),
        )


#: Phase 7F.1：探索到达判定默认半径（格）。与 move_to 的 GoalNear 半径对齐，
#: 后置条件用的是**重新读到的世界坐标**，不是动作返回值。
DEFAULT_ARRIVE_RADIUS = 2.0

#: Phase 7F.3：一条有界路线**最多**允许的移动段数（第一版硬上限）。
#: 超过就在计划校验阶段被拒绝，绝不"凑段数"。
MAX_EXPLORE_LEGS = 3

#: Phase 7F.3：**整条路线**的总距离硬上限（格）—— 沿用 Phase 7F.1 的单段预算，
#: 绝不把单段 24 格悄悄变成每段 24 格（那是 3 倍的授权扩张）。
MAX_EXPLORE_ROUTE_DISTANCE = 24.0

#: Phase 7F.3：两个路点之间的最小间距（格）。比它更近就算同一个目标，
#: 计划直接拒绝 —— 不允许用重复路点凑段数。
MIN_LEG_SEPARATION = 1.0


def finite_number(value: Any) -> float | None:
    """把不可信输入转成**有限浮点数**，否则 fail-closed 返回 ``None``。

    这是数值进入**冻结计划**与**记忆**之前的唯一判定入口（Phase 7F.2.2 / 7F.2.2.1 / 7F.3）：

    * ``bool`` 一律拒绝（它是 ``int`` 子类，会被静默当成 1/0）；
    * 只接受 ``int`` / ``float`` / ``str``（有限数字字符串是既有兼容行为）；
    * ``float()`` 的三种失败都接住：``TypeError`` / ``ValueError`` /
      **``OverflowError``**（``float(10 ** 400)`` 抛的就是它 —— 曾经以"抛异常"替代 fail-closed）；
    * 结果必须通过 ``math.isfinite()``（``NaN`` / ``±Infinity`` 一律拒绝）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _xyz_of(value: Any) -> dict[str, float] | None:
    """从 ``{"x","y","z"}`` 形状里读出三个有限坐标（读不出 → None，fail-closed）。"""
    if not isinstance(value, Mapping):
        return None
    coords: dict[str, float] = {}
    for axis in ("x", "y", "z"):
        number = finite_number(value.get(axis))
        if number is None:
            return None
        coords[axis] = number
    return coords


def _route_of(payload: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """解析冻结路线的 ``route`` 结构（Phase 7F.3）。

    三种结果，必须区分清楚：

    * **不存在** → ``None``（单段计划，行为与 7F.1/7F.2 完全一致）；
    * **完整且合法** → 规范化后的 route；
    * **存在但不合法** → ``{"invalid": True, "reason": ...}``。

    第三种绝不能被当成"没有路线"：那会让一份被篡改/损坏的多段计划**静默退化**成
    "不做逐段校验"，等于把 fail-closed 变成 fail-open。计划校验会因此直接拒绝它。
    """
    if not isinstance(payload, Mapping):
        return None
    raw = payload.get("route")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        return {"invalid": True, "reason": "route 不是对象"}
    start = _xyz_of(raw.get("start"))
    if start is None:
        return {"invalid": True, "reason": "route.start 坐标不合法"}
    budget = finite_number(raw.get("budget"))
    if budget is None or budget <= 0:
        return {"invalid": True, "reason": "route.budget 不合法"}
    raw_legs = raw.get("legs")
    if not isinstance(raw_legs, list) or not raw_legs:
        return {"invalid": True, "reason": "route.legs 缺失或为空"}
    legs: list[dict[str, Any]] = []
    for item in raw_legs:
        if not isinstance(item, Mapping):
            return {"invalid": True, "reason": "route.legs 里有非对象元素"}
        try:
            index = int(item["index"])
        except (KeyError, TypeError, ValueError):
            return {"invalid": True, "reason": "route.legs.index 不合法"}
        if index < 1 or index != len(legs) + 1:
            return {"invalid": True, "reason": f"route.legs.index 不连续（{index}）"}
        step_id = str(item.get("step_id") or "")
        if not step_id:
            return {"invalid": True, "reason": f"route.legs[{index}] 缺少 step_id"}
        # 注意：路线目标用**严格有限**判定（_xyz_of + finite_number），不复用
        # _position_within_of —— 后者对 NaN/Infinity 只做 float() 不做 isfinite。
        # 路线目标决定执行顺序（后一段依赖前一段的到达证据），必须严格有限。
        raw_target = item.get("position_within")
        if not isinstance(raw_target, Mapping):
            return {"invalid": True, "reason": f"route.legs[{index}] 缺少目标坐标"}
        coords = _xyz_of(raw_target)
        radius = finite_number(raw_target.get("radius"))
        if coords is None or radius is None or radius <= 0:
            return {"invalid": True, "reason": f"route.legs[{index}] 目标坐标/半径不合法"}
        target: dict[str, float] = {**coords, "radius": radius}
        distance = finite_number(item.get("distance"))
        if distance is None or distance < 0:
            return {"invalid": True, "reason": f"route.legs[{index}] distance 不合法"}
        legs.append(
            {
                "index": index,
                "step_id": step_id,
                "position_within": target,
                "distance": distance,
            }
        )
    return {"start": start, "budget": budget, "legs": legs}


def _position_within_of(payload: Mapping[str, Any] | None) -> dict[str, float] | None:
    """从 payload 里读出探索后置条件 ``position_within``（读不出 → None，fail-closed）。"""
    raw = (payload or {}).get("position_within") if isinstance(payload, Mapping) else None
    if not isinstance(raw, Mapping):
        return None
    try:
        return {
            "x": float(raw["x"]),
            "y": float(raw["y"]),
            "z": float(raw["z"]),
            "radius": float(raw.get("radius", DEFAULT_ARRIVE_RADIUS)),
        }
    except (KeyError, TypeError, ValueError):  # pragma: no cover - 计划校验会先拦掉
        return None


@dataclass
class ExpectedFinalState:
    """Plan 定义的"最终应该看到什么"（§八十三）——用 SAFE 读重新验证，不看历史结果。"""

    #: 物品名（裸名）→ 至少增加多少
    inventory_delta: dict[str, int] = field(default_factory=dict)
    #: Phase 7F.1：有界探索的后置条件 —— 用 SAFE 读（minecraft_world）确认
    #: "罐头最终在 (x,y,z) 的 radius 格内"。不是靠动作返回值自述，是重新读世界。
    position_within: dict[str, float] | None = None
    #: Phase 7F.3：**有界多段路线**的逐段后置条件（冻结在计划里，进 plan_hash）。
    #: ``{"start": {x,y,z}, "budget": 24.0, "legs": [{"index", "step_id",
    #: "position_within", "distance"}, ...]}``。单段计划**不带**这个字段（保持逐字节兼容）。
    route: dict[str, Any] | None = None

    def to_payload(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "inventory_delta": {k: v for k, v in sorted(self.inventory_delta.items())}
        }
        if self.position_within:
            payload["position_within"] = {
                "x": float(self.position_within["x"]),
                "y": float(self.position_within["y"]),
                "z": float(self.position_within["z"]),
                "radius": float(self.position_within.get("radius", DEFAULT_ARRIVE_RADIUS)),
            }
        if self.route:
            payload["route"] = self.route
        return payload

    def legs(self) -> list[dict[str, Any]]:
        """合法的逐段后置条件（没有路线 / 路线非法 → 空列表）。"""
        if not isinstance(self.route, Mapping) or self.route.get("invalid"):
            return []
        items = self.route.get("legs")
        return (
            [dict(item) for item in items if isinstance(item, Mapping)]
            if isinstance(items, list)
            else []
        )

    def leg_for_step(self, step_id: str) -> dict[str, Any] | None:
        """这一步是不是某一段的移动步？是就返回那一段的冻结后置条件。"""
        for leg in self.legs():
            if str(leg.get("step_id") or "") == str(step_id):
                return leg
        return None

    @property
    def route_invalid(self) -> bool:
        """路线存在但被判定非法（计划必须被拒绝，绝不当成"没有路线"）。"""
        return isinstance(self.route, Mapping) and bool(self.route.get("invalid"))

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> ExpectedFinalState:
        raw = (payload or {}).get("inventory_delta") if isinstance(payload, Mapping) else None
        delta: dict[str, int] = {}
        if isinstance(raw, Mapping):
            for key, value in raw.items():
                try:
                    delta[canonical_item_name(key)] = int(value)
                except (TypeError, ValueError):  # pragma: no cover - 计划校验会先拦掉
                    continue
        return cls(
            inventory_delta=delta,
            position_within=_position_within_of(payload),
            route=_route_of(payload),
        )

    @property
    def empty(self) -> bool:
        return not self.inventory_delta and self.position_within is None and self.route is None


@dataclass
class TaskPlan:
    """准备做什么（冻结后不可静默修改，§十一/§十二）。"""

    objective: str
    steps: list[TaskStep] = field(default_factory=list)
    expected_final_state: ExpectedFinalState = field(default_factory=ExpectedFinalState)
    #: 观察阶段用过的 SAFE 工具（只用于审计：模型当时看到了什么）
    observations: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        return {
            "objective": self.objective,
            "steps": [step.to_payload() for step in self.steps],
            "expected_final_state": self.expected_final_state.to_payload(),
        }

    def hash_payload(self) -> dict[str, Any]:
        """参与 ``plan_hash`` 的**只有**真正决定世界操作的东西。

        观察结果与逐步的运行状态不进 hash（否则"再查一次世界"就会让授权失效），
        但每一步的 tool + **最终参数** 都进（参数变了 = 另一个计划，§十二/§四十七）。
        """
        return {
            "objective": self.objective,
            "steps": [
                {
                    "step_id": step.step_id,
                    "tool": step.tool,
                    "arguments": canonical_arguments(step.arguments),
                    "depends_on": list(step.depends_on),
                }
                for step in self.steps
            ],
            "expected_final_state": self.expected_final_state.to_payload(),
        }

    @property
    def plan_hash(self) -> str:
        return digest(self.hash_payload(), length=20)

    @property
    def action_steps(self) -> list[TaskStep]:
        """LOW/MEDIUM 步骤（真正会改世界/移动的）。"""
        return [
            step for step in self.steps if step.risk in {"LOW", "MEDIUM", "HIGH", "DESTRUCTIVE"}
        ]

    def step(self, step_id: str) -> TaskStep | None:
        for item in self.steps:
            if item.step_id == step_id:
                return item
        return None

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskPlan:
        raw_steps = payload.get("steps")
        steps = [
            TaskStep.from_payload(item)
            for item in (raw_steps if isinstance(raw_steps, list) else [])
            if isinstance(item, Mapping)
        ]
        raw_observations = payload.get("observations")
        return cls(
            objective=str(payload.get("objective") or ""),
            steps=steps,
            expected_final_state=ExpectedFinalState.from_payload(
                payload.get("expected_final_state") if isinstance(payload, Mapping) else None
            ),
            observations=[
                dict(item) for item in (raw_observations or []) if isinstance(item, Mapping)
            ],
        )


@dataclass
class TaskAuthorization:
    """整份冻结计划的用户授权（§十五）。"""

    task_id: str
    user_id: str
    session_id: str
    plan_hash: str
    approved_at: float
    expires_at: float
    #: 这份授权批的是**第几版**计划（5A.1 §四十四：v1 的确认绝不能授权 v2）
    plan_version: int = 0

    def valid(self, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        return moment < self.expires_at

    def remaining_seconds(self, *, now: float | None = None) -> float:
        moment = time.time() if now is None else now
        return max(0.0, self.expires_at - moment)

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "plan_hash": self.plan_hash,
            "plan_version": self.plan_version,
            "approved_at": self.approved_at,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any] | None) -> TaskAuthorization | None:
        if not isinstance(payload, Mapping) or not payload:
            return None
        return cls(
            task_id=str(payload.get("task_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            plan_hash=str(payload.get("plan_hash") or ""),
            approved_at=float(payload.get("approved_at") or 0.0),
            expires_at=float(payload.get("expires_at") or 0.0),
            plan_version=int(payload.get("plan_version") or 0),
        )


@dataclass
class TaskStepAuthorization:
    """单个步骤的授权事实（§十七）——执行前必须与冻结计划完全一致。"""

    task_id: str
    step_id: str
    tool: str
    arguments_hash: str
    risk: str
    plan_hash: str
    status: StepAuthorizationStatus = StepAuthorizationStatus.PENDING

    def matches(self, *, tool: str, arguments_hash_value: str, plan_hash: str) -> bool:
        return (
            self.tool == tool
            and self.arguments_hash == arguments_hash_value
            and self.plan_hash == plan_hash
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "step_id": self.step_id,
            "tool": self.tool,
            "arguments_hash": self.arguments_hash,
            "risk": self.risk,
            "plan_hash": self.plan_hash,
            "status": self.status.value,
        }


@dataclass
class TaskRecord:
    """一个 Task 的完整状态（= 要持久化的 checkpoint，§八/§九）。"""

    task_id: str
    session_id: str
    user_id: str
    origin: str
    objective: str
    plan: TaskPlan
    state: TaskState = TaskState.PLANNING
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    current_step: int = 0
    authorization: TaskAuthorization | None = None
    failure: str = ""
    message: str = ""
    failed_step: str = ""
    result: dict[str, Any] = field(default_factory=dict)
    verification: dict[str, Any] = field(default_factory=dict)
    #: Phase 7F.3：逐段到达验证的**持久化证据**（每段一条；崩溃/重放后仍可审计）。
    #: 形状：``{"index", "step_id", "action_id", "target", "position", "distance",
    #: "verified", "failure", "message", "checked_at"}``。**绝不放在内存变量里** ——
    #: 否则进程恢复就丢掉了"这一段到底有没有到达"的依据。
    leg_verifications: list[dict[str, Any]] = field(default_factory=list)
    replans: int = 0
    no_progress: int = 0
    last_progress_key: str = ""
    confirmation_id: str = ""
    pending_action_id: str = ""
    pending_step_id: str = ""
    #: 暂停请求：有前台动作时先记下来，等它自然结束再真正 PAUSED（§二十九）
    pause_requested: bool = False
    expires_at: float = 0.0
    # ---- Phase 5A.1：版本历史 / 重规划 / 恢复（§七/§八/§十五/§三十一）----
    #: 每一版计划都留档（绝不覆盖旧 Plan）
    plan_history: list[PlanVersion] = field(default_factory=list)
    #: 为什么需要重新规划（ReplanReason；空 = 不需要）
    replan_reason: str = ""
    #: 需要重新规划（世界变了 / 重启后旧动作失效）：在重新确认前不许再动世界
    replan_required: bool = False
    #: 只读对账的结果与恢复审计（reason / outcome / at / detail / message）
    recovery: dict[str, Any] = field(default_factory=dict)
    #: 授权到期时刻（审计用；0 = 没到期过）
    authorization_expired_at: float = 0.0
    #: Phase 5B §十五：单调递增的事件序号（事件载荷里带出去，订阅方按它幂等去重）
    event_seq: int = 0
    #: Phase 5B §二十九/§三十：任务从哪个入口进来（qq / minecraft_chat / webui / smoke）
    source: str = ""
    #: §三十五/§三十六：本阶段**不提供**通用 rollback（世界修改不是事务）
    rollback_supported: bool = False
    resume_note: str = ""

    @property
    def steps(self) -> list[TaskStep]:
        return self.plan.steps

    @property
    def plan_hash(self) -> str:
        return self.plan.plan_hash

    @property
    def plan_version(self) -> int:
        """当前是第几版计划（没有历史记录时算第 1 版）。"""
        if self.plan_history:
            return int(self.plan_history[-1].version)
        return 1

    @property
    def plan_status(self) -> str:
        if self.plan_history:
            return str(self.plan_history[-1].status)
        return PlanStatus.PENDING_CONFIRMATION.value

    def plan_step_snapshot(self) -> list[dict[str, Any]]:
        """审计用的逐步快照（不含运行时状态，只记"这一版计划是什么"）。"""
        return [
            {
                "step_id": step.step_id,
                "tool": step.tool,
                "risk": step.risk,
                "arguments": canonical_arguments(step.arguments),
            }
            for step in self.steps
        ]

    def record_plan(
        self,
        *,
        now: float,
        summary: str = "",
        status: str = PlanStatus.PENDING_CONFIRMATION.value,
        reason: str = "",
    ) -> PlanVersion:
        """给当前计划开一条历史记录（新版本）。"""
        version = PlanVersion(
            version=self.plan_version + 1 if self.plan_history else 1,
            plan_hash=self.plan_hash,
            created_at=now,
            summary=summary,
            steps=self.plan_step_snapshot(),
            status=status,
            reason=reason,
        )
        self.plan_history.append(version)
        return version

    def current_plan_version(self) -> PlanVersion | None:
        return self.plan_history[-1] if self.plan_history else None

    def mark_plan_confirmed(self, *, now: float) -> None:
        version = self.current_plan_version()
        if version is not None:
            version.status = PlanStatus.ACTIVE.value
            version.confirmed_at = now

    def supersede_plan(self, *, now: float, reason: str) -> PlanVersion | None:
        """把当前这版标成被取代（**不删除**，审计要看得见）。"""
        version = self.current_plan_version()
        if version is None:
            return None
        version.status = PlanStatus.SUPERSEDED.value
        version.superseded_at = now
        version.reason = str(reason or version.reason)
        return version

    def mark_plan_completed(self) -> None:
        version = self.current_plan_version()
        if version is not None:
            version.status = PlanStatus.COMPLETED.value

    def step(self, step_id: str) -> TaskStep | None:
        return self.plan.step(step_id)

    def next_step(self) -> TaskStep | None:
        """下一个还没做完的步骤（顺序执行；已完成/跳过的跳过）。"""
        for index in range(self.current_step, len(self.steps)):
            step = self.steps[index]
            if step.state in {StepState.SUCCEEDED, StepState.SKIPPED}:
                continue
            self.current_step = index
            return step
        return None

    def progress(self) -> dict[str, int]:
        done = len([s for s in self.steps if s.state in {StepState.SUCCEEDED, StepState.SKIPPED}])
        return {"completed": done, "total": len(self.steps)}

    def step_authorization(self, step: TaskStep) -> TaskStepAuthorization:
        """由冻结计划推导出的步骤授权事实（绝不额外放宽；绑的是模板指纹）。

        ``plan_hash`` 取的是**授权记录里存的那一份**（= 用户确认时看到的计划），
        不是现算的 —— 否则"计划在确认之后被改过"就永远比不出来（自比自恒等）。
        """
        status = (
            StepAuthorizationStatus.AUTHORIZED
            if self.authorization is not None and self.authorization.valid()
            else StepAuthorizationStatus.PENDING
        )
        if step.state is StepState.SUCCEEDED:
            status = StepAuthorizationStatus.CONSUMED
        approved_plan_hash = (
            self.authorization.plan_hash if self.authorization is not None else self.plan_hash
        )
        return TaskStepAuthorization(
            task_id=self.task_id,
            step_id=step.step_id,
            tool=step.tool,
            arguments_hash=step.template_hash,
            risk=step.risk,
            plan_hash=approved_plan_hash,
            status=status,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "session_id": self.session_id,
            "user_id": self.user_id,
            "origin": self.origin,
            "objective": self.objective,
            "state": self.state.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "current_step": self.current_step,
            "plan": self.plan.to_payload(),
            "plan_hash": self.plan_hash,
            "authorization": self.authorization.to_payload() if self.authorization else None,
            "failure": self.failure,
            "message": self.message,
            "failed_step": self.failed_step,
            "result": dict(self.result),
            "verification": dict(self.verification),
            "leg_verifications": [dict(item) for item in self.leg_verifications],
            "replans": self.replans,
            "no_progress": self.no_progress,
            "confirmation_id": self.confirmation_id,
            "pending_action_id": self.pending_action_id,
            "pending_step_id": self.pending_step_id,
            "pause_requested": self.pause_requested,
            "expires_at": self.expires_at,
            "rollback_supported": self.rollback_supported,
            "resume_note": self.resume_note,
            "plan_history": [item.to_payload() for item in self.plan_history],
            "replan_reason": self.replan_reason,
            "replan_required": self.replan_required,
            "recovery": dict(self.recovery),
            "authorization_expired_at": self.authorization_expired_at,
            "event_seq": self.event_seq,
            "source": self.source,
        }

    @classmethod
    def from_payload(cls, payload: Mapping[str, Any]) -> TaskRecord:
        return cls(
            task_id=str(payload.get("task_id") or ""),
            session_id=str(payload.get("session_id") or ""),
            user_id=str(payload.get("user_id") or ""),
            origin=str(payload.get("origin") or ""),
            objective=str(payload.get("objective") or ""),
            plan=TaskPlan.from_payload(payload.get("plan") or {}),
            state=_enum_or(TaskState, payload.get("state"), TaskState.PLANNING),
            created_at=float(payload.get("created_at") or time.time()),
            updated_at=float(payload.get("updated_at") or time.time()),
            current_step=int(payload.get("current_step") or 0),
            authorization=TaskAuthorization.from_payload(payload.get("authorization")),
            failure=str(payload.get("failure") or ""),
            message=str(payload.get("message") or ""),
            failed_step=str(payload.get("failed_step") or ""),
            result=dict(payload.get("result") or {}),
            verification=dict(payload.get("verification") or {}),
            leg_verifications=[
                dict(item)
                for item in (payload.get("leg_verifications") or [])
                if isinstance(item, Mapping)
            ],
            replans=int(payload.get("replans") or 0),
            no_progress=int(payload.get("no_progress") or 0),
            confirmation_id=str(payload.get("confirmation_id") or ""),
            pending_action_id=str(payload.get("pending_action_id") or ""),
            pending_step_id=str(payload.get("pending_step_id") or ""),
            pause_requested=bool(payload.get("pause_requested", False)),
            expires_at=float(payload.get("expires_at") or 0.0),
            rollback_supported=bool(payload.get("rollback_supported", False)),
            resume_note=str(payload.get("resume_note") or ""),
            plan_history=[
                PlanVersion.from_payload(item)
                for item in (payload.get("plan_history") or [])
                if isinstance(item, Mapping)
            ],
            replan_reason=str(payload.get("replan_reason") or ""),
            replan_required=bool(payload.get("replan_required", False)),
            recovery=dict(payload.get("recovery") or {}),
            authorization_expired_at=float(payload.get("authorization_expired_at") or 0.0),
            event_seq=int(payload.get("event_seq") or 0),
            source=str(payload.get("source") or ""),
        )


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _enum_or(enum_type: Any, value: Any, fallback: Any) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return fallback
