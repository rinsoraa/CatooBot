"""Phase 3 remediation tests: no bypass of the Mutation/Event spine.

Four audits drove this round:

1. ``_start_action`` must not fall back to a direct ``location =`` write when
   the canonical movement gate rejects a move — the action fails instead;
2. social-space state changes go through ``update_social_space`` (mutation +
   SOCIAL_SPACE_CHANGED), never through field assignment;
3. activities are ActionDefinition metadata from the seed — the runtime has no
   action-id → activity table and works for any world;
4. queue eviction respects urgency and is deterministic.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import (
    ExternalEventQueue,
    ExternalSource,
    ExternalUrgency,
    ExternalWorldEvent,
    InfluenceAction,
)
from app.sandbox.external_adapters import adapt_qq_message
from app.sandbox.models import ActionDefinition
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


async def make_runtime(*, bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(None),
        bible=bible,
        clock=clock or Clock(),
    )
    await runtime.start()
    return runtime


# ---------------------------------------------------------------- problem 1


class TestActionStartMovementFailureDoesNotBypassMutation:
    async def test_unreachable_target_fails_the_action_without_bypass(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            # shrink the world graph so the errand destination is unreachable
            social_space = next(iter(runtime.spaces.all())).id
            runtime.spaces.get(runtime._default_location)  # noqa: B018 - sanity
            for space in runtime.spaces.all():
                space.connects = [c for c in space.connects if c != social_space] or (
                    [social_space] if space.id == social_space else []
                )
            # rebuild adjacency exactly as SpaceSystem does on construction
            runtime.spaces = type(runtime.spaces)(runtime.spaces.all())

            where = runtime.character.location
            started = await runtime._start_action("go_shopping_cola")  # noqa: SLF001

            assert started is None  # no fake success
            assert runtime.current_action is None
            assert runtime.character.location == where  # never teleported
            assert runtime.events.last(ET.ACTION_STARTED) is None
            rejected = runtime.events.last(ET.ENTITY_MOVE_REJECTED)
            failed = runtime.events.last(ET.ACTION_FAILED)
            assert rejected is not None and rejected.payload["reason"] == "not_reachable"
            assert failed is not None
            assert failed.payload["reason"] == "move_rejected:not_reachable"
            # the refusal is auditable: a rejected entry sits in the log
            audit = runtime.mutations.recent()
            assert any(entry["reason"].startswith("move_rejected") for entry in audit)
            assert any(entry["before"] is None and entry["after"] is None for entry in audit)
        finally:
            await runtime.shutdown()

    async def test_reachable_start_still_works_and_is_recorded(self) -> None:
        runtime = await make_runtime()
        try:
            kitchen = runtime.spaces.get("kitchen")
            assert kitchen is not None
            started = await runtime._start_action("drink_cola")  # noqa: SLF001
            assert started is not None
            assert runtime.character.location == "kitchen"
            assert runtime.character.current_action_id == started.id
            assert runtime.events.last(ET.ACTION_STARTED) is not None
            assert runtime.events.last(ET.CHARACTER_FIELD_CHANGED) is not None
            audit = runtime.mutations.recent()
            assert any(entry["target"] == "character.location" for entry in audit)
            assert any(entry["target"] == "character.current_action_id" for entry in audit)
        finally:
            await runtime.shutdown()


# ---------------------------------------------------------------- problem 2


class TestExternalSocialEffectUsesMutationSpine:
    async def test_qq_event_updates_social_space_through_mutation(self) -> None:
        runtime = await make_runtime()
        try:
            space_id = (
                "minecraft_server"
                if "minecraft_server" in runtime.social_spaces
                else next(iter(runtime.social_spaces))
            )
            social = runtime.social_spaces[space_id]
            before_presence = social.character_presence
            before_temp = social.social_temperature

            event = adapt_qq_message(
                message_id="m-social-1",
                actor_id="u1",
                text="在吗",
                is_group=True,
                group_id="g1",
                is_core_actor=True,
                social_space_id=space_id,
            )
            await runtime.submit_external(event)
            await runtime.wakeup()

            assert social.character_presence == "active"
            assert social.character_presence != before_presence
            assert social.social_temperature > before_temp
            changed = runtime.events.last(ET.SOCIAL_SPACE_CHANGED)
            assert changed is not None and changed.target_entity_id == space_id
            entries = [
                e for e in runtime.mutations.recent() if e["target"] == f"social_space:{space_id}"
            ]
            fields = {e["field"] for e in entries}
            assert {"character_presence", "social_temperature"} <= fields
            assert all(e["before"] is not None and e["after"] is not None for e in entries)
        finally:
            await runtime.shutdown()

    def test_no_direct_social_assignments_outside_the_helper(self) -> None:
        """Source-level guard: presence/temperature are only written by the helper."""
        root = Path(__file__).resolve().parent.parent
        offenders: list[str] = []
        for path in (root / "app" / "sandbox").glob("*.py"):
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                stripped = line.strip()
                if stripped.startswith("#"):
                    continue
                for field in ("character_presence", "social_temperature"):
                    if (
                        f"{field} =" in stripped.replace("==", "")
                        and not stripped.startswith(f"{field}:")
                        and "self." in stripped
                        and "social." in stripped
                    ):
                        offenders.append(f"{path.name}:{number}")
        assert offenders == [], f"直接赋值绕过 update_social_space: {offenders}"
        # the plugin never touches sandbox internals
        plugin = (root / "plugins" / "chat" / "plugin.py").read_text(encoding="utf-8")
        assert "social_spaces[" not in plugin
        assert ".character_presence" not in plugin


# ---------------------------------------------------------------- problem 3


class TestActivityMetadataIsSeedDriven:
    def test_seed_carries_activity_metadata(self) -> None:
        bible = BibleCompiler(FIXTURE_BIBLE).compile()
        activities = {action.get("activity", "") for action in _seed_actions(bible)}
        assert "gaming" in activities and "reading" in activities
        # every owned action declares an activity (no id guessing downstream)
        assert all(action.get("activity") for action in _seed_actions(bible))

    def test_no_action_id_to_activity_table_in_runtime(self) -> None:
        runtime_source = (
            Path(__file__).resolve().parent.parent / "app" / "sandbox" / "runtime.py"
        ).read_text(encoding="utf-8")
        for forbidden in ("ACTIVITY_IDS", "play_minecraft", "drink_cola", "watch_animation"):
            assert forbidden not in runtime_source, f"runtime 仍含 {forbidden!r}"

    async def test_a_third_activity_works_without_core_changes(self) -> None:
        """World C declares ``crafting`` — the runtime needs no new code path."""
        runtime = await make_runtime()
        try:
            await runtime._start_action("idle", duration_minutes=60)  # noqa: SLF001
            template = runtime.actions.definitions["idle"]
            runtime.actions.definitions["whittle"] = ActionDefinition(
                **{
                    **template.model_dump(),
                    "id": "whittle",
                    "name": "削木头",
                    "activity": "crafting",
                    "spaces": ["*"],
                    "required_objects": [],
                    "consumes": {},
                }
            )
            assert "crafting" in runtime._activity_index()  # noqa: SLF001
            assert runtime._action_for_activity("crafting") == "whittle"  # noqa: SLF001
            event = ExternalWorldEvent(
                event_id="craft-1",
                source=ExternalSource.qq,
                actor_id="u1",
                content="一起来削木头？",
                urgency=ExternalUrgency.high,
                semantic_kind="activity_invitation",
                target_activity="crafting",
                actor_relationship="core_friend",
            )
            await runtime.submit_external(event)
            results = await runtime.wakeup()
            assert results[0]["influence"] in (
                InfluenceAction.INTERRUPT.value,
                InfluenceAction.WAKE.value,
            )
            assert results[0].get("action") == "whittle"
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "whittle"
        finally:
            await runtime.shutdown()

    async def test_second_world_activities_are_its_own(self) -> None:
        runtime = await make_runtime(bible_path=OTHER_BIBLE_PATH)
        try:
            activities = runtime._activity_index()  # noqa: SLF001
            assert "gaming" not in activities  # 阿澈 owns no gaming action
            assert activities  # but the world still has some
            # its own activity vocabulary resolves locally
            first = runtime._action_for_activity(sorted(activities)[0])  # noqa: SLF001
            assert first in runtime.actions.definitions
        finally:
            await runtime.shutdown()


def _seed_actions(bible):  # type: ignore[no-untyped-def]
    from app.sandbox.definition import CharacterDefinition
    from app.sandbox.world_seed import build_world_seed

    seed = build_world_seed(CharacterDefinition.from_bible(bible), bible, simulation_seed=7)
    return seed.actions


# ---------------------------------------------------------------- problem 4


def _event(event_id: str, urgency: ExternalUrgency) -> ExternalWorldEvent:
    return ExternalWorldEvent(
        event_id=event_id,
        source=ExternalSource.other,
        actor_id="t",
        content=event_id,
        urgency=urgency,
    )


class TestQueueEvictionRespectsUrgency:
    def test_critical_survives_queue_pressure(self) -> None:
        queue = ExternalEventQueue(maxlen=3)
        assert queue.push(_event("c1", ExternalUrgency.critical)) is True
        for i in range(2):
            assert queue.push(_event(f"n{i}", ExternalUrgency.normal)) is True
        # a normal arrival cannot evict the critical one
        assert queue.push(_event("n2", ExternalUrgency.normal)) is False
        assert any(e.event_id == "c1" for e in queue.pending())
        assert queue.rejected == 1

    def test_high_urgency_replaces_low_urgency(self) -> None:
        queue = ExternalEventQueue(maxlen=2)
        assert queue.push(_event("low1", ExternalUrgency.low)) is True
        assert queue.push(_event("norm1", ExternalUrgency.normal)) is True
        assert queue.push(_event("high1", ExternalUrgency.high)) is True
        ids = [e.event_id for e in queue.pending()]
        assert "low1" not in ids and "high1" in ids and "norm1" in ids
        assert queue.dropped == 1
        assert "low1" in queue.evicted
        # evicted ≠ consumed: the evicted id may be submitted again later
        assert queue.seen("low1") is False

    def test_low_urgency_rejected_when_queue_is_full_of_higher_priority(self) -> None:
        queue = ExternalEventQueue(maxlen=2)
        queue.push(_event("n1", ExternalUrgency.normal))
        queue.push(_event("c1", ExternalUrgency.critical))
        before = [e.event_id for e in queue.pending()]
        assert queue.push(_event("low1", ExternalUrgency.low)) is False
        assert [e.event_id for e in queue.pending()] == before
        assert queue.rejected == 1 and queue.dropped == 0

    def test_queue_eviction_is_deterministic(self) -> None:
        def scenario() -> tuple[list[str], int, int]:
            queue = ExternalEventQueue(maxlen=3)
            for event in (
                _event("a-normal", ExternalUrgency.normal),
                _event("b-low", ExternalUrgency.low),
                _event("c-normal", ExternalUrgency.normal),
                _event("d-high", ExternalUrgency.high),  # evicts b-low
                _event("e-low", ExternalUrgency.low),  # weaker than all → refused
            ):
                queue.push(event)
            return [e.event_id for e in queue.pending()], queue.dropped, queue.rejected

        first = scenario()
        second = scenario()
        assert first == second
        ids, dropped, rejected = first
        assert ids == ["a-normal", "c-normal", "d-high"]  # FIFO kept within urgency
        assert dropped == 1 and rejected == 1

    def test_high_urgency_batch_pops_before_normal(self) -> None:
        queue = ExternalEventQueue(maxlen=8)
        queue.push(_event("n1", ExternalUrgency.normal))
        queue.push(_event("h1", ExternalUrgency.high))
        queue.push(_event("n2", ExternalUrgency.normal))
        assert [queue.pop().event_id for _ in range(3)] == ["h1", "n1", "n2"]  # type: ignore[union-attr]
