"""R2 admission counterexamples; deterministic integration, not real-server evidence."""

import time
from dataclasses import replace
from types import SimpleNamespace

import pytest

from app.core.bot import Bot
from app.integrations.minecraft.task_adapter import MinecraftTaskInvoker
from app.tasks.agent_plan import AgentPlan, PlanStatus
from app.tasks.life_guard import assess_life_facts, check_life_guard
from app.tasks.models import TaskState
from tests.agent_plan_fakes import PlanRig
from tests.test_task_runtime import make_runtime, simple_plan


async def test_global_current_does_not_lose_active_task_behind_recent_terminal_history():
    runtime, _, _, store = make_runtime(clock=lambda: 1000.0)
    old = await runtime.create_task(
        "still pending", session_id="owner", user_id="owner", origin="user", plan=simple_plan()
    )
    for index in range(25):
        await store.save(
            replace(
                old, task_id=f"done_{index}", state=TaskState.SUCCEEDED, updated_at=1001 + index
            )
        )
    current = await runtime.current()
    assert current is not None and current.task_id == old.task_id


async def test_unreadable_task_state_is_unknown_not_idle():
    class Unreadable:
        async def current(self):
            raise ConnectionError("fixture: task store unavailable")

    states = await Bot._active_task_states(SimpleNamespace(tasks=Unreadable()))
    assert states == ("UNKNOWN",)


async def test_life_approval_obeys_read_only_guard_before_creating_a_task():
    rig = PlanRig()
    assert rig.service is not None
    runtime, _, _, store = make_runtime(clock=lambda: rig.clock_now)
    rig.service.runtime = runtime
    plan = simple_plan(steps=[("minecraft_move_to", {"x": 10, "y": 64, "z": 10})])
    pending, _ = await rig.store.create(
        AgentPlan(
            plan_id="",
            source="LIFE",
            objective="bounded exploration",
            status=PlanStatus.READY_FOR_APPROVAL.value,
            plan=plan.to_payload(),
            plan_hash=plan.plan_hash,
            fingerprint="r2_guard_counterexample",
            version=1,
            created_at=rig.clock_now,
            updated_at=rig.clock_now,
            expires_at=rig.clock_now + 600,
        )
    )
    checked = []

    async def guard(*, ignore_task_id="", intent_id=""):
        checked.append(ignore_task_id)
        return {"allowed": False, "reason": "active_task_conflict"}

    rig.service.life_guard = guard
    result = await rig.service.approve(plan_id=pending.plan_id, user_id="owner", session_id="owner")
    assert result["action"] != "approved"
    assert checked == [""]
    assert await store.list_pending() == []
    unchanged = await rig.store.get(pending.plan_id)
    assert unchanged is not None and unchanged.status == PlanStatus.READY_FOR_APPROVAL.value


@pytest.mark.parametrize("energy", [None, True, float("nan"), float("inf"), -1, 2])
def test_unknown_or_invalid_role_energy_is_not_healthy(energy):
    assert not assess_life_facts(None, energy=energy, world={}, blockers=[], now=1000)["allowed"]


@pytest.mark.parametrize(
    "changes,world,blockers,reason",
    [
        ({"sleeping": True}, None, [], "role_sleeping"),
        ({"quiet_hours": True}, None, [], "role_quiet_hours"),
        ({"degraded": "store unreadable"}, None, [], "role_degraded"),
        ({"recovered_at": 990}, None, [], "role_recovering"),
        ({}, {}, [], "world_unavailable"),
        (
            {},
            {
                "online": True,
                "available": True,
                "semantic": {"self": {"dimension": "overworld"}},
                "layers": {"near": {"age_seconds": 6.0}},
            },
            [],
            "world_unavailable",
        ),
        ({}, None, [object()], "active_task_conflict"),
    ],
)
def test_current_life_preconditions_fail_closed(changes, world, blockers, reason):
    context = SimpleNamespace(**changes)
    decision = assess_life_facts(context, energy=0.8, world=world, blockers=blockers, now=1000)
    assert decision == {"allowed": False, "reason": reason}


def test_known_life_facts_still_do_not_grant_an_authorization():
    result = assess_life_facts(
        SimpleNamespace(),
        energy=0.8,
        world={
            "online": True,
            "available": True,
            "semantic": {"self": {"dimension": "overworld"}},
            "layers": {"near": {"age_seconds": 0.2}},
        },
        blockers=[],
        now=1000,
    )
    assert result == {"allowed": True, "reason": "life_preconditions_checked"}
    assert "authorization" not in result


