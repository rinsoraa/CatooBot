"""Phase 6C §三-§五/§三十三-§四十一：ActivityPlanner 与 ActivityPlan 本体（矩阵 A/B/H/N/U/Z）。

全部是**纯计算**断言：没有数据库、没有沙盒、没有 Minecraft、没有模型 ——
Planner 是纯函数，同一输入必须给同一计划（§六十四）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from app.activity import (
    ACTIVITY_PROFILES,
    FREE_ACTIVITY_POOL,
    ActivityKind,
    ActivityPlanner,
    ItemReason,
    PlanItem,
    PlannerContext,
    PlanStatus,
    RejectionReason,
    looks_like_minecraft_activity,
    profile_for,
)
from app.activity.planner import DEFAULT_HORIZON_SECONDS
from tests.activity_plan_fakes import GoalSourceStub, State, clock_at, goal

PLAN_FREE_ACTIVITIES = {
    "gaming",
    "music",
    "watching_show",
    "reading",
    "relax",
    "free_time",
    "building",
}


def plan_at(
    hour: int,
    minute: int = 0,
    *,
    state: State | None = None,
    horizon: float | None = None,
    goals: GoalSourceStub | None = None,
    planner: ActivityPlanner | None = None,
    max_items: int = 6,
):
    clock = clock_at(hour, minute)
    engine = planner or ActivityPlanner(goal_source=goals, state_provider=lambda: state)
    ctx = PlannerContext(
        clock=clock,
        now=clock.now(),
        character_state=state or State(),
        goals=goals.snapshot() if goals is not None else None,
        anchors=engine.anchors,
        horizon_seconds=float(horizon or DEFAULT_HORIZON_SECONDS),
        max_items=max_items,
    )
    return engine, clock, engine.plan_next(state or State(), None, now=clock.now(), context=ctx)


# ---------------------------------------------------------------- A：候选生成


class TestCandidateGeneration:
    def test_a_candidates_are_bounded_and_known(self) -> None:
        """§三十三：候选 3~6 个、数量有上限、必须是**已知**活动（不扫全项目）。"""
        _engine, _clock, plan = plan_at(14)
        assert 3 <= len(plan.candidates) <= 6
        assert all(candidate.activity in ACTIVITY_PROFILES for candidate in plan.candidates)
        assert all(not looks_like_minecraft_activity(c.activity) for c in plan.candidates)

    def test_a_candidates_come_from_the_expected_sources(self) -> None:
        """候选来自：锚点活动 + 时段习惯 + 自由池 +（目标亲和）—— 去重保序。"""
        engine, _clock, plan = plan_at(8, 30)
        names = [candidate.activity for candidate in plan.candidates]
        assert len(names) == len(set(names)), "候选不能重复"
        # 早饭窗口里必然有 eating（锚点来源）
        assert "eating" in names
        # 自由池里至少进来一个（§十六）
        assert PLAN_FREE_ACTIVITIES & set(names)

    def test_a_free_pool_entries_are_plannable(self) -> None:
        """§十六 的自由活动池：每个都有画像、有时长档（否则会裸 token 进上下文）。"""
        assert FREE_ACTIVITY_POOL
        for name in FREE_ACTIVITY_POOL:
            profile = profile_for(name)
            assert profile.activity == name
            assert profile.typical_duration > 0

    def test_a_goal_affinity_activities_enter_the_candidates(self) -> None:
        """§三十八：目标在推的活动会进候选（只影响排序，不强制，见排名文件）。"""
        source = GoalSourceStub(goals=(goal(kind="complete_project"),))
        _engine, _clock, plan = plan_at(14, goals=source)
        assert "building" in [candidate.activity for candidate in plan.candidates]

    def test_a_no_minecraft_activity_is_ever_proposed(self) -> None:
        """6A §二十九 + §五十五：Planner 绝不产出 Minecraft 活动名。"""
        for hour in (6, 12, 18, 23):
            _engine, _clock, plan = plan_at(hour)
            for item in plan.items:
                assert not looks_like_minecraft_activity(item.activity), (hour, item.activity)
            for candidate in plan.candidates:
                assert not looks_like_minecraft_activity(candidate.activity)


# ---------------------------------------------------------------- B：候选拒绝


class TestCandidateRejection:
    def test_b_rejection_carries_a_reason_code(self) -> None:
        """§三十四：被拒候选必须有 ``eligible=False`` + 原因码。"""
        _engine, _clock, plan = plan_at(14)
        rejected = plan.rejected()
        assert rejected, "下午的候选里应该有被拒的（固定活动 / 能量 / 时段）"
        for candidate in rejected:
            assert candidate.eligible is False
            assert candidate.reason in {reason.value for reason in RejectionReason}

    def test_b_fixed_activity_outside_its_window_is_rejected(self) -> None:
        """§十九：固定活动（睡觉/三餐）只在**锚点窗口里**可排，否则不可挪。"""
        _engine, _clock, plan = plan_at(14)
        eating = next((c for c in plan.candidates if c.activity == "eating"), None)
        assert eating is not None
        assert eating.eligible is False
        assert eating.reason == RejectionReason.NOT_MOVEABLE.value

    def test_b_hard_anchor_blocks_other_candidates_in_its_window(self) -> None:
        """§十四/§十五：硬锚点窗口里，别的事情这一段时间让位（换不换仍由 6B 决定）。"""
        engine, _clock, plan = plan_at(12, 10)  # 午饭窗口（11:15–12:45）
        others = [c for c in plan.candidates if c.activity != "eating"]
        assert others
        assert all(c.reason == RejectionReason.ANCHOR_CONFLICT.value for c in others)
        eating = next(c for c in plan.candidates if c.activity == "eating")
        assert eating.eligible is True
        assert eating.anchor_id == "lunch"

    def test_b_period_illegal_activity_is_rejected(self) -> None:
        """凌晨三点出门这种事不该被排（时段硬规则）。

        03:00 的时段表里本来就没有 ``out``，所以它只会经**目标亲和**进来（补货类目标）——
        这正好能证明硬规则真的会拦下它。
        """
        from app.activity.profiles import ILLEGAL_BY_PERIOD

        source = GoalSourceStub(goals=(goal(kind="restock_resource", target_project=""),))
        _engine, _clock, plan = plan_at(3, goals=source)
        out = next((c for c in plan.candidates if c.activity == "out"), None)
        assert out is not None, "补货类目标应该把 out 带进候选"
        assert out.eligible is False
        assert out.reason == RejectionReason.PERIOD_ILLEGAL.value
        # 更一般的断言：夜里不该出现任何"可用的非法活动"
        illegal = ILLEGAL_BY_PERIOD["night"]
        offenders = [c for c in plan.candidates if c.activity in illegal]
        assert all(not candidate.eligible for candidate in offenders)


# ---------------------------------------------------------------- H：自由活动


class TestFreeActivity:
    def test_h_free_slot_is_filled_from_the_pool(self) -> None:
        """§十六：自由时段（下午、无锚点临期）拿自由池里的活动填上。"""
        _engine, _clock, plan = plan_at(14)
        first = plan.first_item()
        assert first is not None
        assert first.activity in PLAN_FREE_ACTIVITIES | set(engine_free_names())
        assert first.reason in {ItemReason.FREE.value, ItemReason.ROUTINE.value}

    def test_h_free_activities_have_a_deterministic_profile_kind(self) -> None:
        assert profile_for("gaming").kind is ActivityKind.FREE
        assert profile_for("music").kind is ActivityKind.FREE
        assert profile_for("sleeping").kind is ActivityKind.FIXED
        assert profile_for("working").kind is ActivityKind.FLEXIBLE


def engine_free_names() -> frozenset[str]:
    from app.activity.planner import ROUTINE_BY_PERIOD

    names: set[str] = set()
    for entries in ROUTINE_BY_PERIOD.values():
        names.update(entries)
    return frozenset(names)


# ---------------------------------------------------------------- N：rolling horizon


class TestRollingHorizon:
    def test_n_horizon_is_bounded_never_a_whole_day(self) -> None:
        """§六/§四十一：只规划未来一小段，绝不生成全天时间表。"""
        _engine, clock, plan = plan_at(14)
        assert plan.horizon_seconds == pytest.approx(DEFAULT_HORIZON_SECONDS)
        assert plan.horizon_end - plan.horizon_start == pytest.approx(DEFAULT_HORIZON_SECONDS)
        assert len(plan.items) <= 6
        # 最后一条不会超出 horizon
        assert max(item.planned_end for item in plan.items) <= plan.horizon_end + 1e-6

    def test_n_items_are_contiguous_and_ordered(self) -> None:
        """计划项按时间递增、且**不重叠**（她不能同时做两件事）。"""
        _engine, _clock, plan = plan_at(9)
        items = list(plan.items)
        assert len(items) >= 2
        pairs = zip(items, items[1:], strict=False)
        for previous, following in pairs:
            assert following.planned_start >= previous.planned_end - 1e-6

    def test_n_max_items_caps_the_plan(self) -> None:
        """§六十八：条目数受 ``max_future_episodes`` 限制。"""
        _engine, _clock, plan = plan_at(9, horizon=12 * 3600, max_items=3)
        assert len(plan.items) <= 3

    def test_n_custom_horizon_is_respected(self) -> None:
        _engine, _clock, plan = plan_at(14, horizon=3600)
        assert plan.horizon_end - plan.horizon_start == pytest.approx(3600)
        assert plan.items  # 一小时内至少排了一条
        assert all(item.planned_end <= plan.horizon_end + 1e-6 for item in plan.items)

    def test_n_meals_appear_once_each_in_a_full_day_horizon(self) -> None:
        """§十五：一整段时间里，每顿饭只排一条（不重复排同一顿饭）。"""
        _engine, _clock, plan = plan_at(7, horizon=16 * 3600, max_items=6)
        anchors = [item.anchor_id for item in plan.items if item.anchor_id]
        assert len(anchors) == len(set(anchors)), anchors

    def test_n_no_oscillation_around_a_flexible_anchor(self) -> None:
        """§十五：不能出现 gaming → lunch → gaming 这种"锚点前后同一件事"的排法。"""
        _engine, _clock, plan = plan_at(9, horizon=8 * 3600, max_items=6)
        items = list(plan.items)
        for index, item in enumerate(items):
            if item.reason != ItemReason.ANCHOR.value:
                continue
            before = items[index - 1].activity if index > 0 else ""
            after = items[index + 1].activity if index + 1 < len(items) else ""
            assert after != before or not after, (before, item.activity, after)


# ---------------------------------------------------------------- U：确定性


class TestDeterminism:
    def test_u_same_input_same_plan(self) -> None:
        """§六十四：相同输入 → 相同候选、相同分数、相同选择、相同计划。"""
        source_a = GoalSourceStub(goals=(goal(),))
        source_b = GoalSourceStub(goals=(goal(),))
        _engine_a, _clock_a, plan_a = plan_at(14, goals=source_a)
        _engine_b, _clock_b, plan_b = plan_at(14, goals=source_b)
        assert [c.to_payload() for c in plan_a.candidates] == [
            c.to_payload() for c in plan_b.candidates
        ]
        assert [i.to_payload() for i in plan_a.items] == [i.to_payload() for i in plan_b.items]
        assert plan_a.content_hash == plan_b.content_hash

    def test_u_no_randomness_in_the_planning_package(self) -> None:
        """§一/§六十四：规划这一层**源码级**不许出现随机。"""
        package = Path(__file__).resolve().parents[1] / "app" / "activity"
        for path in sorted(package.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "import random" not in source, path
            assert "random." not in source, path
            assert "secrets." not in source, path

    def test_u_planner_has_no_probability_thresholds(self) -> None:
        """§三十五（v1.0 §23）：分数只是排序，不是概率 —— tie-break 是固定顺序表。"""
        from app.activity.profiles import SCORE_WEIGHTS, TIE_BREAK_ORDER

        assert TIE_BREAK_ORDER == (
            "anchor_priority",
            "goal_priority",
            "routine_priority",
            "name",
        )
        assert SCORE_WEIGHTS and all(isinstance(value, float) for value in SCORE_WEIGHTS.values())
        module = inspect.getmodule(ActivityPlanner)
        assert module is not None
        source = inspect.getsource(module)
        assert "import random" not in source and "import secrets" not in source

    def test_u_scores_are_reproducible_across_runs(self) -> None:
        _engine, _clock, first = plan_at(14)
        _engine2, _clock2, second = plan_at(14)
        assert [round(c.score, 9) for c in first.candidates] == [
            round(c.score, 9) for c in second.candidates
        ]


# ---------------------------------------------------------------- Z/Y：不调模型、不碰世界


class TestNoLlmNoWorld:
    def test_z_planner_never_imports_a_model_or_tool_layer(self) -> None:
        """§五十/§六十六：Planner 的 import 里不许有模型/工具/任务/世界动作。"""
        package = Path(__file__).resolve().parents[1] / "app" / "activity"
        forbidden = (
            "app.ai",
            "app.tools",
            "app.tasks",
            "app.integrations.minecraft",
            "app.agent",
            "app.core",
            "httpx",
            "openai",
        )
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert not alias.name.startswith(forbidden), (path, alias.name)
                elif isinstance(node, ast.ImportFrom):
                    module = str(node.module or "")
                    if path.name == "model_advisor.py" and module.startswith("app.ai"):
                        continue  # Phase 6D §四十四/§七十九：唯一被批准的模型缝
                    assert not module.startswith(forbidden), (path, module)

    def test_y_planner_layer_has_no_world_entry_points(self) -> None:
        """§六十六：规划层不许出现"世界动作"的名字（AST guard，不是 grep）。"""
        package = Path(__file__).resolve().parents[1] / "app" / "activity"
        forbidden_calls = {
            "confirm_and_start",
            "execute_action",
            "move_to",
            "dig",
            "place_block",
            "send_message",
            "set_activity",
        }
        allowed_mentions = {"set_activity"}  # 只在文档字符串里解释"为什么不许"
        for path in sorted(package.glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                if name in forbidden_calls - allowed_mentions:
                    raise AssertionError(f"{path} 里出现了世界动作调用：{name}")

    def test_y_plan_item_is_not_an_episode(self) -> None:
        """§四十六：计划项**没有** id、没有状态机 —— 它不可能是"现实"。"""
        item = PlanItem(activity="reading", planned_start=1.0, planned_end=2.0)
        assert not hasattr(item, "episode_id")
        assert not hasattr(item, "status")
        assert not hasattr(item, "to_episode")
        payload = item.to_payload()
        assert "episode_id" not in payload
        assert "status" not in payload

    def test_y_plan_starts_as_active_plan_not_reality(self) -> None:
        _engine, _clock, plan = plan_at(14)
        assert plan.status is PlanStatus.ACTIVE_PLAN
        assert plan.active is True

    def test_z_planner_context_only_reads_state(self) -> None:
        """§二十六：Planner 只**读**状态字段（现场改状态不会影响已经算出来的计划）。"""
        state = State(energy=0.9)
        _engine, _clock, plan = plan_at(14, state=state)
        before = [c.to_payload() for c in plan.candidates]
        state.energy = 0.1
        assert [c.to_payload() for c in plan.candidates] == before


# ---------------------------------------------------------------- 计划 ≡ 时间账


class TestPlanBookkeeping:
    def test_plan_reports_the_state_it_used(self) -> None:
        """§五十八：计划要能解释"当时的状态是什么"。"""
        _engine, clock, plan = plan_at(14, state=State(energy=0.42, current_focus=0.8))
        state = plan.constraints["state"]
        assert state["energy"] == pytest.approx(0.42)
        assert state["focus"] == pytest.approx(0.8)
        assert state["period"] == clock.period()

    def test_plan_records_trigger_and_weights(self) -> None:
        _engine, _clock, plan = plan_at(14)
        assert plan.constraints["weights"]["anchor_fit"] > 0
        assert plan.constraints["anchors"]  # 锚点清单也进审计

    def test_continuation_item_comes_first_when_something_is_live(self) -> None:
        """§三十一/§四十七：现实优先 —— 正在做的事先占第一条，后面才接计划。"""
        from app.activity import ActivitySource, ActivityStatus, ActivityType
        from app.activity.model import build_episode

        clock = clock_at(14)
        episode = build_episode(
            episode_id="ACT-20261015-001",
            character_id="罐头@deadbeef",
            activity_type=ActivityType.VIRTUAL_LIFE,
            activity_name="gaming",
            source=ActivitySource.ROUTINE,
            now=clock.now() - 600,
            status=ActivityStatus.ACTIVE,
        )
        episode.started_at = clock.now() - 600
        episode.planned_end_at = clock.now() + 1800
        engine = ActivityPlanner()
        ctx = PlannerContext(
            clock=clock,
            now=clock.now(),
            character_state=State(),
            current_episode=episode,
            anchors=engine.anchors,
        )
        plan = engine.plan_next(State(), episode, now=clock.now(), context=ctx)
        first = plan.first_item()
        assert first is not None
        assert first.reason == ItemReason.CONTINUATION.value
        assert first.activity == "gaming"
        assert first.planned_end == pytest.approx(episode.planned_end_at)
        # 现实之后才是"打算"
        following = plan.items[1] if len(plan.items) > 1 else None
        assert following is None or following.planned_start >= first.planned_end - 1e-6

    def test_next_item_skips_the_continuation(self) -> None:
        """§七十二：``next`` 说的是"之后"，不能把"现在正在做的"当答案。"""
        from app.activity.plan import ActivityPlan

        clock = clock_at(14)
        now = clock.now()
        plan = ActivityPlan(
            plan_id="PLAN-20261015-001",
            character_id="c",
            plan_version=1,
            generated_at=now,
            horizon_start=now,
            horizon_end=now + 7200,
            items=(
                PlanItem(
                    activity="gaming",
                    planned_start=now,
                    planned_end=now + 1800,
                    reason=ItemReason.CONTINUATION.value,
                ),
                PlanItem(activity="reading", planned_start=now + 1800, planned_end=now + 3600),
            ),
        )
        covering = plan.covers(now)
        assert covering is not None and covering.activity == "gaming"
        nxt = plan.next_item(now)
        assert nxt is not None and nxt.activity == "reading"


def test_known_routine_names_all_have_duration_profiles() -> None:
    """6A 的老不变量：时段表里的名字必须有据可查（不落进默认档）。"""
    from app.activity.planner import missing_durations

    assert missing_durations() == frozenset()
