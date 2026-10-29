"""Goal provenance and pre-observation refusals through real Bot.start assembly."""

import time
from dataclasses import replace
from types import SimpleNamespace
from typing import Any

import pytest

from app.initiative import propose_intents
from app.sandbox.goals import GoalKind, GoalSource
from app.tasks.resource_intent import parse_collection_request
from tests.test_r3_normal_wiring import assembled as shared_assembled

assembled = shared_assembled


async def _candidate(bot: Any, objective: str) -> tuple[Any, Any]:
    goal_id = bot.sandbox.goals.create(
        kind=GoalKind.complete_project,
        source=GoalSource.external_event,
        source_event_id="r3-source-counterexample",
        reason=objective,
        target_project="r3-counterexample",
        priority=1.0,
    )
    await bot.sandbox.goals.flush()
    context = await bot.initiative.context_provider(time.time())
    intent = next(row for row in propose_intents(context) if row.related_goal == goal_id)
    goal = next(row for row in bot.sandbox.goals.all() if row.goal_id == goal_id)
    return intent, goal


@pytest.mark.parametrize(
    "objective", ["采集一块沙子并带回来", "采集一块白桦原木并捡回来", "挖一块铁矿石并捡回来"]
)
async def test_normal_goal_proposal_preserves_full_collection_source(
    assembled: Any, objective: str
) -> None:
    bot, requests = assembled
    before = list(requests)
    goal_id = bot.sandbox.goals.create(
        kind=GoalKind.complete_project,
        source=GoalSource.external_event,
        source_event_id="r3-collection-source",
        reason=objective,
        target_project="r3-source-test",
        priority=1.0,
    )
    await bot.sandbox.goals.flush()
    context = await bot.initiative.context_provider(time.time())
    intent = next(row for row in propose_intents(context) if row.related_goal == goal_id)
    result = await bot.proposals.propose_from_intent(intent)
    assert result["proposal"].objective == objective
    assert result["proposal"].source == "LIFE"
    assert intent.execution_class.value == "VIRTUAL_ONLY"
    assert await bot.tasks.current() is None
    assert requests == before


@pytest.mark.parametrize(
    "objective",
    [
        "不要挖一块铁矿石并带回来",
        "我只是问问：采集一块沙子需要什么条件？",
        "采集一块木头，白桦还是云杉我没决定。",
        "采集99块沙子并带回来",
        "采集一块minecraft:deepslate_iron_ore并带回来",
        "采集一块沙子并带回来" + "请" * 220 + "不要执行",
    ],
)
async def test_goal_source_never_promotes_an_invalid_collection_request(
    assembled: Any, objective: str
) -> None:
    bot, requests = assembled
    before = list(requests)
    intent, _ = await _candidate(bot, objective)
    result = await bot.proposals.propose_from_intent(intent)
    proposed = result["proposal"].objective
    request = parse_collection_request(proposed)
    assert request is not None and request.problem

    async def observe(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("Invalid Goal must not obtain world facts")

    plan = await bot.agent_plans.planner.plan(proposed, observe=observe)
    assert not plan.ready
    assert await bot.tasks.current() is None
    assert requests == before


@pytest.mark.parametrize(
    "case", ["missing", "cancelled", "wrong_role", "wrong_goal_owner", "unreadable", "malformed"]
)
async def test_normal_goal_provenance_fails_closed(assembled: Any, case: str) -> None:
    bot, requests = assembled
    before = list(requests)
    intent, goal = await _candidate(bot, "采集一块沙子并带回来")
    if case == "missing":
        intent = replace(intent, related_goal="missing-goal")
    elif case == "cancelled":
        bot.sandbox.goals.cancel(goal, reason="source closed before proposal")
        await bot.sandbox.goals.flush()
    elif case == "wrong_role":
        intent = replace(intent, character_id="another-role")
    elif case == "wrong_goal_owner":
        bot.proposals._goal_source = lambda: [
            SimpleNamespace(
                goal_id=goal.goal_id,
                character_id="another-role",
                status="pending",
                reason="采集一块沙子并带回来",
            )
        ]
    elif case == "unreadable":

        def unavailable() -> Any:
            raise ConnectionError("read-only source unavailable")

        bot.proposals._goal_source = unavailable
    else:
        bot.proposals._goal_source = lambda: "not a Goal list"
    result = await bot.proposals.propose_from_intent(intent)
    assert result == {"action": "skipped", "reason": "goal_source_unavailable"}
    assert await bot.proposals.recent() == []
    assert await bot.tasks.current() is None
    assert requests == before


async def test_normal_ambiguous_request_asks_for_clarification_without_observation(
    assembled: Any,
) -> None:
    bot, requests = assembled
    before = list(requests)
    outcome = await bot.task_turns.handle(
        session_id="private:123",
        user_id="123",
        text="采集一块木头，白桦还是云杉我没决定。",
        origin="user",
    )
    assert outcome.handled
    assert "明确" in outcome.reply or "选" in outcome.reply
    assert "resource.intent" not in outcome.reply
    assert "看了一圈" not in outcome.reply
    assert await bot.tasks.current() is None
    assert requests == before
