"""Phase 4K · minecraft_find_blocks 工具测试（A–K）。

覆盖：schema（block_names 数组 + 两个可选整数、无坐标起点）、规范化、未知方块名、
离线、SAFE（任何回合都能用 + 不产生确认）、非独占（前台动作跑着也能查）、
距离与条数边界、输出投影、不泄露 raw 数据、以及"只定位、绝不动手"。

真正的搜索（findBlocks 的 id 匹配 / 排序 / 截断）由
``minecraft_runtime/test/find_blocks.test.js`` 用真实方块表覆盖；真机整条资源链由
smoke 覆盖。这里测 Tool → Agent Bridge → Policy → Service。
"""

from __future__ import annotations

from typing import Any

from app.config.settings import ToolsConfig
from app.integrations.minecraft.service import (
    MinecraftActionInvalid,
    MinecraftBlockNameUnknown,
    MinecraftService,
    canonical_block_name,
)
from app.tools.builtins import MinecraftFindBlocksTool
from app.tools.runtime import ToolRuntime
from tests.test_minecraft_agent_confirm_gate import Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService

ARGS: dict[str, Any] = {"block_names": ["minecraft:oak_log"], "max_distance": 16, "max_results": 8}


async def call(gate: Gate, arguments: dict[str, Any] | None = None, context: Any = None) -> Any:
    return await gate.call("minecraft_find_blocks", arguments or ARGS, context or gate.context())


def sample(**overrides: Any) -> dict[str, Any]:
    view: dict[str, Any] = {
        "ok": True,
        "query": {"block_names": ["oak_log"], "max_distance": 16, "max_results": 8},
        "matches": [
            {
                "block": {"name": "oak_log"},
                "position": {"x": 103, "y": 64, "z": 141},
                "distance": {"goal_near": 5, "raw": 5.42},
            },
            {
                "block": {"name": "oak_log"},
                "position": {"x": 100, "y": 66, "z": 139},
                "distance": {"goal_near": 5, "raw": 5.9},
            },
        ],
        "truncated": False,
    }
    view.update(overrides)
    return view


# ------------------------------------------------------------------ A：schema


async def test_schema_is_block_names_plus_two_optional_ints() -> None:
    """A：`block_names` 必填（1~8 个字符串），`max_distance` / `max_results` 可选整数。"""
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    try:
        tool = runtime.registry.maybe_get("minecraft_find_blocks")
        assert tool is not None and isinstance(tool, MinecraftFindBlocksTool)
        schema = tool.metadata.input_schema
        assert set(schema["properties"]) == {"block_names", "max_distance", "max_results"}
        assert schema["required"] == ["block_names"]
        assert schema["additionalProperties"] is False
        names = schema["properties"]["block_names"]
        assert names["type"] == "array" and names["minItems"] == 1 and names["maxItems"] == 8
        assert names["items"] == {"type": "string", "minLength": 1}
        assert schema["properties"]["max_distance"]["maximum"] == 32
        assert schema["properties"]["max_results"]["maximum"] == 16
        # §六：不接受坐标起点（起点永远是罐头当前位置）
        for forbidden in ("x", "y", "z", "point", "origin"):
            assert forbidden not in schema["properties"], forbidden
    finally:
        await runtime.close()


async def test_invalid_arguments_are_rejected_before_the_service() -> None:
    """A：多余参数 / 空数组 / 9 个名字 / 越界数字都在 schema 层被拒。"""
    async with Gate() as gate:
        for bad in (
            {**ARGS, "x": 1},
            {"block_names": []},
            {"block_names": ["stone"] * 9},
            {"block_names": "oak_log"},
            {"block_names": ["stone"], "max_distance": 1000},
            {"block_names": ["stone"], "max_results": 99},
            {"block_names": ["stone"], "max_distance": 4.5},
        ):
            result = await call(gate, bad)
            assert result.success is False, bad
            assert result.error_type == "invalid_arguments", (bad, result.error_type)
        assert gate.service.action_calls("find_blocks") == []


# ------------------------------------------------------------------ B：规范化


