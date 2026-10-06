"""MinecraftService 单元测试（Phase 1）：状态镜像、错误翻译、进程托管边界。

runtime 用一个进程内 aiohttp 假服务器模拟（Bridge API 同形），不需要 Node。
"""

from __future__ import annotations

import asyncio
import socket
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.integrations.minecraft.service import (
    MinecraftBridgeError,
    MinecraftDisabled,
    MinecraftInvalidTarget,
    MinecraftNotConnected,
    MinecraftRuntimeDown,
    MinecraftService,
)


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class FakeRuntime:
    """`runtime.js` 的进程内替身：同形的 Bridge API + 可编排的故障。"""

    def __init__(self) -> None:
        self.port = free_port()
        self.online = False
        self.busy = False
        self.health_ok = True
        self.chats: list[str] = []
        self.connects: list[tuple[str, int]] = []
        self.disconnects = 0
        # Phase 3B：Action Runtime
        self.look_at_calls: list[dict[str, Any]] = []
        self.stop_calls = 0
        self.look_at_plan: list[dict[str, Any]] = []  # 依次消费；空 = 默认 SUCCEEDED
        self.stop_cancelled: list[str] = []
        # Phase 3C：move_to + Pathfinder 诊断
        self.move_to_calls: list[dict[str, Any]] = []
        self.move_to_plan: list[dict[str, Any]] = []  # 依次消费；空 = 默认 SUCCEEDED
        # Phase 3D：follow_player（持续型：默认返回 RUNNING）
        self.follow_player_calls: list[dict[str, Any]] = []
        self.follow_player_plan: list[dict[str, Any]] = []
        # Phase 4B：dig（持续型：默认返回 RUNNING）
        self.dig_calls: list[dict[str, Any]] = []
        self.dig_plan: list[dict[str, Any]] = []
        # Phase 4C：inventory（只读）+ place（持续型）
        self.inventory_calls = 0
        self.inventory_payload: dict[str, Any] = {
            "ok": True,
            "online": True,
            "selected_hotbar_slot": 0,
            "held_item": {"name": "dirt", "count": 12},
            "items": [{"name": "dirt", "count": 12}],
        }
        self.place_calls: list[dict[str, Any]] = []
        self.place_plan: list[dict[str, Any]] = []
        # Phase 4D：inventory/slots（只读调试视图）+ equip + inventory_move（持续型）
        self.inventory_slots_calls = 0
        self.inventory_slots_payload: dict[str, Any] = {
            "ok": True,
            "online": True,
            "hotbar_start": 36,
            "inventory_start": 9,
            "slots": [
                {"slot": 9, "name": "dirt", "count": 12, "hotbar": False},
                {"slot": 36, "name": "dirt", "count": 12, "hotbar": True},
                {"slot": 37, "name": "sand", "count": 24, "hotbar": True},
            ],
        }
        self.equip_calls: list[dict[str, Any]] = []
        self.equip_plan: list[dict[str, Any]] = []
        self.inventory_move_calls: list[dict[str, Any]] = []
        self.inventory_move_plan: list[dict[str, Any]] = []
        self.pathfinder_state: dict[str, Any] = {
            "goal": None,
            "target": None,
            "distance": None,
            "moving": False,
        }
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/minecraft/health", self._health)
        app.router.add_post("/minecraft/connect", self._connect)
        app.router.add_get("/minecraft/status", self._status)
        app.router.add_post("/minecraft/chat", self._chat)
        app.router.add_post("/minecraft/disconnect", self._disconnect)
        app.router.add_get("/minecraft/world/snapshot", self._world_snapshot)
        app.router.add_post("/minecraft/look_at", self._look_at)
        app.router.add_post("/minecraft/move_to", self._move_to)
        app.router.add_post("/minecraft/follow_player", self._follow_player)
        app.router.add_post("/minecraft/dig", self._dig)
        app.router.add_get("/minecraft/inventory", self._inventory)
        app.router.add_post("/minecraft/place", self._place)
        app.router.add_get("/minecraft/inventory/slots", self._inventory_slots)
        app.router.add_post("/minecraft/equip", self._equip)
        app.router.add_post("/minecraft/inventory_move", self._inventory_move)
        app.router.add_post("/minecraft/stop", self._stop)
        self._runner = web.AppRunner(app, access_log=None)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", self.port)
        await site.start()

    async def stop(self) -> None:
        if self._runner is not None:
            await self._runner.cleanup()
            self._runner = None

    async def _health(self, request: web.Request) -> web.Response:
        if not self.health_ok:
            raise web.HTTPServiceUnavailable(text="down")
        return web.json_response({"ok": True})

    async def _connect(self, request: web.Request) -> web.Response:
        body = await request.json()
        if self.busy:
            return web.json_response(
                {"ok": False, "error": {"code": "session.active", "message": "已有会话"}},
                status=409,
            )
        host, port = str(body["host"]), int(body["port"])
        if not host or not 1 <= port <= 65535:
            return web.json_response(
                {"ok": False, "error": {"code": "target.invalid", "message": "bad target"}},
                status=400,
            )
        self.connects.append((host, port))
        self.online = True
        return web.json_response({"ok": True, "session_id": "s-fake", "status": "CONNECTING"})

    async def _status(self, request: web.Request) -> web.Response:
        return web.json_response(
            {
                "ok": True,
                "status": "ONLINE" if self.online else "DISCONNECTED",
                "session_id": "s-fake" if self.online else None,
                "host": "127.0.0.1" if self.online else None,
                "port": self.port if self.online else None,
                "username": "GuanTou" if self.online else None,
                "dimension": "overworld" if self.online else None,
                "position": {"x": 1.0, "y": 2.0, "z": 3.0} if self.online else None,
                "health": 20 if self.online else None,
                "action": {
                    "action": None,
                    "action_id": None,
                    "status": "IDLE",
                    "started_at": None,
                    "finished_at": None,
                    "elapsed_ms": None,
                },
                "pathfinder": dict(self.pathfinder_state),
            }
        )

    async def _chat(self, request: web.Request) -> web.Response:
        body = await request.json()
        if not self.online:
            return web.json_response(
                {"ok": False, "error": {"code": "chat.not_online", "message": "offline"}},
                status=400,
            )
        self.chats.append(str(body["message"]))
        return web.json_response({"ok": True, "sent": True})

    async def _disconnect(self, request: web.Request) -> web.Response:
        self.disconnects += 1
        self.online = False
        return web.json_response({"ok": True, "status": "DISCONNECTED"})

    async def _look_at(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.look_at_calls.append(body)
        plan = self.look_at_plan.pop(0) if self.look_at_plan else {"status": "SUCCEEDED"}
        if "error" in plan:
            code, status = plan["error"]
            return web.json_response(
                {"ok": False, "error": {"code": code, "message": f"{code}（fake runtime）"}},
                status=status,
            )
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_fake_1"),
                "action": "look_at",
                "status": plan["status"],
            }
        )

    async def _move_to(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.move_to_calls.append(body)
        # Phase 3E：move_to 是持续型动作——默认启动即 RUNNING，终点由事件送达
        plan = self.move_to_plan.pop(0) if self.move_to_plan else {"status": "RUNNING"}
        if "error" in plan:
            code, status = plan["error"]
            return web.json_response(
                {"ok": False, "error": {"code": code, "message": f"{code}（fake runtime）"}},
                status=status,
            )
        response: dict[str, Any] = {
            "ok": True,
            "action_id": plan.get("action_id", "act_move_1"),
            "action": "move_to",
            "status": plan["status"],
        }
        if plan.get("result") is not None or plan["status"] == "SUCCEEDED":
            response["result"] = plan.get(
                "result",
                {
                    "target": body,
                    "final_position": {"x": body["x"], "y": body["y"], "z": body["z"]},
                    "distance_to_target": 0.42,
                },
            )
        return web.json_response(response)

    async def _follow_player(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.follow_player_calls.append(body)
        plan = self.follow_player_plan.pop(0) if self.follow_player_plan else {"status": "RUNNING"}
        if "error" in plan:
            code, status = plan["error"]
            return web.json_response(
                {"ok": False, "error": {"code": code, "message": f"{code}（fake runtime）"}},
                status=status,
            )
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_follow_1"),
                "action": "follow_player",
                "status": plan["status"],
            }
        )

    async def _dig(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.dig_calls.append(body)
        plan = self.dig_plan.pop(0) if self.dig_plan else {"status": "RUNNING"}
        if "error" in plan:
            code, status = plan["error"]
            payload: dict[str, Any] = {
                "ok": False,
                "error": {"code": code, "message": f"{code}（fake runtime）"},
            }
            if plan.get("detail"):
                payload["error"]["detail"] = plan["detail"]
            return web.json_response(payload, status=status)
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_dig_1"),
                "action": "dig",
                "status": plan["status"],
            }
        )

    async def _inventory(self, request: web.Request) -> web.Response:
        self.inventory_calls += 1
        return web.json_response(dict(self.inventory_payload))

    async def _inventory_slots(self, request: web.Request) -> web.Response:
        self.inventory_slots_calls += 1
        return web.json_response(dict(self.inventory_slots_payload))

    async def _equip(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.equip_calls.append(body)
        plan = self.equip_plan.pop(0) if self.equip_plan else {"status": "RUNNING"}
        if "error" in plan:
            code, status = plan["error"]
            payload: dict[str, Any] = {
                "ok": False,
                "error": {"code": code, "message": f"{code}（fake runtime）"},
            }
            if plan.get("detail"):
                payload["error"]["detail"] = plan["detail"]
            return web.json_response(payload, status=status)
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_equip_1"),
                "action": "equip",
                "status": plan["status"],
                **plan.get("extra", {}),
            }
        )

    async def _inventory_move(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.inventory_move_calls.append(body)
        plan = (
            self.inventory_move_plan.pop(0) if self.inventory_move_plan else {"status": "RUNNING"}
        )
        if "error" in plan:
            code, status = plan["error"]
            payload: dict[str, Any] = {
                "ok": False,
                "error": {"code": code, "message": f"{code}（fake runtime）"},
            }
            if plan.get("detail"):
                payload["error"]["detail"] = plan["detail"]
            return web.json_response(payload, status=status)
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_move_item_1"),
                "action": "inventory_move",
                "status": plan["status"],
                **plan.get("extra", {}),
            }
        )

    async def _place(self, request: web.Request) -> web.Response:
        body = await request.json()
        self.place_calls.append(body)
        plan = self.place_plan.pop(0) if self.place_plan else {"status": "RUNNING"}
        if "error" in plan:
            code, status = plan["error"]
            payload: dict[str, Any] = {
                "ok": False,
                "error": {"code": code, "message": f"{code}（fake runtime）"},
            }
            if plan.get("detail"):
                payload["error"]["detail"] = plan["detail"]
            return web.json_response(payload, status=status)
        return web.json_response(
            {
                "ok": True,
                "action_id": plan.get("action_id", "act_place_1"),
                "action": "place",
                "status": plan["status"],
            }
        )

    async def _stop(self, request: web.Request) -> web.Response:
        self.stop_calls += 1
        return web.json_response(
            {"ok": True, "status": "IDLE", "cancelled": list(self.stop_cancelled)}
        )

    async def _world_snapshot(self, request: web.Request) -> web.Response:
        from tests.test_minecraft_world import raw_payload

        if not self.online:
            return web.json_response({"ok": True, "online": False, "fetched_at": 0.0})
        return web.json_response(raw_payload())


