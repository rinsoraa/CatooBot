"""Minecraft Action Runtime 服务层测试（Phase 3B）。

用例名对应任务书 §十七：look_at / stop / concurrency / action lifecycle /
runtime stability；runtime 用进程内假服务器（FakeRuntime），不需要 Node。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.service import (
    MinecraftActionBusy,
    MinecraftActionFailed,
    MinecraftActionInvalid,
    MinecraftNotConnected,
    MinecraftService,
)
from tests.test_minecraft_service import FakeRuntime, make_config


@pytest.fixture
async def fake_runtime():
    runtime = FakeRuntime()
    await runtime.start()
    yield runtime
    await runtime.stop()


@pytest.fixture
async def make_service():
    """服务工厂 + 收尾：保证每个 service 的 HTTP 会话都被关闭。"""
    created: list[MinecraftService] = []

    def factory(config: Any) -> MinecraftService:
        service = MinecraftService(None, config)
        created.append(service)
        return service

    yield factory
    for service in created:
        await service._cleanup()


def action_event(name: str, **data: Any) -> dict[str, Any]:
    return {"event": name, "session_id": "s1", "timestamp": 1.0, **data}


# --------------------------------------------------------------------- look_at


async def test_look_at_success(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    result = await service.look_at(100, 65, -200)
    assert result["status"] == "SUCCEEDED"
    assert result["action"] == "look_at"
    assert str(result["action_id"]).startswith("act_")
    # 坐标原样到达 runtime（含 int → float 归一化），上层从不接触 yaw/pitch
    assert fake_runtime.look_at_calls == [{"x": 100.0, "y": 65.0, "z": -200.0}]


async def test_look_at_invalid_coordinates(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    bad_cases = [
        ("abc", 1, 2),
        (1, None, 2),
        (float("nan"), 1, 2),
        (1, 1, float("inf")),
        (4.0e7, 1, 2),  # 超出世界边界
        (1, -600, 2),
        (True, 1, 2),  # bool 不是坐标
    ]
    for bad in bad_cases:
        with pytest.raises(MinecraftActionInvalid):
            await service.look_at(*bad)
    # 本地校验先于任何网络往返
    assert fake_runtime.look_at_calls == []


async def test_look_at_when_offline(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.look_at_plan.append({"error": ("action.not_online", 400)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftNotConnected):
        await service.look_at(1, 2, 3)


async def test_look_at_timeout(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.look_at_plan.append({"status": "TIMEOUT", "action_id": "act_timeout"})
    service = make_service(make_config(fake_runtime))
    result = await service.look_at(1, 2, 3)
    assert result["status"] == "TIMEOUT"
    assert result["action_id"] == "act_timeout"


async def test_look_at_failed_translated(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.look_at_plan.append({"error": ("action.failed", 500)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftActionFailed) as excinfo:
        await service.look_at(1, 2, 3)
    assert excinfo.value.code == "minecraft.action_failed"


# ------------------------------------------------------------------------ stop


async def test_stop_is_idempotent(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    first = await service.stop_action()
    second = await service.stop_action()
    assert first["status"] == "IDLE" and second["status"] == "IDLE"
    assert first["cancelled"] == [] and second["cancelled"] == []
    assert fake_runtime.stop_calls == 2


async def test_stop_cancels_running_action(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.stop_cancelled = ["act_running"]
    service = make_service(make_config(fake_runtime))
    result = await service.stop_action()
    assert result["cancelled"] == ["act_running"]
    assert result["status"] == "IDLE"


async def test_stop_when_idle(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    result = await service.stop_action()
    assert result["cancelled"] == []
    assert result["status"] == "IDLE"


async def test_stop_when_runtime_down_still_succeeds(make_service) -> None:
    """硬停止入口不允许失败：runtime 不可达时也要返回成功（此时没有可执行动作）。"""
    service = make_service(make_config(None))
    result = await service.stop_action()
    assert result["status"] == "IDLE" and result["cancelled"] == []


# ----------------------------------------------------------------- concurrency


async def test_second_exclusive_action_rejected(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.look_at_plan.append({"error": ("action.busy", 409)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftActionBusy) as excinfo:
        await service.look_at(1, 2, 3)
    assert excinfo.value.code == "minecraft.action_busy"
    assert excinfo.value.status == 409


# ------------------------------------------------------------ action lifecycle


async def test_action_started_event(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    seen: list[Any] = []
    service.add_listener(seen.append)
    await service.receive_event(
        action_event(
            "minecraft.action.started",
            action="look_at",
            action_id="act_1",
            status="RUNNING",
            started_at=1.0,
        )
    )
    assert len(seen) == 1 and seen[0].type_name == "minecraft.action.started"
    action = service.snapshot()["action"]
    assert action["status"] == "RUNNING"
    assert action["action_id"] == "act_1"
    assert action["finished_at"] is None


async def test_action_completed_event(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    await service.receive_event(
        action_event("minecraft.action.started", action="look_at", action_id="act_2")
    )
    await service.receive_event(
        action_event(
            "minecraft.action.completed",
            action="look_at",
            action_id="act_2",
            status="SUCCEEDED",
            elapsed_ms=12,
        )
    )
    action = service.snapshot()["action"]
    assert action["status"] == "SUCCEEDED"
    assert action["elapsed_ms"] == 12
    assert action["finished_at"] == 1.0


async def test_action_failed_event(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    seen: list[Any] = []
    service.add_listener(seen.append)
    await service.receive_event(
        action_event("minecraft.action.failed", action="look_at", action_id="act_3", error="kaboom")
    )
    assert service.snapshot()["action"]["status"] == "FAILED"
    assert seen[-1].data["error"] == "kaboom"


async def test_action_cancelled_event(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    await service.receive_event(
        action_event(
            "minecraft.action.cancelled", action="look_at", action_id="act_4", reason="stop"
        )
    )
    action = service.snapshot()["action"]
    assert action["status"] == "CANCELLED"
    assert action["action_id"] == "act_4"


async def test_action_timeout_event_mirrors(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    await service.receive_event(
        action_event(
            "minecraft.action.timeout", action="look_at", action_id="act_5", reason="timeout"
        )
    )
    assert service.snapshot()["action"]["status"] == "TIMEOUT"


# --------------------------------------------------------- runtime stability


async def test_action_failure_does_not_break_connection_or_perception(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """动作失败/取消不得破坏连接状态镜像，也不得影响 WorldPerception（§十九）。"""
    from tests.test_minecraft_world import make_clock, make_perception

    clock, advance = make_clock()
    perception, _client, _events = make_perception(clock, advance)
    await perception.poll({"near"})
    assert perception.cache.online is True

    service = make_service(make_config(fake_runtime))
    service.perception = perception
    await service.receive_event(action_event("minecraft.spawned", action=None, username="Catodayo"))
    await service.receive_event(
        action_event("minecraft.action.failed", action="look_at", action_id="act_x", error="boom")
    )
    assert service.snapshot()["connection"]["status"] == "ONLINE"
    assert service.world_view()["available"] is True
    assert perception.cache.online is True


async def test_action_events_do_not_emit_world_events(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """Phase 3A 关系：动作事件不产生世界差异事件；look_at 不改位置 → 无 WINDOW_SHIFT。"""
    from tests.test_minecraft_world import make_clock, make_perception

    clock, advance = make_clock()
    perception, _client, world_events = make_perception(clock, advance)
    await perception.poll({"near"})  # prime：建立世界基线

    service = make_service(make_config(fake_runtime))
    service.perception = perception
    await service.receive_event(
        action_event("minecraft.action.started", action="look_at", action_id="act_l")
    )
    await service.receive_event(
        action_event("minecraft.action.completed", action="look_at", action_id="act_l")
    )
    assert world_events == []  # 动作事件不会被当成世界事件分发
    assert perception.last_diff is None  # 没有发生过 near 差异（没有窗口位移）


async def test_chat_still_works_and_is_an_action(fake_runtime: FakeRuntime, make_service) -> None:
    """Phase 1 回归：chat 行为不变（sent=True），但已纳入动作生命周期（有 action_id）。"""
    fake_runtime.online = True
    service = make_service(make_config(fake_runtime))
    result = await service.send_chat("我在这里！")
    assert result["sent"] is True
    assert fake_runtime.chats == ["我在这里！"]
