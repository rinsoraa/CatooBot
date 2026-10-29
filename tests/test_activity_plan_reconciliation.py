"""Phase 6C.1 §二十四：Episode 延长之后的计划对齐（矩阵 A–O）。

要守的边界（§二/§十一-§十三）：

* 只动**未来的计划**，当前 Episode 一个字都不改；
* **绝不**再决策一次（6B 已经做完那个决定）—— 否则会形成 EXTEND→replan→decision→EXTEND 的活锁；
* **绝不**提前开下一个活动；
* 冲突时**不硬推时间线**，而是作废 + 受控 replan（``trigger=EPISODE_EXTENDED``，**软**触发）。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.activity import (
    ACTIVITY_EXTENDED,
    ActivityEpisode,
    ActivityPlan,
    ActivityPlanner,
    ActivityStatus,
    ItemReason,
    PlanItem,
    PlanStatus,
    PlanTrigger,
    TransitionReason,
)
from app.activity.plan import HARD_PLAN_TRIGGERS
from tests.activity_plan_fakes import PlanRig, clock_at

RUNTIME_PATH = Path(__file__).resolve().parents[1] / "app" / "activity" / "runtime.py"
RUNTIME_SOURCE = RUNTIME_PATH.read_text(encoding="utf-8")


class CountingPlanner(ActivityPlanner):
    """数一数 Planner 到底被调了几次（§十八：连续 EXTEND 不得 Planner × N）。"""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.calls = 0

    def plan_next(self, *args: Any, **kwargs: Any) -> ActivityPlan:
        self.calls += 1
        return super().plan_next(*args, **kwargs)


async def install_plan(rig: PlanRig, items: tuple[PlanItem, ...], *, version: int = 1) -> Any:
    """把一份**手工搭的**计划放进库里并接管为生效计划（测"计划长什么样"的边界用）。

    计划号走**存储层分配**（和运行时同一条路），否则会与真实分配撞号 —— 而撞号现在会被
    仓储拒绝（§九：历史计划不允许被覆盖）。
    """
    now = rig.clock.now()
    plan_id = await rig.runtime.plan_store.next_plan_id("20261015")  # type: ignore[union-attr]
    plan = ActivityPlan(
        plan_id=plan_id,
        character_id=rig.runtime.character_id,  # type: ignore[union-attr]
        plan_version=version,
        generated_at=now,
        horizon_start=now,
        horizon_end=now + 4 * 3600,
        items=items,
        source="MIXED",
        trigger=PlanTrigger.EPISODE_ENDED.value,
    )
    await rig.runtime.plan_store.create_plan(plan)  # type: ignore[union-attr]
    await rig.runtime.load_plan()  # type: ignore[union-attr]
    return plan


async def live_episode(rig: PlanRig, **kwargs: Any) -> ActivityEpisode:
    """开一个她正在做的事（默认 1 小时、可延长到 2 小时）。"""
    episode = await rig.runtime.start(  # type: ignore[union-attr]
        activity_name=kwargs.pop("activity_name", "reading"),
        duration=kwargs.pop("duration", (600.0, 3600.0, 7200.0)),
        now=rig.clock.now(),
        **kwargs,
    )
    assert episode is not None
    return episode


def continuation(episode: ActivityEpisode, *, end: float | None = None) -> PlanItem:
    return PlanItem(
        activity=episode.activity_name,
        planned_start=episode.started_at,
        planned_end=float(end if end is not None else episode.planned_end_at),
        reason=ItemReason.CONTINUATION.value,
        priority=1.0,
    )


# ---------------------------------------------------------------- A：EXTEND 更新 continuation


class TestContinuationReconciliation:
    async def test_a_extend_moves_the_continuation_boundary(self) -> None:
        """§三 Strategy A：计划第一条是"现实延续"、且后续不冲突 → **只**改它的边界。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        # 后续条目故意留出空档（15:30 才开始），所以 +10 分钟不会压住它
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at + 1800,
                    planned_end=episode.planned_end_at + 3600,
                    reason=ItemReason.FREE.value,
                ),
            ),
        )
        old_plan = rig.runtime.active_plan  # type: ignore[union-attr]
        assert old_plan is not None

        extended = await rig.runtime.extend(  # type: ignore[union-attr]
            episode.episode_id, extra_seconds=600, now=rig.clock.now()
        )
        assert extended is not None and extended.status is ActivityStatus.EXTENDED

        plan = rig.runtime.active_plan  # type: ignore[union-attr]
        assert plan is not None and plan.plan_id != old_plan.plan_id
        first = plan.first_item()
        assert first is not None
        assert first.reason == ItemReason.CONTINUATION.value
        assert first.planned_start == episode.started_at
        assert first.planned_end == extended.planned_end_at  # 对齐到**新的**现实结束时间
        # 后续条目的身份与时间**原样不动**（§三：不得改变后续活动的 identity）
        assert plan.items[1].activity == "music"
        assert plan.items[1].planned_start == old_plan.items[1].planned_start
        assert rig.runtime.last_plan_result["action"] == "reconciled"  # type: ignore[union-attr]

    async def test_a_no_reconciliation_when_already_aligned(self) -> None:
        """已经对齐（真实结束时间没变）→ 不产生新版本（§八：不许因为"读的时刻不同"就升版本）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(rig, (continuation(episode),), version=3)
        before = rig.runtime.active_plan  # type: ignore[union-attr]
        assert before is not None
        result = await rig.runtime.reconcile_plan(now=rig.clock.now())  # type: ignore[union-attr]
        assert result["action"] == "consistent"
        assert rig.runtime.active_plan.plan_version == before.plan_version  # type: ignore[union-attr]

    async def test_a_shape_mismatch_goes_to_replan(self) -> None:
        """第一条不是"现实延续"（形状对不上）→ 不硬改，走受控 replan（§四）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig, activity_name="reading")
        await install_plan(
            rig,
            (
                PlanItem(
                    activity="gaming",  # 与现实不符
                    planned_start=episode.started_at,
                    planned_end=episode.planned_end_at + 600,
                    reason=ItemReason.FREE.value,
                ),
            ),
        )
        result = await rig.runtime.reconcile_plan(now=rig.clock.now())  # type: ignore[union-attr]
        assert result["action"] in {"replanned", "dirty"}
        assert result["reason_code"] == "not_a_continuation"


