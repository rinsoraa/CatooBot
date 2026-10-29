"""Phase 6B §二十/§二一：World Consistency Checker（矩阵 I/J）。

**只检测、只报告，绝不自动修**（§二一）；发现异常必须 ERROR / WARNING，不许静默吞掉。
"""

from __future__ import annotations

from typing import Any

from app.activity import (
    ActivityEpisode,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    InMemoryActivityStore,
)
from app.activity.decision import DecisionTrigger, WorldConsistencyChecker

PROFILE = (10 * 60.0, 60 * 60.0, 150 * 60.0)


def episode(**overrides: Any) -> ActivityEpisode:
    base: dict[str, Any] = {
        "episode_id": "ACT-20261008-001",
        "character_id": "罐头@deadbeef",
        "activity_type": ActivityType.VIRTUAL_LIFE,
        "activity_name": "reading",
        "status": ActivityStatus.ACTIVE,
        "started_at": 1_700_000_000.0,
        "planned_end_at": 1_700_003_600.0,
        "min_duration": PROFILE[0],
        "typical_duration": PROFILE[1],
        "max_duration": PROFILE[2],
        "source": ActivitySource.ROUTINE,
    }
    base.update(overrides)
    return ActivityEpisode(**base)


class TestRules:
    def test_clean_episode_passes(self) -> None:
        report = WorldConsistencyChecker().check(current=episode(), now=1_700_000_100.0)
        assert report.ok and not report.warnings
        assert report.checked == 1

    def test_rule1_started_at_in_the_future(self) -> None:
        report = WorldConsistencyChecker().check(
            current=episode(started_at=1_700_010_000.0), now=1_700_000_000.0
        )
        assert not report.ok
        assert [item["rule"] for item in report.errors] == ["started_at_in_future"]

    def test_rule2_ended_before_started(self) -> None:
        report = WorldConsistencyChecker().check(
            current=episode(ended_at=1_699_999_000.0, status=ActivityStatus.COMPLETED),
            now=1_700_000_100.0,
        )
        assert not report.ok
        assert "ended_before_started" in [item["rule"] for item in report.errors]

    def test_rule3_two_live_primaries(self) -> None:
        """§三六/§二十：同一个角色同时只允许一个 live primary Episode。"""
        first, second = episode(), episode(episode_id="ACT-20261008-002")
        report = WorldConsistencyChecker().check(
            current=first, now=1_700_000_100.0, live=[first, second]
        )
        assert not report.ok
        assert report.errors[0]["rule"] == "single_live_primary"
        assert report.errors[0]["detail"] == ["ACT-20261008-001", "ACT-20261008-002"]

    def test_rule4_current_must_be_live(self) -> None:
        report = WorldConsistencyChecker().check(
            current=episode(status=ActivityStatus.COMPLETED), now=1_700_000_100.0
        )
        assert not report.ok
        assert "current_not_live" in [item["rule"] for item in report.errors]

    def test_rule4_can_be_forced_for_read_only_checks(self) -> None:
        """读只读投影时允许传终态 Episode（``force=True``）—— 仍然报其它规则。"""
        report = WorldConsistencyChecker().check(
            current=episode(status=ActivityStatus.COMPLETED), now=1_700_000_100.0, force=True
        )
        assert report.ok

    def test_rule5_location_conflict_is_a_warning(self) -> None:
        """§二十 Rule 5：sleeping + kitchen 至少 WARNING，绝不静默吞掉。"""
        report = WorldConsistencyChecker().check(
            current=episode(activity_name="sleeping", location="kitchen"),
            now=1_700_000_100.0,
        )
        assert report.ok  # 只是 WARNING，不是 ERROR
        assert [item["rule"] for item in report.warnings] == ["activity_location_conflict"]
        assert report.to_payload()["warnings"][0]["activity"] == "sleeping"

    def test_duration_profile_must_be_ordered(self) -> None:
        report = WorldConsistencyChecker().check(
            current=episode(min_duration=900.0, typical_duration=600.0, max_duration=1200.0),
            now=1_700_000_100.0,
        )
        assert not report.ok
        assert "duration_profile_invalid" in [item["rule"] for item in report.errors]

    def test_no_episode_is_not_an_error(self) -> None:
        """她没有活动是合法状态（6A §五十二），一致性检查不该报错。"""
        report = WorldConsistencyChecker().check(current=None, now=1_700_000_100.0)
        assert report.ok and report.checked == 1


