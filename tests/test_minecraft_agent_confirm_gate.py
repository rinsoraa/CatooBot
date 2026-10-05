"""确认门的真实工具链路（Phase 4A 建立，Phase 4B 起跑在真实的 minecraft_dig 上）。

链路与生产完全一致：ToolRuntime（schema/预算/循环守卫）→ MinecraftActionPolicy
（注册/启用/在线/风险开关/USER 回合/可信/忙/确认）→ 消费确认 → MinecraftService
（这里是假 Service，记录每一次动作调用）。只有 Service 与模型是替身。

Phase 4B 之后 `minecraft_dig` 是**生产** Tool（MEDIUM），所以确认流程、参数指纹、
一次性、过期、可信门全部对真实动作验证。
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.engine import AIEngine
from app.ai.models import ChatMessage
from app.config.settings import AIConfig, MinecraftConfig, ToolsConfig
from app.integrations.minecraft.agent import (
    ACTION_RISK,
    BRIDGE_KEY,
    INTENT_KEY,
    PLAYER_KEY,
    TURN_ORIGIN_KEY,
    MinecraftAgentBridge,
)
from app.integrations.minecraft.confirmation import (
    CODE_MISMATCH,
    CODE_NOT_TRUSTED,
    CODE_REQUIRED,
    CONFIRMATION_RISKS,
)
from app.integrations.minecraft.service import MinecraftBlockChanged
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext, ToolResult
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_tools import FakeMinecraftService

#: dig 的完整参数（Phase 4B 起 expected_block 必填）
DIG_ARGS = {"x": 120, "y": 64, "z": -230, "expected_block": "minecraft:stone"}


def decide(tool: str, **arguments: Any) -> str:
    return json.dumps(
        {"tool_call": {"name": tool, "arguments": arguments, "reason": "用户要求"}},
        ensure_ascii=False,
    )


class Gate:
    """bridge + 真实 ToolRuntime（含真实 minecraft_dig）+ 假 Service。"""

    def __init__(self, *, allow_medium: bool = True, trusted: list[str] | None = None) -> None:
        self.service = FakeMinecraftService()
        self.service.config = MinecraftConfig(
            enabled=True,
            auto_start_runtime=False,
            agent={
                "tools": {"enabled": True, "allow_medium": allow_medium},
                "trusted_players": trusted or ["空凛"],
            },
        )
        self.bridge = MinecraftAgentBridge(self.service)

    async def __aenter__(self) -> Gate:
        self.runtime = ToolRuntime(ToolsConfig(enabled=True))
        await self.runtime.start()
        self.executor = ToolExecutor(
            ToolsConfig(enabled=True), self.runtime.registry, self.runtime.policy
        )
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        await self.runtime.close()
        return False

    # --- 调用 -------------------------------------------------------------

    def context(
        self, *, origin: str = "user", player: str = "", session: str = "private:10001"
    ) -> ToolContext:
        from app.character.turn import TurnOrigin

        turn = TurnOrigin(origin)
        return ToolContext(
            user_id="10001",
            session_id=session,
            metadata={
                BRIDGE_KEY: self.bridge,
                TURN_ORIGIN_KEY: turn.value,
                INTENT_KEY: turn.is_user,
                **({PLAYER_KEY: player} if player else {}),
            },
        )

    async def call(self, tool: str, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        return await self.executor.execute(
            ToolCall(name=tool, arguments=arguments), context, TurnBudget()
        )

    @property
    def dig_calls(self) -> list[dict[str, Any]]:
        return self.service.action_calls("dig")


# ------------------------------------------------ 需要确认 → 用户确认 → 执行


async def test_medium_action_requires_confirmation_then_runs_on_user_confirm() -> None:
    async with Gate() as gate:
        user = gate.context()
        first = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert first.success is False
        assert first.error_type == CODE_REQUIRED
        assert gate.dig_calls == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_dig" and pending["risk"] == "MEDIUM"
        assert "minecraft:stone" in pending["summary"], "摘要要说清挖哪个方块"
        assert gate.bridge.confirmations.pending(), "同时挂起一条 PENDING"

        # 下一轮用户说「确认」= 又一个 USER 回合，模型用相同参数重新调用
        second = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert second.success is True, second.error
        assert second.data["status"] == "RUNNING" and second.data["action_id"] == "act_dig_1"
        assert gate.dig_calls == [dict(DIG_ARGS)], "确认后被消费，Service 真的收到 dig"
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        # 第三次：没有待确认 → 又要确认（不会因为刚执行过就放开）
        third = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert third.error_type == CODE_REQUIRED
        assert len(gate.dig_calls) == 1


async def test_confirmation_is_not_created_for_non_user_turns() -> None:
    """§十一/§十二：主动发言/后台/系统回合连「待确认」都不该产生。"""
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig_calls == []


async def test_non_user_turn_cannot_consume_a_pending_confirmation() -> None:
    """§十一：PENDING 存在，但这一刻不是用户回合 → 不执行。"""
    async with Gate() as gate:
        created = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert created.error_type == CODE_REQUIRED
        for origin in ("initiative", "background", "system"):
            result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.dig_calls == []
        # 用户回来确认依然有效
        done = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert done.success is True
        assert len(gate.dig_calls) == 1


async def test_untrusted_minecraft_player_cannot_dig() -> None:
    """§三十四：游戏内不可信玩家请求挖方块 → 拒绝，且连确认都不产生。"""
    async with Gate() as gate:
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(player="Steve"))
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig_calls == []


# ------------------------------------------------ 参数不可变（§七/§二十八）


async def test_changed_coordinates_do_not_reuse_the_confirmation() -> None:
    """坐标变了就不是同一个动作：作废旧确认、按新参数重挂。"""
    async with Gate() as gate:
        first = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert first.error_type == CODE_REQUIRED
        other = {"x": 121, "y": 64, "z": -230, "expected_block": "minecraft:stone"}
        mismatched = await gate.call("minecraft_dig", other, gate.context())
        assert mismatched.error_type == CODE_MISMATCH
        assert gate.dig_calls == [], "被确认过的参数绝不执行"
        fresh = gate.bridge.confirmations.pending()
        assert len(fresh) == 1, "旧确认作废、按新参数重挂一条"
        assert fresh[0].arguments["x"] == 121, "新确认记的是新参数"
        done = await gate.call("minecraft_dig", other, gate.context())
        assert done.success is True
        assert gate.dig_calls == [other]


async def test_changed_expected_block_is_a_mismatch() -> None:
    """§七/§二十七：用户确认的是「这里的 stone」，不是「这里现在的东西」。"""
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        swapped = {**DIG_ARGS, "expected_block": "minecraft:diamond_ore"}
        result = await gate.call("minecraft_dig", swapped, gate.context())
        assert result.error_type == CODE_MISMATCH
        assert gate.dig_calls == []


async def test_confirmation_is_bound_to_session() -> None:
    """§六：确认不跨会话——另一个会话的同名请求要自己确认一次。"""
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context(session="private:10001"))
        crossed = await gate.call(
            "minecraft_dig", DIG_ARGS, gate.context(session="minecraft:127.0.0.1:25565:空凛")
        )
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权，只能重新发起"
        assert gate.dig_calls == []
        originals = [
            item
            for item in gate.bridge.confirmations.pending()
            if item.session_id == "private:10001"
        ]
        assert len(originals) == 1


async def test_confirmation_expired_cannot_execute() -> None:
    """§八：TTL 到点即失效——过期后必须重新确认，且过期的那条不能执行。"""
    async with Gate() as gate:
        clock = {"now": 1000.0}
        gate.bridge.confirmations._clock = lambda: clock["now"]  # noqa: SLF001
        first = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert first.error_type == CODE_REQUIRED
        clock["now"] += 61.0
        after = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert after.error_type == "minecraft.confirmation_expired"
        assert gate.dig_calls == []
        fresh = gate.bridge.confirmations.pending()
        assert len(fresh) == 1, "过期后重新挂了一条（用户可以再确认）"
        expired = gate.bridge.confirmations.get(first.data["confirmation"]["confirmation_id"])
        assert expired is not None and expired.status == "EXPIRED"


async def test_confirmation_consumed_once_across_calls() -> None:
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        first = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert first.success is True
        second = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert second.error_type == CODE_REQUIRED  # 需要新的一次确认，不是复用
        assert len(gate.dig_calls) == 1


# --------------------------------------------------------- 其它门先于确认


async def test_medium_risk_flag_off_blocks_before_confirmation() -> None:
    """§六十 顺序：风险开关先于确认——关掉 allow_medium 时连确认都不创建。"""
    async with Gate(allow_medium=False) as gate:
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig_calls == []


async def test_medium_action_is_rejected_when_offline() -> None:
    """在线门也在确认之前。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