# ---------------------------------------------------------------- B/C/D：冲突 → 受控 replan


class TestConflictReplan:
    async def test_b_conflict_is_detected(self) -> None:
        """§四：后续条目会被新的结束时间压住 → 只挪边界已经不可能了。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        plan_items = (
            continuation(episode),
            PlanItem(
                activity="music",
                planned_start=episode.planned_end_at,
                planned_end=episode.planned_end_at + 1800,
                reason=ItemReason.FREE.value,
            ),
        )
        await install_plan(rig, plan_items)
        assert (
            rig.runtime._continuation_conflict(  # noqa: SLF001 - 直接问单点判定
                rig.runtime.active_plan,  # type: ignore[union-attr]
                episode,
                new_end=episode.planned_end_at + 600,
            )
            is True
        )

    async def test_c_conflict_triggers_a_controlled_replan(self) -> None:
        """冲突 → 作废当前计划 + 受控 replan，结果是**新的合理时间线**（§十七 的样子）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                    reason=ItemReason.FREE.value,
                ),
            ),
        )
        # 先把冷却走完（不然软触发会先被拦下来标 dirty）
        rig.clock.advance_minutes(6)
        extended = await rig.runtime.extend(  # type: ignore[union-attr]
            episode.episode_id, extra_seconds=600, now=rig.clock.now()
        )
        assert extended is not None
        plan = rig.runtime.active_plan  # type: ignore[union-attr]
        assert plan is not None
        assert rig.runtime.last_plan_result["action"] == "replanned"  # type: ignore[union-attr]
        first = plan.first_item()
        assert first is not None and first.reason == ItemReason.CONTINUATION.value
        assert first.planned_end == extended.planned_end_at
        # 后续条目全部在**新的**结束时间之后（没有重叠）
        for item in plan.items[1:]:
            assert item.planned_start >= extended.planned_end_at - 1.0

    async def test_d_trigger_is_recorded_on_the_new_plan(self) -> None:
        """§十：新计划必须留下 `trigger=episode_extended`，才能回答"计划为什么突然变了"。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        plan = rig.runtime.active_plan  # type: ignore[union-attr]
        assert plan is not None
        assert plan.trigger == PlanTrigger.EPISODE_EXTENDED.value
        assert rig.runtime.last_plan_result["trigger"] == PlanTrigger.EPISODE_EXTENDED.value  # type: ignore[union-attr]
        # §五：它必须是**软**触发
        assert PlanTrigger.EPISODE_EXTENDED not in HARD_PLAN_TRIGGERS


# ---------------------------------------------------------------- E/F：冷却与连续 EXTEND


class TestCooldownAndRepeatedExtensions:
    async def test_e_cooldown_marks_dirty_instead_of_replanning(self) -> None:
        """§六：冷却没到 → 标 dirty，**不**立刻重算；等到合法时机才 replan。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        # 立刻延长（离计划刚落盘不到冷却时间）
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        assert rig.runtime.last_plan_result["action"] == "dirty"  # type: ignore[union-attr]
        assert rig.runtime.plan_view()["dirty"] is True  # type: ignore[union-attr]
        # 冷却一到，普通 tick 就会把它排掉（trigger 仍然如实记 episode_extended）
        rig.clock.advance_minutes(6)
        await rig.runtime.advance()  # type: ignore[union-attr]
        assert rig.runtime.plan_view()["dirty"] is False  # type: ignore[union-attr]
        assert rig.runtime.active_plan.trigger == PlanTrigger.EPISODE_EXTENDED.value  # type: ignore[union-attr]

    async def test_f_repeated_extensions_do_not_thrash_the_planner(self) -> None:
        """§十八：连续三次 EXTEND 不得 Planner × 3 —— 最多一次受控 replan。"""
        planner = CountingPlanner()
        rig = PlanRig(clock=clock_at(14, 0), planner=planner)
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        baseline = planner.calls
        # 第一次：冷却内 → dirty（不调 Planner）
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        assert planner.calls == baseline
        # 第二次：冷却已过 → 一次受控 replan
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        assert planner.calls == baseline + 1
        # 第三次：又落回冷却里 → 只标 dirty
        rig.clock.advance_minutes(1)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        assert planner.calls == baseline + 1
        assert rig.runtime.plan_view()["dirty"] is True  # type: ignore[union-attr]


