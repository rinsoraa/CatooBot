"""VNX-1 P0 AS7: Minecraft player session lifecycle through real Service events."""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService


def _service() -> MinecraftService:
    service = MinecraftService(None, MinecraftConfig(enabled=True, auto_start_runtime=False))
    service._mirror["host"] = "mc.test"  # noqa: SLF001 - test server scope
    service._mirror["port"] = 25565  # noqa: SLF001
    service._mirror["session_id"] = "runtime-1"  # noqa: SLF001
    service._mirror["status"] = "ONLINE"  # noqa: SLF001
    return service


def _event(
    name: str, *, username: str, uuid: str, session: str = "runtime-1", ts: float = 1.0
) -> dict[str, Any]:
    return {
        "event": name,
        "session_id": session,
        "timestamp": ts,
        "username": username,
        "uuid": uuid,
    }


async def _join(
    service: MinecraftService,
    username: str,
    uuid: str,
    *,
    session: str = "runtime-1",
    ts: float = 1.0,
) -> None:
    await service.receive_event(
        _event("minecraft.player_joined", username=username, uuid=uuid, session=session, ts=ts)
    )


async def _leave(
    service: MinecraftService,
    username: str,
    uuid: str,
    *,
    session: str = "runtime-1",
    ts: float = 2.0,
) -> None:
    await service.receive_event(
        _event("minecraft.player_left", username=username, uuid=uuid, session=session, ts=ts)
    )


async def test_sl01_normal_join_leave_invalidates_binding() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A")
    assert service.player_session_binding("Alice")["player_uuid"] == "uuid-A"
    await _leave(service, "Alice", "uuid-A")
    assert service.player_session_binding("Alice") == {}


async def test_sl02_duplicate_join_does_not_create_new_epoch() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    first = service.player_session_ref("Alice")
    await _join(service, "Alice", "uuid-A", ts=1.0)
    assert service.player_session_ref("Alice") == first


async def test_sl03_same_uuid_reconnect_gets_new_epoch() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    old = service.player_session_ref("Alice")
    await _leave(service, "Alice", "uuid-A", ts=2.0)
    await _join(service, "Alice", "uuid-A", ts=2.0)
    new = service.player_session_ref("Alice")
    assert old and new and old != new


async def test_sl05_late_old_leave_keeps_new_session() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    await _leave(service, "Alice", "uuid-A", ts=2.0)
    await _join(service, "Alice", "uuid-A", ts=2.0)
    new = service.player_session_ref("Alice")
    await _leave(service, "Alice", "uuid-A", ts=2.0)
    assert service.player_session_ref("Alice") == new


async def test_sl08_same_name_different_uuid_is_ambiguous() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    await _join(service, "Alice", "uuid-B", ts=2.0)
    assert service.player_session_binding("Alice") == {}
    assert service.player_session_ref("Alice") == ""


async def test_sl09_bot_disconnect_clears_player_sessions() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    await service.receive_event(
        {"event": "minecraft.disconnected", "session_id": "runtime-1", "timestamp": 3.0, "data": {}}
    )
    assert service.player_session_binding("Alice") == {}


async def test_sl10_old_runtime_join_is_ignored() -> None:
    service = _service()
    service._mirror["session_id"] = "runtime-2"  # noqa: SLF001
    await _join(service, "Alice", "uuid-A", session="runtime-1", ts=1.0)
    assert service.player_session_binding("Alice") == {}


async def test_sl12_same_timestamp_different_players_both_tracked() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=5.0)
    await _join(service, "Bob", "uuid-B", ts=5.0)
    assert service.player_session_ref("Alice")
    assert service.player_session_ref("Bob")
    assert service.player_session_ref("Alice") != service.player_session_ref("Bob")


async def test_sl04_same_second_reconnect_gets_new_epoch() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=10.0)
    old = service.player_session_ref("Alice")
    await _leave(service, "Alice", "uuid-A", ts=10.0)
    await _join(service, "Alice", "uuid-A", ts=10.0)
    new = service.player_session_ref("Alice")
    # Identical same-timestamp join after leave is indistinguishable from a duplicate
    # callback under the current protocol; fail closed rather than revive old auth.
    assert new != old
    assert service.player_session_binding("Alice") == {}


