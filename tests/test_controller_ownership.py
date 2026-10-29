"""A1: TaskRuntime must fail closed when the Node controller is not the active owner."""

from __future__ import annotations

import pytest

from app.config.settings import MinecraftConfig
from app.integrations.minecraft.service import MinecraftService
from app.tasks.models import TaskState
from tests.test_action_dispatch_safety import (
    DispatchDouble,
    create,
    make_runtime,
    single_action_plan,
    start,
)


async def _guard_denied() -> str:
    return "controller.fenced"


async def _guard_ok() -> str:
    return ""


@pytest.mark.asyncio
async def test_controller_guard_blocks_world_write_before_dispatch() -> None:
    invoke = DispatchDouble(mode="sync")
    engine = make_runtime(invoke, controller_guard=_guard_denied)
    task = await create(engine, single_action_plan())
    result = await start(engine, task.task_id)
    assert result.state in {TaskState.PAUSED, TaskState.REPLANNING}
    assert invoke.actions == []
    stored = await engine.get(task.task_id)
    assert stored is not None
    assert stored.replan_required or "控制者" in str(stored.message)


def test_service_only_allows_active_owner() -> None:
    service = MinecraftService(None, MinecraftConfig(enabled=True, auto_start_runtime=False))
    for state in ("UNCLAIMED", "UNSUPPORTED", "UNKNOWN", "FAILED"):
        service._controller_state = state  # noqa: SLF001
        assert service.controller_ready() is False
    service._controller_state = "ACTIVE"  # noqa: SLF001
    service._client.owner_token = "token"  # noqa: SLF001
    service._client.owner_state = "ACTIVE"  # noqa: SLF001
    service._client.owner_expires_at = 4102444800.0  # noqa: SLF001
    assert service.controller_ready() is True
    service._client.owner_token = ""  # noqa: SLF001
    assert service.controller_ready() is False


async def test_controller_guard_allows_when_owner_is_active() -> None:
    invoke = DispatchDouble(mode="sync")
    engine = make_runtime(invoke, controller_guard=_guard_ok)
    task = await create(engine, single_action_plan())
    await start(engine, task.task_id)
    assert invoke.actions
