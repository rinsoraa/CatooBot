"""意图传播完整性（Phase 3E.1 §十二-§十九）：回合来源 → 工具上下文 → Minecraft 策略门。

Phase 3E 的 Policy 本身是对的，但它拿到的事实是错的：`_tool_context()` 无条件写
``minecraft_explicit_intent = True``，于是**主动发言**（compose_initiative）与后台生成
也带着"用户明确要求"的标记 → LOW 动作（move_to / follow_player）会被放行。

这里全部走**真实链路**：`CharacterRuntime.respond/compose_initiative` → `_generate`
→ `_tool_context` → 真实 `ToolRuntime`（schema/预算/循环守卫）→ `MinecraftActionPolicy`
→ 假 MinecraftService（记录每一次动作调用）。不直接构造 Policy 假事实。
"""

from __future__ import annotations

import json
from typing import Any

from app.ai.engine import AIEngine
from app.character.persona_manager import PersonaManager
from app.character.runtime import CharacterRuntime
from app.character.turn import TurnOrigin
from app.config.settings import AIConfig, CharacterConfig, DatabaseConfig, ToolsConfig
from app.database.database import Database
from app.integrations.minecraft.agent import (
    BRIDGE_KEY,
    INTENT_KEY,
    TURN_ORIGIN_KEY,
    MinecraftAgentBridge,
)
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_tools import FakeMinecraftService

# ------------------------------------------------------------------ 测试台


def decide(tool: str, **arguments: Any) -> str:
    return json.dumps(
        {"tool_call": {"name": tool, "arguments": arguments, "reason": "用户要求"}},
        ensure_ascii=False,
    )


class Stack:
    """真实 CharacterRuntime + 真实 ToolRuntime + 假 MinecraftService。"""

    def __init__(self, tmp_path: Any, script: list[str], service: FakeMinecraftService) -> None:
        self.tmp_path = tmp_path
        self.script = script
        self.service = service

    async def __aenter__(self) -> Stack:
        self.db = Database(DatabaseConfig(url=f"sqlite:///{self.tmp_path / 'intent.db'}"))
        await self.db.connect()
        self.provider = MockAIProvider(behaviors={"A": list(self.script)})
        self.engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            self.db,
            providers={"mock": self.provider},
        )
        self.personas = PersonaManager(CharacterConfig(identity={"name": "罐头"}), self.db)
        self.runtime = CharacterRuntime(
            self.personas, self.engine, memory_manager=None, database=self.db
        )
        await self.runtime.start()
        self.tools = ToolRuntime(
            ToolsConfig(enabled=True, decision_mode="json", max_calls_per_turn=3)
        )
        await self.tools.start()
        self.runtime.tools = self.tools
        self.bridge = MinecraftAgentBridge(self.service)
        self.runtime.minecraft_agent = self.bridge
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        await self.tools.close()
        await self.db.close()
        return False

    # --- 便捷断言 -----------------------------------------------------------

    @property
    def tool_verdicts(self) -> list[str]:
        """本轮工具调用的判定结果（ToolTrace.error_type；成功为空串）。"""
        return [trace.error_type for trace in self.tools.executor.traces]

    def last_tool_prompt(self) -> str:
        """模型在最后一轮看到的内容（工具结果会以"外部参考数据"回灌）。"""
        return self.provider.calls[-1]["last_user"]


def stack(tmp_path: Any, *script: str, service: FakeMinecraftService | None = None) -> Stack:
    return Stack(tmp_path, list(script), service or FakeMinecraftService())


# ------------------------------------------------- §十三 User Turn（允许）


async def test_character_user_turn_allows_low_minecraft_action(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=126, y=64, z=-228),
        "我这就过去～",
        service=service,
    ) as env:
        reply = await env.runtime.respond(
            "private:10001", 10001, "罐头你过来", turn_origin=TurnOrigin.USER
        )
    assert reply == "我这就过去～"
    assert service.action_calls("move_to") == [{"x": 126, "y": 64, "z": -228}]
    assert env.tool_verdicts == [""], "用户回合里 move_to 必须放行"


# --------------------------------- §十四/§十八 Initiative Turn（必须拒绝）


