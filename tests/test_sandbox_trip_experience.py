"""Phase C tests: 行程的体验聚合（§XI.1/§XI.2/§21）。

契约:
* 一次 trip 的所有购买 Action/Event 完整保留，但体验层只产生 **一条**
  ``errand_trip``（episode_key = ``trip:<id>``），重要性 0.45；
* 摘要完全确定：购买动作名去掉前缀「买」，1/2/3+ 项用「和」「、」连接；
* trip A 与 trip B 是两条体验；同一实例/同一 goal 事件重放不产生第二行；
* 非行程动作的 episode_key 保持原样（action:<instance>），不被削弱。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.experience import ExperienceKind, errand_trip_summary
from app.sandbox.goals import GoalKind
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


def fridge(runtime):  # type: ignore[no-untyped-def]
    return runtime.inventories.get("fridge")


def trips(runtime):  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is GoalKind.shopping_trip]


def start_trip(runtime, clock, *, cola: int, cake: int, pudding: int | None = None):  # type: ignore[no-untyped-def]
    pantry = fridge(runtime)
    pantry.items["可乐"] = cola
    pantry.items["蛋糕"] = cake
    if pudding is not None:
        pantry.items["布丁"] = pudding
    runtime.goal_detector.sweep()
    return trips(runtime)[-1]


async def run_trip(runtime, clock) -> list[str]:  # type: ignore[no-untyped-def]
    acted: list[str] = []
    for _ in range(30):
        if runtime.goals.open_trip() is None:
            break  # the trip closed — later goals belong to other tests
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


async def trip_rows(runtime):  # type: ignore[no-untyped-def]
    rows = await runtime.store.recent_experiences(character_id=runtime.character_id, limit=50)
    return [row for row in rows if row["kind"] == ExperienceKind.errand_trip.value]


# ---------------------------------------------------------- summary shapes


class TestTripSummaryStrings:
    def test_one_two_and_three_item_summaries_are_exact(self) -> None:
        assert errand_trip_summary(["买甜食"]) == "完成了买甜食"
        assert errand_trip_summary(["买甜食", "买饮料"]) == "完成了买甜食和饮料"
        assert errand_trip_summary(["买甜食", "买饮料", "买日用品"]) == "完成了买甜食、饮料和日用品"

    def test_summary_strips_buy_prefix_dedupes_and_falls_back_to_items(self) -> None:
        assert errand_trip_summary(["买可乐", "可乐"]) == "完成了买可乐"
        assert errand_trip_summary(["", "布丁"]) == "完成了买布丁"
        assert errand_trip_summary([]) == "完成了一趟出门采购"


# ------------------------------------------------------------ real trips


class TestRealTripExperience:
    async def test_two_item_trip_yields_one_experience_with_trip_key(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            trip = start_trip(runtime, clock, cola=1, cake=1)
            trip_id = trip.metadata["trip_id"]
            acted = await run_trip(runtime, clock)
            assert acted == ["buy_sweets", "buy_cola", "return_home"]

            rows = await trip_rows(runtime)
            assert len(rows) == 1
            row = rows[0]
            assert row["episode_key"] == f"trip:{trip_id}"
            assert row["summary"] == "完成了买甜食和可乐"
            assert row["importance"] == 0.45
            assert row["metadata"]["trip_id"] == trip_id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_three_purchase_trip_yields_one_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            trip = start_trip(runtime, clock, cola=1, cake=1, pudding=1)
            trip_id = trip.metadata["trip_id"]
            acted = await run_trip(runtime, clock)
            assert acted == ["buy_sweets", "buy_cola", "buy_snacks", "return_home"]

            rows = await trip_rows(runtime)
            assert len(rows) == 1
            assert rows[0]["episode_key"] == f"trip:{trip_id}"
            assert rows[0]["summary"] == "完成了买甜食、可乐和零食"
            # 底层 Action/Event 完整：每一步都有自己的 ACTION_COMPLETED
            completed = [
                event
                for event in runtime.events.of_type(ET.ACTION_COMPLETED)
                if event.payload.get("trip_id") == trip_id
            ]
            assert [event.target_entity_id for event in completed] == [
                "buy_sweets",
                "buy_cola",
                "buy_snacks",
                "return_home",  # the closing walk is a trip step too
            ]
            purchases = [event for event in completed if event.target_entity_id != "return_home"]
            assert len({event.payload["action_instance_id"] for event in purchases}) == 3
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_trip_a_and_trip_b_are_two_distinct_experiences(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            first = start_trip(runtime, clock, cola=1, cake=1)
            await run_trip(runtime, clock)

            second = start_trip(runtime, clock, cola=1, cake=1)
            await run_trip(runtime, clock)

            assert first.metadata["trip_id"] != second.metadata["trip_id"]
            rows = await trip_rows(runtime)
            assert len(rows) == 2
            assert {row["episode_key"] for row in rows} == {
                f"trip:{first.metadata['trip_id']}",
                f"trip:{second.metadata['trip_id']}",
            }
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_replaying_the_same_events_never_duplicates_the_row(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            trip = start_trip(runtime, clock, cola=1, cake=1)
            trip_id = trip.metadata["trip_id"]
            await run_trip(runtime, clock)
            before = await trip_rows(runtime)
            assert len(before) == 1

            # replay every child completion + the goal completion (§10.1 §7)
            for event in runtime.events.of_type(ET.ACTION_COMPLETED):
                runtime.experiences.observe(event)
            completed_goal = next(
                event
                for event in runtime.events.of_type(ET.GOAL_COMPLETED)
                if event.payload.get("trip_id") == trip_id
            )
            runtime.experiences.observe(completed_goal)
            await runtime.flush_experiences()

            after = await trip_rows(runtime)
            assert len(after) == 1
            assert after[0]["episode_key"] == before[0]["episode_key"]
            # the database itself refuses the duplicate episode: a *new* record
            # id for the same trip key lands on the existing row
            emitted = [
                record
                for record in runtime.experiences.emitted()
                if record.kind is ExperienceKind.errand_trip
            ]
            replay_record = emitted[0].model_copy(update={"id": "exp_replayed_trip"})
            assert await runtime.store.save_experience(replay_record) == "existing"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_non_trip_actions_keep_their_instance_episode_key(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            started = await runtime._start_action("play_singleplayer")  # noqa: SLF001
            assert started is not None
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001
            await runtime.flush_experiences()

            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            action_rows = [
                row for row in rows if row["kind"] == ExperienceKind.action_completed.value
            ]
            assert action_rows, "ordinary completions must stay experiences"
            assert action_rows[-1]["episode_key"] == f"action:{started.id}"
            assert await trip_rows(runtime) == []
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------ memory alignment


class TestTripMemoryCandidate:
    def test_candidate_dedupe_is_aligned_with_the_trip_episode(self) -> None:
        from app.sandbox.experience import ExperienceRecord
        from app.sandbox.memory_foundation import MemoryCandidateBuilder

        record = ExperienceRecord(
            id="exp_trip",
            character_id="c1",
            kind=ExperienceKind.errand_trip,
            summary="完成了买甜食和可乐",
            importance=0.45,
            episode_key="trip:trip_abc",
            correlation_id="goal_1",
            metadata={"trip_id": "trip_abc"},
        )
        candidate = MemoryCandidateBuilder().from_experience(record, now=0.0)[0]
        assert candidate.dedupe_key == "c1|episodic|trip:trip_abc"
        assert "trip:trip_abc" in candidate.dedupe_key
