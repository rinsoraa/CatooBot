"""Phase 2 causality tests (§21): not just end states — the *why*.

Every chain test asserts the events that carried the change (type, source,
target, causation, correlation), then the observable state. The second
character (阿澈, no pet, coffee-anchored) proves the spine is name-free (§18).
"""

from __future__ import annotations

import time
from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.interactions import InteractionRequest
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"


async def make_runtime(bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    store = SandboxStore(None)
    config = SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7)
    runtime = SandboxRuntime(config, store, bible=bible, clock=clock or (lambda: time.time()))
    await runtime.start()
    return runtime


async def make_other_runtime(tmp_path=None):  # type: ignore[no-untyped-def]
    return await make_runtime(OTHER_BIBLE_PATH)


# ------------------------------------------------ Test 1: Pet Hungry Chain


class TestPetHungryChain:
    async def test_full_chain_hungry_approach_feed_fed(self) -> None:
        runtime = await make_runtime()
        try:
            assert runtime.pet is not None
            pet_id = runtime.pet.id
            food_key, food_item = runtime._pet_food or ("", "")
            before_food = runtime.inventories.get(food_key).count(food_item)
            hunger_before = runtime.pet.hunger

            # cross the hunger threshold → the world emits facts
            runtime.pet.hunger = 0.74
            await runtime.tick(minutes=10)

            hungry = runtime.events.last(ET.PET_HUNGRY)
            approached = runtime.events.last(ET.PET_APPROACHED)
            assert hungry is not None and approached is not None
            assert hungry.source_entity_id == pet_id
            # causation: the approach was caused by the hunger fact (§13)
            assert approached.causation_id == hungry.event_id
            assert approached.correlation_id == hungry.correlation_id
            # the reaction rode the bus: pet_care rose with a NEED_CHANGED
            assert runtime.needs.level("pet_care") > 0.1
            assert runtime.events.last(ET.NEED_CHANGED) is not None

            # feed via the interaction layer (Case A tail)
            result = await runtime.interactions.execute(
                InteractionRequest(target_id=pet_id, target_kind="pet", interaction_type="feed")
            )
            assert result.ok is True
            consumed = runtime.events.last(ET.ITEM_CONSUMED)
            fed = runtime.events.last(ET.PET_FED)
            assert consumed is not None and consumed.payload["item"] == food_item
            assert fed is not None and fed.target_entity_id == pet_id
            # state actually changed, traceable to the chain
            assert runtime.inventories.get(food_key).count(food_item) == before_food - 1
            assert runtime.pet.hunger < hunger_before
            # the whole behaviour replays under one correlation
            chain = runtime.events.chain(result.correlation_id)
            types = [e.event_type for e in chain]
            assert ET.INTERACTION_STARTED in types
            assert ET.ITEM_CONSUMED in types
            assert ET.PET_FED in types
            assert ET.INTERACTION_COMPLETED in types
            # PET_FED's causation points at the ITEM_CONSUMED inside the chain
            assert fed.causation_id == consumed.event_id
        finally:
            await runtime.shutdown()

    async def test_pet_hungry_fires_once_per_crossing(self) -> None:
        runtime = await make_runtime()
        try:
            runtime.pet.hunger = 0.74
            await runtime.tick(minutes=10)
            first = len(runtime.events.of_type(ET.PET_HUNGRY))
            assert first == 1
            await runtime.tick(minutes=10)  # still hungry: no duplicate fact
            assert len(runtime.events.of_type(ET.PET_HUNGRY)) == 1
        finally:
            await runtime.shutdown()


# ------------------------------------------- Test 2: Inventory Depletion


class TestInventoryDepletion:
    async def test_last_item_consumed_depletes_inventory(self) -> None:
        runtime = await make_runtime()
        try:
            fridge_key, drink = runtime._fridge_key, runtime._drink_item
            runtime.inventories.get(fridge_key).items = {drink: 1}
            result = runtime.take_item(fridge_key, drink, source="test", reason="case_b")
            assert result.ok is True
            assert runtime.inventories.get(fridge_key).count(drink) == 0
            depleted = runtime.events.last(ET.INVENTORY_DEPLETED)
            assert depleted is not None
            assert depleted.target_entity_id == f"inventory:{fridge_key}"
            consumed = runtime.events.last(ET.ITEM_CONSUMED)
            # depletion was caused by the consumption (§13)
            assert depleted.causation_id == consumed.event_id
        finally:
            await runtime.shutdown()

    async def test_taking_more_than_available_is_rejected(self) -> None:
        runtime = await make_runtime()
        try:
            result = runtime.take_item(
                runtime._fridge_key, runtime._drink_item, quantity=999, source="t", reason="t"
            )
            assert result.ok is False and result.error == "not_enough_items"
            assert runtime.events.last(ET.ITEM_CONSUMED) is None
        finally:
            await runtime.shutdown()


# ------------------------------------------------- Test 3: Movement Chain


