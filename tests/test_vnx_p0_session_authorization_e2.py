"""VNX-1 P0 AS7-FU3: real Service session -> TaskRuntime -> AgentBridge -> fake world boundary."""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.service import MinecraftService
from app.integrations.minecraft.task_adapter import MinecraftTaskInvoker
from app.tasks.models import ExpectedFinalState, TaskPlan, TaskStep
from app.tasks.runtime import TaskConfig, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_task_turn import FakeConfirmations

SESSION = "minecraft:mc.test:25565:Alice"


class HybridService:
    """Identity/session from the real Service, network actions from the fake."""

    def __init__(self, identity: MinecraftService, action: FakeMinecraftService) -> None:
        self.identity = identity
        self.action = action
        self.config = action.config
        self.username = action.username

    def __getattr__(self, name: str) -> Any:
        return getattr(self.action, name)

    def player_session_binding(self, username: str) -> dict[str, Any]:
        return self.identity.player_session_binding(username)

    def player_session_ref(self, username: str) -> str:
        return self.identity.player_session_ref(username)

    def player_uuid(self, username: str) -> str:
        return self.identity.player_uuid(username)

    def server_scope(self) -> str:
        return self.identity.server_scope()

    def player_session_is_fresh(self, username: str) -> bool:
        return self.identity.player_session_is_fresh(username)


def _identity() -> MinecraftService:
    service = MinecraftService(None, MinecraftConfig(enabled=True, auto_start_runtime=False))
    service._mirror["host"] = "mc.test"  # noqa: SLF001
    service._mirror["port"] = 25565  # noqa: SLF001
    service._mirror["session_id"] = "runtime-1"  # noqa: SLF001
    service._mirror["status"] = "ONLINE"  # noqa: SLF001
    return service


async def _event(
    service: MinecraftService,
    name: str,
    *,
    session: str = "runtime-1",
    ts: float = 1.0,
    uuid: str = "uuid-A",
) -> None:
    await service.receive_event(
        {
            "event": name,
            "session_id": session,
            "timestamp": ts,
            "username": "Alice",
            "uuid": uuid,
        }
    )


def _runtime() -> TaskRuntime:
    runtime = TaskRuntime(
        store=InMemoryTaskStore(),
        invoke=lambda *a, **k: None,
        confirmations=FakeConfirmations(),
        config=TaskConfig(),
        risk_of=lambda tool: "LOW",
        is_registered=lambda tool: tool == "minecraft_follow_player",
        schema_of=lambda tool: {"type": "object"},
        validate_arguments=lambda schema, arguments: [],
    )
    return runtime


def _plan() -> TaskPlan:
    return TaskPlan(
        objective="follow",
        steps=[
            TaskStep(
                step_id="step_1",
                tool="minecraft_follow_player",
                arguments={"username": "示例好友"},
                risk="LOW",
            )
        ],
        expected_final_state=ExpectedFinalState(),
    )


def _wire(identity: MinecraftService) -> tuple[TaskRuntime, HybridService, FakeMinecraftService]:
    action = FakeMinecraftService()
    action.config.agent.trusted_players = ["Alice"]
    hybrid = HybridService(identity, action)
    bridge = MinecraftAgentBridge(hybrid)
    runtime = _runtime()
    bridge.set_task_authorizer(runtime.authorize_step)
    runtime._invoke = MinecraftTaskInvoker(bridge, task_reader=runtime.get)  # noqa: SLF001
    return runtime, hybrid, action


async def _create(
    runtime: TaskRuntime, binding: dict[str, Any], *, session_id: str = SESSION
) -> Any:
    return await runtime.create_task(
        "follow",
        session_id=session_id,
        user_id="Alice",
        origin="user",
        plan=_plan(),
        source="minecraft_chat",
        actor_session=binding,
    )


