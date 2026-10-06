"""`/api/v1/minecraft/*`（WebUI v1 · Minecraft 连接层，契约 §7.5）。

路由只做「校验 → 调 MinecraftService」，错误统一翻译成契约信封；
连接事实全部留在 Bridge，这里不复制任何状态机逻辑。

* ``GET  /api/v1/minecraft``          —— 连接层只读投影（状态机/世界状态/runtime 健康）
* ``POST /api/v1/minecraft/join``     —— 加入服务器 ``{host, port}``
* ``POST /api/v1/minecraft/leave``    —— 主动离开（幂等）
* ``POST /api/v1/minecraft/events``   —— Bridge runtime 的回调端点（Bearer token 门，
  会话豁免在 ``WebServer._auth_middleware``；这里对 token 做权威复检）
"""

from __future__ import annotations

from typing import Any

from aiohttp import web

from app.integrations.minecraft.agent import ACTION_RISK
from app.integrations.minecraft.service import (
    MinecraftBridgeError,
    MinecraftDisabled,
    MinecraftService,
    canonical_item_name,
)
from app.web.api.common import (
    API_PREFIX,
    bad_request,
    json_endpoint,
    not_found,
    ok,
    read_json,
    unauthorized,
)
from app.web.api_errors import ApiError
from app.web.routes.base import WebContext

#: 连接层未启用时 `GET /minecraft` 的只读投影：读端点永远 200（功能状态不是
#: 故障），动作端点才以 503 `minecraft.disabled` 拒绝。
_DISABLED_SNAPSHOT: dict[str, Any] = {
    "enabled": False,
    "auth_configured": False,
    "runtime": {
        "running": False,
        "pid": None,
        "managed": False,
        "restarts": 0,
        "down": False,
        "log_tail": [],
    },
    "connection": {
        "status": "DISCONNECTED",
        "session_id": None,
        "host": None,
        "port": None,
        "username": None,
        "auth_mode": None,
        "dimension": None,
        "position": None,
        "health": None,
        "last_error": None,
        "kicked_reason": None,
        "connected_at": None,
    },
    # Phase 3C/3D：Pathfinder 诊断（未启用 = 永不动）
    "pathfinder": {"goal": None, "target": None, "distance": None, "moving": False},
    # Phase 3B：动作视图（未启用 = 永远 IDLE）
    "action": {
        "action": None,
        "action_id": None,
        "status": "IDLE",
        "started_at": None,
        "finished_at": None,
        "elapsed_ms": None,
    },
    "last_event": None,
    # Phase 3E：LLM Tool Debug（未启用 = 所有 Minecraft 工具都不可用）
    "agent": {
        "enabled": False,
        "context": {},
        "policy": {},
        "tools": [
            {
                "name": name,
                "risk": risk,
                "enabled": False,
                "allowed": False,
                "reason": "minecraft.disabled",
            }
            for name, risk in sorted(ACTION_RISK.items())
        ],
    },
}


