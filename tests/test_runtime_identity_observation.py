"""Phase 7F.5.1 §五.A（Service 集成测试）：身份观测必须真的贯穿调用链。

这组测试**不直接调 _note_runtime_instance()**（任务书 §二.5 明确不算集成证据），而是：

* 起一个**真实的本地 HTTP 服务器**（模拟外部托管的 Node Runtime）；
* 用**真实的 MinecraftService + 真实的 MinecraftRuntimeClient**发请求
  （服务用 config.runtime_port 自己建客户端，所以走的是生产同一条代码路径）；
* 断言实例身份是否被正确记录、实例变化是否只触发**一次**对账、
  以及失败/401/无效响应/缺字段**绝不**被当成"实例变了"。
"""

from __future__ import annotations

import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService

INSTANCE_A = "rt_aaaa_external"
INSTANCE_B = "rt_bbbb_external"


class _RuntimeHandler(BaseHTTPRequestHandler):
    """极简 Runtime 桩：可切换实例、可切换故障模式，并记录真实收到的请求。"""

    instance = INSTANCE_A
    mode = "ok"
    seen: list[tuple[str, str]] = []

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")

    def _handle(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        type(self).seen.append((method, path))
        mode = type(self).mode
        if mode == "unauthorized":
            self._send(401, {"ok": False, "error": {"code": "auth.invalid", "message": "no"}})
            return
        if mode == "invalid":
            # 200 但**不是 JSON**：客户端必须判成 bad_response，绝不能从中读出身份。
            body = b"<html>nope</html>"
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if mode == "fail":
            self._send(503, {"ok": False, "error": {"code": "runtime.busy", "message": "down"}})
            return
        payload: dict[str, Any] = {"ok": True, "status": "CONNECTED", "session_id": "s1"}
        if mode != "no_instance":
            payload["runtime_instance"] = type(self).instance
        self._send(200, payload)

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture()
def runtime_server() -> Any:
    _RuntimeHandler.instance = INSTANCE_A
    _RuntimeHandler.mode = "ok"
    _RuntimeHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _RuntimeHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _service(
    port: int, *, auto_start: bool = False, changes: list[str] | None = None
) -> MinecraftService:
    """真实的 MinecraftService：客户端由它自己按 runtime_port 建（= 生产路径）。"""
    config = MinecraftConfig(
        enabled=True,
        runtime_port=port,
        auto_start_runtime=auto_start,
        request_timeout_seconds=5.0,
    )
    service = MinecraftService(object(), config)  # type: ignore[arg-type] - bot 在这些路径里不用
    recorded = changes if changes is not None else []

    def on_change(instance: str) -> None:
        recorded.append(instance)

    service.on_runtime_instance_changed = on_change
    service._changes = recorded  # type: ignore[attr-defined] - 供断言读取
    return service


class TestIdentityObservationThroughRealRequests:
    async def test_first_successful_health_records_the_instance(self, runtime_server: int) -> None:
        service = _service(runtime_server)
        assert service.runtime_instance() == "", "探测之前不得凭空有身份"
        await service._ensure_runtime()
        assert service.runtime_instance() == INSTANCE_A
        assert ("GET", "/minecraft/health") in _RuntimeHandler.seen, "必须真的走了 HTTP 路径"
        await service.stop()

    async def test_external_restart_is_detected_through_status(self, runtime_server: int) -> None:
        service = _service(runtime_server)
        await service._ensure_runtime()
        assert service.runtime_instance() == INSTANCE_A
        # Python 一直不退出；外部托管的 Runtime 换成了实例 B。
        _RuntimeHandler.instance = INSTANCE_B
        await service.status()  # 走真实的 /minecraft/status 请求
        assert service.runtime_instance() == INSTANCE_B
        assert service._changes == [INSTANCE_B], "实例变化必须触发**一次**对账"  # type: ignore[attr-defined]
        await service.stop()

    async def test_external_restart_is_detected_through_health(self, runtime_server: int) -> None:
        service = _service(runtime_server)
        await service._ensure_runtime()
        _RuntimeHandler.instance = INSTANCE_B
        await service._ensure_runtime()
        assert service.runtime_instance() == INSTANCE_B
        assert service._changes == [INSTANCE_B]  # type: ignore[attr-defined]
        await service.stop()

    async def test_identity_tracking_works_with_auto_start_disabled(
        self, runtime_server: int
    ) -> None:
        # 任务书 §二.5 的核心场景：外部托管（auto_start_runtime=false）也必须能识别实例。
        service = _service(runtime_server, auto_start=False)
        await service.probe_runtime_instance()
        assert service.runtime_instance() == INSTANCE_A
        _RuntimeHandler.instance = INSTANCE_B
        assert await service.probe_runtime_instance() == INSTANCE_B
        assert service._changes == [INSTANCE_B]  # type: ignore[attr-defined]
        await service.stop()

    async def test_same_instance_is_not_a_change(self, runtime_server: int) -> None:
        service = _service(runtime_server)
        await service._ensure_runtime()
        await service.probe_runtime_instance()
        await service.status()
        assert service.runtime_instance() == INSTANCE_A
        assert service._changes == [], "同一个实例绝不能重复通知对账"  # type: ignore[attr-defined]
        await service.stop()

    async def test_transient_failure_then_recovery_is_not_a_change(
        self, runtime_server: int
    ) -> None:
        service = _service(runtime_server)
        await service._ensure_runtime()
        _RuntimeHandler.mode = "fail"
        await service.status()  # 失败：什么都不该发生
        assert service.runtime_instance() == INSTANCE_A
        assert service._changes == []  # type: ignore[attr-defined]
        _RuntimeHandler.mode = "ok"
        await service.status()  # 恢复成**同一个**实例 A
        assert service.runtime_instance() == INSTANCE_A
        assert service._changes == [], "暂时失败后恢复同一实例不得误判成变化"  # type: ignore[attr-defined]
        await service.stop()

    @pytest.mark.parametrize("mode", ["unauthorized", "invalid", "no_instance"])
    async def test_untrustworthy_responses_never_fake_an_identity(
        self, runtime_server: int, mode: str
    ) -> None:
        service = _service(runtime_server)
        _RuntimeHandler.mode = mode
        await service.status()
        assert service.runtime_instance() == "", "401 / 无效响应 / 缺字段都不得伪造身份"
        assert service._changes == []  # type: ignore[attr-defined]
        _RuntimeHandler.mode = "ok"
        await service._ensure_runtime()
        assert service.runtime_instance() == INSTANCE_A
        # 之后再出现不可信响应：身份必须保持在 A，不得被清掉、也不得被改。
        _RuntimeHandler.mode = mode
        await service.status()
        assert service.runtime_instance() == INSTANCE_A
        assert service._changes == []  # type: ignore[attr-defined]
        await service.stop()


class TestNoDeadlockAcrossObservationAndControl:
    async def test_change_callback_can_call_stop_and_probe_without_deadlock(
        self, runtime_server: int
    ) -> None:
        """§五.A.8：对账回调里等 stop / 再发健康检查，不得与身份观测互相递归等待。

        这里让回调做两件最容易造成死锁的事：真的发一次 stop（它会走 _ensure_runtime →
        再一次身份观测）和真的再探测一次身份。靠的是 _note_runtime_instance **先更新
        实例、再通知**，并且通知是 create_task 异步跑（不同步递归）。
        """
        service = _service(runtime_server)
        await service._ensure_runtime()
        done = asyncio.Event()
        calls: list[str] = []

        async def on_change(instance: str) -> None:
            calls.append(instance)
            # 控制面调用（会再次触达 _ensure_runtime -> 身份观测）
            await service.stop_action()
            # 再主动探测一次（还是同一个实例 -> 不得再次触发回调）
            assert await service.probe_runtime_instance() == instance
            done.set()

        service.on_runtime_instance_changed = on_change
        _RuntimeHandler.instance = INSTANCE_B
        await service.status()
        await asyncio.wait_for(done.wait(), timeout=5.0)  # 死锁的话这里超时
        assert calls == [INSTANCE_B]
        assert ("POST", "/minecraft/stop") in _RuntimeHandler.seen
        assert service.runtime_instance() == INSTANCE_B
        await service.stop()
