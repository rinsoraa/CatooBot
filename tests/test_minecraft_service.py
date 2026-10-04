"""MinecraftService 单元测试（Phase 1）：状态镜像、错误翻译、进程托管边界。

runtime 用一个进程内 aiohttp 假服务器模拟（Bridge API 同形），不需要 Node。
"""

from __future__ import annotations

import asyncio
import socket

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
        self._runner: web.AppRunner | None = None

    async def start(self) -> None:
        app = web.Application()
        app.router.add_get("/minecraft/health", self._health)
        app.router.add_post("/minecraft/connect", self._connect)
        app.router.add_get("/minecraft/status", self._status)
        app.router.add_post("/minecraft/chat", self._chat)
        app.router.add_post("/minecraft/disconnect", self._disconnect)
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