class TestDetectOnly:
    def test_checker_has_no_write_api(self) -> None:
        """§二一：检查器没有"修"的能力 —— 连 store 都不认识。"""
        checker = WorldConsistencyChecker()
        for forbidden in ("fix", "repair", "mutate", "delete", "cancel", "transition", "store"):
            assert not hasattr(checker, forbidden)

    async def test_runtime_reports_but_does_not_mutate(self) -> None:
        """运行时把结论放在只读投影里；`current()` 一行都没被改。"""
        clock = FakeClock(1_700_000_000.0)
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            projection=ActivityProjection(None),
        )
        created = await runtime.start(
            activity_name="sleeping", location="kitchen", duration=PROFILE, now=clock.now()
        )
        assert created is not None
        status = await runtime.status()
        assert status["consistency"]["ok"] is True
        assert status["consistency"]["warnings"][0]["rule"] == "activity_location_conflict"
        current = await runtime.current()
        assert current is not None
        assert current.episode_id == created.episode_id
        assert current.status is ActivityStatus.ACTIVE  # 没有被"自动修掉"

    async def test_decision_continues_on_an_inconsistent_episode(self) -> None:
        """J（无效 Episode）：决策引擎**不会**拿一个自相矛盾的 Episode 去换活动 ——
        它只如实 CONTINUE + NO_VALID_TRANSITION（修不修由 Runtime 的恢复流程决定）。"""
        import dataclasses

        clock = FakeClock(1_700_000_000.0)
        broken = dataclasses.replace(
            episode(episode_id="ACT-20261008-777"),
            min_duration=900.0,
            typical_duration=600.0,
            max_duration=300.0,
            started_at=clock.now() - 61 * 60.0,
            planned_end_at=clock.now() - 60.0,
        )

        class BrokenStore(InMemoryActivityStore):
            """只把这一条坏行喂给运行时（模拟库里的历史脏数据）。"""

            async def live(self, character_id: str) -> Any:
                return broken

            async def recent(self, character_id: str, limit: int = 10) -> list[Any]:
                return [broken]

        runtime = ActivityRuntime(
            store=BrokenStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            projection=ActivityProjection(None),
        )
        decision = await runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert decision["decision"] == "CONTINUE"
        assert decision["reason"] == "NO_VALID_TRANSITION"
        assert decision["trace"]["guard_results"]["consistency"]["ok"] is False
        # 检查器只报告：坏行**没有**被自动改掉
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.ACTIVE
        assert current.typical_duration == 600.0


# ---------------------------------------------------------------- Phase 6C：计划不是现实


class TestPlansAreNotReality:
    """§四十六：计划只是 intent —— 它过期/为空都**不是** Episode 的不一致。"""

    async def test_stale_plan_never_shows_up_as_a_consistency_error(self) -> None:
        from app.activity import (
            ActivityPlanner,
            ActivityRuntime,
            FakeClock,
            InMemoryActivityStore,
            PlanTrigger,
        )

        clock = FakeClock(1_700_000_000.0)
        runtime = ActivityRuntime(
            store=InMemoryActivityStore(),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
        )
        await runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=clock.now(), force=True)
        clock.advance_hours(12)  # 计划整段过去了
        report = runtime.run_consistency_check(None, now=clock.now())
        assert report["ok"] is True, report
        assert runtime.plan_view()["stale"] is True

    async def test_no_plan_is_not_an_episode_error(self) -> None:
        from app.activity import ActivityRuntime, FakeClock, InMemoryActivityStore

        runtime = ActivityRuntime(
            store=InMemoryActivityStore(), clock=FakeClock(1_700_000_000.0), character_id="c"
        )
        report = runtime.run_consistency_check(None, now=runtime._now(None))  # noqa: SLF001
        assert report["ok"] is True
        assert runtime.plan_view()["enabled"] is False
