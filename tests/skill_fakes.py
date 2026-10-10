"""Phase 7E 测试夹具：用**真实** TaskRecord / TaskPlan / 真实职责边界造学习证据。

这里的"假"只有两处：SAFE 观察通道（不联网）与任务运行时（不真的动世界）。
任务记录本身是**真的** ``TaskRecord``（含最终计划版本、步骤状态、步骤结果、运行时自己的
后验 ``verification``），所以资格门看到的东西与生产一致。
"""

from __future__ import annotations

from typing import Any

from app.tasks.models import (
    ExpectedFinalState,
    PlanStatus,
    StepState,
    TaskPlan,
    TaskRecord,
    TaskState,
    TaskStep,
)
from app.tasks.skill_service import SkillService, ToolsSnapshot, tools_signature_of

T0 = 1_800_000_000.0
SERVER = "mc-1a2b3c"
CHARACTER = "罐头@deadbeef"
BLOCK = "oak_log"
DROP = "oak_log"
POSITION = {"x": -993, "y": 81, "z": 646}
DIG_MS = 600

#: Phase 7D Follow-up：挖掘归因夹具（与 ``minecraft_runtime/dig_attribution.js`` 的载荷同形）。
#: 真实的 ``minecraft_dig`` 步骤结果里就有这一份（随动作终态事件回报），
#: 所以夹具必须有能力表达"自证 / 外部 / 歧义 / 缺字段 / 方块还在"五种真实形态。
#: **严格**自证（schema 2）：直接执行者证据 = 本客户端观察到自己的实体在目标坐标的破坏进度
DIG_ATTRIBUTION_SELF: dict[str, Any] = {
    "kind": "dig_attribution",
    "schema": 2,
    "action_id": "act_dig_1",
    "target": dict(POSITION),
    "dimension": "minecraft:overworld",
    "world_effect": "BLOCK_REMOVED",
    "attribution": "SELF_CONFIRMED",
    "assurance": "STRICT",
    "confirm_basis": "self_break_progress",
    "reason_code": "self_break_progress_observed",
    "strict_self_proof": True,
}
#: **推断**自证（schema 2）：只有自身挖掘生命周期 + 时序推断，不算严格自证
DIG_ATTRIBUTION_INFERRED: dict[str, Any] = {
    **DIG_ATTRIBUTION_SELF,
    "attribution": "SELF_INFERRED",
    "assurance": "INFERRED",
    "confirm_basis": "dig_lifecycle_timing",
    "reason_code": "self_dig_completed_at_expected_time",
    "strict_self_proof": False,
}
DIG_ATTRIBUTION_PRESETS: dict[str, dict[str, Any] | None] = {
    "self": DIG_ATTRIBUTION_SELF,
    "self_inferred": DIG_ATTRIBUTION_INFERRED,
    #: 历史过宽契约：SELF_CONFIRMED + 时序依据 + strict_self_proof=True（必须被新门禁拒绝）
    "legacy_self": {
        **DIG_ATTRIBUTION_SELF,
        "schema": 1,
        "confirm_basis": "dig_lifecycle_timing",
        "reason_code": "self_dig_completed_at_expected_time",
    },
    #: strict_self_proof 用字符串（类型转换不得通过）
    "strict_not_bool": {**DIG_ATTRIBUTION_SELF, "strict_self_proof": "true"},
    #: 未知 schema（不可读版本 → fail-closed）
    "bad_schema": {**DIG_ATTRIBUTION_SELF, "schema": 99},
    "external": {
        **DIG_ATTRIBUTION_SELF,
        "attribution": "EXTERNAL_INDICATED",
        "confirm_basis": "external_break_progress",
        "reason_code": "external_break_progress_observed",
        "strict_self_proof": False,
    },
    "conflict": {
        **DIG_ATTRIBUTION_SELF,
        "attribution": "AMBIGUOUS",
        "confirm_basis": "none",
        "reason_code": "conflicting_evidence",
        "strict_self_proof": False,
    },
    "ambiguous": {
        **DIG_ATTRIBUTION_SELF,
        "attribution": "AMBIGUOUS",
        "confirm_basis": "none",
        "reason_code": "block_removed_before_self_dig_completion",
        "strict_self_proof": False,
    },
    "remains": {**DIG_ATTRIBUTION_SELF, "world_effect": "BLOCK_REMAINS"},
    "unknown_effect": {**DIG_ATTRIBUTION_SELF, "world_effect": "UNKNOWN"},
    "missing": None,
}


