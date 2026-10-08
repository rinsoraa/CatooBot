"""Phase 6D §三十八/§三十九/§四十：重启守卫、装配失败隔离、provider 挂掉后的生活（矩阵 O/R）。

三条硬要求：

* **重启不得对同一个 transition cycle 再问一次**（§三十八）—— 靠**既有审计行**兜住（不新增表）；
* 模型层装配失败 → `advisor=None` 完全合法，规则照常（§四十）；
* provider 挂掉 → Activity Runtime **照常工作**（§三十九/§九十七）。
"""

from __future__ import annotations

from typing import Any

from app.activity import ActivityPlanner, ActivityRuntime, FakeClock
from app.activity.decision import DecisionTrigger
from app.activity.store import SqliteActivityStore
from app.config.settings import DatabaseConfig
from app.database.database import Database
from tests.activity_model_fakes import (
    RaisingProvider,
    ScriptedProvider,
    advisor_with,
    proposal_json,
    rig_with_advisor,
)

PROFILE = (600.0, 1800.0, 7200.0)


class TestRestartGuard:
    """§三十八：守卫的**持久侧**在引擎层验证（runtime 的 recover 会先延长 → cycle 就换了）。"""

    async def _engine_pair(self, tmp_path: Any) -> tuple[Any, Any, Any, Any]:
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'guard.db'}"))
        await database.connect()
        clock = FakeClock(1_700_000_000.0)
        provider_a = ScriptedProvider(scripted=[proposal_json("continue")])
        first = ActivityRuntime(
            store=SqliteActivityStore(database),
            clock=clock,
            character_id="c",
            planner=ActivityPlanner(),
            advisor=advisor_with(provider_a),
        )
        episode = await first.start(activity_name="gaming", duration=PROFILE, now=clock.now())
        assert episode is not None
        clock.advance_minutes(31)
        return database, first, episode, clock

    async def test_r_restart_does_not_ask_the_same_cycle_twice(self, tmp_path: Any) -> None:
        database, first, episode, clock = await self._engine_pair(tmp_path)
        try:
            provider_a = first.advisor.provider
            await first.engine.decide(
                episode=episode, now=clock.now(), trigger=DecisionTrigger.TIME_EXPIRED
            )
            assert provider_a.calls == 1
            # "重启"：新的引擎 + **空的**内存守卫，但同一个库、同一个 episode/cycle
            provider_b = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=30)])
            second = ActivityRuntime(
                store=SqliteActivityStore(database),
                clock=clock,
                character_id="c",
                planner=ActivityPlanner(),
                advisor=advisor_with(provider_b),
            )
            fresh = await second.store.get(episode.episode_id)
            assert fresh is not None
            await second.engine.decide(
                episode=fresh, now=clock.now(), trigger=DecisionTrigger.TIME_EXPIRED
            )
            assert provider_b.calls == 0, "重启后同一个 cycle 不许再问一次（§三十八）"
            receipt = dict(second.engine.last_receipt)
            assert receipt["attempted"] is False
            assert receipt["skipped_reason"] == "already_attempted"
        finally:
            await database.close()

    async def test_restart_with_a_new_cycle_may_ask_again(self, tmp_path: Any) -> None:
        """对照：现实变了（planned_end 变了）就是**新** cycle，重启后照问。"""
        database, first, episode, clock = await self._engine_pair(tmp_path)
        try:
            await first.engine.decide(
                episode=episode, now=clock.now(), trigger=DecisionTrigger.TIME_EXPIRED
            )
            provider_b = ScriptedProvider(scripted=[proposal_json("continue")])
            second = ActivityRuntime(
                store=SqliteActivityStore(database),
                clock=clock,
                character_id="c",
                planner=ActivityPlanner(),
                advisor=advisor_with(provider_b),
            )
            fresh = await second.store.get(episode.episode_id)
            assert fresh is not None
            fresh.planned_end_at = float(fresh.planned_end_at or 0.0) + 600.0  # 现实往前走了
            clock.advance(601)  # 时间也要跟到新的到期点（不然还没到软决策时刻）
            await second.engine.decide(
                episode=fresh, now=clock.now(), trigger=DecisionTrigger.TIME_EXPIRED
            )
            assert provider_b.calls == 1
        finally:
            await database.close()

    async def test_probe_failure_degrades_to_the_memory_guard(self, tmp_path: Any) -> None:
        """审计探针坏了 → 只靠内存守卫（绝不因为查不到就崩掉决策）。"""
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'probe.db'}"))
        await database.connect()
        try:
            clock = FakeClock(1_700_000_000.0)
            provider = ScriptedProvider(scripted=[proposal_json("continue")])
            runtime = ActivityRuntime(
                store=SqliteActivityStore(database),
                clock=clock,
                character_id="c",
                planner=ActivityPlanner(),
                advisor=advisor_with(provider),
            )

            async def broken(episode: Any, cycle_key: str) -> bool:
                raise RuntimeError("audit store down")

            runtime.engine.advisory_probe = broken
            episode = await runtime.start(activity_name="gaming", duration=PROFILE, now=clock.now())
            assert episode is not None
            clock.advance_minutes(31)
            await runtime.engine.decide(
                episode=episode, now=clock.now(), trigger=DecisionTrigger.TIME_EXPIRED
            )
            assert provider.calls == 1  # 探针坏了也不影响决策
        finally:
            await database.close()


