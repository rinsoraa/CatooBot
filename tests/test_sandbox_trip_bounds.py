"""Phase C tests: 行程在长时间自主生活中的边界（§28/§34）。

契约:
* 6 小时无对话模拟：trip 数量小且有界，动作数量远低于 tick 数；
* 任何时刻最多一个 open trip（重复低库存事件不会堆积行程）；
* 采购不会失控循环（购买动作次数被 trip 数约束）；
* 行程结束后、货架健康时，她的下一步是消费/休闲，而不是再买一轮。
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

TICKS_6H = 36


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


async def action_rows(db):  # type: ignore[no-untyped-def]
    return await db.fetchall("SELECT definition_id FROM sandbox_actions ORDER BY started_at")


def purchase_action_ids(runtime) -> set[str]:  # type: ignore[no-untyped-def]
    return {
        action_id
        for action_id, definition in runtime.actions.definitions.items()
        if definition.purchase
    }


def trips(runtime):  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.shopping_trip]


async def start_multi_errand_run(runtime, clock) -> None:  # type: ignore[no-untyped-def]
    """The deterministic seed of every bounds test: two low slots → one trip."""
    pantry = runtime.inventories.get("fridge")
    pantry.items["可乐"] = 1
    pantry.items["蛋糕"] = 1
    runtime.goal_detector.sweep()
    assert len(trips(runtime)) == 1
    for _ in range(TICKS_6H):
        clock.advance(600)
        await runtime.tick(minutes=10)


class TestTripBounds:
    async def test_trip_count_is_small_and_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_multi_errand_run(runtime, clock)
            trip_count = len(trips(runtime))
            assert 1 <= trip_count <= 4, f"trip count not bounded: {trip_count}"
            assert runtime.events.dropped == 0
            # at least the seeded trip really ran to completion, closing with
            # the explicit walk home (later leisure may move her again)
            assert any(trip.status.value == "completed" for trip in trips(runtime))
            assert "return_home" in {row["definition_id"] for row in await action_rows(db)}
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_action_count_is_far_below_the_tick_count(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_multi_errand_run(runtime, clock)
            action_count = len(await action_rows(db))
            assert action_count < TICKS_6H, "activity thrashed across ticks"
            # one outing replaces N single purchases: a whole 6h stays sparse
            assert action_count <= TICKS_6H // 2, f"too many actions: {action_count}"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_no_runaway_restock_loop(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_multi_errand_run(runtime, clock)
            rows = await action_rows(db)
            buy_ids = purchase_action_ids(runtime)
            purchases = [row for row in rows if row["definition_id"] in buy_ids]
            trip_count = len(trips(runtime))
            # every purchase belongs to a trip stop; there is no per-slot loop
            assert len(purchases) <= 3 * trip_count + 1, "restock loop ran away"
            for definition in runtime.actions.definitions.values():
                if definition.purchase:
                    stock = runtime.inventories.get("fridge").count(
                        str((definition.restock or {}).get("slot", ""))
                    )
                    floor = int((definition.restock or {}).get("min", 0) or 0)
                    if stock > floor:
                        ok, _reason = runtime.engine.restock_gate(definition)  # noqa: SLF001
                        assert ok is False
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_stock_replays_never_stack_open_trips(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            pantry.items["可乐"] = 1
            pantry.items["蛋糕"] = 1
            runtime.goal_detector.sweep()

            for _ in range(TICKS_6H):
                clock.advance(600)
                await runtime.tick(minutes=10)
                runtime.goal_detector.sweep()  # startup-style rescan every tick
                acquired = runtime.events.last(ET.ITEM_ACQUIRED)
                if acquired is not None:
                    runtime.goal_detector.observe(acquired)  # replay low-stock facts
                open_now = [trip for trip in trips(runtime) if not trip.status.terminal]
                assert len(open_now) <= 1, "two open trips at once (§14)"
            assert len(trips(runtime)) <= 4
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_after_the_trip_she_does_not_rebuy_while_stocked(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_multi_errand_run(runtime, clock)

            # shelves are healthy again → every procurement gate must be shut
            pantry = runtime.inventories.get("fridge")
            pantry.items["可乐"] = 10
            pantry.items["布丁"] = 5
            pantry.items["蛋糕"] = 5
            for definition in runtime.actions.definitions.values():
                if definition.purchase:
                    ok, reason = runtime.engine.restock_gate(definition)  # noqa: SLF001
                    assert ok is False and reason == "restock_not_needed"

            before = {event.event_id for event in runtime.events.of_type(ET.ACTION_STARTED)}
            runtime.needs.get("entertainment").level = 0.9  # type: ignore[union-attr]
            for _ in range(4):
                clock.advance(600)
                await runtime.tick(minutes=10)

            new_starts = [
                event
                for event in runtime.events.of_type(ET.ACTION_STARTED)
                if event.event_id not in before
            ]
            assert new_starts, "she should go back to consumption/leisure"
            purchases = purchase_action_ids(runtime)
            assert all(event.payload["action_id"] not in purchases for event in new_starts), [
                event.payload["action_id"] for event in new_starts
            ]
        finally:
            await runtime.shutdown()
            await db.close()