#: Minecraft Tool 的稳定错误码 → HTTP 语义（开发调试入口用；与 Service 侧一致）
_TOOL_STATUS: dict[str, int] = {
    "minecraft.disabled": 503,
    "minecraft.offline": 409,
    "minecraft.not_connected": 409,
    "minecraft.action_busy": 409,
    "minecraft.action_invalid": 422,
    # Phase 4H.1：导航结束但实际位置不在到达半径内（原来只有 path_not_found）
    "minecraft.path_not_reached": 500,
    "minecraft.action_not_allowed": 403,
    "minecraft.user_not_trusted": 403,
    "minecraft.confirmation_required": 409,
    "minecraft.confirmation_invalid": 409,
    "minecraft.confirmation_expired": 409,
    "minecraft.confirmation_mismatch": 409,
    "minecraft.confirmation_not_user_turn": 409,
    "minecraft.block_not_found": 404,
    "minecraft.block_changed": 409,
    "minecraft.block_not_diggable": 422,
    "minecraft.block_too_far": 422,
    "minecraft.block_break_unconfirmed": 500,
    # Phase 4C：place 的目标/手持物品校验
    "minecraft.held_item_missing": 409,
    "minecraft.held_item_changed": 409,
    "minecraft.target_occupied": 409,
    "minecraft.reference_block_missing": 404,
    "minecraft.block_unavailable": 404,
    "minecraft.block_place_unconfirmed": 500,
    # Phase 4D：背包写操作
    "minecraft.item_not_found": 404,
    "minecraft.item_changed": 409,
    "minecraft.item_count_insufficient": 409,
    "minecraft.destination_occupied": 409,
    "minecraft.slot_invalid": 422,
    "minecraft.equip_unconfirmed": 500,
    "minecraft.move_unconfirmed": 500,
    # Phase 4E：container（读 Chest / Barrel + 单物品存取）
    "minecraft.container_unsupported": 422,
    "minecraft.container_too_far": 422,
    "minecraft.container_open_failed": 500,
    "minecraft.container_closed": 409,
    "minecraft.container_close_failed": 500,
    "minecraft.container_transfer_unconfirmed": 500,
    # Phase 4F：crafting（玩家 2×2）
    "minecraft.recipe_not_found": 404,
    "minecraft.recipe_unavailable": 409,
    "minecraft.recipe_changed": 409,
    "minecraft.material_insufficient": 409,
    "minecraft.craft_failed": 500,
    "minecraft.craft_unconfirmed": 500,
    # Phase 4G：指定工作台（3×3）
    "minecraft.crafting_table_missing": 404,
    "minecraft.crafting_table_invalid": 422,
    "minecraft.crafting_table_too_far": 422,
    # Phase 4H：掉落物 / 拾取
    "minecraft.item_entity_not_found": 404,
    "minecraft.item_entity_invalid": 422,
    "minecraft.item_entity_changed": 409,
    "minecraft.pickup_target_replaced": 409,
    "minecraft.pickup_target_lost": 409,
    "minecraft.pickup_target_too_far": 422,
    "minecraft.pickup_failed": 500,
    "minecraft.pickup_unconfirmed": 500,
}


def _agent_tools(bridge: Any, tools_runtime: Any = None) -> list[dict[str, Any]]:
    """每个 Minecraft Tool 的只读行：风险 / 注册开关 / 现在是否允许（§三十）。"""
    rows: list[dict[str, Any]] = []
    registry = getattr(tools_runtime, "registry", None)
    for name, risk in sorted(ACTION_RISK.items()):
        tool = registry.maybe_get(name) if registry is not None else None
        enabled = bool(tool is not None and registry is not None and registry.is_enabled(name))
        # 判定用「用户现在就明确要求」这一最宽松的合法前提：得到的是
        # 「在线且不忙时它会不会被放行」——正是调试要看的
        decision = bridge.policy.check(
            name,
            {},
            bridge.gate_facts(explicit_intent=True),
        )
        if tool is None:
            reason = "tool.unregistered"
        elif not enabled:
            reason = "tool.disabled"
        elif not decision.allowed:
            reason = decision.code
        else:
            reason = ""
        rows.append(
            {
                "name": name,
                "risk": risk,
                "enabled": enabled,
                "allowed": enabled and decision.allowed,
                "reason": reason,
            }
        )
    return rows


def _service(bot: Any) -> MinecraftService:
    service = getattr(bot, "minecraft", None)
    if service is None:
        raise MinecraftDisabled()
    assert isinstance(service, MinecraftService)
    return service


def _translate(exc: MinecraftBridgeError) -> ApiError:
    """MinecraftService 的稳定错误 → 契约信封（状态码由错误自带）。"""
    return ApiError(int(getattr(exc, "status", 400)), exc.code, str(exc))