async def test_dig_is_exclusive_while_something_is_running() -> None:
    """§三十二：dig 与 move_to/follow_player 互斥——不能边挖边走。"""
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
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.action_busy"
        # chat / world / stop 不受影响（非独占）
        assert (await gate.call("minecraft_world", {}, gate.context())).success is True
        assert (await gate.call("minecraft_stop", {}, gate.context())).success is True


# --------------------------------------------------- 可信玩家门（§二十-§二十二）


async def test_untrusted_minecraft_player_cannot_move_her() -> None:
    async with Gate() as gate:
        result = await gate.call(
            "minecraft_move_to",
            {"x": 126, "y": 64, "z": -228},
            gate.context(player="Steve"),
        )
        assert result.error_type == CODE_NOT_TRUSTED
        assert gate.service.calls == [], "不可信玩家的 LOW 动作绝不产生 Minecraft 调用"


async def test_trusted_minecraft_player_can_move_her() -> None:
    async with Gate() as gate:
        result = await gate.call(
            "minecraft_move_to",
            {"x": 126, "y": 64, "z": -228},
            gate.context(player="空凛"),
        )
        assert result.success is True
        assert gate.service.action_calls("move_to") == [{"x": 126, "y": 64, "z": -228}]


async def test_safe_tools_work_for_untrusted_players() -> None:
    async with Gate() as gate:
        world = await gate.call("minecraft_world", {}, gate.context(player="Steve"))
        stop = await gate.call("minecraft_stop", {}, gate.context(player="Steve"))
        assert world.success is True and stop.success is True


