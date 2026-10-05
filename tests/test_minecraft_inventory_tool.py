"""minecraft_inventory 只读工具测试（Phase 4C §二十七）。

覆盖：schema、离线、disabled、策略门、结构化切片（不外泄 slot/NBT/window）、
摘要可读性、非独占（移动中也能读）。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import (
    BRIDGE_KEY,
    INTENT_KEY,
    MinecraftAgentBridge,
)
from app.integrations.minecraft.service import MinecraftActionBusy, MinecraftRuntimeDown
from app.tools.builtins import MinecraftInventoryTool
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_tools import FakeMinecraftService


def make_bridge(service: FakeMinecraftService, **agent: Any) -> MinecraftAgentBridge:
    service.config = MinecraftConfig(
        enabled=True,
        auto_start_runtime=False,
        agent={"tools": {"enabled": True}, **agent},
    )
    return MinecraftAgentBridge(service)


def make_context(bridge: MinecraftAgentBridge | None, *, origin: str = "user") -> ToolContext:
    from app.character.turn import TurnOrigin

    turn = TurnOrigin(origin)
    metadata: dict[str, Any] = {}
    if bridge is not None:
        metadata = {BRIDGE_KEY: bridge, INTENT_KEY: turn.is_user}
    return ToolContext(user_id="10001", session_id="private:10001", metadata=metadata)


async def run_tool(bridge: MinecraftAgentBridge, arguments: dict[str, Any] | None = None) -> Any:
    return await MinecraftInventoryTool().execute(arguments or {}, make_context(bridge))


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_empty_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    tool = runtime.registry.maybe_get("minecraft_inventory")
    assert tool is not None and isinstance(tool, MinecraftInventoryTool)
    assert runtime.registry.is_enabled("minecraft_inventory")
    assert tool.metadata.input_schema["properties"] == {}
    assert tool.metadata.input_schema["additionalProperties"] is False
    assert tool.metadata.risk_level == "low"  # 通用词表；Minecraft 分级是 SAFE
    assert tool.metadata.description and tool.metadata.when_to_use
    await runtime.close()


async def test_inventory_is_safe_risk() -> None:
    from app.integrations.minecraft.agent import ACTION_RISK, CONFIRMATION_RISKS

    assert ACTION_RISK["minecraft_inventory"] == "SAFE"
    assert "SAFE" not in CONFIRMATION_RISKS  # SAFE 不需要确认


# --------------------------------------------------------------- 结构化返回


async def test_reads_the_inventory_slice() -> None:
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    assert result.success is True
    data = result.data
    assert data["ok"] is True and data["online"] is True
    assert data["selected_hotbar_slot"] == 0
    assert data["held_item"] == {"name": "dirt", "count": 12}
    assert data["items"] == [{"name": "dirt", "count": 12}, {"name": "sand", "count": 24}]
    # 摘要可读（人和模型都能看懂）
    assert "dirt" in result.summary and "sand" in result.summary
    # 只读：不产生任何动作调用
    assert [name for name, _ in service.calls] == ["inventory"]


async def test_slice_never_leaks_raw_inventory_fields() -> None:
    service = FakeMinecraftService()
    service.enqueue(
        "inventory",
        {
            "ok": True,
            "online": True,
            "selected_hotbar_slot": 3,
            "held_item": {"name": "stone", "count": 5},
            "items": [{"name": "stone", "count": 5}],
            # 假 Runtime 就算多给字段，工具也只投影约定的这几样
            "slots": [{"slot": 36, "nbt": {"x": 1}}],
            "window": {"id": 0},
        },
    )
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    dumped = str(result.data)
    for forbidden in ("slots", "nbt", "window"):
        assert forbidden not in dumped, forbidden
    assert set(result.data) == {"ok", "online", "selected_hotbar_slot", "held_item", "items"}


async def test_empty_hand_and_empty_inventory_are_reported_honestly() -> None:
    service = FakeMinecraftService()
    service.enqueue(
        "inventory",
        {"ok": True, "online": True, "selected_hotbar_slot": 1, "held_item": None, "items": []},
    )
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    assert result.success is True
    assert result.data["held_item"] is None
    assert "空着" in result.summary and "空的" in result.summary


# ------------------------------------------------------------ 离线 / 未启用


async def test_offline_reports_not_in_world() -> None:
    service = FakeMinecraftService(online=False)
    service.enqueue("inventory", {"ok": True, "online": False, "items": []})
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    assert result.success is False
    assert result.error_type == "minecraft.offline"


async def test_disabled_minecraft_is_rejected() -> None:
    service = FakeMinecraftService(enabled=False)
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    assert result.success is False and result.error_type == "minecraft.disabled"


async def test_tools_switch_off_is_rejected() -> None:
    service = FakeMinecraftService()
    service.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    bridge = MinecraftAgentBridge(service)
    result = await run_tool(bridge)
    assert result.success is False and result.error_type == "minecraft.disabled"


async def test_no_bridge_fails_closed() -> None:
    result = await run_tool(None)  # type: ignore[arg-type]
    assert result.success is False and result.error_type == "minecraft.disabled"


# ------------------------------------------------------------------ 策略门


async def test_inventory_needs_no_explicit_intent() -> None:
    """SAFE：后台/主动回合也能读背包（不需要用户明确要求）。"""
    service = FakeMinecraftService()
    bridge = make_bridge(service)
    for origin in ("initiative", "background", "system"):
        result = await MinecraftInventoryTool().execute({}, make_context(bridge, origin=origin))
        assert result.success is True, origin


async def test_inventory_is_available_while_something_is_running() -> None:
    """非独占：前台动作（比如 move_to）跑着的时候也能看背包。"""
    from app.integrations.minecraft.events import parse_bridge_event

    service = FakeMinecraftService()
    bridge = make_bridge(service)
    bridge.context.apply_event(
        parse_bridge_event(
            {
                "event": "minecraft.action.started",
                "session_id": "s1",
                "timestamp": 1.0,
                "action": "move_to",
                "action_id": "act_moving",
            }
        )
    )
    result = await run_tool(bridge)
    assert result.success is True


async def test_service_errors_are_structured() -> None:
    service = FakeMinecraftService()
    service.enqueue("inventory", MinecraftRuntimeDown("runtime 不可达"))
    bridge = make_bridge(service)
    result = await run_tool(bridge)
    assert result.success is False
    assert result.error_type == "minecraft.runtime_down"
    assert "Traceback" not in str(result.data)
    assert MinecraftActionBusy is not None  # 是稳定错误族的一员（保持导入一致）


async def test_inventory_does_not_block_dig_in_the_executor() -> None:
    """走真实 executor：inventory 与 dig 的调用互不干扰（一个只读、一个 MEDIUM）。"""
    from tests.test_minecraft_agent_confirm_gate import DIG_ARGS, Gate

    async with Gate() as gate:
        runtime = ToolRuntime(ToolsConfig(enabled=True))
        await runtime.start()
        executor = ToolExecutor(ToolsConfig(enabled=True), runtime.registry, runtime.policy)
        context = gate.context()
        read = await executor.execute(
            ToolCall(name="minecraft_inventory", arguments={}), context, TurnBudget()
        )
        assert read.success is True
        first = await executor.execute(
            ToolCall(name="minecraft_dig", arguments=DIG_ARGS), context, TurnBudget()
        )
        assert first.error_type == "minecraft.confirmation_required"
        again = await executor.execute(
            ToolCall(name="minecraft_inventory", arguments={}), context, TurnBudget()
        )
        assert again.success is True
        await runtime.close()
