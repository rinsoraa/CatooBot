"""Phase 4 tests (§28): SandboxEvent → Experience → Candidate → Memory → Continuity.

The chain is verified causally: a memory that exists must be traceable back to
the exact sandbox events that caused it, must belong to one character, and must
survive a restart. Nothing here calls an LLM or an embedding model.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.experience import ExperienceKind
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.memory_foundation import MemoryCandidateBuilder, MemoryType
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'memory.db'}"))
    await db.connect()
    return db


async def make_runtime(*, db: Database | None = None, bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock or Clock(),
    )
    await runtime.start()
    return runtime


async def complete_now(runtime, clock, action_id: str) -> None:  # type: ignore[no-untyped-def]
    started = await runtime._start_action(action_id)  # noqa: SLF001
    assert started is not None, f"{action_id} did not start"
    runtime.current_action.planned_end_at = clock.now
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001
    await runtime.flush_experiences()  # the completion path also flushes


# ----------------------------------------------------------- Test 1 / 12


class TestActionToMemory:
    async def test_completion_reaches_a_memory_with_provenance(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            await complete_now(runtime, clock, "play_singleplayer")

            experiences = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=10
            )
            assert experiences, "no experience recorded for the completed action"
            kinds = {row["kind"] for row in experiences}
            assert ExperienceKind.action_completed.value in kinds

            memories = await runtime.memory.active_memories()
            assert memories, "no long-term memory promoted"
            memory = memories[0]
            provenance = memory["provenance"]
            assert provenance["candidate_id"].startswith("cand_")
            assert provenance["source_experience_ids"], "experience link missing"
            assert provenance["source_event_ids"], "event ids missing"

            # §28-12: memory → experience → sandbox event, fully walkable
            detail = await runtime.memory.memory_provenance(int(memory["id"]))
            assert detail is not None
            experience_id = provenance["source_experience_ids"][0]
            exp_row = next(row for row in experiences if row["id"] == experience_id)
            event_id = exp_row["source_event_ids"][0]
            assert any(e.event_id == event_id for e in runtime.events.recent(limit=200))
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 2


class TestNoiseFilter:
    async def test_plain_tick_does_not_create_memories(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            for _ in range(3):
                clock.advance(600)
                await runtime.tick(minutes=10)
                await runtime.flush_experiences()

            assert await runtime.memory.count() == 0
            assert await runtime.store.count_experiences(character_id=runtime.character_id) == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_social_temperature_only_is_experience_not_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Low-importance experiences are kept as experiences, never promoted."""
        db = await make_db(tmp_path)
        runtime = await make_runtime(db=db)
        try:
            key = next(iter(runtime.social_spaces))
            runtime.update_social_space(key, temperature_delta=0.05, source="test", reason="poke")
            await runtime.flush_experiences()
            assert await runtime.memory.count() == 0
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 3


class TestLastCola:
    async def test_depletion_learns_a_world_fact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            fridge_key, drink = runtime._fridge_key, runtime._drink_item  # noqa: SLF001
            runtime.inventories.get(fridge_key).items = {drink: 1}
            await complete_now(runtime, clock, "drink_cola")

            # the intermediate facts existed…
            assert runtime.events.last(ET.ITEM_CONSUMED) is not None
            assert runtime.events.last(ET.INVENTORY_DEPLETED) is not None
            assert runtime.events.last(ET.KNOWLEDGE_CHANGED) is not None
            # …but only the actionable knowledge became a memory candidate
            await runtime.flush_experiences()
            types = {m["category"] for m in await runtime.memory.active_memories()}
            assert types, "expected at least one memory"
            facts = [
                m
                for m in await runtime.memory.active_memories()
                if "empty" in str(m["provenance"])
                or "发现" in m["content"]
                or "stock" in m["content"]
            ]
            assert facts, "the first-time knowledge did not become a world fact"
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 4


class TestPetCare:
    async def test_feed_chain_yields_exactly_one_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)  # PET_HUNGRY → PET_APPROACHED
            pet_id = runtime.pet.id  # type: ignore[union-attr]
            from app.sandbox.interactions import InteractionRequest

            await runtime.interactions.execute(
                InteractionRequest(target_id=pet_id, target_kind="pet", interaction_type="feed")
            )
            await runtime.flush_experiences()

            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            pet_rows = [row for row in rows if row["kind"] == ExperienceKind.pet_care.value]
            assert len(pet_rows) == 1, f"expected one pet experience, got {len(pet_rows)}"
            assert runtime.events.last(ET.PET_FED) is not None
            memories = await runtime.memory.active_memories()
            pet_memories = [m for m in memories if "饿" in m["content"]]
            assert len(pet_memories) == 1
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 5


