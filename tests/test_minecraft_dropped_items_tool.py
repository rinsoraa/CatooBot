"""minecraft_dropped_items 工具测试（Phase 4H §三十九）。

覆盖：schema（无参数）、SAFE（任何回合都能用 + 不产生确认）、需要在线、
非独占（前台动作跑着也能读）、空列表、单个/多个、距离与排序（语义投影透传）、
max_items/truncated、不泄露 raw entity 字段、玩家/怪物不进入（服务侧投影）、
以及服务层错误 → 结构化。

真正的实体识别/过滤/排序/上限/生命周期由 ``minecraft_runtime/test/dropped_items.test.js``
用真实物品表 + 真形状的 entity metadata 覆盖；这里测 Tool → Policy → Service。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.service import MinecraftActionFailed, MinecraftService
from app.tools.builtins import MinecraftDroppedItemsTool
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_confirm_gate import Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService


async def call(gate: Gate, context: Any = None) -> Any:
    return await gate.call("minecraft_dropped_items", {}, context or gate.context())


def sample(single: bool = True) -> dict[str, Any]:
    items = [
        {
            "entity_id": 123,
            "item": {"name": "dirt", "count": 3},
            "position": {"x": 100.35, "y": 64.12, "z": 101.84},
            "distance": 3.7,
        }
    ]
    if not single:
        items.append(
            {
                "entity_id": 130,
                "item": {"name": "oak_log", "count": 1},
                "position": {"x": 106.02, "y": 64.0, "z": 99.5},
                "distance": 6.1,
            }
        )
    return {"ok": True, "online": True, "total": len(items), "truncated": False, "items": items}


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_empty_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_dropped_items")
        assert tool is not None and isinstance(tool, MinecraftDroppedItemsTool)
        schema = tool.metadata.input_schema
        assert schema["properties"] == {}
        assert schema["additionalProperties"] is False
        # §六：绝不暴露"要读哪个实体/哪一类实体"这种参数（只读附近掉落物）
        assert schema.get("required", []) == []
    finally:
        await runtime.close()


async def test_extra_arguments_are_rejected() -> None:
    async with Gate() as gate:
        result = await gate.call("minecraft_dropped_items", {"entity_id": 1}, gate.context())
        assert result.success is False and result.error_type == "invalid_arguments"
        assert gate.service.action_calls("dropped_items") == []


# ------------------------------------------------- SAFE 语义 / 非独占 / 在线


async def test_safe_read_needs_no_confirmation_in_any_turn() -> None:
    """§三：SAFE —— 任何回合都能看地上的东西，也不产生任何确认。"""
    async with Gate() as gate:
        for origin in ("user", "initiative", "background", "system"):
            result = await call(gate, gate.context(origin=origin))
            assert result.success is True, (origin, result.error)
        assert len(gate.service.action_calls("dropped_items")) == 4
        assert gate.bridge.confirmations.pending() == []


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate)
        assert result.error_type == "minecraft.offline"
        assert gate.service.action_calls("dropped_items") == []


async def test_disabled_minecraft_and_tools_off() -> None:
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge
    from app.tools.models import ToolContext

    def context_for(bridge: Any) -> ToolContext:
        return ToolContext(
            user_id="10001",
            session_id="private:10001",
            metadata={BRIDGE_KEY: bridge, INTENT_KEY: True},
        )

    service = FakeMinecraftService(enabled=False)
    result = await MinecraftDroppedItemsTool().execute(
        {}, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftDroppedItemsTool().execute(
        {}, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_lookup_is_non_exclusive() -> None:
    """§八：看地上的掉落物是纯读取 → 前台动作跑着的时候也能读。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
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
        result = await call(gate)
        assert result.success is True, result.error
        assert gate.service.action_calls("dropped_items")

        # 反过来：pickup 是独占的，忙的时候会被拒
        blocked = await gate.call(
            "minecraft_pickup_item", {"entity_id": 123, "expected_item": "dirt"}, gate.context()
        )
        assert blocked.error_type == "minecraft.action_busy"


# --------------------------------------------------- 语义投影 / 边界


async def test_empty_list_is_reported_as_empty() -> None:
    async with Gate() as gate:
        gate.service.enqueue(
            "dropped_items",
            {
                "status": "SUCCEEDED",
                "result": {"ok": True, "online": True, "total": 0, "truncated": False, "items": []},
            },
        )
        result = await call(gate)
        assert result.success is True
        assert result.data["result"]["items"] == []
        assert "没有掉落物" in result.summary


async def test_projection_is_passed_through_untouched() -> None:
    """§六：工具层不改写 runtime 的事实（entity_id/物品/坐标/距离原样给模型）。"""
    async with Gate() as gate:
        payload = sample(single=False)
        gate.service.enqueue("dropped_items", {"status": "SUCCEEDED", "result": payload})
        result = await call(gate)
        assert result.data["result"] == payload
        first = result.data["result"]["items"][0]
        assert first == {
            "entity_id": 123,
            "item": {"name": "dirt", "count": 3},
            "position": {"x": 100.35, "y": 64.12, "z": 101.84},
            "distance": 3.7,
        }
        assert set(first) == {"entity_id", "item", "position", "distance"}
        raw = str(result.data)
        for forbidden in ("metadata", "velocity", "uuid", "itemId", "entityType", "nbt"):
            assert forbidden not in raw, forbidden


async def test_summary_mentions_entity_item_and_distance() -> None:
    async with Gate() as gate:
        gate.service.enqueue("dropped_items", {"status": "SUCCEEDED", "result": sample()})
        result = await call(gate)
        assert "dirt×3" in result.summary
        assert "#123" in result.summary
        assert "3.7" in result.summary


async def test_truncated_is_surfaced_in_the_summary() -> None:
    async with Gate() as gate:
        payload = sample(single=False)
        payload["total"] = 40
        payload["truncated"] = True
        gate.service.enqueue("dropped_items", {"status": "SUCCEEDED", "result": payload})
        result = await call(gate)
        assert result.data["result"]["truncated"] is True
        assert "上限" in result.summary, result.summary


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured() -> None:
    async with Gate() as gate:
        gate.service.enqueue("dropped_items", MinecraftActionFailed("读取实体列表失败"))
        result = await call(gate)
        assert result.success is False
        assert result.error_type == "minecraft.action_failed"
        assert "Traceback" not in str(result.data)


def test_tool_stays_a_thin_bridge() -> None:
    """工具层不碰 transport（只经 bridge → Service）。"""
    import inspect

    from app.tools.builtins import minecraft_dropped_items

    source = inspect.getsource(minecraft_dropped_items)
    for forbidden in ("runtime_client", "mineflayer", "prismarine", "aiohttp", "127.0.0.1"):
        assert forbidden not in source, forbidden
    assert MinecraftService is not None