class MinecraftApiRoutes(WebContext):
    """`/api/v1/minecraft` 及其动作。"""

    # ------------------------------------------------------------------ reads

    async def _v1_minecraft_get(self, request: web.Request) -> web.Response:
        service = getattr(self._bot, "minecraft", None)
        if service is None or not isinstance(service, MinecraftService):
            return ok(dict(_DISABLED_SNAPSHOT), request=request)
        try:
            data = await service.status()
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        data["agent"] = self._agent_view(service)
        return ok(data, request=request)

    def _agent_view(self, service: MinecraftService) -> dict[str, Any]:
        """Phase 3E：Agent 只读投影（上下文 + 每个工具的风险/开关/是否允许）。"""
        bridge = getattr(service, "agent", None)
        if bridge is None:
            return {"enabled": False, "context": {}, "policy": {}, "tools": []}
        return bridge.snapshot(tools=_agent_tools(bridge, getattr(self._bot, "tools", None)))

    async def _v1_minecraft_inventory(self, request: web.Request) -> web.Response:
        """Phase 4C：只读背包切片（选中的 hotbar 槽 / 手持物品 / 聚合物品清单）。

        读端点恒 200：Minecraft 未启用/不在世界里时如实返回 ``online: false``
        （「不在游戏里」是功能状态，不是故障）。
        """
        service = getattr(self._bot, "minecraft", None)
        if service is None or not isinstance(service, MinecraftService):
            return ok(
                {
                    "ok": True,
                    "online": False,
                    "selected_hotbar_slot": None,
                    "held_item": None,
                    "items": [],
                },
                request=request,
            )
        try:
            data = await service.inventory()
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(data, request=request)

    async def _v1_minecraft_inventory_slots(self, request: web.Request) -> web.Response:
        """Phase 4D：**调试**用的原始槽位视图（WebUI Move Test / smoke）。

        只读投影（槽位号 + 物品名 + 数量 + 是否快捷栏）；它**不是** LLM 工具的数据源 ——
        ``minecraft_inventory`` 仍然只给按物品名聚合的切片。未启用/未在线恒 200。
        """
        service = getattr(self._bot, "minecraft", None)
        if service is None or not isinstance(service, MinecraftService):
            return ok(
                {
                    "ok": True,
                    "online": False,
                    "hotbar_start": None,
                    "inventory_start": None,
                    "slots": [],
                },
                request=request,
            )
        try:
            data = await service.inventory_slots()
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(data, request=request)

    async def _v1_minecraft_world(self, request: web.Request) -> web.Response:
        """World Debug 只读视图（Phase 2）：语义模型 + raw snapshot + 缓存元信息。"""
        service = getattr(self._bot, "minecraft", None)
        if service is None or not isinstance(service, MinecraftService):
            return ok(
                {"available": False, "online": False, "reason": "minecraft disabled"},
                request=request,
            )
        return ok(service.world_view(), request=request)

    # ---------------------------------------------------------------- actions

    async def _v1_minecraft_join(self, request: web.Request) -> web.Response:
        body = await read_json(request)
        try:
            result = await _service(self._bot).join(
                str(body.get("host", "")), body.get("port", 25565)
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    async def _v1_minecraft_leave(self, request: web.Request) -> web.Response:
        await read_json(request, required=False)
        try:
            result = await _service(self._bot).leave()
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    # ------------------------------------------- Action Runtime（Phase 3B）

    async def _v1_minecraft_look_at(self, request: web.Request) -> web.Response:
        """让罐头看向世界坐标（SAFE 动作：不改世界、不移动）。"""
        body = await read_json(request)
        try:
            result = await _service(self._bot).look_at(body.get("x"), body.get("y"), body.get("z"))
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    async def _v1_minecraft_move_to(self, request: web.Request) -> web.Response:
        """非破坏性导航（Phase 3C）：移动到世界坐标（禁挖/禁放；不可达 → 500 path_not_found）。"""
        body = await read_json(request)
        try:
            result = await _service(self._bot).move_to(body.get("x"), body.get("y"), body.get("z"))
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    async def _v1_minecraft_follow_player(self, request: web.Request) -> web.Response:
        """动态跟随玩家（Phase 3D）：启动即返回 RUNNING，终态经事件/状态呈现。"""
        body = await read_json(request)
        try:
            result = await _service(self._bot).follow_player(
                body.get("username"), body.get("distance")
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    async def _v1_minecraft_stop(self, request: web.Request) -> web.Response:
        """最高优先级安全停止（幂等）：取消进行中动作，返回被取消的 action_id 列表。"""
        await read_json(request, required=False)
        try:
            result = await _service(self._bot).stop_action()
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        return ok(result, request=request)

    async def _v1_minecraft_dig(self, request: web.Request) -> web.Response:
        """Phase 4B：破坏**一个**指定方块（开发调试入口）。

        §三十七：WebUI 的动作按钮可以跳过「这一轮是不是用户对话」的判断（它本来就是
        开发者直接调用动作），但**不能**跳过 MEDIUM 确认门 —— 第一次调用只会得到
        409 ``minecraft.confirmation_required``（``detail`` 里带待确认信息），
        必须由用户在新的对话回合里说「确认」之后才会真正执行。
        """
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        # Phase 4I：可选 expected_tool —— 先规范化再进确认门（指纹绑参数本身，
        # "stone_pickaxe" 与 "minecraft:stone_pickaxe" 必须是同一个动作）
        raw_tool = body.get("expected_tool")
        tool = (
            canonical_item_name(raw_tool)
            if isinstance(raw_tool, str) and raw_tool.strip()
            else None
        )
        arguments: dict[str, Any] = {
            "x": body.get("x"),
            "y": body.get("y"),
            "z": body.get("z"),
            "expected_block": body.get("expected_block"),
        }
        if tool is not None:
            arguments["expected_tool"] = tool
        # 先做参数校验（垃圾参数不该挂出一条待确认），再进确认门
        try:
            service.validate_dig(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["expected_block"],
                tool,
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_dig",
            arguments,
            lambda svc: svc.dig(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["expected_block"],
                tool,
            ),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_place(self, request: web.Request) -> web.Response:
        """Phase 4C：放置**一个**方块（开发调试入口）。

        与 dig 同规矩（§二十六）：WebUI 可以跳过"这一轮是不是用户对话"的判断，
        但**不能**跳过 MEDIUM 确认门 —— 第一次调用只会得到 409
        ``minecraft.confirmation_required``（``detail`` 里带待确认信息）。
        """
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {
            "x": body.get("x"),
            "y": body.get("y"),
            "z": body.get("z"),
            "face": body.get("face"),
            "expected_item": body.get("expected_item"),
        }
        # 先做参数校验（垃圾参数不该挂出一条待确认），再进确认门
        try:
            service.validate_place(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["face"],
                arguments["expected_item"],
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_place",
            arguments,
            lambda svc: svc.place(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["face"],
                arguments["expected_item"],
            ),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_dig_capability(self, request: web.Request) -> web.Response:
        """Phase 4J：只读查「这个方块现在能不能挖、大概多久」（SAFE，同步返回）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {"x": body.get("x"), "y": body.get("y"), "z": body.get("z")}
        try:
            service.validate_dig_capability(arguments["x"], arguments["y"], arguments["z"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_dig_capability",
            arguments,
            lambda svc: svc.dig_capability(arguments["x"], arguments["y"], arguments["z"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_dropped_items(self, request: web.Request) -> web.Response:
        """Phase 4H：看附近的掉落物实体（SAFE 只读，同步返回）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        result = await bridge.invoke_developer(
            "minecraft_dropped_items",
            {},
            lambda svc: svc.dropped_items(),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_pickup_item(self, request: web.Request) -> web.Response:
        """Phase 4H：捡起一个明确的掉落物实体（MEDIUM，开发调试入口；**拿不到**执行权）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {
            "entity_id": body.get("entity_id"),
            "expected_item": body.get("expected_item"),
        }
        try:
            service.validate_pickup(arguments["entity_id"], arguments["expected_item"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_pickup_item",
            arguments,
            lambda svc: svc.pickup_item(arguments["entity_id"], arguments["expected_item"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_recipe_lookup(self, request: web.Request) -> web.Response:
        """Phase 4F：查一个物品在玩家 2×2 里能做的配方（SAFE 只读，同步返回）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {"item": body.get("item"), "crafting_table": body.get("crafting_table")}
        try:
            service.validate_recipe_lookup(arguments["item"], arguments["crafting_table"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_recipe_lookup",
            arguments,
            lambda svc: svc.recipe_lookup(arguments["item"], arguments["crafting_table"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_craft(self, request: web.Request) -> web.Response:
        """Phase 4F：执行一次配方（MEDIUM，开发调试入口；**拿不到**执行权）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {
            "recipe_id": body.get("recipe_id"),
            "crafting_table": body.get("crafting_table"),
        }
        try:
            service.validate_craft(arguments["recipe_id"], arguments["crafting_table"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_craft",
            arguments,
            lambda svc: svc.craft(arguments["recipe_id"], arguments["crafting_table"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_container_inspect(self, request: web.Request) -> web.Response:
        """Phase 4E：读一个 Chest / Barrel 的内容（SAFE，开发调试入口）。

        只读 inspection：不需要确认门，但仍然**独占**（打开窗口是有生命周期的客户端状态）。
        """
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {"x": body.get("x"), "y": body.get("y"), "z": body.get("z")}
        try:
            service.validate_container_inspect(arguments["x"], arguments["y"], arguments["z"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_container_inspect",
            arguments,
            lambda svc: svc.container_inspect(arguments["x"], arguments["y"], arguments["z"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_container_transfer(self, request: web.Request) -> web.Response:
        """Phase 4E：单物品在容器槽 ↔ 背包槽之间搬一次（MEDIUM，开发调试入口）。

        与 dig/place/equip/inventory_move 同规矩：先校验参数，再过确认门；WebUI
        **拿不到**执行权（非用户回合 → 409 confirmation_not_user_turn）。
        """
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {
            "x": body.get("x"),
            "y": body.get("y"),
            "z": body.get("z"),
            "direction": body.get("direction"),
            "container_slot": body.get("container_slot"),
            "inventory_slot": body.get("inventory_slot"),
            "item": body.get("item"),
            "count": body.get("count"),
        }
        try:
            service.validate_container_transfer(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["direction"],
                arguments["container_slot"],
                arguments["inventory_slot"],
                arguments["item"],
                arguments["count"],
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_container_transfer",
            arguments,
            lambda svc: svc.container_transfer(
                arguments["x"],
                arguments["y"],
                arguments["z"],
                arguments["direction"],
                arguments["container_slot"],
                arguments["inventory_slot"],
                arguments["item"],
                arguments["count"],
            ),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_equip(self, request: web.Request) -> web.Response:
        """Phase 4D：把指定物品拿到主手（开发调试入口；**必须**过 MEDIUM 确认门）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {"item": body.get("item")}
        try:
            service.validate_equip(arguments["item"])
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_equip",
            arguments,
            lambda svc: svc.equip(arguments["item"]),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    async def _v1_minecraft_inventory_move(self, request: web.Request) -> web.Response:
        """Phase 4D：单物品单槽位搬运（开发调试入口；**必须**过 MEDIUM 确认门）。"""
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        arguments = {
            "source_slot": body.get("source_slot"),
            "destination_slot": body.get("destination_slot"),
            "item": body.get("item"),
            "count": body.get("count"),
        }
        try:
            service.validate_inventory_move(
                arguments["source_slot"],
                arguments["destination_slot"],
                arguments["item"],
                arguments["count"],
            )
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        result = await bridge.invoke_developer(
            "minecraft_inventory_move",
            arguments,
            lambda svc: svc.inventory_move(
                arguments["source_slot"],
                arguments["destination_slot"],
                arguments["item"],
                arguments["count"],
            ),
        )
        if not result.success:
            code = result.error_type or "minecraft.action_failed"
            raise ApiError(
                _TOOL_STATUS.get(code, 500), code, result.error or code, detail=result.data
            )
        return ok(result.data, request=request)

    # ------------------------------------------- Confirmation Gate（Phase 4A）

    async def _v1_minecraft_confirm(self, request: web.Request) -> web.Response:
        """确认门的 Debug 端点（§九/§三十二）。

        ``action`` ∈ ``create_test`` / ``cancel`` / ``expire``：**只能缩小授权**——这里
        没有 ``confirm``/``consume``，真正的确认必须来自用户的新回合（对话里说「确认」）。
        """
        try:
            service = _service(self._bot)
        except MinecraftBridgeError as exc:
            raise _translate(exc) from exc
        bridge = getattr(service, "agent", None)
        if bridge is None:
            raise ApiError(503, "minecraft.disabled", "Minecraft Agent 未装配")
        body = await read_json(request)
        action = str(body.get("action") or "").strip()
        confirmation_id = str(body.get("confirmation_id") or "").strip()
        if action == "create_test":
            # 开发/验收用：造一条 PENDING。**只允许测试专用工具名**（minecraft_test_*）——
            # 生产动作（minecraft_dig 之类）的确认只能由「真实用户回合里发起该动作」创建，
            # 否则管理台就能凭空造出一条可被用户回合消费的正式授权（Phase 4B §一）。
            tool = str(body.get("tool") or "").strip()
            if not tool:
                raise bad_request(
                    "create_test 需要 tool", code="minecraft.action_invalid", field="tool"
                )
            if not tool.startswith("minecraft_test_"):
                raise bad_request(
                    "create_test 只能创建测试专用工具（minecraft_test_* 前缀）的确认；"
                    "正式动作的确认必须来自用户回合",
                    code="minecraft.confirmation_invalid",
                    field="tool",
                )
            risk = str(body.get("risk") or bridge.policy.risk_of(tool) or "MEDIUM").upper()
            arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
            pending = bridge.confirmations.create(
                session_id=str(body.get("session_id") or "webui:confirm-test"),
                user_id=str(body.get("user_id") or "webui-admin"),
                tool=tool,
                risk=risk,
                arguments=arguments,
                summary=f"{tool}（{risk}）：WebUI 测试确认",
            )
            return ok({"created": True, "confirmation": pending.to_dict()}, request=request)
        if action == "cancel":
            changed = bridge.confirmations.cancel(confirmation_id)
            status = "CANCELLED"
        elif action == "expire":
            changed = bridge.confirmations.expire(confirmation_id)
            status = "EXPIRED"
        else:
            raise bad_request(
                "action 必须是 create_test / cancel / expire",
                code="minecraft.confirmation_invalid",
                field="action",
            )
        if not changed:
            raise not_found("确认不存在或已不是 PENDING", code="minecraft.confirmation_invalid")
        return ok(
            {"confirmation_id": confirmation_id, "status": status},
            request=request,
        )

    # ------------------------------------------------------- bridge callbacks

    async def _v1_minecraft_events(self, request: web.Request) -> web.Response:
        """Bridge runtime 的事件回调。中间件已做 token 门，这里做权威复检。"""
        service = _service(self._bot)
        token = getattr(service, "callback_token", None)
        supplied = request.headers.get("Authorization", "")
        if not token or supplied != f"Bearer {token}":
            raise unauthorized("Bridge 回调 token 无效")
        body = await read_json(request)
        try:
            await service.receive_event(body)
        except ValueError as exc:
            raise bad_request(str(exc), code="minecraft.bad_event") from exc
        return ok({"accepted": True}, request=request)

    # ----------------------------------------------------------- registration

    def _register_v1_minecraft(self, app: web.Application) -> None:
        wrap = json_endpoint
        app.router.add_get(f"{API_PREFIX}/minecraft", wrap(self._v1_minecraft_get))
        app.router.add_get(f"{API_PREFIX}/minecraft/world", wrap(self._v1_minecraft_world))
        app.router.add_get(f"{API_PREFIX}/minecraft/inventory", wrap(self._v1_minecraft_inventory))
        app.router.add_get(
            f"{API_PREFIX}/minecraft/inventory/slots", wrap(self._v1_minecraft_inventory_slots)
        )
        app.router.add_post(f"{API_PREFIX}/minecraft/join", wrap(self._v1_minecraft_join))
        app.router.add_post(f"{API_PREFIX}/minecraft/leave", wrap(self._v1_minecraft_leave))
        app.router.add_post(f"{API_PREFIX}/minecraft/look_at", wrap(self._v1_minecraft_look_at))
        app.router.add_post(f"{API_PREFIX}/minecraft/move_to", wrap(self._v1_minecraft_move_to))
        app.router.add_post(
            f"{API_PREFIX}/minecraft/follow_player", wrap(self._v1_minecraft_follow_player)
        )
        app.router.add_post(f"{API_PREFIX}/minecraft/stop", wrap(self._v1_minecraft_stop))
        app.router.add_post(f"{API_PREFIX}/minecraft/events", wrap(self._v1_minecraft_events))
        app.router.add_post(
            f"{API_PREFIX}/minecraft/agent/confirm", wrap(self._v1_minecraft_confirm)
        )
        app.router.add_post(f"{API_PREFIX}/minecraft/dig", wrap(self._v1_minecraft_dig))
        app.router.add_post(f"{API_PREFIX}/minecraft/place", wrap(self._v1_minecraft_place))
        app.router.add_post(f"{API_PREFIX}/minecraft/equip", wrap(self._v1_minecraft_equip))
        app.router.add_post(
            f"{API_PREFIX}/minecraft/recipe_lookup", wrap(self._v1_minecraft_recipe_lookup)
        )
        app.router.add_post(f"{API_PREFIX}/minecraft/craft", wrap(self._v1_minecraft_craft))
        app.router.add_post(
            f"{API_PREFIX}/minecraft/dig_capability", wrap(self._v1_minecraft_dig_capability)
        )
        app.router.add_post(
            f"{API_PREFIX}/minecraft/dropped_items", wrap(self._v1_minecraft_dropped_items)
        )
        app.router.add_post(
            f"{API_PREFIX}/minecraft/pickup_item", wrap(self._v1_minecraft_pickup_item)
        )
        app.router.add_post(
            f"{API_PREFIX}/minecraft/container_inspect",
            wrap(self._v1_minecraft_container_inspect),
        )
        app.router.add_post(
            f"{API_PREFIX}/minecraft/container_transfer",
            wrap(self._v1_minecraft_container_transfer),
        )
        app.router.add_post(
            f"{API_PREFIX}/minecraft/inventory_move", wrap(self._v1_minecraft_inventory_move)
        )
