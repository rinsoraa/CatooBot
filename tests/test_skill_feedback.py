"""Phase 7E.1.1 回归：反馈资格（§1）与证据列/payload 一致（§2）。

§1：技能绑定在任务进入 ``PENDING_CONFIRMATION`` 时就建立，所以"没开始执行"的任务终态
（等确认被取消 / 确认过期 / 未跑就失败）**不是方法反例**；只有**可归因的执行失败证据**
（运行时自己的后验校验失败 = 方法跑了但预期结果没达成）才允许推进 STALE/INVALIDATED。
不合格的反馈仍然**幂等消费**掉绑定并留原因码（重复终态事件不会稍后再来计一次分）。

§2：SQLite 证据行的 ``skill_id`` 列与 JSON payload 必须一致（写入同事务 + 迁移 36 回填历史）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.models import PlanStatus, StepState, TaskFailure, TaskState
from app.tasks.skill import EvidenceVerdict, SkillStatus, SkillThresholds
from app.tasks.skill_service import SkillService
from app.tasks.skill_store import InMemorySkillStore, SqliteSkillStore
from tests.skill_fakes import (
    DROP,
    FakeObserve,
    make_service,
    resource_record,
)
from tests.test_skill_service import promote

ATTRIBUTABLE = TaskFailure.VERIFICATION.value


async def make_sqlite_service(
    tmp_path: Path, *, observe: Any = None, store: Any = None
) -> tuple[SkillService, Database]:
    database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'feedback.db'}"))
    await database.connect()
    service = make_service(store=store or SqliteSkillStore(database), observe=observe)
    return service, database


async def bound(service: SkillService, skill_id: str, task_id: str, **kwargs: Any) -> Any:
    """一条"由该技能物化、并且真的建立过"的任务记录（绑定是归因的唯一依据）。"""

    record = resource_record(task_id=task_id, **kwargs)
    skill = await service.store.get(skill_id)
    assert skill is not None
    assert await service.store.bind_task(
        task_id=record.task_id,
        skill_id=skill_id,
        subject_key=str(skill.subject_key),
        plan_version=record.plan_version,
        plan_hash=record.plan_hash,
        at=record.created_at,
        expires_at=record.created_at + 600,
    )
    return record


async def active_service(**kwargs: Any) -> tuple[SkillService, str]:
    service = make_service(**kwargs)
    out = await promote(service)
    return service, out["second"]["skill_id"]


# ================================================================ §1 反馈资格


class TestFeedbackEligibility:
    async def test_cancel_while_pending_confirmation_is_not_a_counterexample(self) -> None:
        """等确认时用户取消 → 可审计但不计反例，技能仍 ACTIVE。"""

        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_pending_cancel",
            state=TaskState.CANCELLED,
            verification={},
            never_started=True,
            plan_status=PlanStatus.PENDING_CONFIRMATION.value,
        )
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback"
        assert outcome["verdict"] != "stale" and outcome["verdict"] != "invalidated"
        skill = await service.store.get(skill_id)
        assert skill is not None
        assert skill.failure_count == 0, "没执行过的任务不能算反例"
        assert skill.status == SkillStatus.ACTIVE.value
        rows = await service.store.recent_evidence(5, skill_id=skill_id)
        assert rows and str(rows[0].verdict) == EvidenceVerdict.REJECTED.value
        assert rows[0].reason_code == "skill_task_not_started"

    async def test_expired_before_any_step_is_not_a_counterexample(self) -> None:
        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_expired_nostart",
            state=TaskState.EXPIRED,
            verification={},
            never_started=True,
        )
        await service.on_task_finished(record)
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0
        assert skill.status == SkillStatus.ACTIVE.value
        rows = await service.store.recent_evidence(5, skill_id=skill_id)
        assert rows[0].reason_code == "skill_task_not_started"

    async def test_failed_before_any_step_is_not_a_counterexample(self) -> None:
        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_failed_nostart",
            state=TaskState.FAILED,
            verification={},
            never_started=True,
            failure=TaskFailure.BUSY.value,
        )
        await service.on_task_finished(record)
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0
        assert skill.status == SkillStatus.ACTIVE.value

    async def test_repeated_terminal_event_consumes_the_binding_once(self) -> None:
        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_pending_cancel_2",
            state=TaskState.CANCELLED,
            verification={},
            never_started=True,
        )
        first = await service.on_task_finished(record)
        second = await service.on_task_finished(record)
        third = await service.on_task_finished(
            resource_record(
                task_id="task_pending_cancel_2",
                state=TaskState.CANCELLED,
                verification={},
                never_started=True,
            )
        )
        assert first["action"] == "feedback"
        assert second["action"] == "duplicate" and third["action"] == "duplicate"
        binding = await service.store.binding_for("task_pending_cancel_2")
        assert binding is not None and float(binding["consumed_at"]) > 0, "绑定只能消费一次"
        rows = await service.store.recent_evidence(10, skill_id=skill_id)
        assert len([item for item in rows if item.task_id == "task_pending_cancel_2"]) == 1
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0

    async def test_attributable_execution_failure_is_a_counterexample(self) -> None:
        """任务跑了、后验校验失败（方法没达成预期效果）→ 这才是方法反例。"""

        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_verify_fail",
            state=TaskState.FAILED,
            failure=ATTRIBUTABLE,
            verification={
                "checked": True,
                "ok": False,
                "inventory_delta": {DROP: 0},
                "expected": {DROP: 1},
                "message": "背包最终状态与预期不符",
            },
        )
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback" and outcome["verdict"] == "stale"
        skill = await service.store.get(skill_id)
        assert skill is not None
        assert skill.failure_count == 1 and skill.status == SkillStatus.STALE.value

    async def test_second_attributable_failure_invalidates(self) -> None:
        service, skill_id = await active_service(observe=FakeObserve())
        for index in (1, 2):
            record = await bound(
                service,
                skill_id,
                f"task_verify_fail_{index}",
                state=TaskState.FAILED,
                failure=ATTRIBUTABLE,
                verification={"checked": True, "ok": False, "inventory_delta": {DROP: 0}},
            )
            outcome = await service.on_task_finished(record)
        assert outcome["verdict"] == "invalidated"
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.status == SkillStatus.INVALIDATED.value
        assert await service.candidates_for("去挖一块橡木并捡回来") == []

    async def test_user_cancel_after_starting_is_not_inferred_as_method_failure(self) -> None:
        """跑了一半被用户取消（哪怕有步骤成功）→ 不因 CANCELLED 推断方法失败。"""

        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_user_cancel",
            state=TaskState.CANCELLED,
            verification={},
            step_states={
                "step_5": StepState.CANCELLED,
                "step_6": StepState.PENDING,
            },
        )
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback"
        assert outcome["verdict"] not in {"stale", "invalidated"}
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0
        assert skill.status == SkillStatus.ACTIVE.value
        rows = await service.store.recent_evidence(5, skill_id=skill_id)
        assert rows[0].reason_code == "user_or_external_abort"

    async def test_timeout_or_external_failure_is_not_attributed(self) -> None:
        """外部环境不确定（超时/离线/世界变了）→ 不惩罚技能。"""

        service, skill_id = await active_service(observe=FakeObserve())
        for index, failure in enumerate(
            (TaskFailure.TIMEOUT.value, TaskFailure.OFFLINE.value, TaskFailure.WORLD_CHANGED.value),
            start=1,
        ):
            record = await bound(
                service,
                skill_id,
                f"task_external_{index}",
                state=TaskState.FAILED,
                failure=failure,
                verification={"checked": False},
            )
            await service.on_task_finished(record)
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0
        assert skill.status == SkillStatus.ACTIVE.value

    async def test_missing_step_evidence_is_indeterminate(self) -> None:
        """步骤状态缺失 / 无法判定是否执行 → 保守不计分，并留原因码。"""

        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service, skill_id, "task_indeterminate", state=TaskState.FAILED, verification={}
        )
        record.steps[2].state = "NOT_A_STATE"  # type: ignore[assignment]
        record.state = "NOT_A_STATE"  # type: ignore[assignment]
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback"
        assert outcome["verdict"] == "indeterminate"
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 0 and skill.success_count == 2
        rows = await service.store.recent_evidence(5, skill_id=skill_id)
        assert rows[0].reason_code == "unknown_task_state"

    async def test_success_without_verifiable_postcondition_adds_no_positive_evidence(self) -> None:
        """SUCCEEDED 也不能跳过资格：后验不可核验 → 不计正向（只消费绑定）。"""

        service, skill_id = await active_service(observe=FakeObserve())
        record = await bound(
            service,
            skill_id,
            "task_success_unverified",
            state=TaskState.SUCCEEDED,
            verification={"checked": False},
        )
        # 最终计划未标记完成 → 不可核验
        record.plan_history[-1].status = PlanStatus.PENDING_CONFIRMATION.value
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback"
        assert outcome["verdict"] == "indeterminate"
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.success_count == 2, "不可核验的成功不新增正向证据"
        rows = await service.store.recent_evidence(5, skill_id=skill_id)
        assert rows[0].verdict == EvidenceVerdict.REJECTED.value
        assert rows[0].reason_code in {"unverified_success", "final_plan_not_completed"}

    async def test_in_memory_and_sqlite_agree_on_the_same_records(self, tmp_path: Path) -> None:
        sqlite_service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        memory_service = make_service(store=InMemorySkillStore(), observe=FakeObserve())
        try:
            for service in (memory_service, sqlite_service):
                out = await promote(service)
                skill_id = out["second"]["skill_id"]
                # 未启动就取消 → 不计反例
                await service.on_task_finished(
                    await bound(
                        service,
                        skill_id,
                        "task_ns",
                        state=TaskState.CANCELLED,
                        verification={},
                        never_started=True,
                    )
                )
                # 可归因失败 → 反例
                await service.on_task_finished(
                    await bound(
                        service,
                        skill_id,
                        "task_attr",
                        state=TaskState.FAILED,
                        failure=ATTRIBUTABLE,
                        verification={"checked": True, "ok": False, "inventory_delta": {DROP: 0}},
                    )
                )
                skill = await service.store.get(skill_id)
                assert skill is not None
                assert (skill.success_count, skill.failure_count) == (2, 1)
                assert skill.status == SkillStatus.STALE.value
                verdicts = sorted(
                    str(item.verdict)
                    for item in await service.store.recent_evidence(10, skill_id=skill_id)
                )
                assert verdicts == ["COUNTEREXAMPLE", "POSITIVE", "POSITIVE", "REJECTED"]
        finally:
            await database.close()


# ================================================================ 执行证据的严格判定


VERIFICATION_FAIL = {
    "checked": True,
    "ok": False,
    "inventory_delta": {DROP: 0},
    "expected": {DROP: 1},
    "message": "背包最终状态与预期不符",
}


def verification_failure(task_id: str, **kwargs: Any) -> Any:
    """一条 "FAILED + 运行时后验校验失败" 的任务（步骤执行痕迹由调用方控制）。"""

    return resource_record(
        task_id=task_id,
        state=TaskState.FAILED,
        failure=TaskFailure.VERIFICATION.value,
        verification=VERIFICATION_FAIL,
        **kwargs,
    )


def strip_execution_traces(record: Any, *, states: dict[str, Any] | None = None) -> Any:
    """清掉所有"确实执行过"的痕迹（时间戳 / 动作 id / 调用状态 / 结果），可指定步骤状态。

    这样每一条用例都只保留**状态本身**，用于验证"状态 != 执行证明"。
    """

    wanted = dict(states or {})
    for step in record.steps:
        step.started_at = None
        step.finished_at = None
        step.action_id = ""
        step.status = ""
        step.result = {}
        step.summary = ""
        # 默认把所有步骤压成 PENDING（"仅剩状态本身"），用例可以指定少数步骤的状态
        step.state = wanted.get(step.step_id, StepState.PENDING)
    return record


class TestStrictStepExecutionEvidence:
    """§1/§2：只有**源码可证明**的持久化事实才算"这一步真的开始执行"。

    TaskRuntime 的写入不变量（`app/tasks/runtime.py`）：

    * ``_run_step`` 在**调用工具之前一行**同时写 ``step.state = RUNNING`` 与
      ``step.started_at = clock()``（runtime.py:579-580）；调用循环结束后写
      ``finished_at``（runtime.py:608）；
    * ``_settle`` 只在拿到调用结果之后写 ``status`` / ``result`` / ``action_id`` /
      ``SUCCEEDED`` / ``WAITING_ACTION``（:613-634）；``on_action_event`` 同理（:1076-1087）；
    * ``FAILED`` **还有两处调用前分支**（引用解析失败 :515、参数校验失败 :525）以及恢复/取消
      路径（:775/:928/:1188/:1229）→ 单有 ``FAILED`` / ``CANCELLED`` 不能证明跑过；
    * ``SKIPPED`` / ``WAITING_CONFIRMATION`` 在整个 runtime 里**从不被写入**
      （只在 models.py 的终态集合与进度统计里出现）→ 它们更不能当执行证明。
      这些组合在正常运行时不可达；即使不可达也照样按"不可信"判定（fail-closed）。
    """

    @pytest.mark.parametrize(
        "label,states",
        [
            ("all PENDING", {}),
            ("one SKIPPED", {"step_3": StepState.SKIPPED}),
            ("one WAITING_CONFIRMATION", {"step_3": StepState.WAITING_CONFIRMATION}),
            ("one FAILED (pre-call branch)", {"step_3": StepState.FAILED}),
            ("one CANCELLED", {"step_3": StepState.CANCELLED}),
        ],
    )
    async def test_state_alone_is_not_execution_proof(
        self, label: str, states: dict[str, Any]
    ) -> None:
        """后验失败条件齐全，但没有任何执行痕迹 → 绝不判反例。"""

        service = make_service(observe=FakeObserve())
        out = await promote(service)
        skill_id = out["second"]["skill_id"]
        record = strip_execution_traces(
            await bound(service, skill_id, f"task_no_trace_{abs(hash(label)) % 1000}", **{}),
            states=states,
        )
        # 让这条记录带上"后验失败"的全部条件（步骤状态与痕迹已按用例清空）
        record.state = TaskState.FAILED
        record.failure = TaskFailure.VERIFICATION.value
        record.verification = dict(VERIFICATION_FAIL)
        outcome = await service.on_task_finished(record)
        assert outcome["action"] == "feedback", label
        assert outcome["verdict"] == "indeterminate", label
        skill = await service.store.get(skill_id)
        assert skill is not None
        assert skill.failure_count == 0, f"{label}: 状态不是执行证明"
        assert skill.status == SkillStatus.ACTIVE.value, label
        rows = await service.store.recent_evidence(3, skill_id=skill_id)
        assert str(rows[0].verdict) == EvidenceVerdict.REJECTED.value, label
        assert rows[0].reason_code == "skill_task_not_started", label

    async def test_zero_started_at_counts_as_started(self) -> None:
        """``started_at = 0`` 是**存在**的时间戳（该字段用 None 表示未设置），不能被 falsy 漏掉。"""

        from app.tasks.skill_learning import step_ran

        record = verification_failure("task_zero_stamp")
        strip_execution_traces(record)
        record.steps[2].started_at = 0  # 仓库时钟语义：0 是"设过的值"，未设置是 None
        assert step_ran(record.steps[2]) is True

        service = make_service(observe=FakeObserve())
        out = await promote(service)
        skill_id = out["second"]["skill_id"]
        record = strip_execution_traces(await bound(service, skill_id, "task_zero_stamp"))
        record.state = TaskState.FAILED
        record.failure = TaskFailure.VERIFICATION.value
        record.verification = dict(VERIFICATION_FAIL)
        record.steps[2].started_at = 0
        outcome = await service.on_task_finished(record)
        assert outcome["verdict"] == "stale", "有执行证据 → 反例成立"
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 1

    @pytest.mark.parametrize(
        "label,evidence",
        [
            ("action_id only", {"action_id": "act_1"}),
            ("finished_at only", {"finished_at": 1.0}),
            ("status only", {"status": "SUCCEEDED"}),
            ("result only", {"result": {"ok": True}}),
            ("state RUNNING only", {"state": StepState.RUNNING}),
            ("state SUCCEEDED only", {"state": StepState.SUCCEEDED}),
            ("state WAITING_ACTION only", {"state": StepState.WAITING_ACTION}),
        ],
    )
    async def test_source_proven_evidence_still_counts(
        self, label: str, evidence: dict[str, Any]
    ) -> None:
        """正向控制：被源码证明过的执行痕迹 + 同一套后验失败条件 → 仍是反例。"""

        from app.tasks.skill_learning import step_ran

        service = make_service(observe=FakeObserve())
        out = await promote(service)
        skill_id = out["second"]["skill_id"]
        record = strip_execution_traces(
            await bound(service, skill_id, f"task_evidence_{label[:6]}")
        )
        for key, value in evidence.items():
            setattr(record.steps[3], key, value)
        assert step_ran(record.steps[3]) is True, label
        record.state = TaskState.FAILED
        record.failure = TaskFailure.VERIFICATION.value
        record.verification = dict(VERIFICATION_FAIL)
        outcome = await service.on_task_finished(record)
        assert outcome["verdict"] == "stale", label
        skill = await service.store.get(skill_id)
        assert skill is not None and skill.failure_count == 1, label

    async def test_disqualified_feedback_is_consumed_once_without_scoring(
        self, tmp_path: Path
    ) -> None:
        """服务级回归：不合格反馈仍然幂等消费绑定、保留原因码，但计数/状态零变化。"""

        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            out = await promote(service)
            skill_id = out["second"]["skill_id"]
            record = strip_execution_traces(
                await bound(service, skill_id, "task_disqualified"),
                states={"step_3": StepState.SKIPPED},
            )
            record.state = TaskState.FAILED
            record.failure = TaskFailure.VERIFICATION.value
            record.verification = dict(VERIFICATION_FAIL)
            first = await service.on_task_finished(record)
            second = await service.on_task_finished(record)
            assert first["action"] == "feedback" and first["verdict"] == "indeterminate"
            assert second["action"] == "duplicate", "绑定只消费一次，重复终态不再计分"
            binding = await service.store.binding_for("task_disqualified")
            assert binding is not None and float(binding["consumed_at"]) > 0
            skill = await service.store.get(skill_id)
            assert skill is not None
            assert (skill.success_count, skill.failure_count, skill.ambiguous_count) == (2, 0, 0)
            assert skill.status == SkillStatus.ACTIVE.value
            rows = await service.store.recent_evidence(3, skill_id=skill_id)
            assert len([item for item in rows if item.task_id == "task_disqualified"]) == 1
            assert rows[0].reason_code == "skill_task_not_started"
        finally:
            await database.close()

    async def test_parity_for_disqualified_feedback(self, tmp_path: Path) -> None:
        sqlite_service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        memory_service = make_service(store=InMemorySkillStore(), observe=FakeObserve())
        try:
            for service in (memory_service, sqlite_service):
                out = await promote(service)
                skill_id = out["second"]["skill_id"]
                record = strip_execution_traces(
                    await bound(service, skill_id, "task_parity"),
                    states={"step_5": StepState.CANCELLED},
                )
                record.state = TaskState.FAILED
                record.failure = TaskFailure.VERIFICATION.value
                record.verification = dict(VERIFICATION_FAIL)
                outcome = await service.on_task_finished(record)
                skill = await service.store.get(skill_id)
                assert outcome["verdict"] == "indeterminate"
                assert skill is not None
                assert (skill.failure_count, skill.status) == (0, SkillStatus.ACTIVE.value)
        finally:
            await database.close()


# ================================================================ §2 证据投影一致


class TestEvidenceProjectionConsistency:
    async def test_new_evidence_column_and_payload_agree(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            out = await promote(service)
            skill_id = out["second"]["skill_id"]
            rows = await database.fetchall(
                "SELECT skill_id, payload FROM procedural_skill_evidence WHERE verdict = 'POSITIVE'"
            )
            assert rows
            for row in rows:
                payload = json.loads(row["payload"])
                assert row["skill_id"] == skill_id
                assert payload["skill_id"] == row["skill_id"], "列与 payload 必须一致"
        finally:
            await database.close()

    async def test_restart_reads_the_same_attribution(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            out = await promote(service)
            skill_id = out["second"]["skill_id"]
            restarted = make_service(store=SqliteSkillStore(database), observe=FakeObserve())
            rows = await restarted.store.recent_evidence(10, skill_id=skill_id)
            assert rows and all(item.skill_id == skill_id for item in rows)
        finally:
            await database.close()

    async def test_duplicate_claim_returns_consistent_attribution(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            out = await promote(service)
            skill_id = out["second"]["skill_id"]
            record = resource_record(task_id="task_dup_proj")
            first = await service.on_task_finished(record)
            again = await service.on_task_finished(record)
            assert again["action"] == "duplicate"
            rows = await service.store.recent_evidence(10, skill_id=skill_id)
            dup = [item for item in rows if item.task_id == "task_dup_proj"]
            assert len(dup) == 1 and dup[0].skill_id == first["skill_id"]
            skill = await service.store.get(first["skill_id"])
            assert skill is not None and skill.success_count == 3, "重复认领不改计数"
        finally:
            await database.close()

    async def test_unattached_evidence_keeps_empty_skill_id(self, tmp_path: Path) -> None:
        service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        try:
            # 一个**没有**任何技能的库：歧义证据没有可附着的对象
            record = resource_record(task_id="task_lonely_amb", dig_elapsed_ms=5)
            outcome = await service.on_task_finished(record)
            assert outcome["action"] == "rejected" and outcome["verdict"] == "AMBIGUOUS"
            rows = await database.fetchall(
                "SELECT skill_id, payload FROM procedural_skill_evidence WHERE task_id = ?",
                ("task_lonely_amb",),
            )
            assert len(rows) == 1
            assert rows[0]["skill_id"] == ""
            payload = json.loads(rows[0]["payload"])
            assert payload.get("skill_id", "") == "", "无归属证据保持空归属（列与 payload 一致）"
        finally:
            await database.close()

    async def test_v35_to_v36_upgrade_repairs_payloads(self, tmp_path: Path) -> None:
        """迁移 36：把历史证据的 payload.skill_id 按列回填，保留所有既有字段与非技能数据。"""

        url = f"sqlite:///{tmp_path / 'upgrade36.db'}"
        database = Database(DatabaseConfig(url=url))
        await database.connect()
        # 造一条"列有值、payload 没有"的历史证据（7E.1 的写入缺陷形态）
        await database.execute(
            "INSERT INTO procedural_skills (skill_id, fingerprint, status, character_id,"
            " server_id, subject_key, created_at, updated_at, payload) VALUES"
            " ('SK-1', 'fp-1', 'ACTIVE', 'c', 's', 'subj', 1, 1, '{\"skill_id\": \"SK-1\"}')"
        )
        stale_payload = json.dumps(
            {
                "evidence_id": "EV-old",
                "skill_id": "",
                "subject_key": "subj",
                "task_id": "task-old",
                "verdict": "POSITIVE",
                "reason_code": "QUALIFIED",
            }
        )
        await database.execute(
            "INSERT INTO procedural_skill_evidence (evidence_id, skill_id, subject_key, task_id,"
            " verdict, created_at, payload) VALUES ('EV-old', 'SK-1', 'subj', 'task-old',"
            " 'POSITIVE', 5, ?)",
            (stale_payload,),
        )
        unattached = json.dumps({"evidence_id": "EV-amb", "skill_id": "", "verdict": "AMBIGUOUS"})
        await database.execute(
            "INSERT INTO procedural_skill_evidence (evidence_id, skill_id, subject_key, task_id,"
            " verdict, created_at, payload) VALUES ('EV-amb', '', 'subj2', 'task-amb',"
            " 'AMBIGUOUS', 6, ?)",
            (unattached,),
        )
        await database.execute(
            "INSERT INTO memories (scope_key, category, content, content_hash, created_at,"
            " updated_at) VALUES ('character:x', 'fact', '既有数据', 'h1', 1, 1)"
        )
        # 退化成 v35：删掉迁移记录，让 36 重跑
        await database.execute("DELETE FROM schema_migrations WHERE version >= 36")
        await database.close()

        upgraded = Database(DatabaseConfig(url=url))
        await upgraded.connect()
        version = await upgraded.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        assert int((version or {}).get("v") or 0) == 36
        row = await upgraded.fetchone(
            "SELECT skill_id, payload, verdict, reason_code, task_id, created_at"
            " FROM procedural_skill_evidence WHERE evidence_id = 'EV-old'"
        )
        assert row is not None
        payload = json.loads(row["payload"])
        assert payload["skill_id"] == row["skill_id"] == "SK-1", "历史 payload 被回填"
        assert payload["verdict"] == "POSITIVE" and payload["task_id"] == "task-old"
        assert payload["reason_code"] == "QUALIFIED", "既有字段全部保留"
        assert float(row["created_at"]) == 5.0
        lone = await upgraded.fetchone(
            "SELECT skill_id, payload FROM procedural_skill_evidence WHERE evidence_id = 'EV-amb'"
        )
        assert lone is not None
        assert lone["skill_id"] == "" and json.loads(lone["payload"])["skill_id"] == ""
        kept = await upgraded.fetchall("SELECT COUNT(*) AS n FROM memories")
        assert int(kept[0]["n"]) == 1, "非技能数据不受影响"
        await upgraded.close()

        # 再连一次：迁移幂等（版本仍是 36，数据不变）
        again = Database(DatabaseConfig(url=url))
        await again.connect()
        version = await again.fetchone("SELECT MAX(version) AS v FROM schema_migrations")
        assert int((version or {}).get("v") or 0) == 36
        row = await again.fetchone(
            "SELECT skill_id, payload FROM procedural_skill_evidence WHERE evidence_id = 'EV-old'"
        )
        assert row is not None and json.loads(row["payload"])["skill_id"] == "SK-1"
        await again.close()

    async def test_store_read_contract_parity(self, tmp_path: Path) -> None:
        sqlite_service, database = await make_sqlite_service(tmp_path, observe=FakeObserve())
        memory_service = make_service(store=InMemorySkillStore(), observe=FakeObserve())
        try:
            for service in (memory_service, sqlite_service):
                out = await promote(service)
                skill_id = out["second"]["skill_id"]
                rows = await service.store.recent_evidence(5, skill_id=skill_id)
                assert rows and all(
                    item.skill_id == skill_id and item.verdict == EvidenceVerdict.POSITIVE.value
                    for item in rows
                )
        finally:
            await database.close()

    def test_thresholds_are_still_config_driven(self) -> None:
        thresholds = SkillThresholds(promotion_min_successes=3, invalidate_after_failures=3)
        assert thresholds.promotion_min_successes == 3
        assert Path(__file__).exists()
