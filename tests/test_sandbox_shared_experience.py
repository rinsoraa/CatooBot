"""Phase 10 tests (§25/§34): Social Experience & Shared Memory Continuity.

The contract: "we did this together" is a *verifiable, persistent, retrievable,
isolated* fact chain — built from verified shared-activity facts, aggregated to
one episode per real act, promoted to long-term memory only by deterministic
social weight, and recalled with a person-aware boost that never lets memory
override the current world or the relationship.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.commitments import MIN_FULFILL_DURATION_MINUTES
from app.sandbox.experience import (
    SHARED_ACTIVITY_BASE_IMPORTANCE,
    SHARED_COMMITMENT_BONUS,
    SHARED_LONG_BONUS,
    SHARED_LONG_MINUTES,
    SHARED_MAJOR_BONUS,
)
from app.sandbox.memory_foundation import MemoryScope, MemoryType
from app.sandbox.relations import InteractionSignificance, SocialInteractionFact
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


async def make_sandbox(*, db, clock, bible_path=None, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


def person_of(runtime, qq: str) -> str:  # type: ignore[no-untyped-def]
    return runtime.persons.for_qq(qq).person_id


def shared_fact(
    runtime,  # type: ignore[no-untyped-def]
    qq: str,
    *,
    activity: str = "gaming",
    minutes: float = 35.0,
    commitment_id: str = "",
    instance_id: str = "",
    significance: InteractionSignificance = InteractionSignificance.meaningful,
) -> SocialInteractionFact:
    """The verified fact as the runtime itself emits it (§5)."""
    return SocialInteractionFact.create(
        character_id=runtime.character_id,
        person_id=person_of(runtime, qq),
        interaction_type="shared_activity",
        source="character_action",
        timestamp=runtime._clock(),  # noqa: SLF001
        outcome="completed",
        significance=significance,
        metadata={
            "target_activity": activity,
            "duration_minutes": minutes,
            "commitment_id": commitment_id,
            "action_id": "play_minecraft" if activity == "gaming" else activity,
            "action_instance_id": instance_id or f"act_{qq}",
        },
    )


async def share(runtime, qq: str, **kwargs) -> SocialInteractionFact:  # type: ignore[no-untyped-def]
    fact = shared_fact(runtime, qq, **kwargs)
    await runtime.apply_social_interaction(fact)
    return fact


def shared_experiences(runtime) -> list:  # type: ignore[no-untyped-def]
    return [
        record for record in runtime.experiences.emitted() if record.kind.value == "shared_activity"
    ]


def advance_to_window(clock, commitment) -> None:  # type: ignore[no-untyped-def]
    clock.advance(max(0.0, float(commitment.earliest_at) + 60.0 - float(clock.now)))


# ---------------------------------------------------------------- §25 A/D


class TestCommitmentBoundSharedExperience:
    async def test_a_kept_promise_is_one_coherent_social_episode(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A real episode: commitment → goal → action → fact → one experience."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            person = person_of(runtime, "9201")
            await runtime.apply_social_interaction(
                SocialInteractionFact.create(
                    character_id=runtime.character_id,
                    person_id=person,
                    interaction_type="invitation_accepted",
                    source="test",
                    timestamp=clock.now,
                    significance=InteractionSignificance.meaningful,
                    external_ids={"qq": "9201"},
                    metadata={"time_hint": "今晚一起打游戏", "target_activity": "gaming"},
                )
            )
            commitment = runtime.commitments.open()[0]
            advance_to_window(clock, commitment)
            await runtime.tick(minutes=1)
            action = runtime.current_action
            assert action is not None and action.definition_id == "play_minecraft"
            clock.advance(max(0.0, action.planned_end_at + 60.0 - clock.now))
            await runtime.tick(minutes=int((action.planned_end_at - action.started_at) / 60.0) + 2)
            await runtime.wait_social()
            await runtime.flush_experiences()

            episodes = shared_experiences(runtime)
            assert len(episodes) == 1  # §7: one act is one episode, not a log
            episode = episodes[0]
            assert episode.metadata["person_id"] == person  # §4 stable identity
            assert episode.metadata["activity"] == "gaming"
            assert episode.metadata["commitment_id"] == commitment.commitment_id
            assert episode.metadata["action_id"] == "play_minecraft"
            assert episode.metadata["action_instance_id"] == action.id
            assert episode.metadata["duration_minutes"] >= MIN_FULFILL_DURATION_MINUTES
            assert person in episode.summary or "分钟" in episode.summary
            assert episode.importance >= SHARED_ACTIVITY_BASE_IMPORTANCE + SHARED_COMMITMENT_BONUS

            # §7: the action's own record was absorbed, not duplicated
            same_instance = [
                record
                for record in runtime.experiences.emitted()
                if record.metadata.get("action_instance_id") == action.id
            ]
            assert len(same_instance) == 1
            assert same_instance[0].kind.value == "shared_activity"

            # §10/§11/§12: the long-term memory carries the person context
            memory = runtime.memory
            assert await memory.count() >= 1
            rows, trace = await memory.retrieve_relevant(query="gaming", person_id=person)
            assert rows, trace
            provenance = rows[0]["provenance"]
            assert provenance["person_id"] == person
            assert provenance["commitment_id"] == commitment.commitment_id
            assert provenance["action_instance_id"] == action.id
            assert provenance["activity"] == "gaming"
            assert rows[0]["person_match"] == 1.0
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------------- §25 B/C


class TestInformalSharedActivity:
    async def test_a_short_session_is_an_experience_but_not_a_memory(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§9 plain case: it happened, so it is lived; it is small, so it is not kept."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(runtime, "9202", minutes=35.0)
            await runtime.flush_experiences()
            episodes = shared_experiences(runtime)
            assert len(episodes) == 1  # §6 path B: no commitment needed
            assert episodes[0].metadata["commitment_id"] == ""
            assert episodes[0].importance == SHARED_ACTIVITY_BASE_IMPORTANCE
            assert await runtime.memory.count() == 0  # below promotion threshold
            assert runtime.commitments.all() == []  # §6: never invents a promise
            assert not [g for g in runtime.goals.all() if g.kind.value == "fulfill_commitment"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_too_short_to_count_is_not_a_shared_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§25 C: below the Phase 9 threshold nothing social is remembered."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(runtime, "9203", minutes=MIN_FULFILL_DURATION_MINUTES - 1)
            await runtime.flush_experiences()
            assert shared_experiences(runtime) == []
            assert await runtime.memory.count() == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_long_or_major_session_gains_deterministic_weight(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(runtime, "9204", minutes=SHARED_LONG_MINUTES + 10.0)
            await share(runtime, "9205", minutes=20.0, significance=InteractionSignificance.major)
            await runtime.flush_experiences()
            episodes = {
                record.metadata["person_id"]: record for record in shared_experiences(runtime)
            }
            long_one = episodes[person_of(runtime, "9204")]
            major_one = episodes[person_of(runtime, "9205")]
            assert long_one.importance == SHARED_ACTIVITY_BASE_IMPORTANCE + SHARED_LONG_BONUS
            assert major_one.importance == SHARED_ACTIVITY_BASE_IMPORTANCE + SHARED_MAJOR_BONUS
            # a long plain session still stays an experience, not a memory
            assert await runtime.memory.count() >= 1  # the major one promotes
            rows, _trace = await runtime.memory.retrieve_relevant(query="gaming")
            assert [row["provenance"]["person_id"] for row in rows] == [person_of(runtime, "9205")]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_real_sessions_stay_two_memories(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§12/§25 E: same person + same activity twice is two episodes."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            first = await share(runtime, "9206", minutes=40.0, instance_id="act_day1")
            clock.advance(2 * 86400.0)  # two days later, same activity again
            second = await share(runtime, "9206", minutes=50.0, instance_id="act_day3")
            assert first.interaction_id != second.interaction_id
            await runtime.flush_experiences()
            episodes = shared_experiences(runtime)
            assert len(episodes) == 2
            identities = {record.metadata["action_instance_id"] for record in episodes}
            assert identities == {"act_day1", "act_day3"}
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- §24 replay


class TestReplaySafety:
    async def test_replaying_the_same_fact_does_not_stack_episodes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            fact = shared_fact(
                runtime,
                "9207",
                minutes=45.0,
                instance_id="act_rep",
                significance=InteractionSignificance.major,  # promotion-worthy
            )
            await runtime.apply_social_interaction(fact)
            await runtime.apply_social_interaction(fact)  # the same fact, replayed
            await runtime.flush_experiences()
            episodes = shared_experiences(runtime)
            assert len(episodes) == 1  # one real act, one episode (§24)
            assert len(episodes[0].source_event_ids) == 2  # both events are provenance

            # and re-processing the same episode does not stack memories: the
            # candidate is rebuilt (same identity, new id) and dedupes (§12)
            candidates = runtime.candidates.from_experience(episodes[0], now=clock.now)
            assert len(candidates) == 1
            await runtime.memory.ingest_all(candidates)
            replayed = runtime.candidates.from_experience(episodes[0], now=clock.now)
            assert replayed[0].dedupe_key == candidates[0].dedupe_key
            await runtime.memory.ingest_all(replayed)
            shared_rows = [
                row
                for row in await runtime.memory.active_memories(limit=10)
                if str(row.get("provenance", {}).get("action_instance_id", "")) == "act_rep"
            ]
            assert len(shared_rows) == 1  # one episode, one memory — replay included
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- §14-§16 person-aware recall


class TestPersonAwareRetrieval:
    async def test_the_person_being_talked_to_gets_the_boost(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            # both sessions are long enough to become long-term memories
            await share(
                runtime,
                "9208",
                activity="gaming",
                minutes=30.0,
                significance=InteractionSignificance.major,
                instance_id="act_a",
            )
            await share(
                runtime,
                "9209",
                activity="gaming",
                minutes=30.0,
                significance=InteractionSignificance.major,
                instance_id="act_b",
            )
            await runtime.flush_experiences()
            first, second = person_of(runtime, "9208"), person_of(runtime, "9209")

            rows, trace = await runtime.memory.retrieve_relevant(
                query="gaming", person_id=first, limit=5
            )
            assert trace["person_id"] == first
            by_person = {row["provenance"]["person_id"]: row for row in rows}
            assert by_person[first]["person_match"] == 1.0
            assert by_person[second]["person_match"] == 0.0
            assert rows[0]["provenance"]["person_id"] == first  # the boost decides
            # without a person in play the same rows are scored without any boost
            plain, _ = await runtime.memory.retrieve_relevant(query="gaming", limit=5)
            assert all(row["person_match"] == 0.0 for row in plain)
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_another_person_never_borrows_the_boost(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§15: un-related people's shared memories are not pulled in for free."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(
                runtime,
                "9210",
                activity="movie",
                minutes=40.0,
                significance=InteractionSignificance.major,
                instance_id="act_movie",
            )
            await runtime.flush_experiences()
            movie_person = person_of(runtime, "9210")
            other = person_of(runtime, "9211")

            mine, _ = await runtime.memory.retrieve_relevant(
                query="movie", person_id=movie_person, limit=5
            )
            theirs, _ = await runtime.memory.retrieve_relevant(
                query="movie", person_id=other, limit=5
            )
            assert mine and mine[0]["person_match"] == 1.0
            assert all(row["person_match"] == 0.0 for row in theirs)
            assert all(row["provenance"]["person_id"] != other for row in theirs)
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------- §22 character isolation


class TestCharacterIsolation:
    async def test_two_worlds_sharing_a_person_keep_their_own_memories(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            qq = "9212"
            person = person_of(first, qq)
            assert person == person_of(second, qq)  # same person, two worlds
            await share(
                first,
                qq,
                minutes=30.0,
                significance=InteractionSignificance.major,
                instance_id="act_world1",
            )
            await share(
                second,
                qq,
                minutes=30.0,
                significance=InteractionSignificance.major,
                instance_id="act_world2",
            )
            await first.flush_experiences()
            await second.flush_experiences()
            assert first.character_id != second.character_id

            mine, _ = await first.memory.retrieve_relevant(query="gaming", person_id=person)
            theirs, _ = await second.memory.retrieve_relevant(query="gaming", person_id=person)
            assert mine and theirs
            mine_ids = {row["memory_id"] for row in mine}
            theirs_ids = {row["memory_id"] for row in theirs}
            assert mine_ids.isdisjoint(theirs_ids)  # never each other's rows
            assert mine[0]["provenance"]["action_instance_id"] == "act_world1"
            assert theirs[0]["provenance"]["action_instance_id"] == "act_world2"
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()


# ------------------------------------------- §18/§19/§20 world + relationship


class TestBoundaries:
    async def test_current_world_stays_authoritative(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§18: a memory is a reference — it never rewrites the live world."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(
                runtime,
                "9213",
                activity="gaming",
                minutes=40.0,
                significance=InteractionSignificance.major,
                instance_id="act_past",
            )
            await runtime.flush_experiences()
            assert await runtime._start_action("sleep", duration_minutes=60) is not None  # noqa: SLF001
            sleeping = runtime.current_action
            assert sleeping is not None

            context = await runtime.cognitive_context(query="gaming", relationship_target="9213")
            payload = context.as_prompt_payload()
            assert payload["memories"], "the shared memory is available as a reference"
            assert "gaming" in payload["memories"][0]["text"]
            # the world is untouched by the recall: she is still asleep
            assert runtime.current_action is sleeping
            assert runtime.current_action.definition_id == "sleep"
            definition = runtime.actions.definition(sleeping)
            assert context.current_action == (definition.name if definition else "sleep")
            assert payload["world_line"] != payload["memories"][0]["text"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_memory_work_never_moves_the_relationship(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19: only SocialInteractionFacts change trust; recall changes nothing."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(runtime, "9214", minutes=40.0, instance_id="act_rel")
            await runtime.flush_experiences()
            person = person_of(runtime, "9214")
            before = await runtime.relationships_dyn.get(person)
            assert before is not None
            snapshot = before.model_dump()

            await runtime.memory.retrieve_relevant(query="gaming", person_id=person)
            await runtime.cognitive_context(query="gaming", relationship_target="9214")
            await runtime.build_continuity()

            after = await runtime.relationships_dyn.get(person)
            assert after is not None and after.model_dump() == snapshot
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------- §17/§23/§25 L restart


class TestPersistence:
    async def test_the_person_link_survives_a_restart(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(
                runtime,
                "9215",
                minutes=40.0,
                commitment_id="cm_persisted",
                instance_id="act_survives",
            )
            await runtime.flush_experiences()
            person = person_of(runtime, "9215")
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            rows, trace = await restored.memory.retrieve_relevant(query="gaming", person_id=person)
            assert rows, trace  # still recallable after the restart
            provenance = rows[0]["provenance"]
            assert provenance["person_id"] == person  # §23: the person link persisted
            assert provenance["commitment_id"] == "cm_persisted"
            assert provenance["action_instance_id"] == "act_survives"
            assert rows[0]["person_match"] == 1.0

            # the experience row kept its episode metadata too
            experience_rows = await restored.store.recent_experiences(
                character_id=restored.character_id, limit=10
            )
            shared_rows = [row for row in experience_rows if row.get("kind") == "shared_activity"]
            assert shared_rows
        finally:
            await restored.shutdown()
            await db.close()


# ------------------------------------------------------------------- §21 no-LLM


class TestNoLlm:
    async def test_the_whole_chain_is_deterministic(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)  # no model at all
        try:
            await share(runtime, "9216", minutes=40.0, significance=InteractionSignificance.major)
            await runtime.flush_experiences()
            assert runtime.decisions.llm_calls == 0
            assert shared_experiences(runtime)
            # the shared episode is a long-term memory (the major interaction may
            # also leave its own relationship memory — that is Phase 8's, not a copy)
            shared_rows = [
                row
                for row in await runtime.memory.active_memories(limit=10)
                if str(row.get("provenance", {}).get("action_instance_id", ""))
            ]
            assert len(shared_rows) == 1
            rows, _trace = await runtime.memory.retrieve_relevant(query="gaming")
            assert rows
            # §21: deterministic — the same query + state gives the same order
            again, _ = await runtime.memory.retrieve_relevant(query="gaming")
            assert [row["memory_id"] for row in rows] == [row["memory_id"] for row in again]
        finally:
            await runtime.shutdown()
            await db.close()


class TestCandidateSpec:
    def test_the_social_spec_is_explicit(self) -> None:
        """§11/§12: social memories are verifiable one-liners with an episode id."""
        from app.sandbox.experience import ExperienceKind, ExperienceRecord
        from app.sandbox.memory_foundation import MemoryCandidateBuilder

        record = ExperienceRecord(
            id="exp_test",
            character_id="character@hash",
            kind=ExperienceKind.shared_activity,
            summary="和某人一起gaming了35分钟",
            importance=0.65,
            metadata={
                "person_id": "person_qq_x",
                "name": "小明",
                "activity": "gaming",
                "duration_minutes": 35.0,
                "commitment_id": "cm_1",
            },
        )
        spec = MemoryCandidateBuilder()._spec(record)  # noqa: SLF001
        assert spec is not None
        memory_type, scope, content, identity, confidence = spec
        assert memory_type is MemoryType.social and scope is MemoryScope.social
        assert identity == "shared:person_qq_x:cm_1"
        assert "小明" in content and "gaming" in content
        assert confidence >= 0.8
        # a second real episode for the same person is a different identity
        again = record.model_copy(deep=True)
        again.metadata["commitment_id"] = "cm_2"
        other = MemoryCandidateBuilder()._spec(again)  # noqa: SLF001
        assert other is not None and other[3] != identity


# ------------------------------------------------ §10.1 persistent episode identity


async def episode_rows(db, character_id: str, episode_key: str = "") -> list[dict]:  # type: ignore[no-untyped-def]
    if episode_key:
        return await db.fetchall(
            "SELECT id, character_id, episode_key FROM sandbox_experiences"
            " WHERE character_id = ? AND episode_key = ?",
            (character_id, episode_key),
        )
    return await db.fetchall(
        "SELECT id, character_id, episode_key FROM sandbox_experiences WHERE character_id = ?",
        (character_id,),
    )


class TestPersistentEpisodeIdentity:
    async def test_cross_runtime_replay_keeps_one_persisted_episode(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §9: the run that knows the episode is the *database*, not memory."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(
                runtime,
                "9301",
                minutes=40.0,
                instance_id="act_persist",
                significance=InteractionSignificance.major,  # promotion-worthy
            )
            await runtime.flush_experiences()
            character_id = runtime.character_id
            rows = await episode_rows(db, character_id, "action:act_persist")
            assert len(rows) == 1
            original_id = rows[0]["id"]
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        try:
            # the very same verified fact arrives again after the restart
            await share(
                restored,
                "9301",
                minutes=40.0,
                instance_id="act_persist",
                significance=InteractionSignificance.major,
            )
            await restored.flush_experiences()
            rows = await episode_rows(db, character_id, "action:act_persist")
            assert len(rows) == 1  # §10.1 §2: never a second row for one act
            assert rows[0]["id"] == original_id  # the original episode owns it
            assert restored.duplicate_episodes_skipped == 1
            shared_memories = [
                row
                for row in await restored.memory.active_memories(limit=10)
                if str(row.get("provenance", {}).get("action_instance_id", "")) == "act_persist"
            ]
            assert len(shared_memories) == 1  # memory stays one too
        finally:
            await restored.shutdown()
            await db.close()

    async def test_the_same_episode_key_in_two_worlds_is_two_experiences(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §6/§10 E: the unique identity is (character_id, episode_key)."""
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            assert first.character_id != second.character_id
            await share(first, "9302", minutes=40.0, instance_id="act_shared_key")
            await share(second, "9302", minutes=40.0, instance_id="act_shared_key")
            await first.flush_experiences()
            await second.flush_experiences()
            assert len(await episode_rows(db, first.character_id, "action:act_shared_key")) == 1
            assert len(await episode_rows(db, second.character_id, "action:act_shared_key")) == 1
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_action_instance_beats_the_interaction_fact(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §3/§5: two facts about one instance are one episode."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(runtime, "9303", minutes=40.0, instance_id="act_win")
            await share(runtime, "9303", minutes=35.0, instance_id="act_win")  # new fact id
            await runtime.flush_experiences()
            rows = await episode_rows(db, runtime.character_id, "action:act_win")
            assert len(rows) == 1
            # in-memory aggregation still holds as well (§10.1 §8)
            assert len(shared_experiences(runtime)) == 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_interaction_fact_is_the_fallback_identity(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §10 C/D: without instance or promise the fact itself identifies."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        fact = shared_fact(runtime, "9304", minutes=40.0)
        fact.metadata.pop("action_instance_id")  # nothing better than the fact
        try:
            await runtime.apply_social_interaction(fact)
            await runtime.flush_experiences()
            key = f"interaction:{fact.interaction_id}"
            assert len(await episode_rows(db, runtime.character_id, key)) == 1
        finally:
            await runtime.shutdown()

        restored = await make_sandbox(db=db, clock=clock)
        try:
            replay = shared_fact(restored, "9304", minutes=40.0)
            replay.metadata.pop("action_instance_id")
            replay.interaction_id = fact.interaction_id  # the same verified fact
            await restored.apply_social_interaction(replay)
            await restored.flush_experiences()
            key = f"interaction:{fact.interaction_id}"
            assert len(await episode_rows(db, restored.character_id, key)) == 1
        finally:
            await restored.shutdown()
            await db.close()

    async def test_two_different_interactions_are_two_episodes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            for minutes in (40.0, 45.0):
                fact = shared_fact(runtime, "9305", minutes=minutes)
                fact.metadata.pop("action_instance_id")
                await runtime.apply_social_interaction(fact)
            await runtime.flush_experiences()
            rows = await episode_rows(db, runtime.character_id)
            shared_rows = [
                row for row in rows if str(row["episode_key"] or "").startswith("interaction:")
            ]
            assert len(shared_rows) == 2  # §25 E: two real sessions, two episodes
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_one_commitment_without_instances_stays_one_episode(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §21: the commitment key holds when no instance identifies the act."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            for _ in range(2):
                fact = shared_fact(runtime, "9306", minutes=40.0, commitment_id="cm_single")
                fact.metadata.pop("action_instance_id")
                await runtime.apply_social_interaction(fact)
            await runtime.flush_experiences()
            rows = await episode_rows(db, runtime.character_id, "commitment:cm_single")
            assert len(rows) == 1  # one promise, one fulfilment episode
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_one_commitment_two_instances_stay_two_episodes(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §21: a commitment must never merge two different ActionInstances."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await share(
                runtime, "9307", minutes=40.0, commitment_id="cm_many", instance_id="act_one"
            )
            await share(
                runtime, "9307", minutes=40.0, commitment_id="cm_many", instance_id="act_two"
            )
            await runtime.flush_experiences()
            rows = await episode_rows(db, runtime.character_id)
            keys = sorted(str(row["episode_key"]) for row in rows if row["episode_key"])
            assert keys == ["action:act_one", "action:act_two"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_historical_rows_without_an_episode_key_are_untouched(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10.1 §13: NULL keys coexist — old rows are never deduped or deleted."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            for index in (1, 2):
                await db.execute(
                    "INSERT INTO sandbox_experiences"
                    " (id, character_id, kind, summary, importance, location, actors,"
                    "  source_event_ids, causation_id, correlation_id, action_id,"
                    "  interaction_type, external_source, metadata, created_at, episode_key)"
                    " VALUES (?, ?, 'action_completed', ?, 0.5, '', '[]', '[]', '', '', '',"
                    "  '', '', '{}', 0, NULL)",
                    (f"exp_legacy_{index}", runtime.character_id, f"旧经历{index}"),
                )
            rows = await episode_rows(db, runtime.character_id)
            assert len([row for row in rows if row["episode_key"] is None]) == 2
        finally:
            await runtime.shutdown()
            await db.close()
