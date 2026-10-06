"""Minecraft Agent Tool 层测试（Phase 3E §三十四-§三十八）。

覆盖：Tool Registry 注册（§34）、Policy 门（§35）、Tool→Service 边界（§36）、
异步动作不阻塞（§37）、Action 事件回到 Context（§38）。

所有用例都用**假 Service**（同形的异步方法 + 可编排的失败），不碰 Node runtime：
真 runtime 的行为由 `minecraft_runtime/test/*.js` 与 E2E 负责。
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import (
    ACTION_RISK,
    BRIDGE_KEY,
    INTENT_KEY,
    RUNTIME_ERROR_CODES,
    GateFacts,
    MinecraftActionPolicy,
    MinecraftAgentBridge,
    MinecraftAgentContext,
    PolicyDecision,
)
from app.integrations.minecraft.events import MinecraftBridgeEvent
from app.integrations.minecraft.service import (
    MinecraftActionBusy,
    MinecraftActionInvalid,
    MinecraftBridgeError,
    MinecraftPathNotFound,
    MinecraftPlayerNotFound,
)
from app.tools.builtins import (
    MinecraftChatTool,
    MinecraftFollowPlayerTool,
    MinecraftLookAtTool,
    MinecraftMoveToTool,
    MinecraftStopTool,
    MinecraftWorldTool,
)
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime

# ------------------------------------------------------------------ 假 Service


def world_view_payload(*, players: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {
        "available": True,
        "online": True,
        "age_seconds": 0.4,
        "semantic": {
            "self": {
                "dimension": "minecraft:overworld",
                "location": "plains",
                "position": {"x": 120.5, "y": 64.0, "z": -230.5},
                "health": 20,
            },
            "environment": {"time_phase": "白天", "weather": "clear"},
            "players": players
            if players is not None
            else [
                {
                    "name": "空凛",
                    "direction": "front_right",
                    "distance": 6.4,
                    "compass": "east",
                    "position": {"x": 126.0, "y": 64.0, "z": -228.0},
                }
            ],
            "entities": [{"type": "cow", "count": 2, "direction": "left", "distance": 9.0}],
            "terrain": [{"type": "water", "direction": "north", "distance": 12.0}],
            "points_of_interest": [],
        },
    }


class FakeMinecraftService:
    """Agent 层用的假 Service：只暴露真实 Service 的公开方法，记录每一次调用。"""

    def __init__(
        self,
        *,
        online: bool = True,
        players: list[dict[str, Any]] | None = None,
        enabled: bool = True,
        username: str = "Catodayo",
    ) -> None:
        self.enabled = enabled
        #: 罐头自己的 MC 名字（chat 桥用它挡「自己说自己的话」）
        self.username = username
        self.config = MinecraftConfig(enabled=True, auto_start_runtime=False)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.plan: dict[str, list[Any]] = {}
        # 与真实 Service 同形：未启用 → 感知层根本没起来，只回「不可用」
        if not enabled:
            self.view: dict[str, Any] = {
                "available": False,
                "online": False,
                "reason": "minecraft disabled",
            }
        else:
            self.view = (
                world_view_payload(players=players)
                if online
                else {"available": True, "online": False, "semantic": None}
            )

    # --- 编排
    def enqueue(self, action: str, outcome: Any) -> None:
        self.plan.setdefault(action, []).append(outcome)

    def _next(self, action: str, default: Any) -> Any:
        queue = self.plan.get(action) or []
        if queue:
            return queue.pop(0)
        return default

    def _record(self, action: str, **payload: Any) -> None:
        self.calls.append((action, payload))

    # --- Service 公开面（与 MinecraftService 同形的返回值）
    async def send_chat(self, message: str) -> dict[str, Any]:
        self._record("chat", message=message)
        outcome = self._next("chat", {"ok": True, "sent": True})
        if isinstance(outcome, Exception):
            raise outcome
        return {"ok": True, "sent": True, **outcome}

    async def look_at(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        self._record("look_at", x=x, y=y, z=z)
        outcome = self._next("look_at", {"status": "SUCCEEDED", "action_id": "act_look_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_look_1", "action": "look_at", **outcome}

    async def move_to(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        self._record("move_to", x=x, y=y, z=z)
        outcome = self._next("move_to", {"status": "RUNNING", "action_id": "act_move_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_move_1", "action": "move_to", **outcome}

    async def follow_player(self, username: Any, distance: Any = None) -> dict[str, Any]:
        self._record("follow_player", username=username, distance=distance)
        outcome = self._next("follow_player", {"status": "RUNNING", "action_id": "act_follow_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_follow_1", "action": "follow_player", **outcome}

    async def inventory(self) -> dict[str, Any]:
        self._record("inventory")
        outcome = self._next(
            "inventory",
            {
                "ok": True,
                "online": True,
                "selected_hotbar_slot": 0,
                "held_item": {"name": "dirt", "count": 12},
                "items": [{"name": "dirt", "count": 12}, {"name": "sand", "count": 24}],
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def place(self, x: Any, y: Any, z: Any, face: Any, expected_item: Any) -> dict[str, Any]:
        self._record("place", x=x, y=y, z=z, face=face, expected_item=expected_item)
        outcome = self._next("place", {"status": "RUNNING", "action_id": "act_place_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_place_1", "action": "place", **outcome}

    async def inventory_slots(self) -> dict[str, Any]:
        self._record("inventory_slots")
        outcome = self._next(
            "inventory_slots",
            {
                "ok": True,
                "online": True,
                "hotbar_start": 36,
                "inventory_start": 9,
                "slots": [
                    {"slot": 9, "item": "dirt", "count": 12, "hotbar": False},
                    {"slot": 36, "item": "dirt", "count": 12, "hotbar": True},
                ],
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    async def equip(self, item: Any) -> dict[str, Any]:
        self._record("equip", item=item)
        outcome = self._next("equip", {"status": "RUNNING", "action_id": "act_equip_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_equip_1", "action": "equip", **outcome}

    async def inventory_move(
        self, source_slot: Any, destination_slot: Any, item: Any, count: Any
    ) -> dict[str, Any]:
        self._record(
            "inventory_move",
            source_slot=source_slot,
            destination_slot=destination_slot,
            item=item,
            count=count,
        )
        outcome = self._next(
            "inventory_move", {"status": "RUNNING", "action_id": "act_move_item_1"}
        )
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_move_item_1", "action": "inventory_move", **outcome}

    async def recipe_lookup(self, item: Any) -> dict[str, Any]:
        self._record("recipe_lookup", item=item)
        canonical = str(item or "").strip().lower().replace("minecraft:", "")
        outcome = self._next(
            "recipe_lookup",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "item": canonical,
                    "status": "available",
                    "total": 2,
                    "recipes": [
                        {
                            "recipe_id": "stick*4=oak_planks*2",
                            "result": {"name": "stick", "count_per_craft": 4},
                            "requires_table": False,
                            "available": True,
                            "ingredients": [{"name": "oak_planks", "count": 2}],
                        },
                        {
                            "recipe_id": "stick*4=birch_planks*2",
                            "result": {"name": "stick", "count_per_craft": 4},
                            "requires_table": False,
                            "available": False,
                            "ingredients": [{"name": "birch_planks", "count": 2}],
                        },
                    ],
                },
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_recipe_1", "action": "recipe_lookup", **outcome}

    async def craft(self, recipe_id: Any) -> dict[str, Any]:
        self._record("craft", recipe_id=recipe_id)
        outcome = self._next("craft", {"status": "RUNNING", "action_id": "act_craft_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_craft_1", "action": "craft", **outcome}

    async def container_inspect(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
        self._record("container_inspect", x=x, y=y, z=z)
        outcome = self._next(
            "container_inspect",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "container": {
                        "type": "minecraft:chest",
                        "label": "Chest",
                        "position": {"x": x, "y": y, "z": z},
                        "size": 27,
                    },
                    "slots": [
                        {"slot": 0, "name": "dirt", "count": 12},
                        {"slot": 7, "name": "sand", "count": 32},
                    ],
                },
            },
        )
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_cinspect_1", "action": "container_inspect", **outcome}

    async def container_transfer(
        self,
        x: Any,
        y: Any,
        z: Any,
        direction: Any,
        container_slot: Any,
        inventory_slot: Any,
        item: Any,
        count: Any,
    ) -> dict[str, Any]:
        self._record(
            "container_transfer",
            x=x,
            y=y,
            z=z,
            direction=direction,
            container_slot=container_slot,
            inventory_slot=inventory_slot,
            item=item,
            count=count,
        )
        outcome = self._next(
            "container_transfer", {"status": "RUNNING", "action_id": "act_ctransfer_1"}
        )
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_ctransfer_1", "action": "container_transfer", **outcome}

    async def dig(self, x: Any, y: Any, z: Any, expected_block: Any) -> dict[str, Any]:
        self._record("dig", x=x, y=y, z=z, expected_block=expected_block)
        outcome = self._next("dig", {"status": "RUNNING", "action_id": "act_dig_1"})
        if isinstance(outcome, Exception):
            raise outcome
        return {"action_id": "act_dig_1", "action": "dig", **outcome}

    async def stop_action(self) -> dict[str, Any]:
        self._record("stop")
        outcome = self._next("stop", {"ok": True, "status": "IDLE", "cancelled": []})
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    def world_view(self) -> dict[str, Any]:
        return self.view

    def snapshot(self) -> dict[str, Any]:
        """与真实 Service 同形的最小投影（chat 桥要 connection.username/host/port）。"""
        return {
            "connection": {
                "status": "ONLINE" if self.view.get("online") else "DISCONNECTED",
                "username": self.username,
                "host": "127.0.0.1",
                "port": 25565,
            }
        }

    # --- 便捷断言
    def action_calls(self, name: str) -> list[dict[str, Any]]:
        return [payload for action, payload in self.calls if action == name]


def make_bridge(service: FakeMinecraftService | None = None, **kwargs: Any) -> MinecraftAgentBridge:
    return MinecraftAgentBridge(service or FakeMinecraftService(), **kwargs)


def make_context(bridge: MinecraftAgentBridge | None, *, explicit: bool = True) -> ToolContext:
    metadata: dict[str, Any] = {}
    if bridge is not None:
        metadata[BRIDGE_KEY] = bridge
        if explicit:
            metadata[INTENT_KEY] = True
    return ToolContext(user_id="10001", session_id="s1", metadata=metadata)


def event(name: str, **data: Any) -> MinecraftBridgeEvent:
    raw = {"event": name, "session_id": "s1", "timestamp": 1.0, **data}
    from app.integrations.minecraft.events import parse_bridge_event

    return parse_bridge_event(raw)


# ------------------------------------------------------------ §34 注册表


TOOL_CLASSES = {
    "minecraft_world": MinecraftWorldTool,
    "minecraft_chat": MinecraftChatTool,
    "minecraft_look_at": MinecraftLookAtTool,
    "minecraft_move_to": MinecraftMoveToTool,
    "minecraft_follow_player": MinecraftFollowPlayerTool,
    "minecraft_stop": MinecraftStopTool,
}


async def _runtime() -> ToolRuntime:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    return runtime


async def test_minecraft_world_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_world")
    assert tool is not None and isinstance(tool, MinecraftWorldTool)
    assert runtime.registry.is_enabled("minecraft_world")
    assert tool.metadata.input_schema["properties"] == {}


async def test_minecraft_chat_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_chat")
    assert isinstance(tool, MinecraftChatTool)
    assert tool.metadata.input_schema["required"] == ["message"]


async def test_minecraft_look_at_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_look_at")
    assert isinstance(tool, MinecraftLookAtTool)
    assert tool.metadata.input_schema["required"] == ["x", "y", "z"]
    # §七：绝不把 yaw/pitch 暴露给模型
    assert "yaw" not in tool.metadata.input_schema["properties"]
    assert "pitch" not in tool.metadata.input_schema["properties"]


async def test_minecraft_move_to_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_move_to")
    assert isinstance(tool, MinecraftMoveToTool)
    # §八：只有 x/y/z，没有速度/跳跃/寻路参数
    assert set(tool.metadata.input_schema["properties"]) == {"x", "y", "z"}
    assert tool.metadata.input_schema["additionalProperties"] is False


async def test_minecraft_follow_player_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_follow_player")
    assert isinstance(tool, MinecraftFollowPlayerTool)
    assert set(tool.metadata.input_schema["properties"]) == {"username", "distance"}
    assert tool.metadata.input_schema["required"] == ["username"]


async def test_minecraft_stop_tool_registered() -> None:
    runtime = await _runtime()
    tool = runtime.registry.maybe_get("minecraft_stop")
    assert isinstance(tool, MinecraftStopTool)
    assert tool.metadata.input_schema["properties"] == {}


async def test_all_tools_share_one_risk_table() -> None:
    """Phase 4E 起十三个生产 Tool（SAFE 只读 + 五个 MEDIUM 写动作）。"""
    runtime = await _runtime()
    for name in ACTION_RISK:
        tool = runtime.registry.maybe_get(name)
        assert tool is not None, name
        # 通用 Tool Runtime 的风险词表是 low/medium/high；真正的动作分级在 ACTION_RISK
        assert tool.metadata.risk_level == "low"
        assert tool.metadata.description
    assert runtime.registry.names() == [
        "calculator",
        "minecraft_chat",
        "minecraft_container_inspect",
        "minecraft_container_transfer",
        "minecraft_craft",
        "minecraft_dig",
        "minecraft_equip",
        "minecraft_follow_player",
        "minecraft_inventory",
        "minecraft_inventory_move",
        "minecraft_look_at",
        "minecraft_move_to",
        "minecraft_place",
        "minecraft_recipe_lookup",
        "minecraft_stop",
        "minecraft_world",
        "query_image_memory",
        "time",
        "weather",
        "web_search",
    ]


# -------------------------------------------------------------- §35 Policy


def policy(**flags: Any) -> MinecraftActionPolicy:
    from app.config.settings import MinecraftAgentToolsConfig

    return MinecraftActionPolicy(MinecraftAgentToolsConfig(**flags))


def online_facts(**overrides: Any) -> GateFacts:
    base = {
        "minecraft_enabled": True,
        "online": True,
        "busy": "",
        "explicit_intent": True,
        "nearby_players": ("空凛",),
    }
    base.update(overrides)
    return GateFacts(**base)


def test_safe_action_is_allowed() -> None:
    for tool in ("minecraft_world", "minecraft_chat", "minecraft_look_at", "minecraft_stop"):
        decision = policy().check(tool, {}, online_facts(explicit_intent=False))
        assert decision.allowed, (tool, decision)


def test_low_action_with_explicit_user_intent_is_allowed() -> None:
    for tool in ("minecraft_move_to", "minecraft_follow_player"):
        decision = policy().check(tool, {"username": "空凛"}, online_facts())
        assert decision.allowed, (tool, decision)
        assert decision.risk == "LOW"


def test_low_action_without_explicit_intent_is_rejected() -> None:
    # §十五：模型自己推理出「我应该跟过去」也不行 —— 必须有用户请求
    decision = policy().check(
        "minecraft_move_to", {"x": 1, "y": 2, "z": 3}, online_facts(explicit_intent=False)
    )
    assert not decision.allowed
    assert decision.code == "minecraft.action_not_allowed"


def test_minecraft_offline_is_rejected() -> None:
    for tool in (
        "minecraft_chat",
        "minecraft_look_at",
        "minecraft_move_to",
        "minecraft_follow_player",
    ):
        decision = policy().check(tool, {}, online_facts(online=False))
        assert not decision.allowed and decision.code == "minecraft.offline", tool


def test_read_only_and_stop_work_offline() -> None:
    # 只读的世界查询要能回答「我现在没在游戏里」；stop 是最优先级安全停止，永远可用
    assert policy().check("minecraft_world", {}, online_facts(online=False)).allowed
    assert policy().check("minecraft_stop", {}, online_facts(online=False)).allowed


def test_minecraft_disabled_is_rejected() -> None:
    decision = policy().check("minecraft_world", {}, online_facts(minecraft_enabled=False))
    assert not decision.allowed and decision.code == "minecraft.disabled"


def test_tools_switch_off_is_rejected() -> None:
    decision = policy(enabled=False).check("minecraft_world", {}, online_facts())
    assert not decision.allowed and decision.code == "minecraft.disabled"


def test_risk_level_flag_gates_the_action() -> None:
    # §三十一：关掉 allow_low → LOW 动作被拒，SAFE 照常
    assert not policy(allow_low=False).check("minecraft_move_to", {}, online_facts()).allowed
    assert policy(allow_low=False).check("minecraft_world", {}, online_facts()).allowed


def test_busy_foreground_action_is_rejected() -> None:
    decision = policy().check("minecraft_move_to", {}, online_facts(busy="follow_player"))
    assert not decision.allowed and decision.code == "minecraft.action_busy"
    # 非互斥的 SAFE 动作可以和前台动作并存（§二十六：chat 唯一允许并存）
    assert policy().check("minecraft_chat", {}, online_facts(busy="move_to")).allowed
    assert policy().check("minecraft_stop", {}, online_facts(busy="move_to")).allowed


def test_unknown_tool_is_rejected() -> None:
    # attack/craft/eat 这类还没有实现的动作：风险表里没有名字 → 拒绝
    for unknown in ("minecraft_attack", "minecraft_craft_all", "minecraft_eat", "minecraft_smelt"):
        decision = policy().check(unknown, {}, online_facts())
        assert not decision.allowed and decision.code == "minecraft.action_invalid", unknown


def test_follow_target_must_be_visible() -> None:
    decision = policy().check(
        "minecraft_follow_player", {"username": "无名氏"}, online_facts(nearby_players=("空凛",))
    )
    assert not decision.allowed and decision.code == "minecraft.player_not_found"
    # 目标确实在附近 → 放行
    assert policy().check("minecraft_follow_player", {"username": "空凛"}, online_facts()).allowed


def test_risk_flags_gate_every_level() -> None:
    table = policy()
    # Phase 4B/4C：dig 与 place 都是 MEDIUM，风险开关默认关闭 → 配置视角先拦一道
    assert table.risk_of("minecraft_dig") == "MEDIUM"
    assert table.risk_of("minecraft_place") == "MEDIUM"
    assert table.risk_of("minecraft_equip") == "MEDIUM"
    assert table.risk_of("minecraft_inventory_move") == "MEDIUM"
    assert table.risk_of("minecraft_container_inspect") == "SAFE"
    assert table.risk_of("minecraft_container_transfer") == "MEDIUM"
    assert table.risk_of("minecraft_recipe_lookup") == "SAFE"
    assert table.risk_of("minecraft_craft") == "MEDIUM"
    assert table.risk_of("minecraft_inventory") == "SAFE"
    assert table.allowed_by_config("minecraft_dig") is False
    assert table.allowed_by_config("minecraft_place") is False
    assert table.allowed_by_config("minecraft_inventory") is True
    assert policy(allow_medium=True).allowed_by_config("minecraft_dig") is True
    assert policy(allow_medium=True).allowed_by_config("minecraft_place") is True
    # 还没实现的等级：风险表里没有名字，开了开关也不放行
    assert table.risk_of("minecraft_attack") == ""
    assert policy(allow_high=True).allowed_by_config("minecraft_attack") is False
    assert table.allowed_by_config("minecraft_world") is True
    assert table.allowed_by_config("minecraft_move_to") is True


# --------------------------------------------------- §36 Tool → Service 边界


async def test_world_tool_returns_structured_projection_and_calls_service() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    result = await MinecraftWorldTool().execute({}, make_context(bridge))
    assert result.success and result.data["ok"] is True
    assert result.data["dimension"] == "minecraft:overworld"
    assert result.data["position"] == {"x": 120.5, "y": 64.0, "z": -230.5}
    player = result.data["nearby_players"][0]
    assert player["name"] == "空凛" and player["position"]["x"] == 126.0
    # 结构化结果 + 一句 summary（§五）
    assert "空凛" in result.summary and "(120.5, 64.0, -230.5)" in result.summary
    assert service.calls == []  # 只读工具不产生任何动作调用


async def test_world_tool_reports_offline_without_inventing_facts() -> None:
    bridge = make_bridge(FakeMinecraftService(online=False))
    result = await MinecraftWorldTool().execute({}, make_context(bridge))
    assert result.success is False
    assert result.error_type == "minecraft.offline"


async def test_action_tools_only_talk_to_the_service() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    context = make_context(bridge)
    await MinecraftChatTool().execute({"message": "大家好"}, context)
    await MinecraftLookAtTool().execute({"x": 1, "y": 64, "z": 2}, context)
    await MinecraftMoveToTool().execute({"x": 130, "y": 64, "z": -230}, context)
    await MinecraftFollowPlayerTool().execute({"username": "空凛", "distance": 3}, context)
    await MinecraftStopTool().execute({}, context)
    assert [name for name, _ in service.calls] == [
        "chat",
        "look_at",
        "move_to",
        "follow_player",
        "stop",
    ]
    assert service.action_calls("move_to") == [{"x": 130, "y": 64, "z": -230}]
    assert service.action_calls("follow_player") == [{"username": "空凛", "distance": 3}]


def test_tool_layer_never_touches_runtime_transport() -> None:
    """§三：Tool 不允许自己构造 Bridge HTTP / 操作 Pathfinder / 碰 Mineflayer。"""
    import inspect

    from app.tools.builtins import minecraft_actions, minecraft_world

    for module in (minecraft_actions, minecraft_world):
        source = inspect.getsource(module)
        for forbidden in ("runtime_client", "mineflayer", "aiohttp", "127.0.0.1", "pathfinder"):
            assert forbidden not in source, f"{module.__name__} 出现了 {forbidden}"


async def test_action_tools_fail_closed_without_a_bridge() -> None:
    for tool_class in TOOL_CLASSES.values():
        result = await tool_class().execute({}, make_context(None))
        assert result.success is False
        assert result.error_type == "minecraft.disabled"


async def test_service_errors_are_structured_for_the_model() -> None:
    service = FakeMinecraftService()
    service.enqueue("move_to", MinecraftPathNotFound())
    service.enqueue("move_to", MinecraftActionBusy())
    service.enqueue("move_to", MinecraftActionInvalid("坐标超出边界"))
    bridge = make_bridge(service)
    context = make_context(bridge)
    for expected in (
        "minecraft.path_not_found",
        "minecraft.action_busy",
        "minecraft.action_invalid",
    ):
        result = await MinecraftMoveToTool().execute({"x": 1, "y": 2, "z": 3}, context)
        assert result.success is False
        assert result.error_type == expected, result
        assert result.data["error"]["code"] == expected
        assert "Traceback" not in str(result.data)


async def test_follow_target_not_nearby_is_rejected_before_any_service_call() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    result = await MinecraftFollowPlayerTool().execute({"username": "冒充者"}, make_context(bridge))
    assert result.success is False and result.error_type == "minecraft.player_not_found"
    assert service.calls == []  # 判定在 Service 之前


# ------------------------------------------------------- §37 异步不阻塞


async def test_move_to_returns_running_without_waiting_for_the_walk() -> None:
    service = FakeMinecraftService()
    runtime = await _runtime()
    executor = ToolExecutor(ToolsConfig(enabled=True), runtime.registry, runtime.policy)
    bridge = make_bridge(service)
    started = time.perf_counter()
    result = await asyncio.wait_for(
        executor.execute(
            ToolCall(
                name="minecraft_move_to",
                arguments={"x": 130, "y": 64, "z": -230},
            ),
            make_context(bridge),
            TurnBudget(),
        ),
        timeout=5.0,
    )
    elapsed = time.perf_counter() - started
    assert result.success and result.data["status"] == "RUNNING"
    assert result.data["action_id"] == "act_move_1"
    assert elapsed < 1.0, f"工具请求不该等导航结束（{elapsed:.2f}s）"


async def test_follow_player_returns_running_without_waiting_120s() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    started = time.perf_counter()
    result = await asyncio.wait_for(
        MinecraftFollowPlayerTool().execute({"username": "空凛"}, make_context(bridge)),
        timeout=5.0,
    )
    elapsed = time.perf_counter() - started
    assert result.success and result.data["status"] == "RUNNING"
    assert result.data["action_id"] == "act_follow_1"
    assert elapsed < 1.0, f"跟随最长跑 120s，工具必须立刻返回（{elapsed:.2f}s）"


async def test_stop_reports_cancelled_action_ids() -> None:
    service = FakeMinecraftService()
    service.enqueue("stop", {"ok": True, "status": "IDLE", "cancelled": ["act_follow_1"]})
    bridge = make_bridge(service)
    result = await MinecraftStopTool().execute({}, make_context(bridge))
    assert result.success and result.data["status"] == "IDLE"
    assert result.data["cancelled"] == ["act_follow_1"]
    assert "act_follow_1" in result.summary


# ----------------------------------------------------------- §38 事件→上下文


def test_started_event_marks_current_action() -> None:
    context = MinecraftAgentContext()
    context.apply_event(event("minecraft.spawned", username="GuanTou"))
    context.apply_event(
        event(
            "minecraft.action.started", action="follow_player", action_id="act_1", status="RUNNING"
        )
    )
    assert context.online is True
    assert context.current_action == {
        "action": "follow_player",
        "action_id": "act_1",
        "status": "RUNNING",
        "started_at": None,
    }
    assert context.last_action is None


def test_completed_event_closes_current_action_and_records_activity() -> None:
    context = MinecraftAgentContext()
    context.apply_event(event("minecraft.action.started", action="move_to", action_id="act_1"))
    context.apply_event(
        event(
            "minecraft.action.completed",
            action="move_to",
            action_id="act_1",
            status="SUCCEEDED",
            result={"final_position": {"x": 126.0, "y": 64.0, "z": -228.0}},
        )
    )
    assert context.current_action is None
    assert context.last_action["status"] == "SUCCEEDED"
    assert context.last_action["result"]["final_position"]["x"] == 126.0
    assert context.activity == "刚走到 (126, 64, -228)"


def test_failed_event_records_a_stable_error_code() -> None:
    context = MinecraftAgentContext()
    context.apply_event(event("minecraft.action.started", action="move_to", action_id="act_1"))
    context.apply_event(
        event(
            "minecraft.action.failed",
            action="move_to",
            action_id="act_1",
            status="FAILED",
            code="path.not_found",
            error="无法找到到达目标的非破坏性路径",
        )
    )
    assert context.current_action is None
    assert context.last_action["status"] == "FAILED"
    # runtime 的内部词表 → 面向模型的稳定词表（§二十七）
    assert context.last_action["code"] == "minecraft.path_not_found"
    assert context.activity == ""


def test_cancelled_event_clears_the_foreground_action() -> None:
    context = MinecraftAgentContext()
    context.apply_event(
        event("minecraft.action.started", action="follow_player", action_id="act_9")
    )
    context.apply_event(
        event(
            "minecraft.action.cancelled",
            action="follow_player",
            action_id="act_9",
            status="CANCELLED",
        )
    )
    assert context.current_action is None
    assert context.last_action["status"] == "CANCELLED"


def test_disconnect_clears_the_current_action() -> None:
    context = MinecraftAgentContext()
    context.apply_event(event("minecraft.spawned", username="GuanTou"))
    context.apply_event(event("minecraft.action.started", action="move_to", action_id="act_1"))
    context.apply_event(event("minecraft.disconnected"))
    assert context.online is False and context.current_action is None


def test_running_is_never_self_completed() -> None:
    """§二十二：RUNNING 不许被自己「模拟完成」。"""
    context = MinecraftAgentContext()
    context.apply_event(event("minecraft.action.started", action="move_to", action_id="act_1"))
    for _ in range(20):
        context.apply_event(event("minecraft.action.started", action="move_to", action_id="act_1"))
    assert context.current_action is not None
    assert context.last_action is None


def test_runtime_error_codes_map_to_stable_codes() -> None:
    for internal, stable in RUNTIME_ERROR_CODES.items():
        assert stable.startswith("minecraft."), internal


def test_context_line_is_compact_and_factual() -> None:
    bridge = make_bridge(FakeMinecraftService())
    bridge.context.apply_event(
        event("minecraft.action.started", action="follow_player", action_id="act_1")
    )
    line = bridge.context_line()
    assert "minecraft:overworld" in line and "空凛" in line
    assert "follow_player" in line
    assert len(line) <= 240
    assert line.endswith("。")


def test_context_line_is_silent_when_minecraft_is_disabled() -> None:
    service = FakeMinecraftService(enabled=False)
    service.config = MinecraftConfig(enabled=False)
    bridge = make_bridge(service)
    assert bridge.context_line() == ""


def test_bridge_snapshot_lists_tools_risks_and_context() -> None:
    bridge = make_bridge(FakeMinecraftService())
    snapshot = bridge.snapshot(
        tools=[
            {"name": name, "risk": risk, "enabled": True, "allowed": True, "reason": ""}
            for name, risk in ACTION_RISK.items()
        ]
    )
    assert snapshot["enabled"] is True
    assert {row["name"] for row in snapshot["tools"]} == set(ACTION_RISK)
    assert snapshot["context"]["dimension"] == "minecraft:overworld"
    assert snapshot["policy"]["risk_flags"]["LOW"] is True
    assert snapshot["policy"]["risk_flags"]["MEDIUM"] is False


def test_busy_gate_uses_the_live_context() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    bridge.context.apply_event(
        event("minecraft.action.started", action="follow_player", action_id="act_1")
    )
    decision = bridge.check(
        "minecraft_move_to", {"x": 1, "y": 2, "z": 3}, context=make_context(bridge)
    )
    assert not decision.allowed and decision.code == "minecraft.action_busy"
    # 停止动作永远放行（用户要打断时必须能打断）
    assert bridge.check("minecraft_stop", {}, context=make_context(bridge)).allowed
    # 跟随结束后又回到空闲
    bridge.context.apply_event(
        event(
            "minecraft.action.cancelled",
            action="follow_player",
            action_id="act_1",
            status="CANCELLED",
        )
    )
    assert bridge.check(
        "minecraft_move_to", {"x": 1, "y": 2, "z": 3}, context=make_context(bridge)
    ).allowed


def test_explicit_intent_flag_comes_from_the_tool_context() -> None:
    bridge = make_bridge(FakeMinecraftService())
    assert isinstance(
        bridge.check("minecraft_move_to", {}, context=make_context(bridge)), PolicyDecision
    )
    assert bridge.check("minecraft_move_to", {}, context=make_context(bridge)).allowed
    # 没有意图标记（后台/自主回合）→ LOW 被拒
    assert not bridge.check(
        "minecraft_move_to", {}, context=make_context(bridge, explicit=False)
    ).allowed


async def test_unknown_service_error_becomes_action_failed() -> None:
    service = FakeMinecraftService()
    service.enqueue("chat", MinecraftBridgeError("桥接不可达", code="minecraft.runtime_down"))
    bridge = make_bridge(service)
    result = await MinecraftChatTool().execute({"message": "hi"}, make_context(bridge))
    assert result.success is False
    # 已经是 minecraft.* 的稳定码：原样透出，不再二次翻译
    assert result.error_type == "minecraft.runtime_down"


async def test_minecraft_player_not_found_from_service_is_stable() -> None:
    service = FakeMinecraftService()
    service.enqueue("follow_player", MinecraftPlayerNotFound("找不到玩家 空凛"))
    bridge = make_bridge(service)
    result = await MinecraftFollowPlayerTool().execute({"username": "空凛"}, make_context(bridge))
    assert result.error_type == "minecraft.player_not_found"


def test_tool_layer_carries_no_secrets() -> None:
    """§四十七：日志与结果里不得出现 token / 密码 / 密钥。"""
    import inspect

    from app.tools.builtins import minecraft_actions

    source = inspect.getsource(minecraft_actions)
    for forbidden in ("api_key", "password", "Bearer", "callback_token"):
        assert forbidden not in source
