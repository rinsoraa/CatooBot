"""Phase 5A.1 §三十六：重规划/恢复期的授权安全（8 条必须钉死）。

规则一句话：**旧 Plan、旧 action、旧 Confirmation、旧世界假设，都不能驱动新的世界动作。**

1. v1 的确认不能授权 v2；
2. v2 的 plan_hash 必须与 v1 不同；
3. v1 的动作参数不能被换掉；
4. 重规划后未确认时，MEDIUM 步骤永远被拒；
5. TASK 凭据不能跨 task 使用；
6. TASK 凭据不能跨 step 使用（且一次性）；
7. 过期的授权不能执行任何动作；
8. WebUI / SYSTEM 不能确认计划。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.agent import (
    BRIDGE_KEY,
    CODE_TASK_UNAUTHORIZED,
    INTENT_KEY,
    TASK_TOKEN_KEY,
    TURN_ORIGIN_KEY,
    MinecraftAgentBridge,
)
from app.tasks.models import (
    ExpectedFinalState,
    PlanStatus,
    ReplanReason,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.runtime import TaskAuthorizationError, TaskConfig
from app.tools.models import ToolContext
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_task_recovery import Clock, Events, FakeInvoke, make_runtime

SESSION = "minecraft:127.0.0.1:25565:空凛"
USER = "空凛"


def dig_plan(*, x: int = 10, z: int = 10, expected: str = "oak_log") -> TaskPlan:
    return TaskPlan(
        objective="挖一块橡木原木并捡回来",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_dig",
                arguments={"x": x, "y": 64, "z": z, "expected_block": expected},
                risk="MEDIUM",
            ),
            TaskStep(step_id="step_2", tool="minecraft_inventory", arguments={}, risk="SAFE"),
        ],
        expected_final_state=ExpectedFinalState(),
    )


async def create_confirmed(runtime: Any, plan: TaskPlan | None = None) -> Any:
    plan = plan or dig_plan()
    record = await runtime.create_task(
        plan.objective, session_id=SESSION, user_id=USER, origin="user", plan=plan
    )
    assert record.state is TaskState.PENDING_CONFIRMATION, record.message
    return await runtime.confirm_and_start(
        record.task_id, user_id=USER, session_id=SESSION, origin="user"
    )


# ------------------------------------------------------------------ 1/2：v1 ≠ v2


async def test_1_v1_confirmation_cannot_authorize_v2() -> None:
    """同一 task / 同一 user，换了计划也不行 —— 授权绑的是**那一版**的 hash 与版本号。"""
    runtime, invoke, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    assert record.authorization is not None
    v1_hash = record.plan_hash
    actions_before = list(invoke.world_actions)

    await runtime.recover(record.task_id)  # 世界变了 → REPLANNING
    replanned = await runtime.replan(
        record.task_id, dig_plan(x=99, z=99), reason=ReplanReason.WORLD_CHANGED.value
    )
    assert replanned.state is TaskState.PENDING_CONFIRMATION
    assert replanned.authorization is None, "重规划必须清掉旧授权"
    step = replanned.step("step_1")
    assert step is not None

    # 用**旧**授权（v1 hash）放行 v2 的步骤 → 拒绝
    assert (
        await runtime.authorize_step(
            task_id=replanned.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments=step.effective_arguments,
            plan_hash=v1_hash,
        )
        is False
    )
    # 用 v2 的 hash 也不行：用户还没确认 v2
    assert (
        await runtime.authorize_step(
            task_id=replanned.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments=step.effective_arguments,
            plan_hash=replanned.plan_hash,
        )
        is False
    )
    driven = await runtime.drive(replanned.task_id)
    assert driven.state is TaskState.PENDING_CONFIRMATION
    assert invoke.world_actions == actions_before, "重规划 + 未确认期间零世界动作"


async def test_2_v2_hash_differs_from_v1_and_versions_are_independent() -> None:
    runtime, _, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    v1_hash = record.plan_hash
    await runtime.recover(record.task_id)
    replanned = await runtime.replan(
        record.task_id, dig_plan(x=7, z=7), reason=ReplanReason.WORLD_CHANGED.value
    )
    assert replanned.plan_hash != v1_hash
    assert replanned.plan_history[0].plan_hash == v1_hash
    assert replanned.plan_history[0].status == PlanStatus.SUPERSEDED.value
    # 同样的计划再规划一次（世界没变、参数没变）→ hash 相同，但版本是新的
    again = await runtime.replan(
        replanned.task_id, dig_plan(x=7, z=7), reason=ReplanReason.USER_REQUEST.value
    )
    assert again.plan_version == 3
    assert again.plan_hash == replanned.plan_hash
    assert [item.version for item in again.plan_history] == [1, 2, 3]
    assert again.plan_history[1].status == PlanStatus.SUPERSEDED.value


# ------------------------------------------------------------------ 3：参数不能被换掉


async def test_3_v1_action_arguments_cannot_be_swapped_after_confirmation() -> None:
    runtime, invoke, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    step = record.step("step_1")
    assert step is not None
    step.arguments["x"] = 999  # 偷换目标坐标
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments={"x": 999, "y": 64, "z": 10, "expected_block": "oak_log"},
            plan_hash=record.plan_hash,
        )
        is False
    )
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments={"x": 10, "y": 64, "z": 10, "expected_block": "oak_log"},
            plan_hash=record.plan_hash,
        )
        is False
    ), "计划被改过之后连原参数也不放行（必须先重新确认）"
    assert invoke.world_actions == ["minecraft_dig"], "只有确认时那一次真的跑过"


# ------------------------------------------------------------------ 4：未确认一律拒绝


async def test_4_medium_steps_are_refused_while_unconfirmed() -> None:
    runtime, invoke, confirmations, _ = make_runtime()
    record = await create_confirmed(runtime)
    await runtime.recover(record.task_id)
    replanned = await runtime.replan(
        record.task_id, dig_plan(x=5, z=5), reason=ReplanReason.WORLD_CHANGED.value
    )
    for _ in range(3):
        driven = await runtime.drive(replanned.task_id)
        assert driven.state is TaskState.PENDING_CONFIRMATION
    assert invoke.world_actions == ["minecraft_dig"], "未确认的新计划一步都不许跑"

    started = await runtime.confirm_and_start(
        replanned.task_id, user_id=USER, session_id=SESSION, origin="user"
    )
    assert started.state is TaskState.WAITING_ACTION
    assert invoke.world_actions == ["minecraft_dig", "minecraft_dig"], "确认之后才开始跑新计划"
    assert confirmations.created[-1]["plan_hash"] == replanned.plan_hash


# ------------------------------------------------------------------ 5/6：一次性凭据


class BridgeStack:
    """真 bridge + 假 Service：钉死"凭据不可跨 task/step 复用"。"""

    def __init__(self) -> None:
        self.service = FakeMinecraftService()
        self.service.config.agent.tools.allow_medium = True
        self.bridge = MinecraftAgentBridge(self.service)
        self.authorized: list[dict[str, Any]] = []
        self.allow = True
        self.invocations: list[str] = []

    async def authorizer(self, **kwargs: Any) -> bool:
        self.authorized.append(dict(kwargs))
        return self.allow

    async def call(self, service: Any) -> dict[str, Any]:
        self.invocations.append("service")
        return {"status": "RUNNING", "action_id": "act_bridge_1", "action": "dig"}

    def forged_context(self, token: str) -> ToolContext:
        return ToolContext(
            user_id="attacker",
            session_id="attacker",
            metadata={
                BRIDGE_KEY: self.bridge,
                TURN_ORIGIN_KEY: "task",
                INTENT_KEY: False,
                TASK_TOKEN_KEY: token,
            },
        )


async def test_5_6_task_credentials_are_one_shot_and_never_cross_task_or_step() -> None:
    stack = BridgeStack()
    stack.bridge.set_task_authorizer(stack.authorizer)
    args = {"x": 1, "y": 64, "z": 1, "expected_block": "oak_log"}

    result = await stack.bridge.invoke_task_step(
        "minecraft_dig",
        args,
        task_id="task_a",
        step_id="step_1",
        plan_hash="hash_v1",
        risk="MEDIUM",
        authorization=None,
        call=stack.call,
    )
    assert result.success is True
    assert stack.authorized[0]["task_id"] == "task_a"
    assert stack.authorized[0]["step_id"] == "step_1"
    assert stack.bridge._task_tokens == {}, "凭据用完即作废（TTL 内也不许复用）"  # noqa: SLF001

    # 拿一个编造的凭据（跨 task）→ 策略层拒绝
    decision = stack.bridge.policy.check(
        "minecraft_dig",
        {"x": 2, "y": 64, "z": 2, "expected_block": "oak_log"},
        facts=stack.bridge.gate_facts(
            explicit_intent=False,
            turn_origin="task",
            task_facts_values=stack.bridge._resolve_task_token("task_a:step_1"),  # noqa: SLF001
        ),
    )
    assert decision.allowed is False
    assert decision.code == CODE_TASK_UNAUTHORIZED

    # 手工拼一个"我已授权"的上下文（伪造凭据）→ 一样拒绝
    denied = await stack.bridge.invoke(
        "minecraft_dig",
        {"x": 3, "y": 64, "z": 3, "expected_block": "oak_log"},
        stack.call,
        context=stack.forged_context("totally-made-up"),
    )
    assert denied.success is False
    assert denied.error_type == CODE_TASK_UNAUTHORIZED
    assert stack.invocations == ["service"], "只有那一次合法调用真的执行了"

    # 校验器说不 → 连凭据都不签发（跨 step 的调用一步都不执行）
    stack.allow = False
    refused = await stack.bridge.invoke_task_step(
        "minecraft_dig",
        {"x": 4, "y": 64, "z": 4, "expected_block": "oak_log"},
        task_id="task_b",
        step_id="step_9",
        plan_hash="hash_other",
        risk="MEDIUM",
        authorization=None,
        call=stack.call,
    )
    assert refused.success is False and refused.error_type == CODE_TASK_UNAUTHORIZED
    assert stack.invocations == ["service"]
    assert stack.bridge._task_tokens == {}  # noqa: SLF001


async def test_cross_step_arguments_are_refused_by_the_runtime_authorizer() -> None:
    runtime, invoke, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    allowed = await runtime.authorize_step(
        task_id=record.task_id,
        step_id="step_2",  # step_2 是 inventory，不是 dig
        tool="minecraft_dig",
        arguments={"x": 10, "y": 64, "z": 10, "expected_block": "oak_log"},
        plan_hash=record.plan_hash,
    )
    assert allowed is False, "跨 step 一律不放行"
    assert invoke.world_actions == ["minecraft_dig"]


# ------------------------------------------------------------------ 7：过期授权


async def test_7_expired_authorization_cannot_execute() -> None:
    clock = Clock(1000.0)
    runtime, invoke, confirmations, events = make_runtime(
        clock=clock, config=TaskConfig(authorization_ttl_seconds=60.0)
    )
    record = await create_confirmed(runtime)
    step = record.step("step_1")
    assert step is not None
    clock.advance(61.0)
    assert (
        await runtime.authorize_step(
            task_id=record.task_id,
            step_id="step_1",
            tool="minecraft_dig",
            arguments=step.effective_arguments,
            plan_hash=record.plan_hash,
        )
        is False
    )
    assert invoke.world_actions == ["minecraft_dig"]

    # 推进也只会回到"等重新确认"，不会再动世界
    current = await runtime.get(record.task_id)
    assert current is not None
    current.pending_action_id = ""
    current.pending_step_id = ""
    current.steps[0].state = current.steps[0].state.PENDING
    current.state = TaskState.RUNNING
    await runtime._save(current)  # noqa: SLF001
    driven = await runtime.drive(record.task_id)
    assert driven.state is TaskState.PENDING_CONFIRMATION
    assert len(invoke.world_actions) == 1
    assert "task.authorization_expired" in events.names()
    assert len(confirmations.created) == 2, "过期后必须挂新的确认"


# ------------------------------------------------------------------ 8：WebUI / SYSTEM 不能确认


@pytest.mark.parametrize("origin", ["webui", "system", "background", "initiative", "task"])
async def test_8_non_user_origins_can_never_confirm_a_plan(origin: str) -> None:
    runtime, invoke, confirmations, _ = make_runtime()
    record = await runtime.create_task(
        dig_plan().objective, session_id=SESSION, user_id=USER, origin="user", plan=dig_plan()
    )
    confirmation_id = record.confirmation_id
    with pytest.raises(TaskAuthorizationError) as excinfo:
        await runtime.confirm_and_start(
            record.task_id, user_id=USER, session_id=SESSION, origin=origin
        )
    assert excinfo.value.code == "task.confirmation_not_user_turn"
    still = await runtime.get(record.task_id)
    assert still is not None
    assert still.state is TaskState.PENDING_CONFIRMATION
    assert still.authorization is None
    assert invoke.world_actions == []
    assert confirmations.items[confirmation_id]["status"] == "PENDING", "非用户回合连确认都不该碰"

    # 重规划之后同样：新计划的确认也只能来自用户回合
    await runtime.confirm_and_start(record.task_id, user_id=USER, session_id=SESSION, origin="user")
    await runtime.recover(record.task_id)
    replanned = await runtime.replan(
        record.task_id, dig_plan(x=3), reason=ReplanReason.RUNTIME_RESTART.value
    )
    with pytest.raises(TaskAuthorizationError):
        await runtime.confirm_and_start(
            replanned.task_id, user_id=USER, session_id=SESSION, origin=origin
        )


async def test_replan_itself_only_does_safe_observation() -> None:
    """重规划本身只允许 SAFE 观察：新计划在确认前一个世界动作都不许有。"""
    runtime, _, _, _ = make_runtime()
    record = await create_confirmed(runtime)
    probe = FakeInvoke(dig_reason="air", dig_block=None)
    runtime._invoke = probe  # noqa: SLF001 - 对账/观察走同一套假通道
    await runtime.recover(record.task_id)
    await runtime.replan(record.task_id, dig_plan(x=8), reason="WORLD_CHANGED")
    assert probe.world_actions == []
    assert "minecraft_world" in probe.calls
    assert "minecraft_dig_capability" in probe.calls


def test_enums_are_closed_sets() -> None:
    assert {item.value for item in ReplanReason} >= {
        "TARGET_LOST",
        "WORLD_CHANGED",
        "RUNTIME_RESTART",
        "AUTHORIZATION_EXPIRED",
    }
    assert {item.value for item in PlanStatus} == {
        "PENDING_CONFIRMATION",
        "ACTIVE",
        "SUPERSEDED",
        "COMPLETED",
    }
    assert Events is not None
