"""Phase 7D.1 负面测试矩阵（任务书 §1.3 / §2.3 / §3.3）。

* §1.3：同指纹重复请求 / 旧任务终态或开着 / 并发竞争 / 记账失败补偿 / 崩溃恢复；
* §2.3：身份复核 fail-closed（AgentPlan 缺失、读取异常、UUID 缺失、撤销/换号/跨服、
  username 不一致、取消本身失败）；
* §3.3：过期批准 / handle_qq 过期候选 / 并发批准恰一个成功 / 占用后建任务失败补偿 /
  第二道门拒绝非本人。
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.tasks.agent_plan import (
    AgentPlan,
    PlanSource,
    PlanStatus,
    PlanTarget,
)
from tests.agent_plan_fakes import (
    SERVER,
    T0,
    FakeTaskRuntime,
    PlanRig,
)


def follow_target(uuid: str = "a" * 32, *, name: str = "Rinsora") -> PlanTarget:
    return PlanTarget(status="VERIFIED", server_id=SERVER, player_uuid=uuid, player_name=name)


def follow_plan_payload(name: str = "Rinsora") -> dict[str, Any]:
    return {
        "objective": "跟着我",
        "steps": [
            {
                "step_id": "step_1",
                "tool": "minecraft_follow_player",
                "arguments": {"username": name},
                "risk": "LOW",
            }
        ],
        "observations": [],
    }


# ---------------------------------------------------------------- §1.3 同指纹


class TestSameFingerprintRequests:
    async def test_second_request_yields_when_first_task_is_open(self) -> None:
        """同桶同目标第二次请求：第一份 Task 还开着 → 让路，不建第二个任务。"""
        rig = PlanRig()
        first = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert first["action"] == "created"
        second = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert second["action"] == "already_planned"
        assert second["plan"].plan_id == first["plan"].plan_id
        assert len(rig.runtime.created) == 1, "绝不重复建任务"

    async def test_second_request_after_terminal_task_retries_bounded(self) -> None:
        """第一份 Task 已终态 → 有界重试：新指纹、新任务，旧行不动。"""
        rig = PlanRig()

        class TerminalRuntime(FakeTaskRuntime):
            async def get(self, task_id: str) -> Any:
                return None  # 简化：读不到 = 终态/不存在路径

        rig.runtime = TerminalRuntime()
        rig.service.runtime = rig.runtime
        first = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert first["action"] == "created"
        second = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert second["action"] == "created"
        assert second["plan"].plan_id != first["plan"].plan_id
        assert len(rig.runtime.created) == 2
        rows = await rig.store.recent()
        stems = {row.fingerprint for row in rows}
        assert len(stems) == len(rows), "每份任务一条计划，互不合并"

    async def test_concurrent_requests_only_one_task(self) -> None:
        """两个并发请求竞争同一指纹：恰一个成功建任务，另一个让路。"""
        rig = PlanRig()
        results = await asyncio.gather(
            *[
                rig.service.plan_follow_from_user(
                    objective="跟着我", user_id="u", session_id="s", target=follow_target()
                )
                for _ in range(2)
            ]
        )
        created = [r for r in results if r["action"] == "created"]
        yielded = [r for r in results if r["action"] == "already_planned"]
        assert len(created) == 1, "并发下至多一份任务"
        assert len(yielded) == 1
        assert len(rig.runtime.created) == 1

    async def test_link_persistence_failure_compensates_the_new_task(self) -> None:
        """任务建好但计划关联持久化失败 → 取消该待确认任务（不留孤儿）。"""
        rig = PlanRig()

        class ExplodingStore(rig.store.__class__):  # type: ignore[name-defined]
            async def replace_plan(self, plan: AgentPlan) -> AgentPlan | None:
                raise RuntimeError("db down")

        rig.service.store = ExplodingStore()
        out = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert out["action"] == "create_failed"
        assert out["record"] is not None
        assert rig.runtime.cancelled == [out["record"].task_id]

    async def test_crash_between_reserve_and_link_recovers_as_orphan(self) -> None:
        """reserve 与 link 之间进程中断 → 恢复期孤儿 PLANNING 被补偿为 CANCELLED。"""
        rig = PlanRig()
        # 手工模拟"只完成了 reserve"：一条 PLANNING、无 task_id 的记录
        plan, created = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective="跟着我",
                status=PlanStatus.PLANNING.value,
                initiator="u",
                target=follow_target(),
                plan=follow_plan_payload(),
                version=1,
                fingerprint="crash|reserve",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        assert created and plan.task_id == ""
        summary = await rig.service.recover()
        assert summary["orphans"] == 1
        row = await rig.store.get(plan.plan_id)
        assert row is not None and row.status == PlanStatus.CANCELLED.value
        assert rig.runtime.created == [], "恢复绝不建任务"

    async def test_unexpected_task_state_compensates(self) -> None:
        """create_task 返回 FAILED 记录 → 不算成功，计划补偿为 CANCELLED。"""
        rig = PlanRig()

        class FailedRecord:
            task_id = "T-fail"
            plan_hash = "h"
            state = type("S", (), {"value": "FAILED"})()
            plan = None
            objective = "跟着我"

        class FailingRuntime(FakeTaskRuntime):
            async def create_task(self, objective: str, **kwargs: Any) -> Any:
                self.created.append({"objective": objective})
                return FailedRecord()

            def summary_of(self, record: Any) -> str:
                return ""

        rig.runtime = FailingRuntime()
        rig.service.runtime = rig.runtime
        out = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=follow_target()
        )
        assert out["action"] == "create_failed"
        row = await rig.store.recent()
        assert row[0].status == PlanStatus.CANCELLED.value
        assert "task_unexpected_state" in row[0].reason


# ---------------------------------------------------------------- §2.3 身份复核


class _Identity:
    user_id = "2731431246"
    session_id = "private:2731431246"


class _Step:
    def __init__(self, tool: str, arguments: dict[str, Any] | None = None) -> None:
        self.tool = tool
        self.arguments = arguments or {}


class _PlanObj:
    def __init__(self, steps: list[Any]) -> None:
        self.steps = steps


def _pending_record(task_id: str, steps: list[Any]) -> Any:
    return type(
        "R",
        (),
        {
            "task_id": task_id,
            "state": type("S", (), {"value": "PENDING_CONFIRMATION"})(),
            "plan": _PlanObj(steps),
        },
    )()


class _EntryHarness:
    """只装 _verify_follow_identity 需要的最小入口。"""

    def __init__(
        self,
        *,
        record: Any = None,
        plan: Any = None,
        resolved: Any = None,
        plan_error: bool = False,
        server_id: str = SERVER,
        cancel_fail: bool = False,
        current_error: bool = False,
    ) -> None:
        from app.tasks.qq_entry import QQTaskEntry

        cancelled: list[str] = []

        class Runtime:
            async def current(self, session_id: str | None = None) -> Any:
                if current_error:
                    raise RuntimeError("db down")
                return record

            async def cancel(self, task_id: str, *, reason: str = "") -> Any:
                """模拟**真实**契约（7D.2.1）：成功取消返回带 ``CANCELLED`` 状态的记录。

                真 ``TaskRuntime.cancel()`` 从不返回 ``None`` —— 可取消时返回 CANCELLED，
                已终态时原样返回该记录；所以测试桩也不该再用 ``None`` 冒充成功。
                """
                from app.tasks.models import TaskState

                if cancel_fail:
                    raise RuntimeError("cancel down")
                cancelled.append(task_id)
                return type("R", (), {"task_id": task_id, "state": TaskState.CANCELLED})()

        class Plans:
            async def plan_for_task(self, task_id: str) -> Any:
                if plan_error:
                    raise RuntimeError("plan down")
                return plan

        class Resolved:
            def __init__(self, payload: dict[str, Any]) -> None:
                self.__dict__.update(payload)

        resolved_payload = resolved or {}

        async def resolve(user_id: str, server_id: str = "") -> Any:
            return Resolved(resolved_payload)

        entry = QQTaskEntry.__new__(QQTaskEntry)
        entry.bot = type(
            "B",
            (),
            {
                "agent_plans": Plans(),
                "proposals": type("P", (), {"resolve_target": staticmethod(resolve)})(),
            },
        )()
        entry.runtime = Runtime()
        entry._server_id = lambda: server_id  # type: ignore[method-assign]
        logs: list[str] = []

        class Log:
            def warning(self, *a: Any, **k: Any) -> None:
                logs.append(str(a))

            def exception(self, *a: Any, **k: Any) -> None:
                logs.append(str(a))

            def info(self, *a: Any, **k: Any) -> None:
                logs.append(str(a))

            def debug(self, *a: Any, **k: Any) -> None:
                logs.append(str(a))

        entry._log = Log()  # type: ignore[method-assign]
        entry._cancelled = cancelled  # type: ignore[attr-defined]
        entry._logs = logs  # type: ignore[attr-defined]
        self.entry = entry


class TestIdentityRecheckFailClosed:
    """§2.3：跟随任务缺任何复核依据都拒绝；普通任务放行。"""

    async def test_follow_task_without_agent_plan_is_refused(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "R"})])
        harness = _EntryHarness(record=record, plan=None)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None and "计划关联" in veto
        # 7D.2 §3.3：缺复核依据 → 不只拒绝确认，还要取消待确认任务
        assert harness.entry._cancelled == ["T-1"]

    async def test_plan_lookup_error_is_refused(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "R"})])
        harness = _EntryHarness(record=record, plan=None, plan_error=True)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert harness.entry._cancelled == ["T-1"]

    async def test_current_read_error_is_refused(self) -> None:
        harness = _EntryHarness(record=None, current_error=True)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None, "读不到任务状态绝不放行"

    async def test_missing_target_uuid_is_refused(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "R"})])
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(),  # UUID 缺失
            fingerprint="f1",
        )
        harness = _EntryHarness(record=record, plan=plan)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None and "不完整" in veto
        assert harness.entry._cancelled == ["T-1"]

    @pytest.mark.parametrize(
        "resolved",
        [
            {"status": "REVOKED", "player_uuid": "a" * 32, "server_id": SERVER, "player_name": "R"},
            {
                "status": "CONFLICT",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "R",
            },
            {"status": "MISSING", "player_uuid": "", "server_id": SERVER, "player_name": ""},
            # 换号：VERIFIED 但 UUID 不同
            {
                "status": "VERIFIED",
                "player_uuid": "b" * 32,
                "server_id": SERVER,
                "player_name": "R",
            },
            # 跨服务器：UUID 相同但 server 不同
            {
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": "mc-其它",
                "player_name": "R",
            },
            # username 与冻结参数不一致（同名/别名绝不放行）
            {
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "别的名字",
            },
        ],
    )
    async def test_identity_mismatches_cancel_and_veto(self, resolved: dict[str, Any]) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "Rinsora"})])
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
            fingerprint="f1",
        )
        harness = _EntryHarness(record=record, plan=plan, resolved=resolved)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None, resolved
        assert harness.entry._cancelled == ["T-1"], "身份不一致 → 既有安全路径取消待确认任务"

    async def test_server_unreadable_is_refused(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "Rinsora"})])
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
            fingerprint="f1",
        )
        harness = _EntryHarness(record=record, plan=plan, server_id="")
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None and "服务器" in veto
        assert harness.entry._cancelled == ["T-1"]

    async def test_cancel_failure_still_vetoes(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "Rinsora"})])
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
            fingerprint="f1",
        )
        harness = _EntryHarness(
            record=record,
            plan=plan,
            resolved={
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            cancel_fail=True,
        )
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None and "停不下来" in veto
        assert harness.entry._cancelled == []

    async def test_consistent_identity_passes(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {"username": "Rinsora"})])
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
            fingerprint="f1",
        )
        harness = _EntryHarness(
            record=record,
            plan=plan,
            resolved={
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
        )
        assert await harness.entry._verify_follow_identity(_Identity()) is None

    async def test_non_follow_task_skips_the_recheck(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_dig", {"x": 1, "y": 2, "z": 3})])
        harness = _EntryHarness(record=record, plan=None)
        assert await harness.entry._verify_follow_identity(_Identity()) is None

    async def test_frozen_username_missing_is_refused(self) -> None:
        record = _pending_record("T-1", [_Step("minecraft_follow_player", {})])
        harness = _EntryHarness(record=record, plan=None)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None and "目标玩家" in veto
        assert harness.entry._cancelled == ["T-1"]


# ---------------------------------------------------------------- §3.3 批准


class TestApprovalGuardrails:
    async def test_expired_plan_refused_without_task(self) -> None:
        """expires_at 已过但 expire_due 还没跑 → 批准拒绝、计划置 EXPIRED、零 Task。"""
        rig = PlanRig()
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|1",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        rig.advance(601)
        out = await rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
        assert out["action"] == "not_approvable"
        row = await rig.store.get(stored.plan_id)
        assert row is not None and row.status == PlanStatus.EXPIRED.value
        assert rig.runtime.created == []

    async def test_handle_qq_skips_expired_candidates(self) -> None:
        """handle_qq 面对已过期但状态还是 READY 的计划 → 不批准、不建 Task。"""
        rig = PlanRig()
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|2",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        rig.advance(601)
        out = await rig.service.handle_qq(text="批准", user_id="u", session_id="s")
        assert out is None, "过期候选不会被「批准」选中"
        row = await rig.store.get(stored.plan_id)
        assert row is not None and row.status == PlanStatus.READY_FOR_APPROVAL.value
        assert rig.runtime.created == []

    async def test_concurrent_approval_creates_at_most_one_task(self) -> None:
        """两个并发批准：恰好一个占用成功并建 Task，另一个被 CAS 拒绝。"""
        rig = PlanRig()
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|3",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        results = await asyncio.gather(
            *[
                rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
                for _ in range(2)
            ]
        )
        approved = [r for r in results if r["action"] == "approved"]
        refused = [r for r in results if r["action"] == "not_approvable"]
        assert len(approved) == 1 and len(refused) == 1
        assert len(rig.runtime.created) == 1

    async def test_task_create_failure_finalizes_the_occupation(self) -> None:
        """占用后 create_task 抛异常 → 计划落到 CANCELLED（不留 APPROVED 半完成）。"""
        rig = PlanRig()

        class BusyRuntime(FakeTaskRuntime):
            async def create_task(self, objective: str, **kwargs: Any) -> Any:
                from app.tasks.runtime import TaskBusy

                raise TaskBusy(type("R", (), {"task_id": "other"})())

        rig.runtime = BusyRuntime()
        rig.service.runtime = rig.runtime
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|4",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        out = await rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
        assert out["action"] == "busy"
        row = await rig.store.get(stored.plan_id)
        assert row is not None and row.status == PlanStatus.CANCELLED.value
        assert "task_create_failed" in row.reason

    async def test_unexpected_task_state_finalizes_the_occupation(self) -> None:
        """create_task 返回 FAILED 记录 → 不宣告批准完成，占用被安全终结。"""
        rig = PlanRig()

        class FailedRecord:
            task_id = "T-f"
            plan_hash = "h"
            state = type("S", (), {"value": "FAILED"})()
            plan = None
            objective = "想去 Minecraft 收集一点橡木"

        class FailingRuntime(FakeTaskRuntime):
            async def create_task(self, objective: str, **kwargs: Any) -> Any:
                self.created.append({"objective": objective})
                return FailedRecord()

            def summary_of(self, record: Any) -> str:
                return ""

        rig.runtime = FailingRuntime()
        rig.service.runtime = rig.runtime
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|5",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        out = await rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
        assert out["action"] == "create_failed"
        row = await rig.store.get(stored.plan_id)
        assert row is not None and row.status == PlanStatus.CANCELLED.value

    async def test_second_gate_refuses_a_different_session(self) -> None:
        """第二道确认由不同会话发出 → 既有确认门拒绝（零动作由 TaskRuntime 保证）。"""
        from app.tasks.models import TaskState

        rig = PlanRig()
        stored, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                proposal_id="TP-1",
                initiator="罐头@x",
                target=PlanTarget(),
                plan={
                    "objective": "想去 Minecraft 收集一点橡木",
                    "steps": [
                        {
                            "step_id": "step_1",
                            "tool": "minecraft_dig",
                            "arguments": {"x": 1, "y": 70, "z": 1},
                            "risk": "MEDIUM",
                        }
                    ],
                },
                fingerprint="life|6",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        out = await rig.service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
        assert out["action"] == "approved"
        record = out["record"]
        assert record.session_id == "s"
        # 第二道门：既有 confirm_and_start 的校验（wrong owner/session）—— 语义不在这里重测，
        # 但批准返回的 record 必须携带批准者的 session 供它校验。
        assert record.state.value == TaskState.PENDING_CONFIRMATION.value