class TestAssemblyIsolation:
    def test_o_enabled_but_unconfigured_runtime_still_works(self) -> None:
        """§四十/§四十二：顾问装配失败 = 纯规则，Activity Runtime 完全不受影响。"""
        from app.activity.model_advisor import build_advisor
        from app.config.settings import ModelAdvisorConfig

        assert build_advisor(ModelAdvisorConfig(enabled=True), None) is None
        assert build_advisor(ModelAdvisorConfig(enabled=True, model="m"), None) is None

    async def test_provider_down_does_not_stop_the_world(self) -> None:
        """§三十九：provider 挂了，世界照常转（决策继续、Episode 继续、没有任何异常冒出去）。"""
        provider = RaisingProvider(error=ConnectionError("provider down"))
        rig = rig_with_advisor(provider, hour=14)
        episode = await rig.runtime.start(
            activity_name="gaming", duration=PROFILE, now=rig.clock.now()
        )
        assert episode is not None
        for _ in range(6):
            rig.clock.advance_minutes(31)
            current = await rig.runtime.advance()
            assert current is not None
        assert provider.calls >= 1
        assert rig.runtime.advisor_view()["last_receipt"]["fallback_used"] is True
        assert rig.runtime.degraded_reason in ("", "planner_failed")

    async def test_advisor_failure_never_propagates_out_of_the_tick(self) -> None:
        """§九十七：模型故障**绝不**能让 Activity Runtime 不可用。"""

        class Exploding:
            async def complete_json(self, **kwargs: Any) -> str:
                raise ZeroDivisionError("boom")

        rig = rig_with_advisor(Exploding(), hour=14)
        await rig.runtime.start(activity_name="gaming", duration=PROFILE, now=rig.clock.now())
        rig.clock.advance_minutes(31)
        current = await rig.runtime.advance()  # 不抛异常
        assert current is not None
        assert current.activity_name == "gaming"

    async def test_hard_interrupt_path_never_touches_the_advisor(self) -> None:
        """§二十六：硬中断（用户交互/任务）在模型之前 —— 连一次调用都不该发生。"""
        provider = ScriptedProvider(scripted=[proposal_json("continue")])
        rig = rig_with_advisor(provider, hour=14)
        await rig.runtime.start(activity_name="gaming", duration=PROFILE, now=rig.clock.now())
        rig.clock.advance_minutes(5)
        decision = await rig.runtime.decide_now(trigger=DecisionTrigger.USER_INTERACTION)
        assert decision["decision"] == "TRANSITION"  # 硬中断直接换活动
        assert provider.calls == 0
