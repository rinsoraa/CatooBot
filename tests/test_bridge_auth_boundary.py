"""Phase 7F.5 §七（API 鉴权边界）：**协议层**测试。

用**真实的本地 HTTP 服务器** + 真实的 `MinecraftRuntimeClient` / 服务的凭据注入逻辑，
而不是断言某个局部函数返回了预期值：

* 缺失 / 错误 / 取不到凭据 → 请求被拒（401），且**不产生任何副作用**；
* 有效凭据 → 正常放行，且客户端确实带了 `Authorization`；
* 凭据绝不出现在错误消息、响应体里；
* 凭据注入是**确定且可审计**的（每进程一份、重启换新、经环境变量注入 Node）；
* 鉴权失败是**明确答复**，不是"状态未知"（不会被当成"动作可能已接受"）。
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.runtime_client import (
    MinecraftRuntimeClient,
    MinecraftRuntimeError,
)
from app.integrations.minecraft.service import MinecraftService

EXPECTED_TOKEN = "rt7f5-secret-bridge-token"
LIVE_INSTANCE = "rt_live_instance_1"


class _AuthHandler(BaseHTTPRequestHandler):
    """真实 HTTP 入口：把实际收到的 Authorization 记下来，凭据不符就 401。"""

    seen: list[str] = []
    expected = EXPECTED_TOKEN

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的约定
        supplied = str(self.headers.get("Authorization") or "")
        type(self).seen.append(supplied)
        if supplied != f"Bearer {self.expected}":
            self._send(
                401,
                {"ok": False, "error": {"code": "auth.invalid", "message": "bridge token 无效"}},
            )
            return
        self._send(
            200,
            {"ok": True, "uptime_seconds": 1, "runtime_instance": LIVE_INSTANCE},
        )

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args: Any) -> None:
        return


class _Stub:
    """只带凭据注入 / 身份记账需要的属性（不必为此拉起真实服务与 Node 进程）。"""

    def __init__(self, *, token: str | None = None) -> None:
        self.config = MinecraftConfig()
        self.bridge_token = token
        self.callback_token = None
        self.callback_url = ""
        self._runtime_instance = ""
        self.on_runtime_instance_changed: Any = None

    def _runtime_dir(self) -> Path:
        return Path("minecraft_runtime")


@pytest.fixture()
def auth_server() -> Any:
    _AuthHandler.seen = []
    server = HTTPServer(("127.0.0.1", 0), _AuthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = int(server.server_address[1])
    try:
        yield port
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


async def test_valid_credential_is_actually_sent_and_allowed(auth_server: int) -> None:
    client = MinecraftRuntimeClient(
        f"http://127.0.0.1:{auth_server}", token_getter=lambda: EXPECTED_TOKEN
    )
    try:
        payload = await client.health()
    finally:
        await client.close()
    assert payload["ok"] is True
    assert payload["runtime_instance"] == LIVE_INSTANCE
    assert _AuthHandler.seen == [f"Bearer {EXPECTED_TOKEN}"], "客户端必须真的带上凭据"


async def test_missing_credential_is_rejected_without_side_effects(auth_server: int) -> None:
    # 没有 token_getter ⇒ 请求头里根本没有 Authorization ⇒ 必须被拒。
    client = MinecraftRuntimeClient(f"http://127.0.0.1:{auth_server}")
    with pytest.raises(MinecraftRuntimeError) as excinfo:
        await client.health()
    await client.close()
    assert excinfo.value.code == "auth.invalid"
    assert excinfo.value.status == 401
    assert _AuthHandler.seen == [""], "确实没有携带凭据"
    assert EXPECTED_TOKEN not in str(excinfo.value), "凭据不得出现在错误消息里"


async def test_wrong_credential_is_rejected(auth_server: int) -> None:
    client = MinecraftRuntimeClient(
        f"http://127.0.0.1:{auth_server}", token_getter=lambda: "wrong-token"
    )
    with pytest.raises(MinecraftRuntimeError) as excinfo:
        await client.health()
    await client.close()
    assert excinfo.value.code == "auth.invalid"
    assert _AuthHandler.seen == ["Bearer wrong-token"]
    assert EXPECTED_TOKEN not in json.dumps(excinfo.value.detail)


async def test_token_getter_failure_does_not_become_anonymous_success(auth_server: int) -> None:
    def _boom() -> str:
        raise RuntimeError("拿不到凭据")

    client = MinecraftRuntimeClient(f"http://127.0.0.1:{auth_server}", token_getter=_boom)
    with pytest.raises(MinecraftRuntimeError) as excinfo:
        await client.health()
    await client.close()
    # fail-closed：拿不到凭据 = 不带凭据 = 被拒；绝不能"悄悄成功"。
    assert excinfo.value.code == "auth.invalid"
    assert _AuthHandler.seen == [""]


async def test_auth_failure_is_definitive_not_ambiguity(auth_server: int) -> None:
    """鉴权失败是 runtime 在**动作开始前**给出的明确答复，不是"状态未知"。"""
    from app.integrations.minecraft.task_adapter import AMBIGUOUS_TRANSPORT_CODES

    client = MinecraftRuntimeClient(f"http://127.0.0.1:{auth_server}")
    with pytest.raises(MinecraftRuntimeError) as excinfo:
        await client.health()
    await client.close()
    assert excinfo.value.code == "auth.invalid"
    assert excinfo.value.code not in AMBIGUOUS_TRANSPORT_CODES, (
        "鉴权失败必须被当成明确拒绝；若被当成歧义，任务会不必要地进入人工核对"
    )


def test_runtime_env_injects_the_same_bridge_token() -> None:
    stub = _Stub(token=EXPECTED_TOKEN)
    env = MinecraftService._runtime_env(stub)
    # 注入 Node 的那份**就是**客户端会带的那份（同一个来源，不会两边不一致）。
    assert env["MC_BRIDGE_TOKEN"] == EXPECTED_TOKEN
    assert stub.bridge_token == EXPECTED_TOKEN


def test_bridge_token_is_generated_once_per_process_and_differs_across_restarts() -> None:
    first = _Stub()
    token_a = MinecraftService._ensure_bridge_token(first)
    assert token_a and len(token_a) >= 24, "必须有足够长的随机凭据"
    assert MinecraftService._ensure_bridge_token(first) == token_a, "同一进程内保持稳定"
    second = _Stub()
    token_b = MinecraftService._ensure_bridge_token(second)
    assert token_b != token_a, "重启 Python 必须换新凭据"
    assert MinecraftService._runtime_env(second)["MC_BRIDGE_TOKEN"] == token_b


def test_bridge_token_field_is_sensitive_so_the_webui_never_sees_a_value() -> None:
    """§六：凭据不得出现在 WebUI —— 注册表必须把它当敏感项（只显示是否已配置）。"""
    from app.web.config_registry import _leaf_label, _note, _sensitive

    key = "minecraft.external_bridge_token"
    assert _sensitive(key) is True, "桥接凭据必须按敏感项处理（值永不返回）"
    assert _leaf_label("external_bridge_token") != "external bridge token", "必须有中文标签"
    assert _note(key, "external_bridge_token"), "必须有一句人话说明"


def test_external_token_from_config_is_used_as_is() -> None:
    # 外部托管时用配置值（现有安全配置途径），不会被自动生成覆盖。
    stub = _Stub(token="configured-external-token")
    assert MinecraftService._ensure_bridge_token(stub) == "configured-external-token"
    assert MinecraftService._runtime_env(stub)["MC_BRIDGE_TOKEN"] == "configured-external-token"
