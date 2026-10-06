"""minecraft_craft 工具与确认链测试（Phase 4F §十一-§十七/§二十七/§三十一）。

核心不变量：**只接受 recipe_id、一次只执行一次配方**；MEDIUM → 必须用户确认；
材料不够就如实失败（绝不自动准备材料 / 绝不 recipe chain）；只走玩家自身 2×2。

真正的配方表与"重读 inventory 复核"由 ``minecraft_runtime/test/craft.test.js``
（真实 1.21.1 配方 + 假 inventory + 模拟 craft）覆盖；这里测 Tool → Policy（确认门）→ Service。
"""

from __future__ import annotations

from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.confirmation import CODE_MISMATCH, CODE_NOT_TRUSTED, CODE_REQUIRED
from app.integrations.minecraft.service import (
    MinecraftActionInvalid,
    MinecraftCraftFailed,
    MinecraftCraftingTableInvalid,
    MinecraftCraftingTableMissing,
    MinecraftCraftingTableTooFar,
    MinecraftCraftUnconfirmed,
    MinecraftMaterialInsufficient,
    MinecraftRecipeChanged,
    MinecraftRecipeNotFound,
    MinecraftRecipeUnavailable,
    MinecraftService,
)
from app.tools.builtins import MinecraftCraftTool
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import Gate, decide
from tests.test_minecraft_agent_tools import FakeMinecraftService

STICK_ID = "stick*4=oak_planks*2"
BIRCH_ID = "stick*4=birch_planks*2"
CRAFT_ARGS = {"recipe_id": STICK_ID}


async def call(gate: Gate, arguments: dict[str, Any], context: Any = None) -> Any:
    return await gate.call("minecraft_craft", arguments, context or gate.context())


# ------------------------------------------------------------------ schema


async def test_tool_registered_with_strict_schema() -> None:
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_craft")
        assert tool is not None and isinstance(tool, MinecraftCraftTool)
        schema = tool.metadata.input_schema
        # Phase 4G：crafting_table 是**可选**坐标（不给 = 玩家 2×2）
        assert set(schema["properties"]) == {"recipe_id", "crafting_table"}
        assert schema["required"] == ["recipe_id"], "crafting_table 必须保持可选（4F 向后兼容）"
        table_spec = schema["properties"]["crafting_table"]
        assert set(table_spec["properties"]) == {"x", "y", "z"}
        assert table_spec["required"] == ["x", "y", "z"]
        assert table_spec["additionalProperties"] is False
        assert schema["additionalProperties"] is False
        # §十一：第一版**没有** count / times / batch_size —— 彻底避免"执行几次"的误解
        # Phase 4G：crafting_table 是合法的**可选**上下文；批量参数仍然一个都不许有
        for forbidden in ("count", "times", "amount", "batch_size", "item", "table"):
            assert forbidden not in schema["properties"], forbidden
    finally:
        await runtime.close()


async def test_invalid_arguments_never_reach_the_service() -> None:
    async with Gate() as gate:
        # schema 层：缺参数 / 空串 / 太短 / 非字符串 / 多余参数
        for arguments in (
            {},
            {"recipe_id": ""},
            {"recipe_id": "x"},
            {"recipe_id": 7},
            {"recipe_id": "a", "count": 2},
        ):
            result = await call(gate, arguments)
            assert result.success is False, arguments
            assert result.error_type in {"invalid_arguments", "minecraft.action_invalid"}, arguments
        # 形状不对的 recipe_id：schema 的 pattern 在**进确认门之前**就拒掉
        for bad in ("not-a-signature", "abc=def", "stick*4", "=oak_planks*2"):
            result = await call(gate, {"recipe_id": bad})
            assert result.success is False, bad
            assert result.error_type == "invalid_arguments", (bad, result.error_type)
        assert gate.service.action_calls("craft") == []
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
    result = await MinecraftCraftTool().execute(
        CRAFT_ARGS, context_for(MinecraftAgentBridge(service))
    )
    assert result.error_type == "minecraft.disabled"

    service2 = FakeMinecraftService()
    service2.config = MinecraftConfig(
        enabled=True, auto_start_runtime=False, agent={"tools": {"enabled": False}}
    )
    result2 = await MinecraftCraftTool().execute(
        CRAFT_ARGS, context_for(MinecraftAgentBridge(service2))
    )
    assert result2.error_type == "minecraft.disabled"


async def test_offline_is_rejected() -> None:
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate, CRAFT_ARGS)
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


# ------------------------------------------------------- 意图门 / 可信玩家


