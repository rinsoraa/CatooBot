"""Phase 6C §三十四-§三十九：候选的拒绝、评分与 tie-break（矩阵 B/C/D/I/K/L/M）。

这一层就是 6C 的"判断力"：**确定性加权求和** + 固定 tie-break。
所以这里的每个断言都在问同一件事：**同样的输入，分数与顺序是不是可复现、可解释**。
"""

from __future__ import annotations

import pytest

from app.activity import (
    ACTIVITY_PROFILES,
    LOW_ENERGY_THRESHOLD,
    SCORE_WEIGHTS,
    ActivityPlanner,
    PlannerContext,
    RejectionReason,
    profile_for,
)
from app.activity.plan import Candidate
from app.activity.planner import CATEGORY_FIT_DISFAVORED
from app.activity.profiles import (
    FOCUS_PREFERRED_HIGH,
    FOCUS_PREFERRED_LOW,
    TIE_BREAK_ORDER,
)
from tests.activity_plan_fakes import GoalSourceStub, State, clock_at, goal


def judge(
    activity: str,
    *,
    hour: int = 14,
    minute: int = 0,
    state: State | None = None,
    goals: GoalSourceStub | None = None,
    history: tuple = (),
    planner: ActivityPlanner | None = None,
):
    """直接问 Planner："这个候选现在什么资格、多少分？"（只读，不改任何东西）。"""
    clock = clock_at(hour, minute)
    engine = planner or ActivityPlanner(state_provider=lambda: state)
    ctx = PlannerContext(
        clock=clock,
        now=clock.now(),
        character_state=state or State(),
        history=history,
        goals=goals.snapshot() if goals is not None else None,
        anchors=engine.anchors,
    )
    return engine._judge(activity, ctx, order=0)  # noqa: SLF001 - 测试直接问单点判定


# ---------------------------------------------------------------- C/D：确定性排序


class TestDeterministicRanking:
    def test_c_scores_are_a_weighted_sum_of_named_terms(self) -> None:
        """§三十六：分数 = 各项加权和，每一项都能单独解释（不是黑箱）。"""
        candidate = judge("gaming")
        assert set(candidate.breakdown) == set(SCORE_WEIGHTS)
        assert candidate.score == pytest.approx(sum(candidate.breakdown.values()), abs=1e-6)
        # 只有"重复惩罚"是负向项；其余都是加分项（权重的符号必须真的生效）
        assert candidate.breakdown["repetition_penalty"] <= 0
        for term in SCORE_WEIGHTS:
            if term != "repetition_penalty":
                assert candidate.breakdown[term] >= 0, term

    def test_c_ranking_is_stable_across_runs(self) -> None:
        clock = clock_at(14)
        engine = ActivityPlanner()
        ctx = PlannerContext(clock=clock, now=clock.now(), character_state=State())
        first = engine._rank(engine._collect_candidates(ctx))  # noqa: SLF001
        second = engine._rank(engine._collect_candidates(ctx))  # noqa: SLF001
        assert [c.activity for c in first] == [c.activity for c in second]
        assert [round(c.score, 9) for c in first] == [round(c.score, 9) for c in second]

    def test_d_tie_break_is_fixed_and_documented(self) -> None:
        """§三十七：分数打平时按固定顺序比 —— 锚点优先级 → 目标 → 习惯 → 活动名。"""
        assert TIE_BREAK_ORDER == ("anchor_priority", "goal_priority", "routine_priority", "name")
        engine = ActivityPlanner()
        # 同分且没有锚点/目标/习惯差别时，最后由"稳定的活动名"（字典序）决定
        left = Candidate(activity="aaa", eligible=True, score=1.0, order=3)
        right = Candidate(activity="bbb", eligible=True, score=1.0, order=0)
        ranked = engine._rank([right, left])  # noqa: SLF001
        assert [c.activity for c in ranked] == ["aaa", "bbb"]
        # 生成顺序**不**参与 tie-break（§三十七 只列了那四道）
        assert [c.activity for c in engine._rank([left, right])] == ["aaa", "bbb"]  # noqa: SLF001

    def test_d_anchor_priority_wins_a_tie(self) -> None:
        """同分时锚点优先：有锚点在等的候选排在前面（§十三 的优先级在 tie-break 里生效）。"""
        clock = clock_at(12, 5)  # 午饭窗口
        engine = ActivityPlanner()
        ctx = PlannerContext(clock=clock, now=clock.now(), character_state=State())
        candidates = engine._rank(engine._collect_candidates(ctx))  # noqa: SLF001
        assert candidates[0].activity == "eating"
        assert candidates[0].anchor_id == "lunch"

    def test_d_eligible_candidates_always_rank_before_rejected(self) -> None:
        engine = ActivityPlanner()
        rejected = Candidate(activity="zzz", eligible=False, score=99.0, order=0)
        eligible = Candidate(activity="aaa", eligible=True, score=0.1, order=1)
        assert [c.activity for c in engine._rank([rejected, eligible])] == ["aaa", "zzz"]  # noqa: SLF001