async def test_qq_turns_are_not_subject_to_the_trusted_list() -> None:
    """§二十六：QQ/WebUI 用户不受 trusted_players 约束（他们的身份体系已经明确）。"""
    async with Gate(trusted=[]) as gate:
        result = await gate.call(
            "minecraft_move_to", {"x": 126, "y": 64, "z": -228}, gate.context()
        )
        assert result.success is True


# ------------------------------------------------ 不自动连续挖（§六十五）


async def test_dig_does_not_chain_into_second_block() -> None:
    """一个 Tool Call = 最多一个方块：同一回合里第二块只会得到「需要确认」。"""
    async with Gate() as gate:
        user = gate.context()
        # 第一块：先建确认，再在下一轮确认 → 执行
        await gate.call("minecraft_dig", DIG_ARGS, user)
        await gate.call("minecraft_dig", DIG_ARGS, user)
        assert len(gate.dig_calls) == 1

        # 同一回合里模型又想去挖第二块（不同坐标）→ 只会得到新的「需要确认」，不执行
        second_block = {"x": 127, "y": 64, "z": -230, "expected_block": "minecraft:stone"}
        chained = await gate.call("minecraft_dig", second_block, user)
        assert chained.error_type == CODE_REQUIRED
        assert len(gate.dig_calls) == 1, "第二个方块必须重新走确认，绝不连着挖"


async def test_initiative_turn_cannot_dig_and_creates_no_confirmation() -> None:
    """§六十六：她自己想挖也不行——连确认都不该产生。"""
    async with Gate() as gate:
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin="initiative"))
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig_calls == []


# --------------------------------------------------- 生产安全（§二/§六十八）


