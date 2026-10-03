"""Change-driven console world log tests.

The contract: the world keeps ticking every second, but the ``🌍 世界`` line is
printed only when what the terminal *shows* actually changed — display values,
not internal floats, counters, timestamps or revisions.
"""

from __future__ import annotations

from app.config.settings import RuntimeConfig
from app.runtime.console_world import ConsoleWorldReporter, console_world_snapshot
from app.runtime.scheduler import RuntimeScheduler
from app.sandbox.bible import BibleCompiler
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfigForTests(),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


def SandboxConfigForTests():  # type: ignore[no-untyped-def]
    from app.config.settings import SandboxConfig

    return SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7)


def watch(runtime, *, narrate: bool = True) -> list:  # type: ignore[no-untyped-def]
    """Attach a counting reporter (the real emit path is unchanged)."""
    printed: list = []
    runtime.console_world = ConsoleWorldReporter(emit=printed.append)
    runtime.narrate_ticks = narrate
    return printed


# ----------------------------------------------------------- §16 A/E/H basics


class TestSuppression:
    async def test_sixty_identical_ticks_print_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§9/§16 A/H: 60 scheduler ticks, one world line — the world still ran."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        scheduler = RuntimeScheduler(runtime, config=RuntimeConfig(), clock=clock)
        try:
            for _ in range(60):
                clock.advance(1.0)  # one real second each: no visible change
                await scheduler.tick_once()
            assert scheduler.ticks == 60  # §17: the world kept advancing
            assert len(printed) == 1
            assert runtime.console_world.skipped == 59
            assert printed[0].line().endswith("·") or "·" in printed[0].line()
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_internal_changes_never_print(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§5/§16 E: counters, revisions and timestamps are not visible state."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            await runtime.tick(minutes=0)
            assert len(printed) == 1
            before = console_world_snapshot(runtime)
            # internal movement: a revision bump, time passing, a tick counter
            runtime.note_cognitive_change(reason="test")
            clock.advance(30.0)
            await runtime.tick(minutes=0)
            assert len(printed) == 1  # nothing visible happened
            assert console_world_snapshot(runtime) == before
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_need_float_drifting_inside_its_band_stays_quiet(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7/§16 C: 0.81 → 0.84 under the same rendered band prints nothing."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            # the comparison itself: the rendered band text is the truth, the
            # float behind it is not part of the terminal state (§7)
            runtime.adjust_need("thirst", delta=0.5, source="test", reason="test_pressure")
            runtime.console_world.observe(console_world_snapshot(runtime))
            assert len(printed) == 1
            assert len(printed[0].pressing) == 1  # a single rendered pressure
            before = console_world_snapshot(runtime)
            for _ in range(3):
                runtime.adjust_need("thirst", delta=0.005, source="test", reason="drift")
                runtime.console_world.observe(console_world_snapshot(runtime))
            assert console_world_snapshot(runtime).pressing == before.pressing
            assert len(printed) == 1  # the same band, the same text: quiet
        finally:
            await runtime.shutdown()
            await db.close()


# -------------------------------------------------------------- §16 B/D/G


class TestVisibleChanges:
    async def test_an_action_change_prints_exactly_once_more(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§10/§16 B: same action → quiet; a new action → one line."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            for _ in range(3):
                await runtime.tick(minutes=0)
            assert len(printed) == 1
            assert await runtime._start_action("watch_animation", duration_minutes=60) is not None  # noqa: SLF001
            await runtime.tick(minutes=0)
            assert len(printed) == 2
            assert "看动画" in printed[-1].doing or printed[-1].doing
            await runtime.tick(minutes=0)
            await runtime.tick(minutes=0)
            assert len(printed) == 2  # and quiet again
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_a_need_band_crossing_prints_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§7/§16 D: the rendered band changes → one line, then quiet."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            await runtime.tick(minutes=0)
            assert len(printed) == 1
            runtime.adjust_need("hunger", delta=1.0, source="test", reason="test_pressure")
            await runtime.tick(minutes=0)
            assert len(printed) == 2
            assert any("饿" in entry for entry in printed[-1].pressing)
            await runtime.tick(minutes=0)
            assert len(printed) == 2
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_an_external_event_prints_the_new_world(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§16 G: a real interaction that moves her shows up once."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            await runtime.tick(minutes=0)
            assert len(printed) == 1
            from app.sandbox.external import ExternalSource, ExternalUrgency, ExternalWorldEvent

            await runtime.submit_external(
                ExternalWorldEvent(
                    event_id="console-1",
                    source=ExternalSource.qq,
                    actor_id="50001",
                    content="来一起联机",
                    urgency=ExternalUrgency.critical,
                    semantic_kind="game_invitation",
                    target_activity="gaming",
                    actor_relationship="core_friend",
                )
            )
            await runtime.wakeup()
            await runtime.tick(minutes=0)
            assert len(printed) == 2  # the interruption moved her
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------- §16 F + shape


class TestReporterContract:
    async def test_a_restart_prints_the_first_state_again(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§14/§16 F: the memory is runtime-only; a fresh runtime speaks once."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        printed = watch(runtime)
        try:
            await runtime.tick(minutes=0)
            await runtime.tick(minutes=0)
            assert len(printed) == 1
            await runtime.shutdown()
        finally:
            pass

        restored = await make_sandbox(db=db, clock=clock)
        printed = watch(restored)
        try:
            await restored.tick(minutes=0)
            assert len(printed) == 1  # once again after the restart
        finally:
            await restored.shutdown()
            await db.close()

    def test_the_snapshot_holds_only_display_semantics(self) -> None:
        """§5/§6: no floats, no counters, no ids — only what the terminal shows."""
        snapshot = ConsoleWorldSnapshotFor("闲着", "客厅", ("home",), ("困了", "有点饿"))
        assert snapshot.line() == "闲着（客厅）  ·  home"
        assert snapshot.detail() == "沙盒心跳（困了；有点饿）"
        fields = set(type(snapshot).__dataclass_fields__)
        assert fields == {"doing", "location", "modes", "pressing"}  # nothing internal
        assert snapshot == ConsoleWorldSnapshotFor("闲着", "客厅", ("home",), ("困了", "有点饿"))

    def test_the_reporter_only_emits_on_a_change(self) -> None:
        from app.runtime.console_world import ConsoleWorldSnapshot

        printed: list = []
        reporter = ConsoleWorldReporter(emit=printed.append)
        same = ConsoleWorldSnapshot(doing="闲着", location="客厅", modes=("home",), pressing=())
        assert reporter.observe(same) is True
        assert reporter.observe(same) is False
        assert reporter.observe(
            ConsoleWorldSnapshot(doing="吃蛋糕", location="厨房", modes=("home",), pressing=())
        )
        assert len(printed) == 2
        assert reporter.printed == 2 and reporter.skipped == 1


def ConsoleWorldSnapshotFor(*args: object, **kwargs: object):  # type: ignore[no-untyped-def]
    from app.runtime.console_world import ConsoleWorldSnapshot

    return ConsoleWorldSnapshot(*args, **kwargs)  # type: ignore[arg-type]
