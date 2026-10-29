"""minecraft_container_inspect 工具与 Policy 门测试（Phase 4E §四-§十五）。

覆盖：schema（三个整数坐标、没有别的参数）、非法参数在 schema 层就被拒、offline、
disabled、运行时错误 → 结构化（unsupported / too_far / open_failed / block_unavailable /
close_failed 带快照）、读成功（内容进 result + 摘要说清里面有什么）、close 成功、独占、
以及「SAFE 但仍要在任何回合都能用」的语义。

真正的窗口生命周期（open → read → close / 超时 / 取消 / 断开）由
``minecraft_runtime/test/container.test.js`` 用假窗口覆盖；这里只测 Tool → Policy → Service。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import MinecraftConfig, ToolsConfig
from app.integrations.minecraft.service import (
    MinecraftActionInvalid,
    MinecraftContainerCloseFailed,
    MinecraftContainerOpenFailed,
    MinecraftContainerTooFar,
    MinecraftContainerUnsupported,
    MinecraftService,
)
from app.tools.builtins import MinecraftContainerInspectTool
from app.tools.models import ToolContext, ToolResult
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_confirm_gate import Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService

#: 一个明确的目标容器（整数坐标）
CHEST_ARGS = {"x": 100, "y": 64, "z": 100}


async def call(gate: Gate, arguments: dict[str, Any], context: ToolContext | None = None) -> Any:
    return await gate.call("minecraft_container_inspect", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_container_inspect")
        assert tool is not None and isinstance(tool, MinecraftContainerInspectTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"x", "y", "z"}
        assert schema["required"] == ["x", "y", "z"]
        assert schema["additionalProperties"] is False
        assert all(schema["properties"][axis]["type"] == "integer" for axis in ("x", "y", "z"))
        # §三：绝不暴露 open/close 这类长期状态型参数
        assert "direction" not in schema["properties"]
        assert "slot" not in schema["properties"]
        assert "window" not in schema["properties"]
    finally:
        await runtime.close()


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        bad_cases = [
            ({}, "invalid_arguments"),
            ({"x": 1, "y": 64}, "invalid_arguments"),
            ({"x": 1.5, "y": 64, "z": 0}, "invalid_arguments"),
            ({"x": 1, "y": 64, "z": "0"}, "invalid_arguments"),
            ({"x": "1", "y": 64, "z": 0}, "invalid_arguments"),
            ({**CHEST_ARGS, "direction": "withdraw"}, "invalid_arguments"),
        ]
        for arguments, expected in bad_cases:
            result = await call(gate, arguments)
            assert result.success is False and result.error_type == expected, arguments
        assert gate.service.action_calls("container_inspect") == [], "schema 层就拒了"


# ------------------------------------------------------------ 未启用/离线


async def test_disabled_minecraft_and_tools_off() -> None:
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge

    def context_for(bridge: Any) -> ToolContext:
        return ToolContext(
            user_id="10001",
            session_id="private:10001",
            metadata={BRIDGE_KEY: bridge, INTENT_KEY: True},
        )

    service = FakeMinecraftService(enabled=False)
    result = await MinecraftContainerInspectTool().execute(
        CHEST_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftContainerInspectTool().execute(
        CHEST_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    """§四：SAFE 也要在线（打开真实窗口没有「离线可读」这种语义）。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, CHEST_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.service.action_calls("container_inspect") == []


# ------------------------------------------------------- SAFE 但独占语义