class TestMovementChain:
    async def test_valid_move_emits_entity_moved(self) -> None:
        runtime = await make_runtime()
        try:
            livingroom = runtime.character.location
            target = next(
                s.id
                for s in runtime.spaces.all()
                if s.id != livingroom
                and s.id in {x.id for x in runtime.spaces.reachable(livingroom)}
            )
            result = runtime.move_entity(
                entity_id="character", to_space=target, source="test", reason="case_c"
            )
            assert result.ok is True
            assert runtime.character.location == target
            moved = runtime.events.last(ET.ENTITY_MOVED)
            assert moved is not None and moved.target_entity_id == target
            requested = runtime.events.last(ET.ENTITY_MOVE_REQUESTED)
            assert moved.causation_id == requested.event_id
        finally:
            await runtime.shutdown()

    async def test_invalid_space_never_mutates_state(self) -> None:
        runtime = await make_runtime()
        try:
            before = runtime.character.location
            result = runtime.move_entity(
                entity_id="character", to_space="sewer", source="test", reason="bad"
            )
            assert result.ok is False and result.error == "unknown_space"
            assert runtime.character.location == before  # no wrong state
            rejected = runtime.events.last(ET.ENTITY_MOVE_REJECTED)
            assert rejected is not None and rejected.payload["reason"] == "unknown_space"
        finally:
            await runtime.shutdown()


# ---------------------------------------------- Test 4: Action Lifecycle


class TestActionLifecycle:
    async def test_start_effect_complete_chain(self) -> None:
        runtime = await make_runtime()
        try:
            # drink_cola runs in the kitchen — travel there first (§9 gate)
            kitchen = runtime.spaces.get("kitchen")
            assert kitchen is not None
            runtime.move_entity(
                entity_id="character", to_space="kitchen", source="test", reason="setup"
            )
            instance = await runtime._start_action("drink_cola", reason=["test"])  # noqa: SLF001
            assert instance is not None
            started = runtime.events.last(ET.ACTION_STARTED)
            assert started is not None and started.target_entity_id == "drink_cola"
            correlation = f"act_{instance.id}"

            # force due → tick completes it through the effect spine
            runtime.current_action.planned_end_at = time.time() - 1
            report = await runtime.tick(minutes=10)
            assert report.get("completed") is True

            chain = runtime.events.chain(correlation)
            types = [e.event_type for e in chain]
            assert ET.ACTION_COMPLETED in types
            assert ET.ACTION_EFFECT_APPLIED in types
            consumed = [e for e in chain if e.event_type is ET.ITEM_CONSUMED]
            assert consumed and consumed[0].payload["item"] == runtime._drink_item
        finally:
            await runtime.shutdown()

    async def test_validator_rejection_emits_action_failed(self) -> None:
        runtime = await make_runtime()
        try:
            from app.sandbox.decision import ProposalValidator

            class RejectAll(ProposalValidator):
                def validate(self, action_id, space_id):  # type: ignore[no-untyped-def]
                    return False, "rule:test"

            original = runtime.validator
            runtime.validator = RejectAll(original.rules, original.actions)
            decision = await runtime._decide_and_apply(  # noqa: SLF001
                space_id=runtime.character.location
            )
            assert decision.reason_codes[:1] == ["validator_rejected"]
            failed = runtime.events.last(ET.ACTION_FAILED)
            assert failed is not None and failed.payload["reason"] == "rule:test"
            requested = runtime.events.last(ET.ACTION_REQUESTED)
            assert failed.causation_id == requested.event_id
            assert runtime.current_action is None
        finally:
            await runtime.shutdown()


# ---------------------------------------------- Test 5-7: Bus causality


class TestEventBusCausality:
    async def test_nested_events_keep_causation_chain(self) -> None:
        runtime = await make_runtime()
        try:
            bus = runtime.events
            seen: list[str] = []

            def on_a(event):  # type: ignore[no-untyped-def]
                if event.event_type is ET.WORLD_NOTE and "A" in event.payload.get("tag", ""):
                    seen.append(event.event_id)
                    bus.publish(
                        ET.WORLD_NOTE,
                        payload={"tag": "B"},
                        causation_id=event.event_id,
                        correlation_id=event.correlation_id,
                    )

            bus.subscribe(on_a)
            root = bus.publish(ET.WORLD_NOTE, payload={"tag": "A"})
            assert root is not None
            b_event = bus.last(ET.WORLD_NOTE)
            assert b_event is not None and b_event.payload.get("tag") == "B"
            assert b_event.causation_id == root.event_id
            assert b_event.correlation_id == root.correlation_id
            chain = bus.chain(root.correlation_id)
            assert [e.payload.get("tag") for e in chain] == ["A", "B"]
            assert seen
        finally:
            await runtime.shutdown()

    async def test_infinite_loop_is_bounded_not_fatal(self) -> None:
        runtime = await make_runtime()
        try:
            bus = runtime.events

            def echo(event):  # type: ignore[no-untyped-def]
                if event.payload.get("tag") == "ping":
                    bus.publish(ET.WORLD_NOTE, payload={"tag": "pong"})
                else:
                    bus.publish(ET.WORLD_NOTE, payload={"tag": "ping"})

            bus.subscribe(echo)
            root = bus.publish(ET.WORLD_NOTE, payload={"tag": "ping"})
            assert root is not None
            # bounded: the chain stopped at the limit, the world survived
            assert len(bus.chain(root.correlation_id)) <= bus._max_chain  # noqa: SLF001
            assert bus.dropped > 0
            assert runtime.phase.value == "running"
        finally:
            await runtime.shutdown()

    async def test_depth_guard_blocks_runaway_nesting(self) -> None:
        runtime = await make_runtime()
        try:
            bus = runtime.events

            def recurse(event):  # type: ignore[no-untyped-def]
                bus.publish(ET.WORLD_NOTE, payload={"depth": event.payload.get("depth", 0) + 1})

            bus.subscribe(recurse)
            bus._max_depth = 3  # noqa: SLF001 - shrink for a fast test
            bus.publish(ET.WORLD_NOTE, payload={"depth": 0})
            assert bus.dropped > 0
        finally:
            await runtime.shutdown()