def make_config(fake: FakeRuntime | None, **overrides: object) -> MinecraftConfig:
    values: dict[str, object] = {
        "enabled": True,
        "auto_start_runtime": False,
        # None = 一个没人监听的合法端口（连接被拒 = runtime 不可达）
        "runtime_port": fake.port if fake else free_port(),
    }
    values.update(overrides)
    return MinecraftConfig(**values)  # type: ignore[arg-type]


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

    def factory(config: MinecraftConfig) -> MinecraftService:
        service = MinecraftService(None, config)  # type: ignore[arg-type]
        created.append(service)
        return service

    yield factory
    for service in created:
        await service._cleanup()


async def test_join_updates_mirror_and_returns_session(fake_runtime: FakeRuntime, make_service):
    service = make_service(make_config(fake_runtime))
    result = await service.join("127.0.0.1", 25565)
    assert result["session_id"] == "s-fake"
    assert result["status"] == "CONNECTING"
    assert service.snapshot()["connection"]["status"] == "CONNECTING"
    assert service.snapshot()["connection"]["host"] == "127.0.0.1"


async def test_join_validates_target(fake_runtime: FakeRuntime, make_service):
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftInvalidTarget):
        await service.join("", 25565)
    with pytest.raises(MinecraftInvalidTarget):
        await service.join("127.0.0.1", 0)
    with pytest.raises(MinecraftInvalidTarget):
        await service.join("127.0.0.1", 99999)
    with pytest.raises(MinecraftInvalidTarget):
        await service.join("x" * 300, 25565)


