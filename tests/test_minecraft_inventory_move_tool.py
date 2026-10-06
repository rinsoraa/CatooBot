"""minecraft_inventory_move 工具与确认链测试（Phase 4D §二十/§二十七/§二十八）。

核心不变量：**一个物品、一个来源槽、一个目标槽、一个数量**；目标槽位被别的物品占用时
拒绝且绝不隐式交换；槽位必须在主背包 + 快捷栏（9~44）范围内；移动成功与否以
runtime 的实测结果为准（``move.unconfirmed`` 时不能报成功）。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftDestinationOccupied,
    MinecraftItemChanged,
    MinecraftItemCountInsufficient,
    MinecraftItemNotFound,
    MinecraftMoveUnconfirmed,
    MinecraftSlotInvalid,
)
from app.tools.builtins import MinecraftInventoryMoveTool
from app.tools.models import ToolContext
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService

#: 把 37 格的 dirt 挪一格到 9 格（槽位是玩家窗口绝对槽位：主背包 9-35 + 快捷栏 36-44）
MOVE_ARGS = {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}


async def call(gate: Gate, arguments: dict[str, Any], context: ToolContext | None = None) -> Any:
    return await gate.call("minecraft_inventory_move", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_inventory_move")
        assert tool is not None and isinstance(tool, MinecraftInventoryMoveTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"source_slot", "destination_slot", "item", "count"}
        assert schema["required"] == ["source_slot", "destination_slot", "item", "count"]
        assert schema["additionalProperties"] is False
        # §十六：槽位范围就是玩家窗口的 9~44，模型猜不了更大的数字
        assert schema["properties"]["source_slot"]["minimum"] == 9
        assert schema["properties"]["source_slot"]["maximum"] == 44
        assert schema["properties"]["count"]["minimum"] == 1
    finally:
        await runtime.close()


# --------------------------------------------------------------- 参数校验


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        bad_cases = [
            ({}, "invalid_arguments"),
            ({**MOVE_ARGS, "source_slot": 8}, "invalid_arguments"),
            ({**MOVE_ARGS, "source_slot": 45}, "invalid_arguments"),
            ({**MOVE_ARGS, "destination_slot": 0}, "invalid_arguments"),
            ({**MOVE_ARGS, "source_slot": 2.5}, "invalid_arguments"),
            ({**MOVE_ARGS, "source_slot": True}, "invalid_arguments"),
            ({**MOVE_ARGS, "count": 0}, "invalid_arguments"),
            ({**MOVE_ARGS, "count": -3}, "invalid_arguments"),
            ({**MOVE_ARGS, "count": 1.5}, "invalid_arguments"),
            ({**MOVE_ARGS, "item": ""}, "invalid_arguments"),
            ({**MOVE_ARGS, "window": "chest"}, "invalid_arguments"),
        ]
        for arguments, expected in bad_cases:
            result = await call(gate, arguments)
            assert result.success is False and result.error_type == expected, arguments
        assert gate.service.action_calls("inventory_move") == [], "schema 层就拒了，绝不进 service"
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不挂待确认"


async def test_same_slot_is_rejected_by_the_service_validation() -> None:
    """source == destination 不是 JSON schema 能表达的规则，由 Service/Runtime 拒绝。

    工具层（LLM 路径）因此会先挂出一条待确认——结构上它是个合法请求；但真正执行时
    `MinecraftService.inventory_move` 第一件事就是校验槽位并抛 ``slot_invalid``，
    动作绝不会跑起来。WebUI 调试入口则在进确认门**之前**就校验（见 API 测试）。
    """
    from app.integrations.minecraft.service import MinecraftActionInvalid, MinecraftService

    try:
        MinecraftService.validate_inventory_move(37, 37, "dirt", 1)
    except MinecraftActionInvalid as exc:
        assert "不能相同" in str(exc)
    else:  # pragma: no cover - 校验存在就不该走到这里
        raise AssertionError("同一个槽位的搬运必须被拒绝")

    # 边界内的槽位必须照常通过（拒绝的是"同一个"，不是"数字太大"）
    assert MinecraftService.validate_inventory_move(37, 9, "dirt", 1) == (37, 9, "dirt", 1)


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
    result = await MinecraftInventoryMoveTool().execute(
        MOVE_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftInventoryMoveTool().execute(
        MOVE_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, MOVE_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_move_items() -> None:
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, MOVE_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("inventory_move") == []


async def test_untrusted_minecraft_player_cannot_move_items() -> None:
    async with Gate() as gate:
        result = await call(gate, MOVE_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.action_calls("inventory_move") == []


async def test_risk_flag_gates_inventory_move() -> None:
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, MOVE_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, MOVE_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("inventory_move") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_inventory_move" and pending["risk"] == "MEDIUM"
        # §十四：摘要要说清「哪个格的什么、多少、挪到哪个格」
        assert pending["summary"] == "把 37 格的 dirt ×1 移到 9 格"

        second = await call(gate, MOVE_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING"
        assert second.data["action_id"] == "act_move_item_1"
        assert gate.service.action_calls("inventory_move") == [
            {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
        ]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, MOVE_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("inventory_move")) == 1


async def test_changing_any_argument_creates_a_new_confirmation() -> None:
    """§二十八：source/destination/item/count 任何一个变了 → mismatch + 新确认。

    确认的是「从这一格、搬这个东西、搬这么多、到那一格」这一个具体动作。
    """
    variations = (
        ("source_slot", 36),
        ("destination_slot", 10),
        ("item", "sand"),
        ("count", 2),
    )
    executed = 0
    for field, other_value in variations:
        # 每个变体用自己的 Gate：工具速率限制是「同一用户每分钟 10 次」，循环会撞上
        async with Gate() as gate:
            context = gate.context()
            base = dict(MOVE_ARGS)
            pending = await call(gate, base, context)
            assert pending.error_type == CODE_REQUIRED, field
            tampered = {**base, field: other_value}
            before = len(gate.service.action_calls("inventory_move"))
            mismatched = await call(gate, tampered, context)
            assert mismatched.error_type == CODE_MISMATCH, (field, mismatched.error_type)
            assert len(gate.service.action_calls("inventory_move")) == before, (
                f"{field} 变了就不该执行"
            )
            done = await call(gate, tampered, context)
            assert done.success is True, (field, done.error)
            executed += 1
            assert len(gate.service.action_calls("inventory_move")) == 1
    assert executed == len(variations)


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, MOVE_ARGS, gate.context(session="private:10001"))
        crossed = await call(gate, MOVE_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛"))
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("inventory_move") == []


async def test_inventory_move_is_exclusive() -> None:
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.started",
                    "session_id": "s1",
                    "timestamp": 1.0,
                    "action": "equip",
                    "action_id": "act_equipping",
                }
            )
        )
        blocked = await call(gate, MOVE_ARGS, gate.context())
        assert blocked.error_type == "minecraft.action_busy"


async def test_no_chaining_two_moves_in_one_turn() -> None:
    """一次只能搬一个槽位：同一回合里第二个槽位要重新走确认。"""
    async with Gate() as gate:
        context = gate.context()
        await call(gate, MOVE_ARGS, context)
        await call(gate, MOVE_ARGS, context)
        assert len(gate.service.action_calls("inventory_move")) == 1
        chained = await call(gate, {**MOVE_ARGS, "destination_slot": 10}, context)
        assert chained.error_type in (CODE_MISMATCH, CODE_REQUIRED)
        assert len(gate.service.action_calls("inventory_move")) == 1, "第二格绝不连着搬"


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    cases = [
        (
            MinecraftItemNotFound("9 格是空的"),
            "minecraft.item_not_found",
            None,
        ),
        (
            MinecraftItemChanged("37 格不是 dirt", expected="dirt", actual="sand"),
            "minecraft.item_changed",
            {"expected": "dirt", "actual": "sand"},
        ),
        (
            MinecraftItemCountInsufficient("只有 3 个", available=3, requested=5),
            "minecraft.item_count_insufficient",
            {"available": 3, "requested": 5},
        ),
        (
            MinecraftDestinationOccupied("9 格被 sand 占了", actual="sand"),
            "minecraft.destination_occupied",
            {"actual": "sand"},
        ),
        (
            MinecraftSlotInvalid("服务器给的槽位和预期不一样"),
            "minecraft.slot_invalid",
            None,
        ),
        (
            MinecraftMoveUnconfirmed(
                "移动后 source 还是 5 个", detail={"source_after": 5, "expected": 4}
            ),
            "minecraft.move_unconfirmed",
            {"source_after": 5, "expected": 4},
        ),
    ]
    # 每个错误码用自己的 Gate：工具速率限制是「同一用户每分钟 10 次」
    for exc, expected_code, expected_detail in cases:
        async with Gate() as gate:
            context = gate.context()
            gate.service.enqueue("inventory_move", exc)
            await call(gate, MOVE_ARGS, context)  # 建立确认
            result = await call(gate, MOVE_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_destination_occupied_never_swaps() -> None:
    """§十八：目标被别的物品占用 → 拒绝；绝不隐式交换，也不换目标槽位重试。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "inventory_move", MinecraftDestinationOccupied("9 格被 sand 占了", actual="sand")
        )
        context = gate.context()
        await call(gate, MOVE_ARGS, context)
        result = await call(gate, MOVE_ARGS, context)
        assert result.error_type == "minecraft.destination_occupied"
        assert "sand" in (result.error or "")
        # 只有用户请求的那一次调用；工具没有偷偷换槽位重试
        assert gate.service.action_calls("inventory_move") == [
            {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
        ]


async def test_stack_merge_into_same_item_slot_is_allowed() -> None:
    """§十七：目标槽是**同名可堆叠**的堆 → 允许合并（是否合并由 runtime 判定）。"""
    async with Gate() as gate:
        payload = {
            "status": "RUNNING",
            "action_id": "act_move_item_1",
            "destination_before": {"name": "dirt", "count": 3},
        }
        gate.service.enqueue("inventory_move", payload)
        context = gate.context()
        await call(gate, MOVE_ARGS, context)
        result = await call(gate, MOVE_ARGS, context)
        assert result.success is True
        # 工具如实回「已开始」：真实结果（合并后的数量）由终态事件带回
        assert result.data["status"] == "RUNNING"
        assert result.data["action_id"] == "act_move_item_1"
        assert gate.service.action_calls("inventory_move") == [
            {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
        ]


# --------------------------------------------------- 整轮（含 LLM 决策）


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_inventory_move", **MOVE_ARGS),
                    "搬东西会改背包，需要你确认一下。",
                    decide("minecraft_inventory_move", **MOVE_ARGS),
                    "好，我挪过去了。",
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
                [ChatMessage.user("把 37 格的泥土挪一格到 9 格")],
                context=gate.context(),
                query="把 37 格的泥土挪一格到 9 格",
            )
            assert first == "搬东西会改背包，需要你确认一下。"
            assert gate.service.action_calls("inventory_move") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我挪过去了。"
            assert gate.service.action_calls("inventory_move") == [
                {"source_slot": 37, "destination_slot": 9, "item": "dirt", "count": 1}
            ]
        finally:
            await runtime.close()
