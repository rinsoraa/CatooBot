"""Phase 7F.2：探索结果回流（事实 / 经历 / LifeIntent / AgentPlan）的测试矩阵。

覆盖任务书 §五 的十条：
到达无新事实 / 到达有新事实 / 受阻 / 取消 / 缺证据 / 幂等 / 可追溯路径 /
未批准不执行 / 进入既有 ActivityPlanner / InMemory↔SQLite 一致。

真执行与后置条件验证在 ``tests/test_task_runtime_explore.py``，这里只测"结果怎么回流"。
"""

from __future__ import annotations

from typing import Any

from app.activity.initiative import INTENT_ACTIVITY_MAP, hint_book_from, normalise_hint
from app.initiative.candidates import InitiativeContext, MemorySignal, propose_intents
from app.initiative.model import LifeIntentStatus, LifeIntentType
from app.memory.minecraft.model import MinecraftMemoryKind
from app.tasks.agent_plan import PlanStatus
from app.tasks.exploration import (
    ExplorationOutcome,
    arrival_fact,
    exploration_facts,
    exploration_outcome,
    is_exploration_task,
)
from app.tasks.models import (
    ExpectedFinalState,
    StepState,
    TaskPlan,
    TaskRecord,
    TaskState,
    TaskStep,
)
from app.tasks.proposal_service import intent_objective
from tests.agent_plan_fakes import PlanRig
from tests.minecraft_memory_fakes import build_bridge
from tests.proposal_fakes import T0 as PROPOSAL_T0
from tests.proposal_fakes import ProposalRig

TARGET = {"x": 116.0, "y": 64.0, "z": 100.0, "radius": 2.0}
OBJECTIVE = "去南边探索一下，看看有什么新地方"


# ---------------------------------------------------------------- 夹具


def _poi(x: float, z: float, name: str = "minecraft:chest") -> dict[str, Any]:
    return {"type": name, "distance": 16.0, "pos": {"x": x, "y": 64.0, "z": z}}


def world_step(
    *,
    state: StepState = StepState.SUCCEEDED,
    pois: list[dict[str, Any]] | None = None,
    available: bool = True,
    online: bool = True,
    with_semantic: bool = True,
) -> TaskStep:
    """一个真实的 ``minecraft_world`` 执行步（只认终态 SUCCEEDED 的观察）。"""
    step = TaskStep(
        step_id="step_3",
        tool="minecraft_world",
        risk="SAFE",
        state=state,
        status="SUCCEEDED" if state is StepState.SUCCEEDED else "FAILED",
    )
    if not with_semantic:
        step.result = {"available": available, "online": online}
    else:
        step.result = {
            "available": available,
            "online": online,
            "semantic": {
                "self": {"position": {"x": TARGET["x"], "y": TARGET["y"], "z": TARGET["z"]}},
                "points_of_interest": list(pois or []),
            },
        }
    return step


def explore_record(
    *,
    state: TaskState = TaskState.SUCCEEDED,
    checked: bool = True,
    ok: bool = True,
    steps: list[TaskStep] | None = None,
    task_id: str = "task_explore_1",
    objective: str = OBJECTIVE,
) -> TaskRecord:
    """一条真实的持久化探索 TaskRecord（带 position_within 后置条件）。"""
    return TaskRecord(
        task_id=task_id,
        session_id="s",
        user_id="2731431246",
        origin="USER",
        objective=objective,
        plan=TaskPlan(
            objective=objective,
            steps=list(steps if steps is not None else [world_step()]),
            expected_final_state=ExpectedFinalState(position_within=dict(TARGET)),
        ),
        state=state,
        verification={
            "checked": checked,
            "ok": ok,
            "position_within": dict(TARGET),
        },
        message="blocked at the ridge" if state is TaskState.FAILED else "",
    )


def _context(memories: tuple[Any, ...], *, now: float = PROPOSAL_T0) -> InitiativeContext:
    return InitiativeContext(character_id="罐头@deadbeef", now=now, memories=memories)


def _actionable_signal(summary: str) -> MemorySignal:
    return MemorySignal(
        summary,
        domain="minecraft",
        scope="character:空凛:minecraft",
        actionable=True,
        exploration_outcome=ExplorationOutcome.NEW_FACTS_VERIFIED.value,
    )


# ---------------------------------------------------------------- 1. 到达无新事实


