"""Phase 6C §六-§九/§六十二-§六十五：Rolling Horizon 的节奏与成本。

钉死四件事：

1. **视野有界**（§六/§七）：配置范围 60~720 分钟，超范围直接 validation error；
2. **普通 tick 不重算**（§八/§九）：没有重大触发 + 冷却之内，一次都不重排；
3. **触发点真的会触发**（§八）：Episode 结束 / 计划快耗尽 / 状态或目标变了；
4. **快进不炸**（§六十二/§六十三）：3 天模拟里计划与 Episode 数量都有界，绝不"每分钟一个"。
"""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from app.activity import (
    ActivityPlanner,
    PlanTrigger,
    anchor_adherence,
    as_plan_trigger,
)
from app.activity.plan import HARD_PLAN_TRIGGERS
from app.config.settings import WorldActivityConfig
from tests.activity_plan_fakes import GoalSourceStub, PlanRig, State, clock_at, goal

# ---------------------------------------------------------------- §七 配置边界


class TestHorizonConfig:
    def test_range_is_one_to_twelve_hours(self) -> None:
        """§七：60~720 分钟；默认 240（4 小时）。"""
        config = WorldActivityConfig()
        assert config.planning_horizon_minutes == 240.0
        assert WorldActivityConfig(planning_horizon_minutes=60).planning_horizon_minutes == 60
        assert WorldActivityConfig(planning_horizon_minutes=720).planning_horizon_minutes == 720
        for bad in (59, 0, 721, 1440, -5):
            with pytest.raises(ValidationError):
                WorldActivityConfig(planning_horizon_minutes=bad)

    def test_refresh_and_item_knobs_have_sane_defaults(self) -> None:
        config = WorldActivityConfig()
        assert config.planner_refresh_min_minutes == 5.0
        assert config.max_future_episodes == 6
        with pytest.raises(ValidationError):
            WorldActivityConfig(max_future_episodes=7)
        with pytest.raises(ValidationError):
            WorldActivityConfig(planner_refresh_min_minutes=-1)

    def test_hard_triggers_are_the_ones_that_may_break_the_cooldown(self) -> None:
        """§九：重大触发可以突破冷却；"状态漂移/计划快耗尽"不行。"""
        assert PlanTrigger.EPISODE_ENDED in HARD_PLAN_TRIGGERS
        assert PlanTrigger.RECOVERY in HARD_PLAN_TRIGGERS
        assert PlanTrigger.MANUAL in HARD_PLAN_TRIGGERS
        assert PlanTrigger.ANCHOR_CHANGED in HARD_PLAN_TRIGGERS
        assert PlanTrigger.USER_INTERACTION in HARD_PLAN_TRIGGERS
        assert PlanTrigger.PLAN_EXHAUSTED not in HARD_PLAN_TRIGGERS
        assert PlanTrigger.STATE_CHANGED not in HARD_PLAN_TRIGGERS
        assert PlanTrigger.GOAL_CHANGED not in HARD_PLAN_TRIGGERS

    def test_as_plan_trigger_is_conservative(self) -> None:
        assert as_plan_trigger("manual") is PlanTrigger.MANUAL
        assert as_plan_trigger(PlanTrigger.RECOVERY) is PlanTrigger.RECOVERY
        # 不认识的名字**不能**被猜成"重大触发"
        assert as_plan_trigger("whatever") is PlanTrigger.MANUAL


# ---------------------------------------------------------------- §八/§九 节奏


