"""Normal Bot assembly rejects invalid persisted LIFE origins before world reads."""

import time
from typing import Any

import pytest

from app.initiative.model import LifeIntent, LifeIntentStatus, LifeIntentType
from app.tasks.proposal import TaskProposal
from tests.test_r3_normal_wiring import assembled as shared_assembled

assembled = shared_assembled


@pytest.mark.parametrize(
    "case,reason",
    [
        ("missing", "life_origin_unavailable"),
        ("wrong_character", "life_origin_unavailable"),
        ("suppressed", "life_intent_not_open"),
        ("expired", "life_intent_expired"),
    ],
)
async def test_normal_life_origin_cannot_borrow_execution_authority(
    assembled: Any, case: str, reason: str
) -> None:
    bot, requests = assembled
    before = list(requests)
    now = time.time()
    intent_id = "missing-origin"
    if case != "missing":
        intent, created = await bot.initiative.store.create(
            LifeIntent(
                intent_id="",
                character_id="another-character"
                if case == "wrong_character"
                else bot._activity_character_id(),
                intent_type=LifeIntentType.GOAL_PROGRESS,
                title="采集一块沙子并带回来",
                related_goal="test-goal",
                created_at=now - 30,
                expires_at=now - 1 if case == "expired" else now + 300,
                status=LifeIntentStatus.SUPPRESSED
                if case == "suppressed"
                else LifeIntentStatus.PROPOSED,
                fingerprint=f"r3-normal-origin-{case}",
            )
        )
        assert created
        intent_id = intent.intent_id
    result = await bot.agent_plans.plan_from_proposal(
        TaskProposal(
            proposal_id=f"TP-r3-{case}",
            source="LIFE",
            objective="采集一块沙子并带回来",
            status="NEEDS_USER_APPROVAL",
            intent_id=intent_id,
            initiator=bot._activity_character_id(),
            target={"status": "VERIFIED", "reason": "no_player_target"},
            created_at=now,
            expires_at=now + 300,
        )
    )
    assert result["plan"].status == "BLOCKED_BY_PRECONDITION"
    assert result["plan"].reason == reason
    approval = await bot.agent_plans.approve(
        plan_id=result["plan"].plan_id, user_id="123", session_id="private:123"
    )
    assert approval["action"] == "not_approvable"
    assert await bot.tasks.current() is None
    assert requests == before