async def test_block_names_are_canonicalized() -> None:
    """B：`minecraft:Iron_Ore` / `iron_ore` 都规范成同一个裸名，并去重。

    规范化发生在 **Service 层**（`validate_find_blocks`），所以这里直接测那个纯函数；
    Tool 层只负责把参数交给 Service（下面那条断言的是"确实只调了一次 Service"）。
    """
    assert canonical_block_name("minecraft:Iron_Ore") == "iron_ore"
    assert canonical_block_name("  oak_log  ") == "oak_log"
    names, distance, results = MinecraftService.validate_find_blocks(
        ["minecraft:oak_log", "OAK_LOG", "oak_log"], 8, None
    )
    assert names == ["oak_log"] and distance == 8 and results == 0
    async with Gate() as gate:
        result = await call(
            gate,
            {"block_names": ["minecraft:oak_log", "OAK_LOG", "oak_log"], "max_distance": 8},
        )
        assert result.success is True, result.error
        calls = gate.service.action_calls("find_blocks")
        assert len(calls) == 1 and calls[0]["max_distance"] == 8


async def test_omitted_bounds_use_the_config_defaults() -> None:
    """G/H：省略 max_distance / max_results 时用**配置默认值**（16 / 8）。

    默认值是**真 Service** 补的（假 Service 不做校验/兜底），所以这条走真实的
    `MinecraftService` + FakeRuntime，断言真正发给 runtime 的 HTTP body。
    """
    from tests.test_minecraft_service import FakeRuntime, make_config

    runtime = FakeRuntime()
    await runtime.start()
    try:
        service = MinecraftService(None, make_config(runtime))
        await service.find_blocks(["stone"])
        assert runtime.find_blocks_calls == [
            {"block_names": ["stone"], "max_distance": 16, "max_results": 8}
        ]
        # 显式给了就用给的（仍然在硬上限内）
        await service.find_blocks(["stone"], 32, 16)
        assert runtime.find_blocks_calls[-1] == {
            "block_names": ["stone"],
            "max_distance": 32,
            "max_results": 16,
        }
    finally:
        await service._cleanup()
        await runtime.stop()


# ------------------------------------------------------------------ C/D：未知方块名 / 离线


async def test_unknown_block_name_is_a_structured_error() -> None:
    """C（§二十二）：名字不认识 → minecraft.block_name_unknown（**不是**空结果）。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "find_blocks",
            MinecraftBlockNameUnknown("不认识的方块名：banana_ore", unknown=["banana_ore"]),
        )
        result = await call(gate, {"block_names": ["banana_ore"]})
        assert result.success is False
        assert result.error_type == "minecraft.block_name_unknown"
        assert result.data["error"]["detail"]["unknown"] == ["banana_ore"]


async def test_offline_is_rejected() -> None:
    """D：不在世界里 → minecraft.offline，且不查任何东西。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await call(gate)
        assert result.error_type == "minecraft.offline"
        assert gate.service.action_calls("find_blocks") == []


# ------------------------------------------------------------------ E/F：SAFE / 非独占


async def test_safe_needs_no_confirmation_in_any_turn() -> None:
    """E/F（§五十）：INITIATIVE / BACKGROUND / SYSTEM / USER 都能查，且不产生任何确认。"""
    async with Gate() as gate:
        for origin in ("user", "initiative", "background", "system"):
            result = await call(gate, context=gate.context(origin=origin))
            assert result.success is True, (origin, result.error)
        assert len(gate.service.action_calls("find_blocks")) == 4
        assert gate.bridge.confirmations.pending() == []


async def test_non_exclusive_while_a_foreground_action_runs() -> None:
    """K：前台动作（move_to）正在跑时，只读查询照样能执行。"""
    from app.integrations.minecraft.events import parse_bridge_event

    async with Gate() as gate:
        gate.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.started",
                    "session_id": "private:10001",
                    "timestamp": 1.0,
                    "action": "dig",
                    "action_id": "act_digging",
                }
            )
        )
        result = await call(gate)
        assert result.success is True, result.error
        assert len(gate.service.action_calls("find_blocks")) == 1