def resource_plan(
    *,
    block: str = BLOCK,
    drop: str = DROP,
    position: dict[str, int] | None = None,
    with_equip: bool = True,
    with_move: bool = True,
) -> TaskPlan:
    """5A 资源模板的等价计划（与 ``plan_resource_task`` 同形）。"""

    spot = dict(position or POSITION)
    steps: list[TaskStep] = []
    if with_equip:
        steps.append(
            TaskStep(
                step_id="step_1",
                tool="minecraft_equip",
                arguments={"item": "netherite_axe"},
                risk="MEDIUM",
            )
        )
    if with_move:
        steps.append(
            TaskStep(
                step_id="step_2",
                tool="minecraft_move_to",
                arguments=dict(spot),
                risk="LOW",
            )
        )
    dig_id = f"step_{len(steps) + 1}"
    steps.append(
        TaskStep(
            step_id=dig_id,
            tool="minecraft_dig",
            arguments={
                **spot,
                "expected_block": block,
                "expected_tool": "netherite_axe",
            },
            risk="MEDIUM",
        )
    )
    drop_step = f"step_{len(steps) + 1}"
    steps.append(
        TaskStep(step_id=drop_step, tool="minecraft_dropped_items", arguments={}, risk="SAFE")
    )
    steps.append(
        TaskStep(
            step_id=f"step_{len(steps) + 1}",
            tool="minecraft_pickup_item",
            arguments={
                "entity_id": {"from_step": drop_step, "path": "items.0.entity_id"},
                "expected_item": drop,
            },
            risk="MEDIUM",
        )
    )
    steps.append(
        TaskStep(
            step_id=f"step_{len(steps) + 1}", tool="minecraft_inventory", arguments={}, risk="SAFE"
        )
    )
    return TaskPlan(
        objective="去挖一块橡木并捡回来",
        steps=steps,
        expected_final_state=ExpectedFinalState(inventory_delta={drop: 1}),
        observations=[
            {
                "tool": "minecraft_find_blocks",
                "arguments": {"block_names": [block], "max_distance": 16, "max_results": 8},
                "result": {
                    "ok": True,
                    "matches": [{"block": {"name": block}, "position": dict(spot)}],
                },
                "summary": "找到了",
            },
            {
                "tool": "minecraft_dig_capability",
                "arguments": dict(spot),
                "result": {
                    "ok": True,
                    "position": dict(spot),
                    "block": {"name": block},
                    "can_dig": False,
                    "dig_time_ms": DIG_MS,
                    "reason": "too_far",
                },
                "summary": "够不着",
            },
            {
                "tool": "minecraft_inventory",
                "arguments": {},
                "result": {
                    "ok": True,
                    "held_item": {"name": "netherite_axe", "count": 1},
                    "items": [],
                },
                "summary": "背包",
            },
        ],
    )