async def test_missing_or_unreadable_life_guard_never_approves():
    assert not (await check_life_guard(None))["allowed"]

    async def broken(**kwargs):
        raise ValueError("private detail must not escape")

    assert await check_life_guard(broken) == {
        "allowed": False,
        "reason": "life_context_unavailable",
    }


async def test_life_step_blocked_before_bridge_and_stop_bypasses_the_guard():
    class StopReached(Exception):
        pass

    class Bridge:
        async def invoke_task_step(self, *args, **kwargs):
            raise AssertionError("blocked LIFE step reached the game bridge")

        async def invoke_developer(self, *args, **kwargs):
            raise StopReached

    async def read(task_id):
        return SimpleNamespace(source="qq_life_plan")

    calls = []

    async def blocked(**kwargs):
        calls.append(kwargs)
        return {"allowed": False, "reason": "role_sleeping"}

    invoker = MinecraftTaskInvoker(Bridge(), task_reader=read, life_guard=blocked)
    result = await invoker("minecraft_move_to", {"x": 1, "y": 64, "z": 1}, task_id="life")
    assert not result.ok and result.code == "life.precondition_changed"
    assert len(calls) == 1
    with pytest.raises(StopReached):
        await invoker("minecraft_stop", {}, task_id="life")
    assert len(calls) == 1


async def test_admission_reads_persisted_state_instead_of_chat_defaults(tmp_path):
    from app.character.state import StateManager
    from app.config.settings import DatabaseConfig
    from app.database.database import Database

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'role.db'}"))
    await database.connect()
    try:
        states = StateManager(database)
        assert await states.load() is not None  # ordinary chat may use its established fallback
        assert await states.read_for_admission() is None
        await states.update(energy=0.8)
        known = await states.read_for_admission()
        assert known is not None and known.energy == 0.8
        await database.execute("UPDATE character_states SET data = ? WHERE id = 1", ("{}",))
        assert await states.read_for_admission() is None
    finally:
        await database.close()


async def test_life_guard_rechecks_live_connection_before_trusting_cached_world():
    from app.initiative import InitiativeContext
    from tests.initiative_fakes import intent

    now = time.time()
    row = replace(intent(character_id="r2-role", created_at=now), expires_at=now + 3600)

    class Store:
        async def get(self, intent_id):
            return row if intent_id == row.intent_id else None

    class States:
        async def read_for_admission(self):
            return SimpleNamespace(energy=0.8)

    class Tasks:
        async def execution_blockers(self, **kwargs):
            return []

    class Minecraft:
        connection = {"status": "DISCONNECTED", "connected_at": now - 1}
        captured_at = now

        async def live_connection_status(self):
            return self.connection

        def world_view(self):
            return {
                "online": True,
                "available": True,
                "semantic": {"self": {"dimension": "overworld"}},
                "layers": {"near": {"age_seconds": 0.1, "captured_at": self.captured_at}},
            }

    async def context_provider(moment):
        return InitiativeContext(character_id="r2-role", now=moment)

    minecraft = Minecraft()
    owner = SimpleNamespace(
        initiative=SimpleNamespace(
            store=Store(), context_provider=context_provider, recovered_at=0
        ),
        character=SimpleNamespace(states=States()),
        tasks=Tasks(),
        minecraft=minecraft,
        presence=SimpleNamespace(
            is_sleeping=lambda: False, in_dnd=lambda: False, is_night=lambda: False
        ),
        _activity_character_id=lambda: "r2-role",
    )
    assert (await Bot._life_goal_guard(owner, intent_id=row.intent_id))[
        "reason"
    ] == "world_unavailable"
    minecraft.connection = None
    assert (await Bot._life_goal_guard(owner, intent_id=row.intent_id))[
        "reason"
    ] == "world_unavailable"
    minecraft.connection = {"status": "ONLINE", "connected_at": now - 1}
    assert (await Bot._life_goal_guard(owner, intent_id=row.intent_id))["allowed"]
    minecraft.connection = {"status": "ONLINE", "connected_at": now + 1}
    stale = await Bot._life_goal_guard(owner, intent_id=row.intent_id)
    assert stale["reason"] == "world_unavailable"