async def test_sl_e2_01_old_task_denied_after_leave() -> None:
    identity = _identity()
    await _event(identity, "minecraft.player_joined")
    binding = identity.player_session_binding("Alice")
    assert binding
    runtime, _, action = _wire(identity)
    record = await _create(runtime, binding)
    await _event(identity, "minecraft.player_left", ts=2.0)
    out = await runtime.confirm_and_start(
        record.task_id, user_id="Alice", session_id=SESSION, origin="user"
    )
    assert out.state.value in {"PAUSED", "FAILED", "REPLANNING"}
    assert out.failure == "AUTHORIZATION"
    assert action.action_calls("follow_player") == []


async def test_sl_e2_01b_old_task_denied_after_expiry() -> None:
    identity = _identity()
    now = [1.0]
    identity._player_session_clock = lambda: now[0]  # noqa: SLF001
    await _event(identity, "minecraft.player_joined")
    binding = identity.player_session_binding("Alice")
    runtime, _, action = _wire(identity)
    record = await _create(runtime, binding)
    now[0] += identity.session_max_age_seconds + 1
    out = await runtime.confirm_and_start(
        record.task_id, user_id="Alice", session_id=SESSION, origin="user"
    )
    assert out.state.value in {"PAUSED", "FAILED", "REPLANNING"}
    assert out.failure == "AUTHORIZATION"
    assert action.action_calls("follow_player") == []


async def test_sl_e2_02_new_session_can_dispatch_legally() -> None:
    identity = _identity()
    await _event(identity, "minecraft.player_joined", ts=1.0)
    await _event(identity, "minecraft.player_left", ts=2.0)
    identity._mirror["session_id"] = "runtime-2"  # noqa: SLF001
    await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
    binding = identity.player_session_binding("Alice")
    assert binding
    runtime, _, action = _wire(identity)
    record = await _create(runtime, binding)
    await runtime.confirm_and_start(
        record.task_id, user_id="Alice", session_id=SESSION, origin="user"
    )
    assert len(action.action_calls("follow_player")) == 1


async def test_sl_e2_03_late_events_cannot_revive_old_authorization() -> None:
    identity = _identity()
    await _event(identity, "minecraft.player_joined", ts=1.0)
    old_binding = identity.player_session_binding("Alice")
    runtime, _, action = _wire(identity)
    task_a = await _create(runtime, old_binding)
    await _event(identity, "minecraft.player_left", ts=2.0)
    identity._mirror["session_id"] = "runtime-2"  # noqa: SLF001 - fixture session switch
    await _event(identity, "minecraft.player_joined", session="runtime-2", ts=3.0)
    new_binding = identity.player_session_binding("Alice")
    assert new_binding and new_binding["actor_session_ref"] != old_binding["actor_session_ref"]
    session_b = "minecraft:mc.test:25565:Alice:2"
    task_b = await _create(runtime, new_binding, session_id=session_b)
    # late callbacks from runtime-1, different timestamps so dedup cannot swallow them
    await _event(identity, "minecraft.player_joined", session="runtime-1", ts=4.0)
    await _event(identity, "minecraft.player_left", session="runtime-1", ts=5.0)
    await identity.receive_event(
        {
            "event": "minecraft.spawned",
            "session_id": "runtime-1",
            "timestamp": 6.0,
            "username": "Catodayo",
        }
    )
    refreshed = identity.player_session_binding("Alice")
    assert refreshed["actor_session_ref"] == new_binding["actor_session_ref"]
    assert refreshed["player_uuid"] == new_binding["player_uuid"]
    assert refreshed["server_scope"] == new_binding["server_scope"]
    old = await runtime.confirm_and_start(
        task_a.task_id, user_id="Alice", session_id=SESSION, origin="user"
    )
    assert old.failure == "AUTHORIZATION"
    assert action.action_calls("follow_player") == []
    await runtime.confirm_and_start(
        task_b.task_id, user_id="Alice", session_id=session_b, origin="user"
    )
    assert len(action.action_calls("follow_player")) == 1
