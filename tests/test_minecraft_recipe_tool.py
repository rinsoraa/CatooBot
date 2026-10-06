"""minecraft_recipe_lookup 工具与 Policy 门测试（Phase 4F §四-§十）。

覆盖：schema、canonical item、offline、SAFE 不需要确认（任何回合都能查）、
recipe_not_found / multiple recipes / available / insufficient_material /
crafting_table_required 四态、语义投影（不泄露 raw Recipe）、稳定 recipe_id、
非独占（前台动作跑着也能查）、以及服务层错误 → 结构化。

真正的配方表（1.21.1 minecraft-data）与"材料够不够"的判定由
``minecraft_runtime/test/recipe.test.js`` 覆盖；这里测 Tool → Policy → Service。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.service import (
    MinecraftActionInvalid,
    MinecraftRecipeNotFound,
    MinecraftService,
)
from app.tools.builtins import MinecraftRecipeLookupTool
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_confirm_gate import Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService

STICK_ARGS = {"item": "stick"}


async def call(gate: Gate, arguments: dict[str, Any], context: Any = None) -> Any:
    return await gate.call("minecraft_recipe_lookup", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_recipe_lookup")
        assert tool is not None and isinstance(tool, MinecraftRecipeLookupTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"item"}
        assert schema["required"] == ["item"]
        assert schema["additionalProperties"] is False
        # §十一：recipe_lookup 只认物品名，不带槽位/数量/工作台参数
        assert "count" not in schema["properties"]
        assert "crafting_table" not in schema["properties"]
        assert "recipe_id" not in schema["properties"]
    finally:
        await runtime.close()


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        # 只列 schema 层能判定的（空白名由 Service/runtime 判定；见纯函数测试）
        # 只列 schema 层能判定的（空白/超长物品名由 Service/runtime 判定；见纯函数测试）
        for arguments in ({}, {"item": ""}, {"item": 7}, {"item": "a", "extra": 1}):
            result = await call(gate, arguments)
            assert result.success is False
            assert result.error_type in {"invalid_arguments", "minecraft.action_invalid"}, arguments
        assert gate.service.action_calls("recipe_lookup") == []
        assert gate.bridge.confirmations.pending() == []


async def test_canonical_item_name_reaches_the_service() -> None:
    async with Gate() as gate:
        result = await call(gate, {"item": " minecraft:stick "})
        assert result.success is True, result.error
        # FakeService 不做归一化（真实 Service/runtime 才做）→ 记录原始参数
        assert gate.service.action_calls("recipe_lookup") == [{"item": " minecraft:stick "}]
        # runtime 侧归一化后，结果里的物品名是 canonical 的
        assert result.data["result"]["item"] == "stick"


# ------------------------------------------------------------ 未启用/离线


async def test_disabled_minecraft_and_tools_off() -> None:
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge

    def context_for(bridge: Any) -> Any:
        from app.tools.models import ToolContext

        return ToolContext(
            user_id="10001",
            session_id="private:10001",
            metadata={BRIDGE_KEY: bridge, INTENT_KEY: True},
        )

    service = FakeMinecraftService(enabled=False)
    result = await MinecraftRecipeLookupTool().execute(
        STICK_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftRecipeLookupTool().execute(
        STICK_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    """§十：SAFE 也要在线（配方是"当前背包能做什么"，离线无从谈起）。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, STICK_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.service.action_calls("recipe_lookup") == []


# ------------------------------------------------- SAFE 语义 / 非独占


async def test_safe_lookup_needs_no_confirmation_in_any_turn() -> None:
    """SAFE：不需要用户意图、不需要可信玩家、不产生确认；任何回合都能查。"""
    async with Gate() as gate:
        for origin in ("user", "initiative", "background", "system"):
            result = await call(gate, STICK_ARGS, gate.context(origin=origin))
            assert result.success is True, (origin, result.error)
        assert len(gate.service.action_calls("recipe_lookup")) == 4
        assert gate.bridge.confirmations.pending() == []


async def test_lookup_is_non_exclusive() -> None:
    """§十：纯读取 → 前台动作跑着的时候也能查（NON_EXCLUSIVE_TOOLS）。"""
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
        result = await call(gate, STICK_ARGS)
        assert result.success is True, result.error
        assert gate.service.action_calls("recipe_lookup") == [{"item": "stick"}]

        # 反过来：craft 是独占的，忙的时候会被拒
        blocked = await gate.call(
            "minecraft_craft", {"recipe_id": "stick*4=oak_planks*2"}, gate.context()
        )
        assert blocked.error_type == "minecraft.action_busy"


# ------------------------------------------------------- §九 四种状态