def resource_record(
    *,
    task_id: str = "task_skill_1",
    state: TaskState = TaskState.SUCCEEDED,
    plan: TaskPlan | None = None,
    verification: dict[str, Any] | None = None,
    step_states: dict[str, StepState] | None = None,
    dig_elapsed_ms: float | None = None,
    plan_status: str = PlanStatus.COMPLETED.value,
    replans: int = 0,
    superseded_first: bool = False,
    block: str = BLOCK,
    drop: str = DROP,
    position: dict[str, int] | None = None,
    objective: str = "去挖一块橡木并捡回来",
    #: 7E.1.1：任务**一个步骤都没开始**（等确认就被取消 / 还没跑就失败/过期）
    never_started: bool = False,
    #: 运行时给出的失败分类（TaskFailure 的值；"" = 没有）
    failure: str = "",
    #: Phase 7D Follow-up：挖掘归因形态（预设名见 DIG_ATTRIBUTION_PRESETS，或直接给载荷）
    dig_attribution: dict[str, Any] | str | None = "self",
) -> TaskRecord:
    """一条**真实结构**的任务记录（默认：挖一块原木 → 捡回来 → 背包复核成功）。"""

    spot = dict(position or POSITION)
    plan = plan or resource_plan(block=block, drop=drop, position=spot)
    plan.objective = objective
    record = TaskRecord(
        task_id=task_id,
        session_id="private:2731431246",
        user_id="2731431246",
        origin="user",
        objective=objective,
        plan=plan,
        state=state,
        created_at=T0,
        updated_at=T0 + 60,
        source="qq",
        replans=replans,
    )
    if superseded_first:
        # 版本 A（被取代，含一个失败步骤）→ 版本 B（最终成功）：E4 的夹具
        record.replans = max(record.replans, 1)
        old = TaskPlan(
            objective=objective,
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_dig",
                    arguments={**spot, "expected_block": block},
                    risk="MEDIUM",
                    state=StepState.FAILED,
                    failure="WORLD_CHANGED",
                )
            ],
            expected_final_state=ExpectedFinalState(inventory_delta={drop: 1}),
        )
        record.plan = old
        record.record_plan(now=T0, summary="第 1 版", status=PlanStatus.PENDING_CONFIRMATION.value)
        record.supersede_plan(now=T0 + 1, reason="world_changed")
        record.plan = plan
        record.record_plan(
            now=T0 + 2, summary="第 2 版", status=PlanStatus.PENDING_CONFIRMATION.value
        )
    elif not record.plan_history:
        record.record_plan(now=T0, summary="第 1 版", status=PlanStatus.PENDING_CONFIRMATION.value)
    if record.plan_history:
        record.plan_history[-1].status = plan_status
    if failure:
        record.failure = str(failure)
    if never_started:
        # 真实形态：还在等用户确认（或刚建完就没跑）→ 所有步骤 PENDING、没有时间戳/动作
        for step in record.steps:
            step.state = StepState.PENDING
            step.action_id = ""
            step.status = ""
            step.result = {}
            step.started_at = None
            step.finished_at = None
        record.current_step = 0
        return record
    states = dict(step_states or {})
    for step in record.steps:
        step.state = states.get(step.step_id, StepState.SUCCEEDED)
        if step.tool == "minecraft_pickup_item" and step.state is StepState.SUCCEEDED:
            step.result = {"ok": True, "picked": drop}
        step.started_at = T0 + 1
        step.finished_at = T0 + 2
    for step in record.steps:
        if step.tool == "minecraft_dig":
            # 真实 dig 是 detached 动作：有 action_id + 真实的 started/finished 间隔
            step.action_id = step.action_id or "act_dig_1"
            step.status = "SUCCEEDED"
            step.result = {
                "position": dict(spot),
                "block_before": block,
                "block_after": "air",
            }
            payload = (
                DIG_ATTRIBUTION_PRESETS.get(dig_attribution, DIG_ATTRIBUTION_SELF)
                if isinstance(dig_attribution, str)
                else dig_attribution
            )
            if payload:
                step.result["attribution"] = dict(payload)
    if dig_elapsed_ms is not None:
        for step in record.steps:
            if step.tool == "minecraft_dig":
                step.finished_at = (step.started_at or T0) + dig_elapsed_ms / 1000.0
    if verification is not None:
        record.verification = verification
    elif state is TaskState.SUCCEEDED:
        record.verification = {"inventory_delta": {drop: 1}}
    record.current_step = len(record.steps)
    return record


# ------------------------------------------------------------------ 观察通道


class FakeObserve:
    """最小 SAFE 观察替身：find_blocks / inventory / dig_capability（可配置失败）。"""

    def __init__(
        self,
        *,
        block: str = BLOCK,
        position: dict[str, int] | None = None,
        inventory: list[dict[str, Any]] | None = None,
        held: dict[str, Any] | None = None,
        dig_reason: str = "too_far",
        dig_block: str = "",
        fail: bool = False,
        find_matches: int = 1,
    ) -> None:
        self.block = block
        self.position = dict(position or POSITION)
        self.inventory = list(inventory if inventory is not None else [])
        self.held = held if held is not None else {"name": "netherite_axe", "count": 1}
        self.dig_reason = dig_reason
        self.dig_block = dig_block or block
        self.fail = fail
        self.find_matches = int(find_matches)
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((tool, dict(arguments)))
        if self.fail:
            raise RuntimeError("observe down")
        if tool == "minecraft_find_blocks":
            matches = [
                {
                    "block": {"name": self.block},
                    "position": dict(self.position),
                    "distance": {"raw": 3.0},
                }
                for _ in range(max(0, self.find_matches))
            ]
            return type("R", (), {"result": {"ok": True, "matches": matches}, "summary": "found"})()
        if tool == "minecraft_world":
            return type(
                "R",
                (),
                {
                    "result": {
                        "semantic": {
                            "self": {
                                "position": {"x": 0, "y": 80, "z": 0},
                                "held_item": "netherite_axe",
                            },
                            "players": [],
                        }
                    },
                    "summary": "world",
                },
            )()
        if tool == "minecraft_inventory":
            return type(
                "R",
                (),
                {
                    "result": {"ok": True, "held_item": self.held, "items": self.inventory},
                    "summary": "inv",
                },
            )()
        if tool == "minecraft_dig_capability":
            return type(
                "R",
                (),
                {
                    "result": {
                        "ok": True,
                        "position": dict(self.position),
                        "block": {"name": "" if self.dig_reason == "air" else self.dig_block},
                        "can_dig": False,
                        "dig_time_ms": DIG_MS,
                        "reason": self.dig_reason,
                    },
                    "summary": "probe",
                },
            )()
        raise AssertionError(f"意外工具 {tool}")


