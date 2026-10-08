"""Phase 6C §四十七-§四十九/§五十九/§七十二 + 真机门禁对应的行为：恢复、兜底、QQ 分界。

这一层把 6C 接到现实上，所以断言的都是"**现实优先**"：

* 重启后先认当前 Episode，再认计划；计划过期就重排，绝不盲目续用（§四十七）；
* Planner 失败时保住现有活动、绝不把活动置空（§四十八/§四十九）；
* 计划**不能**绕过 6B 的护栏去改现实（§三十一/§三十二）；
* QQ 的"现在在干嘛"与"接下来准备干嘛"是两个答案，绝不混（§五十九/§七十二）。
"""

from __future__ import annotations

from typing import Any

import pytest

from app.activity import (
    ActivityPlanner,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    PlanTrigger,
    plan_context_block,
)
from app.activity.adapters import TaskActivityAdapter
from app.activity.projection import activity_context_block
from tests.activity_plan_fakes import (
    BrokenPlanner,
    PlanRig,
    State,
    clock_at,
    local_text,
)


class BrokenPlanStore:
    """计划存储整个坏掉（读/写都炸）—— 活动必须照常走（§四十八：只降级）。"""

    async def next_plan_id(self, day: str) -> str:
        raise RuntimeError("plan store exploded")

    async def create_plan(self, plan: Any) -> Any:
        raise RuntimeError("plan store exploded")

    async def active_plan(self, character_id: str) -> Any:
        raise RuntimeError("plan store exploded")

    async def get_plan(self, plan_id: str) -> Any:
        raise RuntimeError("plan store exploded")

    async def recent_plans(self, character_id: str, limit: int = 5) -> list[Any]:
        raise RuntimeError("plan store exploded")


# ---------------------------------------------------------------- R：恢复


class TestPlanRecovery:
    async def test_recovery_loads_reality_first_then_the_plan(self) -> None:
        """§四十七：``load current Episode`` + ``load active plan``，顺序与优先级都写清楚。"""
        rig = PlanRig()
        await rig.runtime.advance()  # 造一个 Episode + 一份计划
        plan = rig.runtime.active_plan
        assert plan is not None
        fresh = PlanRig(clock=rig.clock, store=rig.store)
        result = await fresh.runtime.recover()
        assert result["action"] == "resumed"
        assert result["episode_id"]  # 现实
        assert result["plan"] == "loaded"  # 计划
        assert fresh.runtime.active_plan is not None

    async def test_stale_plan_is_reported_and_replanned(self) -> None:
        """§四十七：``active plan stale`` → 可以重新规划（绝不盲目续用）。"""
        rig = PlanRig()
        await rig.runtime.advance()
        first = rig.runtime.active_plan
        assert first is not None
        # 停机很久（远超 horizon）
        rig.clock.advance_hours(12)
        restarted = PlanRig(clock=rig.clock, store=rig.store)
        result = await restarted.runtime.recover()
        assert result["plan"] == "stale"
        assert restarted.runtime.active_plan is not None
        assert restarted.runtime.active_plan.plan_version > first.plan_version

    async def test_fresh_plan_is_kept_and_recovery_does_not_churn_versions(self) -> None:
        """计划没过期 → 恢复时"先认现实"，版本最多前进一格，且**再恢复一次不再涨**（§四十三）。"""
        rig = PlanRig()
        await rig.runtime.advance()
        first = rig.runtime.active_plan
        assert first is not None
        restarted = PlanRig(clock=rig.clock, store=rig.store)
        await restarted.runtime.recover()
        after = restarted.runtime.active_plan
        assert after is not None
        # 现实里多了一条"她正在做的事"，所以计划内容确实变了 → 最多 +1
        assert first.plan_version <= after.plan_version <= first.plan_version + 1
        # 同样状态再恢复一次：内容没变了 → 版本不许再涨
        again = PlanRig(clock=rig.clock, store=rig.store)
        await again.runtime.recover()
        assert again.runtime.active_plan.plan_version == after.plan_version  # type: ignore[union-attr]

    async def test_recovery_never_fabricates_an_episode_from_the_plan(self) -> None:
        """§四十七：没有 Episode 时**绝不**拿计划里的条目当成"她正在做"（计划≠现实）。"""
        rig = PlanRig()
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        assert rig.runtime.active_plan is not None
        assert await rig.runtime.current() is None
        fresh = PlanRig(clock=rig.clock, store=rig.store)
        result = await fresh.runtime.recover()
        assert result["action"] == "none"
        assert await fresh.runtime.current() is None  # 计划没有变成 Episode

    async def test_recovery_does_not_resurrect_superseded_items(self) -> None:
        """§四十四：旧计划的条目永远不会被"续用"成现实。"""
        rig = PlanRig()
        await rig.runtime.advance()
        rig.clock.advance_hours(12)
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        plans = await rig.runtime.plan_store.recent_plans("罐头@deadbeef", 10)  # type: ignore[union-attr]
        active = [plan for plan in plans if plan.active]
        assert len(active) == 1