async def test_join_translates_busy_from_runtime(fake_runtime: FakeRuntime):
    fake_runtime.busy = True
    service = MinecraftService(None, make_config(fake_runtime))  # type: ignore[arg-type]
    with pytest.raises(MinecraftBridgeError) as excinfo:
        await service.join("127.0.0.1", 25565)
    assert excinfo.value.code == "minecraft.session_active"


async def test_join_when_runtime_down_raises_stable_error(make_service):
    service = make_service(make_config(None))
    with pytest.raises(MinecraftRuntimeDown):
        await service.join("127.0.0.1", 25565)


async def test_receive_event_updates_mirror_and_notifies_listeners(make_service):
    service = make_service(make_config(None))
    seen: list[MinecraftBridgeEvent] = []
    service.add_listener(seen.append)

    await service.receive_event(
        {"event": "minecraft.spawned", "session_id": "s1", "timestamp": 1.0, "username": "GuanTou"}
    )
    assert service.snapshot()["connection"]["status"] == "ONLINE"
    assert len(seen) == 1

    await service.receive_event(
        {"event": "minecraft.kicked", "session_id": "s1", "timestamp": 2.0, "reason": "banned"}
    )
    assert service.snapshot()["connection"]["kicked_reason"] == "banned"

    await service.receive_event(
        {"event": "minecraft.disconnected", "session_id": "s1", "timestamp": 3.0}
    )
    snapshot = service.snapshot()
    assert snapshot["connection"]["status"] == "DISCONNECTED"
    assert snapshot["connection"]["session_id"] is None
    assert len(seen) == 3


