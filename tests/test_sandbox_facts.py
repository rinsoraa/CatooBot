"""Sandbox facts: tag-driven injection + false-claim audit (the 可乐 incident).

Covers:
* every entity family carries tags and is selected by topic keywords
* the injected block carries the must-not-invent constraint
* the outbound audit removes 与沙盒不符的说法（观察到的原句 → 只剩真话）
* the model-thinking excerpt is console-only (never in the log file)
"""

from __future__ import annotations

import logging

from app.config.settings import SandboxConfig
from app.sandbox import BibleCompiler, SandboxRuntime, SandboxStore
from app.sandbox.facts import FACTS_HEADER
from app.sandbox.models import EventLevel, EventSource
from app.utils.logger import _PlainFilter
from app.utils.narrator import narrate

BIBLE = "config/character_bible.md"


async def make_sandbox() -> SandboxRuntime:
    bible = BibleCompiler(BIBLE).compile()
    runtime = SandboxRuntime(SandboxConfig(), SandboxStore(None), bible=bible)
    await runtime.start()
    return runtime


# ------------------------------------------------------------------ selection


class TestTagSelection:
    async def test_food_topic_injects_the_fridge_numbers(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("点个外卖呗 顺便带瓶可乐")
        assert "fridge" in selection.hits
        assert any("可乐×" in line for line in selection.lines)

    async def test_cat_topic_injects_the_cat(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("小喵在干嘛")
        assert "pet" in selection.hits
        assert any("小喵" in line for line in selection.lines)

    async def test_game_topic_injects_the_computer(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("你晚上打不打游戏")
        assert "computer" in selection.hits

    async def test_delivery_topic_injects_the_package_box(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("快递到了吗")
        assert "package_box" in selection.hits
        assert any("没有" in line for line in selection.lines)

    async def test_wardrobe_topic_injects_clothes(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("你穿什么睡衣")
        assert "wardrobe" in selection.hits

    async def test_unrelated_topic_injects_nothing(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("今天天气怎么样")
        assert selection.empty

    async def test_selection_is_capped(self) -> None:
        rt = await make_sandbox()
        selection = rt.facts_for("可乐 猫 游戏 快递 睡衣 出门 洗澡")
        assert len(selection.lines) <= 4

    async def test_block_carries_the_constraint_and_narrates(self, caplog) -> None:
        rt = await make_sandbox()
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            block = rt.facts_block("冰箱里还有可乐吗")
        assert "可乐×" in block
        assert FACTS_HEADER in block
        beats = [r for r in caplog.records if r.name == "CatooBot.Narration"]
        assert any("📎" in r.getMessage() for r in beats), "injection must be narrated"

    async def test_no_match_returns_empty_block(self) -> None:
        rt = await make_sandbox()
        assert rt.facts_block("今天天气怎么样") == ""


# ---------------------------------------------------------------------- audit


class TestClaimAudit:
    async def test_the_observed_incident_is_cleaned(self) -> None:
        """原句（可乐还剩 9 罐时说的）必须只剩真话。"""
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 9
        cleaned = rt.audit_claims("刚喝完最后一罐可乐，冰箱空了……不想出门补货")
        assert cleaned == "不想出门补货"

    async def test_false_container_claim_is_removed(self) -> None:
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 9
        cleaned = rt.audit_claims("冰箱空了")
        assert cleaned == "……"

    async def test_true_statements_survive(self) -> None:
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 9
        for text in ("冰箱里还有 3 罐可乐呢", "我想喝冰可乐", "布丁还有 3 个"):
            assert rt.audit_claims(text) == text

    async def test_true_claim_when_truly_empty(self) -> None:
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 0
        assert rt.audit_claims("刚喝完最后一罐可乐") == "刚喝完最后一罐可乐"

    async def test_partial_falsehood_keeps_the_true_half(self) -> None:
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 5
        cleaned = rt.audit_claims("冰箱空了，不过小喵的猫粮还有")
        assert cleaned == "不过小喵的猫粮还有"

    async def test_audit_narrates_the_removal(self, caplog) -> None:
        rt = await make_sandbox()
        rt.inventories.get("fridge").items["可乐"] = 9
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            rt.audit_claims("可乐没了")
        beats = [r for r in caplog.records if r.name == "CatooBot.Narration"]
        assert any("🛡" in r.getMessage() for r in beats)


# ------------------------------------------------------- console-only thinking


class TestThinkingDisplay:
    def test_thinking_is_console_only(self, caplog) -> None:
        with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
            narrate().thinking("她想了想冰箱里还有没有可乐", detail="测试")
        beats = [r for r in caplog.records if r.name == "CatooBot.Narration"]
        assert beats and getattr(beats[-1], "console_only", False) is True
        # ...and the file filter drops it entirely
        assert _PlainFilter().filter(beats[-1]) is False

    def test_thinking_switch_off_is_silent(self, caplog) -> None:
        from app.utils.narrator import configure

        configure(True, thinking=False)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Narration"):
                narrate().thinking("不应出现")
            assert not [r for r in caplog.records if r.name == "CatooBot.Narration"]
        finally:
            configure(True, thinking=True)


# ------------------------------------------------------------------ end-to-end


class TestEndToEnd:
    async def _bot(self, tmp_path, replies: list[str]):  # type: ignore[no-untyped-def]
        from tests.ai_mocks import MockAIProvider
        from tests.test_chat_integration import make_character_bot

        provider = MockAIProvider(behaviors={"A": replies})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        sandbox = await make_sandbox()
        sandbox.narrate_ticks = False
        bot.sandbox = sandbox
        bot.character.sandbox = sandbox
        return bot, provider

    async def test_prompt_carries_real_fridge_numbers(self, tmp_path) -> None:
        from tests.conftest import private_event

        bot, provider = await self._bot(tmp_path, ["还有九罐吧"])
        try:
            await bot.event_bus.emit(private_event("冰箱里还有可乐吗", user_id=777))
            await bot.conversation.wait_idle()
            system = provider.calls[0]["messages"][0].content
            assert "世界事实" in system
            assert "可乐×" in system
        finally:
            await bot.shutdown()

    async def test_false_claim_never_reaches_qq(self, tmp_path) -> None:
        """端到端复现 15:55 那条消息：模型编的“冰箱空了”会被拦下。"""
        from tests.conftest import private_event

        bot, _provider = await self._bot(
            tmp_path, ["刚喝完最后一罐可乐，冰箱空了……不想出门补货"]
        )
        try:
            await bot.event_bus.emit(private_event("随便聊聊", user_id=777))
            await bot.conversation.wait_idle()
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert texts == ["不想出门补货"]
        finally:
            await bot.shutdown()

    async def test_initiative_event_injects_related_facts(self, tmp_path) -> None:
        """主动消息（life_event=喝完可乐）也要拿到冰箱真实数字。"""
        bot, provider = await self._bot(tmp_path, ["刚喝了罐可乐", "嗯嗯"])
        try:
            # a cola-drinking event just happened
            await bot.sandbox._append_event(  # noqa: SLF001 - simulate the completion
                "action_completed",
                "喝可乐结束（在喝冰可乐）",
                source=EventSource.character_action,
                level=EventLevel.micro,
                reason="natural_completion",
            )
            moment_text, _event_id = await bot.sandbox.life_moment()
            assert "喝可乐" in moment_text
            await bot.character.compose_initiative(
                session_id="private:777", user_id=777,
                reason="life_event", topic=moment_text,
            )
            system = provider.calls[0]["messages"][0].content
            assert "世界事实" in system and "可乐×" in system
            assert "不要补充库存" in system
        finally:
            await bot.shutdown()
