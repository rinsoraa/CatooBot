"""v0.4 integration: the full behaviour pipeline from QQ to QQ (spec §75).

Verifies that the reply path really applies planned delays and message
bubbles through the delivery layer (with an injected sleep so nothing waits),
and that a private message still produces exactly one coherent answer.
"""

from __future__ import annotations

from app.ai.engine import AIEngine
from app.behavior.presence import PresenceResolver
from app.config.settings import (
    AIConfig,
    BehaviorChunkingConfig,
    BehaviorReplyTimingConfig,
    BehaviorScheduleConfig,
)
from app.core.bot import Bot
from app.response.delivery import MessageDelivery
from app.response.planner import CharacterResponsePlanner
from app.response.timing import ReplyTiming
from tests.ai_mocks import MockAIProvider
from tests.conftest import make_bot, private_event

# Two paragraphs, each long enough not to be merged by the chunker.
REPLY = "今天挖矿挖了好久。" + chr(10) * 2 + "手都酸了想歇会儿。"


class RecordingSleep:
    """Captures requested delays instead of actually sleeping."""

    def __init__(self) -> None:
        self.calls: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.calls.append(round(seconds, 3))


def attach_behavior(bot: Bot, provider: MockAIProvider) -> MockAIProvider:
    bot.ai = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        bot.database,
        providers={"mock": provider},
    )
    bot.character.engine = bot.ai
    if bot.character.extractor is not None:
        bot.character.extractor.engine = bot.ai
    bot.behavior.states = bot.character.states
    return provider


async def make_bot_with_behavior(
    tmp_path, *, timing: bool, chunking: bool, probability: float = 1.0
):
    provider = MockAIProvider(
        behaviors={"A": [REPLY]}
    )
    bot = make_bot(tmp_path)
    config = bot.config.model_copy(
        update={
            "behavior": bot.config.behavior.model_copy(
                update={
                    "reply": BehaviorReplyTimingConfig(
                        enabled=timing, min_delay=0.5, max_delay=3.0, jitter=0.0
                    ),
                    "chunking": BehaviorChunkingConfig(
                        enabled=chunking, paragraph_always_split=True
                    ),
                    # no sleep/DND window: these tests must not depend on the hour
                    "schedule": BehaviorScheduleConfig(
                        sleep_enabled=False, dnd_enabled=False
                    ),
                }
            )
        }
    )
    bot.config = config
    # Rebuild the response layer exactly as production wiring does, so the
    # test config is what the delivery path actually uses.
    bot.reply_timing = ReplyTiming(config.behavior.reply, bot.presence)
    bot.response_planner = CharacterResponsePlanner(
        bot.reply_timing, config.behavior.chunking
    )
    attach_behavior(bot, provider)
    await bot.database.connect()
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    return bot


class TestReplyPipeline:
    async def test_delay_and_chunks_reach_the_delivery_layer(self, tmp_path) -> None:
        bot = await make_bot_with_behavior(tmp_path, timing=True, chunking=True)
        sleeper = RecordingSleep()
        bot.response_delivery = MessageDelivery(bot, sleep=sleeper)
        try:
            await bot.event_bus.emit(private_event("在干嘛", user_id=7))
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert texts == ["今天挖矿挖了好久。", "手都酸了想歇会儿。"]  # two bubbles
            assert len(sleeper.calls) == 2  # initial delay + inter-chunk gap
            assert 0.5 <= sleeper.calls[0] <= 3.0
            assert sleeper.calls[1] > 0
        finally:
            await bot.shutdown()

    async def test_timing_disabled_sends_immediately(self, tmp_path) -> None:
        bot = await make_bot_with_behavior(tmp_path, timing=False, chunking=False)
        sleeper = RecordingSleep()
        bot.response_delivery = MessageDelivery(bot, sleep=sleeper)
        try:
            await bot.event_bus.emit(private_event("在干嘛", user_id=7))
            assert sleeper.calls == []  # no artificial wait
            joined = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert len(joined) == 1 and "今天挖矿挖了好久。" in joined[0]
        finally:
            await bot.shutdown()

    async def test_one_user_message_never_yields_two_replies(self, tmp_path) -> None:
        """Regression guard: chunking must not duplicate the answer."""
        bot = await make_bot_with_behavior(tmp_path, timing=True, chunking=True)
        bot.response_delivery = MessageDelivery(bot, sleep=RecordingSleep())
        try:
            await bot.event_bus.emit(private_event("在干嘛", user_id=7))
            actions = [c[0] for c in bot.adapter.calls]  # type: ignore[attr-defined]
            assert actions.count("send_private_msg") == 2  # chunks of ONE reply
            joined = "".join(bot.adapter.sent_texts())  # type: ignore[attr-defined]
            assert joined.count("今天挖矿挖了好久") == 1
        finally:
            await bot.shutdown()

    async def test_group_message_to_other_user_not_answered_by_default(self, tmp_path) -> None:
        from tests.conftest import group_event

        bot = await make_bot_with_behavior(tmp_path, timing=False, chunking=False)
        try:
            await bot.event_bus.emit(group_event("大家好", user_id=9, at_bot=False))
            assert bot.adapter.sent_texts() == []  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_mention_in_group_answered(self, tmp_path) -> None:
        from tests.conftest import group_event

        bot = await make_bot_with_behavior(tmp_path, timing=False, chunking=False)
        try:
            await bot.event_bus.emit(group_event("在干嘛", user_id=9, at_bot=True))
            assert bot.adapter.sent_texts()  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_time_context_reaches_the_prompt(self, tmp_path) -> None:
        bot = await make_bot_with_behavior(tmp_path, timing=False, chunking=False)
        provider = bot.character.engine._providers["mock"]  # noqa: SLF001
        try:
            await bot.event_bus.emit(private_event("几点了", user_id=7))
            system = provider.calls[0]["messages"][0].content
            assert "现在是" in system  # natural-language time context
            assert "T" not in system.split("现在是")[1][:12]  # no ISO timestamp
        finally:
            await bot.shutdown()


