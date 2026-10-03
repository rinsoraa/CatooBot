"""W6 确认闸门：危险动作必须服务端二次确认（§38）。

每个危险端点在"没带 confirm"时都必须是 409 + 契约错误码，并且状态原封不动；
带上 confirm 后闸门放行，后续以 *别的* 原因失败（404 未知 id 等），绝不是
同一个 409。世界级 reset/reinitialize 会写真实 `data/character_reset_backup`
并清空角色数据，所以只验证它们的拒绝路径（`where harmless`），不真的执行。
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.api_harness import api_server, error_code

API = "/api/v1"

#: 真实 .env 里的名字在测试进程里必须无效（config/reset 会触发 load_dotenv）
_ENV_NAMES = ("CATOOBOT_WEB_PASSWORD",)


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr("app.config.settings.load_dotenv", lambda *args, **kwargs: None)
    for name in _ENV_NAMES:
        os.environ.pop(name, None)
    yield
    for name in _ENV_NAMES:
        os.environ.pop(name, None)


#: (method, path, body, 期望的 confirm 错误码)
REFUSED_WITHOUT_CONFIRM: list[tuple[str, str, dict[str, Any], str]] = [
    ("POST", f"{API}/memories/999999/delete", {}, "memory.confirm_required"),
    ("POST", f"{API}/world/control/reset", {}, "world.confirm_required"),
    ("POST", f"{API}/world/control/reinitialize", {}, "world.confirm_required"),
    ("POST", f"{API}/ai/router/reset", {}, "ai.confirm_required"),
    ("POST", f"{API}/config/reset", {}, "config.confirm_required"),
    ("PUT", f"{API}/config/raw", {"yaml": ""}, "config.confirm_required"),
    ("POST", f"{API}/stickers/x/delete", {}, "media.confirm_required"),
    ("POST", f"{API}/expressions/999999/delete", {}, "media.confirm_required"),
    ("POST", f"{API}/tools/nope/test", {}, "tools.confirm_required"),
    ("POST", f"{API}/tools/cache/clear", {}, "tools.confirm_required"),
]

#: 带上 confirm 后闸门必须放行：无害目标要么成功、要么以别的错误码失败
ACCEPTED_WITH_CONFIRM: list[tuple[str, str, dict[str, Any], int, str]] = [
    ("POST", f"{API}/memories/999999/delete", {"confirm": "delete"}, 404, "memory.not_found"),
    ("POST", f"{API}/ai/router/reset", {"confirm": "reset"}, 200, ""),
    ("POST", f"{API}/config/reset", {"confirm": "reset"}, 200, ""),
    ("PUT", f"{API}/config/raw", {"yaml": "", "confirm": "raw"}, 200, ""),
    ("POST", f"{API}/stickers/x/delete", {"confirm": "delete"}, 404, "media.sticker_unknown"),
    (
        "POST",
        f"{API}/expressions/999999/delete",
        {"confirm": "delete"},
        404,
        "media.pattern_unknown",
    ),
    ("POST", f"{API}/tools/nope/test", {"confirm": "nope"}, 404, "tools.unknown"),
    ("POST", f"{API}/tools/cache/clear", {"confirm": "clear"}, 200, ""),
]

#: 每个端点拒绝时使用的闸门错误码（供"绝不再出现同一个 409"判断）
GATE_CODE = {code for _method, _path, _body, code in REFUSED_WITHOUT_CONFIRM}


def state_fingerprint(bot: Any) -> str:
    """配置快照 + 沙盒修订号：409 之后必须一模一样。"""
    sandbox = getattr(bot, "sandbox", None)
    revisions = ""
    if sandbox is not None:
        revisions = (
            f"{getattr(sandbox, 'world_revision', '')}:{getattr(sandbox, 'cognitive_revision', '')}"
        )
    payload = f"{bot.config.model_dump_json()}|{revisions}"
    return hashlib.sha256(payload.encode()).hexdigest()


class TestConfirmGates:
    async def test_every_dangerous_endpoint_refuses_without_confirm(self, tmp_path: Path) -> None:
        async with api_server(tmp_path) as (client, bot, _server):
            status, _ = await client.login()
            assert status == 200
            problems: list[str] = []
            for method, path, body, code in REFUSED_WITHOUT_CONFIRM:
                before = state_fingerprint(bot)
                status, payload = await client.request(method, path, body=body)
                if status != 409 or payload.get("ok") is not False or error_code(payload) != code:
                    problems.append(
                        f"{method} {path} -> {status} {error_code(payload)}，期望 409 {code}"
                    )
                    continue
                if state_fingerprint(bot) != before:
                    problems.append(f"{method} {path} 在 409 之后仍然改动了状态")
            assert not problems, "危险动作必须先要求 confirm：\n" + "\n".join(problems)

    async def test_confirm_field_reaches_the_handler_and_is_not_the_gate(
        self, tmp_path: Path
    ) -> None:
        async with api_server(tmp_path) as (client, _bot, _server):
            status, _ = await client.login()
            assert status == 200
            problems: list[str] = []
            for method, path, body, expected_status, expected_code in ACCEPTED_WITH_CONFIRM:
                status, payload = await client.request(method, path, body=body)
                code = error_code(payload)
                if status != expected_status or (expected_code and code != expected_code):
                    problems.append(
                        f"{method} {path} {body} -> {status} {code}"
                        f"（期望 {expected_status} {expected_code}）"
                    )
                    continue
                # 关键断言：带 confirm 后绝不能再撞回同一个闸门 409
                if status == 409 or code in GATE_CODE:
                    problems.append(f"{method} {path} 带了 confirm 仍被闸门拦下：{code}")
            assert not problems, "带 confirm 的调用必须放行或死于别的原因：\n" + "\n".join(problems)

    async def test_world_gate_refuses_before_touching_the_sandbox(self, tmp_path: Path) -> None:
        """world reset/reinitialize 的 409 必须在任何写入之前返回。

        （它们的确认路径会写真实 data/character_reset_backup 并清空角色数据，
        所以测试只证明"拒绝时什么都没发生"，不真的执行它们。）
        """
        async with api_server(tmp_path) as (client, bot, _server):
            status, _ = await client.login()
            assert status == 200
            sandbox = getattr(bot, "sandbox", None)
            assert sandbox is not None, "沙盒未启用时本用例不成立"
            before = (
                state_fingerprint(bot),
                sandbox.world_revision,
                sandbox.cognitive_revision,
                str(sandbox.phase),
            )
            for action in ("reset", "reinitialize"):
                status, payload = await client.post(
                    f"{API}/world/control/{action}", body={"confirm": "wrong"}
                )
                assert status == 409 and error_code(payload) == "world.confirm_required"
            after = (
                state_fingerprint(bot),
                sandbox.world_revision,
                sandbox.cognitive_revision,
                str(sandbox.phase),
            )
            assert after == before, "被拒绝的 world 控制不得改动沙盒 (revision/phase)"
