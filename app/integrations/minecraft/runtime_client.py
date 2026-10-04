"""Minecraft Runtime 的 HTTP 客户端（任务书 §CatooBot → Minecraft Bridge API）。

只认 runtime.js 暴露的四个 Bridge 端点 + 健康探针；连接失败/非 JSON 响应
统一抛 :class:`MinecraftRuntimeError`，把网络细节挡在这一层之外。
"""

from __future__ import annotations

import json
from typing import Any

import aiohttp

from app.utils.logger import get_logger

log = get_logger("CatooBot.Minecraft.Client")


class MinecraftRuntimeError(Exception):
    """runtime 调用失败。``code`` 是 runtime 的业务错误码（如 ``session.active``）。"""

    def __init__(self, message: str, *, code: str = "runtime.error", status: int = 0) -> None:
        super().__init__(message)
        self.code = code
        self.status = status

    @property
    def unreachable(self) -> bool:
        """进程没起来 / 端口没人听 / 响应不是 JSON —— 需要重启进程级别的故障。"""
        return self.code == "runtime.unreachable"


class MinecraftRuntimeClient:
    """一个 runtime 进程对应一个客户端实例；会话懒创建、可关闭。"""

    def __init__(self, base_url: str, timeout: float = 15.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None

    async def close(self) -> None:
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    # ------------------------------------------------------------------ calls

    async def health(self) -> dict[str, Any]:
        return await self._request("GET", "/minecraft/health")

    async def connect(self, host: str, port: int) -> dict[str, Any]:
        return await self._request("POST", "/minecraft/connect", body={"host": host, "port": port})

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", "/minecraft/status")

    async def chat(self, message: str) -> dict[str, Any]:
        return await self._request("POST", "/minecraft/chat", body={"message": message})

    async def disconnect(self) -> dict[str, Any]:
        return await self._request("POST", "/minecraft/disconnect", body={})

    # ----------------------------------------------------------------- internals

    def _session_or_create(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        try:
            async with self._session_or_create().request(
                method, f"{self._base}{path}", json=body
            ) as response:
                text = await response.text()
                try:
                    payload: Any = json.loads(text)
                except ValueError as exc:
                    raise MinecraftRuntimeError(
                        f"runtime 返回了非 JSON 响应（HTTP {response.status}）",
                        code="runtime.bad_response",
                        status=response.status,
                    ) from exc
                if response.status >= 400:
                    message = "runtime 拒绝了请求"
                    code = "runtime.error"
                    if isinstance(payload, dict):
                        error = payload.get("error")
                        if isinstance(error, dict):
                            message = str(error.get("message") or message)
                            code = str(error.get("code") or code)
                    raise MinecraftRuntimeError(message, code=code, status=response.status)
                return payload
        except aiohttp.ClientError as exc:
            raise MinecraftRuntimeError(
                f"无法访问 Minecraft runtime（{self._base}）：{exc}",
                code="runtime.unreachable",
            ) from exc
        except TimeoutError as exc:
            raise MinecraftRuntimeError(
                f"Minecraft runtime 响应超时（{self._base}{path}）",
                code="runtime.unreachable",
            ) from exc
