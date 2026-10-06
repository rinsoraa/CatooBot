"""minecraft_pickup_item 工具与确认链测试（Phase 4H §四十）。

核心不变量：**只捡一个明确指定的实体**（entity_id + expected_item 双重约束）；
MEDIUM → 必须用户确认；成功判据由 runtime 的"实体真的被收集 + 背包增加"提供。

真正的动作状态机（导航/监督循环/身份绑定/收集确认/取消）由
``minecraft_runtime/test/pickup_item.test.js`` 覆盖；这里测 Tool → Policy（确认门）→ Service。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftActionInvalid,
    MinecraftItemEntityChanged,
    MinecraftItemEntityInvalid,
    MinecraftItemEntityNotFound,
    MinecraftPickupFailed,
    MinecraftPickupTargetLost,
    MinecraftPickupTargetReplaced,
    MinecraftPickupTargetTooFar,
    MinecraftPickupUnconfirmed,
    MinecraftService,
)
from app.tools.builtins import MinecraftPickupItemTool
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService

PICKUP_ARGS = {"entity_id": 123, "expected_item": "dirt"}


async def call(gate: Gate, arguments: dict[str, Any], context: Any = None) -> Any:
    return await gate.call("minecraft_pickup_item", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_pickup_item")
        assert tool is not None and isinstance(tool, MinecraftPickupItemTool)
        schema = tool.metadata.input_schema
        # §十三/§十四：两个字段都 required —— entity_id 不是足够的安全约束
        assert set(schema["properties"]) == {"entity_id", "expected_item"}
        assert schema["required"] == ["entity_id", "expected_item"]
        assert schema["additionalProperties"] is False
        assert schema["properties"]["entity_id"]["type"] == "integer"
        assert schema["properties"]["entity_id"]["minimum"] == 0
        assert schema["properties"]["expected_item"]["minLength"] == 1
        # §十二：绝不接受 x/y/z 当作目标（地上的东西会移动）
        for forbidden in ("x", "y", "z", "position", "count", "all", "nearest"):
            assert forbidden not in schema["properties"], forbidden
    finally:
        await runtime.close()


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        for arguments in (
            {},
            {"entity_id": 123},
            {"expected_item": "dirt"},
            {"entity_id": "123", "expected_item": "dirt"},
            {"entity_id": 1.5, "expected_item": "dirt"},
            {"entity_id": -1, "expected_item": "dirt"},
            {"entity_id": 123, "expected_item": ""},
            {"entity_id": 123, "expected_item": 7},
            {"entity_id": 123, "expected_item": "dirt", "count": 2},
        ):
            result = await call(gate, arguments)
            assert result.success is False, arguments
            assert result.error_type in {"invalid_arguments", "minecraft.action_invalid"}, arguments
        assert gate.service.action_calls("pickup_item") == []
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不挂待确认"


def test_validate_pickup_pure_function() -> None:
    assert MinecraftService.validate_pickup(123, " minecraft:dirt ") == (123, "minecraft:dirt")
    assert MinecraftService.validate_pickup(0, "dirt") == (0, "dirt")
    for bad in (
        (None, "dirt"),
        ("1", "dirt"),
        (1.0, "dirt"),
        (-1, "dirt"),
        (True, "dirt"),
        (1, ""),
        (1, None),
        (1, "x" * 80),
        (1, "bad\x01"),
    ):
        try:
            MinecraftService.validate_pickup(*bad)
        except MinecraftActionInvalid:
            pass
        else:  # pragma: no cover - 非法输入必须被拒
            raise AssertionError(f"应当拒绝：{bad!r}")


# ------------------------------------------------------------ 未启用/离线


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
    result = await MinecraftPickupItemTool().execute(
        PICKUP_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftPickupItemTool().execute(
        PICKUP_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, PICKUP_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_pick_up() -> None:
    """§十七/§五十四：INITIATIVE / BACKGROUND / SYSTEM 一律不能触发拾取。"""
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, PICKUP_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("pickup_item") == []


async def test_untrusted_minecraft_player_cannot_pick_up() -> None:
    async with Gate() as gate:
        result = await call(gate, PICKUP_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.action_calls("pickup_item") == []


async def test_risk_flag_gates_pickup() -> None:
    """§十八：pickup 是 MEDIUM —— allow_medium 默认 false，连确认都不创建。"""
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, PICKUP_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, PICKUP_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("pickup_item") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_pickup_item" and pending["risk"] == "MEDIUM"
        # §十五/§十六：摘要是"拾取附近的 X（实体 #id）"，不写成"在 (x,y,z) 执行"
        assert pending["summary"] == "拾取附近的 dirt（实体 #123）", pending["summary"]
        stored = gate.bridge.confirmations.pending()
        assert stored[0].arguments == PICKUP_ARGS, "指纹绑 entity_id + expected_item"

        second = await call(gate, PICKUP_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_pickup_1"
        assert gate.service.action_calls("pickup_item") == [PICKUP_ARGS]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, PICKUP_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("pickup_item")) == 1


async def test_changing_entity_id_or_item_creates_a_new_confirmation() -> None:
    """§五十四：entity_id 变了 / expected_item 变了都必须 mismatch + 重新确认。"""
    for field, other in (("entity_id", 130), ("expected_item", "sand")):
        async with Gate() as gate:
            context = gate.context()
            pending = await call(gate, PICKUP_ARGS, context)
            assert pending.error_type == CODE_REQUIRED, field
            tampered = {**PICKUP_ARGS, field: other}
            mismatched = await call(gate, tampered, context)
            assert mismatched.error_type == CODE_MISMATCH, (field, mismatched.error_type)
            assert gate.service.action_calls("pickup_item") == [], f"{field} 变了就不该执行"
            repending = gate.bridge.confirmations.pending()
            assert len(repending) == 1 and repending[0].arguments == tampered
            done = await call(gate, tampered, context)
            assert done.success is True, (field, done.error)
            assert gate.service.action_calls("pickup_item") == [tampered]


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, PICKUP_ARGS, gate.context(session="private:10001"))
        crossed = await call(
            gate, PICKUP_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛")
        )
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("pickup_item") == []


async def test_expired_confirmation_cannot_pick_up() -> None:
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await call(gate, PICKUP_ARGS, gate.context())
        clock["now"] += 61.0
        after = await call(gate, PICKUP_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.service.action_calls("pickup_item") == []
        assert len(gate.bridge.confirmations.pending()) == 1, "过期后按当前参数重挂一条"


async def test_cancelling_a_pending_pickup_confirmation() -> None:
    """§四十 stop flow：用户/WebUI 取消这条待确认 → 授权作废，再要就得重新确认。"""
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, PICKUP_ARGS, context)
        assert first.error_type == CODE_REQUIRED
        pending = gate.bridge.confirmations.pending()[0]
        assert gate.bridge.confirmations.cancel(pending.confirmation_id) is True
        assert gate.bridge.confirmations.pending() == []
        again = await call(gate, PICKUP_ARGS, context)
        assert again.error_type == CODE_REQUIRED, "取消之后必须重新确认"
        assert gate.service.action_calls("pickup_item") == []


async def test_pickup_is_exclusive() -> None:
    """§二十五/§十九：拾取与其它前台动作互斥（它会移动 + 改背包）。"""
    from app.integrations.minecraft.events import parse_bridge_event

    for busy_action in (
        "dig",
        "place",
        "equip",
        "inventory_move",
        "container_transfer",
        "craft",
        "move_to",
    ):
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
            blocked = await call(gate, PICKUP_ARGS)
            assert blocked.error_type == "minecraft.action_busy", busy_action


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    cases = [
        (
            MinecraftItemEntityNotFound("附近找不到实体 #123"),
            "minecraft.item_entity_not_found",
            None,
        ),
        (
            MinecraftItemEntityInvalid("实体 #123 不是掉落物", detail={"entity_id": 123}),
            "minecraft.item_entity_invalid",
            {"entity_id": 123},
        ),
        (
            MinecraftItemEntityChanged("实体上是 sand", expected="dirt", actual="sand"),
            "minecraft.item_entity_changed",
            {"expected": "dirt", "actual": "sand"},
        ),
        (
            MinecraftPickupTargetReplaced("id 被复用了", detail={"entity_id": 123}),
            "minecraft.pickup_target_replaced",
            {"entity_id": 123},
        ),
        (
            MinecraftPickupTargetLost("被别人捡走了", detail={"entity_id": 123}),
            "minecraft.pickup_target_lost",
            {"entity_id": 123},
        ),
        (
            MinecraftPickupTargetTooFar("太远", detail={"distance": 40, "max_distance": 16}),
            "minecraft.pickup_target_too_far",
            {"distance": 40, "max_distance": 16},
        ),
        (MinecraftPickupFailed("启动导航失败"), "minecraft.pickup_failed", None),
        (
            MinecraftPickupUnconfirmed(
                "实体没了但背包没增加", detail={"inventory_before": 0, "inventory_after": 0}
            ),
            "minecraft.pickup_unconfirmed",
            {"inventory_before": 0, "inventory_after": 0},
        ),
    ]
    for exc, expected_code, expected_detail in cases:
        async with Gate() as gate:
            context = gate.context()
            gate.service.enqueue("pickup_item", exc)
            await call(gate, PICKUP_ARGS, context)  # 建立确认
            result = await call(gate, PICKUP_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_successful_flow_passes_runtime_facts_through() -> None:
    """§三十一：成功结果原样透传（entity_id / 距离 / 背包 before-after）。"""
    payload = {
        "status": "RUNNING",
        "action_id": "act_pickup_1",
        "result": {
            "entity_id": 123,
            "item": {"name": "dirt", "count_before": 3},
            "distance_start": 5.4,
            "distance_collected": 0.9,
            "inventory_before": 10,
            "inventory_after": 13,
            "collected": True,
        },
    }
    async with Gate() as gate:
        gate.service.enqueue("pickup_item", payload)
        context = gate.context()
        await call(gate, PICKUP_ARGS, context)
        result = await call(gate, PICKUP_ARGS, context)
        assert result.success is True
        assert result.data["action"] == "pickup_item" and result.data["status"] == "RUNNING"
        assert result.data["result"] == payload["result"]


async def test_activity_is_a_plain_fact() -> None:
    """§四十九：成功只记事实（"刚拣起了 dirt ×3"），没有情绪、没有夸大。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "pickup_item",
                    "action_id": "act_pickup_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "entity_id": 123,
                        "item": {"name": "dirt", "count_before": 3},
                        "distance_start": 5.4,
                        "distance_collected": 0.9,
                        "inventory_before": 0,
                        "inventory_after": 3,
                        "collected_count": 3,
                        "collected": True,
                    },
                }
            )
        )
        assert gate.bridge.context.activity == "刚拣起了 dirt ×3"


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_pickup_item", **PICKUP_ARGS),
                    "捡东西会让罐头走过去，需要你确认一下。",
                    decide("minecraft_pickup_item", **PICKUP_ARGS),
                    "好，我去捡。",
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
                [ChatMessage.user("把地上那个泥土捡了")],
                context=gate.context(),
                query="把地上那个泥土捡了",
            )
            assert first == "捡东西会让罐头走过去，需要你确认一下。"
            assert gate.service.action_calls("pickup_item") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我去捡。"
            assert gate.service.action_calls("pickup_item") == [PICKUP_ARGS]
        finally:
            await runtime.close()
