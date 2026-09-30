"""Shared pytest fixtures for CatooBot tests."""

from __future__ import annotations

from typing import Any

import pytest

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.message.event import GroupMessageEvent, PrivateMessageEvent


class FakeAdapter:
    """Records API calls, returns canned responses; no network involved."""

    def __init__(self, self_id: int = 10001) -> None:
        self._self_id = self_id
        self.calls: list[tuple[str, dict[str, Any] | None, float | None]] = []
        self.responses: dict[str, Any] = {}
        self.fail_on: set[str] = set()

    @property
    def connected(self) -> bool:
        return True

    @property
    def self_id(self) -> int | None:
        return self._self_id

    async def start(self) -> None:
        return None

    async def stop(self) -> None:
        return None

    async def call_api(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> Any:
        self.calls.append((action, params, timeout))
        if action in self.fail_on:
            raise RuntimeError(f"simulated failure for {action}")
        if action in self.responses:
            value = self.responses[action]
            if isinstance(value, Exception):
                raise value
            return value
        return {"message_id": 42}

    def sent_texts(self) -> list[str]:
        texts = []
        for _, params, _ in self.calls:
            message = (params or {}).get("message")
            if isinstance(message, list):
                joined = "".join(
                    seg.get("data", {}).get("text", "")
                    for seg in message
                    if isinstance(seg, dict) and seg.get("type") == "text"
                )
                texts.append(joined)
            elif isinstance(message, str):
                texts.append(message)
        return texts


def make_bot(tmp_path, adapter: FakeAdapter | None = None) -> Bot:
    """Test bot with reply delays off so tests never really wait.

    Behaviour tests that care about timing build their own components with an
    injected sleep/RNG; ``tests/test_timing.py`` covers the delay model.
    """
    config = AppConfig(
        bot={"name": "TestBot", "debug": False},
        database={"url": f"sqlite:///{tmp_path / 'test.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        behavior={"reply": {"enabled": False}},
    )
    adapter = adapter or FakeAdapter()
    bot = Bot(config, adapter)
    # v1.2: turn runtime replies are async — keep the debounce tiny in tests.
    bot.config.conversation.debounce.direct_message_ms = 20
    bot.config.conversation.debounce.group_message_ms = 30
    return bot


async def make_ready_bot(tmp_path, adapter: FakeAdapter | None = None) -> Bot:
    """A Bot with plugins loaded but the (fake) adapter not started.

    Mirrors Bot.start wiring: message recording only — QQ has no command
    surface since v0.3.
    """
    bot = make_bot(tmp_path, adapter)
    await bot.database.connect()
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    return bot


def group_event(
    text: str = "/ping",
    *,
    user_id: int = 123,
    group_id: int = 456,
    self_id: int = 10001,
    at_bot: bool = False,
    role: str | None = None,
) -> GroupMessageEvent:
    segments: list[dict[str, Any]] = []
    if at_bot:
        segments.append({"type": "at", "data": {"qq": str(self_id)}})
    segments.append({"type": "text", "data": {"text": text}})
    raw = {
        "post_type": "message",
        "self_id": self_id,
        "time": 1700000000,
        "message_type": "group",
        "sub_type": "normal",
        "message_id": 9,
        "user_id": user_id,
        "group_id": group_id,
        "message": segments,
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "Alice", "role": role},
    }
    return GroupMessageEvent.model_validate(raw)


def private_event(
    text: str = "/ping",
    *,
    user_id: int = 123,
    self_id: int = 10001,
) -> PrivateMessageEvent:
    raw = {
        "post_type": "message",
        "self_id": self_id,
        "time": 1700000000,
        "message_type": "private",
        "sub_type": "friend",
        "message_id": 10,
        "user_id": user_id,
        "message": [{"type": "text", "data": {"text": text}}],
        "raw_message": text,
        "sender": {"user_id": user_id, "nickname": "Alice"},
    }
    return PrivateMessageEvent.model_validate(raw)


@pytest.fixture
def fake_adapter() -> FakeAdapter:
    return FakeAdapter()


@pytest.fixture
async def ready_bot(tmp_path, fake_adapter) -> Bot:
    bot = await make_ready_bot(tmp_path, fake_adapter)
    yield bot
    await bot.shutdown()


@pytest.fixture
def group_message_event() -> GroupMessageEvent:
    return group_event()


@pytest.fixture
def private_message_event() -> PrivateMessageEvent:
    return private_event()
