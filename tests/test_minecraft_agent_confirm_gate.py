"""确认门的真实工具链路（Phase 4A §十/§十一/§十四/§十五）。

本阶段**没有任何 MEDIUM/HIGH 动作**（dig/place/attack 一律不注册，§二），所以确认
基础设施用一个**只在测试里注册**的桩动作（`minecraft_dig` + 注入的风险表）跑通：
生产注册表里没有这个名字（有安全测试断言）。

链路与生产完全一致：ToolRuntime（schema/预算）→ MinecraftActionPolicy（注册/启用/
在线/风险开关/USER 回合/可信/忙/确认）→ 消费确认 → MinecraftService（这里是假 Service）。
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
    bridge_from,
)
from app.integrations.minecraft.confirmation import (
    CODE_MISMATCH,
    CODE_NOT_TRUSTED,
    CODE_REQUIRED,
    CONFIRMATION_RISKS,
)
from app.tools.base import Tool as ToolBase
from app.tools.executor import ToolExecutor
from app.tools.models import ToolCall, ToolContext, ToolMetadata, ToolResult
from app.tools.policy import TurnBudget
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_tools import FakeMinecraftService

DIG_ARGS = {"x": 120, "y": 64, "z": -230}


def decide(tool: str, **arguments: Any) -> str:
    return json.dumps(
        {"tool_call": {"name": tool, "arguments": arguments, "reason": "用户要求"}},
        ensure_ascii=False,
    )


class DigStub(ToolBase):
    """测试桩：一个 MEDIUM 动作（Phase 4B 才会真的实现 dig）。

    通用 Tool Runtime 的风险词表是 low/medium/high，而默认只允许 low —— 这里声明
    ``risk_level="low"`` 只是为了通过**通用**门；真正的 Minecraft 风险等级来自
    注入的风险表（``MEDIUM``），由 ``MinecraftActionPolicy`` 判定并要求确认。
    """

    metadata = ToolMetadata(
        name="minecraft_dig",
        display_name="Minecraft Dig",
        description="测试桩：破坏指定坐标的方块（需要用户确认）。",
        version="0.0.0-test",
        category="system",
        risk_level="low",
        input_schema={
            "type": "object",
            "properties": {
                "x": {"type": "number"},
                "y": {"type": "number"},
                "z": {"type": "number"},
            },
            "required": ["x", "y", "z"],
            "additionalProperties": False,
        },
        timeout=5.0,
    )

    def __init__(self) -> None:
        super().__init__()
        #: 执行面被真正调用过的参数（= 动作确实发生了）
        self.calls: list[dict[str, Any]] = []

    async def execute(self, arguments: dict[str, Any], context: ToolContext) -> ToolResult:
        """与生产工具同形：经 bridge.invoke（判定 → 确认门 → 执行面）。"""
        bridge = bridge_from(context)
        if bridge is None:
            return self.failure(
                self.metadata.name, "Minecraft 连接层不可用", error_type="minecraft.disabled"
            )
        return await bridge.invoke(
            self.metadata.name,
            arguments,
            lambda service: self._perform(arguments),
            context=context,
        )

    async def _perform(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """Phase 4B 这里会是 ``service.dig`` → Action Runtime；测试桩只记录。"""
        self.calls.append(dict(arguments))
        return {"ok": True, "action": "dig", "status": "SUCCEEDED"}


RISK_TABLE = {**ACTION_RISK, "minecraft_dig": "MEDIUM"}


class Gate:
    """把 bridge + 真实 ToolRuntime + 桩动作装在一起。"""

    def __init__(self, *, allow_medium: bool = True, trusted: list[str] | None = None) -> None:
        self.service = FakeMinecraftService()
        config = MinecraftConfig(
            enabled=True,
            auto_start_runtime=False,
            agent={
                "tools": {"enabled": True, "allow_medium": allow_medium},
                "trusted_players": trusted or ["空凛"],
            },
        )
        self.service.config = config
        self.bridge = MinecraftAgentBridge(self.service, risk_table=RISK_TABLE)
        self.dig = DigStub()

    async def __aenter__(self) -> Gate:
        self.runtime = ToolRuntime(ToolsConfig(enabled=True))
        await self.runtime.start()
        self.runtime.registry.register(self.dig)
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

    async def call_twice(self, tool: str, arguments: dict[str, Any], context: ToolContext):
        first = await self.call(tool, arguments, context)
        second = await self.call(tool, arguments, context)
        return first, second


# ------------------------------------------------ 需要确认 → 用户确认 → 执行


async def test_medium_action_requires_confirmation_then_runs_on_user_confirm() -> None:
    async with Gate() as gate:
        user = gate.context()
        first = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert first.success is False
        assert first.error_type == CODE_REQUIRED
        assert gate.dig.calls == [], "需要确认时绝不执行"
        pending = first.data["confirmation"]
        assert pending["tool"] == "minecraft_dig" and pending["risk"] == "MEDIUM"
        assert gate.bridge.confirmations.pending(), "同时挂起一条 PENDING"

        # 下一轮用户说「确认」= 又一个 USER 回合，模型用相同参数重新调用
        second = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert second.success is True, second.error
        assert gate.dig.calls == [DIG_ARGS], "确认后被消费，执行面真的被调用"
        assert gate.bridge.confirmations.pending() == [], "一次性：已 CONSUMED"

        # 第三次：没有待确认 → 又要确认（不会因为刚执行过就放开）
        third = await gate.call("minecraft_dig", DIG_ARGS, user)
        assert third.error_type == CODE_REQUIRED
        assert gate.dig.calls == [DIG_ARGS]


async def test_confirmation_is_not_created_for_non_user_turns() -> None:
    """§十一/§十二：主动发言/后台/系统回合连"待确认"都不该产生。"""
    async with Gate() as gate:
        for origin in ("initiative", "background", "system"):
            result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig.calls == []


async def test_non_user_turn_cannot_consume_a_pending_confirmation() -> None:
    """§十一：PENDING 存在，但这一刻不是用户回合 → 不执行。"""
    async with Gate() as gate:
        created = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert created.error_type == CODE_REQUIRED
        for origin in ("initiative", "background", "system"):
            result = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin=origin))
            assert result.error_type == "minecraft.action_not_allowed", origin
        assert gate.dig.calls == []
        # 用户回来确认依然有效
        done = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert done.success is True


async def test_changed_arguments_do_not_reuse_the_confirmation() -> None:
    """§七：用户确认的是**哪一个**动作不可变 —— 参数变了就不是同一个动作。"""
    async with Gate() as gate:
        first = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert first.error_type == CODE_REQUIRED
        other = {"x": 300, "y": 64, "z": -500}
        mismatched = await gate.call("minecraft_dig", other, gate.context())
        assert mismatched.error_type == CODE_MISMATCH
        assert gate.dig.calls == [], "被确认过的参数绝不执行"
        # 旧确认作废、按新参数重新挂一条：用户对新动作再确认一次才会执行
        fresh = gate.bridge.confirmations.pending()
        assert len(fresh) == 1 and fresh[0].arguments_hash != first.data["confirmation"]
        done = await gate.call("minecraft_dig", other, gate.context())
        assert done.success is True
        assert gate.dig.calls == [other]


async def test_confirmation_is_bound_to_session() -> None:
    """§六：确认不跨会话——另一个会话的同名请求要自己确认一次。"""
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context(session="private:10001"))
        crossed = await gate.call(
            "minecraft_dig", DIG_ARGS, gate.context(session="minecraft:127.0.0.1:25565:空凛")
        )
        assert crossed.error_type == CODE_REQUIRED, "别的会话没有这条授权，只能重新发起"
        assert gate.dig.calls == []
        # 原会话那条 PENDING 没有被别人消费掉
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
        assert gate.dig.calls == []
        # 过期后重新挂了一条新的（用户可以再确认一次），旧的那条已 EXPIRED
        fresh = gate.bridge.confirmations.pending()
        assert len(fresh) == 1
        assert (
            gate.bridge.confirmations.get(first.data["confirmation"]["confirmation_id"]).status
            == "EXPIRED"
        )  # type: ignore[union-attr]


async def test_confirmation_consumed_once_across_calls() -> None:
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        first = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert first.success is True
        second = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert second.error_type == CODE_REQUIRED  # 需要新的一次确认，不是复用
        assert len(gate.dig.calls) == 1


async def test_medium_risk_flag_off_blocks_before_confirmation() -> None:
    """§三十三 顺序：风险开关先于确认——关掉 allow_medium 时连确认都不创建。"""
    async with Gate(allow_medium=False) as gate:
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.action_not_allowed"
        assert gate.bridge.confirmations.pending() == []
        assert gate.dig.calls == []


async def test_medium_action_is_rejected_when_offline() -> None:
    """在线门也在确认之前（本阶段 MEDIUM 动作同样要在线）。"""
    async with Gate() as gate:
        gate.service.view = {"available": True, "online": False, "semantic": None}
        result = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert result.error_type == "minecraft.offline"
        assert gate.bridge.confirmations.pending() == []


async def test_confirmation_never_leaks_execution_to_other_tools() -> None:
    """确认只对**同一个工具+同一组参数**有效：另一个 MEDIUM 工具不会沾光。"""
    async with Gate() as gate:
        await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        other = await gate.call("minecraft_move_to", {"x": 1, "y": 2, "z": 3}, gate.context())
        # move_to 是 LOW：走 USER 回合直接放行（与确认无关），但绝不执行 dig
        assert other.success is True
        assert gate.dig.calls == []


# --------------------------------------------------- 可信玩家门（§二十-§二十二）


async def test_untrusted_minecraft_player_cannot_move_her() -> None:
    async with Gate() as gate:
        result = await gate.call(
            "minecraft_move_to",
            {"x": 126, "y": 64, "z": -228},
            gate.context(player="Steve"),
        )
        assert result.error_type == CODE_NOT_TRUSTED
        assert result.data["error"]["code"] == CODE_NOT_TRUSTED
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


# --------------------------------------------------- 生产安全（§二）


async def test_production_registry_has_no_high_risk_action() -> None:
    """§二：本阶段不注册任何 dig/place/attack/craft，风险表里也没有这些名字。"""
    runtime = ToolRuntime(ToolsConfig(enabled=True))
    await runtime.start()
    forbidding = {"minecraft_dig", "minecraft_place", "minecraft_attack", "minecraft_craft"}
    assert forbidding & set(runtime.registry.names()) == set()
    assert forbidding & set(ACTION_RISK) == set()
    assert "-".join(sorted(CONFIRMATION_RISKS)) == "DESTRUCTIVE-HIGH-MEDIUM"
    await runtime.close()


async def test_llm_cannot_self_confirm_through_messages() -> None:
    """§十一：模型的助手消息/工具结果都不能当确认——只有新的 USER 回合算。"""
    async with Gate() as gate:
        created = await gate.call("minecraft_dig", DIG_ARGS, gate.context())
        assert created.error_type == CODE_REQUIRED
        # 模型说"我确认了"只是普通文本；再调用一次仍然是同一个 USER 回合吗？
        # 是 —— 同一回合内重复调用会被**一次性**语义挡住吗？不会：消费发生在确认回合，
        # 这里验证的是"模型自己的话不构成确认"，所以用非用户回合复查一次。
        blocked = await gate.call("minecraft_dig", DIG_ARGS, gate.context(origin="system"))
        assert blocked.error_type == "minecraft.action_not_allowed"
        assert gate.dig.calls == []


async def test_orchestrator_turn_flow_creates_and_consumes() -> None:
    """整轮串联：第一轮 USER 请求 → 需要确认；下一轮 USER 说「确认」→ 执行。"""
    async with Gate() as gate:
        provider = MockAIProvider(
            behaviors={
                "A": [
                    decide("minecraft_dig", **DIG_ARGS),
                    "这个动作会破坏方块，需要你确认一下。",
                    decide("minecraft_dig", **DIG_ARGS),
                    "好，挖掉了。",
                ]
            }
        )
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            providers={"mock": provider},
        )
        runtime = ToolRuntime(ToolsConfig(enabled=True, decision_mode="json", max_calls_per_turn=2))
        await runtime.start()
        runtime.registry.register(gate.dig)
        try:
            first, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("把面前这个方块挖掉")],
                context=gate.context(),
                query="把面前这个方块挖掉",
            )
            assert first == "这个动作会破坏方块，需要你确认一下。"
            assert gate.dig.calls == []
            assert gate.bridge.confirmations.pending()

            second, _ = await runtime.orchestrator.run(
                engine,
                [ChatMessage.user("确认")],
                context=gate.context(),
                query="确认",
            )
            assert second == "好，挖掉了。"
            assert gate.dig.calls == [DIG_ARGS]
        finally:
            await runtime.close()


def test_risk_table_is_injectable_not_mutated() -> None:
    """注入风险表不得污染全局表（生产 ACTION_RISK 保持六个工具）。"""
    assert "minecraft_dig" in RISK_TABLE
    assert "minecraft_dig" not in ACTION_RISK
    assert len(ACTION_RISK) == 6
