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
)
from app.web.api.common import API_PREFIX, bad_request, json_endpoint, ok, read_json, unauthorized
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
    # Phase 3E：LLM Tool Debug（未启用 = 六个工具都不可用）
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


def _agent_tools(bridge: Any, tools_runtime: Any = None) -> list[dict[str, Any]]:
    """六个 Minecraft Tool 的只读行：风险 / 注册开关 / 现在是否允许（§三十）。"""
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
        """Phase 3E：Agent 只读投影（上下文 + 六个工具的风险/开关/是否允许）。"""
        bridge = getattr(service, "agent", None)
        if bridge is None:
            return {"enabled": False, "context": {}, "policy": {}, "tools": []}
        return bridge.snapshot(tools=_agent_tools(bridge, getattr(self._bot, "tools", None)))

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
        app.router.add_post(f"{API_PREFIX}/minecraft/join", wrap(self._v1_minecraft_join))
        app.router.add_post(f"{API_PREFIX}/minecraft/leave", wrap(self._v1_minecraft_leave))
        app.router.add_post(f"{API_PREFIX}/minecraft/look_at", wrap(self._v1_minecraft_look_at))
        app.router.add_post(f"{API_PREFIX}/minecraft/move_to", wrap(self._v1_minecraft_move_to))
        app.router.add_post(
            f"{API_PREFIX}/minecraft/follow_player", wrap(self._v1_minecraft_follow_player)
        )
        app.router.add_post(f"{API_PREFIX}/minecraft/stop", wrap(self._v1_minecraft_stop))
        app.router.add_post(f"{API_PREFIX}/minecraft/events", wrap(self._v1_minecraft_events))