# ---------------------------------------------------------------- G/H：版本与历史


class TestVersionAndHistory:
    async def test_g_version_bumps_and_uses_the_content_hash(self) -> None:
        """§八：continuation 边界变了 = 内容变了 → 版本 +1，且仍然走 content_hash 判定。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at + 3600,
                    planned_end=episode.planned_end_at + 5400,
                ),
            ),
            version=4,
        )
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        plan = rig.runtime.active_plan  # type: ignore[union-attr]
        assert plan is not None
        assert plan.plan_version == 5
        assert plan.content_hash == plan.content_signature()
        assert plan.content_hash != ""

    async def test_h_old_plan_is_superseded_never_deleted(self) -> None:
        """§九：旧计划标 SUPERSEDED、记 superseded_by，**绝不删除**。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        old = await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at + 3600,
                    planned_end=episode.planned_end_at + 5400,
                ),
            ),
        )
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        stored = await rig.runtime.plan_store.get_plan(old.plan_id)  # type: ignore[union-attr]
        assert stored is not None
        assert stored.status is PlanStatus.SUPERSEDED
        assert stored.superseded_by == rig.runtime.active_plan.plan_id  # type: ignore[union-attr]


# ---------------------------------------------------------------- I：恢复


class TestRecoveryReconciles:
    async def test_i_restart_after_extend_repairs_the_stale_plan(self) -> None:
        """§二十：EXTEND 之后马上重启 → 仍然一致（现实优先，旧计划不被继续使用）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        # 造一份"过时"的计划：continuation 还写着旧的结束时间
        await install_plan(rig, (continuation(episode, end=episode.planned_end_at),), version=2)
        extended = await rig.runtime.extend(  # type: ignore[union-attr]
            episode.episode_id, extra_seconds=1800, now=rig.clock.now()
        )
        assert extended is not None
        assert extended.planned_end_at > episode.planned_end_at
        # 模拟重启：同一个 store / 时钟，新 runtime
        restarted = PlanRig(clock=rig.clock, store=rig.store)
        result = await restarted.runtime.recover()  # type: ignore[union-attr]
        assert result["action"] == "resumed"
        plan = restarted.runtime.active_plan  # type: ignore[union-attr]
        assert plan is not None
        first = plan.first_item()
        assert first is not None
        assert first.planned_end == extended.planned_end_at  # 与现实对齐了
        assert restarted.runtime.plan_view()["dirty"] is False  # type: ignore[union-attr]

    async def test_i_recovery_prefers_reality_over_the_plan(self) -> None:
        """§二十一：Episode > … > Future Plan —— 计划永远不能覆盖现实。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(rig, (continuation(episode, end=episode.planned_end_at + 9999),))
        restarted = PlanRig(clock=rig.clock, store=rig.store)
        await restarted.runtime.recover()  # type: ignore[union-attr]
        current = await restarted.runtime.current()  # type: ignore[union-attr]
        assert current is not None
        assert current.planned_end_at == episode.planned_end_at  # 现实没被计划改掉


