"""Phase 3 tests (§21): external world → influence → wakeup → action → mutation.

The boundary under test: an outside fact may *reach* the sandbox, but nothing
outside may write sandbox state — every effect lands on the Phase 2 spine
(Interaction/Action → Mutation → SandboxEvent). Protocol objects (OneBot /
NapCat / QQ message models) never appear here (§5/§21-7).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.config.settings import DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.external import (
    ExternalEventQueue,
    ExternalInfluenceEvaluator,
    ExternalSource,
    ExternalUrgency,
    ExternalWorldEvent,
    InfluenceAction,
)
from app.sandbox.external_adapters import (
    adapt_delivery,
    adapt_qq_message,
    classify_semantics,
)
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


async def make_runtime(*, bible_path=None, clock=None, database=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    store = SandboxStore(database) if database is not None else SandboxStore(None)
    config = SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7)
    runtime = SandboxRuntime(config, store, bible=bible, clock=clock or Clock())
    await runtime.start()
    return runtime


async def make_other_runtime(*, clock=None):  # type: ignore[no-untyped-def]
    return await make_runtime(bible_path=OTHER_BIBLE_PATH, clock=clock)


def invitation(actor_id: str = "u-core", *, is_core: bool = True) -> ExternalWorldEvent:
    return adapt_qq_message(
        message_id="m-invite-1",
        actor_id=actor_id,
        text="来一起联机",
        is_core_actor=is_core,
    )


def smalltalk(actor_id: str = "u-guest") -> ExternalWorldEvent:
    return adapt_qq_message(
        message_id="m-small-1", actor_id=actor_id, text="你吃饭了吗", is_core_actor=False
    )


# ------------------------------------------------- Test 1: no-effect message


class TestNoEffectMessage:
    async def test_ordinary_message_never_interrupts(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await runtime._start_action("watch_animation")  # noqa: SLF001
            before = runtime.current_action.definition_id  # type: ignore[union-attr]

            assert await runtime.submit_external(smalltalk()) is True
            results = await runtime.wakeup()

            assert len(results) == 1
            assert results[0]["influence"] == InfluenceAction.OBSERVE.value
            assert results[0]["interrupt"] is False
            assert runtime.current_action.definition_id == before  # type: ignore[union-attr]
            assert runtime.events.last(ET.ACTION_INTERRUPTED) is None
            # the fact itself was accepted and recorded
            received = runtime.events.last(ET.EXTERNAL_EVENT_RECEIVED)
            assert received is not None and received.source_entity_id == "qq"
        finally:
            await runtime.shutdown()

    async def test_irrelevant_low_urgency_event_is_no_effect(self) -> None:
        runtime = await make_runtime()
        try:
            event = ExternalWorldEvent(
                event_id="n1",
                source=ExternalSource.other,
                actor_id="broadcast",
                content="系统广播",
                urgency=ExternalUrgency.low,
            )
            await runtime.submit_external(event)
            results = await runtime.wakeup()
            assert results[0]["influence"] == InfluenceAction.NO_EFFECT.value
        finally:
            await runtime.shutdown()


# --------------------------------------------- Test 2: game invitation chain


class TestGameInvitationInterrupt:
    async def test_invitation_interrupts_and_starts_gaming(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await runtime._start_action("watch_animation")  # noqa: SLF001
            clock.advance(30 * 60)  # 30 minutes in, mid-action
            watched = runtime.current_action
            assert watched is not None
            expected_remaining = (watched.planned_end_at - clock.now) / 60.0

            await runtime.submit_external(invitation())
            results = await runtime.wakeup()

            assert results[0]["interrupt"] is True
            assert results[0]["meaning"] == "invitation_game"
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id == "play_minecraft"

            recent = runtime.events.recent()
            start_at = max(
                i for i, e in enumerate(recent) if e.event_type is ET.EXTERNAL_EVENT_RECEIVED
            )
            chain = [
                e.event_type
                for e in recent[start_at:]
                if e.event_type
                in (
                    ET.EXTERNAL_EVENT_RECEIVED,
                    ET.ACTION_INTERRUPTED,
                    ET.ACTION_STARTED,
                    ET.WORLD_EXTERNAL_INFLUENCE,
                )
            ]
            assert chain == [
                ET.EXTERNAL_EVENT_RECEIVED,
                ET.ACTION_INTERRUPTED,
                ET.ACTION_STARTED,
                ET.WORLD_EXTERNAL_INFLUENCE,
            ]
            # the influence fact chains to the received fact (§10)
            influence = runtime.events.last(ET.WORLD_EXTERNAL_INFLUENCE)
            received = runtime.events.last(ET.EXTERNAL_EVENT_RECEIVED)
            assert influence is not None and received is not None
            assert influence.causation_id == received.event_id
            # §14: the paused action is remembered with its remaining time
            assert runtime._interrupted is not None  # noqa: SLF001
            assert runtime._interrupted.definition_id == "watch_animation"  # noqa: SLF001
            assert runtime._interrupted.remaining_minutes == pytest.approx(  # noqa: SLF001
                expected_remaining, abs=0.1
            )
        finally:
            await runtime.shutdown()


# -------------------------------------------------------- Test 3: resume


class TestResume:
    async def test_interrupted_action_resumes_after_the_new_one(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(30 * 60)
            context = None
            await runtime.submit_external(invitation())
            await runtime.wakeup()
            context = runtime._interrupted  # noqa: SLF001
            assert context is not None and context.definition_id == "watch_animation"

            # finish the gaming session
            assert runtime.current_action is not None
            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            report = await runtime.tick(minutes=1)
            assert report.get("completed") is True

            assert runtime.current_action is not None
            resumed = runtime.current_action
            assert resumed.definition_id == "watch_animation"
            total_minutes = (resumed.planned_end_at - resumed.started_at) / 60.0
            assert total_minutes == pytest.approx(context.remaining_minutes, abs=0.5)
            assert runtime.events.last(ET.ACTION_RESUMED) is not None
            assert runtime._interrupted is None  # noqa: SLF001
        finally:
            await runtime.shutdown()

    async def test_resume_is_skipped_when_a_critical_need_outranks_it(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await runtime._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            clock.advance(10 * 60)
            await runtime.submit_external(invitation())
            await runtime.wakeup()
            runtime.needs.get("hunger").level = 1.0  # type: ignore[union-attr]
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime.tick(minutes=1)
            # a critical need wins: no resume of the paused entertainment
            assert runtime.events.last(ET.ACTION_RESUMED) is None
        finally:
            await runtime.shutdown()


# -------------------------------------------- Test 4/19: idempotency


class TestIdempotency:
    async def test_duplicate_event_id_is_processed_once(self) -> None:
        runtime = await make_runtime()
        try:
            event = invitation()
            assert await runtime.submit_external(event) is True
            assert await runtime.submit_external(event) is False  # duplicate refused
            await runtime.wakeup()
            await runtime.submit_external(event)
            assert len(runtime.external_queue) == 0
            received = runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            assert len(received) == 1
        finally:
            await runtime.shutdown()

    async def test_legacy_bridge_is_idempotent_too(self) -> None:
        from app.sandbox.models import EventPriority, ExternalEvent

        runtime = await make_runtime()
        try:
            legacy = ExternalEvent(
                id="dup-1",
                kind="user_message",
                priority=EventPriority.normal,
                user_id="u1",
                data={"text": "你好", "is_core_friend": False},
            )
            await runtime.notify(legacy)
            assert runtime.external_queue.seen("dup-1")  # legacy ids keep their namespace
            await runtime.notify(legacy)  # same id → queue refuses, no reprocess
            assert len(runtime.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)) == 1
        finally:
            await runtime.shutdown()

    async def test_queue_dedupe_and_priority_ordering(self) -> None:
        queue = ExternalEventQueue()
        normal = smalltalk()
        high = adapt_delivery(event_id="d-1", urgency=ExternalUrgency.high)
        assert queue.push(normal) is True
        assert queue.push(high) is True
        assert queue.push(normal) is False  # same id again
        first = queue.pop()
        assert first is not None and first.event_id == "d-1"  # high urgency first
        assert queue.pop() is not None and len(queue) == 0


# ------------------------------------------------------- Test 5: priority


class TestPriority:
    async def test_high_urgency_processes_before_normal_in_one_wakeup(self) -> None:
        runtime = await make_runtime()
        try:
            await runtime.submit_external(smalltalk())
            await runtime.submit_external(
                adapt_delivery(event_id="d-2", urgency=ExternalUrgency.high)
            )
            results = await runtime.wakeup()
            assert [r["event_id"] for r in results] == ["d-2", "qq_m-small-1"]
            # delivery did its generic work through the mutation spine
            package_id = runtime._delivery_object_id()  # noqa: SLF001
            if package_id:
                assert runtime.objects.get(package_id).state.get("present") is True  # type: ignore[union-attr]
                changed = runtime.events.of_type(ET.OBJECT_STATE_CHANGED)
                assert changed, "delivery must emit OBJECT_STATE_CHANGED"
        finally:
            await runtime.shutdown()


# ------------------------------------------ Test 6: multi-sandbox isolation


class TestMultiSandboxIsolation:
    async def test_external_event_never_crosses_sandboxes(self) -> None:
        first = await make_runtime()
        second = await make_other_runtime()
        try:
            await first.submit_external(invitation())
            assert len(first.external_queue) == 1
            assert len(second.external_queue) == 0
            await first.wakeup()
            assert len(second.external_queue) == 0
            assert not second.events.of_type(ET.EXTERNAL_EVENT_RECEIVED)
            assert second.events.last(ET.ACTION_INTERRUPTED) is None
        finally:
            await first.shutdown()
            await second.shutdown()


# ------------------------------------------- Test 7: protocol isolation (§5)


class TestProtocolIsolation:
    def test_sandbox_external_modules_never_import_protocols(self) -> None:
        """Docstrings may *mention* the boundary; code may not depend on it."""
        import ast

        root = Path(__file__).resolve().parent.parent / "app" / "sandbox"
        for name in ("external.py", "external_adapters.py"):
            tree = ast.parse((root / name).read_text(encoding="utf-8"))
            imported: list[str] = []
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.extend(alias.name for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.append(node.module)
            for module in imported:
                lowered = module.lower()
                for forbidden in ("onebot", "napcat", "adapters"):
                    assert forbidden not in lowered, f"{name} imports {module!r}"

    async def test_adapter_builds_events_from_primitives_only(self) -> None:
        event = adapt_qq_message(
            message_id="42", actor_id="7", text="在吗", is_group=True, group_id="9"
        )
        assert event.source is ExternalSource.qq
        assert event.event_id == "qq_42"
        # no QQ object was involved anywhere in this test
        assert isinstance(event.metadata, dict)


# --------------------------------------------- Test 8: second character


class TestSecondCharacter:
    async def test_unavailable_activity_is_rejected_not_faked(self) -> None:
        clock = Clock()
        runtime = await make_other_runtime(clock=clock)
        try:
            await runtime._start_action("idle", duration_minutes=30)  # noqa: SLF001
            before = runtime.current_action.definition_id  # type: ignore[union-attr]

            await runtime.submit_external(invitation())
            results = await runtime.wakeup()

            assert results[0]["influence"] == InfluenceAction.REJECT.value
            assert results[0]["reason"] == "activity_unavailable"
            assert results[0]["interrupt"] is False
            assert runtime.current_action.definition_id == before  # type: ignore[union-attr]
            assert runtime.events.last(ET.EXTERNAL_EVENT_REJECTED) is not None
            assert runtime.events.last(ET.WORLD_EXTERNAL_INFLUENCE) is None
        finally:
            await runtime.shutdown()

    async def test_ordinary_external_stimulus_still_works(self) -> None:
        runtime = await make_other_runtime()
        try:
            await runtime.submit_external(smalltalk(actor_id="u9"))
            results = await runtime.wakeup()
            assert results[0]["influence"] == InfluenceAction.OBSERVE.value
            received = runtime.events.last(ET.EXTERNAL_EVENT_RECEIVED)
            assert received is not None and received.source_entity_id == "qq"
            # cue vocabulary itself is character-free
            assert classify_semantics("来一起联机") == ("game_invitation", "gaming")
        finally:
            await runtime.shutdown()


# ------------------------------------------- §20: persistence / restart


class TestPersistenceAcrossRestart:
    async def test_pending_survives_restart_and_duplicates_stay_refused(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'ext.db'}"))
        await db.connect()
        clock = Clock()
        try:
            first = await make_runtime(clock=clock, database=db)
            event = invitation()
            assert await first.submit_external(event) is True
            await first.shutdown()  # queued but not yet processed

            second = await make_runtime(clock=clock, database=db)
            try:
                assert len(second.external_queue) == 1  # not lost
                assert second.external_queue.pending()[0].event_id == event.event_id
                results = await second.wakeup()
                assert len(results) == 1
                # already-consumed ids survive the restart (§19)
                assert second.external_queue.seen(event.event_id)
                assert await second.submit_external(event) is False
            finally:
                await second.shutdown()
        finally:
            await db.close()


# ------------------------------------------------- evaluator unit surface


class TestEvaluator:
    def _evaluate(self, event: ExternalWorldEvent, **kwargs: object) -> InfluenceAction:
        defaults: dict[str, object] = {
            "available_activities": {"gaming", "reading"},
            "has_action": True,
            "interruptibility": 0.6,
        }
        defaults.update(kwargs)
        return ExternalInfluenceEvaluator().evaluate(event, **defaults).action  # type: ignore[arg-type]

    def test_core_invitation_interrupts_only_when_interruptible(self) -> None:
        event = invitation()
        assert self._evaluate(event) is InfluenceAction.INTERRUPT
        assert self._evaluate(event, interruptibility=0.1) is InfluenceAction.WAKE

    def test_critical_urgency_always_wakes(self) -> None:
        event = smalltalk()
        event.urgency = ExternalUrgency.critical
        assert self._evaluate(event) is InfluenceAction.INTERRUPT
        assert self._evaluate(event, has_action=False) is InfluenceAction.WAKE

    def test_acquaintance_invitation_is_observed_not_obeyed(self) -> None:
        event = invitation(is_core=False)
        assert self._evaluate(event) is InfluenceAction.OBSERVE