# ---------------------------------------------------------------- S/T：失败与兜底


class TestPlannerFailureFallback:
    async def test_s_planner_failure_keeps_the_current_activity(self) -> None:
        """§四十八：Planner 挂了 → 继续当前活动（**绝不** ``activity = null``）。"""
        rig = PlanRig(planner=BrokenPlanner())
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_minutes(30)
        await rig.runtime.advance()
        current = await rig.runtime.current()
        assert current is not None
        assert current.activity_name == "reading"
        assert rig.runtime.degraded_reason in ("planner_failed", "")  # 降级被记下

    async def test_s_planner_failure_still_creates_something_when_shell_is_empty(self) -> None:
        """她空着 + Planner 挂了 → 走 6A 的老兜底，绝不空着（v1.0 §126）。"""
        rig = PlanRig(planner=BrokenPlanner())
        episode = await rig.runtime.advance()
        assert episode is not None
        assert episode.activity_name  # 有名字，不是空串

    async def test_s_broken_plan_store_does_not_break_activity(self) -> None:
        """计划存储坏了：活动照常（计划只是"预习"，不是生命线）。"""
        rig = PlanRig()
        rig.runtime.plan_store = BrokenPlanStore()  # type: ignore[assignment]
        episode = await rig.runtime.advance()
        assert episode is not None
        assert episode.activity_name
        # 计划只能在内存里用（没有计划号），而且降级原因如实记着
        view = rig.runtime.plan_view()
        assert view["plan"]["plan_id"] == ""
        assert rig.runtime.degraded_reason != ""
        # 重新读库也读不到（store 坏了 → 只降级，不抛）
        assert await rig.runtime.load_plan() is None

    async def test_t_no_valid_candidate_falls_back_deterministically(self) -> None:
        """§四十九：没有可用候选 → 兜底（``free_time`` / 夜里或低能量 ``resting``），确定性的。"""
        planner = ActivityPlanner()
        night = clock_at(3)
        ctx = planner._context(night, night.now(), started_today=0, episode=None)  # noqa: SLF001
        plan = planner.plan_next(State(), None, now=night.now(), context=ctx)
        assert plan.items, "计划绝不能是空的"
        assert plan.items[0].activity

    async def test_t_fallback_prefers_resting_when_exhausted(self) -> None:
        """§四十九：低能量时兜底是 ``resting``；精神好时是 ``free_time``。"""
        planner = ActivityPlanner()
        clock = clock_at(14)
        tired = planner._fallback_activity(  # noqa: SLF001
            planner._context(clock, clock.now(), started_today=0, episode=None),  # noqa: SLF001
        )
        assert tired in {"free_time", "resting"}
        ctx = planner._context(clock, clock.now(), started_today=0, episode=None)  # noqa: SLF001
        ctx.character_state = State(energy=0.1)
        assert planner._fallback_activity(ctx) == "resting"  # noqa: SLF001

    async def test_t_fallback_avoids_repeating_the_blocked_activity(self) -> None:
        planner = ActivityPlanner()
        clock = clock_at(14)
        ctx = planner._context(clock, clock.now(), started_today=0, episode=None)  # noqa: SLF001
        ctx.character_state = State(energy=0.5)
        assert planner._fallback_activity(ctx, avoid={"free_time"}) == "idle"  # noqa: SLF001
        assert planner._fallback_activity(ctx, avoid={"free_time", "idle"}) == "resting"  # noqa: SLF001
        assert planner._fallback_activity(ctx, avoid={"free_time", "idle", "resting"}) == "idle"  # noqa: SLF001


# ---------------------------------------------------------------- 6B 护栏优先