# ---------------------------------------------------------------- J/K/L/M：不许碰的东西


class TestBoundaries:
    async def test_j_current_episode_is_never_modified(self) -> None:
        """§十二：对齐期间当前 Episode 完全不变（只允许 future plan 变）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        rig.clock.advance_minutes(6)
        before = await rig.runtime.current()  # type: ignore[union-attr]
        assert before is not None
        await rig.runtime.reconcile_plan(now=rig.clock.now())  # type: ignore[union-attr]
        after = await rig.runtime.current()  # type: ignore[union-attr]
        assert after is not None
        assert after.episode_id == before.episode_id
        assert after.started_at == before.started_at
        assert after.planned_end_at == before.planned_end_at
        assert after.status is before.status
        assert after.ended_at == before.ended_at == 0

    async def test_k_no_new_episode_is_created(self) -> None:
        """§十三：对齐**不会**提前开下一个活动。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        before = await rig.runtime.recent(50)  # type: ignore[union-attr]
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        after = await rig.runtime.recent(50)  # type: ignore[union-attr]
        assert len(after) == len(before)
        assert [item.episode_id for item in after] == [item.episode_id for item in before]
        live = [item for item in after if item.status.open]
        assert len(live) == 1  # 仍然只有一个 live Episode

    async def test_l_decision_engine_is_not_called_again(self) -> None:
        """§十一：绝不"再决策一次"（否则 EXTEND→replan→decision→EXTEND 会活锁）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at,
                    planned_end=episode.planned_end_at + 1800,
                ),
            ),
        )
        names_before = list(rig.names())
        calls: list[str] = []
        original = rig.runtime.engine.decide  # type: ignore[union-attr]

        async def counting(**kwargs: Any) -> Any:
            calls.append("decide")
            return await original(**kwargs)

        rig.runtime.engine.decide = counting  # type: ignore[union-attr]
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        assert calls == []  # 延长 + 对齐期间一次都没问决策引擎
        # 而且这段时间**只**发布了延长事件 —— 没有新的"活动开始/结束"（§十三：不提前开下一个）
        before = len(names_before)
        assert rig.names()[before:] == [ACTIVITY_EXTENDED]

    async def test_m_no_minecraft_world_action_in_the_reconciliation_path(self) -> None:
        """§十五/§二十六：扩长后的对齐只碰计划 —— 一个世界动作入口都没有。"""
        rig = PlanRig(clock=clock_at(14, 0))
        assert not hasattr(rig.runtime, "minecraft")
        tree = ast.parse(RUNTIME_SOURCE)
        forbidden = {
            "confirm_and_start",
            "execute_action",
            "move_to",
            "dig",
            "place_block",
            "send_message",
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            if isinstance(node.func, ast.Attribute):
                assert node.func.attr not in forbidden, node.func.attr
            elif isinstance(node.func, ast.Name):
                assert node.func.id not in forbidden, node.func.id
        for module in ("app.integrations.minecraft", "app.tasks.runtime", "app.tools"):
            assert f"import {module}" not in RUNTIME_SOURCE

    async def test_n_qq_current_and_future_stay_isolated(self) -> None:
        """§十六：对齐之后，"在干嘛"仍然读 Episode，"接下来准备干嘛"仍然读计划。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(
            rig,
            (
                continuation(episode),
                PlanItem(
                    activity="music",
                    planned_start=episode.planned_end_at + 3600,
                    planned_end=episode.planned_end_at + 5400,
                    reason=ItemReason.FREE.value,
                ),
            ),
        )
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=600, now=rig.clock.now())  # type: ignore[union-attr]
        current_block = await rig.runtime.context_block()  # type: ignore[union-attr]
        plan_block = await rig.runtime.plan_context_block()  # type: ignore[union-attr]
        assert "现在的活动" in current_block and "reading" in current_block
        # 计划块说的是**接下来**（措辞是人话标签：music → 听音乐），不是"她正在做的事"
        assert "听音乐" in plan_block
        assert current_block != plan_block
        view = rig.runtime.plan_view()  # type: ignore[union-attr]
        assert view["next"] is None or view["next"]["activity"] != "reading"