async def test_receive_event_dedupes_runtime_retries(make_service):
    service = make_service(make_config(None))
    seen: list[MinecraftBridgeEvent] = []
    service.add_listener(seen.append)
    payload = {"event": "minecraft.chat", "session_id": "s1", "timestamp": 1.0, "message": "hi"}
    await service.receive_event(dict(payload))
    await service.receive_event(dict(payload))
    assert len(seen) == 1


async def test_receive_event_rejects_unknown_type(make_service):
    service = make_service(make_config(None))
    with pytest.raises(ValueError):
        await service.receive_event({"event": "minecraft.explode", "timestamp": 1.0})


async def test_chat_offline_translates_to_not_connected(fake_runtime: FakeRuntime, make_service):
    service = make_service(make_config(fake_runtime))
    with pytest.raises(MinecraftNotConnected):
        await service.send_chat("我在这里！")
    assert fake_runtime.chats == []


async def test_chat_ok(fake_runtime: FakeRuntime):
    fake_runtime.online = True
    service = MinecraftService(None, make_config(fake_runtime))  # type: ignore[arg-type]
    assert (await service.send_chat("我在这里！"))["sent"] is True
    assert fake_runtime.chats == ["我在这里！"]


async def test_leave_is_idempotent_even_when_runtime_dead(make_service):
    service = make_service(make_config(None))
    result = await service.leave()
    assert result["ok"] is True
    assert service.snapshot()["connection"]["status"] == "DISCONNECTED"


async def test_disabled_config_raises_disabled(make_service):
    service = make_service(make_config(None, enabled=False))
    with pytest.raises(MinecraftDisabled):
        await service.join("127.0.0.1", 25565)
    with pytest.raises(MinecraftDisabled):
        await service.leave()
    with pytest.raises(MinecraftDisabled):
        await service.send_chat("hi")