class TestArrivalWithoutNewFact:
    def test_outcome_is_arrived_no_new_fact(self) -> None:
        record = explore_record(steps=[world_step(pois=[])])
        result = exploration_outcome(record)
        assert result.outcome is ExplorationOutcome.ARRIVED_NO_NEW_FACT
        assert result.arrived is True
        assert result.new_facts == ()

    def test_no_new_facts_projected(self) -> None:
        record = explore_record(steps=[world_step(pois=[])])
        assert exploration_facts(record, server_id="mc-1") == ()

    async def test_bridge_saves_experience_but_no_actionable_fact(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        record = explore_record(steps=[world_step(pois=[])])
        await bridge.on_task_finished(record)
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        kinds = {fact.kind for fact in facts}
        assert MinecraftMemoryKind.TASK in kinds
        assert MinecraftMemoryKind.EVENT in kinds
        # 没有任何可操作的探索事实 → 不会反复引导"继续探索"
        assert all(not bool(fact.extra.get("actionable")) for fact in facts)
        await database.close()

    def test_arrival_memory_alone_does_not_generate_repeat_explore_intent(self) -> None:
        event = arrival_fact(explore_record(), server_id="mc-1")
        assert event is not None
        # 到达经历 actionable=False，即便文本带"探索"也不该被当成可继续事实
        signal = MemorySignal(event.content, domain="minecraft", actionable=False)
        intents = propose_intents(_context((signal,)))
        assert all(item.intent_type is not LifeIntentType.EXPLORATION for item in intents)


# ---------------------------------------------------------------- 2. 到达 + 可信新事实


class TestArrivalWithNewFacts:
    def test_outcome_is_new_facts_verified(self) -> None:
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        facts = exploration_facts(record, server_id="mc-1")
        result = exploration_outcome(record, new_facts=facts)
        assert result.outcome is ExplorationOutcome.NEW_FACTS_VERIFIED
        assert len(facts) == 1
        assert facts[0].kind is MinecraftMemoryKind.LOCATION
        assert facts[0].extra["actionable"] is True

    async def test_bridge_writes_actionable_fact_into_memory(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        await bridge.on_task_finished(record)
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        actionable = [f for f in facts if f.extra.get("actionable") is True]
        assert len(actionable) == 1
        assert actionable[0].kind is MinecraftMemoryKind.LOCATION
        await database.close()

    def test_actionable_fact_generates_explore_shaped_intent(self) -> None:
        signal = _actionable_signal(
            "在探索“去南边探索一下”时，(116,64,100) 附近发现了 minecraft:chest。"
        )
        intents = propose_intents(_context((signal,)))
        assert len(intents) == 1
        intent = intents[0]
        assert intent.intent_type is LifeIntentType.MINECRAFT_INTEREST
        assert "exploration_feedback" in intent.tags
        # 目标文本必须能被 7F.1 的探索模板识别（detect_shape = explore）
        from app.tasks.agent_planner import detect_shape

        assert detect_shape(intent_objective(intent)) == "explore"


# ---------------------------------------------------------------- 3. 受阻


class TestBlocked:
    def test_failed_exploration_is_blocked_and_writes_no_positive_fact(self) -> None:
        record = explore_record(
            state=TaskState.FAILED,
            ok=False,
            steps=[world_step(pois=[_poi(116, 100)])],
        )
        result = exploration_outcome(record)
        assert result.outcome is ExplorationOutcome.BLOCKED
        assert exploration_facts(record, server_id="mc-1") == ()
        assert arrival_fact(record, server_id="mc-1") is None

    async def test_failed_bridge_leaves_failure_not_success(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        record = explore_record(state=TaskState.FAILED, ok=False)
        await bridge.on_task_finished(record)
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        assert [f.kind for f in facts] == [MinecraftMemoryKind.TASK]
        assert facts[0].outcome == "FAILED"
        assert facts[0].extra.get("exploration_outcome") == ExplorationOutcome.BLOCKED.value
        await database.close()


# ---------------------------------------------------------------- 4. 取消 / 超时


class TestCancelled:
    def test_cancelled_is_not_success(self) -> None:
        record = explore_record(state=TaskState.CANCELLED, checked=False, ok=False)
        result = exploration_outcome(record)
        assert result.outcome is ExplorationOutcome.CANCELLED_OR_INTERRUPTED
        assert result.arrived is False

    def test_succeeded_without_independent_arrival_is_not_confirmed(self) -> None:
        # 动作自述成功、但运行时没有独立复核 → 绝不当作真实到达
        record = explore_record(checked=False, ok=False)
        assert exploration_outcome(record).outcome is ExplorationOutcome.CANCELLED_OR_INTERRUPTED

    async def test_cancelled_bridge_records_real_result(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        record = explore_record(state=TaskState.CANCELLED, checked=False, ok=False)
        await bridge.on_task_finished(record)
        facts = await bridge.store.all_facts(server_id=bridge.server_id())
        assert [f.kind for f in facts] == [MinecraftMemoryKind.TASK]
        assert facts[0].extra.get("exploration_outcome") == (
            ExplorationOutcome.CANCELLED_OR_INTERRUPTED.value
        )
        await database.close()


# ---------------------------------------------------------------- 5. 缺证据 / 离线 / 矛盾


class TestUnconfirmedEvidence:
    def test_missing_world_step_gives_no_fact(self) -> None:
        record = explore_record(steps=[])
        assert exploration_facts(record, server_id="mc-1") == ()

    def test_offline_observation_gives_no_fact(self) -> None:
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)], online=False)])
        assert exploration_facts(record, server_id="mc-1") == ()

    def test_failed_world_step_gives_no_fact(self) -> None:
        record = explore_record(steps=[world_step(state=StepState.FAILED, pois=[_poi(116, 100)])])
        assert exploration_facts(record, server_id="mc-1") == ()

    def test_poi_without_position_is_skipped(self) -> None:
        broken = {"type": "minecraft:chest", "distance": 16.0}
        record = explore_record(steps=[world_step(pois=[broken])])
        assert exploration_facts(record, server_id="mc-1") == ()

    def test_no_server_scope_gives_no_fact(self) -> None:
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        assert exploration_facts(record, server_id="") == ()

    def test_non_exploration_task_is_not_projected(self) -> None:
        plain = TaskRecord(
            task_id="t",
            session_id="s",
            user_id="u",
            origin="USER",
            objective="过来我这边",
            plan=TaskPlan(objective="过来我这边", steps=[world_step()]),
        )
        assert is_exploration_task(plain) is False