async def test_sl11_missing_uuid_does_not_create_trusted_binding() -> None:
    service = _service()
    await service.receive_event(
        {
            "event": "minecraft.player_joined",
            "session_id": "runtime-1",
            "timestamp": 1.0,
            "username": "Alice",
        }
    )
    assert service.player_session_binding("Alice") == {}


async def test_slfu01_late_join_after_disconnect_cannot_rebuild_binding() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    await service.receive_event(
        {"event": "minecraft.disconnected", "session_id": "runtime-1", "timestamp": 2.0}
    )
    await _join(service, "Alice", "uuid-A", ts=3.0)
    assert service.player_session_binding("Alice") == {}


async def test_slfu03_join_while_disconnected_is_not_trusted() -> None:
    service = _service()
    service._mirror["status"] = "DISCONNECTED"  # noqa: SLF001
    await _join(service, "Alice", "uuid-A", ts=1.0)
    assert service.player_session_binding("Alice") == {}


async def test_sl06_missing_leave_cannot_preserve_authorization_indefinitely() -> None:
    service = _service()
    now = [100.0]
    service._player_session_clock = lambda: now[0]  # noqa: SLF001
    await _join(service, "Alice", "uuid-A", ts=1.0)
    assert service.player_session_binding("Alice")
    now[0] += service.session_max_age_seconds + 1
    assert service.player_session_binding("Alice") == {}


async def test_sl07_leave_before_join_does_not_create_binding() -> None:
    service = _service()
    await _leave(service, "Alice", "uuid-A", ts=1.0)
    assert service.player_session_binding("Alice") == {}


async def test_sl07_old_join_after_new_connection_does_not_replace_current() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    await _leave(service, "Alice", "uuid-A", ts=2.0)
    await _join(service, "Alice", "uuid-A", ts=3.0)
    current = service.player_session_ref("Alice")
    await _join(service, "Alice", "uuid-A", ts=1.0)
    assert service.player_session_ref("Alice") == current


async def test_slfu201_expired_binding_and_raw_ref_differ() -> None:
    service = _service()
    now = [10.0]
    service._player_session_clock = lambda: now[0]  # noqa: SLF001
    await _join(service, "Alice", "uuid-A", ts=1.0)
    old_ref = service.player_session_ref("Alice")
    now[0] += service.session_max_age_seconds + 1
    assert service.player_session_binding("Alice") == {}
    assert service.player_session_ref("Alice") == old_ref
    assert service.player_session_is_fresh("Alice") is False


async def _raise(message: str) -> Any:
    raise AssertionError(message)


async def test_slfu202_expired_session_is_rejected_at_agent_bridge() -> None:
    from app.integrations.minecraft.agent import MinecraftAgentBridge
    from tests.test_minecraft_agent_tools import FakeMinecraftService

    service = FakeMinecraftService()
    service.player_session_ref = lambda _u: "ps-old"
    service.player_uuid = lambda _u: "uuid-A"
    service.server_scope = lambda: "mc.test:25565"
    service.player_session_is_fresh = lambda _u: False
    service.config.agent.trusted_players = ["Alice"]
    bridge = MinecraftAgentBridge(service)
    from app.tasks.models import TaskStepAuthorization

    async def _allow(**_: Any) -> bool:
        return True

    bridge.set_task_authorizer(_allow)
    grant = TaskStepAuthorization(
        task_id="task_x",
        step_id="step_1",
        tool="minecraft_follow_player",
        arguments_hash="h",
        risk="LOW",
        plan_hash="p",
        actor_user_id="Alice",
        actor_source="minecraft",
        task_source="minecraft",
        actor_verified=True,
        actor_session_ref="ps-old",
        actor_player_uuid="uuid-A",
        actor_server_scope="mc.test:25565",
    )
    result = await bridge.invoke_task_step(
        "minecraft_follow_player",
        {"username": "Alice"},
        task_id="task_x",
        step_id="step_1",
        plan_hash="p",
        risk="LOW",
        authorization=grant,
        call=lambda _svc: _raise("world write"),
    )
    assert result.success is False
    assert result.data["reason"] == "session_expired"


async def test_runtime_down_late_join_cannot_restore_binding() -> None:
    service = _service()
    await _join(service, "Alice", "uuid-A", ts=1.0)
    service._mark_runtime_down("test")  # noqa: SLF001
    await _join(service, "Alice", "uuid-A", ts=2.0)
    assert service.player_session_binding("Alice") == {}
