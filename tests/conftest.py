"""Shared pytest fixtures for CatooBot tests."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.message.event import GroupMessageEvent, PrivateMessageEvent

#: Repository root — tests must never depend on the current working directory.
REPO_ROOT = Path(__file__).resolve().parents[1]

#: The character bible used by tests: a self-contained fixture under
#: tests/fixtures (the operator's real config/character_bible.md is private
#: and never required by the suite).
BIBLE_PATH = REPO_ROOT / "tests" / "fixtures" / "character_bible.md"


# ---------------------------------------------------------------------------
# Live-data guard: the suite must never open the operator's real database.
# Tests once reached production data through default paths (junk files in the
# live sticker library); opening data/*.db from a test now fails loudly instead
# of silently reading or writing the live world.
# ---------------------------------------------------------------------------
_LIVE_DATA_DIR = (REPO_ROOT / "data").resolve()
_STDLIB_SQLITE_CONNECT = sqlite3.connect


def _live_db_target(database: Any) -> Path | None:
    """Best-effort live-path detection for a sqlite3.connect() argument."""
    try:
        raw = str(database)
    except Exception:  # noqa: BLE001 - the guard itself must never explode
        return None
    if raw.startswith("file:"):
        raw = raw[5:].split("?", 1)[0]
    candidates: set[Path] = set()
    for base in (Path(), REPO_ROOT):
        try:
            candidates.add((base / raw).resolve())
        except (OSError, ValueError):
            continue
    for candidate in candidates:
        if candidate == _LIVE_DATA_DIR or _LIVE_DATA_DIR in candidate.parents:
            return candidate
    return None


def is_live_db_path(database: Any) -> bool:
    """True when *database* points into the project's own data/ directory."""
    return _live_db_target(database) is not None


def _guarded_sqlite_connect(database: Any, *args: Any, **kwargs: Any) -> Any:
    target = _live_db_target(database)
    if target is not None:
        raise RuntimeError(
            f"refusing to open the live database in tests: {database!r} -> {target}; "
            "point DatabaseConfig at tmp_path instead"
        )
    return _STDLIB_SQLITE_CONNECT(database, *args, **kwargs)


# Installed at import time (before test modules are collected) so no test can
# slip past it.
sqlite3.connect = _guarded_sqlite_connect  # type: ignore[assignment]


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


def make_bot(tmp_path, adapter: FakeAdapter | None = None, *, sandbox: dict | None = None) -> Bot:
    """Test bot with reply delays off so tests never really wait.

    Behaviour tests that care about timing build their own components with an
    injected sleep/RNG; ``tests/test_timing.py`` covers the delay model.
    ``sandbox`` overrides the sandbox section (e.g. core-friend identities)
    without turning the persistent world on.
    """
    config = AppConfig(
        bot={"name": "TestBot", "debug": False},
        database={"url": f"sqlite:///{tmp_path / 'test.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        behavior={"reply": {"enabled": False}},
        # Media acquisition writes real files — a test must never touch the
        # live data/stickers (that is how junk PNGs once reached production).
        media={
            "sticker_dir": str(tmp_path / "stickers"),
            "media_dir": str(tmp_path / "media"),
        },
        # v2.0: the sandbox has its own dedicated tests; generic tests run the
        # legacy wiring unchanged.
        sandbox=sandbox if sandbox is not None else {"enabled": False},
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


@pytest.fixture(scope="session", autouse=True)
def _real_env_untouched() -> Iterator[None]:
    """整套测试不得改动操作者的真实 ``.env``（W6 隔离护栏）。

    历史事故：一个测试把 ``CATOOBOT_ONEBOT_ACCESS_TOKEN`` 写进真实 .env，
    使 OneBot WS 服务器开始强制校验 Token，NapCat 全部连接 401。
    """
    from app.config.settings import PROJECT_ROOT

    env_file = PROJECT_ROOT / ".env"
    before = env_file.read_bytes() if env_file.exists() else None
    yield
    after = env_file.read_bytes() if env_file.exists() else None
    assert before == after, (
        "测试改动了真实 .env：请让测试显式传入临时路径"
        "（monkeypatch app.config.env_store.env_path 或 "
        "app.web.services.config_admin.env_path）"
    )
