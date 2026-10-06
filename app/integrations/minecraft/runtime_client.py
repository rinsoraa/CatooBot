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

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime.error",
        status: int = 0,
        detail: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        #: 稳定错误码之外的结构化细节（如 block.changed 的 expected/actual）
        self.detail = detail or {}

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

    async def world_snapshot(self, layers: str = "near,local,extended") -> dict[str, Any]:
        """Raw World Snapshot（Phase 2）；layers=near,local,extended 子集可选。"""
        return await self._request("GET", f"/minecraft/world/snapshot?layers={layers}")

    async def chat(self, message: str) -> dict[str, Any]:
        return await self._request("POST", "/minecraft/chat", body={"message": message})

    async def look_at(self, x: float, y: float, z: float) -> dict[str, Any]:
        """让 bot 看向世界坐标（Phase 3B：SAFE 动作，yaw/pitch 数学在 runtime 里）。"""
        return await self._request("POST", "/minecraft/look_at", body={"x": x, "y": y, "z": z})

    async def move_to(self, x: float, y: float, z: float) -> dict[str, Any]:
        """非破坏性导航到世界坐标（Phase 3C：LOW；GoalNear 半径/禁挖禁放在 runtime 侧）。"""
        return await self._request("POST", "/minecraft/move_to", body={"x": x, "y": y, "z": z})

    async def follow_player(self, username: str, distance: float) -> dict[str, Any]:
        """动态跟随玩家（Phase 3D：GoalFollow + dynamic；启动即返回 RUNNING）。"""
        return await self._request(
            "POST", "/minecraft/follow_player", body={"username": username, "distance": distance}
        )

    async def inventory(self) -> dict[str, Any]:
        """只读背包切片（Phase 4C：按物品名聚合，无 slot/NBT/window）。"""
        return await self._request("GET", "/minecraft/inventory")

    async def inventory_slots(self) -> dict[str, Any]:
        """调试用的**原始槽位**视图（WebUI Move Test / smoke；LLM 工具不读它）。"""
        return await self._request("GET", "/minecraft/inventory/slots")

    async def equip(self, item: str) -> dict[str, Any]:
        """把指定物品拿到主手（Phase 4D：启动即 RUNNING）。"""
        return await self._request("POST", "/minecraft/equip", body={"item": item})

    async def inventory_move(
        self, source_slot: int, destination_slot: int, item: str, count: int
    ) -> dict[str, Any]:
        """把一个明确槽位上的物品移动指定数量到另一个明确槽位（Phase 4D）。"""
        return await self._request(
            "POST",
            "/minecraft/inventory_move",
            body={
                "source_slot": source_slot,
                "destination_slot": destination_slot,
                "item": item,
                "count": count,
            },
        )

    async def dropped_items(self) -> dict[str, Any]:
        """读附近的掉落物实体（Phase 4H：同步只读语义投影）。"""
        return await self._request("POST", "/minecraft/dropped_items", body={})

    async def pickup_item(self, entity_id: int, expected_item: str) -> dict[str, Any]:
        """捡起一个明确的掉落物实体（Phase 4H：启动即 RUNNING）。"""
        return await self._request(
            "POST",
            "/minecraft/pickup_item",
            body={"entity_id": entity_id, "expected_item": expected_item},
        )

    async def recipe_lookup(
        self, item: str, crafting_table: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """查配方（Phase 4F/4G：同步只读；带坐标 = 那张工作台的 3×3）。

        返回语义投影（recipe_id / result / requires_table / available / ingredients），
        绝不返回 raw Recipe。
        """
        body: dict[str, Any] = {"item": item}
        if crafting_table is not None:
            body["crafting_table"] = crafting_table
        return await self._request("POST", "/minecraft/recipe_lookup", body=body)

    async def craft(
        self, recipe_id: str, crafting_table: dict[str, int] | None = None
    ) -> dict[str, Any]:
        """执行一次配方（Phase 4F/4G：启动即 RUNNING，结果经事件送达）。"""
        body: dict[str, Any] = {"recipe_id": recipe_id}
        if crafting_table is not None:
            body["crafting_table"] = crafting_table
        return await self._request("POST", "/minecraft/craft", body=body)

    async def container_inspect(self, x: int, y: int, z: int) -> dict[str, Any]:
        """读一个 Chest / Barrel 的内容（Phase 4E：同步动作，结果直接返回）。

        runtime 内部固定 open → read → close；绝不把"开着的窗口"暴露给上层。
        """
        return await self._request(
            "POST", "/minecraft/container_inspect", body={"x": x, "y": y, "z": z}
        )

    async def container_transfer(
        self,
        x: int,
        y: int,
        z: int,
        direction: str,
        container_slot: int,
        inventory_slot: int,
        item: str,
        count: int,
    ) -> dict[str, Any]:
        """单物品在「容器槽 ↔ 背包槽」之间移动一次（Phase 4E：启动即 RUNNING）。"""
        return await self._request(
            "POST",
            "/minecraft/container_transfer",
            body={
                "x": x,
                "y": y,
                "z": z,
                "direction": direction,
                "container_slot": container_slot,
                "inventory_slot": inventory_slot,
                "item": item,
                "count": count,
            },
        )

    async def place(self, x: int, y: int, z: int, face: str, expected_item: str) -> dict[str, Any]:
        """放置单个方块（Phase 4C：启动即返回 RUNNING，终态经事件送达）。"""
        return await self._request(
            "POST",
            "/minecraft/place",
            body={"x": x, "y": y, "z": z, "face": face, "expected_item": expected_item},
        )

    async def dig(self, x: float, y: float, z: float, expected_block: str) -> dict[str, Any]:
        """破坏一个指定方块（Phase 4B：启动即返回 RUNNING，终态经事件送达）。"""
        return await self._request(
            "POST",
            "/minecraft/dig",
            body={"x": x, "y": y, "z": z, "expected_block": expected_block},
        )

    async def stop(self) -> dict[str, Any]:
        """最高优先级安全停止（Phase 3B）：取消进行中动作，幂等。"""
        return await self._request("POST", "/minecraft/stop", body={})

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
                    detail: dict[str, Any] = {}
                    if isinstance(payload, dict):
                        error = payload.get("error")
                        if isinstance(error, dict):
                            message = str(error.get("message") or message)
                            code = str(error.get("code") or code)
                            raw_detail = error.get("detail")
                            if isinstance(raw_detail, dict):
                                detail = raw_detail
                    raise MinecraftRuntimeError(
                        message, code=code, status=response.status, detail=detail
                    )
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
