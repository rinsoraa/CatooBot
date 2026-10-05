"""minecraft_place 工具与确认链测试（Phase 4C §二十七/§二十八）。

覆盖：schema、非法参数（坐标/face/expected_item）、offline、disabled、explicit intent、
trusted player、allow_medium=false、conformation 全流程（required → consume → 参数变化）、
held_item 缺失/不符、目标被占用、参考方块缺失、距离、以及"一次只放一块"。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import bridge_from
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftBlockPlaceUnconfirmed,
    MinecraftHeldItemChanged,
    MinecraftHeldItemMissing,
    MinecraftReferenceBlockMissing,
    MinecraftTargetOccupied,
)
from app.tools.builtins import MinecraftPlaceTool
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide

#: place 的完整参数：在 (2,64,2) 的 up 面放一块 dirt（参考方块 = (2,63,2)）
PLACE_ARGS = {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"}


def place_context(gate: Gate, *, origin: str = "user", player: str = "") -> ToolContext:
    return gate.context(origin=origin, player=player)


async def call(gate: Gate, arguments: dict[str, Any], context: ToolContext | None = None) -> Any:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        executor = ToolExecutor(ToolsConfig(enabled=True), runtime.registry, runtime.policy)
        return await executor.execute(
            ToolCall(name="minecraft_place", arguments=arguments),
            context or gate.context(),
            TurnBudget(),
        )
    finally:
        await runtime.close()


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    tool = runtime.registry.maybe_get("minecraft_place")
    assert tool is not None and isinstance(tool, MinecraftPlaceTool)
    schema = tool.metadata.input_schema
    assert set(schema["properties"]) == {"x", "y", "z", "face", "expected_item"}
    assert schema["required"] == ["x", "y", "z", "face", "expected_item"]
    assert schema["additionalProperties"] is False
    assert schema["properties"]["face"]["enum"] == ["up", "down", "north", "south", "east", "west"]
    assert schema["properties"]["x"]["type"] == "integer"
    await runtime.close()


# --------------------------------------------------------------- 参数校验


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        bad_cases = [
            (
                {"x": 2.5, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"},
                "invalid_arguments",
            ),
            (
                {"x": 2, "y": 64, "z": 2, "face": "north_east", "expected_item": "dirt"},
                "invalid_arguments",
            ),
            ({"x": 2, "y": 64, "z": 2, "face": 1, "expected_item": "dirt"}, "invalid_arguments"),
            ({"x": 2, "y": 64, "z": 2, "face": "up"}, "invalid_arguments"),
            ({"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": ""}, "invalid_arguments"),
            (
                {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt", "extra": 1},
                "invalid_arguments",
            ),
        ]
        for arguments, expected in bad_cases:
            result = await call(gate, arguments)
            assert result.success is False and result.error_type == expected, arguments
        assert gate.service.action_calls("place") == [], "schema 层就拒了，绝不进 service"
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不挂待确认"


# ------------------------------------------------------------ 未启用/离线


async def test_disabled_minecraft_and_tools_off() -> None:
    from app.integrations.minecraft.agent import MinecraftAgentBridge
    from tests.test_minecraft_agent_tools import FakeMinecraftService

    service = FakeMinecraftService(enabled=False)
    bridge = MinecraftAgentBridge(service)
    result = await MinecraftPlaceTool().execute(PLACE_ARGS, Gate_context(bridge))
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    bridge2 = MinecraftAgentBridge(service2)
    result2 = await MinecraftPlaceTool().execute(PLACE_ARGS, Gate_context(bridge2))
    assert result2.error_type == "minecraft.disabled"


def Gate_context(bridge: Any) -> ToolContext:
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY

    return ToolContext(
        user_id="10001",
        session_id="private:10001",
        metadata={BRIDGE_KEY: bridge, INTENT_KEY: True},
    )


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, PLACE_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_place() -> None:
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, PLACE_ARGS, place_context(gate, origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("place") == []


async def test_untrusted_minecraft_player_cannot_place() -> None:
    async with Gate() as gate:
        result = await call(gate, PLACE_ARGS, place_context(gate, player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("place") == []


async def test_risk_flag_gates_place() -> None:
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, PLACE_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, PLACE_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("place") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_place" and pending["risk"] == "MEDIUM"
        # 摘要要让用户知道"放什么、放哪里、哪个面"（§二十二）
        assert "dirt" in pending["summary"] and "up" in pending["summary"]

        second = await call(gate, PLACE_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_place_1"
        assert gate.service.action_calls("place") == [
            {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"}
        ]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, PLACE_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("place")) == 1


async def test_changing_any_place_argument_creates_a_new_confirmation() -> None:
    """§二十八：x / y / z / face / expected_item 任何一个变了 → mismatch + 新确认。

    确认指纹覆盖**全部**参数：用户确认的是"在这个位置、这个面、放这个东西"这一个具体动作。
    """
    variations = (
        ("x", 9),
        ("y", 70),
        ("z", 9),
        ("face", "north"),
        ("expected_item", "sand"),
    )
    async with Gate() as gate:
        context = gate.context()
        executed = 0
        for field, other_value in variations:
            base = dict(PLACE_ARGS)
            pending = await call(gate, base, context)
            assert pending.error_type == CODE_REQUIRED, field
            tampered = {**base, field: other_value}
            before = len(gate.service.action_calls("place"))
            mismatched = await call(gate, tampered, context)
            assert mismatched.error_type == CODE_MISMATCH, (field, mismatched.error_type)
            assert len(gate.service.action_calls("place")) == before, f"{field} 变了就不该执行"
            done = await call(gate, tampered, context)
            assert done.success is True, (field, done.error)
            executed += 1
            assert len(gate.service.action_calls("place")) == executed
        assert executed == len(variations)


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, PLACE_ARGS, gate.context(session="private:10001"))
        crossed = await call(gate, PLACE_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛"))
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("place") == []


async def test_expired_confirmation_cannot_place() -> None:
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await call(gate, PLACE_ARGS, gate.context())
        clock["now"] += 61.0
        after = await call(gate, PLACE_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.service.action_calls("place") == []
        assert len(gate.bridge.confirmations.pending()) == 1, "过期后按当前参数重挂一条"


async def test_place_is_exclusive() -> None:
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
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
        blocked = await call(gate, PLACE_ARGS, gate.context())
        assert blocked.error_type == "minecraft.action_busy"
        # inventory 是非独占只读：忙的时候也能读
        runtime = ToolRuntime(ToolsConfig(enabled=True))
        await runtime.start()
        executor = ToolExecutor(ToolsConfig(enabled=True), runtime.registry, runtime.policy)
        read = await executor.execute(
            ToolCall(name="minecraft_inventory", arguments={}), gate.context(), TurnBudget()
        )
        assert read.success is True
        await runtime.close()


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    async with Gate() as gate:
        cases = [
            (
                MinecraftHeldItemMissing(),
                "minecraft.held_item_missing",
                None,
            ),
            (
                MinecraftHeldItemChanged("主手是沙", expected="dirt", actual="sand"),
                "minecraft.held_item_changed",
                {"expected": "dirt", "actual": "sand"},
            ),
            (
                MinecraftTargetOccupied("已经有方块", actual="stone"),
                "minecraft.target_occupied",
                {"actual": "stone"},
            ),
            (
                MinecraftReferenceBlockMissing(),
                "minecraft.reference_block_missing",
                None,
            ),
            (
                MinecraftBlockPlaceUnconfirmed("没放上", expected="dirt", actual="air"),
                "minecraft.block_place_unconfirmed",
                {"expected": "dirt", "actual": "air"},
            ),
        ]
        context = gate.context()
        for exc, expected_code, expected_detail in cases:
            gate.service.enqueue("place", exc)
            await call(gate, PLACE_ARGS, context)  # 建立确认
            result = await call(gate, PLACE_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_no_chaining_two_blocks_in_one_turn() -> None:
    """一次只能放一块：同一回合里的第二块只会得到新的"需要确认"。"""
    async with Gate() as gate:
        context = gate.context()
        await call(gate, PLACE_ARGS, context)
        await call(gate, PLACE_ARGS, context)
        assert len(gate.service.action_calls("place")) == 1
        second_block = {**PLACE_ARGS, "x": 6, "z": 6}
        chained = await call(gate, second_block, context)
        # 上一块已经消费掉了确认：第二块必须重新走确认（要么 mismatch 要么 required），反正不执行
        assert chained.error_type in (CODE_MISMATCH, CODE_REQUIRED)
        assert len(gate.service.action_calls("place")) == 1, "第二个方块绝不连着放"


# --------------------------------------------------- 整轮（含 LLM 决策）


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_place", **PLACE_ARGS),
                    "把这个方块放上去会真的改变世界，需要你确认一下。",
                    decide("minecraft_place", **PLACE_ARGS),
                    "好，我去放。",
                ]
            }
        )
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            providers={"mock": provider},
        )
        runtime = ToolRuntime(ToolsConfig(enabled=True, decision_mode="json", max_calls_per_turn=2))
        await runtime.start()
        try:
            first, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("在脚下放一块泥土")],
                context=gate.context(),
                query="在脚下放一块泥土",
            )
            assert first == "把这个方块放上去会真的改变世界，需要你确认一下。"
            assert gate.service.action_calls("place") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我去放。"
            assert len(gate.service.action_calls("place")) == 1
        finally:
            await runtime.close()


async def test_bridge_is_the_only_way_to_the_service() -> None:
    """工具不允许直接碰 runtime/HTTP：它只经 bridge（判定 → Service）。"""
    import inspect

    from app.tools.builtins import minecraft_inventory, minecraft_place

    for module in (minecraft_place, minecraft_inventory):
        source = inspect.getsource(module)
        for forbidden in ("runtime_client", "mineflayer", "aiohttp", "127.0.0.1"):
            assert forbidden not in source, f"{module.__name__} 出现了 {forbidden}"
    assert bridge_from is not None
