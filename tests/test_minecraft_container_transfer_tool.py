"""minecraft_container_transfer 工具与确认链测试（Phase 4E §十六-§三十四）。

核心不变量：**一个方向、一个 container_slot、一个 inventory_slot、一个 item、一个 count**；
MEDIUM → 必须用户确认；目标槽被别的物品占用时拒绝（绝不交换/换槽）；只支持单方块 Chest / Barrel。

真正的窗口生命周期与「移动后重读复核」由 ``minecraft_runtime/test/container_transfer.test.js``
用假窗口覆盖；这里测 Tool → Policy（含确认门）→ Service 的完整链路。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftContainerClosed,
    MinecraftContainerCloseFailed,
    MinecraftContainerOpenFailed,
    MinecraftContainerTooFar,
    MinecraftContainerTransferUnconfirmed,
    MinecraftContainerUnsupported,
    MinecraftDestinationOccupied,
    MinecraftItemChanged,
    MinecraftItemCountInsufficient,
    MinecraftItemNotFound,
    MinecraftSlotInvalid,
)
from app.tools.builtins import MinecraftContainerTransferTool
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService

#: 把箱子第 0 格的 dirt 拿 1 个到背包第 9 格
TRANSFER_ARGS = {
    "x": 100,
    "y": 64,
    "z": 100,
    "direction": "withdraw",
    "container_slot": 0,
    "inventory_slot": 9,
    "item": "dirt",
    "count": 1,
}
DEPOSIT_ARGS = {**TRANSFER_ARGS, "direction": "deposit"}


async def call(gate: Gate, arguments: dict[str, Any], context: Any = None) -> Any:
    return await gate.call("minecraft_container_transfer", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_container_transfer")
        assert tool is not None and isinstance(tool, MinecraftContainerTransferTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {
            "x",
            "y",
            "z",
            "direction",
            "container_slot",
            "inventory_slot",
            "item",
            "count",
        }
        assert schema["required"] == [
            "x",
            "y",
            "z",
            "direction",
            "container_slot",
            "inventory_slot",
            "item",
            "count",
        ]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["direction"]["enum"] == ["withdraw", "deposit"]
        assert schema["properties"]["container_slot"]["minimum"] == 0
        assert schema["properties"]["inventory_slot"]["minimum"] == 9
        assert schema["properties"]["inventory_slot"]["maximum"] == 44
        assert schema["properties"]["count"]["minimum"] == 1
    finally:
        await runtime.close()


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        bad_cases = [
            ({}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "direction": "take"}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "direction": None}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "container_slot": -1}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "container_slot": 1.5}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "inventory_slot": 8}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "inventory_slot": 45}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "count": 0}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "count": 2.5}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "item": ""}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "x": 1.5}, "invalid_arguments"),
            ({**TRANSFER_ARGS, "extra": 1}, "invalid_arguments"),
        ]
        for arguments, expected in bad_cases:
            result = await call(gate, arguments)
            assert result.success is False and result.error_type == expected, arguments
        assert gate.service.action_calls("container_transfer") == [], "schema 层就拒了"
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不挂待确认"


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
    result = await MinecraftContainerTransferTool().execute(
        TRANSFER_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftContainerTransferTool().execute(
        TRANSFER_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, TRANSFER_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_transfer() -> None:
    """§三十五：只有 USER 回合能产生 explicit intent —— 自主回合里模型自己想搬也不行。"""
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, TRANSFER_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("container_transfer") == []


async def test_untrusted_minecraft_player_cannot_transfer() -> None:
    async with Gate() as gate:
        result = await call(gate, TRANSFER_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.action_calls("container_transfer") == []


async def test_risk_flag_gates_transfer() -> None:
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, TRANSFER_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, TRANSFER_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("container_transfer") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_container_transfer" and pending["risk"] == "MEDIUM"

        second = await call(gate, TRANSFER_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_ctransfer_1"
        assert gate.service.action_calls("container_transfer") == [TRANSFER_ARGS]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, TRANSFER_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("container_transfer")) == 1


async def test_confirmation_summaries_are_exact_for_both_directions() -> None:
    """§三十四：摘要必须写清「哪个箱子、第几格、什么物品、多少、另一个槽位」。"""
    async with Gate() as gate:
        context = gate.context()
        withdraw = await call(gate, TRANSFER_ARGS, context)
        assert withdraw.error_type == CODE_REQUIRED
        summary = withdraw.data["confirmation"]["summary"]
        assert summary == "从 (100, 64, 100) 的容器第 0 格取 dirt ×1 到背包第 9 格", summary

        deposit = await call(gate, DEPOSIT_ARGS, context)
        assert deposit.error_type == CODE_MISMATCH, "方向变了就不是同一个动作"
        # mismatch 会作废旧确认并按新参数重挂一条 —— 摘要看那条
        repending = gate.bridge.confirmations.pending()
        assert len(repending) == 1
        assert repending[0].arguments == DEPOSIT_ARGS
        assert (
            repending[0].summary == "把背包第 9 格的 dirt ×1 放入 (100, 64, 100) 的容器第 0 格"
        ), repending[0].summary


async def test_changing_any_argument_creates_a_new_confirmation() -> None:
    """§三十四：x/y/z/direction/container_slot/inventory_slot/item/count 任一变化 → mismatch。"""
    variations = (
        ("x", 101),
        ("y", 65),
        ("z", 101),
        ("direction", "deposit"),
        ("container_slot", 3),
        ("inventory_slot", 10),
        ("item", "sand"),
        ("count", 2),
    )
    executed = 0
    for field, other_value in variations:
        # 每个变体用自己的 Gate：工具速率限制是「同一用户每分钟 10 次」
        async with Gate() as gate:
            context = gate.context()
            base = dict(TRANSFER_ARGS)
            pending = await call(gate, base, context)
            assert pending.error_type == CODE_REQUIRED, field
            tampered = {**base, field: other_value}
            mismatched = await call(gate, tampered, context)
            assert mismatched.error_type == CODE_MISMATCH, (field, mismatched.error_type)
            assert gate.service.action_calls("container_transfer") == [], f"{field} 变了就不该执行"
            done = await call(gate, tampered, context)
            assert done.success is True, (field, done.error)
            executed += 1
    assert executed == len(variations)


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, TRANSFER_ARGS, gate.context(session="private:10001"))
        crossed = await call(
            gate, TRANSFER_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛")
        )
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("container_transfer") == []


async def test_expired_confirmation_cannot_transfer() -> None:
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await call(gate, TRANSFER_ARGS, gate.context())
        clock["now"] += 61.0
        after = await call(gate, TRANSFER_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.service.action_calls("container_transfer") == []
        assert len(gate.bridge.confirmations.pending()) == 1, "过期后按当前参数重挂一条"


async def test_container_transfer_is_exclusive() -> None:
    """§三十六：transfer 与 equip / inventory_move / dig / place / move_to 全部互斥。"""
    from app.integrations.minecraft.events import parse_bridge_event

    for busy_action in ("equip", "inventory_move", "dig", "place", "move_to", "container_inspect"):
        async with Gate() as gate:
            gate.bridge.context.apply_event(
                parse_bridge_event(
                    {
                        "event": "minecraft.action.started",
                        "session_id": "s1",
                        "timestamp": 1.0,
                        "action": busy_action,
                        "action_id": "act_busy",
                    }
                )
            )
            blocked = await call(gate, TRANSFER_ARGS, gate.context())
            assert blocked.error_type == "minecraft.action_busy", busy_action


async def test_no_chaining_two_transfers_in_one_turn() -> None:
    async with Gate() as gate:
        context = gate.context()
        await call(gate, TRANSFER_ARGS, context)
        await call(gate, TRANSFER_ARGS, context)
        assert len(gate.service.action_calls("container_transfer")) == 1
        chained = await call(gate, {**TRANSFER_ARGS, "container_slot": 5}, context)
        assert chained.error_type in (CODE_MISMATCH, CODE_REQUIRED)
        assert len(gate.service.action_calls("container_transfer")) == 1, "第二格绝不连着搬"


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    cases = [
        (
            MinecraftContainerUnsupported("这是 furnace", block="minecraft:furnace"),
            "minecraft.container_unsupported",
            {"block": "minecraft:furnace"},
        ),
        (
            MinecraftContainerTooFar("太远", distance=7.2),
            "minecraft.container_too_far",
            {"distance": 7.2},
        ),
        (MinecraftContainerOpenFailed("服务器没给窗口"), "minecraft.container_open_failed", None),
        (MinecraftContainerClosed("窗口被关掉了"), "minecraft.container_closed", None),
        (
            MinecraftContainerCloseFailed("关不上", detail={"result": {"moved_out": 1}}),
            "minecraft.container_close_failed",
            {"result": {"moved_out": 1}},
        ),
        (
            MinecraftContainerTransferUnconfirmed("状态不对", detail={"moved_out": 0}),
            "minecraft.container_transfer_unconfirmed",
            {"moved_out": 0},
        ),
        (MinecraftItemNotFound("箱子那一格是空的"), "minecraft.item_not_found", None),
        (
            MinecraftItemChanged("那一格是 sand", expected="dirt", actual="sand"),
            "minecraft.item_changed",
            {"expected": "dirt", "actual": "sand"},
        ),
        (
            MinecraftItemCountInsufficient("只有 3 个", available=3, requested=5),
            "minecraft.item_count_insufficient",
            {"available": 3, "requested": 5},
        ),
        (
            MinecraftDestinationOccupied("背包那格被 sand 占了", actual="sand"),
            "minecraft.destination_occupied",
            {"actual": "sand"},
        ),
        (MinecraftSlotInvalid("容器没有那么格"), "minecraft.slot_invalid", None),
    ]
    for exc, expected_code, expected_detail in cases:
        async with Gate() as gate:
            context = gate.context()
            gate.service.enqueue("container_transfer", exc)
            await call(gate, TRANSFER_ARGS, context)  # 建立确认
            result = await call(gate, TRANSFER_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_destination_occupied_never_swaps_or_picks_another_slot() -> None:
    async with Gate() as gate:
        context = gate.context()
        gate.service.enqueue(
            "container_transfer", MinecraftDestinationOccupied("被 sand 占了", actual="sand")
        )
        await call(gate, TRANSFER_ARGS, context)
        result = await call(gate, TRANSFER_ARGS, context)
        assert result.error_type == "minecraft.destination_occupied"
        # 只有用户请求的那一次调用；工具绝不偷偷换槽位重试
        assert gate.service.action_calls("container_transfer") == [TRANSFER_ARGS]


# ------------------------------------------------ §四十七：activity 只写事实


async def test_activity_lines_are_factual_for_both_directions() -> None:
    from app.integrations.minecraft.events import parse_bridge_event

    cases = [
        (
            "withdraw",
            {
                "direction": "withdraw",
                "position": {"x": 100, "y": 64, "z": 100},
                "container_type": "minecraft:chest",
                "item": "dirt",
                "count": 1,
            },
            "刚从 (100, 64, 100) 的 Chest 取出了 dirt ×1",
        ),
        (
            "deposit",
            {
                "direction": "deposit",
                "position": {"x": 100, "y": 64, "z": 100},
                "container_type": "minecraft:barrel",
                "item": "sand",
                "count": 8,
            },
            "刚把 sand ×8 放回 (100, 64, 100) 的 Barrel",
        ),
    ]
    for direction, result, expected in cases:
        async with Gate() as gate:
            gate.bridge.context.apply_event(
                parse_bridge_event(
                    {
                        "event": "minecraft.action.completed",
                        "session_id": "s1",
                        "timestamp": 2.0,
                        "action": "container_transfer",
                        "action_id": "act_ctransfer_1",
                        "status": "SUCCEEDED",
                        "result": result,
                    }
                )
            )
            assert gate.bridge.context.activity == expected, direction


async def test_inspect_activity_is_factual() -> None:
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "container_inspect",
                    "action_id": "act_cinspect_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "ok": True,
                        "container": {
                            "type": "minecraft:barrel",
                            "position": {"x": 1, "y": 2, "z": 3},
                        },
                        "slots": [{"slot": 0, "name": "dirt", "count": 3}],
                    },
                }
            )
        )
        assert gate.bridge.context.activity == "刚打开了一个 Barrel 并查看了里面的东西"


# --------------------------------------------------- 整轮（含 LLM 决策）


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_container_transfer", **TRANSFER_ARGS),
                    "从箱子里拿东西会改背包和箱子，需要你确认一下。",
                    decide("minecraft_container_transfer", **TRANSFER_ARGS),
                    "好，我拿出来了。",
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
                [ChatMessage.user("从箱子里拿一个泥土")],
                context=gate.context(),
                query="从箱子里拿一个泥土",
            )
            assert first == "从箱子里拿东西会改背包和箱子，需要你确认一下。"
            assert gate.service.action_calls("container_transfer") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我拿出来了。"
            assert gate.service.action_calls("container_transfer") == [TRANSFER_ARGS]
        finally:
            await runtime.close()