# ------------------------------------------------------------------ 服务装配

TOOLS = (
    "minecraft_dig",
    "minecraft_equip",
    "minecraft_move_to",
    "minecraft_dropped_items",
    "minecraft_pickup_item",
    "minecraft_inventory",
    "minecraft_find_blocks",
    "minecraft_dig_capability",
    "minecraft_follow_player",
    "minecraft_stop",
)
RISKS = {
    "minecraft_dig": "MEDIUM",
    "minecraft_equip": "MEDIUM",
    "minecraft_pickup_item": "MEDIUM",
    "minecraft_move_to": "LOW",
    "minecraft_follow_player": "LOW",
    "minecraft_dropped_items": "SAFE",
    "minecraft_inventory": "SAFE",
    "minecraft_find_blocks": "SAFE",
    "minecraft_dig_capability": "SAFE",
    "minecraft_stop": "SAFE",
}
SCHEMAS = {
    "minecraft_dig": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "z": {"type": "integer"},
            "expected_block": {"type": "string"},
            "expected_tool": {"type": "string"},
        },
        "required": ["x", "y", "z", "expected_block"],
    },
    "minecraft_equip": {
        "type": "object",
        "properties": {"item": {"type": "string"}},
        "required": ["item"],
    },
    "minecraft_move_to": {
        "type": "object",
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "z": {"type": "integer"},
        },
        "required": ["x", "y", "z"],
    },
    "minecraft_pickup_item": {
        "type": "object",
        "properties": {
            "entity_id": {"type": "integer"},
            "expected_item": {"type": "string"},
        },
        "required": ["entity_id"],
    },
    "minecraft_dropped_items": {"type": "object", "properties": {}},
    "minecraft_inventory": {"type": "object", "properties": {}},
    "minecraft_find_blocks": {
        "type": "object",
        "properties": {
            "block_names": {"type": "array"},
            "max_distance": {"type": "integer"},
            "max_results": {"type": "integer"},
        },
    },
}


def classify(objective: str) -> str:
    """与生产装配同形的确定性分类器（这里只认两种方块，够测试用）。"""

    text = str(objective or "")
    if "橡木" in text or "原木" in text:
        return f"resource:{BLOCK}:{DROP}"
    if "铁矿" in text:
        return "resource:iron_ore:raw_iron"
    return "text:" + " ".join(text.split()).lower()[:120]


def tools_snapshot(*, signature: str = "", names: tuple[str, ...] = TOOLS) -> ToolsSnapshot:
    return ToolsSnapshot(
        registered=frozenset(names),
        risks=dict(RISKS),
        signature=signature or tools_signature_of(names, RISKS, SCHEMAS.get),
    )


def make_service(
    *,
    store: Any = None,
    observe: Any = None,
    server_id: str = SERVER,
    character_id: str = CHARACTER,
    snapshot: ToolsSnapshot | None = None,
    allowed_risks: Any = None,
    config: Any = None,
    clock: Any = None,
) -> SkillService:
    from app.tasks.skill_store import InMemorySkillStore

    return SkillService(
        store=store or InMemorySkillStore(),
        config=config,
        character_id=character_id,
        observe=observe,
        server_id=lambda: server_id,
        tools_snapshot=(lambda: snapshot) if snapshot is not None else (lambda: tools_snapshot()),
        classify=classify,
        schema_of=SCHEMAS.get,
        allowed_risks=allowed_risks or (lambda _risk: True),
        clock=clock or (lambda: T0 + 10),
    )


