"""A2 shutdown primitives: bounded task cancel and UNKNOWN preservation (no fake success)."""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.minecraft.task_coordinator import MinecraftTaskCoordinator
from app.tasks.models import TaskState
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    create,
    make_runtime,
    single_action_plan,
    start,
)


@pytest.mark.asyncio
async def test_shutdown_pending_cancels_and_records_unknown_action() -> None:
    invoke = DispatchDouble(mode="detached")
    engine = make_runtime(invoke)
    task = await create(engine, single_action_plan())
    await start(engine, task.task_id)
    record = await engine.get(task.task_id)
    assert record is not None and record.state is TaskState.WAITING_ACTION

    await engine.begin_shutdown()
    pending = await engine.shutdown_pending(timeout=2.0)

    fresh = await engine.get(task.task_id)
    assert fresh is not None and fresh.state.terminal
    assert invoke.stops, "shutdown must use the existing STOP channel"
    assert fresh.pending_action_id or pending, "unconfirmed action must remain visible"


@pytest.mark.asyncio
async def test_shutdown_pending_is_bounded_when_stop_hangs() -> None:
    class HangingStop(DispatchDouble):
        async def __call__(self, tool: str, arguments: object, **kwargs: object) -> object:
            if tool == "minecraft_stop":
                self.stops.append(str(arguments))
                import asyncio

                await asyncio.sleep(3600)
            return await super().__call__(tool, arguments, **kwargs)

    invoke = HangingStop(mode="detached")
    engine = make_runtime(invoke)
    task = await create(engine, single_action_plan())
    await start(engine, task.task_id)
    await engine.begin_shutdown()
    pending = await engine.shutdown_pending(timeout=0.2)
    fresh = await engine.get(task.task_id)
    assert fresh is not None and fresh.state.terminal
    assert pending or fresh.pending_action_id


@pytest.mark.asyncio
async def test_coordinator_begin_shutdown_stops_ticker_and_drains() -> None:
    invoke = DispatchDouble(mode="sync")
    engine = make_runtime(invoke)

    class _Service:
        def add_listener(self, listener: object) -> None:
            self.listener = listener

    coordinator = MinecraftTaskCoordinator(engine, _Service())  # type: ignore[arg-type]
    coordinator.start()
    try:
        pending = await coordinator.begin_shutdown(timeout=0.5)
        assert pending == []
        await coordinator.stop()
    finally:
        await coordinator.stop()


@pytest.mark.asyncio
async def test_new_dispatch_after_shutdown_has_zero_external_writes() -> None:
    invoke = DispatchDouble(mode="sync")
    engine = make_runtime(invoke)
    engine.freeze_dispatch()
    await engine.begin_shutdown()
    task = await create(engine, single_action_plan())
    await start(engine, task.task_id)
    assert invoke.actions == []
    fresh = await engine.get(task.task_id)
    assert fresh is not None and fresh.state is TaskState.PAUSED


async def _deny_guard() -> str:
    return "controller.fenced"


@pytest.mark.asyncio
async def test_recovered_runtime_does_not_redispatch_unknown_action(tmp_path: Any) -> None:
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.tasks.store import SqliteTaskStore

    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'recover.db'}"))
    await db.connect()
    try:
        store = SqliteTaskStore(db)
        invoke = DispatchDouble(mode="detached")
        engine = make_runtime(invoke, store=store)
        task = await create(engine, single_action_plan())
        await start(engine, task.task_id)
        await engine.begin_shutdown()
        await engine.shutdown_pending(timeout=2.0)
        before = await engine.get(task.task_id)
        assert before is not None and before.state.terminal
        assert before.pending_action_id

        new_invoke = DispatchDouble(mode="sync")
        restarted = make_runtime(new_invoke, store=store)
        recovered = await restarted.recover(task.task_id)
        assert recovered.state is TaskState.CANCELLED
        assert new_invoke.actions == []
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_bot_shutdown_freezes_before_long_cleanup() -> None:
    from app.core.bot import Bot

    order: list[str] = []

    class _Minecraft:
        def begin_shutdown(self) -> None:
            order.append("minecraft")

    class _Coordinator:
        def freeze(self) -> None:
            order.append("coordinator")

    bot = object.__new__(Bot)
    bot.minecraft = _Minecraft()  # type: ignore[assignment]
    bot.task_coordinator = _Coordinator()  # type: ignore[assignment]

    async def _world_stop() -> None:
        order.append("world_stop")
        raise RuntimeError("stop after freeze")

    bot._stop_world_activity = _world_stop  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await bot.shutdown()
    assert order == ["minecraft", "coordinator", "world_stop"]
