"""Phase 7E §7.2/§7.3：学习资格门与归一化（**纯函数**，没有执行句柄）。

输入是**真实持久化记录**的事实（TaskRecord + 运行时自己的后置验证 + 学习期 SAFE 复核结论），
输出是一个结构化的"方法候选"或一个稳定的拒绝原因码。这里不做任何 IO、不认识 Mineflayer、
不调用任何工具 —— 一切都由调用方（技能服务）用既有 SAFE 通道算好再传进来。

判定顺序与原因码（§7.2 要求"可解释"）：

``not_terminal`` → ``unknown_state`` → ``not_succeeded:<state>`` → ``final_plan_not_completed``
→ ``no_steps`` → ``unsupported_step:<tool>`` → ``risk_mismatch:<tool>``
→ ``step_not_run:<step>:<state>`` → ``world_change_unverified:<step>``
→ ``ambiguous_world_change:<step>`` → ``no_postcondition`` → 全部通过 = QUALIFIED。

**7D §10.4 dig 歧义的隔离策略（只隔离，不修 7D）**：

* 主力规则：**改动世界的步骤必须有独立后置条件**（运行时自己的新鲜 SAFE 校验，或学习期
  的新鲜 SAFE 复核）。没有 → ``world_change_unverified``，记 ``AMBIGUOUS`` 证据、不计正向。
* 附加信号：计划期 ``dig_capability`` 给出过预期挖掘时长、而实测步骤时长不到它的一半
  → 方块很可能在罐头挖到之前就已经被别人移除 → ``ambiguous_world_change``（``AMBIGUOUS``）。
* **已知残余窗口（不假装解决）**：若另一名玩家"真的把方块挖掉"（产生掉落物）恰好与罐头的
  挖掘重叠，现有 ``step.result`` 里没有归因字段，无法区分；本阶段不改 7D 执行路径，
  该窗口作为 7D 后续结果完整性缺陷单独跟踪。
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from app.tasks.models import StepState, TaskFailure, TaskState
from app.tasks.skill import (
    EvidenceVerdict,
    SkillPrecondition,
    SkillSlot,
    SkillStep,
    SkillSuccessCriteria,
    skill_fingerprint,
    task_state_enum,
)

#: 会**改动世界/背包**的工具：它们的"成功"必须由独立后置条件背书（§7.2 第 5 条）。
#: 刻意**不含** ``minecraft_equip``：换手改的是"罐头自己拿什么"，世界没变，而它的效果
#: 由下一步的 ``expected_tool`` 前置（4I 的"执行瞬间主手必须拿着它"）在执行期强制 ——
#: 为一个"拿在手里"编造独立后置条件等于虚构感知能力。
WORLD_CHANGING_TOOLS = frozenset(
    {
        "minecraft_dig",
        "minecraft_place",
        "minecraft_craft",
        "minecraft_pickup_item",
        "minecraft_inventory_move",
        "minecraft_container_transfer",
    }
)

#: 这些工具**预期效果就是背包变化**，因此运行时的独立后验（新鲜 SAFE 背包读的
#: ``inventory_delta``）可以直接为它们背书（§7.2 第 5 条的主力证据）。
INVENTORY_VERIFIED_TOOLS = frozenset(
    {
        "minecraft_dig",
        "minecraft_craft",
        "minecraft_pickup_item",
        "minecraft_inventory_move",
        "minecraft_container_transfer",
    }
)

#: 这些工具的效果**不在背包里**（例如"方块真的出现在世界上"），必须由**步骤级**后置条件
#: 背书；本阶段还没有对应的验证器 → 一律判歧义，绝不靠"工具返回 ok"冒充（§7.2 第 5 条）。
STEP_POSTCONDITION_ONLY_TOOLS = frozenset({"minecraft_place"})

#: 归一化时会被替换成槽位的参数键（一次性执行值，绝不写进模板）
_POSITION_KEYS = ("x", "y", "z")
#: 槽位名：既有引用（``from_step``）之外的动态参数都落在这里
POSITION_SLOT = "target_position"
ENTITY_SLOT = "target_entity"


@dataclass(frozen=True)
class LearningInput:
    """资格门的输入：全部是"事实"，不含任何可执行对象。"""

    record: Any
    character_id: str = ""
    server_id: str = ""
    tools_signature: str = ""
    #: 当前真实注册的工具（来自同一个工具运行时，绝不靠猜）
    registered_tools: frozenset[str] = frozenset()
    #: 当前真实风险表（``ACTION_RISK``；技能无权修改风险）
    risk_table: Mapping[str, str] = field(default_factory=dict)
    #: 步骤级后置条件结论（学习期用既有 SAFE 观察复核）：``step_id -> (ok, kind)``
    postconditions: Mapping[str, tuple[bool, str]] = field(default_factory=dict)
    #: 运行时**自己的**独立后验是否覆盖了这份计划的预期效果（``_finish`` 用新鲜 SAFE 背包读
    #: 算出的 ``inventory_delta``）—— 它是"背包类"步骤的主力证据
    task_effect_verified: bool = False
    #: 计划期已知的预期挖掘时长：``step_id -> dig_time_ms``（缺失就不做时长判定）
    dig_expected_ms: Mapping[str, int] = field(default_factory=dict)
    #: 实测时长不到预期的这个比例 → 判定为"挖到之前方块就没了"（保守信号）
    dig_short_ratio: float = 0.5
    #: 方法类键（由调用方用既有 block_for/DROP_OVERRIDES 算好；这里只当数据）
    method_class: str = ""
    objective: str = ""


@dataclass(frozen=True)
class Qualification:
    """资格门结论。``ok=False`` 时只有原因码与结论（证据照样要留痕，§7.2 第 7 条）。"""

    ok: bool
    reason_code: str
    verdict: EvidenceVerdict
    method_class: str = ""
    steps: tuple[SkillStep, ...] = ()
    slots: tuple[SkillSlot, ...] = ()
    preconditions: tuple[SkillPrecondition, ...] = ()
    success_criteria: tuple[SkillSuccessCriteria, ...] = ()
    postcondition_kind: str = ""
    postcondition_ok: bool = False
    required_capabilities: tuple[str, ...] = ()
    max_risk: str = "SAFE"
    fingerprint: str = ""
    detail: dict[str, Any] = field(default_factory=dict)


def _reject(
    reason: str, *, method_class: str = "", verdict: EvidenceVerdict | None = None
) -> Qualification:
    return Qualification(
        ok=False,
        reason_code=reason,
        verdict=verdict or EvidenceVerdict.REJECTED,
        method_class=method_class,
    )


def _plan_steps(record: Any) -> list[Any]:
    plan = getattr(record, "plan", None)
    steps = getattr(plan, "steps", None)
    return list(steps or [])


def _final_plan_completed(record: Any) -> bool:
    """当前（最后一版）计划是否被标记为完成 —— 只有完成的最终版本才能当方法来源。"""

    status = str(getattr(record, "plan_status", "") or "")
    return status.upper() == "COMPLETED"


def _step_state(step: Any) -> str:
    raw = getattr(getattr(step, "state", None), "value", getattr(step, "state", ""))
    return str(raw or "")


def _step_duration_ms(step: Any) -> float | None:
    started = getattr(step, "started_at", None)
    finished = getattr(step, "finished_at", None)
    if not isinstance(started, (int, float)) or not isinstance(finished, (int, float)):
        return None
    if finished <= 0 or started <= 0 or finished < started:
        return None
    return (float(finished) - float(started)) * 1000.0


def _template_arguments(step: Any) -> dict[str, Any]:
    """取步骤的**冻结模板**参数（绝不用 ``effective_arguments`` 里的解析结果）。

    已执行记录的 ``effective_arguments`` 会把 ``{"from_step","path"}`` 解析成字面量
    （例如 ``entity_id: 42``）—— 那是一执行一次的动态值：进了模板会污染指纹，
    也会把一次性 ID 写进技能正文（§7.3 / E6）。模板参数才是"这条方法长什么样"的事实。
    """

    template = getattr(step, "arguments", None)
    if isinstance(template, Mapping) and template:
        return dict(template)
    return dict(getattr(step, "effective_arguments", None) or {})


def _arguments_with_position_slot(step: Any) -> tuple[dict[str, Any], bool]:
    """把坐标替换成 ``{"$slot": "target_position"}``；返回 (参数, 是否真的换过)。"""

    arguments = _template_arguments(step)
    if not all(key in arguments for key in _POSITION_KEYS):
        return arguments, False
    for key in _POSITION_KEYS:
        arguments.pop(key, None)
    arguments["$slot"] = POSITION_SLOT
    return arguments, True


def normalize_steps(record: Any) -> tuple[tuple[SkillStep, ...], tuple[SkillSlot, ...]]:
    """把**最终成功计划**的步骤归一化成模板（§7.3）。

    只做两件事：一次性坐标 → 位置槽位；其余参数原样保留（含既有 ``{"from_step","path"}``
    引用与"工具/物品"这类环境硬约束 —— 无法安全泛化的值保留为硬约束，绝不猜着改）。
    """

    steps: list[SkillStep] = []
    slots: list[SkillSlot] = []
    for step in _plan_steps(record):
        tool = str(getattr(step, "tool", "") or "")
        arguments, changed = _arguments_with_position_slot(step)
        if changed:
            slots.append(
                SkillSlot(
                    name=POSITION_SLOT,
                    kind="position",
                    description=f"{tool} 的目标坐标（复用前必须重新观察/用户指定）",
                )
            )
        steps.append(
            SkillStep(
                step_id=str(getattr(step, "step_id", "") or ""),
                tool=tool,
                arguments=arguments,
                risk=str(getattr(step, "risk", "SAFE") or "SAFE"),
                depends_on=tuple(str(item) for item in (getattr(step, "depends_on", ()) or ())),
            )
        )
    unique: list[SkillSlot] = []
    seen: set[str] = set()
    for slot in slots:
        if slot.name in seen:
            continue
        seen.add(slot.name)
        unique.append(slot)
    return tuple(steps), tuple(unique)


def preconditions_of(record: Any) -> tuple[SkillPrecondition, ...]:
    """从计划里能**真的验证**的事实抽前置条件（封闭集合，§7.1/§7.4）。

    * ``minecraft_equip`` 用到的物品 → ``inventory_has``（复用前必须还在背包里）。

    "目标位置上有那个方块"不在评估期检查：位置是物化期用新鲜 ``find_blocks`` 重新解析的；
    解析不到就等于前置不成立（§7.4：不能编造感知能力）。
    """

    conditions: list[SkillPrecondition] = []
    seen: set[tuple[str, str]] = set()
    for step in _plan_steps(record):
        tool = str(getattr(step, "tool", "") or "")
        arguments = dict(
            getattr(step, "effective_arguments", None) or getattr(step, "arguments", {}) or {}
        )
        if tool == "minecraft_equip":
            item = str(arguments.get("item") or "")
            if item:
                key = ("inventory_has", item)
                if key not in seen:
                    seen.add(key)
                    conditions.append(
                        SkillPrecondition(kind="inventory_has", params={"item": item, "count": 1})
                    )
    return tuple(conditions)


def success_criteria_of(record: Any) -> tuple[SkillSuccessCriteria, ...]:
    """成功判据（§7.1："工具调用成功" ≠ "目标已实现"）。"""

    criteria: list[SkillSuccessCriteria] = []
    plan = getattr(record, "plan", None)
    expected = getattr(plan, "expected_final_state", None)
    delta = dict(getattr(expected, "inventory_delta", None) or {})
    for item, count in delta.items():
        criteria.append(
            SkillSuccessCriteria(
                kind="inventory_delta", params={"item": str(item), "count": int(count)}
            )
        )
    for step in _plan_steps(record):
        if str(getattr(step, "tool", "")) == "minecraft_dig":
            arguments = dict(
                getattr(step, "effective_arguments", None) or getattr(step, "arguments", {}) or {}
            )
            block = str(arguments.get("expected_block") or "")
            if block:
                criteria.append(
                    SkillSuccessCriteria(
                        kind="block_absent",
                        params={"block": block, "slot": POSITION_SLOT},
                    )
                )
    return tuple(criteria)


#: 目前**唯一**被承认的"可归因执行失败"证据：运行时自己的后验校验失败
#: （``_finish`` 用新鲜 SAFE 读复核预期效果 —— 方法真的跑了，但预期结果没达成）。
#: 其它失败（超时 / 离线 / 世界变化 / 授权 / 忙 / 卡住 / 用户取消 / 确认过期）都可能来自
#: 外部环境或用户决定，**不足以**证明方法本身失败（§1.1）。扩大这个集合必须有新的
#: 可审计执行证据，绝不从自由文本或模型自述推断。
ATTRIBUTABLE_FAILURES = frozenset({TaskFailure.VERIFICATION.value})

#: 步骤"真的开始执行过"的判据（任一成立）：有开始时间 / 有动作 id / 状态不是 PENDING
_STEP_RAN_STATES = frozenset(
    {
        StepState.RUNNING.value,
        StepState.WAITING_ACTION.value,
        StepState.WAITING_CONFIRMATION.value,
        StepState.SUCCEEDED.value,
        StepState.FAILED.value,
        StepState.SKIPPED.value,
        StepState.CANCELLED.value,
    }
)


@dataclass(frozen=True)
class ReuseVerdict:
    """一条"绑定了技能的"任务给出的反馈判定（§1.1）。"""

    #: 证据 verdict：POSITIVE / COUNTEREXAMPLE / REJECTED（后者 = 不计分，只留痕）
    evidence_verdict: EvidenceVerdict
    #: 稳定原因码（审计与只读视图用）
    reason_code: str
    #: 服务层给调用方的标签：reuse_ok / stale / invalidated / indeterminate
    label: str
    detail: dict[str, Any] = field(default_factory=dict)


def step_ran(step: Any) -> bool:
    """这一步是否**真的开始执行过**（只看持久化事实：时间戳 / action_id / 状态）。"""

    if getattr(step, "started_at", None):
        return True
    if str(getattr(step, "action_id", "") or ""):
        return True
    state = getattr(getattr(step, "state", None), "value", getattr(step, "state", ""))
    return str(state or "") in _STEP_RAN_STATES


def any_step_ran(record: Any) -> bool:
    return any(step_ran(step) for step in (getattr(record.plan, "steps", None) or []))


def classify_reuse(record: Any, *, learning: Qualification | None = None) -> ReuseVerdict:
    """决定"这条任务给技能什么反馈"（§1.1，**纯函数**，绝不从文本推断）。

    * 状态读不出来 → 不计分（``unknown_task_state``）；
    * ``SUCCEEDED`` → **仍要过既有资格门**（最终计划版本完成 + 步骤真实终态 + 后验可核验）：
      合格才计正向，否则只留痕（``unverified_success`` / 资格门自己的原因码）；
    * 非成功终态 + **没有任何步骤执行过** → 不计分（``skill_task_not_started``）；
    * 执行过但失败**不可归因**（超时/离线/世界变化/授权/用户取消/确认过期/卡住…）→ 不计分；
    * 只有"跑了 + 运行时后验校验失败"才是方法反例（``reuse_verification_failed``）。
    """

    state = task_state_enum(getattr(record, "state", None))
    if state is None:
        return ReuseVerdict(EvidenceVerdict.REJECTED, "unknown_task_state", "indeterminate")
    if state is TaskState.SUCCEEDED:
        if learning is not None and learning.ok:
            return ReuseVerdict(EvidenceVerdict.POSITIVE, "reuse_succeeded", "reuse_ok")
        reason = learning.reason_code if learning is not None else "unverified_success"
        if reason == "QUALIFIED":  # 理论上不会发生；保守兜底
            reason = "unverified_success"
        return ReuseVerdict(EvidenceVerdict.REJECTED, reason, "indeterminate")
    if not any_step_ran(record):
        # 还在等确认就被取消 / 确认过期 / 没跑就失败 —— 与"方法失败"无关
        return ReuseVerdict(
            EvidenceVerdict.REJECTED,
            "skill_task_not_started",
            "indeterminate",
            {"state": state.value},
        )
    if state is TaskState.FAILED:
        verification = getattr(record, "verification", None) or {}
        failure = str(getattr(record, "failure", "") or "")
        if (
            failure in ATTRIBUTABLE_FAILURES
            and verification.get("checked") is True
            and verification.get("ok") is False
        ):
            return ReuseVerdict(
                EvidenceVerdict.COUNTEREXAMPLE,
                "reuse_verification_failed",
                "stale",
                {"failure": failure, "state": state.value},
            )
        return ReuseVerdict(
            EvidenceVerdict.REJECTED,
            f"unattributed_failure:{failure or 'unknown'}",
            "indeterminate",
            {"state": state.value, "failure": failure},
        )
    if state is TaskState.CANCELLED:
        # 用户主动中止 ≠ 方法失败（即便已有部分步骤成功）
        return ReuseVerdict(
            EvidenceVerdict.REJECTED,
            "user_or_external_abort",
            "indeterminate",
            {"state": state.value},
        )
    if state is TaskState.EXPIRED:
        return ReuseVerdict(
            EvidenceVerdict.REJECTED,
            "task_expired_before_attribution",
            "indeterminate",
            {"state": state.value},
        )
    return ReuseVerdict(
        EvidenceVerdict.REJECTED, f"unattributed_terminal:{state.value}", "indeterminate"
    )


def qualify(inputs: LearningInput) -> Qualification:
    """学习资格门（§7.2）。纯函数：同样的输入永远给同样的结论与原因码。"""

    record = inputs.record
    method_class = inputs.method_class
    state = task_state_enum(getattr(record, "state", None))
    if state is None:
        return _reject("unknown_state", method_class=method_class)
    if not state.terminal:
        return _reject("not_terminal", method_class=method_class)
    if state is not TaskState.SUCCEEDED:
        return _reject(f"not_succeeded:{state.value}", method_class=method_class)
    if not _final_plan_completed(record):
        # 计划版本没被标记完成（被 supersede 的旧版本 / 状态字段缺失）→ 不能当方法来源
        return _reject("final_plan_not_completed", method_class=method_class)

    steps = _plan_steps(record)
    if not steps:
        return _reject("no_steps", method_class=method_class)

    # ---- 逐步：工具仍注册、风险一致、真的执行成功
    for step in steps:
        tool = str(getattr(step, "tool", "") or "")
        step_id = str(getattr(step, "step_id", "") or "")
        if inputs.registered_tools and tool not in inputs.registered_tools:
            return _reject(f"unsupported_step:{tool}", method_class=method_class)
        expected_risk = str(inputs.risk_table.get(tool, "") or "")
        actual_risk = str(getattr(step, "risk", "") or "")
        if expected_risk and actual_risk and expected_risk != actual_risk:
            return _reject(f"risk_mismatch:{tool}", method_class=method_class)
        step_state = _step_state(step)
        if step_state != StepState.SUCCEEDED.value:
            return _reject(
                f"step_not_run:{step_id}:{step_state or 'unknown'}", method_class=method_class
            )

    # ---- 改动世界的步骤必须有独立后置条件（§7.2 第 5 条）
    postcondition_kind = ""
    for step in steps:
        tool = str(getattr(step, "tool", "") or "")
        step_id = str(getattr(step, "step_id", "") or "")
        if tool not in WORLD_CHANGING_TOOLS:
            continue
        # 时长信号：方块可能在挖到之前就没了
        expected_ms = inputs.dig_expected_ms.get(step_id)
        duration = _step_duration_ms(step)
        if (
            tool == "minecraft_dig"
            # 只有"真的异步动作"（started→finished 之间有真实等待）才可比；
            # 同步返回的步骤两个时间戳几乎相同，拿它做时长判定只会误伤。
            and str(getattr(step, "action_id", "") or "")
            and isinstance(expected_ms, (int, float))
            and expected_ms
            and duration is not None
            and duration < float(expected_ms) * float(inputs.dig_short_ratio)
        ):
            return Qualification(
                ok=False,
                reason_code=f"ambiguous_world_change:{step_id}",
                verdict=EvidenceVerdict.AMBIGUOUS,
                method_class=method_class,
                detail={
                    "step_id": step_id,
                    "expected_ms": float(expected_ms),
                    "actual_ms": duration,
                    "ratio": round(duration / float(expected_ms), 3),
                },
            )
        ok, kind = inputs.postconditions.get(step_id, (False, ""))
        if not kind and tool in INVENTORY_VERIFIED_TOOLS and inputs.task_effect_verified:
            # 运行时的独立后验就是这一步的证据（背包变化可表达它的预期效果）
            ok, kind = True, "inventory_delta"
        if not ok:
            return Qualification(
                ok=False,
                reason_code=f"world_change_unverified:{step_id}",
                verdict=EvidenceVerdict.AMBIGUOUS,
                method_class=method_class,
                detail={"step_id": step_id, "tool": tool},
            )
        postcondition_kind = kind

    # ---- 判据与归一化
    criteria = success_criteria_of(record)
    if not criteria:
        return _reject("no_postcondition", method_class=method_class)
    steps_template, slots = normalize_steps(record)
    tools = tuple(dict.fromkeys(str(getattr(step, "tool", "")) for step in steps))
    risks = [str(getattr(step, "risk", "SAFE") or "SAFE") for step in steps]
    order = {"SAFE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "DESTRUCTIVE": 4}
    max_risk = max(risks, key=lambda item: order.get(item, 9)) if risks else "SAFE"
    fingerprint = skill_fingerprint(
        character_id=inputs.character_id,
        server_id=inputs.server_id,
        tools_signature=inputs.tools_signature,
        objective_pattern=method_class,
        steps=steps_template,
    )
    return Qualification(
        ok=True,
        reason_code="QUALIFIED",
        verdict=EvidenceVerdict.POSITIVE,
        method_class=method_class,
        steps=steps_template,
        slots=slots,
        preconditions=preconditions_of(record),
        success_criteria=criteria,
        postcondition_kind=postcondition_kind,
        postcondition_ok=True,
        required_capabilities=tools,
        max_risk=max_risk,
        fingerprint=fingerprint,
        detail={"steps": len(steps_template), "objective": inputs.objective[:120]},
    )


__all__ = [
    "ATTRIBUTABLE_FAILURES",
    "ENTITY_SLOT",
    "INVENTORY_VERIFIED_TOOLS",
    "STEP_POSTCONDITION_ONLY_TOOLS",
    "LearningInput",
    "POSITION_SLOT",
    "Qualification",
    "ReuseVerdict",
    "WORLD_CHANGING_TOOLS",
    "classify_reuse",
    "normalize_steps",
    "preconditions_of",
    "qualify",
    "step_ran",
    "success_criteria_of",
]
