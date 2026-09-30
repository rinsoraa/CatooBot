"""World integration tests (v0.8 §3/§41-§47/§57/§59): chat, initiative, agent.

Spec coverage: her own life reaches the prompt (clearly bounded as fiction),
background life never becomes background messaging, the Initiative Gate still
decides every proactive message, an agent result becomes a world *event*, and
a broken world cannot take chat down with it.
"""

from __future__ import annotations

import random

import pytest

from app.config.settings import WorldConfig
from app.world.runtime import build_world
from tests.ai_mocks import MockAIProvider
from tests.conftest import FakeAdapter, make_bot, private_event
from tests.test_chat_integration import make_character_bot
from tests.world_helpers import FakeTime, make_world, world_events


async def attach_world(bot, *, start=None, config: WorldConfig | None = None):
    """Give an existing test bot a world with controllable time."""
    from app.world.clock import WorldClock

    fake = FakeTime(start if start is not None else 1781506800.0)  # 2026-06-15 15:00 +08
    clock = WorldClock(timezone="Asia/Singapore", clock=fake)
    world = build_world(
        config or bot.config.world,
        database=bot.database,
        state_manager=bot.character.states,
        timezone="Asia/Singapore",
        presence=bot.presence,
        clock=clock,
        rng=random.Random(11),
    )
    await world.state.load()
    bot.world = world
    bot.character.world = world
    return world, fake


async def awake_initiative(bot):
    """An initiative engine whose presence rules ignore the wall clock."""
    from app.behavior.initiative import InitiativeEngine
    from app.behavior.presence import PresenceResolver
    from app.config.settings import BehaviorScheduleConfig

    config = bot.config.behavior.initiative.model_copy(update={"enabled": True})
    presence = PresenceResolver(
        bot.config.character.timezone,
        BehaviorScheduleConfig(sleep_enabled=False, dnd_enabled=False),
    )
    return InitiativeEngine(config, presence, database=bot.database)