# ---------------------------------------------------------------- K：能量硬规则


class TestEnergyRules:
    def test_k_very_low_energy_rejects_costly_activities(self) -> None:
        """§二十八：``energy=0.15`` 时不能排 gaming 两小时。"""
        candidate = judge("gaming", state=State(energy=0.15))
        assert candidate.eligible is False
        assert candidate.reason == RejectionReason.ENERGY_TOO_LOW.value

    def test_k_very_low_energy_keeps_rest_and_light_activities(self) -> None:
        for activity in ("sleeping", "napping", "resting", "free_time", "music", "reading"):
            candidate = judge(activity, hour=14, state=State(energy=0.15))
            assert candidate.reason != RejectionReason.ENERGY_TOO_LOW.value, activity

    def test_k_threshold_is_inclusive_above(self) -> None:
        just_above = State(energy=LOW_ENERGY_THRESHOLD + 0.01)
        assert judge("gaming", state=just_above).eligible is True
        just_below = State(energy=LOW_ENERGY_THRESHOLD - 0.01)
        assert judge("gaming", state=just_below).eligible is False

    def test_k_rest_activities_score_higher_when_tired(self) -> None:
        """能量越低，"休息类"越被偏好（state_fit 的确定性方向）。"""
        tired = judge("resting", state=State(energy=0.2))
        fresh = judge("resting", state=State(energy=0.95))
        assert tired.breakdown["energy_fit"] > fresh.breakdown["energy_fit"]

    def test_k_high_cost_activities_need_energy(self) -> None:
        tired = judge("working", state=State(energy=0.35))
        fresh = judge("working", state=State(energy=0.95))
        assert fresh.breakdown["energy_fit"] > tired.breakdown["energy_fit"]

    def test_k_exempt_list_is_empty_by_default(self) -> None:
        """§二十八 的"除非角色 profile 明确允许"是一个**显式**白名单，默认没有任何豁免。"""
        from app.activity.profiles import LOW_ENERGY_EXEMPT

        assert frozenset() == LOW_ENERGY_EXEMPT


# ---------------------------------------------------------------- L：专注（软信号）