# ------------------------------------------------------------------ I/J：投影


async def test_output_projection_and_sorting_are_passed_through() -> None:
    """I：匹配条目的 block / position / distance 原样透传（含两种距离口径）。"""
    async with Gate() as gate:
        result = await call(gate)
        matches = result.data["result"]["matches"]
        assert len(matches) == 2
        assert set(matches[0]) == {"block", "position", "distance"}
        assert set(matches[0]["distance"]) == {"goal_near", "raw"}
        assert set(matches[0]["position"]) == {"x", "y", "z"}
        # §十一：排序是 runtime 的职责；这一层只如实透传（顺序与 Service 返回的一致）
        assert matches[0]["position"] == {"x": 103, "y": 64, "z": 141}


async def test_no_raw_data_is_leaked() -> None:
    """J：只有语义字段 —— 没有 raw Block / metadata / NBT / 内部 id / chunk。"""
    async with Gate() as gate:
        result = await call(gate)
        text = str(result.data)
        for forbidden in ("metadata", "nbt", "stateId", "chunk", "diggable", "hardness"):
            assert forbidden not in text, forbidden
        # §十五：不给推荐/最佳
        assert "recommended" not in text and "best" not in text and "optimal" not in text


async def test_empty_matches_is_a_normal_result() -> None:
    """§十三：范围内没有就是正常空结果（不是错误码）。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "find_blocks",
            {
                "status": "SUCCEEDED",
                "result": sample(matches=[], truncated=False),
            },
        )
        result = await call(gate)
        assert result.success is True
        assert result.data["result"]["matches"] == []
        assert result.data["result"]["truncated"] is False


async def test_truncated_flag_is_surfaced() -> None:
    """§十二：truncated 表示"被条数上限截断"，不是失败。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "find_blocks",
            {"status": "SUCCEEDED", "result": sample(truncated=True)},
        )
        result = await call(gate)
        assert result.success is True
        assert result.data["result"]["truncated"] is True


async def test_summary_is_readable() -> None:
    async with Gate() as gate:
        result = await call(gate)
        assert "oak_log" in result.summary
        assert "2" in result.summary


# ------------------------------------------------------------------ Service 层


def test_service_validator_bounds() -> None:
    """G/H：Service 层的边界校验（越界一律 MinecraftActionInvalid）。"""
    assert MinecraftService.validate_find_blocks(["minecraft:stone"], 32, 16) == (
        ["stone"],
        32,
        16,
    )
    assert MinecraftService.validate_find_blocks(["stone"], None, None) == (["stone"], 0, 0)
    for names, distance, results in (
        ([], None, None),
        ("stone", None, None),
        (["stone"] * 9, None, None),
        ([42], None, None),
        (["stone"], 0, None),
        (["stone"], 33, None),
        (["stone"], None, 0),
        (["stone"], None, 17),
        (["stone"], "16", None),
        (["stone"], None, 8.5),
    ):
        try:
            MinecraftService.validate_find_blocks(names, distance, results)
        except MinecraftActionInvalid:
            continue
        raise AssertionError(f"{names}/{distance}/{results} 应该被拒绝")


async def test_service_failure_is_translated() -> None:
    """错误映射：Service 里的稳定错误码原样交给模型。"""
    from app.integrations.minecraft.agent import BRIDGE_KEY, INTENT_KEY, MinecraftAgentBridge
    from app.integrations.minecraft.service import MinecraftActionFailed
    from app.tools.models import ToolContext

    service = FakeMinecraftService()
    service.enqueue("find_blocks", MinecraftActionFailed("runtime 侧失败"))
    context = ToolContext(
        user_id="10001",
        session_id="private:10001",
        metadata={BRIDGE_KEY: MinecraftAgentBridge(service), INTENT_KEY: True},
    )
    result = await MinecraftFindBlocksTool().execute(ARGS, context)
    assert result.success is False
    assert result.error_type == "minecraft.action_failed"
