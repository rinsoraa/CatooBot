"""Phase 3.5 tests: Need / Project / Knowledge all close over the mutation spine.

The claim under test is causal, not cosmetic: a change produced by real
runtime work must be reachable as StateMutation → SandboxEvent, with
before/after and a causation link back into the action's chain.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
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


async def complete_now(runtime, clock, action_id: str) -> None:  # type: ignore[no-untyped-def]
    """Start the action and complete it immediately (no drift in between)."""
    started = await runtime._start_action(action_id)  # noqa: SLF001
    assert started is not None, f"{action_id} did not start"
    runtime.current_action.planned_end_at = clock.now
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001 - the completion path itself


# ---------------------------------------------------------------- Test 1/2


class TestNeedMutations:
    async def test_action_completion_relief_is_a_recorded_mutation(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            runtime.needs.get("hunger").level = 0.8  # type: ignore[union-attr]
            before = runtime.needs.level("hunger")
            await complete_now(runtime, clock, "eat_pudding")

            after = runtime.needs.level("hunger")
            assert after < before  # relief happened
            changed = [
                e for e in runtime.events.of_type(ET.NEED_CHANGED) if e.target_entity_id == "hunger"
            ]
            assert changed, "NEED_CHANGED for hunger missing"
            assert changed[-1].payload["before"] == before
            assert changed[-1].payload["after"] == after
            entries = [
                e for e in runtime.mutations.recent(limit=200) if e["target"] == "needs:hunger"
            ]
            assert entries and entries[0]["before"] == before and entries[0]["after"] == after
        finally:
            await runtime.shutdown()

    async def test_action_cost_is_a_recorded_mutation(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            before = runtime.needs.level("sleepiness")
            await complete_now(runtime, clock, "play_singleplayer")

            after = runtime.needs.level("sleepiness")
            assert after > before  # the cost was paid
            entries = [
                e
                for e in runtime.mutations.recent(limit=200)
                if e["target"] == "needs:sleepiness" and e["reason"] == "play_singleplayer"
            ]
            assert entries, "sleepiness cost mutation missing"
            assert entries[0]["before"] == before and entries[0]["after"] == after
            costs = [
                e
                for e in runtime.events.of_type(ET.NEED_CHANGED)
                if e.target_entity_id == "sleepiness"
            ]
            assert costs and costs[-1].payload["reason"] == "play_singleplayer"
        finally:
            await runtime.shutdown()

    async def test_need_events_are_not_islands(self) -> None:
        """§11: NEED_CHANGED hangs off ACTION_EFFECT_APPLIED in the chain."""
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await complete_now(runtime, clock, "eat_pudding")
            applied = [
                e
                for e in runtime.events.recent(limit=100)
                if e.event_type is ET.ACTION_EFFECT_APPLIED
            ][-1]
            chain = runtime.events.chain(applied.correlation_id)
            need_events = [e for e in chain if e.event_type is ET.NEED_CHANGED]
            assert need_events, "need changes missing from the action chain"
            assert all(e.causation_id == applied.event_id for e in need_events)
        finally:
            await runtime.shutdown()

    async def test_time_drift_emits_band_crossings_only(self) -> None:
        """Continuous drift is time evolution; band changes are world facts."""
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            before_events = len(runtime.events.of_type(ET.NEED_CHANGED))
            await runtime.tick(minutes=5)  # small drift, no band change
            assert len(runtime.events.of_type(ET.NEED_CHANGED)) == before_events

            runtime.needs.get("hunger").level = 0.49  # type: ignore[union-attr]
            await runtime.tick(minutes=60 * 10)  # crosses the soft band
            crossed = [
                e
                for e in runtime.events.of_type(ET.NEED_CHANGED)
                if e.source_entity_id == "time_advance"
            ]
            hunger_crossings = [e for e in crossed if e.target_entity_id == "hunger"]
            assert hunger_crossings, "hunger band crossing not recorded"
            assert (
                hunger_crossings[-1].payload["band_after"]
                != hunger_crossings[-1].payload["band_before"]
            )
        finally:
            await runtime.shutdown()


# ---------------------------------------------------------------- Test 3/6


class TestProjectProgress:
    async def test_progress_goes_through_the_canonical_helper(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            project_id = next(iter(runtime.projects))
            before = float(runtime.projects[project_id]["progress"])
            await complete_now(runtime, clock, "play_minecraft")

            after = float(runtime.projects[project_id]["progress"])
            assert after > before
            changed = runtime.events.last(ET.PROJECT_PROGRESS_CHANGED)
            assert changed is not None and changed.target_entity_id == project_id
            assert changed.payload["before"] == before and changed.payload["after"] == after
            assert changed.payload["completed"] is False
            entries = [
                e
                for e in runtime.mutations.recent(limit=200)
                if e["target"] == f"project:{project_id}"
            ]
            assert entries and entries[0]["before"] == before and entries[0]["after"] == after
        finally:
            await runtime.shutdown()

    async def test_completion_is_reported_in_the_same_event(self) -> None:
        runtime = await make_runtime()
        try:
            project_id = next(iter(runtime.projects))
            runtime.projects[project_id]["progress"] = 0.99
            result = runtime.update_project_progress(
                project_id, delta=0.05, source="test", reason="finish_it"
            )
            assert result.ok is True
            changed = runtime.events.last(ET.PROJECT_PROGRESS_CHANGED)
            assert changed is not None
            assert changed.payload["completed"] is True
            assert changed.payload["after"] == 1.0
            assert runtime.projects[project_id]["status"] == "completed"
        finally:
            await runtime.shutdown()

    async def test_unknown_project_is_refused_cleanly(self) -> None:
        runtime = await make_runtime()
        try:
            result = runtime.update_project_progress(
                "ghost", delta=0.1, source="test", reason="nope"
            )
            assert result.ok is False and result.error == "unknown_project"
            assert runtime.events.last(ET.PROJECT_PROGRESS_CHANGED) is None
        finally:
            await runtime.shutdown()


# ---------------------------------------------------------------- Test 4/7


class TestKnowledge:
    async def test_world_fact_becomes_traced_knowledge(self) -> None:
        clock = Clock()
        runtime = await make_runtime(clock=clock)
        try:
            await complete_now(runtime, clock, "eat_pudding")

            changed = runtime.events.last(ET.KNOWLEDGE_CHANGED)
            assert changed is not None
            assert changed.payload["key"].endswith("_stock")
            assert changed.payload["before"] is None  # first time she noticed
            assert changed.payload["after"]["known"] is True
            assert changed.payload["source"] == "observation"
            entries = [
                e
                for e in runtime.mutations.recent(limit=200)
                if e["target"] == f"knowledge:{changed.payload['key']}"
            ]
            assert entries and entries[0]["before"] is None
            assert entries[0]["after"]["learned_at"] == clock.now
        finally:
            await runtime.shutdown()

    async def test_repeated_learning_records_before_and_after(self) -> None:
        runtime = await make_runtime()
        try:
            runtime.set_knowledge("mood_of_cat", source="observation", reason="first")
            first = runtime.events.last(ET.KNOWLEDGE_CHANGED)
            assert first is not None and first.payload["before"] is None
            runtime.set_knowledge(
                "mood_of_cat", source="observation", reason="second", data={"value": "sleepy"}
            )
            second = runtime.events.last(ET.KNOWLEDGE_CHANGED)
            assert second is not None
            assert second.payload["before"]["known"] is True  # entry shape kept
            assert second.payload["after"]["data"] == {"value": "sleepy"}
            assert second.payload["after"]["source"] == "observation"
        finally:
            await runtime.shutdown()

    def test_business_paths_have_no_direct_writes(self) -> None:
        """Source guard: outside their canonical helpers, nothing writes these.

        The helpers themselves may call ``needs.add/relieve`` / assign
        ``project["progress"]`` / ``self.knowledge[...]`` — that *is* the
        canonical path. Everything else is a bypass.
        """
        import re

        source = (
            Path(__file__).resolve().parent.parent / "app" / "sandbox" / "runtime.py"
        ).read_text(encoding="utf-8")
        allowed_scopes = {
            "adjust_need",
            "_adjust_need",
            "_note_need_drift",
            "update_project_progress",
            "set_knowledge",
        }
        offenders: list[str] = []
        scope = ""
        for number, line in enumerate(source.splitlines(), 1):
            match = re.match(r"\s*(?:async )?def (\w+)", line)
            if match:
                scope = match.group(1)
            stripped = line.strip()
            if stripped.startswith("#") or scope in allowed_scopes:
                continue
            if "self.needs.relieve(" in stripped or "self.needs.add(" in stripped:
                offenders.append(f"needs:{number}({scope})")
            if 'project["progress"] =' in stripped or "project['progress'] =" in stripped:
                offenders.append(f"project:{number}({scope})")
            if "self.knowledge[" in stripped and "] =" in stripped:
                offenders.append(f"knowledge:{number}({scope})")
        assert offenders == [], f"绕过 canonical path 的写入: {offenders}"


# ---------------------------------------------------------------- Test 8


class TestSecondCharacter:
    async def test_all_three_domains_close_over_the_spine(self) -> None:
        clock = Clock()
        runtime = await make_runtime(bible_path=OTHER_BIBLE_PATH, clock=clock)
        try:
            # need: adjust_need works on any world
            result = runtime.adjust_need(
                "social_need", delta=-0.05, source="test", reason="ache_first"
            )
            assert result.ok is True
            assert runtime.events.last(ET.NEED_CHANGED) is not None

            # knowledge: generic key, no character vocabulary
            runtime.set_knowledge("kettle_stock", source="observation", reason="ache_second")
            changed = runtime.events.last(ET.KNOWLEDGE_CHANGED)
            assert changed is not None and changed.payload["key"] == "kettle_stock"

            # project: whatever this world seeded (may be none for 阿澈)
            if runtime.projects:
                project_id = next(iter(runtime.projects))
                runtime.update_project_progress(
                    project_id, delta=0.1, source="test", reason="ache_third"
                )
                assert runtime.events.last(ET.PROJECT_PROGRESS_CHANGED) is not None

            # action completion still routes costs/relief through the helpers
            clock.advance(1)
            await complete_now(runtime, clock, "idle")
            assert all(
                not entry["reason"].startswith("bypass") for entry in runtime.mutations.recent()
            )
        finally:
            await runtime.shutdown()
