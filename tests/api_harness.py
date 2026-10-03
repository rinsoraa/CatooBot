"""Shared harness for the `/api/v1` tests (WebUI v1.0 · W2).

A real HTTP server on an ephemeral port with a real cookie jar: the tests
exercise the middleware (auth + CSRF) instead of bypassing it, which is the
only way to prove the contract's status codes.
"""

from __future__ import annotations

import json
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiohttp

from app.config.settings import AppConfig
from app.core.bot import Bot
from app.web.server import WebServer
from tests.test_web_realtime import DummyAdapter


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


class ApiClient:
    """JSON client that keeps the session cookie and the CSRF token."""

    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")
        self.csrf = ""
        # aiohttp refuses cookies for IP hosts unless the jar is unsafe
        self._session = aiohttp.ClientSession(cookie_jar=aiohttp.CookieJar(unsafe=True))

    async def close(self) -> None:
        await self._session.close()

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: Any = None,
        csrf: bool = True,
        headers: dict[str, str] | None = None,
        raw: bool = False,
    ) -> tuple[int, Any]:
        sent = dict(headers or {})
        if csrf and method in ("POST", "PUT", "PATCH", "DELETE") and self.csrf:
            sent.setdefault("X-CSRF-Token", self.csrf)
        async with self._session.request(
            method, self.base + path, json=body, headers=sent
        ) as response:
            text = await response.text()
            if raw:
                return response.status, text
            try:
                return response.status, json.loads(text)
            except ValueError:
                return response.status, {"_text": text[:400]}

    async def get(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return await self.request("GET", path, **kwargs)

    async def post(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return await self.request("POST", path, **kwargs)

    async def patch(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return await self.request("PATCH", path, **kwargs)

    async def put(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return await self.request("PUT", path, **kwargs)

    async def delete(self, path: str, **kwargs: Any) -> tuple[int, Any]:
        return await self.request("DELETE", path, **kwargs)

    async def login(self, username: str = "admin", password: str = "pw123") -> tuple[int, Any]:
        status, payload = await self.request(
            "POST",
            "/api/v1/session",
            body={"username": username, "password": password},
            csrf=False,
        )
        if status == 200:
            self.csrf = str(payload.get("data", {}).get("csrf_token", ""))
        return status, payload


@asynccontextmanager
async def api_server(
    tmp_path: Path, *, config_overrides: dict[str, Any] | None = None
) -> AsyncIterator[tuple[ApiClient, Bot, WebServer]]:
    """Start a real WebServer; the overrides file is isolated per test."""
    port = free_port()
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": "sqlite:///" + str(tmp_path / "api.db")},
        logging={"log_dir": str(tmp_path / "logs"), "level": "WARNING"},
        web={
            "enabled": True,
            "host": "127.0.0.1",
            "port": port,
            "username": "admin",
            "password": "pw123",
        },
        **(config_overrides or {}),
    )
    bot = Bot(config, DummyAdapter())
    await bot.database.connect()
    await bot.character.start()
    server = WebServer(config.web, bot)
    server._config_admin.overrides_path = tmp_path / "overrides.yaml"
    await server.start()
    client = ApiClient(f"http://127.0.0.1:{port}")
    try:
        yield client, bot, server
    finally:
        await client.close()
        await server.stop()
        await bot.shutdown()


def error_code(payload: Any) -> str:
    return str(payload.get("error", {}).get("code", "")) if isinstance(payload, dict) else ""
