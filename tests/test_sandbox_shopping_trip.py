"""Phase C tests: 一次 shopping_trip 完成多件采购（多站行程）。

契约（§C/§14）:
* 库存低的槽按「购买动作所在店铺」分组，同店多件 → 一个 stop，店铺只去一次；
* 不同店铺按「从家出发的 BFS 跳数」排序（同跳数按动作优先级），最后显式回家；
* 一个角色同时只有一个 open trip —— 重复低库存事件不会开出第二个；
* 单个短缺仍走 Phase B 的 restock_resource goal（不倒退）；
* 行程中途某站已达标 → 跳过不买；critical 需要走既有 interrupt/rebind 机制，
  行程保持可继续；
* 底层 Action/Event 完整（每个 stop 都有 sandbox_actions 行），体验层只有一条。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.experience import ExperienceKind
from app.sandbox.goals import MIN_TRIP_ITEMS, GoalKind, GoalStatus
from app.sandbox.models import ActionDefinition, SpaceKind, SpaceNode
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from app.sandbox.world import SpaceSystem
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


def fridge(runtime):  # type: ignore[no-untyped-def]
    return runtime.inventories.get("fridge")


def trips(runtime):  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.shopping_trip]


def stops_of(runtime):  # type: ignore[no-untyped-def]
    trip = trips(runtime)[0]
    return trip.metadata["stops"]


def moves(runtime):  # type: ignore[no-untyped-def]
    return [event.target_entity_id for event in runtime.events.of_type(ET.ENTITY_MOVED)]


async def start_trip(  # type: ignore[no-untyped-def]
    runtime, clock, *, cola: int | None = None, pudding: int | None = None, cake: int | None = None
):
    """Set the requested shelves and let the detector build the trip."""
    pantry = fridge(runtime)
    if cola is not None:
        pantry.items["可乐"] = cola
    if pudding is not None:
        pantry.items["布丁"] = pudding
    if cake is not None:
        pantry.items["蛋糕"] = cake
    runtime.goal_detector.sweep()


async def run_trip(runtime, clock) -> list[str]:  # type: ignore[no-untyped-def]
    """Drive the open trip one materialized step at a time; return action ids."""
    acted: list[str] = []
    for _ in range(30):
        if runtime.goals.open_trip() is None:
            break  # the trip closed — later goals are not part of this test
        if not await runtime.goals.advance():
            break
        if runtime.current_action is None:
            break  # the trip closed without another action
        acted.append(runtime.current_action.definition_id)
        runtime.current_action.planned_end_at = clock.now
        clock.advance(1)
        await runtime._complete_action()  # noqa: SLF001
        await runtime.flush_experiences()
    return acted


def add_shop(runtime, *, space_id: str, name: str, connects: list[str]) -> None:  # type: ignore[no-untyped-def]
    runtime.spaces = SpaceSystem(
        [
            *runtime.spaces.all(),
            SpaceNode(
                id=space_id,
                name=name,
                kind=SpaceKind.shop,
                parent_id="outside",
                connects=list(connects),
                private=False,
            ),
        ]
    )
    runtime.engine.spaces = runtime.spaces  # keep the decision engine in sync


def add_buy_action(runtime, action_id: str, *, name: str, space: str, item: str, qty: int):  # type: ignore[no-untyped-def]
    runtime.actions.definitions[action_id] = ActionDefinition(
        id=action_id,
        name=name,
        activity="out",
        spaces=[space],
        min_minutes=5,
        typical_minutes=10,
        max_minutes=20,
        priority=0.5,
        tags=["shopping", "outdoor"],
        purchase={"fridge": {item: qty}},
        restock={"inventory": "fridge", "slot": item, "min": 0, "target": qty},
    )


# -------------------------------------------------------------- Case D


class TestOneShopOneStop:
    async def test_two_items_in_one_shop_are_one_stop_visited_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)

            trip = trips(runtime)[0]
            assert trip.kind is GoalKind.shopping_trip
            assert trip.priority > 0.6  # outranks the per-slot restock goals
            assert trip.dedupe_key.endswith("|shopping_trip|open")
            stops = stops_of(runtime)
            assert len(stops) == 1  # 同一家店 = 一个 stop
            assert stops[0]["space"] == "convenience_store"
            assert [item["action_id"] for item in stops[0]["items"]] == [
                "buy_cola",
                "buy_snacks",
            ]

            acted = await run_trip(runtime, clock)

            assert acted == ["buy_cola", "buy_snacks", "return_home"]
            visited = moves(runtime)
            assert visited.count("convenience_store") == 1  # 店铺只去一次
            assert visited[-1] == "apartment"
            assert trip.status is GoalStatus.completed
            assert fridge(runtime).count("可乐") == 7
            assert fridge(runtime).count("布丁") == 3
            outcomes = [item["outcome"] for item in stops[0]["items"]]
            assert outcomes == ["purchased", "purchased"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_trip_keeps_every_purchase_as_a_real_action_row(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            await run_trip(runtime, clock)

            rows = await db.fetchall(
                "SELECT definition_id, status FROM sandbox_actions"
                " WHERE definition_id IN ('buy_cola', 'buy_snacks')"
            )
            assert {row["definition_id"] for row in rows} == {"buy_cola", "buy_snacks"}
            assert all(row["status"] == "completed" for row in rows)
            # …while the experience layer only produced the one trip episode
            experiences = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            assert [row["kind"] for row in experiences] == [ExperienceKind.errand_trip.value]
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- Case E


class TestTwoShopRoute:
    async def test_route_follows_bfs_hops_then_priority_and_returns_home(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, cake=1)

            stops = stops_of(runtime)
            assert [stop["space"] for stop in stops] == ["dessert_shop", "convenience_store"]

            await run_trip(runtime, clock)

            # 家(apartment) → 甜品店 → 便利店 → 家：两店同为 2 跳，
            # 同跳数按购买动作优先级（买甜食 0.65 > 买可乐 0.6）
            assert moves(runtime) == ["dessert_shop", "convenience_store", "apartment"]
            assert runtime.character.location == "apartment"
            assert trips(runtime)[0].status is GoalStatus.completed
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_hops_are_measured_from_the_seeded_home_space(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            from app.sandbox.goals import _bfs_hops

            home = runtime.seed.character.home_space
            assert home == "apartment"
            assert _bfs_hops(runtime.spaces, home, "dessert_shop") == 2
            assert _bfs_hops(runtime.spaces, home, "livingroom") == 1
            assert _bfs_hops(runtime.spaces, home, "no_such_shop") == 1_000_000
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- Case F


class TestThreeStopsAndUnreachableShop:
    async def test_three_stops_route_reachability_and_return_home(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            add_shop(runtime, space_id="pharmacy", name="药店", connects=["outside"])
            add_shop(runtime, space_id="island_shop", name="孤岛商店", connects=[])
            add_buy_action(runtime, "buy_medicine", name="买药", space="pharmacy", item="药", qty=2)
            add_buy_action(
                runtime, "buy_island", name="买地图", space="island_shop", item="地图", qty=1
            )
            fridge(runtime).items["药"] = 0
            fridge(runtime).items["地图"] = 0

            await start_trip(runtime, clock, cola=1, cake=1)

            stops = stops_of(runtime)
            assert [stop["space"] for stop in stops] == [
                "dessert_shop",
                "convenience_store",
                "pharmacy",
            ]
            # 不可达的孤岛商店被丢弃（有日志、无异常），行程仍是 3 站
            assert all("地图" not in item["slot"] for stop in stops for item in stop["items"])
            assert len([item for stop in stops for item in stop["items"]]) == 3

            acted = await run_trip(runtime, clock)

            assert acted == ["buy_sweets", "buy_cola", "buy_medicine", "return_home"]
            assert moves(runtime) == [
                "dessert_shop",
                "convenience_store",
                "pharmacy",
                "apartment",
            ]
            assert fridge(runtime).count("药") == 2
            assert trips(runtime)[0].status is GoalStatus.completed
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ §14 dedupe


class TestTripDedupe:
    async def test_repeated_low_stock_never_opens_a_second_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            trip_id = trips(runtime)[0].goal_id
            trip_key = trips(runtime)[0].metadata["trip_id"]

            # one real purchase happens, then the same facts are replayed
            assert await runtime.goals.advance() is True
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001
            acquired = runtime.events.last(ET.ITEM_ACQUIRED)
            assert acquired is not None

            for _ in range(3):
                runtime.goal_detector.sweep()
                runtime.goal_detector.observe(acquired)  # replay the fact

            assert len(trips(runtime)) == 1
            assert trips(runtime)[0].goal_id == trip_id
            assert trips(runtime)[0].metadata["trip_id"] == trip_key
            created = [
                event
                for event in runtime.events.of_type(ET.GOAL_CREATED)
                if event.payload.get("kind") == GoalKind.shopping_trip.value
            ]
            assert len(created) == 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_new_shortage_merges_into_the_open_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            trip = trips(runtime)[0]
            assert len(trips(runtime)) == 1 and len(stops_of(runtime)) == 1

            fridge(runtime).items["蛋糕"] = 0  # a third errand appears mid-trip
            runtime.goal_detector.sweep()

            assert len(trips(runtime)) == 1
            assert trips(runtime)[0].goal_id == trip.goal_id
            assert len(stops_of(runtime)) == 2
            slots = [item["slot"] for stop in stops_of(runtime) for item in stop["items"]]
            assert "蛋糕" in slots
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stocked_world_has_no_trip_and_no_restock_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=10, pudding=3, cake=2)

            assert trips(runtime) == []
            assert runtime.goals.open_trip() is None
            assert not [
                goal
                for goal in runtime.goals.all()
                if goal.kind is GoalKind.restock_resource and not goal.status.terminal
            ]
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------- interruption / skipping


class TestTripInterruptAndSkip:
    async def test_critical_invitation_interrupts_then_the_trip_resumes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            trip = trips(runtime)[0]
            trip_id = trip.metadata["trip_id"]

            assert await runtime.goals.advance() is True
            assert runtime.current_action.definition_id == "buy_cola"  # type: ignore[union-attr]
            stop_one_instance = runtime.current_action.id  # type: ignore[union-attr]

            from app.sandbox.external import (
                ExternalSource,
                ExternalUrgency,
                ExternalWorldEvent,
            )

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="trip-inv",
                    source=ExternalSource.qq,
                    actor_id="u-core",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            assert runtime.events.last(ET.ACTION_INTERRUPTED) is not None
            assert runtime.current_action.definition_id == "play_minecraft"  # type: ignore[union-attr]

            # finish the invitation → Phase 3 resume brings the *same trip* back
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)

            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "buy_cola"
            assert runtime.current_action.id != stop_one_instance  # rebinding, not replay
            assert runtime.events.last(ET.ACTION_RESUMED) is not None
            assert trips(runtime)[0].goal_id == trip.goal_id
            assert trips(runtime)[0].metadata["trip_id"] == trip_id
            assert not trips(runtime)[0].status.terminal

            # the rebound step still closes stop one on completion
            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001
            assert stops_of(runtime)[0]["items"][0]["done"] is True
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_stop_already_at_target_is_skipped_without_buying(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            trip = trips(runtime)[0]

            assert await runtime.goals.advance() is True
            assert runtime.current_action.definition_id == "buy_cola"  # type: ignore[union-attr]
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001

            fridge(runtime).items["布丁"] = 2  # target already reached elsewhere
            assert await runtime.goals.advance() is True
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "return_home"
            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001

            stop = stops_of(runtime)[0]
            assert stop["items"][1]["outcome"] == "satisfied"
            assert stop["items"][1]["done"] is True
            assert fridge(runtime).count("布丁") == 2  # never bought
            started = [
                event.payload["action_id"] for event in runtime.events.of_type(ET.ACTION_STARTED)
            ]
            assert "buy_snacks" not in started
            assert trips(runtime)[0].goal_id == trip.goal_id
            assert trips(runtime)[0].status is GoalStatus.completed
        finally:
            await runtime.shutdown()
            await db.close()

    def test_min_trip_items_is_a_documented_threshold(self) -> None:
        assert MIN_TRIP_ITEMS >= 2


# --------------------------------------------------- shapes / Phase B bridge


class TestTripShapesAndCompatibility:
    async def test_single_shortage_stays_a_phase_b_restock_goal(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1)  # one low slot only

            assert trips(runtime) == []
            restock = [
                goal
                for goal in runtime.goals.all()
                if goal.kind is GoalKind.restock_resource and not goal.status.terminal
            ]
            assert len(restock) == 1 and restock[0].target_item == "可乐"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_trip_outranks_project_but_loses_to_critical_pet_care(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, cake=1)
            trip = trips(runtime)[0]
            active = runtime.goals.active_goal()
            assert active is not None and active.goal_id == trip.goal_id

            project = next(
                goal
                for goal in runtime.goals.all()
                if goal.kind is GoalKind.complete_project and not goal.status.terminal
            )
            assert trip.priority > project.priority

            runtime.pet.hunger = 0.95  # type: ignore[union-attr]
            runtime.events.publish(ET.PET_HUNGRY, source="test", payload={"hunger": 0.95})
            active = runtime.goals.active_goal()
            assert active is not None and active.kind is GoalKind.pet_care
            assert not trip.status.terminal  # waiting, not cancelled
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stop_entries_carry_the_documented_shape(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, cake=1)
            trip = trips(runtime)[0]
            assert set(trip.metadata) >= {"trip_id", "stops", "created_at"}
            assert trip.metadata["trip_id"].startswith("trip_")
            for stop in stops_of(runtime):
                assert set(stop) >= {"space", "items", "done"}
                for item in stop["items"]:
                    assert set(item) >= {
                        "inventory",
                        "slot",
                        "item",
                        "action_id",
                        "target",
                        "done",
                        "outcome",
                    }
                    assert item["inventory"] == "fridge"
                    assert item["target"] >= 1
                    assert item["done"] is False and item["outcome"] == ""
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_return_home_is_pure_travel(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            definition = runtime.actions.definitions.get("return_home")
            assert definition is not None, "a 回家 action must be owned by the fixture"
            assert definition.name == "回家"
            assert definition.spaces == ["apartment"]  # the conventional home token resolved
            assert definition.min_minutes == 10 and definition.typical_minutes == 20
            assert "chore" in definition.tags
            assert not definition.need_relief
            assert not definition.consumes
            assert not definition.purchase
            assert not definition.destination
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_non_trip_completion_keeps_its_own_identity_inside_a_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await start_trip(runtime, clock, cola=1, pudding=1)
            trip = trips(runtime)[0]

            started = await runtime._start_action("play_singleplayer")  # noqa: SLF001
            assert started is not None
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001
            await runtime.flush_experiences()

            completed = runtime.events.last(ET.ACTION_COMPLETED)
            assert completed is not None
            assert "trip_id" not in completed.payload  # ordinary action, no trip tag
            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            own = [row for row in rows if row["episode_key"] == f"action:{started.id}"]
            assert own and own[0]["kind"] == ExperienceKind.action_completed.value
            # the open trip is untouched by the unrelated completion
            assert not trip.status.terminal
            assert stops_of(runtime)[0]["items"][0]["done"] is False
        finally:
            await runtime.shutdown()
            await db.close()
