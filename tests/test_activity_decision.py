"""Phase 6B §四-§十六/§二三-§二五/§三十-§三一/§四九：决策引擎（矩阵 A/B/C/D/E/L/T/U/V/W + 性能）。

用**真的** ActivityRuntime + 真的决策引擎 + 假时钟（``FakeClock``，绝不 sleep）。
"""

from __future__ import annotations

import ast
import pathlib
import time
from typing import Any

import pytest

from app.activity import (
    ACTIVITY_COMPLETED,
    ACTIVITY_EXPIRED,
    ACTIVITY_EXTENDED,
    ActivityEventPublisher,
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivityStatus,
    FakeClock,
    InMemoryActivityStore,
    SqliteActivityStore,
)
from app.activity.decision import (
    ActivityDecision,
    ActivityDecisionEngine,
    ActivityDecisionKind,
    DecisionReason,
    DecisionTrigger,
    TransitionGuard,
    as_trigger,
    decision_reason_for_transition,
)
from app.config.settings import DatabaseConfig
from app.database.database import Database

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIVITY_PACKAGE = REPO_ROOT / "app" / "activity"

#: 可延长、时长明确的日常活动档：min 10 / typical 60 / max 150 分钟
READING = ("reading", (10 * 60.0, 60 * 60.0, 150 * 60.0))
#: max 足够大的档（让"延长预算"先触发，而不是硬上限）
LONG_READING = ("reading", (5 * 60.0, 20 * 60.0, 600 * 60.0))
#: max 很小的档（让"硬上限"先触发）
SHORT_READING = ("reading", (5 * 60.0, 20 * 60.0, 60 * 60.0))
FALLBACKS = {"idle", "free_time", "resting", "napping"}


class _States:
    async def update(self, **_changes: Any) -> dict[str, Any]:
        return {}


class Rig:
    def __init__(
        self, *, window: float = 300.0, max_extensions: int = 2, cooldown: float = 600.0
    ) -> None:
        self.clock = FakeClock(1_700_000_000.0)  # 2023-11-15 06:13 +08
        self.store = InMemoryActivityStore()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.runtime = ActivityRuntime(
            store=self.store,
            clock=self.clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            publisher=ActivityEventPublisher(sink=self._record),
            projection=ActivityProjection(_States()),
            transition_window_seconds=window,
            max_extensions_per_episode=max_extensions,
            bounce_cooldown_seconds=cooldown,
        )

    def _record(self, name: str, payload: dict[str, Any]) -> None:
        self.events.append((name, dict(payload)))

    def names(self) -> list[str]:
        return [name for name, _payload in self.events]

    async def start(self, profile: tuple[str, tuple[float, float, float]] = READING) -> Any:
        episode = await self.runtime.start(
            activity_name=profile[0], duration=profile[1], now=self.clock.now()
        )
        assert episode is not None
        return episode

    async def settled(self, episode_id: str) -> Any:
        for item in await self.runtime.recent(20):
            if item.episode_id == episode_id:
                return item
        raise AssertionError(f"找不到 Episode {episode_id}")


@pytest.fixture()
def rig() -> Rig:
    return Rig()


# ---------------------------------------------------------------- A/B/C


class TestDecisionTriplet:
    async def test_a_continue_before_the_window(self, rig: Rig) -> None:
        """A：还没到期 → CONTINUE，**不动 Episode**（§十/§十三）。"""
        episode = await rig.start()
        rig.clock.advance_minutes(10)
        result = await rig.runtime.advance()
        assert result is not None
        assert result.episode_id == episode.episode_id
        assert result.status is ActivityStatus.ACTIVE
        assert result.updated_at == episode.updated_at  # 连 updated_at 都没动
        view = rig.runtime.decision_view(result, now=rig.clock.now())
        assert view["last_decision"]["decision"] == "CONTINUE"
        assert view["last_decision"]["reason_code"] == DecisionReason.BEFORE_END.value

    async def test_b_extend_at_expiry(self, rig: Rig) -> None:
        """B：到期且还能延长 → EXTEND（planned_end 前移、extension_count +1）。"""
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        result = await rig.runtime.advance()
        assert result is not None
        assert result.episode_id == episode.episode_id
        assert result.status is ActivityStatus.EXTENDED
        assert result.extension_count == 1
        assert result.planned_end_at > episode.planned_end_at
        assert ACTIVITY_EXTENDED in rig.names()

    async def test_c_transition_after_the_extension_budget(self, rig: Rig) -> None:
        """C：延长次数用尽 → TRANSITION（COMPLETED 收尾 + 排下一个），不再无限续命（§十六）。"""
        episode = await rig.start(LONG_READING)
        extended = None
        for _ in range(2):  # 上限 2 次延长
            rig.clock.advance_minutes(21)
            extended = await rig.runtime.advance()
            assert extended is not None and extended.episode_id == episode.episode_id
            assert extended.status is ActivityStatus.EXTENDED
        assert extended is not None and extended.extension_count == 2
        rig.clock.advance_minutes(21)  # 第三次到期：预算用完 → 必须换
        third = await rig.runtime.advance()
        assert third is not None
        assert third.episode_id != episode.episode_id
        closed = await rig.settled(episode.episode_id)
        assert closed.status is ActivityStatus.COMPLETED
        assert closed.extension_count == 2  # 正好用在允许的次数上
        assert ACTIVITY_COMPLETED in rig.names()
        assert third.activity_name != episode.activity_name  # 真的换了（不是同名重开）


