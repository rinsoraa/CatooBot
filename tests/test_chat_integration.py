"""End-to-end character chat integration tests (v0.3 flow).

MessageEvent -> CharacterPlugin -> CharacterRuntime -> AIEngine -> Mock
            -> Bot API (FakeAdapter). Covers triggers, context, isolation,
            failures, splitting. QQ-side commands are gone: prefixed text is
            just ordinary chat now.
"""

from __future__ import annotations

import asyncio

from app.ai.engine import AIEngine
from app.config.settings import AIConfig
from tests.ai_mocks import MockAIProvider
from tests.conftest import group_event, make_bot, private_event


class RateLimitedMock(MockAIProvider):
    """A mock whose every model always fails with 429."""

    async def chat(self, request) -> None:  # type: ignore[no-untyped-def]
        from app.ai.errors import RateLimitError

        self.calls.append(
            {
                "model": request.model,
                "n_messages": len(request.messages),
                "messages": list(request.messages),
                "last_user": request.messages[-1].content,
            }
        )
        raise RateLimitError("mock", model=request.model or "?")


def attach_ai(bot, provider: MockAIProvider, models: list[str]) -> AIEngine:  # type: ignore[no-untyped-def]
    """Replace the bot's (disabled by default) AI engine with a mock-backed one."""
    config = AIConfig(
        enabled=True,
        system_prompt="ignored",  # character builder owns the system prompt now
        context={"enabled": True, "max_messages": 20},
        models=[{"name": n, "provider": "mock", "model": n} for n in models],
    )
    # Same wiring as production Bot.__init__: router events feed the dashboard
    # counters (ai_requests / ai_errors / rate_limited).
    engine = AIEngine(
        config,
        bot.database,
        providers={"mock": provider},
        router_event_listener=bot._on_router_event,  # noqa: SLF001 - production wiring
    )
    bot.ai = engine
    bot.character.engine = engine
    if bot.character.extractor is not None:
        bot.character.extractor.engine = engine
    return engine


async def make_character_bot(tmp_path, provider: MockAIProvider, models: list[str] | None = None):  # type: ignore[no-untyped-def]
    bot = make_bot(tmp_path)
    attach_ai(bot, provider, models or ["A"])
    await bot.database.connect()
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    if bot.continuity is not None:
        await bot.continuity.start()
    # v1.2: replies go through the turn runtime — keep the debounce tiny so
    # tests emit → wait_idle() → assert without real waiting.
    bot.config.conversation.debounce.direct_message_ms = 20
    bot.config.conversation.debounce.group_message_ms = 30
    return bot


