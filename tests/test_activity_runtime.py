"""Phase 6A §九/§十二/§八：ActivityRuntime 的生命周期（矩阵 B–G、I、J、M、P）。

这里用的是**真的** ActivityRuntime + 真的 planner + 假时钟（``FakeClock``，绝不 sleep）。
"""

from __future__ import annotations

import asyncio

import pytest

from app.activity import (
    ACTIVITY_COMPLETED,
    ACTIVITY_EXPIRED,
    ACTIVITY_EXTENDED,
    ACTIVITY_INTERRUPTED,
    ACTIVITY_SCHEDULED,
    ACTIVITY_STARTED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    InMemoryActivityStore,
    TransitionReason,
)
from app.activity.adapters import SandboxActivityAdapter, TaskActivityAdapter
from app.activity.store import ActivityConflict, InvalidActivityTransition


class RecordingStates:
    """最小的 CharacterState 替身（记录投影写了什么）。"""

    def __init__(self) -> None:
        self.data: dict[str, object] = {}
        self.reasons: list[str] = []

    async def update(self, **changes: object) -> dict[str, object]:
        reason = str(changes.pop("reason", ""))
        self.reasons.append(reason)
        self.data.update(changes)
        return dict(self.data)


class Rig:
    """一整套真的部件（store/planner/publisher/projection）+ 假时钟。"""

    def __init__(self, *, character_id: str = "罐头@deadbeef") -> None:
        self.clock = FakeClock(1_700_000_000.0)  # 2023-11-15 06:13 +08
        self.store = InMemoryActivityStore()
        self.states = RecordingStates()
        self.events: list[tuple[str, dict[str, object]]] = []
        self.runtime = ActivityRuntime(
            store=self.store,
            clock=self.clock,
            character_id=character_id,
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(sink=self._record),
            projection=ActivityProjection(self.states),
        )
        self.sandbox = SandboxActivityAdapter(self.runtime)
        self.tasks = TaskActivityAdapter(self.runtime)

    def _record(self, name: str, payload: dict[str, object]) -> None:
        self.events.append((name, dict(payload)))

    def names(self) -> list[str]:
        return [name for name, _payload in self.events]


@pytest.fixture()
def rig() -> Rig:
    return Rig()


# ---------------------------------------------------------------- A/B/C/D/E/F/G