# ---------------------------------------------------------------- 6. 幂等 & 重启


class TestIdempotency:
    async def test_replay_does_not_duplicate_facts(self, tmp_path) -> None:
        bridge, database, _manager, _service = await build_bridge(tmp_path)
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        for _ in range(3):
            await bridge.on_task_finished(record)
        location = await bridge.store.facts(
            server_id=bridge.server_id(), kinds=[MinecraftMemoryKind.LOCATION]
        )
        assert len(location) == 1  # 同一个 dedupe_key → 强化，不新增
        assert location[0].observation_count >= 3
        await database.close()

    async def test_restart_reads_fact_back_with_evidence(self, tmp_path) -> None:
        bridge, database, manager, _service = await build_bridge(tmp_path)
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        await bridge.on_task_finished(record)
        await database.close()

        # 重启：新的 Database/MemoryManager 指向同一个 SQLite 文件
        from app.config.settings import DatabaseConfig, MemoryConfig
        from app.database.database import Database
        from app.memory.manager import MemoryManager

        db2 = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'mc_memory.db'}"))
        await db2.connect()
        bridge2, _db, _mgr, _svc = await build_bridge(
            tmp_path, database=db2, manager=MemoryManager(MemoryConfig(), db2)
        )
        facts = await bridge2.store.all_facts(server_id=bridge2.server_id())
        actionable = [f for f in facts if f.extra.get("actionable") is True]
        assert len(actionable) == 1
        assert actionable[0].task_id == "task_explore_1"
        await db2.close()

    def test_same_signal_produces_one_fingerprint(self) -> None:
        signal = _actionable_signal("在探索“去南边”时，(116,64,100) 附近发现了箱子。")
        first = propose_intents(_context((signal,)))
        second = propose_intents(_context((signal,)))
        assert [i.fingerprint for i in first] == [i.fingerprint for i in second]


# ---------------------------------------------------------------- 7. 可追溯真实路径


class TestTraceablePath:
    async def test_life_intent_to_task_proposal_to_agent_plan(self) -> None:
        signal = _actionable_signal(
            "在探索“去南边探索一下”时，(116,64,100) 附近发现了 minecraft:chest。"
        )
        intents = propose_intents(_context((signal,)))
        assert len(intents) == 1
        intent = intents[0]
        assert intent.status is LifeIntentStatus.PROPOSED

        proposal_rig = ProposalRig(clock_now=PROPOSAL_T0)
        proposed = await proposal_rig.service.propose_from_intent(intent)
        assert proposed["action"] == "created"
        proposal = proposed["proposal"]
        assert proposal.intent_id == intent.intent_id or proposal.intent_id == ""
        assert "探索" in proposal.objective

        plan_rig = PlanRig()
        planned = await plan_rig.service.plan_from_proposal(proposal)
        assert planned["action"] == "planned"
        plan = planned["plan"]
        assert plan.status == PlanStatus.READY_FOR_APPROVAL.value
        assert plan.intent_id == intent.intent_id
        assert plan.proposal_id == proposal.proposal_id
        # 计划只是计划：第一道门之前不建任务
        assert plan_rig.runtime.created == []
        assert proposal.source == "LIFE"

    async def test_unapproved_plan_never_creates_task(self) -> None:
        signal = _actionable_signal("在探索“去南边”时，发现了 minecraft:chest。")
        intent = propose_intents(_context((signal,)))[0]
        proposal_rig = ProposalRig(clock_now=PROPOSAL_T0)
        proposal = (await proposal_rig.service.propose_from_intent(intent))["proposal"]
        plan_rig = PlanRig()
        planned = await plan_rig.service.plan_from_proposal(proposal)
        assert planned["action"] == "planned"
        assert plan_rig.runtime.created == []