# ------------------------------------------ Test 8: Multi-Sandbox isolation


class TestMultiSandboxIsolation:
    async def test_two_runtimes_never_share_events_or_state(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        first = await make_runtime()
        second = await make_other_runtime(tmp_path)
        try:
            first.events.publish(ET.WORLD_NOTE, payload={"tag": "from-first"})
            second.events.publish(ET.WORLD_NOTE, payload={"tag": "from-second"})
            first_tags = [e.payload.get("tag") for e in first.events.recent()]
            second_tags = [e.payload.get("tag") for e in second.events.recent()]
            assert "from-second" not in first_tags
            assert "from-first" not in second_tags
            # state isolation: different characters, different worlds
            assert first.character.name != second.character.name
            assert second.pet is None and first.pet is not None
            assert "fridge" not in [o.id for o in second.objects.all()]
        finally:
            await first.shutdown()
            await second.shutdown()


# ------------------------------------ Test 9: Second-character regression


class TestSecondCharacterRegression:
    async def test_spine_is_name_free_achei_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        runtime = await make_other_runtime(tmp_path)
        try:
            # inventory mutation works on *her* anchor (咖啡), not 可乐
            key = runtime._fridge_key
            drink = runtime._drink_item
            assert drink == "咖啡"
            before = runtime.inventories.get(key).count(drink)
            runtime.take_item(key, drink, source="test", reason="regression")
            assert runtime.inventories.get(key).count(drink) == before - 1

            # interaction candidates are kind-driven; feeding has no pet target
            result = await runtime.interactions.execute(
                InteractionRequest(
                    target_id="kettle", target_kind="object", interaction_type="take_item"
                )
            )
            assert result.ok is True
            feed = await runtime.interactions.execute(
                InteractionRequest(target_id="pet", target_kind="pet", interaction_type="feed")
            )
            assert feed.ok is False and feed.reason == "no_pet_food"

            # action lifecycle works without any Minecraft anywhere
            owned = set(runtime.actions.definitions)
            assert "play_minecraft" not in owned
            moved = runtime.move_entity(
                entity_id="character", to_space="workroom", source="test", reason="regression"
            )
            assert moved.ok is True

            # no first-character names leaked into this world's facts
            exported = runtime.seed.export_seed()
            import json

            blob = json.dumps(exported, ensure_ascii=False)
            for name in ("罐头", "小喵", "空凛", "可乐", "Minecraft"):
                assert name not in blob, f"second world leaked {name}"
        finally:
            await runtime.shutdown()


# ------------------------------------ Interaction candidate surface (§11)


class TestInteractionResolver:
    async def test_pet_candidates_reflect_requirements(self) -> None:
        runtime = await make_runtime()
        try:
            pet_id = runtime.pet.id  # type: ignore[union-attr]
            cands = runtime.interactions.candidates(pet_id, "pet")
            types = {c.interaction_type for c in cands}
            assert types == {"feed", "observe"}
            feed = next(c for c in cands if c.interaction_type == "feed")
            assert feed.requirements == []  # food available → no unmet requirement
            runtime.inventories.get(runtime._pet_food[0]).items.clear()  # noqa: SLF001
            empty = runtime.interactions.candidates(pet_id, "pet")
            feed_after = next(c for c in empty if c.interaction_type == "feed")
            assert "pet_food_available" in feed_after.requirements
        finally:
            await runtime.shutdown()

    async def test_inspect_object_reports_inventory(self) -> None:
        runtime = await make_runtime()
        try:
            cands = runtime.interactions.candidates("fridge", "object")
            types = {c.interaction_type for c in cands}
            assert {"inspect", "take_item"} <= types
            result = await runtime.interactions.execute(
                InteractionRequest(
                    target_id="fridge",
                    target_kind="object",
                    interaction_type="take_item",
                    payload={"item": "布丁"},
                )
            )
            assert result.ok is True
            assert runtime.inventories.get("fridge").count("布丁") == 2
        finally:
            await runtime.shutdown()
