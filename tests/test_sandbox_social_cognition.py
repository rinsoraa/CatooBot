"""Phase 11 tests (§43): social cognition & conversational continuity.

One contract: when a person shows up, the turn's cognition carries a *small,
deterministic, read-only* social situation — who they are, what the
relationship is, what is still owed, what we recently did together, and which
long-term shared memories relate — with the current world always on top.
"""

from __future__ import annotations

import json
from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.commitments import MIN_FULFILL_DURATION_MINUTES
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


async def promise(runtime, qq: str, hint: str, *, activity: str = "gaming"):  # type: ignore[no-untyped-def]
    await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person_of(runtime, qq),
            interaction_type="invitation_accepted",
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=InteractionSignificance.meaningful,
            external_ids={"qq": qq},
            metadata={"time_hint": hint, "target_activity": activity},
        )
    )
    return runtime.commitments.open()[-1]


async def shared_session(  # type: ignore[no-untyped-def]
    runtime,
    qq: str,
    *,
    minutes: float = 40.0,
    instance_id: str = "",
    activity: str = "gaming",
    significance: InteractionSignificance = InteractionSignificance.major,
):
    """A promoted (long-term) shared episode for this person."""
    metadata: dict[str, object] = {
        "target_activity": activity,
        "duration_minutes": minutes,
        "action_id": f"play_{activity}",
        "action_instance_id": instance_id or f"act_{qq}_{activity}",
    }
    await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person_of(runtime, qq),
            interaction_type="shared_activity",
            source="character_action",
            timestamp=runtime._clock(),  # noqa: SLF001
            outcome="completed",
            significance=significance,
            metadata=metadata,
        )
    )
    await runtime.flush_experiences()
    return metadata["action_instance_id"]


async def situation_of(runtime, qq: str, *, query: str = ""):  # type: ignore[no-untyped-def]
    context = await runtime.cognitive_context(query=query, relationship_target=qq)
    return context, context.as_prompt_payload()


# ------------------------------------------------------------------- §43 A/B


class TestPersonResolution:
    async def test_the_handle_resolves_to_the_person_everywhere(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§20: handle → person_id — and every layer agrees on that id."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9501", "今晚一起打游戏")
            await shared_session(runtime, "9501", instance_id="act_a")
            context, payload = await situation_of(runtime, "9501", query="gaming")
            person = person_of(runtime, "9501")

            assert payload["person"]["person_id"] == person
            assert payload["person"]["external_id"] == "9501"
            situation = payload["social_situation"]
            assert situation["person_id"] == person
            assert payload["relationships"][0]["person_id"] == person
            assert payload["commitments"][0]["commitment_id"]
            assert all(
                row["provenance"]["person_id"] == person
                for row in context.relevant_memories
                if row["provenance"].get("episode_key")
            )
            assert context.social_situation["person_id"] == person
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_unresolvable_handle_gets_no_social_context(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§21: never guess a person — degrade to empty, not to someone else."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9502", "今晚一起打游戏")
            await shared_session(runtime, "9502", instance_id="act_b")

            def broken_resolver(*_args, **_kwargs):  # type: ignore[no-untyped-def]
                raise RuntimeError("identity unavailable")

            runtime.persons.for_qq = broken_resolver  # type: ignore[method-assign]
            context, payload = await situation_of(runtime, "9502", query="gaming")
            assert payload["person"] == {}
            assert payload["social_situation"] == {}
            assert context.social_situation == {}
            assert payload["commitments"] == []
            assert payload["relationships"] == []
            assert context.retrieval["person_id"] == ""  # no boost, no guess
            assert all(row["person_match"] == 0.0 for row in context.relevant_memories)
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------------- §43 C/D