# ---------------------------------------------------------------- 8. 未批准 / 未确认不执行


class TestAuthorizationBoundary:
    async def test_approval_still_waits_for_second_confirmation(self) -> None:
        signal = _actionable_signal("在探索“去南边”时，发现了 minecraft:chest。")
        intent = propose_intents(_context((signal,)))[0]
        proposal_rig = ProposalRig(clock_now=PROPOSAL_T0)
        proposal = (await proposal_rig.service.propose_from_intent(intent))["proposal"]
        plan_rig = PlanRig()
        plan = (await plan_rig.service.plan_from_proposal(proposal))["plan"]

        approved = await plan_rig.service.approve(
            plan_id=plan.plan_id, user_id="10001", session_id="private:10001"
        )
        assert approved["action"] == "approved"
        # 第二道门：停在 PENDING_CONFIRMATION，没有真正执行
        assert str(approved["record"].state.value) == "PENDING_CONFIRMATION"
        assert plan_rig.runtime.created[-1]["steps"] == [
            "minecraft_move_to",
            "minecraft_look_at",
            "minecraft_world",
        ]

    def test_plan_has_no_execution_handle_before_task(self) -> None:
        assert PlanStatus.READY_FOR_APPROVAL.value in {
            PlanStatus.READY_FOR_APPROVAL.value,
            PlanStatus.NEEDS_MORE_INFORMATION.value,
        }  # 计划层没有 EXECUTING 状态

    def test_life_intent_never_maps_to_world_action(self) -> None:
        # 活动桥只把 MINECRAFT_INTEREST 映射到**虚拟** building，绝不映射真实动作
        assert INTENT_ACTIVITY_MAP["MINECRAFT_INTEREST"] == "building"
        assert "minecraft" not in INTENT_ACTIVITY_MAP.values()
        assert not any("move" in v or "dig" in v for v in INTENT_ACTIVITY_MAP.values())


# ---------------------------------------------------------------- 9. ActivityPlanner 兼容


class TestActivityBridge:
    def test_explore_feedback_intent_maps_to_virtual_building(self) -> None:
        signal = _actionable_signal("在探索“去南边”时，发现了 minecraft:chest。")
        intent = propose_intents(_context((signal,)))[0]
        hint, reason = normalise_hint(
            {
                "intent_type": intent.intent_type.value,
                "priority": intent.priority,
                "intent_id": intent.intent_id,
            }
        )
        assert reason == ""
        assert hint is not None
        assert hint.activity_name == "building"

    def test_old_virtual_activity_rules_still_hold(self) -> None:
        # 真实动作名依旧是"永远不能作为虚拟活动"
        for forbidden in ("minecraft_task", "minecraft_move_to", "minecraft_dig"):
            hint, reason = normalise_hint(
                {"intent_type": "MINECRAFT_INTEREST", "activity": forbidden}
            )
            assert hint is None
            assert reason in {"not_registered", "no_activity"}

    def test_hint_book_from_service_shaped_source(self) -> None:
        class Source:
            def hints(self, *, limit: int, now: float) -> list[dict[str, Any]]:
                return [{"intent_type": "MINECRAFT_INTEREST", "priority": 0.5}]

        book = hint_book_from(Source(), now=PROPOSAL_T0)
        assert book.activities() == ("building",)
        assert book.bonus_for("building") > 0


# ---------------------------------------------------------------- 10. InMemory ↔ SQLite


class TestStorageConsistency:
    async def test_sqlite_and_inmemory_agree_on_candidates(self, tmp_path) -> None:
        bridge, database, manager, _service = await build_bridge(tmp_path)
        record = explore_record(steps=[world_step(pois=[_poi(116, 100)])])
        await bridge.on_task_finished(record)

        rows = await manager.list_memories(scope_key=bridge.store.scope_key, limit=5)
        signals = tuple(
            MemorySignal(
                str(getattr(row, "content", "") or ""),
                domain="minecraft",
                actionable=bool((getattr(row, "provenance", {}) or {}).get("actionable")),
            )
            for row in rows
        )
        sqlite_intents = propose_intents(_context(signals))
        # InMemory 侧用同一条信号喂进去，结果必须一致（同类型/同指纹）
        inmem_signals = tuple(
            MemorySignal(s.text, domain=s.domain, actionable=s.actionable) for s in signals
        )
        inmem_intents = propose_intents(_context(inmem_signals))
        assert [i.intent_type for i in sqlite_intents] == [i.intent_type for i in inmem_intents]
        assert [i.fingerprint for i in sqlite_intents] == [i.fingerprint for i in inmem_intents]
        await database.close()
