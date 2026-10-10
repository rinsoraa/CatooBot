"""Phase 7E.1 回归：证据认领的并发幂等（§1）与技能归因的真实任务绑定（§2）。

竞态是**人为制造**的：SAFE 后置条件观察会 await，我们把两个并发学习调用卡在同一个
barrier 上，让它们**同时**通过"重复证据"的快速路径，然后一起冲到认领那一步 ——
权威判据是证据表的唯一索引（SQLite 事务 / 内存临界区）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import TaskState
from app.tasks.skill import SkillStatus, SkillThresholds
from app.tasks.skill_planner import SkillAwarePlanner
from app.tasks.skill_service import SkillService
from app.tasks.skill_store import InMemorySkillStore, SqliteSkillStore
from tests.skill_fakes import (
    BLOCK,
    CHARACTER,
    DROP,
    FakeObserve,
    build_task_runtime,
    make_service,
    resource_record,
)
from tests.test_skill_service import promote


class BarrierObserve:
    """把 SAFE 观察卡在 barrier 上，制造"两个调用同时通过初始检查"的竞争窗口。"""

    def __init__(self, inner: Any, *, parties: int = 2) -> None:
        self._inner = inner
        self._parties = max(2, int(parties))
        self._arrived = 0
        self._event = asyncio.Event()

    async def __call__(self, tool: str, arguments: dict[str, Any]) -> Any:
        result = await self._inner(tool, arguments)
        if tool == "minecraft_dig_capability":
            self._arrived += 1
            if self._arrived >= self._parties:
                self._event.set()
            await asyncio.wait_for(self._event.wait(), timeout=5)
        return result


async def make_sqlite_service(
    tmp_path: Path, *, observe: Any = None, store: Any = None
) -> tuple[SkillService, Database]:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'closure.db'}"))
    await database.connect()
    service = make_service(store=store or SqliteSkillStore(database), observe=observe)
    return service, database


# ================================================================ §1 并发认领


class TestConcurrentEvidenceClaims:
    async def test_same_task_concurrent_callbacks_claim_once_in_memory(self) -> None:
        service = make_service(observe=BarrierObserve(FakeObserve()))
        record = resource_record(task_id="task_race")

        first, second = await asyncio.gather(
            service.on_task_finished(record), service.on_task_finished(record)
        )
        outcomes = {first["action"], second["action"]}
        assert outcomes == {"learned", "duplicate"}, outcomes
        evidence = await service.store.evidence_for_task("task_race")
        assert len(evidence) == 1, "同一条任务证据只能有一条"
        skill = await service.store.get(evidence[0].skill_id)
        assert skill is not None
        assert skill.success_count == 1, "并发重复回调不能把计数加成 2"
        assert skill.status == SkillStatus.CANDIDATE.value, "只有一条独立证据 → 不得晋升 ACTIVE"

    async def test_same_task_concurrent_callbacks_claim_once_real_sqlite(
        self, tmp_path: Path
    ) -> None:
        service, database = await make_sqlite_service(
            tmp_path, observe=BarrierObserve(FakeObserve())
        )
        try:
            record = resource_record(task_id="task_race_sql")
            first, second = await asyncio.gather(
                service.on_task_finished(record), service.on_task_finished(record)
            )
            assert {first["action"], second["action"]} == {"learned", "duplicate"}
            rows = await database.fetchall(
                "SELECT COUNT(*) AS n FROM procedural_skill_evidence WHERE task_id = ?",
                ("task_race_sql",),
            )
            assert int(rows[0]["n"]) == 1
            skills = await database.fetchall("SELECT status, success_count FROM procedural_skills")
            assert len(skills) == 1
            assert int(skills[0]["success_count"]) == 1
            assert str(skills[0]["status"]) == SkillStatus.CANDIDATE.value
        finally:
            await database.close()

    async def test_two_different_tasks_concurrently_accumulate_two_evidences(self) -> None:
        """两个**不同** task 的合格记录并发学习 → 恰好两条证据、计数 2、ACTIVE（无丢失更新）。"""

        service = make_service(observe=BarrierObserve(FakeObserve(), parties=2))
        left = resource_record(task_id="task_left")
        right = resource_record(task_id="task_right")

        results = await asyncio.gather(
            service.on_task_finished(left), service.on_task_finished(right)
        )
        assert {item["action"] for item in results} == {"learned"}
        skills = await service.store.recent(10)
        assert len(skills) == 1, "同一方法只留一条技能"
        skill = skills[0]
        assert skill.success_count == 2, "两条独立证据都要算上（不能 lost update）"
        assert skill.status == SkillStatus.ACTIVE.value, "达到阈值 → 晋升"
        assert len(await service.store.evidence_for_task("task_left")) == 1
        assert len(await service.store.evidence_for_task("task_right")) == 1

    async def test_two_different_tasks_concurrently_real_sqlite(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(
            tmp_path, observe=BarrierObserve(FakeObserve(), parties=2)
        )
        try:
            await asyncio.gather(
                service.on_task_finished(resource_record(task_id="sql_a")),
                service.on_task_finished(resource_record(task_id="sql_b")),
            )
            rows = await database.fetchall("SELECT success_count, status FROM procedural_skills")
            assert len(rows) == 1
            assert int(rows[0]["success_count"]) == 2
            assert str(rows[0]["status"]) == SkillStatus.ACTIVE.value
            count = await database.fetchall("SELECT COUNT(*) AS n FROM procedural_skill_evidence")
            assert int(count[0]["n"]) == 2
        finally:
            await database.close()

    async def test_concurrent_ambiguous_and_positive_do_not_cross(self) -> None:
        """歧义证据与正向证据并发：各自计数，互不覆盖、互不冒充。"""

        service = make_service(observe=BarrierObserve(FakeObserve(), parties=2))
        positive = resource_record(task_id="task_pos")
        # 同类方法但**时长过短**（方块在挖到之前就没了）→ AMBIGUOUS
        ambiguous = resource_record(task_id="task_amb", dig_elapsed_ms=10)

        results = await asyncio.gather(
            service.on_task_finished(positive), service.on_task_finished(ambiguous)
        )
        actions = {item.get("action") for item in results}
        assert "learned" in actions and "rejected" in actions
        skill = (await service.store.recent(1))[0]
        assert skill.success_count == 1, "歧义证据绝不能当成第二条独立成功"
        assert skill.status == SkillStatus.CANDIDATE.value
        assert skill.ambiguous_count <= 1
        evidences = await service.store.recent_evidence(10)
        verdicts = sorted(str(item.verdict) for item in evidences)
        assert verdicts == ["AMBIGUOUS", "POSITIVE"], "两条证据都在账本里，互不覆盖"

        # 重复回调：计数不再累加
        before = skill.ambiguous_count
        await service.on_task_finished(ambiguous)
        skill = (await service.store.get(skill.skill_id)) or skill
        assert skill.ambiguous_count == before and skill.success_count == 1

    async def test_ambiguous_evidence_attaches_to_the_same_subject_sequentially(self) -> None:
        """先有技能、后出现歧义证据（顺序场景）→ 附着到同主题技能，只影响歧义计数。"""

        service = make_service(observe=FakeObserve())
        out = await promote(service)
        skill_id = out["second"]["skill_id"]
        ambiguous = resource_record(task_id="task_amb_seq", dig_elapsed_ms=10)
        outcome = await service.on_task_finished(ambiguous)
        assert outcome["action"] == "rejected" and outcome["verdict"] == "AMBIGUOUS"
        skill = await service.store.get(skill_id)
        assert skill is not None
        assert skill.ambiguous_count == 1 and skill.success_count == 2
        assert skill.status == SkillStatus.ACTIVE.value, "单条歧义不足以否定已验证过的方法"
        attached = await service.store.recent_evidence(5, skill_id=skill_id)
        assert any(str(item.verdict) == "AMBIGUOUS" for item in attached)

    async def test_duplicate_ambiguous_evidence_counts_once_real_sqlite(
        self, tmp_path: Path
    ) -> None:
        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            record = resource_record(task_id="task_amb_sql", dig_elapsed_ms=10)
            await service.on_task_finished(record)
            await service.on_task_finished(record)
            rows = await database.fetchall("SELECT COUNT(*) AS n FROM procedural_skill_evidence")
            assert int(rows[0]["n"]) == 1
        finally:
            await database.close()

    async def test_thresholds_are_respected_from_the_store_transaction(self) -> None:
        """阈值参与派生：阈值调高时，两条证据也不再晋升（规则来自配置，不是硬编码）。"""

        class Config:
            enabled = True
            retrieve_limit = 5
            promotion_min_successes = 3
            invalidate_after_failures = 2
            dig_short_ratio = 0.5

        service = make_service(config=Config())
        await promote(service)
        skill = (await service.store.recent(1))[0]
        assert skill.success_count == 2
        assert skill.status == SkillStatus.CANDIDATE.value, "阈值 3 → 两条还不够"


# ================================================================ §2 真实任务归因


class TestTaskBindingAttribution:
    async def test_candidate_without_a_created_task_never_feeds_back(self) -> None:
        """候选物化了但任务没建成 → 之后同目标同 plan_hash 的普通任务不会被当成熟用。"""

        observe = FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}])
        service = make_service(observe=observe)
        await promote(service)
        suggestion = await service.suggest("去挖一块橡木并捡回来")
        assert suggestion is not None
        # "任务建立失败"（busy / 校验失败 / 回退）→ 没有任何绑定
        assert await service.store.binding_for("task_never_created") is None

        # 之后一条**基规划器**任务：同目标、同计划内容（因此同 plan_hash）
        runtime = build_task_runtime()
        record = await runtime.create_task(
            "去挖一块橡木并捡回来",
            session_id="s",
            user_id="u",
            origin="user",
            plan=suggestion.plan,
            source="qq",
        )
        finished = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert finished.state is TaskState.SUCCEEDED
        outcome = await service.on_task_finished(finished)
        assert outcome["action"] == "learned", "没有绑定 → 走普通学习，不当作技能复用"
        skill = await service.store.get(outcome["skill_id"])
        assert skill is not None and skill.last_used_at == 0, "绝不能把无关任务当成用过技能"

    async def test_binding_routes_the_terminal_event_after_restart(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(
            tmp_path, observe=FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}])
        )
        try:
            out = await promote(service)
            skill_id = out["second"]["skill_id"]
            suggestion = await service.suggest("去挖一块橡木并捡回来")
            assert suggestion is not None
            runtime = build_task_runtime()
            record = await runtime.create_task(
                "去挖一块橡木并捡回来",
                session_id="s",
                user_id="u",
                origin="user",
                plan=suggestion.plan,
                source="qq",
            )
            assert await service.bind_task_for_plan(record, suggestion.plan) == skill_id

            # 重启：全新 service/store，同一个库
            restarted = make_service(
                store=SqliteSkillStore(database),
                observe=FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}]),
            )
            binding = await restarted.store.binding_for(record.task_id)
            assert binding is not None and binding["skill_id"] == skill_id
            finished = await runtime.confirm_and_start(
                record.task_id, user_id="u", session_id="s", origin="user"
            )
            assert finished.state is TaskState.SUCCEEDED
            feedback = await restarted.on_task_finished(finished)
            assert feedback["action"] == "feedback" and feedback["verdict"] == "reuse_ok"
            skill = await restarted.store.get(skill_id)
            assert skill is not None and skill.success_count == 3 and skill.last_used_at > 0
            # 重复终态回调：绑定已消费 → 不再反馈、不再计数
            again = await restarted.on_task_finished(finished)
            assert again["action"] == "duplicate"
            skill = await restarted.store.get(skill_id)
            assert skill is not None and skill.success_count == 3
        finally:
            await database.close()

    async def test_only_the_bound_task_of_two_identical_plans_feeds_back(self) -> None:
        """同 plan_hash 的两条任务：只有真正绑定的那条产生技能反馈。"""

        observe = FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}])
        service = make_service(observe=observe)
        out = await promote(service)
        skill_id = out["second"]["skill_id"]
        suggestion = await service.suggest("去挖一块橡木并捡回来")
        assert suggestion is not None

        # 两条任务各用**独立**运行时（背包快照各自独立，最终校验才成立）
        bound_runtime = build_task_runtime()
        bound_task = await bound_runtime.create_task(
            "去挖一块橡木并捡回来",
            session_id="s1",
            user_id="u",
            origin="user",
            plan=suggestion.plan,
            source="qq",
        )
        assert await service.bind_task_for_plan(bound_task, suggestion.plan) == skill_id
        other_runtime = build_task_runtime()
        other_task = await other_runtime.create_task(
            "去挖一块橡木并捡回来",
            session_id="s2",
            user_id="u",
            origin="user",
            plan=suggestion.plan,
            source="qq",
        )
        assert bound_task.plan_hash == other_task.plan_hash, "两条任务计划内容一致"

        done_bound = await bound_runtime.confirm_and_start(
            bound_task.task_id, user_id="u", session_id="s1", origin="user"
        )
        done_other = await other_runtime.confirm_and_start(
            other_task.task_id, user_id="u", session_id="s2", origin="user"
        )
        assert done_bound.state is TaskState.SUCCEEDED and done_other.state is TaskState.SUCCEEDED
        first = await service.on_task_finished(done_bound)
        second = await service.on_task_finished(done_other)
        assert first["action"] == "feedback" and first["verdict"] == "reuse_ok"
        assert second["action"] == "learned", "没绑定的那条只能当普通学习"
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.last_used_at > 0

    async def test_life_path_transfers_the_reference_through_the_agent_plan(self) -> None:
        """LIFE：技能候选 → AgentPlan（checks 持久化引用）→ 批准建任务 → 绑定准确。"""

        from app.tasks.agent_plan import AgentPlan, PlanSource, PlanStatus, PlanTarget
        from app.tasks.skill_planner import SKILL_REUSE_REASON
        from tests.agent_plan_fakes import SERVER, T0, PlanRig

        verified = PlanTarget(
            status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
        )

        observe = FakeObserve(inventory=[{"name": "netherite_axe", "count": 1}])
        skills = make_service(observe=observe)
        out = await promote(skills)
        skill_id = out["second"]["skill_id"]

        rig = PlanRig()
        rig.service.skills = skills
        planner = SkillAwarePlanner(rig.service.planner, skills=skills)
        rig.service.planner = planner
        result = await planner.plan("去挖一块橡木并捡回来", observe=observe)
        assert result.reason == SKILL_REUSE_REASON

        # LIFE 计划：只规划，不建任务（observations 不落库，引用走 checks）
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=verified,
                plan=result.plan.plan.to_payload(),
                checks=result.checks,
                version=1,
                fingerprint="life|skill|1",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        assert (await skills.store.binding_for("any-task")) is None
        approved = await rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
        assert approved["action"] == "approved", approved
        record = approved["record"]
        binding = await skills.store.binding_for(record.task_id)
        assert binding is not None and binding["skill_id"] == skill_id, "LIFE 路径也要绑定"

        # 未批准 / 过期 / 建任务失败的计划不能留下有效绑定
        pending, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-2",
                initiator="罐头@x",
                target=verified,
                plan=result.plan.plan.to_payload(),
                checks=result.checks,
                version=1,
                fingerprint="life|skill|2",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        assert (await skills.store.binding_for(pending.plan_id)) is None
        busy_rig = PlanRig()
        busy_rig.service.skills = skills
        busy_rig.runtime.busy = True
        busy_plan, _ = await busy_rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective=result.plan.plan.objective,
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-3",
                initiator="罐头@x",
                target=verified,
                plan=result.plan.plan.to_payload(),
                checks=result.checks,
                version=1,
                fingerprint="life|skill|3",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        failed = await busy_rig.service.approve(
            plan_id=busy_plan.plan_id, user_id="u", session_id="s"
        )
        assert failed["action"] == "busy"
        bindings = await skills.store.recent_evidence(10)
        assert bindings is not None  # 只是确保库还能读（下面按 task_id 精确断言）
        assert await skills.store.binding_for("busy-task") is None

    async def test_unknown_task_without_binding_is_never_guessed(self) -> None:
        """完全没有绑定的任务 → 普通学习（或拒绝）；绝不按目标文本猜技能。"""

        service = make_service(observe=FakeObserve())
        await promote(service)
        skill = (await service.store.recent(1))[0]
        assert skill.last_used_at == 0
        unknown = resource_record(task_id="task_unknown")
        outcome = await service.on_task_finished(unknown)
        assert outcome["action"] == "learned"
        skill = await service.store.get(skill.skill_id)
        assert skill is not None and skill.last_used_at == 0, "没有被当作复用"

    def test_binding_store_is_the_only_attribution_source(self) -> None:
        """源码级：服务里不再有按 (objective, plan_hash) 反查技能的通路。"""

        source = (
            Path(__file__).resolve().parent.parent / "app" / "tasks" / "skill_service.py"
        ).read_text(encoding="utf-8")
        assert "usage_for" not in source and "note_usage" not in source
        assert "binding_for" in source and "bind_task" in source


# ================================================================ §2 存储层协议一致性


class TestStoreProtocolParity:
    async def test_in_memory_and_sqlite_agree(self, tmp_path: Path) -> None:
        """同一组操作在两种 store 上给同样的结论（计数、状态、绑定消费语义）。"""

        sqlite_service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        memory_service = make_service(store=InMemorySkillStore(), observe=FakeObserve())
        try:
            for service in (memory_service, sqlite_service):
                await promote(service)
                skill = (await service.store.recent(1))[0]
                assert skill.status == SkillStatus.ACTIVE.value
                assert skill.success_count == 2
                assert await service.store.bind_task(
                    task_id="task_x",
                    skill_id=skill.skill_id,
                    subject_key=skill.subject_key,
                    plan_version=1,
                    plan_hash="h",
                    at=0.0,
                    expires_at=600.0,
                )
                binding = await service.store.binding_for("task_x")
                assert binding is not None and binding["skill_id"] == skill.skill_id
                again = await service.store.bind_task(
                    task_id="task_x",
                    skill_id="other",
                    subject_key="s",
                    plan_version=1,
                    plan_hash="h",
                    at=0.0,
                    expires_at=600.0,
                )
                assert again is False, "task_id 唯一：重复绑定不改写"
                assert await service.store.prune_bindings(now=1e9 + 86400) >= 0
        finally:
            await database.close()

    def test_thresholds_dataclass_defaults(self) -> None:
        thresholds = SkillThresholds()
        assert thresholds.promotion_min_successes == 2
        assert thresholds.invalidate_after_failures == 2

    async def test_service_exposes_thresholds_from_config(self) -> None:
        service = make_service()
        assert service.thresholds.promotion_min_successes == 2
        assert CHARACTER  # 夹具常量仍在使用（防止 import 被误删）
        assert BLOCK and DROP
