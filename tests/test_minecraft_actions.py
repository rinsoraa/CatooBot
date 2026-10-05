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
    MinecraftFollowTargetTooFar,
    MinecraftNotConnected,
    MinecraftPathNotFound,
    MinecraftPlayerLost,
    MinecraftPlayerNotFound,
    MinecraftRuntimeDown,
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


# --------------------------------------------------------------- Phase 3C：move_to
# 说明：§十九 里的 test_move_to_registers_as_exclusive / test_move_to_validates_coordinates
# 在 Node 侧实现（minecraft_runtime/test/move_to.test.js：注册表属性与校验器直测）；
# 这里覆盖服务层语义与错误翻译。
# Phase 3E：move_to 与 follow_player 同为持续型动作（HTTP 启动即 RUNNING，
# 终点/失败经 action 事件送达）——下面的断言按这个契约更新。


async def test_move_to_returns_running(fake_runtime: FakeRuntime, make_service) -> None:
    service = make_service(make_config(fake_runtime))
    result = await service.move_to(120, 64, -230)
    # 启动成功即返回 RUNNING（§三十三：绝不等导航完成）
    assert result["status"] == "RUNNING"
    assert result["action"] == "move_to"
    assert str(result["action_id"]).startswith("act_")
    assert fake_runtime.move_to_calls == [{"x": 120.0, "y": 64.0, "z": -230.0}]


