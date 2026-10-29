"""Phase 4J · minecraft_dig_capability 工具测试（A–O）。

覆盖：schema（整数坐标、无 block name、无 expected_tool）、需要在线、参数非法、
没有方块（block.unavailable）、空气（正常数据 + reason=air）、太远（reason=too_far）、
can_dig 真/假、dig_time_ms、主手有/无、不泄露 raw Item、SAFE 且任何回合都能用、
非独占（前台动作跑着也能查）、以及 Service → 结构化错误。

真正的"能不能挖 / 挖多久"来自 mineflayer 运行时（runtime.js 的 digCapabilityView），
由 ``minecraft_runtime/test/dig_capability.test.js`` 与真机 smoke 覆盖；这里测
Tool → Agent Bridge → Policy → Service 这一层。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import ToolsConfig
from app.integrations.minecraft.service import (
    MinecraftActionFailed,
    MinecraftBlockUnavailable,
    MinecraftService,
)
from app.tools.builtins import MinecraftDigCapabilityTool
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_confirm_gate import Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService

ARGS = {"x": 100, "y": 64, "z": 100}


async def call(gate: Gate, arguments: dict[str, Any] | None = None, context: Any = None) -> Any:
    return await gate.call("minecraft_dig_capability", arguments or ARGS, context or gate.context())


def sample(**overrides: Any) -> dict[str, Any]:
    """与 runtime 同形的语义投影（默认：手持石镐、能挖、1250ms）。"""
    view: dict[str, Any] = {
        "ok": True,
        "position": {"x": 100, "y": 64, "z": 100},
        "block": {"name": "iron_ore"},
        "held_item": {"name": "stone_pickaxe", "count": 1},
        "distance": {"goal_near": 1, "raw": 4.28},
        "can_dig": True,
        "dig_time_ms": 1250,
        "reason": None,
    }
    view.update(overrides)
    return view


# ------------------------------------------------------------------ A：schema


async def test_schema_is_three_integer_coords_only() -> None:
    """A：只有 x/y/z 三个整数参数 —— 既不收 block name，也不收 expected_tool。"""
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_dig_capability")
        assert tool is not None and isinstance(tool, MinecraftDigCapabilityTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"x", "y", "z"}
        assert schema["required"] == ["x", "y", "z"]
        assert schema["additionalProperties"] is False
        for name in ("x", "y", "z"):
            assert schema["properties"][name]["type"] == "integer"
        # §四/§五：不接受 block name，也不接受 expected_tool
        assert "block" not in schema["properties"]
        assert "expected_tool" not in schema["properties"]
        assert "expected_block" not in schema["properties"]
    finally:
        await runtime.close()


async def test_non_integer_or_extra_arguments_are_rejected() -> None:
    """A/C：浮点坐标 / 字符串坐标 / 多传参数 → invalid_arguments（不落到 Service）。"""
    async with Gate() as gate:
        for bad in (
            {"x": 100.5, "y": 64, "z": 100},
            {"x": "100", "y": 64, "z": 100},
            {"x": 100, "y": 64},
            {**ARGS, "expected_tool": "minecraft:stone_pickaxe"},
            {**ARGS, "block": "minecraft:stone"},
        ):
            result = await call(gate, bad)
            assert result.success is False, bad
            assert result.error_type == "invalid_arguments", (bad, result.error_type)
        assert gate.service.action_calls("dig_capability") == []


# ------------------------------------------------------------------ B：离线 / 关闭


async def test_offline_is_rejected() -> None:
    """B：不在世界里 → minecraft.offline，且不查任何东西。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate)
        assert result.error_type == "minecraft.offline"
        assert gate.service.action_calls("dig_capability") == []


async def test_disabled_minecraft_is_reported() -> None:
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge
    from app.tools.models import ToolContext

    service = FakeMinecraftService(enabled=False)
    context = ToolContext(
        user_id="10001",
        session_id="private:10001",
        metadata={BRIDGE_KEY: MinecraftAgentBridge(service), INTENT_KEY: True},
    )
    result = await MinecraftDigCapabilityTool().execute(ARGS, context)
    assert result.error_type == "minecraft.disabled"


# ------------------------------------------------------------------ D/E/F：事实透传


async def test_block_unavailable_is_a_structured_error() -> None:
    """D：那个位置没有方块 → minecraft.block_unavailable（不是 can_dig=false）。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig_capability", MinecraftBlockUnavailable("那个位置没有方块（100,64,100）")
        )
        result = await call(gate)
        assert result.success is False
        assert result.error_type == "minecraft.block_unavailable"


async def test_air_is_normal_data_not_an_error() -> None:
    """E：空气是正常数据（§八）——reason=air、can_dig=false、不是异常。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig_capability",
            {
                "status": "SUCCEEDED",
                "result": sample(
                    block={"name": "air"}, can_dig=False, dig_time_ms=None, reason="air"
                ),
            },
        )
        result = await call(gate)
        assert result.success is True
        view = result.data["result"]
        assert view["reason"] == "air" and view["can_dig"] is False


