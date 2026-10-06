"""Phase 4I：minecraft_dig 的工具级契约 —— 可选 ``expected_tool``（工具感知）。

覆盖（任务书 §二十五 A–J）：

* A 旧参数（没有 expected_tool）→ 完全兼容，调用形状与 Phase 4B 一模一样
* B expected_tool 合法 → 规范化后进 Service
* C canonicalization：``stone_pickaxe`` / ``STONE_Pickaxe`` 都是 ``minecraft:stone_pickaxe``
* D expected_tool 非法（空 / 纯空白 / 类型不对 / 超长）→ reject，且**不挂待确认**
* E 主手拿了别的物品 → ``minecraft.held_item_changed``（detail 带 expected/actual）
* F 主手空着 → ``minecraft.held_item_missing``
* G 确认指纹包含 expected_tool（有没有工具是两条不同的确认）
* H 工具变了 → confirmation mismatch（旧确认作废、按新参数重挂）
* I 删掉 expected_tool → confirmation mismatch
* J Service / runtime 错误映射（``held.*`` → 4C 起的稳定码，本阶段不新增错误码）

关键安全性质（§二十七）：**背包里有那把镐也不会自动装备** —— 只有"当前主手是它"才允许挖。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.runtime_client import MinecraftRuntimeError
from app.integrations.minecraft.service import (
    MinecraftHeldItemChanged,
    MinecraftHeldItemMissing,
    _translate,
)
from app.tools.builtins.minecraft_dig import DIG_METADATA
from tests.test_minecraft_agent_confirm_gate import DIG_ARGS, Gate
from tests.test_minecraft_agent_tools import FakeMinecraftService

TOOL_ARGS: dict[str, Any] = {**DIG_ARGS, "expected_tool": "minecraft:stone_pickaxe"}
CANONICAL_TOOL_ARGS: dict[str, Any] = {**DIG_ARGS, "expected_tool": "minecraft:stone_pickaxe"}


def _runtime_error(code: str, status: int, detail: dict[str, Any] | None = None) -> Any:
    return MinecraftRuntimeError("测试用错误", code=code, status=status, detail=detail)


# ------------------------------------------------------------------ A：向后兼容


async def test_schema_keeps_expected_tool_optional() -> None:
    """A：expected_tool 是**可选**参数 —— 4B 的 schema 继续通过。"""
    schema = DIG_METADATA.input_schema
    assert schema["required"] == ["x", "y", "z", "expected_block"]
    assert set(schema["properties"]) == {"x", "y", "z", "expected_block", "expected_tool"}
    assert schema["additionalProperties"] is False
    assert schema["properties"]["expected_tool"]["type"] == "string"


async def test_legacy_arguments_call_the_service_exactly_like_phase_4b() -> None:
    """A：没有 expected_tool 时，Service 收到的参数与 4B **一个字节都不差**。"""
    async with Gate() as gate:
        user = gate.context()
        await gate.call("minecraft_dig", DIG_ARGS, user)  # 第一次 → 需要确认
        confirmed = await gate.call("minecraft_dig", DIG_ARGS, user)  # 用户确认后执行
        assert confirmed.success is True
        assert gate.dig_calls == [dict(DIG_ARGS)]


# ------------------------------------------------------------------ B / C


async def test_expected_tool_reaches_the_service() -> None:
    """B：expected_tool 合法 → 规范化后原样进 Service。"""
    async with Gate() as gate:
        user = gate.context()
        await gate.call("minecraft_dig", TOOL_ARGS, user)
        confirmed = await gate.call("minecraft_dig", TOOL_ARGS, user)
        assert confirmed.success is True, confirmed.error
        assert gate.dig_calls == [dict(CANONICAL_TOOL_ARGS)]


async def test_expected_tool_is_canonicalized_before_the_confirmation_gate() -> None:
    """C（§六）：``stone_pickaxe`` 与 ``minecraft:stone_pickaxe`` 是同一把工具。

    规范化必须发生在**进确认门之前**：模型第一次写 ``stone_pickaxe``、确认时写
    ``minecraft:stone_pickaxe``，仍然是同一个动作（否则用户已经确认过的授权会莫名 mismatch）。
    """
    async with Gate() as gate:
        user = gate.context()
        bare = {**DIG_ARGS, "expected_tool": "stone_pickaxe"}
        first = await gate.call("minecraft_dig", bare, user)
        assert first.success is False and first.error_type == "minecraft.confirmation_required"
        confirmed = await gate.call("minecraft_dig", TOOL_ARGS, user)
        assert confirmed.success is True, confirmed.error
        assert gate.dig_calls == [dict(CANONICAL_TOOL_ARGS)]


async def test_expected_tool_case_does_not_matter() -> None:
    """C：大小写不同也是同一把工具（Minecraft 的 item id 永远小写）。"""
    async with Gate() as gate:
        user = gate.context()
        await gate.call("minecraft_dig", {**DIG_ARGS, "expected_tool": "STONE_Pickaxe"}, user)
        confirmed = await gate.call("minecraft_dig", TOOL_ARGS, user)
        assert confirmed.success is True, confirmed.error
        assert gate.dig_calls == [dict(CANONICAL_TOOL_ARGS)]


# ------------------------------------------------------------------ D


@pytest.mark.parametrize("raw", ["", "   ", 42, ["stone_pickaxe"], "x" * 100])
async def test_invalid_expected_tool_is_rejected_without_a_pending_confirmation(raw: Any) -> None:
    """D：非法 expected_tool → invalid_arguments，且**不挂**待确认、不执行。"""
    async with Gate() as gate:
        user = gate.context()
        result = await gate.call("minecraft_dig", {**DIG_ARGS, "expected_tool": raw}, user)
        assert result.success is False
        assert result.error_type == "invalid_arguments", result.error_type
        assert gate.dig_calls == []
        assert gate.bridge.confirmations.pending() == [], "垃圾参数不该挂出待确认"


# ------------------------------------------------------------------ E / F / J


def test_held_item_mismatch_maps_to_the_stable_code() -> None:
    """E/J：``held.item_changed`` → ``minecraft.held_item_changed``（带 expected/actual）。"""
    translated = _translate(
        _runtime_error(
            "held.item_changed",
            409,
            {"expected": "minecraft:stone_pickaxe", "actual": "minecraft:dirt"},
        )
    )
    assert isinstance(translated, MinecraftHeldItemChanged)
    assert translated.code == "minecraft.held_item_changed"
    assert translated.status == 409
    assert translated.detail["expected"] == "minecraft:stone_pickaxe"
    assert translated.detail["actual"] == "minecraft:dirt"


def test_held_item_missing_maps_to_the_stable_code() -> None:
    """F/J：``held.item_missing`` → ``minecraft.held_item_missing``（4C 起就有的稳定码）。"""
    translated = _translate(
        _runtime_error("held.item_missing", 400, {"expected": "minecraft:stone_pickaxe"})
    )
    assert isinstance(translated, MinecraftHeldItemMissing)
    assert translated.code == "minecraft.held_item_missing"
    assert translated.status == 400


# ------------------------------------------------------------------ G / H / I


async def test_confirmation_fingerprint_binds_the_expected_tool() -> None:
    """G：同坐标 + 不同工具是**两条**确认（指纹包含 expected_tool）。"""
    async with Gate() as gate:
        user = gate.context()
        first = await gate.call("minecraft_dig", TOOL_ARGS, user)
        assert first.success is False
        assert first.data["confirmation"]["summary"].count("minecraft:stone_pickaxe") == 1
        pending_with_tool = gate.bridge.confirmations.find_pending(
            session_id="private:10001", user_id="10001", tool="minecraft_dig", arguments=TOOL_ARGS
        )
        assert pending_with_tool is not None
        assert (
            gate.bridge.confirmations.find_pending(
                session_id="private:10001",
                user_id="10001",
                tool="minecraft_dig",
                arguments=DIG_ARGS,
            )
            is None
        ), "没要求工具的确认不能授权'要石镐'的动作"


async def test_changing_the_tool_is_a_confirmation_mismatch() -> None:
    """H：确认时换一把镐 → mismatch；旧确认作废，按新参数重挂（不执行）。"""
    async with Gate() as gate:
        user = gate.context()
        await gate.call("minecraft_dig", TOOL_ARGS, user)
        other = {**DIG_ARGS, "expected_tool": "minecraft:iron_pickaxe"}
        mismatch = await gate.call("minecraft_dig", other, user)
        assert mismatch.success is False
        assert mismatch.error_type == "minecraft.confirmation_mismatch"
        assert gate.dig_calls == []
        assert (
            gate.bridge.confirmations.find_pending(
                session_id="private:10001", user_id="10001", tool="minecraft_dig", arguments=other
            )
            is not None
        ), "按新参数重新挂了 PENDING"


async def test_dropping_the_tool_is_a_confirmation_mismatch() -> None:
    """I：删掉 expected_tool 也 mismatch —— 不能拿"要石镐"的确认去空手挖。"""
    async with Gate() as gate:
        user = gate.context()
        await gate.call("minecraft_dig", TOOL_ARGS, user)
        dropped = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert dropped.success is False
        assert dropped.error_type == "minecraft.confirmation_mismatch"
        assert gate.dig_calls == []


async def test_summary_explains_tool_block_and_position() -> None:
    """§八：摘要要把工具 / 方块 / 位置三样都讲清楚（没工具时照旧）。"""
    async with Gate() as gate:
        with_tool = await gate.call("minecraft_dig", TOOL_ARGS, gate.context())
        summary = with_tool.data["confirmation"]["summary"]
        assert "minecraft:stone_pickaxe" in summary
        assert "minecraft:stone" in summary
        assert "120" in summary and "-230" in summary

        legacy = await gate.call("minecraft_dig", DIG_ARGS, gate.context(session="private:20002"))
        legacy_summary = legacy.data["confirmation"]["summary"]
        assert "minecraft:stone_pickaxe" not in legacy_summary
        assert "minecraft:stone" in legacy_summary


# ------------------------------------------------------------------ §二十七 关键安全门


async def test_dig_never_auto_equips_even_when_the_backpack_has_the_tool() -> None:
    """§二十七：背包里有石镐也绝不自动装备 —— dig 只调 dig，永远不调 equip。

    （"主手不是它 → 拒绝"由 runtime 在 start 里用实时 heldItem 判定，
    这一条证明工具层根本不会为了满足 expected_tool 去动 equip。）
    """
    calls: list[str] = []

    class RecordingService(FakeMinecraftService):
        async def equip(self, item: Any) -> dict[str, Any]:  # pragma: no cover - 不该被调用
            calls.append("equip")
            raise AssertionError("dig 绝不能自动装备工具")

        async def dig(
            self,
            x: Any,
            y: Any,
            z: Any,
            expected_block: Any,
            expected_tool: Any = None,
        ) -> dict[str, Any]:
            calls.append("dig")
            return await super().dig(x, y, z, expected_block, expected_tool)

    service = RecordingService()
    gate = Gate()
    gate.service = service
    # 只换 Service，保留 Gate 建好的 policy/配置（allow_medium=True）——
    # 重建 bridge 会退回默认配置（MEDIUM 被拒），那是测试脚手架的坑，不是被测行为。
    gate.bridge.service = service
    async with gate as live:
        user = live.context()
        first = await live.call("minecraft_dig", TOOL_ARGS, user)
        assert first.success is False
        confirmed = await live.call("minecraft_dig", TOOL_ARGS, user)
        assert confirmed.success is True
        assert live.service.action_calls("dig") == [dict(CANONICAL_TOOL_ARGS)]
    assert calls == ["dig"], f"只该发生 dig（得到 {calls}）"