async def test_cleared_startup_guard_can_propose_without_rewriting_suppressed_history():
    from app.initiative import GoalSignal, InitiativeContext, LifeIntentStatus
    from app.initiative.gate import RECOVERY_GRACE_SECONDS
    from tests.initiative_fakes import T0, Rig

    rig = Rig(
        context=InitiativeContext(
            character_id="r2-role",
            now=T0,
            minecraft_online=True,
            goals=(
                GoalSignal(
                    goal_id="r2-goal", kind="complete_project", label="Minecraft 探索", priority=1
                ),
            ),
        )
    )
    assert rig.service is not None
    rig.service.recovered_at = T0
    first = await rig.service.check()
    assert first["reason"] == "CHARACTER_RECOVERY"
    old = await rig.store.recent("r2-role")
    assert old and all(row.status is LifeIntentStatus.SUPPRESSED for row in old)
    after = await rig.check(advance=RECOVERY_GRACE_SECONDS + 1)
    assert after["created"], (
        "cleared recovery must not stay suppressed for the entire fingerprint hour"
    )
    assert (await rig.check())["created"] == []
    for row in old:
        persisted = await rig.store.get(row.intent_id)
        assert persisted is not None and persisted.status is LifeIntentStatus.SUPPRESSED


async def test_sqlite_recovery_reproposal_preserves_suppression_and_is_idempotent(tmp_path):
    from app.config.settings import DatabaseConfig
    from app.database.database import Database
    from app.initiative import (
        GoalSignal,
        InitiativeContext,
        LifeIntentService,
        LifeIntentStatus,
        SqliteLifeIntentStore,
    )
    from app.initiative.gate import RECOVERY_GRACE_SECONDS
    from tests.initiative_fakes import T0, FakeConfig

    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'recovery.db'}"))
    await database.connect()
    try:
        store = SqliteLifeIntentStore(database)
        context = InitiativeContext(
            character_id="r2-role",
            now=T0,
            minecraft_online=True,
            goals=(
                GoalSignal(
                    goal_id="r2-goal", kind="complete_project", label="Minecraft 探索", priority=1
                ),
            ),
        )

        async def provider(now):
            return replace(context, now=now)

        service = LifeIntentService(
            store=store,
            config=FakeConfig(),
            context_provider=provider,
            character_id="r2-role",
            clock=lambda: T0,
        )
        service.recovered_at = T0
        cold = await service.check(now=T0)
        assert cold["reason"] == "CHARACTER_RECOVERY" and not cold["created"]
        suppressed = await store.recent("r2-role")
        assert suppressed and all(row.status is LifeIntentStatus.SUPPRESSED for row in suppressed)

        warm = T0 + RECOVERY_GRACE_SECONDS + 1
        ready = await service.check(now=warm)
        assert len(ready["created"]) == 1
        assert (await service.check(now=warm))["created"] == []
        for row in suppressed:
            unchanged = await store.get(row.intent_id)
            assert unchanged is not None and unchanged.status is LifeIntentStatus.SUPPRESSED
        proposed = await store.get(ready["created"][0])
        assert proposed is not None and proposed.status is LifeIntentStatus.PROPOSED
        assert proposed.fingerprint.startswith(suppressed[0].fingerprint.split("|", 1)[0])
    finally:
        await database.close()


@pytest.mark.parametrize(
    "blocked,cleared,reason",
    [
        ({"sleeping": True}, {"sleeping": False}, "SLEEPING"),
        ({"quiet_hours": True}, {"quiet_hours": False}, "QUIET_HOURS"),
        (
            {"task_states": ("PENDING_CONFIRMATION",)},
            {"task_states": ()},
            "PENDING_CONFIRMATION",
        ),
    ],
)
async def test_cleared_reversible_life_guard_reproposes_once(blocked, cleared, reason):
    from app.initiative import GoalSignal, InitiativeContext, LifeIntentStatus
    from tests.initiative_fakes import T0, Rig

    rig = Rig(
        context=InitiativeContext(
            character_id="r2-role",
            now=T0,
            minecraft_online=True,
            goals=(GoalSignal(goal_id="r2-goal", kind="complete_project", label="Minecraft 探索"),),
        )
    )
    rig.feed(**blocked)
    first = await rig.check()
    assert first["reason"] == reason and not first["created"]
    old = await rig.store.recent("r2-role")
    assert old and all(row.status is LifeIntentStatus.SUPPRESSED for row in old)
    rig.feed(**cleared)
    assert len((await rig.check())["created"]) == 1
    assert (await rig.check())["created"] == []
    for row in old:
        persisted = await rig.store.get(row.intent_id)
        assert persisted is not None and persisted.status is LifeIntentStatus.SUPPRESSED