async def test_poll_loop_reconciles_status(fake_runtime: FakeRuntime, make_service):
    service = make_service(make_config(fake_runtime, poll_interval_seconds=1.0))
    fake_runtime.online = True
    service._started = True
    task = asyncio.create_task(service._poll_loop())
    await asyncio.sleep(1.3)
    assert service.snapshot()["connection"]["status"] == "ONLINE"
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


def test_default_runtime_dir_resolves_against_project_root():
    """防回归：MC_AUTH_FILE 必须是绝对路径——Node 子进程的 CWD 就在 runtime
    目录里，相对路径会被二次拼接，auth.json 永远读不到（曾静默回退默认名）。"""
    from app.config.settings import PROJECT_ROOT

    service = MinecraftService(None, make_config(None))  # type: ignore[arg-type]
    assert service._runtime_dir() == PROJECT_ROOT / "minecraft_runtime"
    auth = Path(service._runtime_env()["MC_AUTH_FILE"])
    assert auth.is_absolute()
    assert auth == PROJECT_ROOT / "minecraft_runtime" / "auth.json"


def test_auth_configured_detects_local_auth_file(tmp_path):
    (tmp_path / "auth.json").write_text("{}", encoding="utf-8")
    service = MinecraftService(
        None,
        MinecraftConfig(enabled=True, runtime_dir=str(tmp_path)),  # type: ignore[arg-type]
    )
    assert service.auth_configured is True
    assert Path(service._runtime_env()["MC_AUTH_FILE"]) == tmp_path / "auth.json"

    empty = tmp_path / "empty"
    empty.mkdir()
    service2 = MinecraftService(
        None,
        MinecraftConfig(enabled=True, runtime_dir=str(empty)),  # type: ignore[arg-type]
    )
    assert service2.auth_configured is False


# ------------------------------------------------------- Phase 2 感知集成（真 HTTP）


async def test_start_runs_perception_and_world_view(fake_runtime: FakeRuntime, make_service):
    """集成：service.start() 拉起感知循环 → 进世界事件后 World View 可读语义模型。"""
    fake_runtime.online = True
    service = make_service(
        make_config(
            fake_runtime,
            perception_enabled=True,
            near_interval_seconds=0.3,
            local_interval_seconds=1.0,
            extended_interval_seconds=5.0,
            external_callback_url="http://127.0.0.1:9/events",
        )
    )
    await service.start()
    try:
        # 模拟进世界（状态镜像 ONLINE 是感知开扫的前置条件）
        await service.receive_event(
            {
                "event": "minecraft.spawned",
                "session_id": "s-int",
                "timestamp": 1.0,
                "username": "Catodayo",
            }
        )
        deadline = asyncio.get_event_loop().time() + 6
        view: dict = {}
        while asyncio.get_event_loop().time() < deadline:
            view = service.world_view()
            if view.get("available"):
                break
            await asyncio.sleep(0.1)
        assert view["available"] is True, f"感知未在期限内产出：{view}"
        assert view["online"] is True
        assert view["semantic"]["self"]["dimension"] == "overworld"
        assert view["semantic"]["self"]["location"] == "plains"
        # raw 原文必须保留（Semantic 不是唯一数据源）
        assert view["raw"]["self"]["dimension"] == "overworld"
        assert view["layers"]["near"]["age_seconds"] is not None

        # 断开事件：World View 立即失效（Test 10）
        await service.receive_event(
            {"event": "minecraft.disconnected", "session_id": "s-int", "timestamp": 2.0}
        )
        assert service.world_view()["available"] is False
    finally:
        await service.stop()


async def test_perception_disabled_keeps_world_view_unavailable(
    fake_runtime: FakeRuntime, make_service
):
    service = make_service(
        make_config(
            fake_runtime,
            perception_enabled=False,
            external_callback_url="http://127.0.0.1:9/events",
        )
    )
    view = service.world_view()
    assert view["available"] is False
    assert "perception disabled" in view["reason"]