async def test_available_recipe_projection() -> None:
    async with Gate() as gate:
        result = await call(gate, STICK_ARGS)
        assert result.success is True, result.error
        payload = result.data["result"]
        assert payload["status"] == "available" and payload["total"] == 2
        first = payload["recipes"][0]
        assert first == {
            "recipe_id": "stick*4=oak_planks*2",
            "result": {"name": "stick", "count_per_craft": 4},
            "requires_table": False,
            "available": True,
            "ingredients": [{"name": "oak_planks", "count": 2}],
        }, f"嵌套结构必须原样保留（ToolResultProcessor 曾把它打成 None）：{first}"
        # 第二条材料不够，也如实列出来（让模型看到"差哪个"）
        assert payload["recipes"][1]["available"] is False
        # 摘要说清"用多少材料做几个" + recipe_id
        assert "oak_planks×2" in result.summary
        assert "stick*4=oak_planks*2" in result.summary or "recipe_id" in result.summary


async def test_recipe_not_leaked_raw() -> None:
    """§五：不返回 raw Recipe / 数字 id / metadata / 内部结构。"""
    async with Gate() as gate:
        result = await call(gate, STICK_ARGS)
        raw = str(result.data)
        for forbidden in ("inShape", "outShape", "delta", "requiresTable", "metadata", "prototype"):
            assert forbidden not in raw, forbidden
        assert set(result.data["result"]["recipes"][0]) == {
            "recipe_id",
            "result",
            "requires_table",
            "available",
            "ingredients",
        }


async def test_recipe_not_found_status() -> None:
    async with Gate() as gate:
        gate.service.enqueue(
            "recipe_lookup",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "item": "unobtainium",
                    "status": "recipe_not_found",
                    "total": 0,
                    "recipes": [],
                },
            },
        )
        result = await call(gate, {"item": "unobtainium"})
        assert result.success is True, "查不到配方是「查清了」而不是工具失败"
        assert result.data["result"]["status"] == "recipe_not_found"
        assert "查不到" in result.summary


async def test_insufficient_material_status_and_summary() -> None:
    async with Gate() as gate:
        gate.service.enqueue(
            "recipe_lookup",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "item": "stick",
                    "status": "insufficient_material",
                    "total": 12,
                    "recipes": [
                        {
                            "recipe_id": "stick*4=oak_planks*2",
                            "result": {"name": "stick", "count_per_craft": 4},
                            "requires_table": False,
                            "available": False,
                            "ingredients": [{"name": "oak_planks", "count": 2}],
                        }
                    ],
                },
            },
        )
        result = await call(gate, STICK_ARGS)
        assert result.data["result"]["status"] == "insufficient_material"
        assert "材料都不够" in result.summary


async def test_crafting_table_required_status() -> None:
    """§九/§三十一：只有工作台配方 → 如实回报，绝不自动去找工作台。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "recipe_lookup",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "item": "chest",
                    "status": "crafting_table_required",
                    "total": 0,
                    "recipes": [],
                },
            },
        )
        result = await call(gate, {"item": "chest"})
        assert result.success is True
        assert result.data["result"]["status"] == "crafting_table_required"
        assert "工作台" in result.summary
        assert gate.service.action_calls("recipe_lookup") == [{"item": "chest"}]
        # 只查了一次；没有任何"去找工作台"的动作
        assert len(gate.service.calls) == 1


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured() -> None:
    cases = [
        (MinecraftRecipeNotFound("这个版本没有它的配方"), "minecraft.recipe_not_found"),
        (MinecraftActionInvalid("item 不能包含控制字符"), "minecraft.action_invalid"),
    ]
    for exc, expected_code in cases:
        async with Gate() as gate:
            gate.service.enqueue("recipe_lookup", exc)
            result = await call(gate, STICK_ARGS)
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            assert "Traceback" not in str(result.data)


def test_validate_recipe_lookup_pure_function() -> None:
    assert MinecraftService.validate_recipe_lookup(" minecraft:stick ") == "minecraft:stick"
    for bad in ("", "   ", 7, None, "x" * 80, "bad\x01name"):
        try:
            MinecraftService.validate_recipe_lookup(bad)
        except MinecraftActionInvalid:
            pass
        else:  # pragma: no cover - 非法输入必须被拒
            raise AssertionError(f"应当拒绝：{bad!r}")


async def test_bridge_is_the_only_way_to_the_service() -> None:
    import inspect

    from app.tools.builtins import minecraft_craft, minecraft_recipe

    for module in (minecraft_recipe, minecraft_craft):
        source = inspect.getsource(module)
        for forbidden in ("runtime_client", "mineflayer", "prismarine", "aiohttp", "127.0.0.1"):
            assert forbidden not in source, f"{module.__name__} 出现了 {forbidden}"