async def test_production_registry_has_exactly_the_approved_tools() -> None:
    """§六十八：正式生产 Tool 只有这九个；attack/craft/inventory-mutation 等一律不存在。"""
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    minecraft_tools = [name for name in runtime.registry.names() if name.startswith("minecraft_")]
    assert minecraft_tools == [
        "minecraft_chat",
        "minecraft_dig",
        "minecraft_follow_player",
        "minecraft_inventory",
        "minecraft_look_at",
        "minecraft_move_to",
        "minecraft_place",
        "minecraft_stop",
        "minecraft_world",
    ]
    # minecraft_inventory 是**只读**切片（Phase 4C 合法）；这里禁的是"改背包"类动作：
    # 自动装备、移动物品、容器、连续挖掘/采集等都还没有实现。
    for forbidden in (
        "minecraft_attack",
        "minecraft_craft",
        "minecraft_eat",
        "minecraft_equip",
        "minecraft_inventory_move",
        "minecraft_container",
        "minecraft_mine",
        "minecraft_collect",
        "minecraft_test_confirmation",
    ):
        assert runtime.registry.maybe_get(forbidden) is None, forbidden
        assert forbidden not in ACTION_RISK, forbidden
    await runtime.close()


async def test_risk_table_matches_the_production_tools() -> None:
    assert set(ACTION_RISK) == {
        "minecraft_world",
        "minecraft_chat",
        "minecraft_look_at",
        "minecraft_stop",
        "minecraft_move_to",
        "minecraft_follow_player",
        "minecraft_dig",
        "minecraft_inventory",
        "minecraft_place",
    }
    assert ACTION_RISK["minecraft_dig"] == "MEDIUM"
    assert ACTION_RISK["minecraft_place"] == "MEDIUM"
    assert ACTION_RISK["minecraft_inventory"] == "SAFE"
    assert "-".join(sorted(CONFIRMATION_RISKS)) == "DESTRUCTIVE-HIGH-MEDIUM"


def test_risk_table_is_injectable_not_mutated() -> None:
    """注入风险表不得污染全局表（生产 ACTION_RISK 保持九个工具）。"""
    injected = {**ACTION_RISK, "minecraft_test_medium": "MEDIUM"}
    bridge = MinecraftAgentBridge(FakeMinecraftService(), risk_table=injected)
    assert bridge.policy.risk_of("minecraft_test_medium") == "MEDIUM"
    assert "minecraft_test_medium" not in ACTION_RISK


async def test_block_changed_detail_reaches_the_tool_result() -> None:
    """§十四：block_changed 必须把 expected/actual 交给模型（结构化，不带原始异常文本）。"""
    async with Gate() as gate:
        gate.service.enqueue(
            "dig",
            MinecraftBlockChanged(
                "方块已经变了：期望 minecraft:stone，实际 minecraft:dirt",
                expected="minecraft:stone",
                actual="minecraft:dirt",
            ),
        )
        await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.block_changed"
        assert result.data["error"]["detail"] == {
            "expected": "minecraft:stone",
            "actual": "minecraft:dirt",
        }


# --------------------------------------------------- 确认摘要（§六十二）


def test_confirmation_summary_names_the_block_and_position() -> None:
    from app.integrations.minecraft.agent import _confirmation_summary

    summary = _confirmation_summary("minecraft_dig", "MEDIUM", DIG_ARGS)
    assert summary == "挖掉 minecraft:stone（(120, 64, -230)）"
    # 其它动作仍用通用预览
    assert _confirmation_summary("minecraft_move_to", "LOW", {"x": 1, "y": 2, "z": 3}).startswith(
        "minecraft_move_to（LOW）"
    )


async def test_orchestrator_turn_flow_creates_and_consumes() -> None:
    """整轮串联：第一轮 USER 请求 → 需要确认；下一轮 USER 说「确认」→ 执行。"""
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_dig", **DIG_ARGS),
                    "挖掉那块石头会真的改变世界，需要你确认一下。",
                    decide("minecraft_dig", **DIG_ARGS),
                    "好，我去挖。",
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
                [ChatMessage.user("把面前这个石头挖掉")],
                context=gate.context(),
                query="把面前这个石头挖掉",
            )
            assert first == "挖掉那块石头会真的改变世界，需要你确认一下。"
            assert gate.dig_calls == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，我去挖。"
            assert gate.dig_calls == [dict(DIG_ARGS)]
        finally:
            await runtime.close()