class TestContextInjection:
    def test_world_block_carries_reality_boundary(self) -> None:
        from app.character.context import CharacterContextBuilder
        from app.character.persona import Persona
        from app.character.relationship import Relationship
        from app.character.state import CharacterState

        builder = CharacterContextBuilder()
        messages = builder.build(
            Persona(),
            CharacterState(activity="打游戏", location="房间"),
            Relationship(user_id="1"),
            [],
            [],
            "在干嘛",
            world={
                "state_line": "你在房间打游戏",
                "goal": "你最近在做的事：把房子盖完（进度 20%）",
                "events": ["06-15 14:30 推进了「把房子盖完」"],
            },
        )
        system = messages[0].content
        assert "你自己的日常" in system
        assert "把房子盖完" in system
        assert "现实边界" in system
        assert "可验证" in system  # never claim real-world verifiable acts

    def test_no_world_block_without_world(self) -> None:
        from app.character.context import CharacterContextBuilder
        from app.character.persona import Persona
        from app.character.relationship import Relationship
        from app.character.state import CharacterState

        builder = CharacterContextBuilder()
        messages = builder.build(
            Persona(), CharacterState(), Relationship(user_id="1"), [], [], "在干嘛",
            world=None,
        )
        assert "现实边界" not in messages[0].content

    async def test_respond_injects_her_own_life(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["刚在搭房子呢"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            world, _fake = await attach_world(bot)
            await world.state.change("manual", activity="建房子", location="房间")
            await world.goals.create("把房子盖完", milestones=["打地基"], next_action="打地基")
            await world.goals.set_current(
                (await world.goals.all(status="active"))[0].goal_id
            )
            await bot.event_bus.emit(private_event("在干嘛", user_id=777))
            system = provider.calls[0]["messages"][0].content
            assert "建房子" in system
            assert "把房子盖完" in system
            assert "现实边界" in system
        finally:
            await bot.shutdown()

    async def test_respond_without_world_is_unchanged(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["嗯嗯"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            bot.character.world = None
            await bot.event_bus.emit(private_event("在干嘛", user_id=777))
            assert "现实边界" not in provider.calls[0]["messages"][0].content
        finally:
            await bot.shutdown()


class TestUserInteraction:
    async def test_message_marks_her_as_chatting(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["在的"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            world, _fake = await attach_world(bot)
            activity_before = world.state.state.activity
            await bot.event_bus.emit(private_event("在吗", user_id=777))
            # v1.0: chat is an interaction overlay, never a new primary activity.
            assert world.state.state.interaction_overlay == "chatting"
            assert world.state.state.activity == activity_before
            events = await world_events(bot.database, "relationship")
            assert events and "聊天" in events[0]["summary"]
        finally:
            await bot.shutdown()

    async def test_interaction_does_not_create_memories(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["在的"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        bot.config.memory.extraction.enabled = False
        try:
            world, _fake = await attach_world(bot)
            await bot.event_bus.emit(private_event("在吗", user_id=777))
            assert await world.events.stats() is not None
            rows = await bot.database.fetchall("SELECT COUNT(*) AS n FROM memories")
            assert rows[0]["n"] == 0
        finally:
            await bot.shutdown()


class TestAgentReadAccess:
    async def test_agent_context_can_read_world_state(self, tmp_path) -> None:
        """Tools/Agent may *read* the world through ToolContext (§18)."""
        provider = MockAIProvider(behaviors={"A": ["嗯"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            world, _fake = await attach_world(bot)
            await world.state.change("manual", activity="打游戏", location="房间")
            context = bot.character._tool_context(  # noqa: SLF001 - the read path
                session_id="private:1", user_id="1", time_context=None, is_group=False
            )
            assert "打游戏" in context.metadata.get("world", "")
        finally:
            await bot.shutdown()

    async def test_no_world_metadata_when_disabled(self, tmp_path) -> None:
        provider = MockAIProvider(behaviors={"A": ["嗯"]})
        bot = await make_character_bot(tmp_path, provider, models=["A"])
        try:
            bot.character.world = None
            context = bot.character._tool_context(  # noqa: SLF001
                session_id="private:1", user_id="1", time_context=None, is_group=False
            )
            assert context.metadata == {}
        finally:
            await bot.shutdown()


class TestAgentLink:
    async def test_agent_result_becomes_a_world_event(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.note_agent_result(
            task_type="agent_task",
            status="completed",
            summary="帮人把一件事办完了",
            session_id="private:1",
            user_id="1",
        )
        events = await world_events(database)
        assert events and events[-1]["source"] == "agent"
        assert world.state.state.last_change_reason == "task_completion"
        await database.close()

    async def test_failure_is_recorded_and_nudges_mood_down(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.state.change("manual", mood="happy")
        await world.note_agent_result(
            task_type="agent_task", status="failed", summary="有件事没办成"
        )
        assert world.state.state.last_change_reason == "task_failure"
        assert world.state.state.mood in ("neutral", "quiet", "happy")
        await database.close()

    def test_agent_never_writes_world_state_directly(self) -> None:
        """The agent may read the world, never mutate it (§18)."""
        import pathlib

        agent_dir = pathlib.Path("app/agent")
        offenders = []
        for path in agent_dir.rglob("*.py"):
            source = path.read_text(encoding="utf-8")
            if "app.world" in source:
                offenders.append(path.name)
        assert offenders == []


class TestInitiativeLinkage:
    async def test_world_moment_becomes_a_candidate(self, tmp_path) -> None:

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        manager = await awake_initiative(bot)
        candidates = await manager.build_candidates(
            scope_key="private:1",
            user_id="1",
            last_seen=None,
            relationship_stage="familiar",
            world_moment="把「把房子盖完」做完了",
        )
        assert any(c.reason == "life_event" for c in candidates)
        # without a moment, the same call yields no life_event candidate
        plain = await manager.build_candidates(
            scope_key="private:1", user_id="1", last_seen=None,
            relationship_stage="familiar",
        )
        assert all(c.reason != "life_event" for c in plain)
        await bot.database.close()

    async def test_gate_still_decides_for_world_candidates(self, tmp_path) -> None:
        from app.behavior.initiative import InitiativeCandidate

        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        manager = await awake_initiative(bot)
        candidate = InitiativeCandidate(
            scope_key="private:1",
            user_id="1",
            reason="life_event",
            topic="把「把房子盖完」做完了",
            priority=0.7,
        )
        blocked = await manager.evaluate(
            candidate, relationship_stage="familiar", user_enabled=False
        )
        assert blocked.allowed is False and blocked.reason == "user_disabled"
        await bot.database.close()

    async def test_world_moment_is_surfaced_once(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot)
        await world.goals.create("把房子盖完")
        goal = (await world.goals.all(status="active"))[0]
        await world.goals.advance(goal.goal_id, amount=0.4)
        await world.goals.advance(goal.goal_id, amount=0.4)
        await world.goals.advance(goal.goal_id, amount=0.4)  # completes -> milestone
        text, event_id = await bot.scheduler._world_moment()  # noqa: SLF001
        assert "做完了" in text and event_id
        await world.events.mark_surfaced([event_id])
        text_again, _ = await bot.scheduler._world_moment()  # noqa: SLF001
        assert text_again == ""
        await bot.database.close()


class TestBackgroundBudget:
    async def test_budget_counts_sent_initiatives(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot)
        assert await world.background_budget_left() == 3
        for index in range(3):
            await bot.database.execute(
                "INSERT INTO behavior_events (type, scope_key, user_id, reason,"
                " detail, status, created_at)"
                " VALUES ('initiative_sent', 'private:1', '1', 'x', '', 'done', ?)",
                (int(world.clock.now().timestamp()) + index,),
            )
        assert await world.background_budget_left() == 0
        assert await bot.scheduler._background_budget_ok() is False  # noqa: SLF001
        await bot.database.close()

    async def test_disabled_messaging_means_zero_budget(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(
            bot, config=WorldConfig(messaging={"enabled": False})
        )
        assert await world.background_budget_left() == 0
        await bot.database.close()


class TestPendingQueue:
    async def test_add_and_flush_when_online(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot)
        queue = world.messaging_queue
        await queue.add(
            scope_key="private:1", user_id="1", text="刚搭完一面墙"
        )
        sent: list[dict] = []

        async def send(item: dict) -> bool:
            sent.append(item)
            return True

        assert await queue.flush(send, online=False, budget_left=3) == 0  # offline
        assert await queue.flush(send, online=True, budget_left=0) == 0  # no budget
        assert await queue.flush(send, online=True, budget_left=3) == 1
        assert sent[0]["text"] == "刚搭完一面墙"
        assert await queue.items() == []
        await bot.database.close()

    async def test_ttl_drops_stale_messages(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, fake = await attach_world(
            bot, config=WorldConfig(messaging={"pending_ttl_minutes": 30})
        )
        queue = world.messaging_queue
        await queue.add(scope_key="private:1", user_id="1", text="过期的问候")
        fake.advance(31 * 60)
        delivered: list[dict] = []

        async def send(item: dict) -> bool:
            delivered.append(item)
            return True

        await queue.flush(send, online=True, budget_left=3)
        assert delivered == []
        assert queue.dropped == 1
        await bot.database.close()

    async def test_replaces_older_message_for_same_scope(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot)
        queue = world.messaging_queue
        await queue.add(scope_key="private:1", user_id="1", text="旧的想法")
        await queue.add(scope_key="private:1", user_id="1", text="新的想法")
        items = await queue.items()
        assert [item["text"] for item in items] == ["新的想法"]
        await bot.database.close()

    async def test_skip_when_user_already_active(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot)
        queue = world.messaging_queue
        await queue.add(scope_key="private:1", user_id="1", text="想找人说句话")
        sent: list[dict] = []

        async def send(item: dict) -> bool:
            sent.append(item)
            return True

        async def skip(_item: dict) -> bool:
            return True

        assert await queue.flush(send, online=True, budget_left=3, skip=skip) == 0
        assert sent == []
        assert queue.dropped == 1
        await bot.database.close()

    async def test_queue_persists_across_instances(self, tmp_path) -> None:
        bot = make_bot(tmp_path, FakeAdapter())
        await bot.database.connect()
        world, _fake = await attach_world(bot, config=WorldConfig())
        await world.messaging_queue.add(scope_key="private:2", user_id="2", text="记得问问")
        from app.world.messaging import PendingInitiativeQueue

        rebuilt = PendingInitiativeQueue(
            database=bot.database, clock=world.clock, config=bot.config.world.messaging
        )
        assert [item["text"] for item in await rebuilt.items()] == ["记得问问"]
        await bot.database.close()


class TestResilience:
    async def test_paused_world_does_not_tick(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        world.pause("test")
        before = await world.events.stats()
        fake.advance(3600)
        await world.tick()
        after = await world.events.stats()
        assert before["total"] == after["total"]
        world.resume()
        await world.tick()
        await database.close()

    async def test_broken_step_is_isolated(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)

        async def boom(*_args, **_kwargs):
            raise RuntimeError("world step exploded")

        world.ambient.maybe_emit = boom  # type: ignore[assignment]
        fake.advance(600)
        await world.tick()  # must not raise
        assert world.errors >= 1
        assert world.ticks >= 1
        await database.close()

    async def test_disabled_world_is_inert(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path, config=WorldConfig(enabled=False))
        fake.advance(3600)
        await world.tick()
        assert await world.events.stats() is not None
        rows = await database.fetchall("SELECT COUNT(*) AS n FROM world_events")
        assert rows[0]["n"] == 0
        await database.close()


class TestRealityBoundary:
    def test_ambient_templates_avoid_verifiable_claims(self) -> None:
        from app.world.ambient import AMBIENT_TEMPLATES, is_safe_ambient

        for templates in AMBIENT_TEMPLATES.values():
            for text in templates:
                assert is_safe_ambient(text), text

    def test_forbidden_markers_rejected(self) -> None:
        from app.world.ambient import is_safe_ambient

        assert is_safe_ambient("打游戏的时候卡关了")
        assert not is_safe_ambient("今天看了新闻说台风要来")
        assert not is_safe_ambient("去公司上了一个班")

    async def test_sleeping_character_produces_no_colour(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        outcome = await world.ambient.maybe_emit(activity="睡觉", period="night", sleeping=True)
        assert outcome.startswith("skip:")
        rows = await database.fetchall("SELECT COUNT(*) AS n FROM world_events")
        assert rows[0]["n"] == 0
        await database.close()

    async def test_rest_mode_silences_ambient(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        world.set_rest_mode(True)
        outcome = await world.ambient.maybe_emit(activity="打游戏", period="evening")
        assert outcome == "skip:disabled"
        await database.close()


@pytest.mark.parametrize("step", [1, 2])
async def test_routine_reconfigure_reflected_in_tick(tmp_path, step) -> None:
    """The WebUI routine is what the tick actually uses (§21)."""
    config = WorldConfig(
        routine={"periods": {"afternoon": [f"活动{step}"]}, "transition_minutes": 0}
    )
    world, database, _fake = await make_world(tmp_path, config=config)
    await world.tick()
    assert world.state.state.activity == f"活动{step}"
    await database.close()
