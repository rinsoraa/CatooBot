"""Minecraft Runtime 的 HTTP 客户端（任务书 §CatooBot → Minecraft Bridge API）。

只认 runtime.js 暴露的四个 Bridge 端点 + 健康探针；连接失败/非 JSON 响应
统一抛 :class:`MinecraftRuntimeError`，把网络细节挡在这一层之外。
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import aiohttp

from app.utils.logger import get_logger

log = get_logger("CatooBot.Minecraft.Client")


#: 控制者释放的结果分类（P1-F1 / A3-FU2）。
#:
#: 四态**必须**可区分：传输超时 / 业务拒绝 / 成功释放 / 未知结果。超时与未知
#: 都属于"没有拿到释放回执"，下游绝不能把它们当成已释放。
RELEASE_RELEASED = "RELEASED"
RELEASE_REJECTED = "REJECTED"
RELEASE_TIMEOUT = "TIMEOUT"
RELEASE_UNKNOWN = "UNKNOWN"
#: 连接根本没建立（端口没人听 / DNS 失败）：可以确定"这一次没有释放"。
RELEASE_NOT_DELIVERED = "NOT_DELIVERED"
#: runtime 回了 2xx 但 released=false：它那边本来就没有可释放的所有权。
RELEASE_NOT_RELEASED = "NOT_RELEASED"
#: 本服务从未持有所有权，没有发出释放请求。
RELEASE_SKIPPED = "SKIPPED"
#: runtime 侧表示所有权已终结的状态（旧 runtime 没有 released 字段时用于**推断**回执）。
_RELEASED_TERMINAL_STATES = frozenset({"REVOKED", "UNCLAIMED", "EXPIRED"})


def classify_release_failure(exc: BaseException) -> str:
    """把释放失败的异常归到四态之一（其余细分态只表示"没有回执"）。

    判定顺序是有意为之：

    1. 传输超时但请求**可能已送达** → TIMEOUT（等响应等到预算用完）；
    2. 请求确定没送达（连接被拒 / 连接阶段超时）→ NOT_DELIVERED；
    3. runtime 明确 4xx 业务拒绝 → REJECTED；
    4. 其它（响应丢失 / 5xx / 非 JSON / 未知异常）→ UNKNOWN。
    """
    if isinstance(exc, TimeoutError):
        return RELEASE_TIMEOUT
    if isinstance(exc, MinecraftRuntimeError):
        if exc.timed_out and exc.delivered:
            return RELEASE_TIMEOUT
        if not exc.delivered:
            return RELEASE_NOT_DELIVERED
        if 400 <= int(exc.status or 0) < 500:
            return RELEASE_REJECTED
    return RELEASE_UNKNOWN


class MinecraftRuntimeError(Exception):
    """runtime 调用失败。``code`` 是 runtime 的业务错误码（如 ``session.active``）。"""

    def __init__(
        self,
        message: str,
        *,
        code: str = "runtime.error",
        status: int = 0,
        detail: dict[str, Any] | None = None,
        timed_out: bool = False,
        delivered: bool = True,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        #: 稳定错误码之外的结构化细节（如 block.changed 的 expected/actual）
        self.detail = detail or {}
        #: 传输层超时（等响应等到预算用完）。同为 runtime.unreachable 时，
        #: 超时与断连必须是两种可区分的事实，否则关停侧无法判断释放是否可能生效。
        self.timed_out = timed_out
        #: 请求是否**可能**已送达对端；只有"连接都没建立"才是确定 False。
        self.delivered = delivered

    @property
    def unreachable(self) -> bool:
        """进程没起来 / 端口没人听 / 响应不是 JSON —— 需要重启进程级别的故障。"""
        return self.code == "runtime.unreachable"


class MinecraftRuntimeClient:
    """一个 runtime 进程对应一个客户端实例；会话懒创建、可关闭。"""

    def __init__(
        self,
        base_url: str,
        timeout: float = 15.0,
        token_getter: Callable[[], str] | None = None,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = aiohttp.ClientTimeout(total=timeout)
        self._session: aiohttp.ClientSession | None = None
        #: Phase 7F.5：**正向**调用凭据（惰性读取：token 可能在客户端创建之后才生成）。
        #: 凭据本身绝不进日志、错误消息或事件。
        self._token_getter = token_getter
        #: Node 控制者所有权凭据（仅世界写请求携带；绝不进日志）。
        self.controller_id = ""
        self.owner_epoch = 0
        self.owner_token = ""
        self.owner_expires_at = 0.0
        self.owner_state = "UNKNOWN"
        #: 最近一次 release 是否拿到 runtime 的**明确**回执（released=true / 状态已非 ACTIVE）。
        #: 超时、响应丢失、业务拒绝都保持 False —— 绝不由本地推测成"已释放"。
        self.release_confirmed = False
        #: 回执可信度：explicit / inferred（旧 runtime 由终态推断）/ none（未取得可信回执）。
        self.release_ack = "none"
        #: 回执判定原因（ack.explicit / ack.inferred_legacy / ack.contradictory_state …）。
        self.release_ack_reason = ""
        #: close() 之后绝不再新建会话：关停后的 STOP/status 不得把 I/O 复活。
        self._closed = False

    async def close(self) -> None:
        self._closed = True
        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

    async def set_request_timeout(self, seconds: float) -> float:
        """Change the per-request budget WITHOUT permanently closing the client.

        close() is the terminal shutdown signal: after it, I/O must not revive (a
        shutdown-time STOP/status must not resurrect the session). A temporary budget
        change is a different thing, so it must not go through close(): the caller gets
        a fresh session lazily and the closed guard stays intact. Returns the previous
        total budget in seconds.
        """
        previous = float(getattr(self._timeout, "total", 0.0) or 0.0)
        self._timeout = aiohttp.ClientTimeout(total=float(seconds))
        if self._session is not None and not self._session.closed:
            await self._session.close()
            self._session = None
        return previous

    # ------------------------------------------------------------------ calls

    async def claim_controller(
        self, controller_id: str, *, ttl: float | None = None
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"controller_id": controller_id}
        if ttl is not None:
            body["ttl"] = ttl
        payload = await self._request("POST", "/minecraft/controller/claim", body=body)
        self.controller_id = str(payload.get("controller_id") or controller_id)
        self.owner_epoch = int(payload.get("owner_epoch") or 0)
        self.owner_token = str(payload.get("owner_token") or "")
        self.owner_expires_at = float(payload.get("owner_expires_at") or 0.0)
        self.owner_state = str(payload.get("state") or "UNKNOWN")
        return payload

    async def renew_controller(self) -> dict[str, Any]:
        payload = await self._request(
            "POST",
            "/minecraft/controller/renew",
            body={
                "controller_id": self.controller_id,
                "owner_epoch": self.owner_epoch,
                "owner_token": self.owner_token,
            },
        )
        self.owner_expires_at = float(payload.get("owner_expires_at") or 0.0)
        self.owner_state = str(payload.get("state") or "UNKNOWN")
        return payload

    def _parse_release_ack(self, payload: Any) -> tuple[bool | None, str, str]:
        """把释放响应解析成 (released, state, reason)。

        released 只有三种取值，**绝不**用真值判断（"false" / 1 都不能当成已释放）：

        * True  —— runtime 明确回执 released=true；旧 runtime 由终态推断时 reason 为
          "ack.inferred_legacy"，调用方据此知道这是推断而非明确 ACK；
        * False —— runtime 明确说这次没有释放（released=false，或 2xx 但状态仍 ACTIVE）；
        * None  —— 回执不可判定（缺字段 / 自相矛盾 / 归属不符 / 畸形值）→ 必须按 UNKNOWN。
        """
        if not isinstance(payload, dict):
            return None, "", "ack.not_a_dict"
        if payload.get("ok") is False:
            # 200 + {ok:false}（_request 只在 >=400 抛错）不得被当成成功 payload。
            return None, str(payload.get("state") or ""), "ack.not_ok"
        state = str(payload.get("state") or "")
        echo_controller = str(payload.get("controller_id") or "")
        if echo_controller and self.controller_id and echo_controller != self.controller_id:
            return None, state, "ack.foreign_controller"
        echo_epoch = payload.get("owner_epoch")
        if echo_epoch is not None and self.owner_epoch:
            try:
                if int(echo_epoch) != int(self.owner_epoch):
                    return None, state, "ack.foreign_epoch"
            except (TypeError, ValueError):
                return None, state, "ack.bad_epoch"
        released = payload.get("released")
        if released is not None and not isinstance(released, bool):
            # 畸形值（"false" / 1 / "yes"）一律不可判定，不能靠真值蒙对。
            return None, state, "ack.non_bool_released"
        if released is True:
            if state and state not in _RELEASED_TERMINAL_STATES:
                # released=true 却说状态仍 ACTIVE：矛盾回执，保留不确定性。
                return None, state, "ack.contradictory_state"
            return True, state, "ack.explicit"
        if released is False:
            return False, state, "ack.refused"
        if state in _RELEASED_TERMINAL_STATES:
            return True, state, "ack.inferred_legacy"
        return None, state, "ack.state_unknown"

    async def release_controller(self) -> dict[str, Any]:
        # 每次释放都重新判定：粘性字段会让上一生命周期的 True 污染本次报告。
        self.release_confirmed = False
        self.release_ack = "none"
        self.release_ack_reason = ""
        payload = await self._request(
            "POST",
            "/minecraft/controller/release",
            body={
                "controller_id": self.controller_id,
                "owner_epoch": self.owner_epoch,
                "owner_token": self.owner_token,
            },
        )
        released, state, reason = self._parse_release_ack(payload)
        self.release_ack_reason = reason
        if released is True:
            self.release_confirmed = True
            self.release_ack = "inferred" if reason == "ack.inferred_legacy" else "explicit"
            self.owner_state = state or "REVOKED"
            # 只有拿到可信回执才清 token；否则 token 是"可能仍然持有"的证据。
            self.owner_token = ""
        elif released is False:
            self.release_ack = "explicit"
            self.owner_state = state or self.owner_state
        else:
            # 不可判定：保留不确定性，token 与 owner_state 都不改写成功。
            self.owner_state = "UNKNOWN"
        return payload

    def controller_ready(self, *, now: float | None = None) -> bool:
        import time as _time

        moment = _time.time() if now is None else float(now)
        return bool(
            self.owner_token and self.owner_state == "ACTIVE" and self.owner_expires_at > moment
        )

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

    async def drop_item(self, item: str, count: int = 1) -> dict[str, Any]:
        """R6: one explicit main-hand drop (MEDIUM; no auto-equip)."""
        return await self._request(
            "POST", "/minecraft/drop_item", body={"item": item, "count": count}
        )

    async def entities(self) -> dict[str, Any]:
        """R5: read nearby living entities (SAFE; synchronous projection)."""
        return await self._request("POST", "/minecraft/entities", body={})

    async def attack(
        self,
        entity_id: int,
        expected_type: str,
        *,
        expected_uuid: str | None = None,
        max_distance: float | None = None,
    ) -> dict[str, Any]:
        """R5: one hit on one frozen entity (MEDIUM; no navigation)."""
        body: dict[str, Any] = {
            "entity_id": entity_id,
            "expected_type": expected_type,
        }
        if expected_uuid:
            body["expected_uuid"] = expected_uuid
        if max_distance is not None:
            body["max_distance"] = max_distance
        return await self._request("POST", "/minecraft/attack", body=body)

    async def pickup_item(
        self,
        entity_id: int,
        expected_item: str,
        *,
        entity_uuid: str | None = None,
        expected_break_action: str | None = None,
    ) -> dict[str, Any]:
        """捡起一个明确的掉落物实体（Phase 4H：启动即 RUNNING）。"""
        body = {"entity_id": entity_id, "expected_item": expected_item}
        if entity_uuid is not None:
            body["entity_uuid"] = entity_uuid
        if expected_break_action is not None:
            body["expected_break_action"] = expected_break_action
        return await self._request("POST", "/minecraft/pickup_item", body=body)

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

    async def dig(
        self,
        x: float,
        y: float,
        z: float,
        expected_block: str,
        expected_tool: str | None = None,
        *,
        require_collection: bool = False,
        expected_empty_hand: bool = False,
    ) -> dict[str, Any]:
        """破坏一个指定方块（Phase 4B；Phase 4I 起可带 expected_tool）。

        启动即返回 RUNNING，终态经事件送达。``expected_tool`` 给了就要求**执行瞬间**主手
        拿着它（runtime 在 start 里实时校验，绝不自动换工具）；缺省 = Phase 4B 行为。
        """
        body: dict[str, Any] = {
            "x": x,
            "y": y,
            "z": z,
            "expected_block": expected_block,
        }
        if expected_tool is not None:
            body["expected_tool"] = expected_tool
        if require_collection:
            body["require_collection"] = True
        if expected_empty_hand:
            body["expected_empty_hand"] = True
        return await self._request("POST", "/minecraft/dig", body=body)

    async def find_blocks(
        self, block_names: list[str], max_distance: int, max_results: int
    ) -> dict[str, Any]:
        """找附近的指定方块（Phase 4K：只读；同步返回语义投影）。"""
        return await self._request(
            "POST",
            "/minecraft/find_blocks",
            body={
                "block_names": list(block_names),
                "max_distance": max_distance,
                "max_results": max_results,
            },
        )

    async def dig_capability(self, x: int, y: int, z: int) -> dict[str, Any]:
        """只读查「这个方块现在能不能挖、大概多久」（Phase 4J；同步返回语义投影）。"""
        return await self._request(
            "POST", "/minecraft/dig_capability", body={"x": x, "y": y, "z": z}
        )

    async def stop(
        self, action_id: str | None = None, stop_request_id: str | None = None
    ) -> dict[str, Any]:
        """安全停止（Phase 3B）；传 action_id 时只取消该动作。"""
        body: dict[str, Any] = {}
        if action_id:
            body["action_id"] = action_id
        if stop_request_id:
            body["stop_request_id"] = stop_request_id
        return await self._request("POST", "/minecraft/stop", body=body)

    async def disconnect(self) -> dict[str, Any]:
        return await self._request("POST", "/minecraft/disconnect", body={})

    # ----------------------------------------------------------------- internals

    def _session_or_create(self) -> aiohttp.ClientSession:
        if self._closed:
            # 关停后不得复活 I/O：deterministic "did not deliver"，不是超时也不是成功。
            raise MinecraftRuntimeError(
                "Minecraft runtime 客户端已关闭", code="runtime.closed", delivered=False
            )
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(timeout=self._timeout)
        return self._session

    async def _request(self, method: str, path: str, *, body: dict[str, Any] | None = None) -> Any:
        headers: dict[str, str] = {}
        token = ""
        if self._token_getter is not None:
            try:
                token = str(self._token_getter() or "")
            except Exception:  # noqa: BLE001 - 拿不到凭据也不能变成「匿名调用」
                token = ""
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if self.owner_token:
            headers["X-Catoo-Controller-Token"] = self.owner_token
        try:
            async with self._session_or_create().request(
                method, f"{self._base}{path}", json=body, headers=headers
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
        except aiohttp.ClientConnectorError as exc:
            # 连接根本没建立（端口没人听 / DNS 失败）：请求确定没有送达。
            raise MinecraftRuntimeError(
                f"无法访问 Minecraft runtime（{self._base}）：{exc}",
                code="runtime.unreachable",
                timed_out=isinstance(exc, TimeoutError),
                delivered=False,
            ) from exc
        except aiohttp.ConnectionTimeoutError as exc:
            # 连接阶段就超时：同样确定没送达，但事实是"超时"而不是"被拒"。
            raise MinecraftRuntimeError(
                f"连接 Minecraft runtime 超时（{self._base}）：{exc}",
                code="runtime.unreachable",
                timed_out=True,
                delivered=False,
            ) from exc
        except aiohttp.ClientError as exc:
            # 响应可能在请求发出之后才丢失（对端读完就断 / 响应中途截断），
            # 因此这里默认 delivered=True：调用方必须按"可能已经生效"处理。
            raise MinecraftRuntimeError(
                f"无法访问 Minecraft runtime（{self._base}）：{exc}",
                code="runtime.unreachable",
                timed_out=isinstance(exc, TimeoutError),
            ) from exc
        except TimeoutError as exc:
            raise MinecraftRuntimeError(
                f"Minecraft runtime 响应超时（{self._base}{path}）",
                code="runtime.unreachable",
                timed_out=True,
            ) from exc