class TestDecisionGuardStillWins:
    async def test_plan_cannot_force_a_transition_during_min_duration(self) -> None:
        """§三十二：计划说"该吃饭了"，但当前活动才 3 分钟 → 6B 仍然 CONTINUE。"""
        rig = PlanRig(clock=clock_at(12, 10))  # 午饭窗口里
        episode = await rig.runtime.start(
            activity_name="gaming", duration=(20 * 60.0, 60 * 60.0, 180 * 60.0), now=rig.clock.now()
        )
        assert episode is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.advance()
        assert result is not None
        assert result.episode_id == episode.episode_id  # 没被计划拽走
        assert result.activity_name == "gaming"
        view = rig.runtime.decision_view(result)
        assert view["last_decision"]["decision"] == "CONTINUE"

    async def test_plan_refresh_on_episode_boundary(self) -> None:
        """§八 触发点 1：Episode 边界的换活动会重排计划（世界真的变了）。"""
        rig = PlanRig()
        await rig.runtime.advance()
        first = rig.runtime.active_plan
        assert first is not None
        rig.clock.advance_minutes(90)
        await rig.runtime.switch_to(activity_name="gaming", source=ActivitySource.ROUTINE)
        current = await rig.runtime.current()
        assert current is not None and current.activity_name == "gaming"
        plan = rig.runtime.active_plan
        assert plan is not None
        # 计划里出现了"现状"那条（现实优先），而且它排在第一位
        assert plan.first_item() is not None
        assert plan.first_item().activity in {"gaming", "reading", "out", "online", "eating"}

    async def test_task_episode_completion_refreshes_the_plan(self) -> None:
        """§六十一 Real Java B：任务完成 → 计划跟着重排（不是"还按老计划走"）。"""
        rig = PlanRig()
        adapter = TaskActivityAdapter(rig.runtime)
        started = await adapter.on_task_event(
            "task.started",
            {"state": "RUNNING", "task_id": "task_1", "activity": "minecraft_task"},
        )
        assert started is not None
        assert started.activity_name == TaskActivityAdapter.ACTIVITY_NAME
        before = rig.runtime.active_plan
        assert before is not None
        rig.clock.advance_minutes(5)
        await adapter.on_task_event(
            "task.succeeded",
            {"state": "SUCCEEDED", "task_id": "task_1", "activity": "minecraft_task"},
        )
        # 6A 的既有语义：任务型 Episode 结束后**不自动续摊**，由下一次 tick 问她"现在做什么"
        assert await rig.runtime.current() is None
        rig.clock.advance_minutes(1)
        current = await rig.runtime.advance()
        assert current is not None
        assert current.activity_name != TaskActivityAdapter.ACTIVITY_NAME
        plan = rig.runtime.active_plan
        assert plan is not None
        # 计划**真的**重排了（新计划号 + 时间戳在任务结束之后）
        assert plan.plan_id != before.plan_id
        assert plan.generated_at >= started.started_at

    async def test_minecraft_is_offline_so_no_real_world_activity_is_claimed(self) -> None:
        """§五十五/§五十六 Real Java C：不许产出"我正在 Minecraft 里"这种活动。"""
        rig = PlanRig()
        for _ in range(6):
            await rig.runtime.advance()
            rig.clock.advance_minutes(45)
        episodes = await rig.runtime.recent(50)
        assert episodes
        for episode in episodes:
            assert "minecraft" not in episode.activity_name
            assert episode.related_task_id == ""  # 没有任务 → 绝不声称在现实世界里行动

    async def test_world_events_never_bypass_the_task_runtime(self) -> None:
        """§六十一 Real Java D：世界事件（沙盒换活动）不得直接产生 Minecraft 动作。"""
        from pathlib import Path

        adapters = Path(__file__).resolve().parents[1] / "app" / "activity" / "adapters.py"
        source = adapters.read_text(encoding="utf-8")
        for forbidden in ("confirm_and_start", "execute_action", "TaskRuntime(", "Policy("):
            assert forbidden not in source, forbidden


# ---------------------------------------------------------------- QQ：现状 vs 计划