async def test_non_user_turns_cannot_craft() -> None:
    """§十二：INITIATIVE / BACKGROUND / SYSTEM 都不能执行 craft。"""
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await call(gate, CRAFT_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.service.action_calls("craft") == []


async def test_untrusted_minecraft_player_cannot_craft() -> None:
    async with Gate() as gate:
        result = await call(gate, CRAFT_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.action_calls("craft") == []


async def test_risk_flag_gates_craft() -> None:
    """§三：allow_medium 默认 false —— 关着的时候连确认都不创建。"""
    async with Gate(allow_medium=False) as gate:
        result = await call(gate, CRAFT_ARGS)
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []


async def test_craft_is_exclusive() -> None:
    """§二十：与其它前台动作互斥（合成期间 inventory 正在变）。"""
    from app.integrations.minecraft.events import parse_bridge_event

    for busy_action in ("dig", "place", "equip", "inventory_move", "move_to", "container_transfer"):
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
            blocked = await call(gate, CRAFT_ARGS)
            assert blocked.error_type == "minecraft.action_busy", busy_action


# ---------------------------------------------------------- confirmation


async def test_first_call_requires_confirmation_then_executes() -> None:
    async with Gate() as gate:
        context = gate.context()
        first = await call(gate, CRAFT_ARGS, context)
        assert first.success is False and first.error_type == CODE_REQUIRED
        assert gate.service.action_calls("craft") == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_craft" and pending["risk"] == "MEDIUM"
        # §十三：摘要必须展开成"用多少材料做几个"，而不是"执行 recipe abc123"
        assert pending["summary"] == "用 2 个 oak_planks 制作 4 个 stick"
        # 指纹只绑 recipe_id（store 里的 arguments 就是工具参数本身）
        stored = gate.bridge.confirmations.pending()
        assert len(stored) == 1 and stored[0].arguments == {"recipe_id": STICK_ID}

        second = await call(gate, CRAFT_ARGS, context)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_craft_1"
        assert gate.service.action_calls("craft") == [
            {"recipe_id": STICK_ID, "crafting_table": None}
        ]
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        third = await call(gate, CRAFT_ARGS, context)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("craft")) == 1


async def test_changing_recipe_id_creates_a_new_confirmation() -> None:
    async with Gate() as gate:
        context = gate.context()
        pending = await call(gate, CRAFT_ARGS, context)
        assert pending.error_type == CODE_REQUIRED
        mismatched = await call(gate, {"recipe_id": BIRCH_ID}, context)
        assert mismatched.error_type == CODE_MISMATCH
        assert gate.service.action_calls("craft") == [], "换了配方就不是同一个动作"
        repending = gate.bridge.confirmations.pending()
        assert len(repending) == 1 and repending[0].arguments == {"recipe_id": BIRCH_ID}
        assert repending[0].summary == "用 2 个 birch_planks 制作 4 个 stick"
        done = await call(gate, {"recipe_id": BIRCH_ID}, context)
        assert done.success is True
        assert gate.service.action_calls("craft") == [
            {"recipe_id": BIRCH_ID, "crafting_table": None}
        ]


async def test_confirmation_is_bound_to_session_and_user() -> None:
    async with Gate() as gate:
        await call(gate, CRAFT_ARGS, gate.context(session="private:10001"))
        crossed = await call(gate, CRAFT_ARGS, gate.context(session="minecraft:1.2.3.4:25565:空凛"))
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权"
        assert gate.service.action_calls("craft") == []


async def test_expired_confirmation_cannot_craft() -> None:
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        await call(gate, CRAFT_ARGS, gate.context())
        clock["now"] += 61.0
        after = await call(gate, CRAFT_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.service.action_calls("craft") == []
        assert len(gate.bridge.confirmations.pending()) == 1, "过期后按当前参数重挂一条"


# --------------------------------------------------- 服务层错误 → 结构化


async def test_service_errors_are_structured_with_detail() -> None:
    cases = [
        (MinecraftRecipeNotFound("这个版本里没有这个配方"), "minecraft.recipe_not_found", None),
        (MinecraftRecipeUnavailable("需要工作台"), "minecraft.recipe_unavailable", None),
        (MinecraftRecipeChanged("配方变了"), "minecraft.recipe_changed", None),
        (MinecraftCraftFailed("底层合成失败"), "minecraft.craft_failed", None),
        (
            MinecraftMaterialInsufficient(
                "材料不够：oak_planks 需要 2 个、只有 1 个",
                detail={"missing": [{"name": "oak_planks", "need": 2, "have": 1}]},
            ),
            "minecraft.material_insufficient",
            {"missing": [{"name": "oak_planks", "need": 2, "have": 1}]},
        ),
        (
            MinecraftCraftUnconfirmed(
                "产物 +0", detail={"before": {"result_count": 0}, "after": {"result_count": 0}}
            ),
            "minecraft.craft_unconfirmed",
            {"before": {"result_count": 0}, "after": {"result_count": 0}},
        ),
    ]
    for exc, expected_code, expected_detail in cases:
        async with Gate() as gate:
            context = gate.context()
            gate.service.enqueue("craft", exc)
            await call(gate, CRAFT_ARGS, context)  # 建立确认
            result = await call(gate, CRAFT_ARGS, context)  # 消费并执行 → 失败
            assert result.success is False
            assert result.error_type == expected_code, (exc, result.error_type)
            if expected_detail:
                assert result.data["error"]["detail"] == expected_detail, expected_detail
            assert "Traceback" not in str(result.data)


async def test_material_insufficient_never_prepares_materials() -> None:
    """§四十二/§四十三：材料不够时只失败 —— 绝不调用别的工具去准备材料。"""
    async with Gate() as gate:
        context = gate.context()
        gate.service.enqueue(
            "craft",
            MinecraftMaterialInsufficient(
                "材料不够（只有 oak_log，没有 oak_planks）",
                detail={"missing": [{"name": "oak_planks", "need": 2, "have": 0}]},
            ),
        )
        await call(gate, CRAFT_ARGS, context)
        result = await call(gate, CRAFT_ARGS, context)
        assert result.error_type == "minecraft.material_insufficient"
        # 只发生了这一次 craft 尝试：没有 inventory_move / equip / container_transfer / dig
        actions = [name for name, _ in gate.service.calls]
        assert actions == ["craft"], actions
        assert "做不到" in (result.summary or "") or "材料" in (result.error or "")


async def test_no_chaining_two_crafts_in_one_turn() -> None:
    """§十七：不做 recipe chain —— 第二刀必须重新确认。"""
    async with Gate() as gate:
        context = gate.context()
        await call(gate, CRAFT_ARGS, context)
        await call(gate, CRAFT_ARGS, context)
        assert len(gate.service.action_calls("craft")) == 1
        chained = await call(gate, CRAFT_ARGS, context)
        assert chained.error_type == CODE_REQUIRED
        assert len(gate.service.action_calls("craft")) == 1, "第二刀绝不连着做"


# --------------------------------------------------- §三十九 activity 只写事实


async def test_activity_reports_the_real_count() -> None:
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "craft",
                    "action_id": "act_craft_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "recipe_id": STICK_ID,
                        "item": "stick",
                        "result": {"name": "stick", "count_per_craft": 4, "crafted_count": 4},
                        "ingredients": [{"name": "oak_planks", "consumed": 2}],
                        "before": {"result_count": 0, "ingredient_counts": {"oak_planks": 2}},
                        "after": {"result_count": 4, "ingredient_counts": {"oak_planks": 0}},
                    },
                }
            )
        )
        assert gate.bridge.context.activity == "刚做好了 4 个 stick"


