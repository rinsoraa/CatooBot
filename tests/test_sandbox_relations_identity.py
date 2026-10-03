"""Phase 8.1 tests (§6/§10/§13-§23): identity persistence, core-friend mapping,
and the cognitive revision line.

Three contracts:

* ``external_ids`` stores *platform handles*, never the person id, and legacy
  self-mappings are read as "no mapping" instead of being mistaken for QQ;
* several core friends map one-to-one — ambiguity warns instead of binding
  everyone to the first bible friend;
* a relationship change moves a **cognitive** revision (not the world's), and a
  proposal older than either revision is rejected without executing.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from types import SimpleNamespace

from app.config.settings import CoreFriendRelationshipConfig, SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.relations import (
    InteractionSignificance,
    SocialInteractionFact,
    decode_external_ids,
    person_id_for,
)
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

TWO_FRIENDS_BIBLE = Path(__file__).resolve().parent / "fixtures" / "character_two_friends.md"


async def make_sandbox(*, db, clock, bible_path=None, engine=None, **cfg):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7, **cfg),
        SandboxStore(db),
        bible=bible,
        clock=clock,
        ai_engine=engine,
    )
    await runtime.start()
    return runtime


async def fact(runtime, person_id: str, interaction_type: str, *, external_ids=None, **kw):  # type: ignore[no-untyped-def]
    return await runtime.apply_social_interaction(
        SocialInteractionFact.create(
            character_id=runtime.character_id,
            person_id=person_id,
            interaction_type=interaction_type,
            source="test",
            timestamp=runtime._clock(),  # noqa: SLF001
            significance=kw.pop("significance", InteractionSignificance.normal),
            external_ids=external_ids or {},
            **kw,
        )
    )


# --------------------------------------------------------- external_ids (§1-§6)


class TestPersonExternalIdPersistence:
    async def test_qq_handle_is_stored_as_a_platform_mapping(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="123456")
            await fact(runtime, person, "message_received", external_ids={"qq": "123456"})
            row = await runtime.relationships_dyn.person_row(person)
            assert row is not None
            assert row["external_ids"]["qq"] == "123456"  # §3: the real handle
            assert "person_id" not in row["external_ids"]  # §4: never a self-map
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_the_real_qq_path_persists_the_handle(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """The production entry (adapter → influence → fact → store), not a mock."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            await runtime.submit_external(
                adapt_qq_message(
                    message_id="p81-map-1",
                    actor_id="234567",
                    text="周末要不要一起联机打游戏",
                    is_core_actor=False,
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()
            row = await runtime.relationships_dyn.person_row(
                person_id_for(kind="qq", value="234567")
            )
            assert row is not None and row["external_ids"] == {"qq": "234567"}
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_legacy_self_mapping_is_not_an_identity(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§5: old Phase 8 rows are readable but never decoded as a QQ handle."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            person = person_id_for(kind="qq", value="345678")
            await db.execute(
                "INSERT INTO sandbox_persons"
                " (person_id, display_name, external_ids, source, updated_at)"
                " VALUES (?, '', ?, 'runtime', 0)",
                (person, json.dumps({"person_id": person})),
            )
            row = await runtime.relationships_dyn.person_row(person)
            assert row is not None and row["external_ids"] == {}
            assert decode_external_ids(json.dumps({"person_id": person})) == {}

            # the next real platform identity replaces the fake one
            await fact(runtime, person, "message_received", external_ids={"qq": "345678"})
            row = await runtime.relationships_dyn.person_row(person)
            assert row is not None and row["external_ids"] == {"qq": "345678"}
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------- core-friend map (§7-§11)


class TestMultipleCoreFriendIdentityMapping:
    async def test_two_core_friends_map_one_to_one(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(
            db=db,
            clock=Clock(),
            bible_path=TWO_FRIENDS_BIBLE,
            core_friend_identities={"123": "小凛", "234": "阿澈"},
        )
        try:
            # the bible declares both — it is taken at its word (§7)
            assert runtime.seed.core_friend_names == ["小凛", "阿澈"]
            first = runtime.persons.for_qq("123")
            second = runtime.persons.for_qq("234")
            assert first.person_id != second.person_id  # §8: never one person
            assert first.external_ids == {"character_bible": "小凛", "qq": "123"}
            assert second.external_ids == {"character_bible": "阿澈", "qq": "234"}

            # and their relationships stay separate states (§10)
            await fact(runtime, first.person_id, "shared_activity")
            state_a = await runtime.relationships_dyn.get(first.person_id)
            state_b = await runtime.relationships_dyn.get(second.person_id)
            assert state_a is not None and state_b is not None
            assert state_a.closeness != state_b.closeness
            assert state_b.interaction_count == 0
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_legacy_single_id_still_reaches_the_core_friend(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§11: the old one-entry list keeps working while it is unambiguous."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock(), core_friend_ids=["123456"])
        try:
            name = runtime.seed.core_friend_names[0]
            seeded = runtime.relationships_dyn.initial_for_name(name)
            assert seeded is not None
            identity = runtime.persons.for_qq("123456")
            assert identity.person_id == seeded.person_id  # the bible person
            assert identity.external_ids == {"character_bible": name, "qq": "123456"}
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_ambiguous_legacy_list_never_binds_anyone(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Sandbox"):
            runtime = await make_sandbox(
                db=db,
                clock=Clock(),
                bible_path=TWO_FRIENDS_BIBLE,
                core_friend_ids=["123", "234"],  # several ids, no names: unusable
            )
            try:
                first = runtime.persons.for_qq("123")
                second = runtime.persons.for_qq("234")
            finally:
                await runtime.shutdown()
                await db.close()
        assert first.person_id != second.person_id
        assert first.person_id.startswith("person_qq_")  # plain identity, no guessing
        assert "cannot express" in caplog.text

    async def test_single_id_with_two_core_friends_stays_unbound(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        """One id, two bible core friends: which one? Do not guess (§9)."""
        db = await make_db(tmp_path)
        with caplog.at_level(logging.WARNING, logger="CatooBot.Sandbox"):
            runtime = await make_sandbox(
                db=db,
                clock=Clock(),
                bible_path=TWO_FRIENDS_BIBLE,
                core_friend_ids=["123"],
            )
            try:
                identity = runtime.persons.for_qq("123")
            finally:
                await runtime.shutdown()
                await db.close()
        assert identity.person_id.startswith("person_qq_")
        assert "cannot express" in caplog.text


# ---------------------------------------------------- cognitive revision (§12-§25)


class TestCognitiveRevision:
    async def test_relationship_change_bumps_cognitive_not_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            world_before, cognitive_before = runtime.world_revision, runtime.cognitive_revision
            await fact(runtime, person_id_for(kind="qq", value="7016"), "shared_activity")
            assert runtime.world_revision == world_before  # §14: not a world change
            assert runtime.cognitive_revision == cognitive_before + 1
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_revisions_are_independent(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            world = runtime.world_revision
            cognitive = runtime.cognitive_revision

            # A: physical world only
            runtime.adjust_need("hunger", delta=0.01, source="test", reason="world_touch")
            assert (runtime.world_revision, runtime.cognitive_revision) == (
                world + 1,
                cognitive,
            )
            world, cognitive = runtime.world_revision, runtime.cognitive_revision

            # B: relationship only
            await fact(runtime, person_id_for(kind="qq", value="7017"), "shared_activity")
            assert (runtime.world_revision, runtime.cognitive_revision) == (
                world,
                cognitive + 1,
            )
            world, cognitive = runtime.world_revision, runtime.cognitive_revision

            # C: both, each on its own line
            runtime.adjust_need("thirst", delta=0.01, source="test", reason="world_touch")
            await fact(runtime, person_id_for(kind="qq", value="7018"), "message_received")
            assert (runtime.world_revision, runtime.cognitive_revision) == (
                world + 1,
                cognitive + 1,
            )
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_stale_proposal_is_rejected_without_executing(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§22: a relationship change while the model thinks makes it stale."""
        db = await make_db(tmp_path)
        clock = Clock()
        engine = _ChattyEngine()
        runtime = await make_sandbox(db=db, clock=clock, engine=engine)
        engine.runtime = runtime
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            result = await runtime._run_invitation_decision(  # noqa: SLF001
                activity="gaming",
                from_core=True,
                actor="7019",
                reason_code="test",
                received_id="",
                correlation="p81-stale",
            )
            rejected = runtime.events.last(ET.DECISION_REJECTED)
            assert rejected is not None and rejected.payload["reason"] == "cognitive_changed"
            assert result["decision"]["accepted"] is False
            # nothing from the stale request executed
            assert runtime.current_action.definition_id == "watch_animation"  # type: ignore[union-attr]
            assert runtime.events.last(ET.ACTION_REQUESTED) is None
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_invitation_fact_lands_before_its_decision_request(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§19A: the same event's fact is settled before the decision reads it."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        seen: dict[str, float] = {}
        original = runtime.decisions.decide

        async def spy(**kwargs):  # type: ignore[no-untyped-def]
            state = await runtime.relationship_for("7021")
            seen["familiarity"] = state.familiarity if state is not None else 0.0
            return await original(**kwargs)

        runtime.decisions.decide = spy  # type: ignore[method-assign]
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(5 * 60)
            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="p81-inv-order",
                    source=ExternalSource.qq,
                    actor_id="7021",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            await runtime.wait_social()
        finally:
            runtime.decisions.decide = original  # type: ignore[method-assign]

        assert runtime.events.last(ET.DECISION_REQUESTED) is not None
        assert seen["familiarity"] > 0.1  # the invite was counted *before* deciding
        state = await runtime.relationship_for("7021")
        assert state is not None and seen["familiarity"] <= state.familiarity
        await runtime.shutdown()
        await db.close()


class TestRelationshipPersistenceFailure:
    async def test_failed_write_is_reported_not_hidden(self, tmp_path, caplog) -> None:  # type: ignore[no-untyped-def]
        """§27: the world continues on the in-memory truth, and says so."""
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        person = person_id_for(kind="qq", value="7022")

        async def broken(_state):  # type: ignore[no-untyped-def]
            return False

        runtime.relationships_dyn.save = broken  # type: ignore[method-assign]
        with caplog.at_level(logging.WARNING, logger="CatooBot.Sandbox"):
            await fact(runtime, person, "shared_activity")
        try:
            changed = runtime.events.last(ET.RELATIONSHIP_CHANGED)
            assert changed is not None and changed.payload["persisted"] is False
            assert runtime.social.persist_failures == 1
            assert "could not be persisted" in caplog.text
            state = await runtime.relationships_dyn.get(person)  # in-memory truth kept
            assert state is not None and state.closeness > 0.1
        finally:
            await runtime.shutdown()
            await db.close()


class _ChattyEngine:
    """A model that answers while a relationship fact lands underneath it."""

    enabled = True

    def __init__(self) -> None:
        self.runtime = None
        self.payload = '{"candidate_id": "action:play_minecraft", "confidence": 0.9}'

    async def chat(self, _request):  # type: ignore[no-untyped-def]
        if self.runtime is not None:  # someone else's message lands mid-thought
            await fact(self.runtime, person_id_for(kind="qq", value="7020"), "shared_activity")
        return SimpleNamespace(content=self.payload)


class TestCoreFriendRelationshipValues:
    """core 好友的初始关系数值可配置；缺省时与档案的 core 档完全一致（回归护栏）。"""

    async def test_max_values_apply_and_the_qq_mapping_resolves(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(
            db=db,
            clock=Clock(),
            core_friend_identities={"2731431246": "空凛"},
            core_friend_relationship=CoreFriendRelationshipConfig(
                trust=1.0, familiarity=1.0, closeness=1.0, social_comfort=1.0
            ),
        )
        try:
            state = runtime.relationships_dyn.initial_for_name("空凛")
            assert state is not None
            assert (
                state.trust,
                state.familiarity,
                state.closeness,
                state.social_comfort,
            ) == (1.0, 1.0, 1.0, 1.0)
            # 映射命中：这个 QQ 就是档案里的空凛（而不是陌生人 person_qq_*）
            identity = runtime.persons.for_qq("2731431246")
            assert identity.person_id == state.person_id
            assert identity.person_id.startswith("person_bible_")
            assert identity.display_name == "空凛"
            # 没被映射的 QQ 仍然是陌生人
            assert runtime.persons.for_qq("999999999").person_id.startswith("person_qq_")
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_defaults_stay_at_the_bible_core_tier(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        runtime = await make_sandbox(db=db, clock=Clock())
        try:
            state = runtime.relationships_dyn.initial_for_name("空凛")
            assert state is not None
            assert (
                state.trust,
                state.familiarity,
                state.closeness,
                state.social_comfort,
            ) == (0.7, 0.5, 0.6, 0.8)
        finally:
            await runtime.shutdown()
            await db.close()

    def test_only_core_entries_are_overridden_and_values_are_clamped(self) -> None:
        from app.sandbox.relations import initial_states

        definition = SimpleNamespace(
            relationships=[
                SimpleNamespace(name="核心", type="core_friend", core=True),
                SimpleNamespace(name="路人", type="acquaintance", core=False),
            ]
        )
        overrides = SimpleNamespace(trust=2.0, familiarity=None, closeness=0.9, social_comfort=None)
        states = {
            state.metadata["name"]: state
            for state in initial_states(
                definition, character_id="c", clock=lambda: 0.0, core_relationship=overrides
            )
        }
        core = states["核心"]
        assert core.trust == 1.0  # 2.0 被夹到上限
        assert core.familiarity == 0.5  # 未配置 → 档案 core 档
        assert core.closeness == 0.9
        assert core.social_comfort == 0.8
        stranger = states["路人"]
        assert (
            stranger.trust,
            stranger.familiarity,
            stranger.closeness,
            stranger.social_comfort,
        ) == (
            0.3,
            0.1,
            0.1,
            0.3,
        )
