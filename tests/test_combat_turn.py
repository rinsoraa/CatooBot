"""R5 combat entry routing test; fake runtime, no attack."""

from types import SimpleNamespace
from typing import Any

from app.tasks.turn import TaskTurnHandler
from app.tasks.validation import summarize_plan
from tests.test_combat_plan import CombatObservations


class FakeRuntime:
    def __init__(self) -> None:
        self.created: list[Any] = []

    async def current(self, session_id: str) -> Any:
        return None

    async def create_task(self, objective: str, **kwargs: Any) -> Any:
        self.created.append((objective, kwargs))
        return SimpleNamespace(
            task_id="task_combat_1",
            objective=objective,
            plan=kwargs["plan"],
            state=SimpleNamespace(value="PENDING_CONFIRMATION", terminal=False),
            plan_version=1,
            plan_hash="hash_combat",
        )

    def summary_of(self, record: Any) -> str:
        return summarize_plan(record.plan)


async def test_combat_text_creates_bounded_plan() -> None:
    runtime = FakeRuntime()
    handler = TaskTurnHandler(runtime, observe=CombatObservations())
    outcome = await handler.handle(session_id="private:1", user_id="1", text="攻击附近的一只僵尸")
    assert outcome.handled and outcome.action == "created"
    assert len(runtime.created) == 1
    plan = runtime.created[0][1]["plan"]
    assert [step.tool for step in plan.steps].count("minecraft_attack") == 4
    assert "minecraft_attack" not in outcome.reply


async def test_forbidden_player_does_not_create_task() -> None:
    runtime = FakeRuntime()
    handler = TaskTurnHandler(runtime, observe=CombatObservations())
    outcome = await handler.handle(session_id="private:1", user_id="1", text="攻击那个玩家")
    assert outcome.handled and outcome.action == "plan_failed"
    assert runtime.created == []