# ---------------------------------------------------------------- O：回归


class TestRegression:
    async def test_o_max_duration_guard_is_still_6b_s_own_business(self) -> None:
        """§十九：到硬上限时 6B 直接收尾 —— 6C.1 **不**自己处理 max guard。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig, duration=(600.0, 600.0, 1200.0))
        await install_plan(rig, (continuation(episode),), version=7)
        before = rig.runtime.active_plan  # type: ignore[union-attr]
        assert before is not None
        rig.clock.advance(3600)  # 远超 max_end
        result = await rig.runtime.extend(  # type: ignore[union-attr]
            episode.episode_id, extra_seconds=600, now=rig.clock.now()
        )
        assert result is not None
        assert result.status is ActivityStatus.EXPIRED  # 6B 的硬上限守卫照旧生效
        assert rig.runtime.active_plan.plan_id == before.plan_id  # type: ignore[union-attr]

    async def test_o_extension_audit_row_is_still_written(self) -> None:
        """6B 的延长审计（§十六）没被 6C.1 改坏。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(rig, (continuation(episode),))
        rig.clock.advance_minutes(6)
        await rig.runtime.extend(episode.episode_id, extra_seconds=900, now=rig.clock.now())  # type: ignore[union-attr]
        rows = await rig.runtime.recent_transitions(episode.episode_id, limit=5)  # type: ignore[union-attr]
        extended = [row for row in rows if row["transition"] == ActivityStatus.EXTENDED.value]
        assert extended, rows
        assert "+900s" in str(extended[-1]["reason"])

    async def test_o_6b_decisions_and_state_machine_still_work(self) -> None:
        """6A/6B 的老语义没被改坏：最短时长内仍然 CONTINUE；非法转移仍然被拒。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig, duration=(1200.0, 3600.0, 7200.0))
        rig.clock.advance_minutes(3)
        current = await rig.runtime.advance()  # type: ignore[union-attr]
        assert current is not None and current.episode_id == episode.episode_id
        view = rig.runtime.decision_view(current)  # type: ignore[union-attr]
        assert view["last_decision"]["decision"] == "CONTINUE"
        assert view["last_decision"]["reason_code"] == "MIN_DURATION_GUARD"

    async def test_o_planner_stays_pure_and_deterministic(self) -> None:
        """6C 的底线：规划仍然是纯函数、同一输入同一输出。"""
        assert not hasattr(PlanRig().runtime.planner, "_executor")  # type: ignore[union-attr]
        planner = ActivityPlanner()
        clock = clock_at(14)
        from app.activity import PlannerContext
        from tests.activity_plan_fakes import State

        ctx = PlannerContext(clock=clock, now=clock.now(), character_state=State())
        first = planner.plan_next(State(), None, now=clock.now(), context=ctx)
        second = planner.plan_next(State(), None, now=clock.now(), context=ctx)
        assert first.content_hash == second.content_hash

    async def test_o_transition_reason_is_still_the_extend_one(self) -> None:
        """延长的转移原因仍然是 6B 的 ``TIME_EXPIRED``（6C.1 不改语义）。"""
        rig = PlanRig(clock=clock_at(14, 0))
        episode = await live_episode(rig)
        await install_plan(rig, (continuation(episode),))
        rig.clock.advance_minutes(6)
        extended = await rig.runtime.extend(  # type: ignore[union-attr]
            episode.episode_id,
            reason=TransitionReason.TIME_EXPIRED,
            extra_seconds=600,
            now=rig.clock.now(),
        )
        assert extended is not None
        assert extended.transition_reason == TransitionReason.TIME_EXPIRED.value
