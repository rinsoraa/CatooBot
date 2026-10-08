"""Phase 6D.1 A：撞车护栏**统一化**（所有产生"下一个活动"的路径都过护栏）。

不变量：``candidate → ActivityBounceGuard → accepted / rejected``。
模型候选、Planner 候选、中性兜底、硬中断候选走的是**同一条缝** —— 于是
"刚被护栏拒掉的那一个"绝不可能成为最终结果；没有合法候选时只能 EXTEND / CONTINUE。

任务书给的场景（6D.1 A）：

* ``current = napping``、``previous = napping``（终端、冷却期内）、cooldown active；
* 模型提 ``napping`` → ``RULE_REJECTED``；
* 规则的回退候选（夜里中性活动**也是** ``napping``）→ **同样被拒**；
* 最终必须落到 ``idle`` / ``resting`` / ``reading`` 这类**合法**候选，而不是再次 ``napping``。
"""

from __future__ import annotations

from typing import Any

from app.activity import ActivityPlanner, TransitionReason
from app.activity.decision import (
    BOUNCE_RESOLUTION_POLICY,
    DecisionReason,
    DecisionTrigger,
)
from app.activity.planner import ActivityDecision as PlannerDecision
from tests.activity_model_fakes import ScriptedProvider, proposal_json, rig_with_advisor
from tests.activity_plan_fakes import PlanRig, clock_at

#: 上一个活动用长档（做完它要一会儿），当前这个用短档（好让它"到期"）
LONG = (600.0, 1800.0, 7200.0)
SHORT = (60.0, 120.0, 1800.0)
#: 夜里 02:00（新加坡时间）：`NEUTRAL_BY_PERIOD["night"] == "napping"`，正是要防的那条旁路
NIGHT = 2
#: 上午 10:00：中性活动是 `idle`
MORNING = 10