async def test_character_initiative_turn_rejects_low_minecraft_action(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=126, y=64, z=-228),
        "（自己嘀咕了一句）",
        service=service,
    ) as env:
        text = await env.runtime.compose_initiative(
            session_id="private:10001",
            user_id=10001,
            reason="unfinished_topic",
            topic="上次没聊完的事",
        )
    assert text == "（自己嘀咕了一句）"
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert service.calls == [], "主动发言回合绝不能产生任何 Minecraft 动作"
    # 模型也被告知这次不行（工具结果原样回灌）
    assert "这需要用户明确要求" in env.last_tool_prompt()


async def test_initiative_cannot_execute_move_to(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path, decide("minecraft_move_to", x=1, y=2, z=3), "嗯", service=service
    ) as env:
        await env.runtime.compose_initiative(
            session_id="private:10001", user_id=10001, reason="long_absence"
        )
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert service.action_calls("move_to") == []


async def test_initiative_cannot_execute_follow_player(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_follow_player", username="空凛"),
        "嗯",
        service=service,
    ) as env:
        await env.runtime.compose_initiative(
            session_id="private:10001", user_id=10001, reason="long_absence"
        )
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert service.action_calls("follow_player") == []


async def test_compose_initiative_declares_the_initiative_origin(tmp_path: Any) -> None:
    """链路证明：主动发言确实把 INITIATIVE 传到 _tool_context（不是靠默认值）。"""
    seen: list[TurnOrigin] = []
    service = FakeMinecraftService()
    async with stack(tmp_path, "嗯，最近还好。", service=service) as env:
        original = env.runtime._tool_context  # noqa: SLF001

        def spy(*args: Any, **kwargs: Any) -> Any:
            seen.append(kwargs["turn_origin"])
            return original(*args, **kwargs)

        env.runtime._tool_context = spy  # type: ignore[method-assign]  # noqa: SLF001
        await env.runtime.compose_initiative(
            session_id="private:10001", user_id=10001, reason="long_absence"
        )
    assert seen == [TurnOrigin.INITIATIVE]


# ------------------------------------------------- §十五 Background Turn


async def test_character_background_turn_rejects_low_minecraft_action(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_follow_player", username="空凛"),
        "（后台重放的一句话）",
        service=service,
    ) as env:
        reply = await env.runtime.respond(
            "private:10001", 10001, "罐头跟着我", turn_origin=TurnOrigin.BACKGROUND
        )
    assert reply == "（后台重放的一句话）"
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert service.calls == []


# ----------------------------------------------------- §十六 System Turn


async def test_character_system_turn_rejects_low_minecraft_action(tmp_path: Any) -> None:
    service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=1, y=2, z=3),
        decide("minecraft_follow_player", username="空凛"),
        "（系统回合）",
        service=service,
    ) as env:
        # 正文看起来像用户在要求（关键词命中候选），但来源是系统回合 → 仍然拒绝
        reply = await env.runtime.respond(
            "private:10001", 10001, "罐头你过来", turn_origin=TurnOrigin.SYSTEM
        )
    assert reply == "（系统回合）"
    assert env.tool_verdicts == ["minecraft.action_not_allowed", "minecraft.action_not_allowed"]
    assert service.calls == []


async def test_safe_tools_stay_available_in_non_user_turns(tmp_path: Any) -> None:
    """SAFE 工具不受意图门影响：后台/系统回合照样能看世界、能停止。"""
    for origin in (TurnOrigin.BACKGROUND, TurnOrigin.SYSTEM, TurnOrigin.INITIATIVE):
        service = FakeMinecraftService()
        async with stack(
            tmp_path,
            decide("minecraft_world"),
            decide("minecraft_stop"),
            "看完了",
            service=service,
        ) as env:
            reply = await env.runtime.respond(
                "private:10001", 10001, "你那边有什么？", turn_origin=origin
            )
        assert reply == "看完了"
        assert env.tool_verdicts == ["", ""], (origin, env.tool_verdicts)
        assert service.action_calls("stop")


# --------------------------------------------- §二 字符串不可推断来源