class FakeConfirmations:
    """TaskRuntime 需要的最小确认门（生产实现是 Minecraft 的 ConfirmationStore）。"""

    def __init__(self) -> None:
        self.pending: dict[str, dict[str, Any]] = {}
        self._seq = 0

    async def request(self, *, task_id: str, **kwargs: Any) -> str:
        self._seq += 1
        cid = f"conf-{self._seq}"
        self.pending[cid] = {"task_id": task_id, **kwargs}
        return cid

    async def consume(self, confirmation_id: str, **kwargs: Any) -> tuple[bool, str]:
        entry = self.pending.get(str(confirmation_id))
        if entry is None:
            return False, "confirmation_mismatch"
        return True, ""

    async def cancel(self, confirmation_id: str) -> None:
        self.pending.pop(str(confirmation_id), None)


def build_task_runtime() -> Any:
    from app.tasks.models import TaskState
    from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
    from app.tasks.store import InMemoryTaskStore

    inventory_reads = {"n": 0}
    dig_results: dict[str, dict[str, Any]] = {}
    dig_seq = {"n": 0}

    async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
        # 语义投影的形状与真实 MinecraftService 一致（引用路径 ``items.0.entity_id`` 要能解出来）。
        # 第一次读背包是"开工快照"（空的），之后才真的多出一块 —— 这样最终校验才有意义。
        if tool == "minecraft_dropped_items":
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={"items": [{"entity_id": 42, "item": {"name": DROP, "count": 1}}]},
            )
        if tool == "minecraft_inventory":
            inventory_reads["n"] += 1
            items = [] if inventory_reads["n"] == 1 else [{"name": DROP, "count": 1}]
            return TaskInvocation(
                ok=True, status="SUCCEEDED", result={"items": items, "held_item": None}
            )
        if tool == "minecraft_dig":
            # 真实 minecraft_dig 是**持续型动作**：启动即返回 RUNNING + action_id，
            # 终态（含归因载荷）由 action 事件异步送达。夹具必须同形，否则严格归因的
            # action_id 绑定形同虚设（7D.3 §2.2）。
            dig_seq["n"] += 1
            action_id = f"act_dig_{dig_seq['n']}"
            position = {axis: arguments.get(axis) for axis in ("x", "y", "z")}
            dig_results[action_id] = {
                "position": position,
                "block_before": str(arguments.get("expected_block") or BLOCK),
                "block_after": "air",
                "attribution": {
                    **DIG_ATTRIBUTION_SELF,
                    "action_id": action_id,
                    "target": dict(position),
                },
            }
            return TaskInvocation(ok=True, status="RUNNING", action_id=action_id)
        return TaskInvocation(ok=True, status="SUCCEEDED", result={})

    class _AutoDigRuntime(TaskRuntime):
        """把持续型的 dig 自动"按时"完成，让既有资源任务测试能一次跑完。"""

        async def _finish_pending_dig(self, record: Any) -> Any:
            while (
                record is not None
                and record.state is TaskState.WAITING_ACTION
                and getattr(record, "pending_action_id", "")
            ):
                action_id = str(record.pending_action_id)
                result = dig_results.get(action_id)
                if result is None:
                    return record
                step = record.step(record.pending_step_id) if record.pending_step_id else None
                if step is not None:
                    # 模拟真实 started→finished 间隔（预期 600ms），否则会被"时长过短"辅助信号拦下
                    step.started_at = self._clock() - (DIG_MS / 1000.0)
                    await self._save(record)
                record = await self.on_action_event(
                    action_id=action_id,
                    event="minecraft.action.completed",
                    status="SUCCEEDED",
                    result=dict(result),
                )
            return record

        async def confirm_and_start(self, task_id: str, **kwargs: Any) -> Any:
            record = await super().confirm_and_start(task_id, **kwargs)
            return await self._finish_pending_dig(record)

    return _AutoDigRuntime(
        store=InMemoryTaskStore(),
        invoke=invoke,
        confirmations=FakeConfirmations(),
        config=TaskConfig(ttl_seconds=600.0),
        risk_of=lambda tool: str(RISKS.get(tool, "")),
        is_registered=lambda tool: tool in TOOLS,
        schema_of=SCHEMAS.get,
    )


__all__ = [
    "BLOCK",
    "CHARACTER",
    "DIG_MS",
    "DROP",
    "FakeConfirmations",
    "FakeObserve",
    "build_task_runtime",
    "POSITION",
    "RISKS",
    "SCHEMAS",
    "SERVER",
    "T0",
    "TOOLS",
    "classify",
    "make_service",
    "resource_plan",
    "resource_record",
    "tools_snapshot",
]