class TestGameInvitation:
    async def test_invitation_episode_is_one_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(10 * 60)
            event = adapt_qq_message(
                message_id="m-inv-1",
                actor_id="u-core",
                text="来一起联机",
                is_core_actor=True,
            )
            await runtime.submit_external(event)
            await runtime.wakeup()
            await runtime.flush_experiences()

            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            influence = [
                row for row in rows if row["kind"] == ExperienceKind.external_influence.value
            ]
            assert len(influence) == 1, "the invitation episode must be one experience"
            assert runtime.events.last(ET.WORLD_EXTERNAL_INFLUENCE) is not None
            # ACTION_STARTED is bookkeeping, never an experience
            assert not any(row["kind"] == "action_started" for row in rows)
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 6


class TestProjectMilestone:
    async def test_routine_progress_is_not_a_milestone(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            project_id = next(iter(runtime.projects))
            runtime.projects[project_id]["progress"] = 0.35
            runtime.update_project_progress(project_id, delta=0.01, source="test", reason="routine")
            await runtime.flush_experiences()
            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            assert not [r for r in rows if r["kind"] == ExperienceKind.project_milestone.value]

            runtime.update_project_progress(
                project_id, delta=0.64, source="test", reason="finish"
            )  # crosses 0.5/0.75/1.0
            await runtime.flush_experiences()
            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            milestones = [r for r in rows if r["kind"] == ExperienceKind.project_milestone.value]
            assert len(milestones) == 1
            memories = await runtime.memory.active_memories()
            assert any("完工" in m["content"] for m in memories)
        finally:
            await runtime.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 7


class TestDedup:
    async def test_same_candidate_is_never_stored_twice(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            await complete_now(runtime, clock, "play_singleplayer")
            before = await runtime.memory.count()
            assert before >= 1

            # re-process the very same experiences (as a restart/replay would)
            records = runtime.experiences.emitted()
            candidates = [
                c
                for record in records
                for c in runtime.candidates.from_experience(record, now=clock.now)
            ]
            assert candidates
            await runtime.memory.ingest_all(candidates)
            assert await runtime.memory.count() == before
            assert runtime.memory.duplicates >= 1
        finally:
            await runtime.shutdown()
            await db.close()

    def test_episodic_identity_includes_the_chain(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Two genuinely separate episodes must not collapse into one (§15)."""
        builder = MemoryCandidateBuilder()
        from app.sandbox.experience import ExperienceRecord

        def candidate_for(correlation: str):  # type: ignore[no-untyped-def]
            return builder.from_experience(
                ExperienceRecord(
                    id=f"exp_{correlation}",
                    character_id="c1",
                    kind=ExperienceKind.action_completed,
                    summary="她做完了某件事",
                    importance=0.5,
                    correlation_id=correlation,
                    metadata={"action_name": "某动作"},
                ),
                now=0.0,
            )[0]

        first, second = candidate_for("chain-a"), candidate_for("chain-b")
        assert first.dedupe_key != second.dedupe_key


# ----------------------------------------------------------------- Test 8


class TestCrossCharacterIsolation:
    async def test_two_characters_share_the_store_but_not_memories(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_runtime(db=db, clock=clock)
        second = await make_runtime(db=db, bible_path=OTHER_BIBLE_PATH, clock=clock)
        try:
            assert first.character_id != second.character_id
            await complete_now(first, clock, "play_singleplayer")
            # 阿澈's world has no long actions here — a discovery still counts
            second.set_knowledge("something_learned", source="observation", reason="test")
            await second.flush_experiences()

            first_memories = await first.memory.active_memories()
            second_memories = await second.memory.active_memories()
            assert first_memories and second_memories
            first_ids = {m["id"] for m in first_memories}
            second_ids = {m["id"] for m in second_memories}
            assert first_ids.isdisjoint(second_ids)
            # provenance lookups are scoped too
            foreign = next(iter(second_ids))
            assert await first.memory.memory_provenance(int(foreign)) is None
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()


# ----------------------------------------------------------------- Test 9


class TestRestartPersistence:
    async def test_memory_survives_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_runtime(db=db, clock=clock)
        try:
            await complete_now(first, clock, "play_singleplayer")
            count_before = await first.memory.count()
            character_id = first.character_id
            assert count_before >= 1
        finally:
            await first.shutdown()

        second = await make_runtime(db=db, clock=clock)
        try:
            assert second.character_id == character_id
            assert await second.memory.count() == count_before
            memories = await second.memory.active_memories()
            assert memories and memories[0]["provenance"]["source_event_ids"]
            # experiences survive too
            rows = await second.store.recent_experiences(character_id=character_id, limit=10)
            assert rows
        finally:
            await second.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 10


class TestContinuitySnapshot:
    async def test_snapshot_composes_world_knowledge_and_memories(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            await complete_now(runtime, clock, "play_singleplayer")
            runtime.set_knowledge("somewhere_thing", source="observation", reason="test")
            project_id = next(iter(runtime.projects))
            runtime.projects[project_id]["progress"] = 0.5  # unfinished
            await runtime.flush_experiences()

            snapshot = await runtime.build_continuity()
            assert snapshot.character_id == runtime.character_id
            assert snapshot.current_location  # where she is
            assert snapshot.current_world_state_summary
            assert snapshot.active_knowledge, "knowledge missing from the snapshot"
            assert snapshot.unfinished_projects, "unfinished project missing"
            assert snapshot.important_recent_experiences, "experiences missing"
            assert snapshot.active_memories, "memories missing"

            # persisted for restart continuity (§23/§26)
            saved = await runtime.continuity_snapshot.load()
            assert saved is not None and saved["character_id"] == runtime.character_id
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_snapshot_never_touches_world_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§25/§29: the snapshot is a read model — building it changes nothing."""
        db = await make_db(tmp_path)
        runtime = await make_runtime(db=db)
        try:
            location = runtime.character.location
            action = runtime.current_action
            mutations_before = len(runtime.mutations)
            await runtime.build_continuity()
            assert runtime.character.location == location
            assert runtime.current_action is action
            assert len(runtime.mutations) == mutations_before
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 11


class TestKnowledgeIsNotMemory:
    async def test_relearning_creates_no_experience_or_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            # a first-time discovery may become one world_fact memory (§18)…
            runtime.set_knowledge("daily_thing", source="observation", reason="first")
            await runtime.flush_experiences()
            assert runtime.experiences.pending() == 0
            after_discovery = await runtime.memory.count()

            # …but re-learning the same thing is upkeep, never a new memory
            runtime.set_knowledge("daily_thing", source="observation", reason="again")
            await runtime.flush_experiences()
            rows = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=10
            )
            learned = [r for r in rows if r["kind"] == ExperienceKind.knowledge_learned.value]
            assert len(learned) == 1, "re-learning must not add experiences"
            assert await runtime.memory.count() == after_discovery
        finally:
            await runtime.shutdown()
            await db.close()

    def test_candidate_types_cover_the_spec(self) -> None:
        """:memory_type vocabulary = episodic / semantic / social / world_fact."""
        assert {t.value for t in MemoryType} >= {"episodic", "semantic", "social", "world_fact"}
        builder = MemoryCandidateBuilder()
        assert builder.PROMOTION_THRESHOLD > builder.CANDIDATE_FLOOR


# ---------------------------------------------------------------- Test 12


class TestProvenanceChain:
    async def test_every_active_memory_is_walkable_to_events(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            await complete_now(runtime, clock, "play_singleplayer")
            runtime.pet.hunger = 0.74  # type: ignore[union-attr]
            await runtime.tick(minutes=10)
            from app.sandbox.interactions import InteractionRequest

            await runtime.interactions.execute(
                InteractionRequest(
                    target_id=runtime.pet.id,
                    target_type=None,
                    target_kind="pet",  # type: ignore[arg-type,union-attr]
                    interaction_type="feed",
                )
            )
            await runtime.flush_experiences()

            memories = await runtime.memory.active_memories()
            assert len(memories) >= 2
            known_events = {e.event_id for e in runtime.events.recent(limit=256)}
            for memory in memories:
                detail = await runtime.memory.memory_provenance(int(memory["id"]))
                assert detail is not None
                assert detail["source_event_ids"], "event provenance missing"
                assert all(eid in known_events for eid in detail["source_event_ids"])
                assert detail["source_experience_ids"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_importance_candidate_is_rejected_not_stored(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_runtime(db=db)
        try:
            from app.sandbox.experience import ExperienceRecord

            record = ExperienceRecord(
                id="exp_low",
                character_id=runtime.character_id,
                kind=ExperienceKind.action_resumed,
                summary="她回来继续某件事",
                importance=0.45,  # below promotion threshold, above the floor
                correlation_id="chain-low",
                metadata={"action_name": "某动作"},
            )
            candidates = runtime.candidates.from_experience(record, now=0.0)
            assert candidates
            result = await runtime.memory.ingest(candidates[0])
            assert result.status == "rejected"
            assert "below_threshold" in result.reason_code
            assert await runtime.memory.count() == 0
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- evaluator-level extras


class TestExperienceBuilderRules:
    def test_event_allowlist_excludes_noise(self) -> None:
        from app.sandbox.experience import EXPERIENCE_EVENTS

        assert ET.NEED_CHANGED not in EXPERIENCE_EVENTS
        assert ET.ACTION_REQUESTED not in EXPERIENCE_EVENTS
        assert ET.ACTION_STARTED not in EXPERIENCE_EVENTS
        assert ET.ENTITY_MOVE_REQUESTED not in EXPERIENCE_EVENTS
        assert ET.ACTION_COMPLETED in EXPERIENCE_EVENTS
        assert ET.PET_FED in EXPERIENCE_EVENTS

    async def test_background_pressure_does_not_pollute(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_runtime(db=db)
        try:
            for _ in range(5):
                runtime.adjust_need("thirst", delta=0.05, source="test", reason="drift")
            await runtime.flush_experiences()
            assert runtime.experiences.pending() == 0
            assert await runtime.store.count_experiences(character_id=runtime.character_id) == 0
        finally:
            await runtime.shutdown()
            await db.close()