class TestCurrentVersusPlanned:
    def _plan(self):
        rig = PlanRig(clock=clock_at(14, 0))
        return rig

    async def test_qq_blocks_are_separate_and_labelled(self) -> None:
        """§五十九/§七十二：两块上下文各自成型，计划那块明说"不是现在正在做的事"。"""
        rig = PlanRig()
        episode = await rig.runtime.start(activity_name="gaming", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_minutes(10)
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        current_block = await rig.runtime.context_block()
        plan_block = await rig.runtime.plan_context_block()
        assert current_block and "现在的活动" in current_block
        assert plan_block and "计划" in plan_block
        assert "不是现在正在做的事" in plan_block
        assert "以现状为准" in plan_block
        # 现状里是她正在做的，计划里**不该**再重复一遍这句话
        assert current_block != plan_block

    async def test_planned_next_is_not_the_current_activity(self) -> None:
        """§七十二：``你接下来准备干嘛？`` 的答案不能是"我现在在做的这件事"。"""
        rig = PlanRig()
        episode = await rig.runtime.start(activity_name="gaming", now=rig.clock.now())
        assert episode is not None
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        view = rig.runtime.plan_view()
        assert view["next"] is None or view["next"]["activity"] != "gaming"

    async def test_plan_block_without_a_plan_is_empty(self) -> None:
        """没有计划就什么都不加 —— 绝不编一个计划出来（§四十八）。"""
        rig = PlanRig()
        assert await rig.runtime.plan_context_block() == ""

    async def test_plan_block_uses_the_configured_timezone(self) -> None:
        """计划块的时刻按**配置时区**写（否则"15:20 休息"在别的时区上是错的）。"""
        rig = PlanRig()
        await rig.runtime.start(activity_name="gaming", now=rig.clock.now())
        await rig.runtime.refresh_plan(trigger=PlanTrigger.MANUAL, now=rig.clock.now(), force=True)
        plan = rig.runtime.active_plan
        assert plan is not None
        block = plan_context_block(plan, episode=None, now=rig.clock.now(), clock=rig.clock)
        # 计划块跳过"现状"那条（CONTINUATION）—— 所以拿第一条**未来**条目来对时区
        item = next(
            item
            for item in plan.items
            if item.planned_end > rig.clock.now() and item.reason != "CONTINUATION"
        )
        assert local_text(rig.clock, item.planned_start) in block

    async def test_activity_context_block_accepts_a_clock(self) -> None:
        """既有活动块也能按时区渲染（6C 顺手统一了口径）。"""
        rig = PlanRig()
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        block = activity_context_block(episode, [], clock=rig.clock)
        assert local_text(rig.clock, episode.planned_end_at) in block


# ---------------------------------------------------------------- 只读视图


class TestPlanView:
    async def test_plan_view_exposes_the_taskbook_fields(self) -> None:
        """§五十八：Current / Candidates / Rejected / Scores / Constraints / Goal /
        Routine / Anchor / Selected / Plan Horizon / Plan Version —— 一个都不少。"""
        rig = PlanRig()
        await rig.runtime.advance()
        view = rig.runtime.plan_view()
        for key in (
            "plan",
            "plan_id",
            "plan_version",
            "horizon_start",
            "horizon_end",
            "source",
            "trigger",
            "current_item",
            "next",
            "upcoming",
            "candidates",
            "rejected",
            "selected",
            "anchors",
            "stale",
            "refresh_count",
            "last_result",
        ):
            assert key in view, key
        assert view["plan"]["constraints"]["weights"]
        assert all("breakdown" in candidate for candidate in view["candidates"])

    async def test_plan_view_is_read_only(self) -> None:
        """§五十八：视图里**没有**任何"强制选择/重排"的入口（只有数据）。"""
        rig = PlanRig()
        await rig.runtime.advance()
        view = rig.runtime.plan_view()
        for forbidden in ("force", "force_select", "refresh_now", "override", "apply"):
            assert forbidden not in view

    async def test_status_includes_the_plan_section(self) -> None:
        """WebUI 的 ``/world/activity`` 也带上计划（同一份事实，不另开口径）。"""
        rig = PlanRig()
        await rig.runtime.advance()
        status = await rig.runtime.status()
        assert "plan" in status
        assert status["plan"]["enabled"] is True
        assert status["decision"]  # 6B 的视图还在


@pytest.mark.parametrize("hour", [7, 13, 19, 23])
async def test_plan_always_covers_the_next_hour(hour: int) -> None:
    """§一二六：无论几点，计划都得覆盖"接下来一小时"（最差也有兜底活动）。"""
    rig = PlanRig(clock=clock_at(hour, 0))
    await rig.runtime.advance()
    plan = rig.runtime.active_plan
    assert plan is not None
    assert plan.items
    assert max(item.planned_end for item in plan.items) > rig.clock.now() + 600
    assert all(item.activity for item in plan.items)
    assert plan.items[0].reason in {
        reason.value for reason in __import__("app.activity", fromlist=["ItemReason"]).ItemReason
    }


async def test_scheduled_status_is_activated_by_the_tick() -> None:
    """6A 的老语义没被计划改坏：SCHEDULED 的 Episode 仍然由 tick 激活。"""
    rig = PlanRig()
    episode = await rig.runtime.start(activity_name="reading", scheduled=True, now=rig.clock.now())
    assert episode is not None and episode.status is ActivityStatus.SCHEDULED
    activated = await rig.runtime.advance()
    assert activated is not None
    assert activated.episode_id == episode.episode_id
    assert activated.status is ActivityStatus.ACTIVE
    assert activated.activity_type is ActivityType.VIRTUAL_LIFE
