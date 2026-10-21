"""R4 build entry routing test; fake runtime, no world action."""

from types import SimpleNamespace
from typing import Any

from app.tasks.turn import TaskTurnHandler
from app.tasks.validation import summarize_plan
from tests.test_build_plan import BuildObservations


class FakeRuntime:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def current(self, session_id: str) -> Any:
        return None

    async def create_task(self, objective: str, **kwargs: Any) -> Any:
        self.created.append((objective, kwargs))
        return SimpleNamespace(
            task_id="task_build_1",
            objective=objective,
            plan=kwargs["plan"],
            state=SimpleNamespace(value="PENDING_CONFIRMATION", terminal=False),
            plan_version=1,
            plan_hash="hash_build",
        )

    def summary_of(self, record: Any) -> str:
        return summarize_plan(record.plan)


async def test_build_text_creates_plan_through_normal_turn_handler() -> None:
    runtime = FakeRuntime()
    observe = BuildObservations()
    observe.cells = [(10, 64, 10)]
    handler = TaskTurnHandler(runtime, observe=observe)
    outcome = await handler.handle(
        session_id="private:1", user_id="1", text="在 (10,64,10) 放一块泥土"
    )
    assert outcome.handled and outcome.action == "created"
    assert len(runtime.created) == 1
    plan = runtime.created[0][1]["plan"]
    assert [step.tool for step in plan.steps] == ["minecraft_place"]
    assert "minecraft_place" not in outcome.reply
    assert "放一个 dirt" in outcome.reply


async def test_unsupported_build_item_does_not_create_task() -> None:
    runtime = FakeRuntime()
    handler = TaskTurnHandler(runtime, observe=BuildObservations())
    outcome = await handler.handle(
        session_id="private:1", user_id="1", text="在 (10,64,10) 放一块钻石块"
    )
    assert outcome.handled and outcome.action == "plan_failed"
    assert runtime.created == []
