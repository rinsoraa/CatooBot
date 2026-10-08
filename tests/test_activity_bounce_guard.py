"""Phase 6B §十七/§十八：撞车护栏（矩阵 G）。

防止 A → B → A 快速来回跳；拒绝之后要么继续当前（能延长就延长），要么换一个**中性**活动。
必须**确定性**。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.activity import (
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    InMemoryActivityStore,
)
from app.activity.decision import (
    ActivityBounceGuard,
    DecisionReason,
)

#: 短档：typical 10 分钟，便于快速制造"刚做完"
SHORT = (5 * 60.0, 10 * 60.0, 90 * 60.0)


class _States:
    async def update(self, **_changes: Any) -> dict[str, Any]:
        return {}


class Rig:
    def __init__(self, *, cooldown: float = 600.0) -> None:
        self.clock = FakeClock(1_700_000_000.0)
        self.runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=self.clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(),
            projection=ActivityProjection(_States()),
            transition_window_seconds=60.0,
            max_extensions_per_episode=2,
            bounce_cooldown_seconds=cooldown,
        )

    async def run_activity(self, name: str, *, minutes: float) -> Any:
        episode = await self.runtime.start(
            activity_name=name,
            activity_type=ActivityType.VIRTUAL_LIFE,
            source=ActivitySource.ROUTINE,
            duration=SHORT,
            now=self.clock.now(),
        )
        assert episode is not None
        self.clock.advance_minutes(minutes)
        await self.runtime.complete(now=self.clock.now())
        return episode


@pytest.fixture()
def rig() -> Rig:
    return Rig()


class TestBounceGuard:
    def test_rejects_a_candidate_that_just_ended(self) -> None:
        """§十八：B 刚结束（< cooldown）就想回到 A → 拒绝。"""
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=600.0, clock=clock)
        from app.activity import ActivityEpisode

        recent = ActivityEpisode(
            episode_id="ACT-1",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="gaming",
            status=ActivityStatus.COMPLETED,
            started_at=100.0,
            ended_at=clock.now() - 60.0,
        )
        current = ActivityEpisode(
            episode_id="ACT-2",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now() - 600.0,
        )
        verdict = guard.check(
            current=current, candidate="gaming", history=[recent], now=clock.now()
        )
        assert not verdict.ok
        assert verdict.code is DecisionReason.BOUNCE_GUARD
        assert verdict.detail["reason"] == "cooldown"
        assert guard.rejections == 1

    def test_allows_after_the_cooldown(self) -> None:
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=600.0, clock=clock)
        from app.activity import ActivityEpisode

        recent = ActivityEpisode(
            episode_id="ACT-1",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="gaming",
            status=ActivityStatus.COMPLETED,
            started_at=0.0,
            ended_at=clock.now() - 3600.0,  # 一小时前
        )
        current = ActivityEpisode(
            episode_id="ACT-2",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now() - 600.0,
        )
        assert guard.check(
            current=current, candidate="gaming", history=[recent], now=clock.now()
        ).ok

    def test_live_episodes_do_not_trigger_the_guard(self) -> None:
        """还活着的同名不是"刚跳走过"（那是当前活动自己）。"""
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=600.0, clock=clock)
        from app.activity import ActivityEpisode

        live = ActivityEpisode(
            episode_id="ACT-1",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="gaming",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now(),
        )
        current = ActivityEpisode(
            episode_id="ACT-2",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now() - 900.0,
        )
        assert guard.check(current=current, candidate="gaming", history=[live], now=clock.now()).ok

    def test_minimum_stable_rejects_a_too_short_episode(self) -> None:
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=0.0, minimum_stable_seconds=120.0, clock=clock)
        from app.activity import ActivityEpisode

        current = ActivityEpisode(
            episode_id="ACT-1",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now() - 10.0,
        )
        verdict = guard.check(current=current, candidate="gaming", history=[], now=clock.now())
        assert not verdict.ok and verdict.detail["reason"] == "not_stable_yet"

    def test_empty_candidate_is_refused(self) -> None:
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=600.0, clock=clock)
        from app.activity import ActivityEpisode

        current = ActivityEpisode(
            episode_id="ACT-1",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            status=ActivityStatus.ACTIVE,
            started_at=clock.now() - 600.0,
        )
        assert not guard.check(current=current, candidate="", history=[], now=clock.now()).ok

    def test_neutral_fallback_is_deterministic(self) -> None:
        clock = FakeClock()
        guard = ActivityBounceGuard(cooldown_seconds=600.0, clock=clock)
        for period in ("morning", "afternoon", "evening", "night", "unknown"):
            assert guard.neutral_fallback(period=period) == guard.neutral_fallback(period=period)
        assert guard.neutral_fallback(period="morning") == "idle"
        assert guard.neutral_fallback(period="night") == "napping"


class TestBounceInTheEngine:
    """引擎里的撞车护栏：候选必须过护栏，撞车时不许立刻回到刚做过的活动。"""

    async def test_g_engine_does_not_switch_back_within_the_cooldown(self) -> None:
        rig = Rig(cooldown=3600.0)
        rig.runtime.engine.guard.max_extensions = 0  # 逼它必须换活动
        await rig.run_activity("gaming", minutes=11)  # 刚做完 gaming（一分钟前）
        reading = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert reading is not None
        rig.clock.advance_minutes(11)  # reading 到期 → 必须换
        after = await rig.runtime.advance()
        assert after is not None
        assert after.episode_id != reading.episode_id
        assert after.activity_name != "gaming", "刚做过的活动不许立刻回来"
        traces = rig.runtime.engine.traces
        assert "bounce" in traces[-1].guard_results, "换活动必须过撞车护栏"

    async def test_g_bounce_rejection_falls_back_to_a_neutral_activity(self) -> None:
        """§十八：候选撞车 → 换一个**中性**活动（确定性），并如实记 BOUNCE_GUARD。"""

        class GamingPlanner(ActivityPlanner):
            """总是建议 gaming（正是刚做过的那件事）。"""

            def next_after(self, *args: Any, **kwargs: Any) -> Any:
                from app.activity.planner import ActivityDecision as PlannerDecision

                return PlannerDecision(
                    action="next",
                    reason=__import__(
                        "app.activity", fromlist=["TransitionReason"]
                    ).TransitionReason.TIME_EXPIRED,
                    activity_name="gaming",
                )

        rig = Rig(cooldown=3600.0)
        rig.runtime.engine.planner = GamingPlanner()
        rig.runtime.engine.guard.max_extensions = 0
        await rig.run_activity("gaming", minutes=11)
        reading = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert reading is not None
        rig.clock.advance_minutes(11)
        after = await rig.runtime.advance()
        assert after is not None
        assert after.episode_id != reading.episode_id
        assert after.activity_name == "idle"  # 中性活动（上午/下午/傍晚都是 idle）
        trace = rig.runtime.engine.traces[-1]
        assert trace.reason_code == DecisionReason.BOUNCE_GUARD.value
        assert trace.guard_results["bounce"]["ok"] is False

    async def test_g_candidate_passes_the_guard_when_not_recent(self) -> None:
        """没在冷却期内的候选正常放行（不是"一律拒绝换活动"）。"""
        rig = Rig(cooldown=3600.0)
        rig.runtime.engine.guard.max_extensions = 0
        await rig.run_activity("gaming", minutes=11)
        rig.clock.advance_minutes(60 * 60)  # 一小时后再换（远超 cooldown）
        reading = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert reading is not None
        rig.clock.advance_minutes(11)
        after = await rig.runtime.advance()
        assert after is not None
        assert after.episode_id != reading.episode_id
        assert after.activity_name != reading.activity_name
        assert rig.runtime.engine.traces[-1].guard_results["bounce"]["ok"] is True
