"""minecraft_equip 工具与确认链测试（Phase 4D §十九/§二十七）。

覆盖：schema（只有 item、没有槽位）、非法参数、offline、disabled、allow_medium=false、
非 USER 回合、可信玩家、确认门全流程（required → consume → 参数变化 → 会话绑定）、
服务层错误（item_not_found / item_changed / equip_unconfirmed）、already_equipped 透传、
「一次只能拿一个」，以及 equip → place 的联动链（§二十三）。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftEquipUnconfirmed,
    MinecraftHeldItemChanged,
    MinecraftItemChanged,
    MinecraftItemNotFound,
)
from app.tools.builtins import MinecraftEquipTool
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService
from tests.test_minecraft_place_tool import PLACE_ARGS
from tests.test_minecraft_place_tool import call as place_call

#: equip 只接受一个物品名（槽位不在这一层出现）
EQUIP_ARGS = {"item": "dirt"}


async def call(gate: Gate, arguments: dict[str, Any], context: ToolContext | None = None) -> Any:
    return await gate.call("minecraft_equip", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_equip")
        assert tool is not None and isinstance(tool, MinecraftEquipTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"item"}
        assert schema["required"] == ["item"]
        assert schema["additionalProperties"] is False
        # §三：destination 固定 hand，绝不暴露给模型；槽位更不该出现（那是 move 的事）
        assert "destination" not in schema["properties"]
        assert "slot" not in schema["properties"]
        assert "count" not in schema["properties"]
    finally:
        await runtime.close()


# --------------------------------------------------------------- 参数校验


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        bad_cases = [
            ({}, "invalid_arguments"),
            ({"item": ""}, "invalid_arguments"),
            ({"item": 7}, "invalid_arguments"),
            ({"item": "dirt", "slot": 36}, "invalid_arguments"),
            ({"item": "dirt", "destination": "hand"}, "invalid_arguments"),
        ]
        for arguments, expected in bad_cases:
            result = await call(gate, arguments)
            assert result.success is False and result.error_type == expected, arguments
        assert gate.service.action_calls("equip") == [], "schema 层就拒了，绝不进 service"
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不挂待确认"


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
    result = await MinecraftEquipTool().execute(
        EQUIP_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftEquipTool().execute(
        EQUIP_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, EQUIP_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_equip() -> None:
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, EQUIP_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("equip") == []


async def test_untrusted_minecraft_player_cannot_equip() -> None:
    async with Gate() as gate:
        result = await call(gate, EQUIP_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.action_calls("equip") == []


async def test_risk_flag_gates_equip() -> None:
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, EQUIP_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, EQUIP_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("equip") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_equip" and pending["risk"] == "MEDIUM"
        # §十四：摘要说清「把什么拿到手里」
        assert pending["summary"] == "把 dirt 拿到手里"

        second = await call(gate, EQUIP_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_equip_1"
        assert gate.service.action_calls("equip") == [{"item": "dirt"}]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, EQUIP_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("equip")) == 1, "第二次拿同一个物品也要重新确认"


async def test_running_payload_and_terminal_activity_are_honest() -> None:
    """启动响应只报 RUNNING + action_id；「已经拿在手里」这类事实走终态事件。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        context = gate.context()
        await call(gate, EQUIP_ARGS, context)
        result = await call(gate, EQUIP_ARGS, context)
        assert result.success is True
        # §三十三：启动阶段不发明 already_equipped / held_item 之类的事实
        assert result.data == {
            "ok": True,
            "action": "equip",
            "status": "RUNNING",
            "action_id": "act_equip_1",
        }
        assert "act_equip_1" in (result.summary or "")
        assert "完成" not in (result.summary or "").replace("完成与否", "")

        # 真正的事实（含 already_equipped 与 held_item）由终态事件带回上下文
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "equip",
                    "action_id": "act_equip_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "item": "dirt",
                        "destination": "hand",
                        "source_slot": 36,
                        "held_item": {"name": "dirt", "count": 12},
                        "already_equipped": True,
                    },
                }
            )
        )
        assert gate.bridge.context.activity == "刚把 dirt 拿到手里"
        assert (gate.bridge.context.last_action or {})["status"] == "SUCCEEDED"