class TestSocialSituationComposition:
    async def test_the_situation_gathers_the_four_social_facts(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§5/§43 C: relationship + open promise + recent episode + shared memory."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            person = person_of(runtime, "9503")
            commitment = await promise(runtime, "9503", "今晚一起打游戏")
            instance = await shared_session(runtime, "9503", instance_id="act_c")
            _context, payload = await situation_of(runtime, "9503", query="gaming")
            situation = payload["social_situation"]

            assert situation["person_id"] == person
            assert situation["relationship"]["person_id"] == person
            assert situation["relationship"]["relation_type"] == "acquaintance"
            assert situation["open_commitments"][0]["commitment_id"] == (commitment.commitment_id)
            episodes = situation["recent_shared_experiences"]
            assert len(episodes) == 1
            assert episodes[0]["kind"] == "shared_activity"
            assert "gaming" in episodes[0]["summary"]
            memories = situation["relevant_shared_memories"]
            assert memories and memories[0]["activity"] == "gaming"
            assert memories[0]["text"]
            assert situation["continuity"] == {
                "has_open_commitments": True,
                "recent_shared_experience_count": 1,
                "relevant_shared_memory_count": len(memories),
            }
            # §7/§22: facts and counts only — no psychological conclusions
            assert "feeling" not in json.dumps(situation, ensure_ascii=False)
            assert instance == episodes[0]["episode_key"].removeprefix("action:")
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_memory_and_experience_keep_their_roles(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§9: a memory is a long-term reference; an experience is one episode."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await shared_session(runtime, "9504", instance_id="act_roles")
            _context, payload = await situation_of(runtime, "9504", query="gaming")
            situation = payload["social_situation"]
            episode = situation["recent_shared_experiences"][0]
            memory = situation["relevant_shared_memories"][0]
            assert episode["experience_id"].startswith("exp_")
            assert episode["at"] > 0  # the concrete episode happened at a time
            assert memory["memory_id"] > 0 and "gaming" in memory["text"]
            assert episode["episode_key"].startswith("action:")
            assert memory["episode_key"] == episode["episode_key"]  # §43 K: same act
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ §43 D/E/F/G


class TestRelevanceAndIsolation:
    async def test_the_person_being_talked_to_gets_the_boost(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await shared_session(runtime, "9505", instance_id="act_mine")
            await shared_session(runtime, "9506", instance_id="act_theirs")
            mine = person_of(runtime, "9505")
            context, payload = await situation_of(runtime, "9505", query="gaming")
            rows = context.relevant_memories
            by_person = {row["provenance"]["person_id"]: row for row in rows}
            assert by_person[mine]["person_match"] == 1.0
            assert rows[0]["provenance"]["person_id"] == mine
            # only *my* memories enter my situation (§24)
            assert all(
                row["text"]
                in {
                    "",
                    *[m["text"] for m in payload["social_situation"]["relevant_shared_memories"]],
                }
                for row in payload["social_situation"]["relevant_shared_memories"]
            )
            assert len(payload["social_situation"]["relevant_shared_memories"]) == 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_another_person_never_borrows_the_boost(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§35: a similar-sounding query does not give B A's social weight."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await shared_session(runtime, "9507", instance_id="act_a7", activity="movie")
            await shared_session(runtime, "9508", instance_id="act_b8", activity="movie")
            other = person_of(runtime, "9508")
            context, payload = await situation_of(runtime, "9508", query="movie")
            rows = context.relevant_memories
            assert rows
            assert all(
                row["person_match"] == float(row["provenance"]["person_id"] == other)
                for row in rows
            )
            assert len(payload["social_situation"]["relevant_shared_memories"]) == 1
            assert payload["social_situation"]["relevant_shared_memories"][0]["activity"] == "movie"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_characters_never_share_a_situation(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§34: character_id + person_id isolate the whole projection."""
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        second = await make_sandbox(db=db, clock=clock, bible_path=OTHER_BIBLE_PATH)
        try:
            qq = "9509"
            person = person_of(first, qq)
            await shared_session(first, qq, instance_id="act_world1")
            await shared_session(second, qq, instance_id="act_world2")
            _c1, payload_first = await situation_of(first, qq, query="gaming")
            _c2, payload_second = await situation_of(second, qq, query="gaming")
            assert payload_first["social_situation"]["person_id"] == person
            assert payload_second["social_situation"]["person_id"] == person
            first_episodes = payload_first["social_situation"]["recent_shared_experiences"]
            second_episodes = payload_second["social_situation"]["recent_shared_experiences"]
            assert first_episodes[0]["episode_key"] == "action:act_world1"
            assert second_episodes[0]["episode_key"] == "action:act_world2"
            first_ids = {row["memory_id"] for row in payload_first["memories"]}
            second_ids = {row["memory_id"] for row in payload_second["memories"]}
            assert first_ids.isdisjoint(second_ids)
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_the_current_world_stays_on_top(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§8/§36: the situation carries the past; the world is what is true."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await shared_session(runtime, "9510", instance_id="act_past")
            assert await runtime._start_action("sleep", duration_minutes=60) is not None  # noqa: SLF001
            sleeping = runtime.current_action
            context, payload = await situation_of(runtime, "9510", query="gaming")

            assert runtime.current_action is sleeping  # recall changed nothing
            assert runtime.current_action.definition_id == "sleep"
            definition = runtime.actions.definition(sleeping)
            assert context.current_action == (definition.name if definition else "sleep")
            assert "gaming" not in payload["world_line"]
            assert payload["social_situation"]["recent_shared_experiences"]
            assert (
                "gaming" in payload["social_situation"]["recent_shared_experiences"][0]["summary"]
            )
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ §43 H/I/J


class TestReadOnlyAndBounds:
    async def test_building_the_situation_changes_nothing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§17/§33/§37-§40: the projection is strictly read-only."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            person = person_of(runtime, "9511")
            await promise(runtime, "9511", "今晚一起打游戏")
            await shared_session(runtime, "9511", instance_id="act_ro")
            before = {
                "world": runtime.world_revision,
                "cognitive": runtime.cognitive_revision,
                "relationship": (await runtime.relationships_dyn.get(person)).model_dump(),  # type: ignore[union-attr]
                "commitments": [
                    (item.commitment_id, item.status.value, item.revision)
                    for item in runtime.commitments.all()
                ],
                "memories": await runtime.memory.count(),
                "experiences": len(runtime.experiences.emitted()),
                "mutations": len(runtime.mutations.recent(limit=200)),
                "action": runtime.current_action,
            }
            await runtime.cognitive_context(query="gaming", relationship_target="9511")
            await runtime.cognitive_context(query="gaming", relationship_target="9511")
            after = {
                "world": runtime.world_revision,
                "cognitive": runtime.cognitive_revision,
                "relationship": (await runtime.relationships_dyn.get(person)).model_dump(),  # type: ignore[union-attr]
                "commitments": [
                    (item.commitment_id, item.status.value, item.revision)
                    for item in runtime.commitments.all()
                ],
                "memories": await runtime.memory.count(),
                "experiences": len(runtime.experiences.emitted()),
                "mutations": len(runtime.mutations.recent(limit=200)),
                "action": runtime.current_action,
            }
            assert before == after
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_builds_are_identical(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§41/§43 I: same state + same query → same cognition."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await promise(runtime, "9512", "今晚一起打游戏")
            await shared_session(runtime, "9512", instance_id="act_det")
            first = (
                await runtime.cognitive_context(query="gaming", relationship_target="9512")
            ).as_prompt_payload()
            second = (
                await runtime.cognitive_context(query="gaming", relationship_target="9512")
            ).as_prompt_payload()
            assert first["social_situation"] == second["social_situation"]
            assert [row["memory_id"] for row in first["memories"]] == [
                row["memory_id"] for row in second["memories"]
            ]
            assert first["retrieval"] == second["retrieval"]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_situation_is_bounded(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§24: three promises, three episodes, memory_context_limit memories."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock, memory_context_limit=2)
        try:
            for index, hint in enumerate(
                (
                    "今晚7点一起打游戏",
                    "今晚8点一起打游戏",
                    "今晚9点一起打游戏",
                    "明天晚上一起打游戏",
                    "周一的晚上一起打游戏",
                )
            ):
                await promise(runtime, "9513", hint, activity=f"activity{index}")
            assert len(runtime.commitments.open()) == 5
            for index in range(4):
                await shared_session(
                    runtime,
                    "9513",
                    minutes=40.0,
                    instance_id=f"act_many{index}",
                    activity="gaming",
                )
            _context, payload = await situation_of(runtime, "9513", query="gaming")
            situation = payload["social_situation"]
            assert len(situation["open_commitments"]) == 3  # §24
            assert len(situation["recent_shared_experiences"]) == 3
            assert len(payload["memories"]) <= 2  # memory_context_limit
            assert len(situation["relevant_shared_memories"]) <= 2
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_old_shared_episodes_fall_out_of_the_recent_window(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§23: one source of truth for "recent" — the configured window."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock, social_context_recent_window_minutes=120.0)
        try:
            await shared_session(runtime, "9514", instance_id="act_old")
            clock.advance(5 * 3600.0)  # five hours later
            _context, payload = await situation_of(runtime, "9514", query="gaming")
            situation = payload["social_situation"]
            assert situation["recent_shared_experiences"] == []  # too old to be "recent"
            assert situation["continuity"]["recent_shared_experience_count"] == 0
            # the long-term memory is still there — it is a reference, not a window
            assert situation["relevant_shared_memories"]
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------------ §43 K/§10


class TestEpisodeLinkage:
    async def test_a_memory_points_back_at_its_episode(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10/§43 K: the situation links memory → experience by episode_key."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            await shared_session(runtime, "9515", instance_id="act_link")
            _context, payload = await situation_of(runtime, "9515", query="gaming")
            situation = payload["social_situation"]
            memory = situation["relevant_shared_memories"][0]
            episode = situation["recent_shared_experiences"][0]
            assert memory["episode_key"] == episode["episode_key"] == "action:act_link"

            row = await db.fetchone(
                "SELECT episode_key FROM sandbox_experiences WHERE character_id = ?"
                " AND episode_key = ?",
                (runtime.character_id, "action:act_link"),
            )
            assert row is not None and row["episode_key"] == memory["episode_key"]
        finally:
            await runtime.shutdown()
            await db.close()


class TestPromptSurface:
    def test_the_shared_line_is_rendered_without_duplicating_the_rest(self) -> None:
        """§26/§55: the chat prompt carries the shared episodes as facts."""
        from app.character.context import CharacterContextBuilder

        parts = CharacterContextBuilder._sandbox_blocks(  # noqa: SLF001
            {
                "world_line": "在家（睡觉）",
                "relationships": [],
                "commitments": [],
                "memories": [],
                "experiences": [],
                "social_situation": {
                    "person_id": "person_qq_x",
                    "recent_shared_experiences": [
                        {"summary": "和某人一起gaming了40分钟", "episode_key": "action:act_1"}
                    ],
                    "continuity": {"recent_shared_experience_count": 1},
                },
            }
        )
        text = "\n".join(parts[0])
        assert "一起做过的事" in text
        assert "gaming" in text
        layers = {name for name, *_rest in parts[1]}
        assert "shared_experience" in layers
        assert MIN_FULFILL_DURATION_MINUTES >= 1.0  # the threshold stays Phase 9's