class TestRefreshRhythm:
    async def test_first_refresh_plans_and_persists(self) -> None:
        rig = PlanRig()
        assert rig.runtime.active_plan is None
        result = await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now())
        assert result["refreshed"] is True
        plan = rig.runtime.active_plan
        assert plan is not None and plan.plan_id.startswith("PLAN-")
        assert plan.items
        # 落盘了：重新从 store 读也能拿到（不是只在内存里）
        stored = await rig.runtime.plan_store.active_plan("罐头@deadbeef")  # type: ignore[union-attr]
        assert stored is not None and stored.plan_id == plan.plan_id
        assert rig.runtime.plan_view()["enabled"] is True

    async def test_soft_trigger_inside_the_cooldown_does_not_replan(self) -> None:
        """§九：普通 tick + 没有重大触发 → **不得**重算整个 horizon。"""
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now())
        first = rig.runtime.active_plan
        rig.clock.advance_minutes(1)
        rig.state.energy = 0.3  # 状态变了，但还在冷却里
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.STATE_CHANGED, now=rig.clock.now()
        )
        assert result["refreshed"] is False
        assert result["reason"] == "cooldown"
        assert rig.runtime.active_plan is first

    async def test_hard_trigger_breaks_the_cooldown(self) -> None:
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now())
        rig.clock.advance_minutes(1)
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.EPISODE_ENDED, now=rig.clock.now()
        )
        assert result["trigger"] == PlanTrigger.EPISODE_ENDED.value
        assert result["refreshed"] is True  # 内容变了（现在是新的一刻）

    async def test_unchanged_content_does_not_bump_the_version(self) -> None:
        """§四十三：**内容完全一致**时不产生新版本、不写库。

        同一个时刻被两个触发点各叫一次（例如同一 tick 里 Episode 结束 + 状态漂移），
        第二次必须只说"没变"——否则版本号会被无意义地刷。
        """
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        first = rig.runtime.active_plan
        assert first is not None
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.EPISODE_ENDED, now=rig.clock.now()
        )
        assert result["refreshed"] is False
        assert result["reason"] == "unchanged"
        assert result["plan_version"] == first.plan_version
        assert rig.runtime.active_plan.plan_version == first.plan_version  # type: ignore[union-attr]

    async def test_version_only_grows(self) -> None:
        """§四十三：真的重排时版本单调递增（绝不回退、绝不跳号乱来）。"""
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        versions = [rig.runtime.active_plan.plan_version]  # type: ignore[union-attr]
        for _ in range(3):
            rig.clock.advance_minutes(30)
            await rig.runtime.refresh_plan(
                trigger=PlanTrigger.EPISODE_ENDED, now=rig.clock.now(), force=True
            )
            versions.append(rig.runtime.active_plan.plan_version)  # type: ignore[union-attr]
        assert versions == sorted(versions)
        assert len(set(versions)) == len(versions)  # 每次都真的变了（时间前移 30 分钟）

    async def test_plain_tick_does_not_replan_when_nothing_changed(self) -> None:
        """§八/§六十五：普通 tick 只做廉价检查，绝不规划。"""
        rig = PlanRig()
        episode = await rig.runtime.advance()
        assert episode is not None
        plan = rig.runtime.active_plan
        assert plan is not None
        for _ in range(20):
            rig.clock.advance_minutes(1)
            await rig.runtime.advance()
        assert rig.runtime.active_plan.plan_id == plan.plan_id
        assert rig.runtime._plan_refresh_count == 1  # noqa: SLF001 - 断言"一次都没重算"

    async def test_plan_exhaustion_triggers_a_refresh(self) -> None:
        """§八 触发点 2：计划快覆盖不到了 → 重排（软触发，过冷却才生效）。"""
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        first = rig.runtime.active_plan
        assert first is not None
        # 跳到"计划覆盖只剩 1 分钟"的那一刻（已经过了冷却）
        rig.clock.advance((first.horizon_end - 60) - rig.clock.now())
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.PLAN_EXHAUSTED, now=rig.clock.now()
        )
        assert result["refreshed"] is True
        assert rig.runtime.active_plan.plan_id != first.plan_id  # type: ignore[union-attr]

    async def test_goal_change_is_a_soft_trigger_with_cooldown(self) -> None:
        """§八 触发点 4：目标变了要重排，但走冷却（否则进度一动就重算）。"""
        source = GoalSourceStub(goals=(goal(kind="complete_project"),))
        planner = ActivityPlanner(goal_source=source, state_provider=lambda: rig.state)
        rig = PlanRig(planner=planner)
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        rig.clock.advance_minutes(1)
        source.goals = (goal(kind="complete_project", progress=0.9),)
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.GOAL_CHANGED, now=rig.clock.now()
        )
        assert result["refreshed"] is False and result["reason"] == "cooldown"
        rig.clock.advance_minutes(5)
        result = await rig.runtime.refresh_plan(
            trigger=PlanTrigger.GOAL_CHANGED, now=rig.clock.now()
        )
        assert result["refreshed"] is True

    async def test_tick_is_cheap(self) -> None:
        """§六十五：普通 tick 是 O(1) 量级 —— 50 次 tick 远快于 1 秒。"""
        rig = PlanRig()
        await rig.runtime.advance()
        started = time.perf_counter()
        for _ in range(50):
            rig.clock.advance(30)
            await rig.runtime.advance()
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, elapsed