async def test_changing_item_creates_a_new_confirmation() -> None:
    """确认指纹覆盖 item：用户确认的是「拿这个物品」，换个物品必须重新确认。"""
    async with Gate() as gate:
        context = gate.context()
        pending = await call(gate, EQUIP_ARGS, context)
        assert pending.error_type == CODE_REQUIRED
        mismatched = await call(gate, {"item": "sand"}, context)
        assert mismatched.error_type == CODE_MISMATCH
        assert gate.service.action_calls("equip") == [], "参数变了就不该执行"
        done = await call(gate, {"item": "sand"}, context)
        assert done.success is True
        assert gate.service.action_calls("equip") == [{"item": "sand"}]


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, EQUIP_ARGS, gate.context(session="private:10001"))
        crossed = await call(gate, EQUIP_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛"))
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("equip") == []


async def test_expired_confirmation_cannot_equip() -> None:
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await call(gate, EQUIP_ARGS, gate.context())
        clock["now"] += 61.0
        after = await call(gate, EQUIP_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.service.action_calls("equip") == []
        assert len(gate.bridge.confirmations.pending()) == 1, "过期后按当前参数重挂一条"


async def test_equip_is_exclusive() -> None:
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
        blocked = await call(gate, EQUIP_ARGS, gate.context())
        assert blocked.error_type == "minecraft.action_busy"
        # 只读的 inventory 仍然可用（非独占）
        runtime = ToolRuntime(ToolsConfig(enabled=True))
        await runtime.start()
        try:
            executor = ToolExecutor(ToolsConfig(enabled=True), runtime.registry, runtime.policy)
            read = await executor.execute(
                ToolCall(name="minecraft_inventory", arguments={}),
                gate.context(),
                TurnBudget(),
            )
            assert read.success is True
        finally:
            await runtime.close()


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    async with Gate() as gate:
        cases = [
            (
                MinecraftItemNotFound("背包里没有 dirt"),
                "minecraft.item_not_found",
                None,
            ),
            (
                MinecraftItemChanged("槽位换人了", expected="dirt", actual="sand"),
                "minecraft.item_changed",
                {"expected": "dirt", "actual": "sand"},
            ),
            (
                MinecraftEquipUnconfirmed("主手还是空的", expected="dirt", actual="empty"),
                "minecraft.equip_unconfirmed",
                {"expected": "dirt", "actual": "empty"},
            ),
        ]
        context = gate.context()
        for exc, expected_code, expected_detail in cases:
            gate.service.enqueue("equip", exc)
            await call(gate, EQUIP_ARGS, context)  # 建立确认
            result = await call(gate, EQUIP_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


# --------------------------------------------------- 整轮（含 LLM 决策）


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_equip", **EQUIP_ARGS),
                    "换手会改手持状态，需要你确认一下。",
                    decide("minecraft_equip", **EQUIP_ARGS),
                    "好，我拿到手里了。",
                ]
            }
        )
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            providers={"mock": provider},
        )
        runtime = ToolRuntime(ToolsConfig(enabled=True))
        await runtime.start()
        try:
            first, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("把泥土拿到手里")],
                context=gate.context(),
                query="把泥土拿到手里",
            )
            assert first == "换手会改手持状态，需要你确认一下。"
            assert gate.service.action_calls("equip") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我拿到手里了。"
            assert gate.service.action_calls("equip") == [{"item": "dirt"}]
        finally:
            await runtime.close()


# ------------------------------------------------ §二十三 equip → place 联动


async def test_equip_then_place_chain_needs_two_separate_confirmations() -> None:
    """链条：inventory（只读）→ equip（确认）→ 主手变了 → place（另一次确认）。

    两件事各自是一次独立授权：确认了「拿 dirt」不等于确认了「在某个位置放 dirt」。
    """
    async with Gate() as gate:
        context = gate.context()

        # 1) 只读地看清背包（SAFE，不需要确认；不占用前台动作）
        inventory = await gate.call("minecraft_inventory", {}, context)
        assert inventory.success is True
        assert gate.service.action_calls("inventory")
        assert gate.bridge.confirmations.pending() == [], "只读不产生确认"

        # 2) 主手还是 sand 时放 dirt → 服务层如实拒绝（held_item_changed）
        gate.service.enqueue(
            "place", MinecraftHeldItemChanged("主手不是 dirt", expected="dirt", actual="sand")
        )
        await place_call(gate, PLACE_ARGS, context)
        failed = await place_call(gate, PLACE_ARGS, context)
        assert failed.error_type == "minecraft.held_item_changed"
        assert failed.data["error"]["detail"] == {"expected": "dirt", "actual": "sand"}

        # 3) equip dirt：独立的确认（摘要只说拿东西，不承诺放置）
        equip_pending = await call(gate, EQUIP_ARGS, context)
        assert equip_pending.error_type == CODE_REQUIRED
        assert "手里" in equip_pending.data["confirmation"]["summary"]
        assert "放" not in equip_pending.data["confirmation"]["summary"]
        equipped = await call(gate, EQUIP_ARGS, context)
        assert equipped.success is True and equipped.data["action_id"] == "act_equip_1"

        # 4) place 再走一遍自己的确认（equip 的授权不覆盖放置）
        place_pending = await place_call(gate, PLACE_ARGS, context)
        assert place_pending.error_type == CODE_REQUIRED
        placed = await place_call(gate, PLACE_ARGS, context)
        assert placed.success is True and placed.data["action_id"] == "act_place_1"

        # 两次动作各执行一次，参数原样到达 service（第一次 place 在 service 层被
        # 主手校验拒掉 —— 那次调用也真实发生过，记录在案）
        assert gate.service.action_calls("equip") == [{"item": "dirt"}]
        assert gate.service.action_calls("place") == [
            {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"},
            {"x": 2, "y": 64, "z": 2, "face": "up", "expected_item": "dirt"},
        ]
        assert gate.bridge.confirmations.pending() == [], "两次授权都已消费掉"


async def test_bridge_is_the_only_way_to_the_service() -> None:
    """工具不允许直接碰 runtime/HTTP：它只经 bridge（判定 → Service）。"""
    import inspect

    from app.tools.builtins import minecraft_equip, minecraft_inventory_move

    for module in (minecraft_equip, minecraft_inventory_move):
        source = inspect.getsource(module)
        for forbidden in ("runtime_client", "mineflayer", "aiohttp", "127.0.0.1"):
            assert forbidden not in source, f"{module.__name__} 出现了 {forbidden}"
    assert MinecraftConfig is not None