# ---------------------------------------------------------------- D/E


class TestDurationGuards:
    async def test_d_min_duration_guard_blocks_an_early_transition(self) -> None:
        """D：还没到 min_duration → 即使到期也不能换（§十四）。"""
        rig = Rig()
        episode = await rig.start()
        rig.clock.advance_minutes(5)  # min 是 10 分钟
        decision = await rig.runtime.decide_now(trigger=DecisionTrigger.TIME_EXPIRED)
        assert decision["decision"] == "CONTINUE"
        assert decision["reason"] == DecisionReason.MIN_DURATION_GUARD.value
        current = await rig.runtime.current()
        assert current is not None and current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE

    async def test_d_hard_interruption_bypasses_min_duration(self) -> None:
        """§十四：硬中断（任务开始 / 用户打断 / 恢复）可以突破最短时长。"""
        rig = Rig()
        await rig.start()
        rig.clock.advance_minutes(5)
        decision = await rig.runtime.decide_now(trigger=DecisionTrigger.TASK_STARTED)
        assert decision["decision"] == "TRANSITION"
        assert decision["reason"] == DecisionReason.TASK_STARTED.value

    async def test_e_max_duration_is_a_hard_transition(self) -> None:
        """E：到硬上限 → **必须** TRANSITION（EXPIRED 收尾），连延长预算都不许救它（§十五）。"""
        rig = Rig(max_extensions=5)  # 就算允许一直延长，也不能越过 max
        episode = await rig.start(SHORT_READING)
        rig.clock.advance_minutes(61)
        result = await rig.runtime.advance()
        assert result is not None
        assert result.episode_id != episode.episode_id
        assert result.activity_name != episode.activity_name
        closed = await rig.settled(episode.episode_id)
        assert closed.status is ActivityStatus.EXPIRED
        assert closed.transition_reason == "TIME_EXPIRED"
        assert ACTIVITY_EXPIRED in rig.names()

    async def test_extension_never_exceeds_max_end(self) -> None:
        guard = TransitionGuard(max_extensions=9)
        rig = Rig(max_extensions=9)
        episode = await rig.start()
        verdict = guard.extension(
            episode, now=rig.clock.now(), extension_seconds=episode.max_duration
        )
        assert verdict.ok
        assert float(verdict.detail["extension_seconds"]) <= (
            episode.max_duration - episode.typical_duration
        )


# ---------------------------------------------------------------- 决策对象 / 原因码