class TestFocusInfluence:
    def test_l_high_focus_prefers_deep_activities(self) -> None:
        """§二十九：专注高 → reading / building 更合适（**只是**排序信号）。"""
        deep = judge("reading", state=State(energy=0.9, current_focus=0.9))
        light = judge("music", state=State(energy=0.9, current_focus=0.9))
        assert deep.breakdown["focus_fit"] > light.breakdown["focus_fit"]

    def test_l_low_focus_prefers_light_activities(self) -> None:
        deep = judge("reading", state=State(energy=0.9, current_focus=0.1))
        light = judge("music", state=State(energy=0.9, current_focus=0.1))
        # 专注低时"轻活动"拿到满分专注契合，而阅读只拿到"方向对但不匹配"的折扣分
        assert light.breakdown["focus_fit"] > deep.breakdown["focus_fit"]

    def test_l_focus_never_rejects_anything(self) -> None:
        """§二十九：专注**不是**硬动作 —— 专注再低也不会把 reading 判死。"""
        for activity in FOCUS_PREFERRED_HIGH | FOCUS_PREFERRED_LOW:
            candidate = judge(activity, hour=14, state=State(energy=0.9, current_focus=0.0))
            assert candidate.reason in ("", RejectionReason.NOT_MOVEABLE.value), activity

    def test_l_text_focus_field_degrades_to_neutral(self) -> None:
        """既有字段 ``current_focus`` 是**文字**时（例如 "reading"）→ 专注未知 → 中性。"""
        neutral = judge("gaming", state=State(current_focus=""))
        texty = judge("gaming", state=State(current_focus="reading"))
        assert texty.breakdown == neutral.breakdown


# ---------------------------------------------------------------- M：历史惩罚


class TestHistoryPenalty:
    def test_m_recent_repetition_lowers_the_score(self) -> None:
        """§三十：刚做过的事不要再排 —— 重复越多，惩罚越重。"""
        from app.activity.model import ActivityEpisode, ActivitySource, ActivityType

        def episode(name: str, at: float) -> ActivityEpisode:
            return ActivityEpisode(
                episode_id=f"ACT-20261015-{int(at)}",
                character_id="c",
                activity_type=ActivityType.VIRTUAL_LIFE,
                activity_name=name,
                source=ActivitySource.ROUTINE,
                started_at=at,
                created_at=at,
            )

        clock = clock_at(14)
        fresh = judge("gaming")
        repeated = judge(
            "gaming",
            history=(
                episode("gaming", clock.now() - 600),
                episode("gaming", clock.now() - 1800),
                episode("gaming", clock.now() - 3600),
            ),
        )
        assert repeated.breakdown["repetition_penalty"] < fresh.breakdown["repetition_penalty"]
        assert repeated.score < fresh.score

    def test_m_recent_other_activities_do_not_penalize(self) -> None:
        from app.activity.model import ActivityEpisode, ActivitySource, ActivityType

        other = ActivityEpisode(
            episode_id="ACT-20261015-001",
            character_id="c",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="reading",
            source=ActivitySource.ROUTINE,
            started_at=1.0,
            created_at=1.0,
        )
        assert judge("gaming", history=(other,)).breakdown["repetition_penalty"] == 0.0

    def test_m_history_is_bounded_to_the_last_five(self) -> None:
        """§三十：Planner 最多读最近 ≤5 个 Episode（不扫全表）。"""
        from app.activity.model import ActivityEpisode, ActivitySource, ActivityType

        many = tuple(
            ActivityEpisode(
                episode_id=f"ACT-20261015-{index:03d}",
                character_id="c",
                activity_type=ActivityType.VIRTUAL_LIFE,
                activity_name="gaming",
                source=ActivitySource.ROUTINE,
                started_at=float(index),
                created_at=float(index),
            )
            for index in range(30)
        )
        five = judge("gaming", history=many)
        exactly_five = judge("gaming", history=many[:5])
        assert five.breakdown == exactly_five.breakdown


# ---------------------------------------------------------------- I：目标只影响排名


