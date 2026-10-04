"""§23/§25-Phase 5: 6h / 24h 无人聊天的长期模拟，六项指标必须有界。

契约（任务书 §23）：
* action_count 远低于 tick_count（tick 不能退化成高频决策）；
* goal_count / trip_count / experience_count / memory_count 全部有界；
* LLM calls 为 0（自主循环不经过任何模型）；
* 库存/需求检测不会让 shopping trip runaway：任何 tick 至多一个 open trip，
  购买次数被行程数约束，行程结束后货架健康时不再复购。

夹具默认库存不会同时在两个品类见底（>min），所以"成行"场景显式把
可乐/蛋糕降到 min 之下；默认场景则用来测量纯生命周期有界性。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

TICKS_6H = 36  # 36 × 10 min
TICKS_24H = 144  # 144 × 10 min
MINUTES_PER_TICK = 10


class NoLLM:
    """Tripwire: the autonomous loop must never reach a model (§23 LLM calls)."""

    def __init__(self) -> None:
        self.calls = 0

    def __getattr__(self, name: str):  # noqa: ANN204 - dynamic engine facade
        def _call(*args: object, **kwargs: object) -> None:
            self.calls += 1
            raise AssertionError(f"autonomous simulation touched the LLM: {name}")

        return _call


class Metrics:
    __slots__ = ("actions", "experiences", "goals", "memories", "trips")

    def __init__(self, **kw: int) -> None:
        for key in self.__slots__:
            setattr(self, key, kw[key])


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=BibleCompiler(FIXTURE_BIBLE).compile(),
        clock=clock,
    )
    await runtime.start()
    return runtime


async def run_ticks(runtime, clock, ticks: int) -> list[int]:
    """Advance `ticks` × 10 min; returns the per-tick open-trip counts."""
    open_trips: list[int] = []
    for _ in range(ticks):
        clock.advance(MINUTES_PER_TICK * 60)
        await runtime.tick(minutes=float(MINUTES_PER_TICK))
        await runtime.flush_experiences()
        open_trips.append(
            sum(
                1
                for goal in runtime.goals.all()
                if goal.kind is GoalKind.shopping_trip and not goal.status.terminal
            )
        )
    return open_trips


async def collect(runtime, ticks: int) -> Metrics:
    rows = await runtime.store.recent_finished_actions(limit=10000)
    goals = runtime.goals.all()
    return Metrics(
        actions=len(rows),
        goals=len(goals),
        trips=sum(1 for goal in goals if goal.kind is GoalKind.shopping_trip),
        experiences=len(runtime.experiences.emitted()),
        memories=await runtime.memory.count(),
    )


async def purchase_count(runtime) -> int:
    buy_ids = {
        action_id
        for action_id, definition in runtime.actions.definitions.items()
        if definition.purchase
    }
    rows = await runtime.store.recent_finished_actions(limit=10000)
    return sum(1 for row in rows if row[0] in buy_ids)


class TestLongRunSimulation:
    async def test_6h_metrics_stay_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        spy = NoLLM()
        runtime.ai_engine = spy
        try:
            open_trips = await run_ticks(runtime, clock, TICKS_6H)
            metrics = await collect(runtime, TICKS_6H)

            assert metrics.actions < TICKS_6H // 2, "actions thrashing across ticks"
            assert metrics.goals <= 12, f"goals not bounded: {metrics.goals}"
            assert metrics.trips <= 4, f"trips not bounded: {metrics.trips}"
            assert metrics.experiences <= 2 * TICKS_6H, metrics.experiences
            assert metrics.memories <= metrics.experiences, "memory outgrew the pipeline"
            assert all(count <= 1 for count in open_trips), "two open trips at once"
            assert spy.calls == 0
            assert runtime.events.dropped == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_24h_metrics_stay_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        spy = NoLLM()
        runtime.ai_engine = spy
        try:
            open_trips = await run_ticks(runtime, clock, TICKS_24H)
            metrics = await collect(runtime, TICKS_24H)

            assert metrics.actions < TICKS_24H // 4, "actions thrashing across a day"
            assert metrics.goals <= 24, f"goals not bounded: {metrics.goals}"
            assert metrics.trips <= 8, f"trips not bounded: {metrics.trips}"
            assert metrics.experiences <= TICKS_24H, metrics.experiences
            assert metrics.memories <= metrics.experiences
            assert all(count <= 1 for count in open_trips)
            assert spy.calls == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_24h_two_low_slots_makes_one_trip_and_never_runs_away(
        self,
        tmp_path,  # type: ignore[no-untyped-def]
    ) -> None:
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            pantry.items["可乐"] = 1  # min 2
            pantry.items["蛋糕"] = 1  # min 1
            runtime.goal_detector.sweep()
            open_trips = await run_ticks(runtime, clock, TICKS_24H)
            metrics = await collect(runtime, TICKS_24H)

            assert 1 <= metrics.trips <= 4, f"trip count not bounded: {metrics.trips}"
            purchases = await purchase_count(runtime)
            assert purchases <= 3 * metrics.trips + 1, "restock loop ran away"
            assert all(count <= 1 for count in open_trips)

            trips = [g for g in runtime.goals.all() if g.kind is GoalKind.shopping_trip]
            assert any(trip.status.value == "completed" for trip in trips)
            # the trip became exactly one user-visible experience
            trips_exp = [
                exp for exp in runtime.experiences.emitted() if exp.kind.value == "errand_trip"
            ]
            assert len(trips_exp) >= 1
            assert all("完成了" in exp.summary for exp in trips_exp)
            keys = [exp.episode_key for exp in trips_exp]
            assert len(keys) == len(set(keys)), "trip identity duplicated"
            assert all(key.startswith("trip:") for key in keys)
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stocked_shelves_end_the_restock_loop(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            pantry.items["可乐"] = 1
            pantry.items["蛋糕"] = 1
            runtime.goal_detector.sweep()
            await run_ticks(runtime, clock, TICKS_6H)

            # a full shelf keeps every procurement gate shut for the next 6h
            pantry.items["可乐"] = 10
            pantry.items["布丁"] = 5
            pantry.items["蛋糕"] = 5
            marks = {event.event_id for event in runtime.events.of_type(ET.ACTION_STARTED)}
            await run_ticks(runtime, clock, TICKS_6H)
            started = [
                event
                for event in runtime.events.of_type(ET.ACTION_STARTED)
                if event.event_id not in marks
            ]
            buy_ids = {
                action_id
                for action_id, definition in runtime.actions.definitions.items()
                if definition.purchase
            }
            assert started, "she should keep living, not freeze"
            assert all(event.payload["action_id"] not in buy_ids for event in started)
        finally:
            await runtime.shutdown()
            await db.close()