async def test_turn_origin_is_not_inferred_from_text(tmp_path: Any) -> None:
    """§二：不许用 user_text 判断来源——同样的文本在不同回合里结论不同。"""
    # 用户回合里正文写着"（主动发起）"：仍然是用户回合 → LOW 放行
    user_service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=5, y=64, z=5),
        "好",
        service=user_service,
    ) as env:
        await env.runtime.respond(
            "private:10001", 10001, "（主动发起）", turn_origin=TurnOrigin.USER
        )
    assert env.tool_verdicts == [""]
    assert user_service.action_calls("move_to") == [{"x": 5, "y": 64, "z": 5}]

    # 主动发言回合里正文写着"罐头你过来"：仍然是主动发言 → LOW 拒绝
    initiative_service = FakeMinecraftService()
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=5, y=64, z=5),
        "嗯",
        service=initiative_service,
    ) as env:
        await env.runtime.respond(
            "private:10001",
            10001,
            "罐头你过来",
            turn_origin=TurnOrigin.INITIATIVE,
            record_interaction=False,
        )
    assert env.tool_verdicts == ["minecraft.action_not_allowed"]
    assert initiative_service.action_calls("move_to") == []


async def test_tool_context_derives_intent_from_origin(tmp_path: Any) -> None:
    """传播链的原子事实：四个来源 → 两个元数据键（意图 = origin 派生）。"""
    async with stack(tmp_path, "嗯") as env:
        for origin, expected in (
            (TurnOrigin.USER, True),
            (TurnOrigin.INITIATIVE, False),
            (TurnOrigin.BACKGROUND, False),
            (TurnOrigin.SYSTEM, False),
        ):
            context = env.runtime._tool_context(  # noqa: SLF001
                session_id="private:10001",
                user_id=10001,
                time_context=None,
                is_group=False,
                turn_origin=origin,
            )
            assert context.metadata[INTENT_KEY] is expected, origin
            assert context.metadata[TURN_ORIGIN_KEY] == origin.value
            assert context.metadata[BRIDGE_KEY] is env.bridge


async def test_respond_requires_an_explicit_turn_origin(tmp_path: Any) -> None:
    """§九：没有默认值可依赖——来源必须显式声明（漏了就直接 TypeError）。"""
    import inspect

    signature = inspect.signature(CharacterRuntime.respond)
    parameter = signature.parameters["turn_origin"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY

    generate = inspect.signature(CharacterRuntime._generate)  # noqa: SLF001
    assert generate.parameters["turn_origin"].default is inspect.Parameter.empty
    tool_context = inspect.signature(CharacterRuntime._tool_context)  # noqa: SLF001
    assert tool_context.parameters["turn_origin"].default is inspect.Parameter.empty


# ------------------------------------------------ §十九 管理台干跑（SYSTEM）


class _AdminBot:
    """ToolAdminService 需要的最小 Bot 面（干跑路径是鸭子类型的）。"""

    def __init__(self, tools: ToolRuntime, bridge: MinecraftAgentBridge) -> None:
        from types import SimpleNamespace

        self.tools = tools
        self.minecraft = SimpleNamespace(agent=bridge)
        self.character = SimpleNamespace(
            personas=SimpleNamespace(persona=SimpleNamespace(identity=SimpleNamespace(name="罐头")))
        )
        self.config = SimpleNamespace(
            tools=SimpleNamespace(max_execution_time=30.0),
            character=SimpleNamespace(timezone="Asia/Shanghai"),
        )


async def test_webui_dry_run_is_a_system_turn(tmp_path: Any) -> None:
    """§十九：管理台干跑不得手写意图标记——LOW 被拒，SAFE 照常，并说明原因。"""
    from app.web.services.tools import ToolAdminService

    service = FakeMinecraftService()
    async with stack(tmp_path, "嗯", service=service) as env:
        admin = ToolAdminService(_AdminBot(env.tools, env.bridge))  # type: ignore[arg-type]

        moved = await admin.test_tool("minecraft_move_to", {"x": 1, "y": 2, "z": 3})
        assert moved["ok"] is False
        assert moved["result"]["error_type"] == "minecraft.action_not_allowed"
        assert "系统回合" in moved["note"]

        world = await admin.test_tool("minecraft_world", {})
        assert world["ok"] is True

        stopped = await admin.test_tool("minecraft_stop", {})
        assert stopped["ok"] is True
    assert service.action_calls("move_to") == []
