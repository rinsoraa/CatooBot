"""Phase 8E Follow-up §6.5：异常日志隐私收口（不输出异常正文 / provider 内容 / 输入值）。

敏感标记只出现在**故障替身**里；测试断言它们不出现在日志文本中。
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

from app.tasks.wood_intent import WoodIntent, WoodIntentInterpreter
from tests.test_minecraft_chat_bridge import stack
from tests.test_phase8e_followup_routing_closure import PlansStub, real_entry, wire

SENSITIVE_PROVIDER = "SENSITIVE_SENTINEL_PROVIDER_BODY_8EFU"
SENSITIVE_CONTEXT = "SENSITIVE_SENTINEL_CONTEXT_8EFU"
SENSITIVE_ROUTE = "SENSITIVE_SENTINEL_ROUTE_8EFU"
SENSITIVE_SCHEMA = "SENSITIVE_SENTINEL_SCHEMA_8EFU"
WOOD_ASK = "帮我砍两根橡木原木带回来"
USER = "空凛"


class ExplodingEngine:
    """provider 抛出的异常正文带敏感标记。"""

    def __init__(self, message: str = SENSITIVE_PROVIDER) -> None:
        self._message = message

    async def chat(self, request: Any) -> Any:
        raise RuntimeError(self._message)


class EchoEngine:
    """直接回一段指定内容（用于 schema 故障注入）。"""

    def __init__(self, content: str) -> None:
        self._content = content

    async def chat(self, request: Any) -> Any:
        return SimpleNamespace(content=self._content)


class EvilContext:
    """上下文对象：转成字符串就抛带敏感标记的异常。"""

    def __str__(self) -> str:
        raise RuntimeError(SENSITIVE_CONTEXT)


def test_provider_error_logs_type_name_only(caplog: Any) -> None:
    """provider 异常 → 只记 ``error_type=RuntimeError``，不记异常正文。"""
    interpreter = WoodIntentInterpreter(ExplodingEngine())
    with caplog.at_level(logging.WARNING, logger="CatooBot.Tasks.WoodIntent"):
        result = asyncio.run(interpreter.interpret(message=WOOD_ASK))
    assert result.intent is WoodIntent.OTHER
    assert result.needs_clarification is True
    assert SENSITIVE_PROVIDER not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_context_error_logs_type_name_only(caplog: Any) -> None:
    """上下文构造故障 → 只记类型名，不记异常正文。"""
    interpreter = WoodIntentInterpreter(EchoEngine("{}"))
    with caplog.at_level(logging.WARNING, logger="CatooBot.Tasks.WoodIntent"):
        result = asyncio.run(
            interpreter.interpret(message=WOOD_ASK, recent_context=[EvilContext()])
        )
    assert result.needs_clarification is True
    assert SENSITIVE_CONTEXT not in caplog.text
    assert "error_type=RuntimeError" in caplog.text


def test_schema_error_logs_field_paths_only(caplog: Any) -> None:
    """schema 失败 → 只记字段路径/错误类型摘要，不记输入值。"""
    payload = (
        '{"intent": "wood_collection_request", "polarity": "positive", "reason_code": "'
        + SENSITIVE_SCHEMA
        + '"}'
    )
    interpreter = WoodIntentInterpreter(EchoEngine(payload))
    with caplog.at_level(logging.WARNING, logger="CatooBot.Tasks.WoodIntent"):
        result = asyncio.run(interpreter.interpret(message=WOOD_ASK))
    assert result.needs_clarification is True
    assert SENSITIVE_SCHEMA not in caplog.text
    assert "schema rejected" in caplog.text


async def test_route_error_logs_type_name_only(tmp_path: Any, caplog: Any) -> None:
    """入口层解释器故障 → 只记类型名，且玩家看到的是安全澄清。"""

    class BoomInterpreter:
        async def interpret(self, *, message: str, **_: Any) -> Any:
            raise RuntimeError(SENSITIVE_ROUTE)

    async with stack(tmp_path, "好的～") as chat:
        handler, runtime, counter = real_entry()
        plans = PlansStub()
        wire(chat, handler, plans, BoomInterpreter())
        with caplog.at_level(logging.WARNING, logger="CatooBot.Minecraft.Chat"):
            await chat.say(USER, WOOD_ASK)
    assert SENSITIVE_ROUTE not in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert await runtime.active("minecraft:127.0.0.1:25565:" + USER) is None
