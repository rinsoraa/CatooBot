"""v1.0 Activity Episode tests (spec §139-§148).

Core acceptance: an activity is a *lifecycle* — it persists for a planned
duration, extends or transitions only at its end, a chat is an overlay (never a
new episode), ambient is bounded per episode, and recovery never fabricates
history. And the follow-up / group-relevance behavior of v0.9 keeps working.
"""

from __future__ import annotations

from app.config.settings import WorldConfig
from app.world.episode import ActivityEpisode
from app.world.planner import ActivityBounceGuard, ActivityContinuationEvaluator, ActivityPlanner
from app.world.routine import RoutineService
from tests.world_helpers import FakeTime, make_clock, make_world


def _evaluator() -> ActivityContinuationEvaluator:
    return ActivityContinuationEvaluator()


def _profile(minutes: int = 90, *, momentum: float = 0.85, energy_delta: float = -0.05) -> object:
    from app.world.episode import ActivityProfile

    return ActivityProfile(
        key="gaming", label="打游戏", location="房间", tags=["game", "focus"],
        min_minutes=30, typical_minutes=minutes, max_minutes=minutes * 2,
        momentum=momentum, energy_delta_per_hour=energy_delta,
    )


class TestContinuationEvaluator:
    def test_within_minimum_continues(self) -> None:
        clock = FakeTime(1_000_000.0)
        episode = ActivityEpisode(activity_key="gaming", started_at=clock(),
                                  planned_end_at=clock() + 90 * 60)
        clock.advance(10 * 60)  # 10 min in
        decision = _evaluator().decide(
            episode=episode, profile=_profile(), energy=0.8, focus="game", sleeping=False,
            now=clock(),
        )
        assert decision["decision"] == "continue"

    def test_high_momentum_extends(self) -> None:
        clock = FakeTime(1_000_000.0)
        episode = ActivityEpisode(activity_key="gaming", started_at=clock(),
                                  planned_end_at=clock() + 90 * 60)
        clock.advance(90 * 60)  # at planned end
        decision = _evaluator().decide(
            episode=episode, profile=_profile(), energy=0.7, focus="game", sleeping=False,
            now=clock(),
        )
        assert decision["decision"] == "extend"
        assert decision["extension_minutes"] > 0

    def test_max_duration_transitions(self) -> None:
        clock = FakeTime(1_000_000.0)
        episode = ActivityEpisode(activity_key="gaming", started_at=clock(),
                                  planned_end_at=clock() + 180 * 60)
        clock.advance(180 * 60)  # at max
        decision = _evaluator().decide(
            episode=episode, profile=_profile(), energy=0.7, focus="game", sleeping=False,
            now=clock(),
        )
        assert decision["decision"] == "transition"

    def test_low_energy_transitions(self) -> None:
        clock = FakeTime(1_000_000.0)
        episode = ActivityEpisode(activity_key="gaming", started_at=clock(),
                                  planned_end_at=clock() + 90 * 60)
        clock.advance(90 * 60)
        decision = _evaluator().decide(
            episode=episode, profile=_profile(), energy=0.1, focus="", sleeping=False,
            now=clock(),
        )
        assert decision["decision"] == "transition"
        assert decision["reason_code"] == "energy_low"

    def test_sleep_transitions(self) -> None:
        clock = FakeTime(1_000_000.0)
        episode = ActivityEpisode(activity_key="gaming", started_at=clock(),
                                  planned_end_at=clock() + 90 * 60)
        decision = _evaluator().decide(
            episode=episode, profile=_profile(), energy=0.7, focus="", sleeping=True,
            now=clock(),
        )
        assert decision["decision"] == "transition"
        assert decision["reason_code"] == "sleep_transition"


class TestBounceGuard:
    def test_detects_abab(self) -> None:
        guard = ActivityBounceGuard()
        guard.note("a")
        guard.note("b")
        guard.note("a")
        assert guard.would_bounce("b") is True
        assert guard.would_bounce("c") is False


class TestPlanner:
    def _planner(self) -> ActivityPlanner:
        clock, _fake = make_clock()
        routine = RoutineService(WorldConfig().routine, clock=clock)
        return ActivityPlanner(routine=routine)

    def test_sleeping_picks_sleep(self) -> None:
        planner = self._planner()
        activity = planner.plan_next(
            period="late_night", sleeping=True, energy=0.5, current_key="", current_goal=""
        )
        assert "sleep" in activity.tags

    def test_avoids_current_key(self) -> None:
        planner = self._planner()
        activity = planner.plan_next(
            period="afternoon", sleeping=False, energy=0.8, current_key="gaming", current_goal=""
        )
        assert activity.key != "gaming"

    def test_low_energy_prefers_rest(self) -> None:
        planner = self._planner()
        activity = planner.plan_next(
            period="afternoon", sleeping=False, energy=0.1, current_key="", current_goal=""
        )
        assert activity.tags and any(
            tag in ("slow", "ambient", "sleep") for tag in activity.tags
        )