async def test_safe_inspection_works_in_any_turn_but_is_exclusive() -> None:
    """SAFE = 不需要用户意图；但打开窗口仍然独占（忙的时候会被拒）。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        for origin in ("user", "initiative", "background", "system"):
            result = await call(gate, CHEST_ARGS, gate.context(origin=origin))
            assert result.success is True, (origin, result.error)
        assert len(gate.service.action_calls("container_inspect")) == 4

        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.started",
                    "session_id": "s1",
                    "timestamp": 1.0,
                    "action": "dig",
                    "action_id": "act_digging",
                }
            )
        )
        blocked = await call(gate, CHEST_ARGS, gate.context())
        assert blocked.error_type == "minecraft.action_busy"
        assert len(gate.service.action_calls("container_inspect")) == 4


# ----------------------------------------------------------- 读成功与摘要


async def test_read_success_returns_container_snapshot() -> None:
    async with Gate() as gate:
        result = await call(gate, CHEST_ARGS)
        assert result.success is True, result.error
        assert gate.service.action_calls("container_inspect") == [{"x": 100, "y": 64, "z": 100}]
        snapshot = result.data["result"]
        assert snapshot["container"]["type"] == "minecraft:chest"
        assert snapshot["container"]["size"] == 27
        assert snapshot["container"]["position"] == {"x": 100, "y": 64, "z": 100}
        assert snapshot["slots"] == [
            {"slot": 0, "name": "dirt", "count": 12},
            {"slot": 7, "name": "sand", "count": 32},
        ]
        # 摘要要能让人（和模型）一眼看懂"第几格有什么"
        assert "Chest" in result.summary and "dirt×12" in result.summary
        assert "第 0 格" in result.summary and "第 7 格" in result.summary
        # 只读：绝不产生待确认
        assert gate.bridge.confirmations.pending() == []


async def test_empty_container_summary_says_it_is_empty() -> None:
    async with Gate() as gate:
        gate.service.enqueue(
            "container_inspect",
            {
                "status": "SUCCEEDED",
                "result": {
                    "ok": True,
                    "container": {
                        "type": "minecraft:barrel",
                        "label": "Barrel",
                        "position": {"x": 1, "y": 2, "z": 3},
                        "size": 27,
                    },
                    "slots": [],
                },
            },
        )
        result = await call(gate, {"x": 1, "y": 2, "z": 3})
        assert result.success is True
        assert "Barrel" in result.summary and "空" in result.summary


# --------------------------------------------------- 服务层错误 → 结构化


async def test_runtime_errors_are_structured_with_detail() -> None:
    cases = [
        (
            MinecraftContainerUnsupported("这是 furnace", block="minecraft:furnace"),
            "minecraft.container_unsupported",
            {"block": "minecraft:furnace"},
        ),
        (
            MinecraftContainerTooFar("太远", distance=8.5),
            "minecraft.container_too_far",
            {"distance": 8.5},
        ),
        (MinecraftContainerOpenFailed("服务器没给窗口"), "minecraft.container_open_failed", None),
        (
            MinecraftContainerCloseFailed("关不上", detail={"snapshot": {"slots": []}}),
            "minecraft.container_close_failed",
            {"snapshot": {"slots": []}},
        ),
    ]
    for exc, expected_code, expected_detail in cases:
        async with Gate() as gate:
            gate.service.enqueue("container_inspect", exc)
            result = await call(gate, CHEST_ARGS)
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_trapped_chest_and_double_chest_are_rejected_not_guessed() -> None:
    """§七/§十：trapped chest 与双箱都由 runtime 明确拒绝（tool 层不猜、不特殊绕过）。"""
    for message in (
        "这个位置不是箱子或桶（minecraft:trapped_chest）——本阶段只支持单方块 Chest / Barrel",
        "只支持单方块容器（容器槽位 54，玩家背包 36 格）",
    ):
        async with Gate() as gate:
            gate.service.enqueue("container_inspect", MinecraftContainerUnsupported(message))
            result = await call(gate, CHEST_ARGS)
            assert result.error_type == "minecraft.container_unsupported"
            assert message in (result.error or "")


# --------------------------------------------------- 参数校验纯函数（Service）


def test_validate_container_inspect_pure_function() -> None:
    assert MinecraftService.validate_container_inspect(1, 64, -3) == {"x": 1, "y": 64, "z": -3}
    for bad in ((1.5, 64, 0), (1, "64", 0), (True, 64, 0), (1, 9999, 0), (4.0e7, 64, 0)):
        try:
            MinecraftService.validate_container_inspect(*bad)
        except MinecraftActionInvalid as exc:
            assert "坐标" in str(exc)
        else:  # pragma: no cover - 非法输入必须被拒
            raise AssertionError(f"应当拒绝：{bad}")


async def test_bridge_is_the_only_way_to_the_service() -> None:
    """工具层不允许直接碰 runtime/HTTP。"""
    import inspect

    from app.tools.builtins import minecraft_container, minecraft_container_transfer

    for module in (minecraft_container, minecraft_container_transfer):
        source = inspect.getsource(module)
        for forbidden in ("runtime_client", "mineflayer", "aiohttp", "127.0.0.1"):
            assert forbidden not in source, f"{module.__name__} 出现了 {forbidden}"


def test_tool_result_shape_is_prompt_safe() -> None:
    """工具结果里绝不出现 window/NBT/cursor 之类的原始结构关键词。"""
    result = ToolResult(
        tool_name="minecraft_container_inspect",
        success=True,
        data={"ok": True, "result": {"container": {"type": "minecraft:chest"}, "slots": []}},
    )
    assert "window" not in str(result.data)