# --------------------------------------------------- 整轮（含 LLM 决策）


async def test_orchestrator_flow_requires_then_consumes() -> None:
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_craft", **CRAFT_ARGS),
                    "合成会改背包，需要你确认一下。",
                    decide("minecraft_craft", **CRAFT_ARGS),
                    "好，我做好了。",
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
                [ChatMessage.user("用木板做几根木棍")],
                context=gate.context(),
                query="用木板做几根木棍",
            )
            assert first == "合成会改背包，需要你确认一下。"
            assert gate.service.action_calls("craft") == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我做好了。"
            assert gate.service.action_calls("craft") == [
                {"recipe_id": STICK_ID, "crafting_table": None}
            ]
        finally:
            await runtime.close()


# ------------------------------------------------- Phase 4G：3×3 工作台合成

TABLE = {"x": 100, "y": 64, "z": 100}
CHEST_ID = "!chest*1=oak_planks*8"


async def test_crafting_table_is_optional_and_passed_through() -> None:
    """J/O：crafting_table 可选；给了就原样传给 Service（4F 调用方式不变）。"""
    async with Gate() as gate:
        omitted = await call(gate, {"recipe_id": STICK_ID}, gate.context())
        assert omitted.error_type == CODE_REQUIRED
        assert gate.service.action_calls("craft") == []

        async with Gate() as gate2:
            context = gate2.context()
            first = await call(gate2, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
            assert first.error_type == CODE_REQUIRED
            assert (
                first.data["confirmation"]["summary"]
                == "用 8 个 oak_planks 在 (100, 64, 100) 的 Crafting Table 制作 1 个 chest"
            )
            done = await call(gate2, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
            assert done.success is True, done.error
            assert done.data["status"] == "RUNNING"
            assert gate2.service.action_calls("craft") == [
                {"recipe_id": CHEST_ID, "crafting_table": TABLE}
            ]


async def test_fingerprint_covers_the_table_coordinates() -> None:
    """§十五/K：指纹包含工作台坐标 —— 同一配方、不同工作台是两次不同的授权。"""
    async with Gate() as gate:
        context = gate.context()
        await call(gate, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
        first = gate.bridge.confirmations.pending()
        assert len(first) == 1 and first[0].arguments == {
            "recipe_id": CHEST_ID,
            "crafting_table": TABLE,
        }
        hash_with_table = first[0].arguments_hash
        gate.bridge.confirmations.cancel(first[0].confirmation_id)

        await call(gate, {"recipe_id": CHEST_ID}, context)
        second = gate.bridge.confirmations.pending()
        assert second[0].arguments == {"recipe_id": CHEST_ID}, "没给工作台就只绑 recipe_id"
        assert second[0].arguments_hash != hash_with_table, "坐标不同 → 指纹必须不同"


async def test_changing_table_coordinates_creates_a_new_confirmation() -> None:
    """L：坐标变了 → mismatch + 按新坐标重挂一条（摘要也更新）。"""
    async with Gate() as gate:
        context = gate.context()
        await call(gate, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
        moved = {"x": 101, "y": 64, "z": 100}
        mismatched = await call(gate, {"recipe_id": CHEST_ID, "crafting_table": moved}, context)
        assert mismatched.error_type == CODE_MISMATCH
        assert gate.service.action_calls("craft") == [], "换个工作台就是不授权"
        repending = gate.bridge.confirmations.pending()
        assert len(repending) == 1 and repending[0].arguments["crafting_table"] == moved
        assert (
            repending[0].summary
            == "用 8 个 oak_planks 在 (101, 64, 100) 的 Crafting Table 制作 1 个 chest"
        )


async def test_table_vanishing_after_confirmation_is_reported() -> None:
    """M/N：确认后工作台被挖掉 / 被换掉 → 两个稳定错误码，绝不继续。"""
    cases = [
        (
            MinecraftCraftingTableMissing("那里没有工作台（100,64,100）"),
            "minecraft.crafting_table_missing",
        ),
        (
            MinecraftCraftingTableInvalid("那个位置不是工作台（chest）", detail={"block": "chest"}),
            "minecraft.crafting_table_invalid",
        ),
        (
            MinecraftCraftingTableTooFar("工作台距离 26.0 格", detail={"distance": 26.0}),
            "minecraft.crafting_table_too_far",
        ),
    ]
    for exc, expected_code in cases:
        async with Gate() as gate:
            context = gate.context()
            gate.service.enqueue("craft", exc)
            await call(gate, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
            result = await call(gate, {"recipe_id": CHEST_ID, "crafting_table": TABLE}, context)
            assert result.error_type == expected_code, (exc, result.error_type)
            assert "Traceback" not in str(result.data)


async def test_3x3_craft_is_exclusive_like_2x2() -> None:
    """Q：带上工作台的合成同样是 exclusive（与 equip / inventory_move / container 互斥）。"""
    from app.integrations.minecraft.events import parse_bridge_event

    for busy_action in ("equip", "inventory_move", "container_transfer", "dig", "place", "move_to"):
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
            blocked = await call(gate, {"recipe_id": CHEST_ID, "crafting_table": TABLE})
            assert blocked.error_type == "minecraft.action_busy", busy_action


async def test_3x3_activity_and_verification_payload_are_honest() -> None:
    """O/P：完成事件带工作台坐标与 before/after（事实全部来自重读）。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.completed",
                    "session_id": "s1",
                    "timestamp": 2.0,
                    "action": "craft",
                    "action_id": "act_craft_1",
                    "status": "SUCCEEDED",
                    "result": {
                        "recipe_id": CHEST_ID,
                        "crafting_table": TABLE,
                        "item": "chest",
                        "result": {"name": "chest", "count_per_craft": 1, "crafted_count": 1},
                        "ingredients": [{"name": "oak_planks", "consumed": 8}],
                        "before": {"result_count": 0, "ingredient_counts": {"oak_planks": 8}},
                        "after": {"result_count": 1, "ingredient_counts": {"oak_planks": 0}},
                    },
                }
            )
        )
        assert gate.bridge.context.activity == "刚做好了 1 个 chest"
        assert (gate.bridge.context.last_action or {})["action"] == "craft"


def test_validate_craft_pure_function() -> None:
    assert MinecraftService.validate_craft(f" {STICK_ID} ") == (STICK_ID, None)
    assert MinecraftService.validate_craft("!chest*1=oak_planks*8") == (
        "!chest*1=oak_planks*8",
        None,
    )
    for bad in ("", "   ", "abc", "abc=def", 7, None, "x" * 220, "bad\x01=1"):
        try:
            MinecraftService.validate_craft(bad)
        except MinecraftActionInvalid:
            pass
        else:  # pragma: no cover - 非法输入必须被拒
            raise AssertionError(f"应当拒绝：{bad!r}")
