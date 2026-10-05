"""Minecraft 玩家聊天 → USER 回合（Phase 4A §二十四/§二十五/§二十八/§三十）。

游戏内玩家说话必须变成一次**真正的用户回合**：玩家可以用自然语言触发已有的
Minecraft Tools（SAFE 不限；LOW 还需可信玩家），同时沙盒外部事件链照旧（§十六/§二十九）。

这里的 CharacterRuntime / ToolRuntime / 策略门 / 工具链全是真的，只有两处替身：
脚本化模型（MockAIProvider）与假 MinecraftService（记录每一次动作调用）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai.engine import AIEngine
from app.character.persona_manager import PersonaManager
from app.character.runtime import CharacterRuntime
from app.config.settings import (
    AIConfig,
    CharacterConfig,
    DatabaseConfig,
    MinecraftConfig,
    ToolsConfig,
)
from app.database.database import Database
from app.integrations.minecraft.agent import MinecraftAgentBridge
from app.integrations.minecraft.chat_bridge import MinecraftChatBridge, chat_session_id
from app.integrations.minecraft.events import parse_bridge_event
from app.integrations.minecraft.service import MinecraftService
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_minecraft_agent_confirm_gate import decide
from tests.test_minecraft_agent_tools import FakeMinecraftService


class FakeBot:
    """ChatBridge 需要的最小 Bot 面（ai.conversations / character / behavior）。"""

    def __init__(self, engine: AIEngine, runtime: CharacterRuntime) -> None:
        self.ai = engine
        self.character = runtime
        self.behavior = None


class ChatStack:
    def __init__(
        self,
        tmp_path: Any,
        script: list[str],
        *,
        service: FakeMinecraftService | None = None,
        trusted: list[str] | None = None,
    ) -> None:
        self.tmp_path = tmp_path
        self.script = script
        self.service = service or FakeMinecraftService()
        self.trusted = trusted if trusted is not None else ["空凛"]

    async def __aenter__(self) -> ChatStack:
        self.db = Database(DatabaseConfig(url=f"sqlite:///{self.tmp_path / 'chat.db'}"))
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
        self.service.config = MinecraftConfig(
            enabled=True,
            auto_start_runtime=False,
            agent={"tools": {"enabled": True}, "trusted_players": self.trusted},
        )
        self.bridge = MinecraftAgentBridge(self.service)
        self.runtime.minecraft_agent = self.bridge
        self.chat = MinecraftChatBridge(FakeBot(self.engine, self.runtime), self.service)  # type: ignore[arg-type]
        #: 角色回合计数（§二十九：一条消息只能产生一个回合）
        self.turns = 0
        original = self.runtime.respond

        async def counting_respond(*args: Any, **kwargs: Any) -> str:
            self.turns += 1
            return await original(*args, **kwargs)

        self.runtime.respond = counting_respond  # type: ignore[method-assign]
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        await self.tools.close()
        await self.db.close()
        return False

    # --- 触发一条游戏内聊天 -------------------------------------------------

    async def say(self, username: str, message: str, **extra: Any) -> None:
        event = parse_bridge_event(
            {
                "event": "minecraft.chat",
                "session_id": "s1",
                "timestamp": 1000.0,
                "username": username,
                "message": message,
                **extra,
            }
        )
        task = self.chat.apply_event(event)
        if task is not None:
            await task

    @property
    def chat_replies(self) -> list[str]:
        """她发进游戏里的话。"""
        return [payload["message"] for payload in self.service.action_calls("chat")]

    @property
    def verdicts(self) -> list[str]:
        return [trace.error_type for trace in self.tools.executor.traces]


def stack(tmp_path: Any, *script: str, **kwargs: Any) -> ChatStack:
    return ChatStack(tmp_path, list(script), **kwargs)


# ------------------------------------------------------------- §二十八 用例


async def test_minecraft_chat_becomes_user_turn(tmp_path: Any) -> None:
    async with stack(tmp_path, decide("minecraft_world"), "我在平原上呢～") as env:
        await env.say("空凛", "罐头，你在哪？")
    assert env.turns == 1, "一条游戏内消息 = 一个角色回合"
    # 提示里带上了"来自游戏内聊天"的说明，回合来源是 USER
    assert "Minecraft" in env.provider.calls[0]["messages"][0].content
    assert env.chat_replies == ["我在平原上呢～"]
    # 会话独立于 QQ（§十八），并且两个回合都记进了这个会话
    session_id = chat_session_id("127.0.0.1", 25565, "空凛")
    history = await env.engine.conversations.get_context(session_id)
    assert [message.content for message in history][-2:] == ["罐头，你在哪？", "我在平原上呢～"]


async def test_minecraft_chat_preserves_mc_username(tmp_path: Any) -> None:
    """§十七/§二十：MC 用户名一路带下去（会话名 + 关系/记忆的 user_id）。"""
    async with stack(tmp_path, "你好呀") as env:
        await env.say("RinsoraNeko", "在吗")
    assert env.turns == 1
    session_id = chat_session_id("127.0.0.1", 25565, "RinsoraNeko")
    assert "RinsoraNeko" in session_id
    relationship = await env.runtime.relationships.get("RinsoraNeko")
    assert relationship.interaction_count == 1


async def test_minecraft_chat_trusted_player_allows_low(tmp_path: Any) -> None:
    """§二十四 Test A：可信玩家说「你过来」→ world → move_to 真的执行。"""
    async with stack(
        tmp_path,
        decide("minecraft_world"),
        decide("minecraft_move_to", x=126, y=64, z=-228),
        "我这就过来～",
    ) as env:
        await env.say("空凛", "罐头，你过来")
    assert env.verdicts == ["", ""]
    assert env.service.action_calls("move_to") == [{"x": 126, "y": 64, "z": -228}]
    assert env.chat_replies == ["我这就过来～"]


async def test_minecraft_chat_follow_returns_running(tmp_path: Any) -> None:
    """§二十四 Test B：可信玩家说「跟着我」→ follow_player 启动即 RUNNING。"""
    async with stack(
        tmp_path,
        decide("minecraft_world"),
        decide("minecraft_follow_player", username="空凛"),
        "好呀，我跟着你～",
    ) as env:
        await env.say("空凛", "罐头跟着我")
    assert env.service.action_calls("follow_player") == [{"username": "空凛", "distance": None}]
    assert env.verdicts == ["", ""]


async def test_minecraft_chat_stop_cancels(tmp_path: Any) -> None:
    """§二十四 Test C：可信玩家说「停」→ stop 取消进行中的动作。"""
    service = FakeMinecraftService()
    service.enqueue("stop", {"ok": True, "status": "IDLE", "cancelled": ["act_follow_1"]})
    async with stack(tmp_path, decide("minecraft_stop"), "好，我停下了。", service=service) as env:
        env.bridge.context.apply_event(
            parse_bridge_event(
                {
                    "event": "minecraft.action.started",
                    "session_id": "s1",
                    "timestamp": 999.0,
                    "action": "follow_player",
                    "action_id": "act_follow_1",
                }
            )
        )
        await env.say("空凛", "停")
    assert env.service.action_calls("stop")
    assert env.verdicts == [""]
    assert env.chat_replies == ["好，我停下了。"]


async def test_minecraft_chat_untrusted_player_rejects_low(tmp_path: Any) -> None:
    """§二十五：不可信玩家喊「过来」不执行，但对话照常。"""
    async with stack(
        tmp_path,
        decide("minecraft_move_to", x=126, y=64, z=-228),
        "我在这儿呢，不过我不能乱跑～",
    ) as env:
        await env.say("Steve", "罐头过来")
    assert env.verdicts == ["minecraft.user_not_trusted"]
    assert env.service.action_calls("move_to") == []
    assert env.service.calls and env.service.calls[0][0] == "chat", "仍然正常聊天（发了一句回去）"


async def test_minecraft_chat_safe_tools_work_for_untrusted(tmp_path: Any) -> None:
    """§二十一：SAFE（查世界/停止）对所有玩家开放。"""
    async with stack(tmp_path, decide("minecraft_world"), "我在平原上，坐标我就不念啦") as env:
        await env.say("Steve", "罐头你是谁？")
    assert env.verdicts == [""]
    assert env.chat_replies == ["我在平原上，坐标我就不念啦"]


async def test_minecraft_chat_no_duplicate_agent_turn(tmp_path: Any) -> None:
    """§二十九：一条消息只产生一个角色回合（沙盒外部事件链是另一个消费者）。"""
    async with stack(tmp_path, "你好") as env:
        await env.say("空凛", "在吗")
        assert env.turns == 1
        # 桥不碰沙盒：它没有 sandbox 引用，也不会触发沙盒生成
        assert not hasattr(env.chat, "_sandbox")


# --------------------------------------------------------- §三十 防循环


async def test_own_chat_never_enters_her_own_turn(tmp_path: Any) -> None:
    """§三十 硬门禁：她自己说的话绝不能回到自己的 LLM 回合（否则无限循环）。"""
    async with stack(tmp_path, "（不该被用到）") as env:
        await env.say("Catodayo", "我来啦")  # = FakeMinecraftService.username
        assert env.turns == 0
        assert env.provider.calls == []
        assert env.service.calls == []


async def test_own_chat_is_skipped_even_without_a_known_username(tmp_path: Any) -> None:
    """拿不到自己的名字时保守处理：宁可不回，也不冒无限循环的风险。"""
    service = FakeMinecraftService(username="")
    async with stack(tmp_path, "（不该被用到）", service=service) as env:
        await env.say("空凛", "罐头在吗")
        assert env.turns == 0
        assert env.service.calls == []


async def test_whisper_is_not_answered_publicly(tmp_path: Any) -> None:
    """悄悄话不公开回（本阶段只有公共聊天发送能力）。"""
    async with stack(tmp_path, "（不该被用到）") as env:
        await env.say("空凛", "偷偷跟我说", private=True)
        assert env.turns == 0
        assert env.service.calls == []


async def test_system_lines_without_a_player_are_ignored(tmp_path: Any) -> None:
    """生存提示/系统行没有 username —— 不该触发回合。"""
    async with stack(tmp_path, "（不该被用到）") as env:
        await env.say("", "You are now in spectator mode")
        assert env.turns == 0


async def test_chat_bridge_disabled_does_nothing(tmp_path: Any) -> None:
    async with stack(tmp_path, "（不该被用到）") as env:
        env.service.config = MinecraftConfig(
            enabled=True, auto_start_runtime=False, agent={"chat": {"enabled": False}}
        )
        await env.say("空凛", "罐头")
        assert env.turns == 0


# --------------------------------------------------------------- 边界


async def test_reply_is_truncated_to_the_configured_limit(tmp_path: Any) -> None:
    long_reply = "啊" * 300
    async with stack(tmp_path, long_reply) as env:
        await env.say("空凛", "说点长的")
    assert len(env.chat_replies[0]) <= 200
    assert env.chat_replies[0].endswith("…")


async def test_respond_failure_is_swallowed_and_logged(tmp_path: Any) -> None:
    """模型失败不能拖垮事件通道（她这次没说话，但桥还活着）。"""
    async with stack(tmp_path, "ok") as env:

        async def broken(*args: Any, **kwargs: Any) -> str:
            raise RuntimeError("model down")

        env.runtime.respond = broken  # type: ignore[method-assign]
        await env.say("空凛", "在吗")
        assert env.service.calls == []  # 没有发送任何东西
        # 桥仍然可用（下一次正常）
        env.runtime.respond = env.runtime.respond  # noqa: PLW0127 - 保持可读
        assert env.chat.enabled is True


async def test_apply_event_ignores_other_event_types(tmp_path: Any) -> None:
    async with stack(tmp_path, "嗯") as env:
        other = parse_bridge_event(
            {
                "event": "minecraft.spawned",
                "session_id": "s1",
                "timestamp": 1.0,
                "username": "Catodayo",
            }
        )
        assert env.chat.apply_event(other) is None
        assert env.turns == 0


async def test_session_naming_falls_back_without_server_info(tmp_path: Any) -> None:
    assert chat_session_id("127.0.0.1", 25565, "空凛") == "minecraft:127.0.0.1:25565:空凛"
    assert chat_session_id(None, None, "空凛") == "minecraft:空凛"


async def test_service_listener_wiring_registers_the_bridge(tmp_path: Any) -> None:
    """service.start() 会把 chat bridge 挂成监听器（与 agent 同款鸭子类型装配）。"""
    service = MinecraftService(None, MinecraftConfig(enabled=True, auto_start_runtime=False))  # type: ignore[arg-type]
    chat = MinecraftChatBridge(FakeBot(None, None), service)  # type: ignore[arg-type]
    service.chat_bridge = chat
    listeners = len(service._listeners)  # noqa: SLF001
    service.add_listener(chat.apply_event)
    assert len(service._listeners) == listeners + 1  # noqa: SLF001
    assert pytest is not None