# ---------------------------------------------------------------- §六十二/§六十三 快进


class TestFastForward:
    async def test_three_day_simulation_stays_bounded(self) -> None:
        """§六十三：3 天模拟 —— Episode 与计划数量都有界，锚点遵守度可测。"""
        rig = PlanRig(clock=clock_at(7, 0, day=15))
        steps = 3 * 24 * 4  # 每 15 分钟一次，共 3 天
        for _ in range(steps):
            await rig.runtime.advance()
            rig.clock.advance_minutes(15)
        episodes = await rig.runtime.recent(500)
        plans = await rig.runtime.plan_store.recent_plans("罐头@deadbeef", 500)  # type: ignore[union-attr]
        assert episodes, "3 天里她总得干点事"
        assert len(episodes) < 200, f"Episode 太多（{len(episodes)}）——像在每分钟换活动"
        assert len(plans) < steps / 2, f"计划太多（{len(plans)}）——像在每个 tick 重算"
        # 每一条 Episode 都有活动名（§一二六：Primary Activity 不能为空）
        assert all(episode.activity_name for episode in episodes)
        # 没有 Minecraft 活动名混进来
        assert all("minecraft" not in episode.activity_name for episode in episodes)

    async def test_three_day_simulation_respects_anchors(self) -> None:
        """§六十3：锚点遵守度可测（吃饭/睡觉大致发生了）。"""
        rig = PlanRig(clock=clock_at(6, 0, day=15))
        for _ in range(3 * 24 * 4):
            await rig.runtime.advance()
            rig.clock.advance_minutes(15)
        episodes = await rig.runtime.recent(500)
        spans = [
            (episode.activity_name, episode.started_at or episode.created_at, episode.ended_at)
            for episode in episodes
            if (episode.ended_at or 0) > (episode.started_at or 0)
        ]
        report = anchor_adherence(
            ActivityPlanner().anchors, spans, clock=rig.clock, now=rig.clock.now()
        )
        assert report["checked"] > 0, "3 天里应该有已经过去的锚点可核对"
        assert report["ratio"] >= 0.5, f"锚点遵守度太低：{report}"

    async def test_sleeping_happens_at_night(self) -> None:
        """夜里她应该睡觉（锚点真在起作用），而不是继续打游戏。"""
        rig = PlanRig(clock=clock_at(21, 0))
        for _ in range(3 * 60):  # 每 5 分钟一次，跑 3 小时（21:00 → 24:00）
            await rig.runtime.advance()
            rig.clock.advance_minutes(5)
        episodes = await rig.runtime.recent(100)
        assert any(episode.activity_name == "sleeping" for episode in episodes), [
            episode.activity_name for episode in episodes
        ]

    async def test_no_full_day_schedule_is_ever_built(self) -> None:
        """§六：绝不生成全天时间表 —— 计划的 horizon 永远只有配置那么长。"""
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        plan = rig.runtime.active_plan
        assert plan is not None
        assert plan.horizon_seconds <= 4 * 3600 + 1e-6
        assert len(plan.items) <= 6
        assert plan.horizon_end - plan.horizon_start < 24 * 3600


# ---------------------------------------------------------------- 状态签名


class TestStateSignature:
    def test_signature_ignores_micro_jitter(self) -> None:
        """能量抖动 0.001 不该被当成"状态重大变化"（否则等于每 tick 重算，§九）。"""
        from app.activity import state_signature

        assert state_signature(State(energy=0.800)) == state_signature(State(energy=0.801))
        assert state_signature(State(energy=0.80)) != state_signature(State(energy=0.70))

    def test_signature_covers_the_planner_inputs(self) -> None:
        from app.activity import state_signature

        base = state_signature(State())
        assert state_signature(State(current_focus=0.9)) != base
        assert state_signature(State(schedule_state="sleeping")) != base
        assert state_signature(State(social_state="chatting")) != base
        assert state_signature(State(mood="happy")) != base
        assert state_signature(None) == ""

    def test_signature_accepts_dicts_too(self) -> None:
        from app.activity import state_signature

        assert state_signature({"energy": 0.8, "mood": "neutral"}) == state_signature(
            {"energy": 0.8, "mood": "neutral"}
        )