class TestDecisionShape:
    def test_only_three_kinds(self) -> None:
        """§四：只有 CONTINUE / EXTEND / TRANSITION，**没有** EXECUTE / ACT / DO_TOOL。"""
        assert {kind.value for kind in ActivityDecisionKind} == {
            "CONTINUE",
            "EXTEND",
            "TRANSITION",
        }

    def test_reason_codes_are_the_standard_set(self) -> None:
        assert {
            "BEFORE_END",
            "TRANSITION_WINDOW",
            "MAX_DURATION",
            "MIN_DURATION_GUARD",
            "BOUNCE_GUARD",
            "TASK_STARTED",
            "TASK_COMPLETED",
            "TASK_FAILED",
            "TASK_INTERRUPTED",
            "USER_INTERACTION",
            "WORLD_EVENT",
            "RECOVERY",
            "NO_VALID_TRANSITION",
        } == {reason.value for reason in DecisionReason}

    def test_no_random_trigger(self) -> None:
        """§十二：触发器里不许有 RANDOM_TICK 这种东西。"""
        assert "RANDOM_TICK" not in {trigger.value for trigger in DecisionTrigger}

    def test_payload_shape(self) -> None:
        decision = ActivityDecision(
            ActivityDecisionKind.EXTEND, DecisionReason.TRANSITION_WINDOW, extension_seconds=60.0
        )
        assert decision.extend and not decision.transition
        assert set(decision.to_payload()) == {
            "decision",
            "reason_code",
            "next_activity_hint",
            "extension_seconds",
            "confidence",
            "trace_id",
        }

    def test_reason_to_transition_mapping_is_shared(self) -> None:
        """决策原因 → Episode 转移原因：两套词表只有这一处换算。"""
        assert (
            decision_reason_for_transition(DecisionReason.TASK_COMPLETED).value == "TASK_COMPLETED"
        )
        assert decision_reason_for_transition(DecisionReason.MAX_DURATION).value == "TIME_EXPIRED"

    def test_as_trigger_never_guesses_random(self) -> None:
        assert as_trigger("TASK_STARTED") is DecisionTrigger.TASK_STARTED
        assert as_trigger("不认识的东西") is DecisionTrigger.MANUAL


# ---------------------------------------------------------------- U/V：兜底


class TestFallbacks:
    async def test_u_planner_failure_falls_back_and_never_nulls_the_activity(self) -> None:
        """U（§三十/§三十一）：Planner 失败 → 兜底活动，绝不 ``activity = null``。

        用"不许延长"的配置逼出 TRANSITION 分支 —— 只有那条路才会去问 Planner。
        """

        class BrokenPlanner(ActivityPlanner):
            def next_after(self, *args: Any, **kwargs: Any) -> Any:
                raise RuntimeError("planner exploded")

        rig = Rig(max_extensions=0)
        rig.runtime.engine.planner = BrokenPlanner()
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        result = await rig.runtime.advance()
        assert result is not None
        current = await rig.runtime.current()
        assert current is not None  # 绝不为空
        assert current.episode_id != episode.episode_id  # 真的换了
        assert current.activity_name in FALLBACKS
        assert current.typical_duration > 0  # 兜底活动也有完整生命周期

    async def test_v_unknown_is_only_a_short_recovery_state(self) -> None:
        """V（§三十/§一二七）：低语义活动只作"短暂恢复"用，且仍然是有生命周期的 Episode。"""
        from app.activity.decision import FALLBACK_ACTIVITIES

        assert "idle" in FALLBACK_ACTIVITIES
        rig = Rig(max_extensions=0)
        decision = await rig.runtime.decide_now(trigger=DecisionTrigger.RECOVERY)
        assert decision["decision"] == ""  # 没有 Episode 时什么都不决策
        episode = await rig.start()
        rig.clock.advance_minutes(61)
        result = await rig.runtime.advance()
        assert result is not None
        assert episode.episode_id != result.episode_id
        assert result.typical_duration > 0 and result.max_duration >= result.typical_duration
        assert result.status is ActivityStatus.ACTIVE
        assert result.activity_name  # 绝不为空


# ---------------------------------------------------------------- L/M/T：频率与幂等