class TestWebSettings:
    """WebUI settings must actually reach the live engine (regression: the
    override map was once wrapped one level too deep and silently ignored)."""

    async def test_saved_settings_hot_apply(self, tmp_path) -> None:
        from app.web.services.behavior import BehaviorService

        bot = await make_bot_with_behavior(tmp_path, timing=True, chunking=True)
        service = BehaviorService(bot)
        try:
            await service.save_settings(
                {
                    "reply_enabled": "1",
                    "min_delay": "1.2",
                    "max_delay": "6.0",
                    "chunking_enabled": "1",
                    "chunk_probability": "0.4",
                    "max_chunks": "3",
                    "activity_enabled": "1",
                    "sleep_enabled": "1",
                    "sleep_start": "01:00",
                    "sleep_end": "08:30",
                    "dnd_enabled": "0",
                    "dnd_start": "23:00",
                    "dnd_end": "08:00",
                    "participation_enabled": "0",
                    "participation_probability": "0.04",
                    "group_cooldown": "300",
                    "initiative_enabled": "1",
                    "min_interval_minutes": "90",
                    "daily_limit": "2",
                    "hourly_limit": "1",
                    "idle_hours": "5",
                    "min_relationship_stage": "familiar",
                    "max_unanswered": "1",
                }
            )
            cfg = bot.config.behavior
            assert cfg.reply.min_delay == 1.2
            assert cfg.reply.max_delay == 6.0
            assert cfg.chunking.chunk_probability == 0.4
            assert cfg.schedule.sleep_start == "01:00"
            assert cfg.initiative.enabled is True
            assert cfg.initiative.min_interval_minutes == 90
            assert cfg.initiative.daily_limit == 2
            # live components — not just the config object
            assert bot.reply_timing._config.min_delay == 1.2  # noqa: SLF001
            assert bot.behavior.initiative.config.enabled is True
            assert bot.behavior.presence.timezone_name
        finally:
            await bot.shutdown()

    async def test_overrides_persist_and_restore(self, tmp_path) -> None:
        from app.web.services.behavior import BehaviorService

        bot = await make_bot_with_behavior(tmp_path, timing=True, chunking=True)
        service = BehaviorService(bot)
        try:
            await service.save_settings(
                {"reply_enabled": "0", "min_delay": "2.5", "max_delay": "9.0"}
            )
            stored = await service.load_overrides()
            assert stored["reply"]["min_delay"] == 2.5

            # simulate a restart: fresh config, then restore the overrides
            bot.config.behavior = bot.config.behavior.model_copy(
                update={"reply": bot.config.behavior.reply.model_copy(update={"min_delay": 0.8})}
            )
            await service.restore_overrides()
            assert bot.config.behavior.reply.min_delay == 2.5
            assert bot.config.behavior.reply.enabled is False
        finally:
            await bot.shutdown()


class TestBehaviorSnapshot:
    async def test_snapshot_exposes_dashboard_fields(self, tmp_path) -> None:
        bot = await make_bot_with_behavior(tmp_path, timing=True, chunking=True)
        try:
            snapshot = await bot.behavior.snapshot()
            assert set(snapshot) >= {
                "enabled",
                "state",
                "time",
                "activity",
                "group",
                "topics_active",
                "initiative",
            }
            assert snapshot["time"]["local_time"]
            assert snapshot["initiative"]["daily_limit"] >= 0
        finally:
            await bot.shutdown()

    async def test_presence_uses_configured_timezone(self, tmp_path) -> None:
        bot = make_bot(tmp_path)
        config = bot.config.model_copy(
            update={"character": bot.config.character.model_copy(update={"timezone": "UTC"})}
        )
        presence = PresenceResolver(config.character.timezone, config.behavior.schedule)
        assert presence.timezone_name == "UTC"
        assert presence.now().tzinfo is not None
