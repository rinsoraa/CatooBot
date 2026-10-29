"""Phase C tests: 行程跨重启继续（§10/§14）。

契约:
* 重启后同一个 trip_id 继续：不新建第二个 trip，已完成/未完成的 stop 保持；
* 剩余站点与回家路线按原顺序执行；中途正在跑的动作随快照恢复；
* 回到家后 goal 恰好完成一次（GOAL_COMPLETED 只发一次）。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalStatus
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db


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


def trips(runtime):  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.shopping_trip]


def open_trip(runtime):  # type: ignore[no-untyped-def]
    return next((goal for goal in trips(runtime) if not goal.status.terminal), None)


def stops_of(trip):  # type: ignore[no-untyped-def]
    return trip.metadata["stops"]


async def build_trip(runtime, clock) -> None:  # type: ignore[no-untyped-def]
    """Case D: 便利店的两个槽同时见底 → 一个 stop、两条采购。"""
    pantry = runtime.inventories.get("fridge")
    pantry.items["可乐"] = 1
    pantry.items["布丁"] = 1
    runtime.goal_detector.sweep()


async def run_one_stop(runtime, clock) -> str:  # type: ignore[no-untyped-def]
    assert await runtime.goals.advance() is True
    action_id = runtime.current_action.definition_id  # type: ignore[union-attr]
    runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001
    await runtime.flush_experiences()
    return action_id


async def persist_and_stop(runtime) -> None:  # type: ignore[no-untyped-def]
    await runtime.goals.flush()
    await runtime._snapshot()  # noqa: SLF001 - the restore point
    await runtime.shutdown()


async def run_trip(runtime, clock) -> list[str]:  # type: ignore[no-untyped-def]
    acted: list[str] = []
    for _ in range(30):
        if runtime.goals.open_trip() is None:
            break  # the trip closed — do not wander into other goals
        if not await runtime.goals.advance():
            break
        if runtime.current_action is None:
            break
        acted.append(runtime.current_action.definition_id)
        runtime.current_action.planned_end_at = clock.now
        clock.advance(1)
        await runtime._complete_action()  # noqa: SLF001
        await runtime.flush_experiences()
    return acted


class TestTripRestart:
    async def test_same_trip_continues_and_no_second_one_is_opened(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        trip_id = ""
        goal_id = ""
        try:
            await build_trip(first, clock)
            trip = open_trip(first)
            assert trip is not None
            trip_id = trip.metadata["trip_id"]
            goal_id = trip.goal_id
            assert await run_one_stop(first, clock) == "buy_cola"
            await persist_and_stop(first)
        finally:
            if first.phase.value != "stopped":
                await first.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            trips_now = trips(revived)
            assert len(trips_now) == 1, "restart must never open a second trip"
            assert trips_now[0].goal_id == goal_id
            assert revived.goals.open_trip() is not None
            assert trips_now[0].metadata["trip_id"] == trip_id
            # no GOAL_CREATED for a trip in the revived runtime
            created = [
                event
                for event in revived.events.of_type(ET.GOAL_CREATED)
                if event.payload.get("kind") == GoalKind.shopping_trip.value
            ]
            assert created == []

            stop = stops_of(trips_now[0])[0]
            assert stop["items"][0]["done"] is True
            assert stop["items"][1]["done"] is False

            acted = await run_trip(revived, clock)
            assert acted == ["buy_snacks", "return_home"]
            assert revived.character.location == "apartment"
            assert trips_now[0].status is GoalStatus.completed
        finally:
            await revived.shutdown()
            await db.close()

    async def test_remaining_stop_and_route_stay_intact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        try:
            await build_trip(first, clock)
            await run_one_stop(first, clock)
            await persist_and_stop(first)
        finally:
            if first.phase.value != "stopped":
                await first.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            trip = open_trip(revived)
            assert trip is not None
            stop = stops_of(trip)[0]
            assert stop["space"] == "convenience_store"
            pending = [item for item in stop["items"] if not item["done"]]
            assert [(item["slot"], item["action_id"]) for item in pending] == [
                ("布丁", "buy_snacks")
            ]
            # she is still at the shop; the remaining route is buy → home
            assert revived.character.location == "convenience_store"
            await run_trip(revived, clock)
            moved = [event.target_entity_id for event in revived.events.of_type(ET.ENTITY_MOVED)]
            assert moved == ["apartment"]
        finally:
            await revived.shutdown()
            await db.close()

    async def test_goal_completes_exactly_once_after_returning_home(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        try:
            await build_trip(first, clock)
            await run_one_stop(first, clock)
            await persist_and_stop(first)
        finally:
            if first.phase.value != "stopped":
                await first.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            trip = open_trip(revived)
            assert trip is not None
            await run_trip(revived, clock)
            completions = [
                event
                for event in revived.events.of_type(ET.GOAL_COMPLETED)
                if event.payload.get("goal_id") == trip.goal_id
            ]
            assert len(completions) == 1
            assert completions[0].payload["trip_id"] == trip.metadata["trip_id"]
            assert trip.progress == 1.0
            assert trip.status is GoalStatus.completed
            assert revived.goals.open_trip() is None  # never reopened
        finally:
            await revived.shutdown()
            await db.close()

    async def test_restart_mid_stop_resumes_the_pending_purchase(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        try:
            await build_trip(first, clock)
            assert await first.goals.advance() is True
            assert first.current_action is not None
            assert first.current_action.definition_id == "buy_cola"
            first.current_action.planned_end_at = clock.now + 300.0  # still running
            await persist_and_stop(first)
        finally:
            if first.phase.value != "stopped":
                await first.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            trip = open_trip(revived)
            assert trip is not None
            assert revived.current_action is not None
            assert revived.current_action.definition_id == "buy_cola"

            clock.advance(400)  # it finished while the process was down
            await revived.tick(minutes=1)

            stop = stops_of(trip)[0]
            assert stop["items"][0]["done"] is True
            assert stop["items"][0]["outcome"] == "purchased"
            assert not trip.status.terminal
            assert revived.current_action is not None
            assert revived.current_action.definition_id == "buy_snacks"
        finally:
            await revived.shutdown()
            await db.close()