class TestFrequencyAndIdempotency:
    def test_t_no_model_entry_points_anywhere_in_the_package(self) -> None:
        """T（§三四/§三七）：普通 tick 只做规则，**没有任何**模型入口（AST 级核对）。"""
        engine = ActivityDecisionEngine(clock=FakeClock(), planner=ActivityPlanner())
        assert engine.advisor is None  # 6B 永远是 None（§三 硬约束）

        def imported(path: pathlib.Path) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            found: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    found.add(node.module)
                elif isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names)
            return found

        def called(path: pathlib.Path) -> set[str]:
            tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
            return {
                node.func.attr
                for node in ast.walk(tree)
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            }

        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            modules = imported(path)
            assert not any(name.startswith("app.ai") for name in modules), path.name
            assert "AIEngine" not in path.read_text(encoding="utf-8"), path.name
            calls = called(path)
            for forbidden in ("complete", "completion", "chat", "generate", "respond"):
                assert forbidden not in calls, f"{path.name} 调用了模型入口 {forbidden}()"

    def test_no_randomness_in_the_whole_package(self) -> None:
        """§二一/§四五：活动/时长/决策**绝不**用 random。"""
        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            source = path.read_text(encoding="utf-8")
            assert "import random" not in source, path.name
            assert "random." not in source, path.name
            assert "uniform(" not in source, path.name

    async def test_m_ordinary_ticks_do_not_create_episodes_or_decisions(self, rig: Rig) -> None:
        """M（§十/§三七）：窗口之外连续 tick —— 不建 Episode、不刷完成/延长事件。"""
        episode = await rig.start()
        for _ in range(40):  # 40 次 tick ≈ 40 分钟，窗口是最后 5 分钟
            rig.clock.advance_minutes(1)
            current = await rig.runtime.advance()
            assert current is not None and current.episode_id == episode.episode_id
        assert len(await rig.runtime.recent(50)) == 1  # 一次都没换
        assert rig.names().count(ACTIVITY_COMPLETED) == 0
        assert rig.names().count(ACTIVITY_EXTENDED) == 0

    async def test_l_duplicate_decision_does_not_extend_twice(self, rig: Rig) -> None:
        """L（§四十）：同一时刻重复决策不会 EXTEND × 2（tick 重试也算）。"""
        episode = await rig.start(LONG_READING)
        rig.clock.advance_minutes(21)
        first = await rig.runtime.advance()
        assert first is not None and first.extension_count == 1
        second = await rig.runtime.tick(trigger=DecisionTrigger.TIME_EXPIRED, now=rig.clock.now())
        assert second is not None
        assert second.episode_id == episode.episode_id
        assert second.extension_count == 1  # 没有第二次延长
        assert second.planned_end_at == first.planned_end_at

    async def test_l_restart_keeps_the_extension_count(self, tmp_path: Any) -> None:
        """§四十：重启之后延长计数仍在（不许把预算刷回来 → EXTEND × 3）。"""
        url = f"sqlite:///{tmp_path / 'ext.db'}"
        database = Database(DatabaseConfig(url=url))
        await database.connect()
        clock = FakeClock()
        first = ActivityRuntime(
            store=SqliteActivityStore(database),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            transition_window_seconds=300.0,
            max_extensions_per_episode=2,
        )
        episode = await first.start(
            activity_name="reading", duration=LONG_READING[1], now=clock.now()
        )
        assert episode is not None
        clock.advance_minutes(21)
        extended = await first.advance()
        assert extended is not None and extended.extension_count == 1
        await database.close()

        reopened = Database(DatabaseConfig(url=url))
        await reopened.connect()
        second = ActivityRuntime(
            store=SqliteActivityStore(reopened),
            clock=clock,
            character_id="罐头@deadbeef",
            planner=ActivityPlanner(),
            transition_window_seconds=300.0,
            max_extensions_per_episode=2,
        )
        current = await second.current()
        assert current is not None and current.extension_count == 1  # 预算没被刷回来
        clock.advance_minutes(21)
        again = await second.advance()
        assert again is not None and again.extension_count == 2
        await reopened.close()


# ---------------------------------------------------------------- W：快进


class TestFastForward:
    async def test_w_one_hour_of_ticks_is_a_handful_of_transitions(self, rig: Rig) -> None:
        """W（§三五）：一小时 60 次 tick → **少量** Episode 变化，绝不是 60 个事件。"""
        await rig.runtime.advance()  # 从"她空着"开始
        for _ in range(60):
            rig.clock.advance_minutes(1)
            await rig.runtime.advance()
        recent = await rig.runtime.recent(50)
        assert len(recent) <= 6, f"一小时产生了 {len(recent)} 个 Episode"
        assert rig.names().count(ACTIVITY_COMPLETED) <= 5

    async def test_w_three_days_of_ticks_stays_sane(self, rig: Rig) -> None:
        """W（§三五）：三天（每 5 分钟一次 tick）→ 合理的 Episode 数量，不是"每分钟一个"。"""
        await rig.runtime.advance()
        for _ in range(3 * 24 * 12):  # 3 天 × 24 小时 × 12 次/小时
            rig.clock.advance_minutes(5)
            await rig.runtime.advance()
        recent = await rig.runtime.recent(300)
        assert 3 <= len(recent) < 3 * 24 * 12 / 2

    async def test_performance_tick_is_cheap(self, rig: Rig) -> None:
        """§四九：普通 tick 必须低成本（只读当前 Episode + 少量历史），不许扫全表。"""
        await rig.runtime.advance()
        rig.clock.advance_minutes(1)
        started = time.perf_counter()
        for _ in range(50):
            await rig.runtime.advance()
        elapsed = time.perf_counter() - started
        assert elapsed < 2.0, f"50 次 tick 用了 {elapsed:.3f}s"