async def test_move_to_terminal_result_arrives_via_event(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """终点位置只经 ``minecraft.action.completed`` 事件回来（HTTP 早已返回 RUNNING）。"""
    service = make_service(make_config(fake_runtime))
    await service.receive_event(
        action_event(
            "minecraft.action.started", action="move_to", action_id="act_moving", status="RUNNING"
        )
    )
    assert service.snapshot()["action"]["status"] == "RUNNING"
    await service.receive_event(
        action_event(
            "minecraft.action.completed",
            action="move_to",
            action_id="act_moving",
            status="SUCCEEDED",
            result={
                "target": {"x": 120.0, "y": 64.0, "z": -230.0},
                "final_position": {"x": 119.6, "y": 64.0, "z": -230.4},
                "distance_to_target": 0.42,
            },
        )
    )
    action = service.snapshot()["action"]
    assert action["status"] == "SUCCEEDED"
    assert action["result"]["distance_to_target"] == 0.42
    assert action["result"]["final_position"]["x"] == 119.6
    assert action["error"] is None and action["code"] is None


async def test_move_to_failure_code_arrives_via_event(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """无路径这类失败改为终态事件（runtime 词表原样留在镜像里）。"""
    service = make_service(make_config(fake_runtime))
    await service.receive_event(
        action_event(
            "minecraft.action.failed",
            action="move_to",
            action_id="act_moving",
            status="FAILED",
            code="path.not_found",
            error="无法找到到达目标的非破坏性路径",
        )
    )
    action = service.snapshot()["action"]
    assert action["status"] == "FAILED"
    assert action["code"] == "path.not_found"
    assert "非破坏性路径" in action["error"]


async def test_move_to_rejects_too_far(fake_runtime: FakeRuntime, make_service) -> None:
    """§六：max_distance=64（相对当前位置）；超限在 Service 层就被拒（不发 HTTP）。"""
    fake_runtime.online = True
    service = make_service(make_config(fake_runtime))
    await service.status()  # 让镜像拿到位置 (1, 2, 3)
    with pytest.raises(MinecraftActionInvalid, match="超过 move_to 上限"):
        await service.move_to(100, 2, 3)  # 距 (1,2,3) 99 格
    assert fake_runtime.move_to_calls == []

    # 边界内正常通过（64 格以内）
    result = await service.move_to(60, 2, 3)  # 59 格
    assert result["status"] == "RUNNING"


async def test_move_to_rejects_offline(fake_runtime: FakeRuntime, make_service) -> None:
    fake_runtime.move_to_plan.append({"error": ("action.not_online", 400)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftNotConnected):
        await service.move_to(1, 2, 3)


async def test_move_to_busy(fake_runtime: FakeRuntime, make_service) -> None:
    """§八：同一时间只允许一个前台动作；移动中再来 → action.busy。"""
    fake_runtime.move_to_plan.append({"error": ("action.busy", 409)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftActionBusy) as excinfo:
        await service.move_to(1, 2, 3)
    assert excinfo.value.code == "minecraft.action_busy"
    assert excinfo.value.status == 409


async def test_move_to_timeout(fake_runtime: FakeRuntime, make_service) -> None:
    """超时后 runtime 回的是**事件**；若启动阶段就同步回 TIMEOUT（防御），服务照样透传。"""
    fake_runtime.move_to_plan.append({"status": "TIMEOUT", "action_id": "act_move_timeout"})
    service = make_service(make_config(fake_runtime))
    result = await service.move_to(1, 2, 3)
    assert result["status"] == "TIMEOUT"
    assert result["action_id"] == "act_move_timeout"


async def test_move_to_no_path(fake_runtime: FakeRuntime, make_service) -> None:
    """§十：目标不可达 → ``minecraft.path_not_found``（绝不自动挖/搭/绕圈）。

    正常路径下这发生在启动之后（终态事件）；这里是 runtime 在启动阶段就同步
    拒绝时的错误翻译（防御性，词表不变）。
    """
    fake_runtime.move_to_plan.append({"error": ("path.not_found", 500)})
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftPathNotFound) as excinfo:
        await service.move_to(1, 2, 3)
    assert excinfo.value.code == "minecraft.path_not_found"
    assert excinfo.value.status == 500


async def test_move_to_cleanup_clears_goal(fake_runtime: FakeRuntime, make_service) -> None:
    """§十一/§十二：取消后 Pathfinder Goal 必须清空、不再移动。

    真正的 in-process 断言（goal == null、isMoving() == false、位置冻结）在
    Node E2E Test B 里对真实 pathfinder 执行；这里是服务层镜像的对应验证。
    """
    fake_runtime.online = True
    fake_runtime.pathfinder_state = {
        "goal": "GoalNear",
        "target": {"x": 10.0, "y": 64.0, "z": 10.0},
        "moving": True,
    }
    service = make_service(make_config(fake_runtime))
    running = await service.status()
    assert running["pathfinder"]["goal"] == "GoalNear"
    assert running["pathfinder"]["moving"] is True

    # stop 之后：runtime 已清 Goal（E2E 已证），镜像随之回落
    fake_runtime.pathfinder_state = {"goal": None, "target": None, "moving": False}
    fake_runtime.stop_cancelled = ["act_moving"]
    result = await service.stop_action()
    assert result["cancelled"] == ["act_moving"]
    stopped = await service.status()
    assert stopped["pathfinder"]["goal"] is None
    assert stopped["pathfinder"]["moving"] is False


async def test_move_to_mirror_survives_runtime_down(make_service) -> None:
    """runtime 不可达 → 503 runtime_down；镜像 pathfinder 回落为空闲。"""
    service = make_service(make_config(None))
    with pytest.raises(MinecraftRuntimeDown):
        await service.move_to(1, 2, 3)
    assert service.snapshot()["pathfinder"] == {
        "goal": None,
        "target": None,
        "distance": None,
        "moving": False,
    }


# ----------------------------------------------------------- Phase 3D：follow_player
# §二十二 的 Node Unit 命名用例（registered/exclusive/risk/timeout/invalid_username/
# invalid_distance/target_not_found/cleanup_clears_goal）在 Node 侧实现
# （minecraft_runtime/test/follow_player.test.js）；这里覆盖服务层语义与错误翻译。


async def _online_service(fake_runtime: FakeRuntime, make_service) -> MinecraftService:
    fake_runtime.online = True
    service = make_service(make_config(fake_runtime))
    await service.status()  # 让镜像变成 ONLINE（follow 的 Service 层要求）
    return service


async def test_follow_player_success(fake_runtime: FakeRuntime, make_service) -> None:
    service = await _online_service(fake_runtime, make_service)
    result = await service.follow_player("空凛")
    assert result["status"] == "RUNNING"  # 持续型：启动即返回，终态经事件送达
    assert result["action"] == "follow_player"
    assert str(result["action_id"]).startswith("act_")
    assert fake_runtime.follow_player_calls == [{"username": "空凛", "distance": 2.5}]


async def test_follow_player_custom_distance(fake_runtime: FakeRuntime, make_service) -> None:
    service = await _online_service(fake_runtime, make_service)
    await service.follow_player("空凛", 4)
    assert fake_runtime.follow_player_calls == [{"username": "空凛", "distance": 4.0}]


async def test_follow_player_invalid_username(fake_runtime: FakeRuntime, make_service) -> None:
    service = await _online_service(fake_runtime, make_service)
    for bad in ("", "   ", "a" * 17, "bad\nname", "bad\u0000name", 123, None):
        with pytest.raises(MinecraftActionInvalid):
            await service.follow_player(bad)  # type: ignore[arg-type]
    assert fake_runtime.follow_player_calls == []  # 本地校验先于任何网络往返


async def test_follow_player_invalid_distance(fake_runtime: FakeRuntime, make_service) -> None:
    service = await _online_service(fake_runtime, make_service)
    for bad in (1.0, 1.49, 6.01, 9, float("nan"), float("inf"), "2.5"):
        with pytest.raises(MinecraftActionInvalid):
            await service.follow_player("空凛", bad)  # type: ignore[arg-type]
    assert fake_runtime.follow_player_calls == []


async def test_follow_player_requires_online(fake_runtime: FakeRuntime, make_service) -> None:
    """Service 层验证 Minecraft ONLINE：镜像不是 ONLINE 时直接拒绝。"""
    service = make_service(make_config(fake_runtime))  # 未调用 status() → 镜像 DISCONNECTED
    with pytest.raises(MinecraftNotConnected, match="无法跟随"):
        await service.follow_player("空凛")
    assert fake_runtime.follow_player_calls == []


async def test_follow_player_target_not_found(fake_runtime: FakeRuntime, make_service) -> None:
    service = await _online_service(fake_runtime, make_service)
    fake_runtime.follow_player_plan.append({"error": ("player.not_found", 404)})
    with pytest.raises(MinecraftPlayerNotFound) as excinfo:
        await service.follow_player("不存在的人")
    assert excinfo.value.code == "minecraft.player_not_found"
    assert excinfo.value.status == 404


async def test_follow_player_lost_and_too_far_translated(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """player.lost / follow.target_too_far 通常经事件上报；同步路径也保持稳定错误码。"""
    service = await _online_service(fake_runtime, make_service)
    fake_runtime.follow_player_plan.append({"error": ("player.lost", 500)})
    with pytest.raises(MinecraftPlayerLost) as lost:
        await service.follow_player("空凛")
    assert lost.value.code == "minecraft.player_lost"

    fake_runtime.follow_player_plan.append({"error": ("follow.target_too_far", 500)})
    with pytest.raises(MinecraftFollowTargetTooFar) as far:
        await service.follow_player("空凛")
    assert far.value.code == "minecraft.follow_target_too_far"


async def test_follow_player_mirror_shows_who_is_followed(
    fake_runtime: FakeRuntime, make_service
) -> None:
    """§十二：跟随期间 status.pathfinder 必须能看出在跟谁、距离多少。"""
    service = await _online_service(fake_runtime, make_service)
    fake_runtime.pathfinder_state = {
        "goal": "GoalFollow",
        "target": {"username": "空凛", "x": 123.5, "y": 64.0, "z": -230.25},
        "distance": 2.5,
        "moving": True,
    }
    snapshot = await service.status()
    pathfinder = snapshot["pathfinder"]
    assert pathfinder["goal"] == "GoalFollow"
    assert pathfinder["target"]["username"] == "空凛"
    assert pathfinder["distance"] == 2.5
    assert pathfinder["moving"] is True

    # 终态（STOP/超时/丢失）之后镜像回落：goal null、moving false
    fake_runtime.pathfinder_state = {
        "goal": None,
        "target": None,
        "distance": None,
        "moving": False,
    }
    settled = await service.status()
    assert settled["pathfinder"]["goal"] is None
    assert settled["pathfinder"]["moving"] is False
