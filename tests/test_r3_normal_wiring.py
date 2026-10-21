"""Normal Bot.start assembly with controlled external transport, not live evidence."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from app.activity.adapters import TaskActivityAdapter
from app.activity.model import ActivityStatus
from app.config.settings import AppConfig
from app.core.bot import Bot
from app.integrations.minecraft.runtime_client import MinecraftRuntimeClient
from app.tasks.agent_plan import PlanTarget
from app.tasks.skill_planner import SkillAwarePlanner
from tests.conftest import BIBLE_PATH, FakeAdapter, private_event


@pytest.fixture
async def assembled(tmp_path: Any, monkeypatch: Any) -> Any:
    requests: list[tuple[str, str]] = []

    async def request(
        client: Any, method: str, path: str, *, body: dict[str, Any] | None = None
    ) -> Any:
        requests.append((method, path))
        if path in {"/minecraft/health", "/minecraft/status", "/minecraft/disconnect"}:
            return {"ok": True, "status": "DISCONNECTED", "runtime_instance": "r3_test_instance"}
        raise AssertionError(f"Unexpected external request: {method} {path}")

    monkeypatch.setattr(MinecraftRuntimeClient, "_request", request)
    config = AppConfig(
        bot={"name": "R3 Assembly Test", "debug": False},
        onebot={"enabled": False},
        database={"url": f"sqlite:///{tmp_path / 'assembly.db'}"},
        logging={
            "log_dir": str(tmp_path / "logs"),
            "watchdog_enabled": False,
            "watch_config_enabled": False,
        },
        ai={"enabled": False},
        web={"enabled": False},
        behavior={"enabled": False},
        runtime={"enabled": False},
        media={
            "enabled": False,
            "indexer_enabled": False,
            "sticker_dir": str(tmp_path / "stickers"),
            "media_dir": str(tmp_path / "media"),
        },
        sandbox={
            "enabled": True,
            "bible_path": str(BIBLE_PATH),
            "allow_ai_decisions": False,
            "max_background_messages_per_day": 0,
        },
        memory={"semantic": {"enabled": False}},
        tools={"enabled": True},
        minecraft={
            "enabled": True,
            "auto_start_runtime": False,
            "perception_enabled": False,
        },
    )
    adapter = FakeAdapter()
    bot = Bot(config, adapter)
    try:
        await bot.start()
        yield bot, requests
    finally:
        await bot.shutdown()


async def test_normal_start_wires_life_tasks_skills_and_memory(assembled: Any) -> None:
    bot, requests = assembled
    assert all(
        getattr(bot, name) is not None
        for name in (
            "sandbox",
            "activity",
            "initiative",
            "proposals",
            "agent_plans",
            "skills",
            "minecraft_memory",
            "tasks",
            "task_coordinator",
        )
    )
    assert bot.task_entry.runtime is bot.tasks
    assert bot.task_entry.handler._runtime is bot.tasks
    assert bot.task_turns._runtime is bot.tasks
    assert bot.task_entry.handler._plans is bot.agent_plans
    assert bot.task_turns._plans is bot.agent_plans
    assert bot.task_entry.handler._skills is bot.skills
    assert bot.task_turns._skills is bot.skills
    assert bot.agent_plans.skills is bot.skills
    assert bot.agent_plans.runtime is bot.tasks
    assert isinstance(bot.agent_plans.planner, SkillAwarePlanner)
    assert bot.agent_plans.planner.skills is bot.skills
    assert bot.agent_plans.planner.allow_medium is False
    assert bot.activity.planner.intent_source is bot.initiative
    assert bot.character.minecraft_memory is bot.minecraft_memory
    assert not any(method == "POST" for method, _ in requests)


@pytest.mark.parametrize(
    "text",
    [
        "不要挖一块铁矿石并带回来",
        "采集一块木头，白桦还是云杉我没决定",
        "采集99块沙子并带回来",
        "采集一块minecraft:deepslate_iron_ore并带回来",
    ],
)
async def test_normal_assembly_rejects_unexecutable_collection_at_shared_entries(
    assembled: Any, text: str
) -> None:
    bot, requests = assembled
    before = list(requests)
    await bot.task_entry.on_message(private_event(text, user_id=123))
    await bot.task_turns.handle(
        session_id="minecraft:test:123", user_id="123", text=text, origin="user"
    )
    observed: list[str] = []

    async def observe(tool: str, arguments: dict[str, Any]) -> Any:
        observed.append(tool)
        raise AssertionError("Invalid intent must be rejected before observation")

    plan = await bot.agent_plans.planner.plan(text, observe=observe)
    assert not plan.ready
    assert not observed
    assert await bot.tasks.current("private:123") is None
    assert await bot.tasks.current("minecraft:test:123") is None
    assert requests == before


async def test_task_episode_terminal_waits_for_started_projection() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    queued = asyncio.Event()

    class GatedRuntime:
        episode: Any = None

        async def current(self) -> Any:
            return self.episode

        async def recent(self, limit: int) -> list[Any]:
            return []

        async def switch_to(self, **kwargs: Any) -> Any:
            self.episode = SimpleNamespace(
                related_task_id=kwargs["related_task_id"],
                episode_id="episode_test",
                status=ActivityStatus.SCHEDULED,
            )
            entered.set()
            await release.wait()
            self.episode.status = ActivityStatus.ACTIVE
            return self.episode

        async def complete(self, **kwargs: Any) -> Any:
            assert self.episode.status is ActivityStatus.ACTIVE
            self.episode.status = ActivityStatus.COMPLETED
            return self.episode

    runtime = GatedRuntime()
    adapter = TaskActivityAdapter(runtime)
    starting = asyncio.create_task(adapter.on_task_event("task.started", {"task_id": "task1"}))
    finishing: asyncio.Task[Any] | None = None

    async def finish() -> Any:
        queued.set()
        return await adapter.on_task_event("task.succeeded", {"task_id": "task1"})

    try:
        await asyncio.wait_for(entered.wait(), 3)
        finishing = asyncio.create_task(finish())
        await asyncio.wait_for(queued.wait(), 3)
        assert not finishing.done(), "terminal event must not overtake unfinished task.start"
        release.set()
        await asyncio.wait_for(asyncio.gather(starting, finishing), 3)
        assert adapter.degraded_reason == ""
        assert runtime.episode.status is ActivityStatus.COMPLETED
    finally:
        release.set()
        await asyncio.gather(starting, *([finishing] if finishing is not None else []))


async def test_normal_plan_service_preserves_the_shared_planner_interface(assembled: Any) -> None:
    bot, requests = assembled
    before = list(requests)
    outcome = await bot.agent_plans.plan_goal_from_user(
        objective="采集99块沙子并带回来",
        user_id="123",
        session_id="private:123",
        target=PlanTarget(),
        source="qq",
        shape_hint="resource",
    )
    assert outcome["action"] == "plan_rejected"
    assert await bot.tasks.current("private:123") is None
    assert requests == before