class SuggestingPlanner(ActivityPlanner):
    """总是建议同一个活动（就是"刚做过的那件事"）。"""

    def __init__(self, name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.suggested = str(name)

    def next_after(self, *args: Any, **kwargs: Any) -> Any:
        return PlannerDecision(
            action="next",
            reason=TransitionReason.TIME_EXPIRED,
            activity_name=self.suggested,
        )


def make_rig(hour: int, suggestion: str, provider: Any = None) -> Any:
    """一套真部件 + 只会建议 ``suggestion`` 的 Planner；禁止延长（逼它真的换活动）。"""
    if provider is not None:
        rig = rig_with_advisor(provider, hour=hour)
    else:
        rig = PlanRig(clock=clock_at(hour, 0))
    rig.runtime.engine.planner = SuggestingPlanner(suggestion)
    rig.runtime.engine.guard.max_extensions = 0
    return rig


async def seed_recent(rig: Any, name: str, *, duration: Any = LONG) -> None:
    """刚做完 ``name``（用长档），让它落进 10 分钟冷却里。"""
    episode = await rig.runtime.start(activity_name=name, duration=duration, now=rig.clock.now())
    assert episode is not None
    rig.clock.advance_minutes(31)
    await rig.runtime.complete(now=rig.clock.now())


async def stage(rig: Any, current_name: str) -> Any:
    """上一个 = ``current_name``（终端、3 分钟前），当前 = ``current_name``（已到期）。"""
    await seed_recent(rig, current_name)
    current = await rig.runtime.start(
        activity_name=current_name, duration=SHORT, now=rig.clock.now()
    )
    assert current is not None
    # 3 分钟 < 10 分钟冷却 → 上一个仍在冷却里；而当前这个已经到期
    rig.clock.advance_minutes(3)
    return current


def resolution_of(result: dict[str, Any]) -> dict[str, Any]:
    trace = dict(result.get("trace") or {})
    return dict(trace["guard_results"]["bounce_resolution"])


def rejected_names(result: dict[str, Any]) -> list[str]:
    return [item["activity"] for item in resolution_of(result)["checked"] if item["ok"] is False]


class TestGuardUnification:
    async def test_literal_scenario_from_the_taskbook(self) -> None:
        """任务书场景：current=napping / previous=napping / cooldown active → 落合法候选。"""
        rig = make_rig(NIGHT, "napping")
        await stage(rig, "napping")
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["decision"] == "TRANSITION"
        assert result["next_activity_hint"] != "napping", "被护栏拒掉的活动不许成为结果"
        assert result["next_activity_hint"] == "idle", "应落到兜底合法候选"
        resolution = resolution_of(result)
        assert resolution["policy"] == BOUNCE_RESOLUTION_POLICY
        assert resolution["chosen"] == "idle"
        # 凡是名叫 napping 的候选，一条都不许被接受
        assert all(
            item["ok"] is False for item in resolution["checked"] if item["activity"] == "napping"
        ), resolution["checked"]

    async def test_neutral_fallback_is_rejected_too(self) -> None:
        """首选与**中性兜底**都撞车 → 两条都被拒，落到兜底活动（这是 A 的核心）。"""
        rig = make_rig(NIGHT, "napping")
        await seed_recent(rig, "napping")  # 上一个 napping 还在冷却里
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["next_activity_hint"] == "idle"
        assert rejected_names(result) == ["napping", "napping"]
        rejected_sources = [
            item["source"] for item in resolution_of(result)["checked"] if item["ok"] is False
        ]
        assert rejected_sources == ["preferred", "neutral"]

    async def test_model_proposal_rejected_and_fallback_also_guarded(self) -> None:
        """模型提 napping → RULE_REJECTED；规则的首选与中性兜底同样被拒 → 落 idle。"""
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="napping")])
        rig = make_rig(NIGHT, "napping", provider)
        await seed_recent(rig, "napping")
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["decision"] == "TRANSITION"
        assert result["next_activity_hint"] == "idle"
        trace = dict(result["trace"] or {})
        # 模型这一半：提案被规则整条拒绝
        assert trace["model_attempted"] is True
        assert trace["model_rejected"] is True
        assert trace["model_reject_reason"] == "RULE_REJECTED"
        assert trace["fallback_used"] is True
        # 规则这一半：回退候选（首选 + 中性兜底）同样过护栏、同样被拒
        assert rejected_names(result) == ["napping", "napping"]
        assert resolution_of(result)["chosen"] == "idle"

    async def test_hard_interrupt_is_guarded_too(self) -> None:
        """硬中断可以突破最短时长，但**不能**突破撞车护栏。"""
        rig = make_rig(NIGHT, "napping")
        await seed_recent(rig, "napping")
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.USER_INTERACTION)
        assert result["next_activity_hint"] == "idle"
        resolution = resolution_of(result)
        assert resolution["policy"] == BOUNCE_RESOLUTION_POLICY
        assert resolution["chosen"] == "idle"

    async def test_normal_transition_is_unchanged_when_nothing_collides(self) -> None:
        """回归：没有撞车时行为与以前**逐字一致**（首选候选直接过，原因码不变）。"""
        rig = make_rig(NIGHT, "napping")
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["decision"] == "TRANSITION"
        assert result["next_activity_hint"] == "napping"  # Planner 的首选，没被挡
        assert result["reason"] == DecisionReason.TRANSITION_WINDOW.value
        resolution = resolution_of(result)
        assert resolution["source"] == "preferred"
        assert resolution["chosen"] == "napping"

    async def test_neutral_equal_to_current_keeps_continuing(self) -> None:
        """当前活动**就是**中性活动、而首选候选撞车 → 不换活动，也不重开第二个（老语义）。"""
        rig = make_rig(MORNING, "gaming")  # 上午的中性活动 = idle
        await seed_recent(rig, "gaming")
        current = await rig.runtime.start(activity_name="idle", duration=SHORT, now=rig.clock.now())
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        # 首选 gaming 撞车；中性 idle == 当前 → 没有"下一个活动" → 不能延长就只能继续当前
        assert result["decision"] == "CONTINUE"
        assert result["reason"] == DecisionReason.BOUNCE_GUARD.value
        resolution = resolution_of(result)
        assert resolution["same_as_current"] is True
        assert resolution["chosen"] == ""  # 不会开新 Episode
        assert resolution["accepted_candidate"] == "idle"  # 护栏放行的那一个就是她正在做的事
        assert rejected_names(result) == ["gaming"]

    async def test_window_extension_still_wins_before_touching_the_candidates(self) -> None:
        """6B 逐字一致：窗口到期且**能延长**时先延长（§十六），根本不会走到"换活动"那一步。

        于是"换到合法候选"这条口径只在**不能延长**时才生效 —— 与 6B §十八 的
        "能延长就延长，否则换一个中性活动"完全同序，只是那个"中性活动"现在也过护栏。
        """
        rig = make_rig(MORNING, "gaming")
        rig.runtime.engine.guard.max_extensions = 2  # 允许延长
        await seed_recent(rig, "gaming")
        current = await rig.runtime.start(activity_name="idle", duration=SHORT, now=rig.clock.now())
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["decision"] == "EXTEND"
        assert result["reason"] == DecisionReason.TRANSITION_WINDOW.value
        # 没走到"换活动"这一步 → 也就没有候选审计（这不是旁路：压根没产生 next activity）
        guards = dict(dict(result["trace"] or {})["guard_results"])
        assert "bounce_resolution" not in guards

    async def test_accepted_model_transition_goes_through_the_same_exit(self) -> None:
        """模型转移被采纳时，也留下同一套候选审计（source=preferred）——不是另一条缝。"""
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="music")])
        rig = make_rig(NIGHT, "napping", provider)
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        result = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert result["decision"] == "TRANSITION"
        assert result["next_activity_hint"] == "music"
        trace = dict(result["trace"] or {})
        assert trace["model_attempted"] is True
        assert trace["model_rejected"] is False
        resolution = resolution_of(result)
        assert resolution["policy"] == BOUNCE_RESOLUTION_POLICY
        assert resolution["source"] == "preferred"
        assert resolution["chosen"] == "music"

    async def test_every_path_leaves_the_same_audit(self) -> None:
        """两条路径（到期 / 硬中断）都留下同一套审计：policy + checked + source。"""
        rig = make_rig(NIGHT, "napping")
        await seed_recent(rig, "napping")
        current = await rig.runtime.start(
            activity_name="reading", duration=SHORT, now=rig.clock.now()
        )
        assert current is not None
        rig.clock.advance_minutes(3)
        for trigger in (DecisionTrigger.TIME_EXPIRED, DecisionTrigger.USER_INTERACTION):
            result = await rig.runtime.decide_now(trigger=trigger)
            resolution = resolution_of(result)
            assert resolution["policy"] == BOUNCE_RESOLUTION_POLICY
            assert resolution["checked"], "每条路径都必须留下「过了哪些候选」的审计"
            assert resolution["source"] in {"preferred", "neutral", "fallback", "planner"}
            assert resolution["chosen"] != "napping"