async def test_too_far_is_reported_with_both_distances() -> None:
    """F：太远 → reason=too_far；两种距离口径都如实透传。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig_capability",
            {
                "status": "SUCCEEDED",
                "result": sample(
                    can_dig=False,
                    dig_time_ms=None,
                    reason="too_far",
                    distance={"goal_near": 12, "raw": 13.4},
                ),
            },
        )
        result = await call(gate)
        view = result.data["result"]
        assert view["reason"] == "too_far"
        assert view["distance"]["goal_near"] == 12 and view["distance"]["raw"] == 13.4


async def test_can_dig_true_carries_a_finite_dig_time() -> None:
    """G/K：能挖时 dig_time_ms 是有限整数。"""
    async with Gate() as gate:
        result = await call(gate)
        view = result.data["result"]
        assert view["can_dig"] is True
        assert isinstance(view["dig_time_ms"], int) and view["dig_time_ms"] >= 0


async def test_can_dig_false_has_no_dig_time() -> None:
    """H：挖不动 → can_dig=false / reason=not_diggable / dig_time_ms=null。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig_capability",
            {
                "status": "SUCCEEDED",
                "result": sample(can_dig=False, dig_time_ms=None, reason="not_diggable"),
            },
        )
        result = await call(gate)
        view = result.data["result"]
        assert view["can_dig"] is False
        assert view["reason"] == "not_diggable"
        assert view["dig_time_ms"] is None


# ------------------------------------------------------------------ I/J/L：主手与投影


async def test_held_item_is_a_semantic_projection() -> None:
    """I/L：主手只给 name/count —— 不泄露 raw Item / NBT / components / 内部 id。"""
    async with Gate() as gate:
        result = await call(gate)
        held = result.data["result"]["held_item"]
        assert held == {"name": "stone_pickaxe", "count": 1}
        assert set(held) == {"name", "count"}


async def test_empty_hand_is_null() -> None:
    """J：空手 → held_item = null（照样回答能不能挖）。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig_capability",
            {
                "status": "SUCCEEDED",
                "result": sample(
                    held_item=None, can_dig=False, dig_time_ms=None, reason="not_diggable"
                ),
            },
        )
        result = await call(gate)
        assert result.data["result"]["held_item"] is None


async def test_no_recommendation_fields() -> None:
    """§十七：绝不返回推荐工具（recommended_tool / best_tool）。"""
    async with Gate() as gate:
        result = await call(gate)
        view = result.data["result"]
        assert "recommended_tool" not in view
        assert "best_tool" not in view


# ------------------------------------------------------------------ M/N/O：SAFE / 非独占


async def test_safe_needs_no_confirmation_in_any_turn() -> None:
    """M：SAFE —— 任何回合都能查，且不产生任何确认。"""
    async with Gate() as gate:
        for origin in ("user", "initiative", "background", "system"):
            result = await call(gate, context=gate.context(origin=origin))
            assert result.success is True, (origin, result.error)
        assert len(gate.service.action_calls("dig_capability")) == 4
        assert gate.bridge.confirmations.pending() == []


async def test_non_exclusive_while_a_foreground_action_runs() -> None:
    """N/O：前台动作（move_to）正在跑时，只读查询照样能执行。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.started",
                    "session_id": "private:10001",
                    "timestamp": 1.0,
                    "action": "move_to",
                    "action_id": "act_moving",
                }
            )
        )
        result = await call(gate)
        assert result.success is True, result.error
        assert len(gate.service.action_calls("dig_capability")) == 1


async def test_summary_is_readable() -> None:
    """给模型看的摘要要说清"能不能挖 + 多久"。"""
    async with Gate() as gate:
        result = await call(gate)
        assert "iron_ore" in result.summary
        assert "1250" in result.summary or "1.2" in result.summary or "能挖" in result.summary


# ------------------------------------------------------------------ Service 层


def test_service_validator_rejects_non_integers() -> None:
    from app.integrations.minecraft.service import MinecraftActionInvalid

    for bad in ((100.5, 64, 100), ("100", 64, 100), (100, 64, None), (4.0e7, 64, 0)):
        try:
            MinecraftService.validate_dig_capability(*bad)
        except MinecraftActionInvalid:
            continue
        raise AssertionError(f"{bad} 应该被拒绝")
    assert MinecraftService.validate_dig_capability(100, 64, 100) == {"x": 100, "y": 64, "z": 100}


async def test_service_layer_failure_is_translated() -> None:
    """J/错误映射：Service 抛出的稳定错误码原样交给模型。"""
    service = FakeMinecraftService()
    service.enqueue("dig_capability", MinecraftActionFailed("runtime 侧失败"))
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge
    from app.tools.models import ToolContext

    bridge = MinecraftAgentBridge(service)
    context = ToolContext(
        user_id="10001",
        session_id="private:10001",
        metadata={BRIDGE_KEY: bridge, INTENT_KEY: True},
    )
    result = await MinecraftDigCapabilityTool().execute(ARGS, context)
    assert result.success is False
    assert result.error_type == "minecraft.action_failed"
