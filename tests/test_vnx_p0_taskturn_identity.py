"""VNX-1 P0 AS6: TaskTurn identity binding and concurrency checks (focused)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.tasks.models import TaskState
from app.tasks.runtime import TaskAuthorizationError, TaskConfig, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from app.tasks.turn import TaskTurnHandler
from tests.test_build_plan import BuildObservations
from tests.test_combat_plan import CombatObservations
from tests.test_delivery_plan import DeliveryObservations
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_task_turn import FakeConfirmations, Recorder, make_handler

A = {
    "actor_session_ref": "ps_A",
    "player_uuid": "uuid-A",
    "server_scope": "host:25565",
    "identity_assurance": "server_observed_uuid",
    "captured_at": 1.0,
}
B = {
    "actor_session_ref": "ps_B",
    "player_uuid": "uuid-B",
    "server_scope": "host:25565",
    "identity_assurance": "server_observed_uuid",
    "captured_at": 2.0,
}


class CaptureRuntime:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.gate: Any = None

    async def current(self, session_id: str) -> Any:
        return None

    async def create_task(self, objective: str, **kwargs: Any) -> Any:
        if self.gate is not None:
            await self.gate.wait()
        self.calls.append(dict(kwargs))
        return SimpleNamespace(
            task_id=f"task_{len(self.calls)}",
            objective=objective,
            plan=kwargs["plan"],
            state=SimpleNamespace(value="PENDING_CONFIRMATION", terminal=False),
            plan_version=1,
            plan_hash="hash",
        )

    def summary_of(self, record: Any) -> str:
        return "summary"


async def test_tt01_build_path_carries_actor_binding() -> None:
    runtime = CaptureRuntime()
    handler = TaskTurnHandler(runtime, observe=BuildObservations())
    outcome = await handler.handle(
        session_id="minecraft:host:25565:A",
        user_id="A",
        text="在 (10,64,10) 放一块泥土",
        actor_session=A,
    )
    assert outcome.action == "created"
    assert runtime.calls[0]["actor_session"] == A


async def test_tt02_combat_path_carries_actor_binding() -> None:
    runtime = CaptureRuntime()
    handler = TaskTurnHandler(runtime, observe=CombatObservations())
    outcome = await handler.handle(
        session_id="minecraft:host:25565:A",
        user_id="A",
        text="攻击附近的一只僵尸",
        actor_session=A,
    )
    assert outcome.action == "created"
    assert runtime.calls[0]["actor_session"] == A


async def test_tt04_resource_path_persists_actor_binding() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    outcome = await handler.handle(
        session_id="minecraft:host:25565:A",
        user_id="A",
        text="去砍一棵橡树，挖一块原木并捡回来",
        actor_session=A,
    )
    assert outcome.action == "created"
    record = await runtime.get(outcome.task_id)
    assert record is not None
    assert record.user_id == "A"
    assert record.actor_verified is True
    assert record.actor_session_ref == "ps_A"
    assert record.actor_player_uuid == "uuid-A"
    assert record.actor_server_scope == "host:25565"


async def test_tt05_contextvar_isolated_across_interleaved_requests() -> None:
    runtime = CaptureRuntime()
    import asyncio

    runtime.gate = asyncio.Event()
    observe = BuildObservations()
    observe.cells = [(10, 64, 10), (10, 64, 11)]
    handler = TaskTurnHandler(runtime, observe=observe)
    first = asyncio.create_task(
        handler.handle(
            session_id="minecraft:host:25565:A",
            user_id="A",
            text="在 (10,64,10) 放一块泥土",
            actor_session=A,
        )
    )
    await asyncio.sleep(0)  # let A await at the injected gate
    second = asyncio.create_task(
        handler.handle(
            session_id="minecraft:host:25565:B",
            user_id="B",
            text="在 (10,64,11) 放一块泥土",
            actor_session=B,
        )
    )
    await asyncio.sleep(0)
    runtime.gate.set()
    await asyncio.gather(first, second)
    assert runtime.calls[0]["actor_session"] == A
    assert runtime.calls[1]["actor_session"] == B


async def test_tt06_missing_actor_is_not_inferred() -> None:
    runtime = CaptureRuntime()
    handler = TaskTurnHandler(runtime, observe=BuildObservations())
    outcome = await handler.handle(
        session_id="minecraft:host:25565:A",
        user_id="A",
        text="在 (10,64,10) 放一块泥土",
        actor_session=None,
    )
    assert outcome.action == "created"
    assert runtime.calls[0]["actor_session"] is None


def _delivery_runtime() -> TaskRuntime:
    tools = {
        "minecraft_drop_item": "MEDIUM",
        "minecraft_delivery_verify": "SAFE",
        "minecraft_entities": "SAFE",
        "minecraft_inventory": "SAFE",
    }
    return TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=Recorder(),
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: tools.get(tool, "SAFE"),
        is_registered=lambda tool: tool in tools,
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )


async def test_tt03_delivery_path_persists_actor_binding() -> None:
    runtime = _delivery_runtime()
    handler = TaskTurnHandler(runtime, observe=DeliveryObservations())
    outcome = await handler.handle(
        session_id="minecraft:host:25565:PlayerA",
        user_id="PlayerA",
        text="给 GuanTou 一个泥土",
        actor_session=A,
    )
    assert outcome.action == "created", outcome
    record = await runtime.get(outcome.task_id)
    assert record is not None
    assert record.user_id == "PlayerA"
    assert record.actor_player_uuid == "uuid-A"
    assert record.actor_session_ref == "ps_A"
    assert record.actor_server_scope == "host:25565"
    assert record.source == "minecraft_chat"
    assert record.actor_verified is True
    target = record.plan.steps[1].arguments
    assert target["entity_id"] == {"from_step": "step_1", "path": "entity_id"}


async def test_tt07_cross_owner_cannot_confirm() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    out = await handler.handle(
        session_id="minecraft:host:25565:PlayerA",
        user_id="PlayerA",
        text="去砍一棵橡树，挖一块原木并捡回来",
        actor_session=A,
    )
    assert out.action == "created"
    before = await runtime.get(out.task_id)
    assert before is not None
    with pytest.raises(TaskAuthorizationError):
        await runtime.confirm_and_start(
            out.task_id,
            user_id="PlayerB",
            session_id="minecraft:host:25565:PlayerB",
            origin="user",
        )
    after = await runtime.get(out.task_id)
    assert after is not None
    assert after.state is TaskState.PENDING_CONFIRMATION
    assert after.plan_hash == before.plan_hash


async def test_tt08_session_change_blocks_protected_step() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    out = await handler.handle(
        session_id="minecraft:host:25565:PlayerA",
        user_id="PlayerA",
        text="去砍一棵橡树，挖一块原木并捡回来",
        actor_session=A,
    )
    assert out.action == "created"
    record = await runtime.get(out.task_id)
    assert record is not None
    grant = record.step_authorization(record.steps[1])
    service = FakeMinecraftService()
    service.config.agent.trusted_players = ["PlayerA"]
    service.player_session_ref = lambda _u: "ps_A_NEW"
    service.player_uuid = lambda _u: "uuid-A"
    service.server_scope = lambda: "host:25565"
    bridge = MinecraftAgentBridge(service)

    async def _allow(**_: Any) -> bool:
        return True

    bridge.set_task_authorizer(_allow)
    result = await bridge.invoke_task_step(
        record.steps[1].tool,
        record.steps[1].effective_arguments,
        task_id=record.task_id,
        step_id=record.steps[1].step_id,
        plan_hash=record.plan_hash,
        risk=record.steps[1].risk,
        authorization=grant,
        call=lambda _svc: _raise("world write"),
    )
    assert result.success is False
    assert result.data["error"]["message"].startswith("玩家会话绑定缺失或已变化")


async def _raise(message: str) -> Any:
    raise AssertionError(message)


async def test_tt10_binding_is_snapshotted_not_referenced() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    binding = dict(A)
    out = await handler.handle(
        session_id="minecraft:host:25565:PlayerA",
        user_id="PlayerA",
        text="去砍一棵橡树，挖一块原木并捡回来",
        actor_session=binding,
    )
    assert out.action == "created"
    binding["actor_session_ref"] = "ps_MUTATED"
    binding["player_uuid"] = "uuid-MUTATED"
    binding["server_scope"] = "evil:1"
    record = await runtime.get(out.task_id)
    assert record is not None
    assert record.actor_session_ref == "ps_A"
    assert record.actor_player_uuid == "uuid-A"
    assert record.actor_server_scope == "host:25565"


class FailingRuntime(CaptureRuntime):
    async def create_task(self, objective: str, **kwargs: Any) -> Any:
        raise RuntimeError("controlled planning failure")


async def test_tt09b_exception_does_not_leak_actor_context() -> None:
    handler = TaskTurnHandler(FailingRuntime(), observe=BuildObservations())
    with pytest.raises(RuntimeError):
        await handler.handle(
            session_id="minecraft:host:25565:PlayerA",
            user_id="PlayerA",
            text="在 (10,64,10) 放一块泥土",
            actor_session=A,
        )
    runtime = CaptureRuntime()
    handler2 = TaskTurnHandler(runtime, observe=BuildObservations())
    await handler2.handle(
        session_id="minecraft:host:25565:PlayerB",
        user_id="PlayerB",
        text="在 (10,64,10) 放一块泥土",
        actor_session=None,
    )
    assert runtime.calls[0]["actor_session"] is None


class GateRuntime(CaptureRuntime):
    def __init__(self) -> None:
        super().__init__()
        import asyncio

        self.entered = asyncio.Event()
        self.gate = asyncio.Event()

    async def create_task(self, objective: str, **kwargs: Any) -> Any:
        self.entered.set()
        await self.gate.wait()
        return await super().create_task(objective, **kwargs)


async def test_tt09c_cancelled_request_does_not_leak_actor_context() -> None:
    import asyncio

    runtime = GateRuntime()
    handler = TaskTurnHandler(runtime, observe=BuildObservations())
    task = asyncio.create_task(
        handler.handle(
            session_id="minecraft:host:25565:PlayerA",
            user_id="PlayerA",
            text="在 (10,64,10) 放一块泥土",
            actor_session=A,
        )
    )
    await runtime.entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    runtime.gate.set()
    await handler.handle(
        session_id="minecraft:host:25565:PlayerB",
        user_id="PlayerB",
        text="在 (10,64,10) 放一块泥土",
        actor_session=B,
    )
    assert runtime.calls[-1]["actor_session"] == B


async def test_tt06_real_execution_without_actor_is_denied() -> None:
    handler, runtime = make_handler(Recorder(minecraft_move_to="detached"))
    out = await handler.handle(
        session_id="minecraft:host:25565:PlayerA",
        user_id="PlayerA",
        text="去砍一棵橡树，挖一块原木并捡回来",
        actor_session=None,
    )
    assert out.action == "created"
    record = await runtime.get(out.task_id)
    assert record is not None
    grant = record.step_authorization(record.steps[1])
    service = FakeMinecraftService()
    service.config.agent.trusted_players = ["PlayerA"]
    bridge = MinecraftAgentBridge(service)

    async def _allow(**_: Any) -> bool:
        return True

    bridge.set_task_authorizer(_allow)
    result = await bridge.invoke_task_step(
        record.steps[1].tool,
        record.steps[1].effective_arguments,
        task_id=record.task_id,
        step_id=record.steps[1].step_id,
        plan_hash=record.plan_hash,
        risk="SAFE",
        authorization=grant,
        call=lambda _svc: _raise("world write"),
    )
    assert result.success is False
    assert result.error_type == "minecraft.task_authorization_missing"