class TestGoalRelevanceRanking:
    def test_i_goal_raises_the_score_of_affine_activities(self) -> None:
        """§二十二/§三十八：目标让相关活动的**排名提高**（不是"必须选它"）。"""
        source = GoalSourceStub(goals=(goal(kind="complete_project", priority=0.9),))
        without = judge("building")
        with_goal = judge("building", goals=source, planner=ActivityPlanner(goal_source=source))
        assert with_goal.breakdown["goal_relevance"] > 0.0
        assert with_goal.score > without.score
        assert with_goal.goal_id == "g1"

    def test_i_unrelated_activities_get_no_bonus(self) -> None:
        source = GoalSourceStub(goals=(goal(kind="complete_project", priority=0.9),))
        candidate = judge("gaming", goals=source, planner=ActivityPlanner(goal_source=source))
        assert candidate.breakdown["goal_relevance"] == 0.0

    def test_i_goal_never_forces_the_selection(self) -> None:
        """§二十三/§三十八：目标再强也压不过"现在该睡觉"这种硬生活约束。"""
        source = GoalSourceStub(goals=(goal(kind="complete_project", priority=1.0),))
        planner = ActivityPlanner(goal_source=source)
        clock = clock_at(23, 30)  # 睡觉窗口
        ctx = PlannerContext(
            clock=clock,
            now=clock.now(),
            character_state=State(),
            goals=source.snapshot(),
            anchors=planner.anchors,
        )
        plan = planner.plan_next(State(), None, now=clock.now(), context=ctx)
        assert plan.items[0].activity == "sleeping"


# ---------------------------------------------------------------- E：习惯与时段


class TestRoutineAndPeriod:
    def test_e_period_habits_are_preferred(self) -> None:
        """§三十九：时段表里的活动拿到习惯分（表里越靠前越高）。"""
        afternoon = judge("reading", hour=14)  # 下午表第一项
        assert afternoon.breakdown["routine_preference"] > 0
        not_in_table = judge("pet_care", hour=14)
        assert not_in_table.breakdown["routine_preference"] == 0

    def test_e_fixed_activities_do_not_ride_on_habits(self) -> None:
        """固定活动（三餐/睡觉）的位置由锚点决定 —— 不给习惯分，免得整天排饭。"""
        eating_in_window = judge("eating", hour=12, minute=30)
        assert eating_in_window.breakdown["routine_preference"] == 0.0

    def test_e_category_fit_follows_the_period(self) -> None:
        """时段—类别契合：夜里"工作/家务"明显不合适。"""
        night_work = judge("working", hour=2)
        day_work = judge("working", hour=14)
        assert night_work.breakdown["time_period_fit"] == pytest.approx(
            CATEGORY_FIT_DISFAVORED * SCORE_WEIGHTS["time_period_fit"]
        )
        assert day_work.breakdown["time_period_fit"] > night_work.breakdown["time_period_fit"]


# ---------------------------------------------------------------- 画像本身


class TestProfileTable:
    def test_every_duration_name_has_a_profile(self) -> None:
        from app.activity.profiles import missing_profiles

        assert missing_profiles() == frozenset()

    def test_profiles_expose_the_taskbook_fields(self) -> None:
        """§十八：min/typical/max + flexibility 必须支持（其余字段复用既有模型）。"""
        for name, profile in ACTIVITY_PROFILES.items():
            assert profile.min_duration <= profile.typical_duration <= profile.max_duration, name
            assert 0.0 <= profile.flexibility <= 1.0, name
            assert -1.0 <= profile.energy_cost <= 1.0, name
            assert profile.social_preference in {"alone", "either", "together"}, name
            assert profile.anchor_compatibility, name

    def test_kinds_match_the_taskbook_examples(self) -> None:
        """§十九 的例子：sleep/meal 固定、音乐/看书 自由。"""
        assert profile_for("sleeping").kind.value == "fixed"
        assert profile_for("eating").kind.value == "fixed"
        assert profile_for("music").kind.value == "free"
        assert profile_for("reading").kind.value == "free"
        assert profile_for("working").kind.value == "flexible"
        assert profile_for("working").moveable is True
        assert profile_for("sleeping").moveable is False

    def test_unknown_activity_gets_a_deterministic_fallback_profile(self) -> None:
        profile = profile_for("something_unknown")
        assert profile.kind.value == "free"
        assert profile.activity == ""
        assert profile_for("something_unknown").to_payload() == profile.to_payload()