class TestPrivateChat:
    async def test_plain_message_triggers_character(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["嗨嗨，今天怎么样？"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("你好", user_id=777))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == ["嗨嗨，今天怎么样？"]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_slash_text_is_ordinary_chat(self, tmp_path) -> None:
        """/ping etc. no longer execute anything — spec v0.3 §2/§3."""
        provider = MockAIProvider(behaviors={"A": ["Pong 是什么呀？"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("/ping", user_id=777))
            await bot.conversation.wait_idle()
            assert provider.calls[0]["last_user"] == "/ping"
            assert bot.adapter.sent_texts() == ["Pong 是什么呀？"]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_context_is_used_across_turns(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["答1", "答2"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        bot.config.memory.extraction.enabled = False  # keep the mock call list chat-only
        try:
            await bot.event_bus.emit(private_event("我叫小明", user_id=777))
            await bot.conversation.wait_idle()
            await bot.event_bus.emit(private_event("我叫什么？", user_id=777))
            await bot.conversation.wait_idle()
            first, second = provider.calls[0], provider.calls[1]
            assert second["n_messages"] == first["n_messages"] + 2
            assert any(m.content == "我叫小明" for m in second["messages"])
        finally:
            await bot.shutdown()

    async def test_system_prompt_contains_knowledge_boundary(self, tmp_path) -> None:
        provider = MockAIProvider()
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("你是什么？", user_id=777))
            await bot.conversation.wait_idle()
            system = provider.calls[0]["messages"][0].content
            assert "NapCat" in system  # boundary instruction text, not a leak
            assert "不要输出功能菜单" in system
        finally:
            await bot.shutdown()

    async def test_sticker_only_private_message_gets_reply(self, tmp_path) -> None:
        """A caption-less sticker (mface) must still reach the character (v1.1)."""
        from app.message.event import PrivateMessageEvent

        provider = MockAIProvider(behaviors={"A": ["这个表情好可爱"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            raw = {
                "post_type": "message",
                "self_id": 10001,
                "time": 1700000000,
                "message_type": "private",
                "sub_type": "friend",
                "message_id": 11,
                "user_id": 777,
                "message": [
                    {
                        "type": "mface",
                        "data": {
                            "emoji_id": "1",
                            "emoji_package_id": "2",
                            "key": "k",
                            "summary": "一只猫在笑",
                        },
                    }
                ],
                "raw_message": "[mface]",
                "sender": {"user_id": 777, "nickname": "Alice"},
            }
            event = PrivateMessageEvent.model_validate(raw)
            await bot.event_bus.emit(event)
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert texts and texts[0] == "这个表情好可爱"
            assert provider.calls and provider.calls[0]["last_user"] == "（发来一个表情包）"
            # the expression engine replies with a face/sticker after the text
            assert any(
                isinstance(seg, dict) and seg.get("type") in ("face", "mface")
                for _, params, _ in bot.adapter.calls  # type: ignore[attr-defined]
                for seg in ((params or {}).get("message") or [])
            )
        finally:
            await bot.shutdown()


class TestGroupChat:
    async def test_at_bot_triggers_character(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["来啦来啦"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(group_event("你好", user_id=888, at_bot=True))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == ["来啦来啦"]  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_non_mention_group_message_ignored(self, tmp_path) -> None:
        provider = MockAIProvider()
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(group_event("大家好", user_id=888))
            await bot.conversation.wait_idle()
            assert provider.calls == []
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()


class TestSessionIsolation:
    async def test_two_users_contexts_do_not_leak(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["答A", "答B"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("我叫用户一", user_id=1))
            await bot.conversation.wait_idle()
            await bot.event_bus.emit(private_event("我叫用户二", user_id=2))
            await bot.conversation.wait_idle()
            ctx1 = await bot.ai.conversations.get_context("private:1")
            ctx2 = await bot.ai.conversations.get_context("private:2")
            assert [m.content for m in ctx1] == ["我叫用户一", "答A"]
            assert [m.content for m in ctx2] == ["我叫用户二", "答B"]
        finally:
            await bot.shutdown()

    async def test_concurrent_users_isolated(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["r1", "r2", "r3", "r4"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await asyncio.gather(
                bot.event_bus.emit(private_event("甲的问题", user_id=1)),
                bot.event_bus.emit(private_event("乙的问题", user_id=2)),
            )
            await bot.conversation.wait_idle()
            ctx1 = await bot.ai.conversations.get_context("private:1")
            ctx2 = await bot.ai.conversations.get_context("private:2")
            # The two sessions run concurrently, so which reply lands in which
            # session is scheduling-dependent; what must hold is isolation.
            assert ctx1[0].content == "甲的问题" and ctx2[0].content == "乙的问题"
            assert ctx1[1].content.startswith("r") and ctx2[1].content.startswith("r")
            assert ctx1[1].content != ctx2[1].content
            assert all("乙" not in m.content for m in ctx1)
            assert all("甲" not in m.content for m in ctx2)
        finally:
            await bot.shutdown()


class TestFailureHandling:
    async def test_all_models_fail_natural_fallback(self, tmp_path) -> None:
        from plugins.chat.plugin import BUSY_REPLIES

        provider = RateLimitedMock()
        bot = await make_character_bot(tmp_path, provider, models=["A", "B"])
        try:
            await bot.event_bus.emit(private_event("你好", user_id=777))
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(texts) == 1
            assert texts[0] in BUSY_REPLIES
            # still alive afterwards
            provider_ok = MockAIProvider(behaviors={"A": ["又好啦"]})
            attach_ai(bot, provider_ok, ["A"])
            await bot.event_bus.emit(private_event("还在吗", user_id=777))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts()[-1] == "又好啦"  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_empty_response_not_sent(self, tmp_path) -> None:
        from plugins.chat.plugin import BUSY_REPLIES

        provider = MockAIProvider(behaviors={"A": ["   "]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("你好", user_id=777))
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(texts) == 1
            assert texts[0] in BUSY_REPLIES
            assert await bot.ai.conversations.get_context("private:777") == []
        finally:
            await bot.shutdown()

    async def test_long_reply_is_split(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["很" * 4500]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            await bot.event_bus.emit(private_event("讲个长故事", user_id=777))
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(texts) >= 2
            assert all(len(t) <= 2000 for t in texts)
            assert "".join(texts) == "很" * 4500
        finally:
            await bot.shutdown()


class TestAiDisabled:
    async def test_disabled_ai_stays_silent(self, tmp_path) -> None:
        bot = make_bot(tmp_path)  # default config: ai.enabled=False
        await bot.database.connect()
        bot.event_bus.on("message", bot.core_router.on_message)
        await bot.plugins.load_all()
        try:
            await bot.event_bus.emit(private_event("你好", user_id=777))
            await bot.conversation.wait_idle()
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()


class TestPluginReload:
    async def test_reload_does_not_duplicate_replies(self, tmp_path) -> None:
        """Regression: reloading plugins (WebUI Runtime page) must not leave
        the previous plugin instance subscribed — that sent every reply twice."""
        provider = MockAIProvider(behaviors={"A": ["只回一次"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        bot.config.memory.extraction.enabled = False
        try:
            await bot.event_bus.emit(private_event("你好", user_id=5))
            await bot.conversation.wait_idle()
            first = len(bot.adapter.sent_texts())  # type: ignore[attr-defined]
            assert first == 1

            await bot.plugins.unload_all()
            await bot.plugins.load_all()

            await bot.event_bus.emit(private_event("在吗", user_id=5))
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(texts) == first + 1, f"duplicated reply: {texts}"
        finally:
            await bot.shutdown()

    async def test_reload_removes_stale_subscriptions(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["回复"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            before = bot.event_bus.subscriber_count()
            await bot.plugins.unload_all()
            assert bot.event_bus.subscriber_count() < before  # old handlers gone
            await bot.plugins.load_all()
            assert bot.event_bus.subscriber_count() == before
        finally:
            await bot.shutdown()


class TestMemoryExtractionFlow:
    async def test_extraction_runs_after_reply(self, tmp_path) -> None:
        provider = MockAIProvider(
            behaviors={
                "A": ["哇，猫咪最可爱了！"],
                "E": [
                    '{"memories": [{"category": "preference", '
                    '"content": "用户喜欢猫", "importance": 0.8}]}'
                ],
            }
        )
        bot = await make_character_bot(tmp_path, provider, models=["A", "E"])
        bot.config.memory.extraction.model = "E"  # pin extraction to model E
        try:
            await bot.event_bus.emit(private_event("我最喜欢猫了！", user_id=42))
            await bot.conversation.wait_idle()
            await bot.character.extractor.wait_idle()
            memories = await bot.memory.list_memories(scope_key="user:42")
            assert any("猫" in m.content for m in memories)
        finally:
            await bot.shutdown()