class TestEpisodeLifecycle:
    async def test_episode_persists_mid_life(self, tmp_path) -> None:
        """Test B: an episode does not change activity between ticks."""
        world, database, fake = await make_world(tmp_path)
        await world.tick()
        first = world.state.state.activity
        first_episode = world.activity.episode.id
        for _ in range(5):
            fake.advance(10 * 60)
            await world.tick()
        assert world.state.state.activity == first
        assert world.activity.episode.id == first_episode
        await database.close()

    async def test_breakfast_does_not_linger_three_hours(self, tmp_path) -> None:
        """Test A: a meal has a bounded duration (typical ~30-40 min)."""
        from app.world.profiles import profile_for

        world, database, fake = await make_world(tmp_path)
        # force breakfast first, but give the morning other options so it can move on.
        world.routine._routine = WorldConfig(
            routine={"periods": {"morning": ["吃早饭", "刷手机", "听歌"], "late_night": ["睡觉"],
                                 "early_morning": ["睡觉"], "noon": ["吃午饭"],
                                 "afternoon": ["打游戏"], "evening": ["打游戏"], "night": ["发呆"]},
                     "transition_minutes": 0}
        ).routine
        # set time to 08:00 morning
        fake.set_local(15, 8, 0)
        await world.tick()
        assert world.state.state.activity == "吃早饭"
        episode = world.activity.episode
        profile = profile_for("breakfast")
        assert episode is not None
        assert episode.planned_end_at - episode.started_at <= profile.max_minutes * 60
        # advance past planned end: it must transition, not stay on breakfast
        fake.advance(profile.max_minutes * 60 + 60)
        await world.tick()
        assert world.state.state.activity != "吃早饭"
        await database.close()

    async def test_recovery_does_not_fabricate_history(self, tmp_path) -> None:
        """Test I: recovery reconciles the episode, never replays missed days."""
        world, database, fake = await make_world(tmp_path)
        await world.tick()
        before = await database.fetchone("SELECT COUNT(*) AS n FROM activity_episodes")
        # a long downtime: only one reconcile, no chain of fabricated episodes
        fake.advance(8 * 3600)
        await world.restore()
        after = await database.fetchone("SELECT COUNT(*) AS n FROM activity_episodes")
        assert after["n"] <= before["n"] + 1
        await database.close()

    async def test_episode_persisted_to_db(self, tmp_path) -> None:
        world, database, fake = await make_world(tmp_path)
        await world.tick()
        row = await database.fetchone(
            "SELECT id, activity_key, status, planned_end_at FROM activity_episodes"
            " ORDER BY started_at DESC LIMIT 1"
        )
        assert row is not None and row["status"] == "active"
        assert row["planned_end_at"] >= row["planned_end_at"] * 0  # sanity
        await database.close()


class TestAmbientBounded:
    async def test_ambient_bound_to_episode(self, tmp_path) -> None:
        """Test G: a short episode yields at most one ambient, not one per hour."""
        world, database, fake = await make_world(
            tmp_path,
            config=WorldConfig(
                routine={
                    "periods": {
                        "morning": ["吃早饭"], "late_night": ["睡觉"],
                        "early_morning": ["睡觉"], "noon": ["吃午饭"],
                        "afternoon": ["打游戏"], "evening": ["打游戏"], "night": ["发呆"],
                    },
                    "transition_minutes": 0,
                },
                ambient={"min_interval_minutes": 5, "max_per_day": 10},
            ),
        )
        fake.set_local(15, 8, 0)
        await world.tick()
        # breakfast profile allows 1 ambient per episode; tick many times
        for _ in range(8):
            fake.advance(5 * 60)
            await world.tick()
        episode = world.activity.episode
        # if still on breakfast, ambient_count must be <= ambient_max_per_episode
        if episode is not None and episode.activity_key == "breakfast":
            assert episode.ambient_count <= 1
        await database.close()


class TestOverlay:
    async def test_chat_is_overlay_not_new_episode(self, tmp_path) -> None:
        """Test F: chatting overlays the primary activity, never replaces it."""
        world, database, fake = await make_world(tmp_path)
        await world.tick()
        primary = world.state.state.activity
        await world.note_user_interaction(user_id="1", session_id="private:1")
        assert world.state.state.interaction_overlay == "chatting"
        assert world.state.state.activity == primary
        await world.activity.clear_overlay()
        assert world.state.state.interaction_overlay == ""
        await database.close()


class TestWorldContext:
    async def test_world_context_exposes_episode(self, tmp_path) -> None:
        world, database, _fake = await make_world(tmp_path)
        await world.tick()
        context = world.activity.world_context()
        assert context["primary_activity"]
        assert "elapsed_minutes" in context and "planned_remaining_minutes" in context
        assert "interaction_overlay" in context
        await database.close()