class TestLifecycle:
    async def test_b_scheduled_to_active(self, rig: Rig) -> None:
        scheduled = await rig.runtime.start(
            activity_name="reading", scheduled=True, now=rig.clock.now()
        )
        assert scheduled is not None and scheduled.status is ActivityStatus.SCHEDULED
        active = await rig.runtime.advance()
        assert active is not None
        assert active.episode_id == scheduled.episode_id  # 同一个 Episode，不是新建
        assert active.status is ActivityStatus.ACTIVE
        assert active.started_at == rig.clock.now()
        assert active.planned_end_at == rig.clock.now() + active.typical_duration
        assert rig.names()[:2] == [ACTIVITY_SCHEDULED, ACTIVITY_STARTED]

    async def test_c_active_to_extended(self, rig: Rig) -> None:
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_minutes(60)
        extended = await rig.runtime.advance()
        assert extended is not None
        assert extended.status is ActivityStatus.EXTENDED
        assert extended.extension_count == 1
        assert extended.planned_end_at > episode.planned_end_at
        assert ACTIVITY_EXTENDED in rig.names()

    async def test_d_active_to_completed(self, rig: Rig) -> None:
        episode = await rig.runtime.start(activity_name="eating", now=rig.clock.now())
        assert episode is not None
        done = await rig.runtime.complete(reason=TransitionReason.MANUAL, now=rig.clock.now())
        assert done is not None and done.status is ActivityStatus.COMPLETED
        assert done.ended_at == rig.clock.now()
        assert ACTIVITY_COMPLETED in rig.names()

    async def test_e_active_to_interrupted(self, rig: Rig) -> None:
        await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        stopped = await rig.runtime.interrupt(
            reason=TransitionReason.USER_INTERACTION, now=rig.clock.now()
        )
        assert stopped is not None and stopped.status is ActivityStatus.INTERRUPTED
        assert stopped.transition_reason == TransitionReason.USER_INTERACTION.value
        assert ACTIVITY_INTERRUPTED in rig.names()

    async def test_f_scheduled_to_cancelled(self, rig: Rig) -> None:
        await rig.runtime.start(activity_name="reading", scheduled=True, now=rig.clock.now())
        cancelled = await rig.runtime.cancel(reason=TransitionReason.MANUAL, now=rig.clock.now())
        assert cancelled is not None and cancelled.status is ActivityStatus.CANCELLED
        assert cancelled.started_at == 0.0  # 从没开始过

    async def test_g_active_to_expired(self, rig: Rig) -> None:
        episode = await rig.runtime.start(activity_name="eating", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_hours(2)  # eating 的硬上限 60 分钟
        after = await rig.runtime.advance()
        assert after is not None
        # 超硬上限 → 旧的 EXPIRED，并且排了一个新的（§二十七 情况 C）
        assert after.episode_id != episode.episode_id
        recent = await rig.runtime.recent(5)
        expired = [item for item in recent if item.episode_id == episode.episode_id]
        assert expired and expired[0].status is ActivityStatus.EXPIRED
        assert ACTIVITY_EXPIRED in rig.names()

    async def test_hard_limit_refuses_extension(self, rig: Rig) -> None:
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_hours(10)  # reading 的硬上限 150 分钟
        result = await rig.runtime.extend(now=rig.clock.now())
        assert result is not None
        assert result.status is ActivityStatus.EXPIRED  # 到上限就不许再延长

    async def test_invalid_transition_is_rejected(self, rig: Rig) -> None:
        """H：终态 → ACTIVE 必须被拒绝（状态机是显式的）。"""
        await rig.runtime.start(activity_name="eating", now=rig.clock.now())
        done = await rig.runtime.complete(now=rig.clock.now())
        assert done is not None
        with pytest.raises(InvalidActivityTransition):
            await rig.runtime._terminate(  # noqa: SLF001 - 直接验证状态机裁决
                done,
                to=ActivityStatus.ACTIVE,
                reason=TransitionReason.MANUAL,
                now=rig.clock.now(),
                publish=False,
            )


# ---------------------------------------------------------------- I / J / 世界 tick


class TestUniquenessAndClock:
    async def test_i_only_one_live_episode(self, rig: Rig) -> None:
        first = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert first is not None
        with pytest.raises(ActivityConflict):
            await rig.runtime.start(activity_name="gaming", now=rig.clock.now())
        live = await rig.runtime.current()
        assert live is not None and live.episode_id == first.episode_id

    async def test_i_switch_to_replaces_instead_of_parallel(self, rig: Rig) -> None:
        first = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert first is not None
        second = await rig.runtime.switch_to(activity_name="gaming", now=rig.clock.now())
        assert second is not None and second.episode_id != first.episode_id
        assert second.parent_episode_id == first.episode_id  # 后续 Episode 记着前一个
        live = await rig.runtime.current()
        assert live is not None and live.episode_id == second.episode_id
        recent = await rig.runtime.recent(5)
        closed = [item for item in recent if item.episode_id == first.episode_id]
        assert closed and closed[0].status is ActivityStatus.COMPLETED

    async def test_i_switch_to_same_activity_is_a_noop(self, rig: Rig) -> None:
        """§十三：同名活动不许把 primary Episode 切碎（反抖动）。"""
        first = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert first is not None
        again = await rig.runtime.switch_to(activity_name="reading", now=rig.clock.now())
        assert again is not None and again.episode_id == first.episode_id
        assert len(await rig.runtime.recent(10)) == 1

    async def test_j_fake_clock_advances_without_sleep(self, rig: Rig) -> None:
        """J：时间推进不依赖 sleep（这整套测试都没有 sleep）。"""
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        before = rig.clock.now()
        rig.clock.advance_hours(1)
        assert rig.clock.now() == before + 3600
        assert (await rig.runtime.current()).episode_id == episode.episode_id  # 窗口内不动

    async def test_tick_does_not_reselect_within_the_window(self, rig: Rig) -> None:
        """§八：世界 tick **不**重新决定她在做什么。"""
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        for _ in range(20):
            rig.clock.advance_minutes(1)
            current = await rig.runtime.advance()
            assert current is not None and current.episode_id == episode.episode_id
        assert len(await rig.runtime.recent(50)) == 1  # 一次都没换

    async def test_empty_slot_gets_planned_once(self, rig: Rig) -> None:
        first = await rig.runtime.advance()
        second = await rig.runtime.advance()
        assert first is not None and second is not None
        assert first.episode_id == second.episode_id  # 不会每次 tick 都排新的
        assert first.activity_name in {"eating", "reading", "out"}


# ---------------------------------------------------------------- M：Task 绑定


class TestTaskBinding:
    async def test_m_task_start_creates_a_task_episode(self, rig: Rig) -> None:
        episode = await rig.tasks.on_task_event(
            "task.started", {"task_id": "task_abc", "session_id": "private:1"}
        )
        assert episode is not None
        assert episode.activity_type is ActivityType.TASK_EXECUTION
        assert episode.source is ActivitySource.TASK
        assert episode.related_task_id == "task_abc"
        assert episode.activity_name == TaskActivityAdapter.ACTIVITY_NAME
        assert episode.status is ActivityStatus.ACTIVE

    async def test_m_task_events_are_ignored_without_a_task_id(self, rig: Rig) -> None:
        assert await rig.tasks.on_task_event("task.started", {}) is None
        assert await rig.runtime.current() is None

    async def test_m_succeeded_completes_with_the_task_reason(self, rig: Rig) -> None:
        await rig.tasks.on_task_event("task.started", {"task_id": "task_abc"})
        done = await rig.tasks.on_task_event("task.succeeded", {"task_id": "task_abc"})
        assert done is not None
        assert done.status is ActivityStatus.COMPLETED
        assert done.transition_reason == TransitionReason.TASK_COMPLETED.value

    @pytest.mark.parametrize(
        ("event", "status", "reason"),
        [
            ("task.failed", ActivityStatus.INTERRUPTED, TransitionReason.TASK_FAILED),
            ("task.cancelled", ActivityStatus.CANCELLED, TransitionReason.MANUAL),
            ("task.expired", ActivityStatus.EXPIRED, TransitionReason.TIME_EXPIRED),
        ],
    )
    async def test_m_terminal_mapping_is_distinct(
        self, rig: Rig, event: str, status: ActivityStatus, reason: TransitionReason
    ) -> None:
        await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        done = await rig.tasks.on_task_event(event, {"task_id": "t1"})
        assert done is not None
        assert done.status is status and done.transition_reason == reason.value

    async def test_m_four_two_pause_interrupts_the_episode(self, rig: Rig) -> None:
        """§四十二：任务 PAUSED 时活动**不能**还 ACTIVE。"""
        await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        paused = await rig.tasks.on_task_event("task.paused", {"task_id": "t1"})
        assert paused is not None and paused.status is ActivityStatus.INTERRUPTED
        assert await rig.runtime.current() is None

    async def test_m_resume_opens_a_new_episode_linked_to_the_old(self, rig: Rig) -> None:
        await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        paused = await rig.tasks.on_task_event("task.paused", {"task_id": "t1"})
        assert paused is not None
        resumed = await rig.tasks.on_task_event("task.resumed", {"task_id": "t1"})
        assert resumed is not None
        assert resumed.episode_id != paused.episode_id
        assert resumed.parent_episode_id == paused.episode_id
        assert resumed.related_task_id == "t1"

    async def test_m_intermediate_task_events_do_not_touch_the_episode(self, rig: Rig) -> None:
        started = await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        assert started is not None
        for event in (
            "task.created",
            "task.plan_ready",
            "task.confirmation_required",
            "task.step_started",
            "task.step_succeeded",
            "task.replanning",
            "task.authorization_expired",
        ):
            assert await rig.tasks.on_task_event(event, {"task_id": "t1"}) is None
        current = await rig.runtime.current()
        assert current is not None and current.episode_id == started.episode_id

    async def test_m_terminal_without_episode_does_not_fabricate_one(self, rig: Rig) -> None:
        assert await rig.tasks.on_task_event("task.succeeded", {"task_id": "ghost"}) is None
        assert await rig.runtime.current() is None

    async def test_four_three_recovery_does_not_create_a_second_episode(self, rig: Rig) -> None:
        """§四十三：任务恢复（再发一次 started/resumed）不许再建第二个 Episode。"""
        first = await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        again = await rig.tasks.on_task_event("task.resumed", {"task_id": "t1"})
        assert first is not None and again is not None
        assert again.episode_id == first.episode_id
        assert len(await rig.runtime.recent(10)) == 1

    async def test_task_episode_then_return_to_her_own_life(self, rig: Rig) -> None:
        """任务结束后：下一个 tick 让她回到日常（planner 决定，不是任务层）。"""
        await rig.tasks.on_task_event("task.started", {"task_id": "t1"})
        await rig.tasks.on_task_event("task.succeeded", {"task_id": "t1"})
        assert await rig.runtime.current() is None  # 任务型 Episode 已收尾
        nxt = await rig.runtime.advance()
        assert nxt is not None
        assert nxt.source is ActivitySource.ROUTINE
        assert nxt.activity_type is ActivityType.VIRTUAL_LIFE


# ---------------------------------------------------------------- 沙盒适配器


class TestSandboxAdapter:
    async def test_virtual_life_creates_a_routine_episode(self, rig: Rig) -> None:
        episode = await rig.sandbox.observe_virtual_life("reading", location="客厅")
        assert episode is not None
        assert episode.source is ActivitySource.ROUTINE
        assert episode.activity_name == "reading"
        assert episode.location == "客厅"

    async def test_same_activity_is_not_reapplied(self, rig: Rig) -> None:
        first = await rig.sandbox.observe_virtual_life("reading")
        again = await rig.sandbox.observe_virtual_life("reading")
        assert first is not None and again is not None
        assert first.episode_id == again.episode_id
        assert len(await rig.runtime.recent(10)) == 1

    async def test_changed_activity_switches_the_primary(self, rig: Rig) -> None:
        first = await rig.sandbox.observe_virtual_life("reading")
        second = await rig.sandbox.observe_virtual_life("gaming")
        assert first is not None and second is not None
        assert second.episode_id != first.episode_id
        assert second.transition_reason == TransitionReason.WORLD_EVENT.value


# ---------------------------------------------------------------- 并发（§四十四）


class TestConcurrency:
    async def test_two_events_racing_only_one_wins(self, rig: Rig) -> None:
        """§四十四：任务完成 + 时钟到期同时到来 → 只有一次最终转移。"""
        episode = await rig.runtime.start(activity_name="reading", now=rig.clock.now())
        assert episode is not None
        rig.clock.advance_hours(1)
        results = await asyncio.gather(
            rig.runtime.complete(now=rig.clock.now()),
            rig.runtime.expire(now=rig.clock.now()),
            return_exceptions=True,
        )
        settled = [
            item for item in results if not isinstance(item, BaseException) and item is not None
        ]
        assert len(settled) == 1  # CAS：第二个拿不到 live 状态
        final = settled[0]
        assert final.status.terminal
        terminal_events = [
            name
            for name in rig.names()
            if name in {ACTIVITY_COMPLETED, ACTIVITY_EXPIRED, ACTIVITY_INTERRUPTED}
        ]
        assert len(terminal_events) == 1
