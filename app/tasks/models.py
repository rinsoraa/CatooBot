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
        {TaskState.RUNNING, TaskState.CANCELLED, TaskState.EXPIRED, TaskState.FAILED}
    ),
    TaskState.RUNNING: frozenset(
        {
            TaskState.WAITING_ACTION,
            TaskState.WAITING_USER,
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
        )


@dataclass
class ExpectedFinalState:
    """Plan 定义的"最终应该看到什么"（§八十三）——用 SAFE 读重新验证，不看历史结果。"""

    #: 物品名（裸名）→ 至少增加多少
    inventory_delta: dict[str, int] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {"inventory_delta": {k: v for k, v in sorted(self.inventory_delta.items())}}

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
        return cls(inventory_delta=delta)

    @property
    def empty(self) -> bool:
        return not self.inventory_delta


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

    def valid(self, *, now: float | None = None) -> bool:
        moment = time.time() if now is None else now
        return moment < self.expires_at

    def to_payload(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "user_id": self.user_id,
            "session_id": self.session_id,
            "plan_hash": self.plan_hash,
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
    replans: int = 0
    no_progress: int = 0
    last_progress_key: str = ""
    confirmation_id: str = ""
    pending_action_id: str = ""
    pending_step_id: str = ""
    #: 暂停请求：有前台动作时先记下来，等它自然结束再真正 PAUSED（§二十九）
    pause_requested: bool = False
    expires_at: float = 0.0
    #: §三十五/§三十六：本阶段**不提供**通用 rollback（世界修改不是事务）
    rollback_supported: bool = False
    resume_note: str = ""

    @property
    def steps(self) -> list[TaskStep]:
        return self.plan.steps

    @property
    def plan_hash(self) -> str:
        return self.plan.plan_hash

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
            "replans": self.replans,
            "no_progress": self.no_progress,
            "confirmation_id": self.confirmation_id,
            "pending_action_id": self.pending_action_id,
            "pending_step_id": self.pending_step_id,
            "pause_requested": self.pause_requested,
            "expires_at": self.expires_at,
            "rollback_supported": self.rollback_supported,
            "resume_note": self.resume_note,
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
            replans=int(payload.get("replans") or 0),
            no_progress=int(payload.get("no_progress") or 0),
            confirmation_id=str(payload.get("confirmation_id") or ""),
            pending_action_id=str(payload.get("pending_action_id") or ""),
            pending_step_id=str(payload.get("pending_step_id") or ""),
            pause_requested=bool(payload.get("pause_requested", False)),
            expires_at=float(payload.get("expires_at") or 0.0),
            rollback_supported=bool(payload.get("rollback_supported", False)),
            resume_note=str(payload.get("resume_note") or ""),
        )


def new_task_id() -> str:
    return f"task_{uuid.uuid4().hex[:12]}"


def _enum_or(enum_type: Any, value: Any, fallback: Any) -> Any:
    try:
        return enum_type(value)
    except (TypeError, ValueError):
        return fallback
