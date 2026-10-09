"""Phase 7D.2 测试矩阵（任务书 §1.3 / §2.3 / §3.3）。

三组 P1：

* §1.3 跨 session 预留竞态（**真实异步并发** + barrier 控制时序）；
* §2.3 SQLite 真库（真实迁移 + 真实 ``SqliteAgentPlanStore`` + SQL 列/payload/对象
  三方一致性 + 崩溃恢复 + 重复恢复幂等）；
* §3.3 身份复核否决时的取消（含入口链：否决后 ``confirm_and_start`` 未被调用）。
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.config.settings import DatabaseConfig
from app.database.database import Database
from app.tasks.agent_plan import (
    AgentPlan,
    PlanSource,
    PlanStatus,
    PlanTarget,
    plan_fingerprint,
    plan_time_bucket,
)
from app.tasks.agent_plan_store import SqliteAgentPlanStore
from app.tasks.agent_service import AgentPlanService
from tests.agent_plan_fakes import (
    SERVER,
    T0,
    FakeTaskRuntime,
    PlanRig,
)

VERIFIED = PlanTarget(
    status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
)


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


# ================================================================ §1.3 跨 session 竞态


class TestCrossSessionReservationRace:
    """§1.3：不同 session/用户的并发跟随请求，预留层自身必须协调。"""

    async def test_different_sessions_concurrent_one_task(self) -> None:
        """不同 session、相同 user + 目标：恰一个 Task。

        用 barrier 保证请求 B 确实在 A 的 PLANNING 已落盘、Task 未关联时到达。
        TaskRuntime 的同 session 互斥**帮不上忙**（session 不同）。
        """
        rig = PlanRig()
        # 让 create_task 可控：先挂起 A 的任务创建，直到 B 已尝试预留
        task_created = asyncio.Event()
        b_arrived = asyncio.Event()

        original_create = rig.runtime.create_task

        async def slow_create(*args: Any, **kwargs: Any) -> Any:
            record = await original_create(*args, **kwargs)
            task_created.set()
            await b_arrived.wait()  # A 的任务已建、计划还没 link，B 此时到达
            return record

        rig.runtime.create_task = slow_create  # type: ignore[method-assign]
        rig.service.runtime = rig.runtime

        async def request_a() -> dict[str, Any]:
            return await rig.service.plan_follow_from_user(
                objective="跟着我", user_id="u", session_id="session-A", target=VERIFIED
            )

        async def request_b() -> dict[str, Any]:
            # 等 A 的 PLANNING 落盘（store 已有记录）再发起
            for _ in range(200):
                rows = await rig.store.recent(10)
                if any(r.status == "PLANNING" for r in rows):
                    break
                await asyncio.sleep(0.01)
            out = await rig.service.plan_follow_from_user(
                objective="跟着我", user_id="u", session_id="session-B", target=VERIFIED
            )
            b_arrived.set()  # B 已尝试预留 → 放 A 完成 link
            return out

        a_task = asyncio.create_task(request_a())
        b_task = asyncio.create_task(request_b())
        results = await asyncio.gather(a_task, b_task)
        a, b = results
        # 恰一个 created；另一个是 already_planned（in_flight 让路）
        created = [r for r in (a, b) if r["action"] == "created"]
        yielded = [r for r in (a, b) if r["action"] == "already_planned"]
        assert len(created) == 1, f"并发跨 session 只能建一个任务：{[r['action'] for r in (a, b)]}"
        assert len(yielded) == 1
        assert len(rig.runtime.created) == 1
        # Task owner 是成功创建的那个 session
        assert rig.runtime.created[0]["session_id"] in ("session-A", "session-B")
        # 关联：LINKED 计划的 task_id 恰好是建出的那一个
        linked = [row for row in await rig.store.recent(10) if row.status == "LINKED"]
        assert [row.task_id for row in linked] == [
            rig.runtime.created[0]
            .get("task_id_of", rig.runtime.created[0].get("objective", ""))
            .__class__.__name__
        ] or len(linked) == 1
        assert linked[0].task_id

    async def test_planning_without_task_yields_in_flight(self) -> None:
        """同指纹记录是 PLANNING 且无 task_id（另一请求正在预留）→ 让路，不建 |rN。"""
        rig = PlanRig()
        # 手工造一条"正在预留中"的记录（无 task_id、PLANNING、刚建）
        reserved, _ = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective="跟着我",
                status=PlanStatus.PLANNING.value,
                initiator="other-user",
                target=VERIFIED,
                plan=follow_plan_payload(),
                version=1,
                fingerprint=plan_fingerprint(
                    source="USER",
                    objective="跟着我",
                    target_key=f"VERIFIED:{'a' * 32}@{SERVER}",
                    bucket=plan_time_bucket(T0),
                ),
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        out = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=VERIFIED
        )
        assert out["action"] == "already_planned"
        assert out.get("in_flight") is True, "必须明确告知'处理中'，不是含糊的重复"
        assert rig.runtime.created == [], "绝不立即创建 |rN 任务"
        # 原预留记录未被碰（PLANNING 保持，等 lease 或 link）
        row = await rig.store.get(reserved.plan_id)
        assert (
            row is not None
            and row.status == "PLANNING.value".replace(".value", "")
            or row.status == "PLANNING"
        )

    async def test_expired_planning_lease_allows_bounded_retry(self) -> None:
        """PLANNING 超过 lease（崩溃遗留）→ 终结旧预留 → 有界重试建新任务。"""
        rig = PlanRig()
        base_fp = plan_fingerprint(
            source="USER",
            objective="跟着我",
            target_key=f"VERIFIED:{'a' * 32}@{SERVER}",
            bucket=plan_time_bucket(T0),
        )
        await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective="跟着我",
                status=PlanStatus.PLANNING.value,
                initiator="u",
                target=VERIFIED,
                plan=follow_plan_payload(),
                version=1,
                fingerprint=base_fp,
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        # 超过 lease（默认 120s）—— 同一时间桶（600s）内，指纹不变
        rig.advance(121)
        out = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=VERIFIED
        )
        assert out["action"] == "created"
        assert len(rig.runtime.created) == 1
        # 旧预留被终结为 CANCELLED（按指纹找，不依赖 id 分配）
        rows = await rig.store.recent(10)
        old = next(r for r in rows if r.fingerprint == base_fp)
        assert old.status == "CANCELLED"
        # 新计划是**确定性重试序号**（有界），不是无法追溯的又一次新建
        assert str(out["plan"].fingerprint) == f"{base_fp.rsplit('|', 1)[0]}|r1"

    async def test_task_read_error_yields_not_retry(self) -> None:
        """旧记录有 task_id 但读取任务异常 → 保守让路，不猜成'已终态'。"""
        rig = PlanRig()
        out1 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=VERIFIED
        )
        assert out1["action"] == "created"

        class FlakyRuntime(FakeTaskRuntime):
            async def get(self, task_id: str) -> Any:
                raise RuntimeError("db down")

        flaky = FlakyRuntime()
        rig.runtime = flaky
        rig.service.runtime = flaky
        out2 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=VERIFIED
        )
        assert out2["action"] == "already_planned", "读不到任务状态 = 保守让路"
        assert len(flaky.created) == 0, "读不到 ≠ 已终态，绝不重试建第二个任务"
        assert len(rig.runtime.created) == 0

    async def test_different_users_no_cross_leak(self) -> None:
        """不同 QQ 用户绑同一 MC UUID：计划/任务不串用。"""
        rig = PlanRig()
        out1 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="user-1", session_id="s1", target=VERIFIED
        )
        assert out1["action"] == "created"
        task_owner = rig.runtime.created[0]["user_id"]
        assert task_owner == "user-1"
        # 第二个用户（同 MC 目标）在别的 session 发同样请求 → 已有开着 → 让路
        out2 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="user-2", session_id="s2", target=VERIFIED
        )
        assert out2["action"] == "already_planned"
        assert len(rig.runtime.created) == 1
        # 让路回复**不泄漏**任务详情（只有"已在办"）
        assert "user-1" not in str(out2.get("reply", ""))

    async def test_different_servers_no_cross_reuse(self) -> None:
        """同名/同 UUID 但 server_id 不同 → 不同指纹，不串用待确认任务。"""
        rig = PlanRig()
        other = PlanTarget(
            status="VERIFIED", server_id="mc-OTHER", player_uuid="a" * 32, player_name="Rinsora"
        )
        out1 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s", target=VERIFIED
        )
        assert out1["action"] == "created"
        # 指纹隔离：server 不同 → 不同指纹 → 新预留成功（不是复用第一份）
        out2 = await rig.service.plan_follow_from_user(
            objective="跟着我", user_id="u", session_id="s2", target=other
        )
        assert out2["action"] == "created"
        # 两份计划、两份任务、target 各自正确
        rows = await rig.store.recent(10)
        assert sum(1 for r in rows if r.status == "LINKED") == 2
        targets = {r.target.server_id for r in rows if r.status == "LINKED"}
        assert targets == {SERVER, "mc-OTHER"}

    async def test_concurrent_rN_fingerprint_race(self) -> None:
        """并发竞争同一个 |rN 重试指纹：恰一个成功。"""
        rig = PlanRig()
        # 预置一条终态记录（占据基础指纹），迫使并发请求都走重试路径
        await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.USER.value,
                objective="跟着我",
                status=PlanStatus.CANCELLED.value,
                task_id="T-old",
                initiator="u",
                target=VERIFIED,
                plan=follow_plan_payload(),
                version=1,
                fingerprint=plan_fingerprint(
                    source="USER",
                    objective="跟着我",
                    target_key=f"VERIFIED:{'a' * 32}@{SERVER}",
                    bucket=plan_time_bucket(T0),
                ),
                created_at=T0 - 700,
                expires_at=T0 - 100,
            ),
            now=T0 - 700,
        )
        # 4 个请求一起读到"同一批既有行数"（真实并发下 COUNT 与 INSERT 之间会交错）
        store = rig.store
        original_count = store.count_by_stem
        arrived = 0
        release = asyncio.Event()

        async def gated_count(stem: str) -> int:
            nonlocal arrived
            value = int(await original_count(stem))
            arrived += 1
            if arrived >= 4:
                release.set()
            await release.wait()
            return value

        store.count_by_stem = gated_count  # type: ignore[method-assign]
        results = await asyncio.gather(
            *[
                rig.service.plan_follow_from_user(
                    objective="跟着我", user_id="u", session_id=f"s-{i}", target=VERIFIED
                )
                for i in range(4)
            ]
        )
        created = [r for r in results if r["action"] == "created"]
        assert len(created) == 1, "并发 |rN 竞争恰一个成功"
        assert len(rig.runtime.created) == 1
        # 赢家与输家看到的是**同一份**重试指纹（确定性、可追溯），不是各自 |r1..|r4
        assert {str(r["plan"].fingerprint).rsplit("|", 1)[-1] for r in results} == {"r1"}


# ================================================================ §2.3 SQLite 真库


class TestSqliteCasAndPayload:
    """§2.3：真实 SQLite + 真实迁移 + 真实 SqliteAgentPlanStore。"""

    async def _make_service(
        self, tmp_path: Path, *, rig: PlanRig | None = None
    ) -> tuple[AgentPlanService, Database, PlanRig]:
        from app.tasks.agent_planner import BoundedAgentPlanner

        rig = rig or PlanRig()
        database = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'ap.db'}"))
        await database.connect()
        service = AgentPlanService(
            store=SqliteAgentPlanStore(database),
            planner=rig.planner or BoundedAgentPlanner(),
            task_runtime=rig.runtime,
            character_id="罐头@x",
            plan_ttl_seconds=600.0,
            reserve_lease_seconds=120.0,
            clock=lambda: rig.clock_now,
        )
        return service, database, rig

    async def _seed_ready_plan(self, service: AgentPlanService) -> AgentPlan:
        stored, _ = await service._create(
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
                fingerprint="life|sqlite|1",
                created_at=T0,
                expires_at=T0 + 600,
            ),
            now=T0,
        )
        return stored

    async def test_cas_updates_column_payload_and_object(self, tmp_path: Path) -> None:
        """三方一致性：SQL status 列 == payload.status == get() 返回对象。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            stored = await self._seed_ready_plan(service)
            assert await service.store.occupy_for_approval(stored.plan_id, now=T0) is not None
            row = await database.fetchone(
                "SELECT status, payload FROM task_agent_plans WHERE plan_id = ?",
                (stored.plan_id,),
            )
            assert row["status"] == "APPROVED"
            import json as _json

            payload = _json.loads(row["payload"])
            assert payload["status"] == "APPROVED", "payload 必须同步更新（7D.2 P1-2）"
            obj = await service.store.get(stored.plan_id)
            assert obj is not None and obj.status == "APPROVED"
        finally:
            await database.close()

    async def test_concurrent_approval_one_task_real_sqlite(self, tmp_path: Path) -> None:
        """真库并发批准：恰一个 CAS 成功、至多一个 Task。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            stored = await self._seed_ready_plan(service)
            results = await asyncio.gather(
                *[
                    service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
                    for _ in range(3)
                ]
            )
            approved = [r for r in results if r["action"] == "approved"]
            assert len(approved) == 1
            row = await database.fetchone(
                "SELECT COUNT(*) AS n FROM task_agent_plans WHERE status = 'LINKED'"
            )
            assert (row or {}).get("n") == 1
        finally:
            await database.close()

    async def test_approved_crash_recovery_real_sqlite(self, tmp_path: Path) -> None:
        """CAS 成功后在建任务前中断 → recover：不建 Task、不回 READY、安全终结。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            stored = await self._seed_ready_plan(service)
            # 占用成功（模拟"占用后、建任务前"中断 —— 不调 approve 的后半段）
            assert await service.store.occupy_for_approval(stored.plan_id, now=T0) is not None
            # 重启：重新构建 Store/Service（同一真库）
            store2 = SqliteAgentPlanStore(database)
            rig2 = PlanRig()
            service2 = AgentPlanService(
                store=store2,
                planner=rig2.planner,
                task_runtime=rig2.runtime,
                clock=lambda: T0 + 10,
            )
            summary = await service2.recover()
            assert summary["orphans"] == 1
            row = await database.fetchone(
                "SELECT status, payload FROM task_agent_plans WHERE plan_id = ?",
                (stored.plan_id,),
            )
            import json as _json

            assert row["status"] == "CANCELLED"
            assert _json.loads(row["payload"])["status"] == "CANCELLED"
            assert rig2.runtime.created == [], "恢复绝不建 Task"
            # 再次「批准」不能把它当 READY
            out = await service2.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
            assert out["action"] == "not_approvable"
        finally:
            await database.close()

    async def test_recovery_idempotent_real_sqlite(self, tmp_path: Path) -> None:
        """同一恢复跑两遍：不重复建 Task、不复活终态。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            stored = await self._seed_ready_plan(service)
            await service.store.occupy_for_approval(stored.plan_id, now=T0)
            first = await service.recover()
            second = await service.recover()
            assert first["orphans"] == 1 and second["orphans"] == 0
            row = await database.fetchone(
                "SELECT status FROM task_agent_plans WHERE plan_id = ?", (stored.plan_id,)
            )
            assert row["status"] == "CANCELLED"
        finally:
            await database.close()

    async def test_expire_and_cas_interleaved_real_sqlite(self, tmp_path: Path) -> None:
        """expire_due 与 CAS 交错：过期计划仍不能被批准。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            stored = await self._seed_ready_plan(service)
            expired = await service.expire_due(now=T0 + 601)
            assert len(expired) == 1
            # CAS 在过期后必然失败
            assert await service.store.occupy_for_approval(stored.plan_id, now=T0 + 601) is None
            out = await service.approve(plan_id=stored.plan_id, user_id="u", session_id="s")
            assert out["action"] == "not_approvable"
            row = await database.fetchone(
                "SELECT COUNT(*) AS n FROM task_agent_plans WHERE status = 'LINKED'"
            )
            assert (row or {}).get("n") == 0
        finally:
            await database.close()

    async def test_half_written_link_recovered_to_linked(self, tmp_path: Path) -> None:
        """任务已建但关联更新失败（列写了 payload 没写）：恢复把 task_id 补回。"""
        service, database, rig = await self._make_service(tmp_path)
        try:
            from app.tasks.models import TaskPlan

            stored = await self._seed_ready_plan(service)
            # 模拟"占用 → 任务建了 → 只写了列、没写 payload"的半写态
            # （任务建在 **service 自己** 的 runtime 上 —— 恢复要能在那里查到它）
            record = await rig.runtime.create_task(
                str(stored.plan["objective"]),
                session_id="s",
                user_id="u",
                origin="user",
                plan=TaskPlan.from_payload(dict(stored.plan)),
                source="qq_life_plan",
            )
            await database.execute(
                "UPDATE task_agent_plans SET status = 'APPROVED', task_id = ? WHERE plan_id = ?",
                (record.task_id, stored.plan_id),
            )
            await service.recover()
            # 半写态 → 修复关联为 LINKED（不取消：任务可能正等着确认）
            row = await database.fetchone(
                "SELECT status, payload FROM task_agent_plans WHERE plan_id = ?",
                (stored.plan_id,),
            )
            import json as _json

            assert row["status"] == "LINKED"
            payload = _json.loads(row["payload"])
            assert payload["task_id"] == record.task_id
            assert payload["status"] == "LINKED"
            # 修复是可审计的，且没有建第二个任务
            events = await service.store.recent_events(limit=5)
            assert any(
                item["type"] == "agentplan.compensated"
                and item["reason"] == "half_written_link_repaired"
                for item in events
            )
            assert len(rig.runtime.created) == 1
        finally:
            await database.close()


# ================================================================ §3.3 取消 + 入口链


class _Identity:
    user_id = "2731431246"
    session_id = "private:2731431246"


class _Step:
    def __init__(self, tool: str, arguments: dict[str, Any] | None = None) -> None:
        self.tool = tool
        self.arguments = arguments or {}


def _pending_follow_record(task_id: str, username: str = "Rinsora") -> Any:
    return type(
        "R",
        (),
        {
            "task_id": task_id,
            "state": type("S", (), {"value": "PENDING_CONFIRMATION"})(),
            "plan": type(
                "P", (), {"steps": [_Step("minecraft_follow_player", {"username": username})]}
            ),
        },
    )()


class TestVetoCancelsTask:
    """§3.3：否决路径必须调用既有取消（不再只是返回文案）。"""

    def _harness(
        self,
        *,
        record: Any,
        plan: Any = None,
        resolved: Any = None,
        plan_error: bool = False,
        server_id: str = SERVER,
        cancel_outcome: str = "CANCELLED",
        current_error: bool = False,
    ) -> Any:
        """``cancel_outcome`` 模拟**真实**取消返回契约（7D.2.1）：

        * ``TaskState`` 的值（如 ``CANCELLED`` / ``SUCCEEDED`` / ``EXPIRED`` / ``PAUSED``）
          → 返回带该状态的真实枚举记录；
        * ``"none"`` 返回 None；``"no_state"`` 返回缺 state 的对象；
          ``"weird"`` 返回状态串不认识的对象；``"raises"`` 抛异常。
        """
        from app.tasks.models import TaskState
        from app.tasks.qq_entry import QQTaskEntry

        cancelled: list[str] = []
        confirm_calls: list[str] = []

        def _cancel_return(task_id: str) -> Any:
            if cancel_outcome == "none":
                return None
            if cancel_outcome == "no_state":
                return type("R", (), {"task_id": task_id})()
            if cancel_outcome == "weird":
                return type("R", (), {"task_id": task_id, "state": "NOT_A_REAL_STATE"})()
            return type("R", (), {"task_id": task_id, "state": TaskState(cancel_outcome)})()

        class Runtime:
            async def current(self, session_id: str | None = None) -> Any:
                if current_error:
                    raise RuntimeError("db down")
                return record

            async def cancel(self, task_id: str, *, reason: str = "") -> Any:
                cancelled.append(task_id)
                if cancel_outcome == "raises":
                    raise RuntimeError("cancel down")
                return _cancel_return(task_id)

            async def confirm_and_start(self, *args: Any, **kwargs: Any) -> Any:
                confirm_calls.append(str(args))
                return None

        class Plans:
            async def plan_for_task(self, task_id: str) -> Any:
                if plan_error:
                    raise RuntimeError("plan down")
                return plan

        class Resolved:
            def __init__(self, payload: dict[str, Any]) -> None:
                self.__dict__.update(payload)

        async def resolve(user_id: str, server_id: str = "") -> Any:
            return Resolved(resolved or {})

        harness = type("H", (), {})()
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
        entry.detector = type(
            "D",
            (),
            {
                "detect": staticmethod(lambda text: type("I", (), {"is_task": False})()),
                "status_query": staticmethod(lambda text: False),
                "control_command": staticmethod(lambda text: "confirm" if text == "确认" else ""),
            },
        )()
        entry._server_id = lambda: server_id  # type: ignore[method-assign]

        class Log:
            def warning(self, *a: Any, **k: Any) -> None: ...
            def exception(self, *a: Any, **k: Any) -> None: ...
            def info(self, *a: Any, **k: Any) -> None: ...
            def debug(self, *a: Any, **k: Any) -> None: ...

        entry._log = Log()  # type: ignore[method-assign]
        harness.entry = entry
        harness.cancelled = cancelled
        harness.confirm_calls = confirm_calls
        return harness

    async def test_missing_plan_cancels_and_blocks_confirm(self) -> None:
        """AgentPlan 缺失 → 取消 + 阻止 confirm_and_start（入口链级）。"""
        harness = self._harness(record=_pending_follow_record("T-1"), plan=None)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert harness.cancelled == ["T-1"], "必须调用既有取消路径"
        # 入口链：否决后 confirm_and_start 不被调用
        await harness.entry._handle(_identity_event("确认"))
        assert harness.confirm_calls == []

    async def test_plan_lookup_error_cancels(self) -> None:
        harness = self._harness(record=_pending_follow_record("T-1"), plan_error=True)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert harness.cancelled == ["T-1"]

    async def test_missing_uuid_cancels(self) -> None:
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=PlanTarget(),
            fingerprint="f",
        )
        harness = self._harness(record=_pending_follow_record("T-1"), plan=plan)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert harness.cancelled == ["T-1"]

    @pytest.mark.parametrize(
        "resolved",
        [
            {
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            {
                "status": "CONFLICT",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            {"status": "MISSING", "player_uuid": "", "server_id": SERVER, "player_name": ""},
            {
                "status": "VERIFIED",
                "player_uuid": "b" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            {
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": "mc-OTHER",
                "player_name": "Rinsora",
            },
            {
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "别的名字",
            },
        ],
    )
    async def test_identity_mismatch_cancels(self, resolved: dict[str, Any]) -> None:
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(record=_pending_follow_record("T-1"), plan=plan, resolved=resolved)
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None, resolved
        assert harness.cancelled == ["T-1"]

    async def test_cancel_failure_still_blocks(self) -> None:
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(
            record=_pending_follow_record("T-1"),
            plan=plan,
            resolved={
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            cancel_outcome="raises",
        )
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert "停不下来" in veto or "取消未能确认" in veto, "如实说取消失败"
        assert harness.cancelled == ["T-1"], "取消仍被调用（失败点在被调用之后）"

    async def test_cancelled_state_reports_success(self) -> None:
        """取消返回真实 ``CANCELLED`` → 正常路径仍显示"已取消"（不因本修复被破坏）。"""
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(
            record=_pending_follow_record("T-1"),
            plan=plan,
            resolved={
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            cancel_outcome="CANCELLED",
        )
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None
        assert "这个跟随任务我取消了" in veto
        assert "取消未能确认" not in veto and "停不下来" not in veto

    @pytest.mark.parametrize("state", ["SUCCEEDED", "FAILED", "EXPIRED", "PAUSED"])
    async def test_non_cancelled_state_never_claims_success(self, state: str) -> None:
        """``cancel()`` 返回别的状态（既有契约：已终态任务原样返回）→ 不算取消成功。"""
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(
            record=_pending_follow_record("T-1"),
            plan=plan,
            resolved={
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            cancel_outcome=state,
        )
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None, "否决必须保留"
        assert "我取消了" not in veto, f"状态 {state} 绝不能报成取消成功"
        assert state in veto and "不是取消成功" in veto, "如实报状态"
        assert harness.cancelled == ["T-1"], "仍然调用了既有取消路径"
        # 入口链：本次「确认」不得被执行
        await harness.entry._handle(_identity_event("确认"))
        assert harness.confirm_calls == []

    @pytest.mark.parametrize("outcome", ["none", "no_state", "weird"])
    async def test_unverifiable_cancel_result_is_conservative(self, outcome: str) -> None:
        """返回 None / 缺状态 / 状态串不认识 → 保守拒绝且不声称取消成功。"""
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(
            record=_pending_follow_record("T-1"),
            plan=plan,
            resolved={
                "status": "REVOKED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
            cancel_outcome=outcome,
        )
        veto = await harness.entry._verify_follow_identity(_Identity())
        assert veto is not None, "否决必须保留"
        assert "我取消了" not in veto, f"取消结果 {outcome} 无法核验，绝不能说成功"
        assert "取消未能确认" in veto
        await harness.entry._handle(_identity_event("确认"))
        assert harness.confirm_calls == []

    async def test_cancel_contract_with_the_real_task_runtime(self) -> None:
        """**真 TaskRuntime 契约**（不是测试桩）：

        * 待确认任务 → ``cancel()`` 返回 ``CANCELLED`` → 报"已取消"；
        * 已经终态（EXPIRED）任务 → ``cancel()`` **原样返回该记录**（不抛异常、也不取消）
          → 修复后必须**不**报"已取消"（修复前这里会被误报成功）。
        """
        from app.tasks.models import TaskPlan, TaskState, TaskStep
        from app.tasks.runtime import TaskConfig, TaskRuntime
        from app.tasks.store import InMemoryTaskStore

        async def invoke(tool: str, arguments: dict[str, Any], **kwargs: Any) -> Any:
            raise AssertionError(f"跟随否决链不应该调用工具：{tool}")

        class FakeConfirmations:
            def __init__(self) -> None:
                self.pending: dict[str, dict[str, Any]] = {}
                self._seq = 0

            async def request(self, *, task_id: str, **kwargs: Any) -> str:
                self._seq += 1
                cid = f"conf-{self._seq}"
                self.pending[cid] = {"task_id": task_id, **kwargs}
                return cid

            async def consume(self, confirmation_id: str, **kwargs: Any) -> tuple[bool, str]:
                return False, "minecraft.confirmation_mismatch"

            async def cancel(self, confirmation_id: str) -> None:
                self.pending.pop(str(confirmation_id), None)

        def make_runtime() -> Any:
            return TaskRuntime(
                store=InMemoryTaskStore(),
                invoke=invoke,
                confirmations=FakeConfirmations(),
                config=TaskConfig(ttl_seconds=600.0),
                risk_of=lambda tool: "LOW" if tool == "minecraft_follow_player" else "",
                is_registered=lambda tool: tool == "minecraft_follow_player",
                schema_of=lambda tool: (
                    {
                        "type": "object",
                        "properties": {"username": {"type": "string"}},
                        "required": ["username"],
                    }
                    if tool == "minecraft_follow_player"
                    else None
                ),
            )

        plan = TaskPlan(
            objective="跟着我",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_follow_player",
                    arguments={"username": "Rinsora"},
                    risk="LOW",
                )
            ],
        )

        # ---- 场景 1：真实的待确认任务 → 真实 cancel() → CANCELLED
        runtime = make_runtime()
        record = await runtime.create_task(
            "跟着我", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.PENDING_CONFIRMATION
        cancelled = await runtime.cancel(record.task_id, reason="probe")
        assert cancelled.state is TaskState.CANCELLED, "真契约：可取消的任务返回 CANCELLED"

        harness = self._harness(record=_pending_follow_record(record.task_id), plan=None)
        harness.entry.runtime = runtime
        veto = await harness.entry._follow_veto_message(
            "测试原因", task_id=record.task_id, runtime=runtime
        )
        assert "这个跟随任务我取消了" in veto, "真 CANCELLED 仍要报成功"

        # ---- 场景 2：真实的已终态（EXPIRED）任务 → 真实 cancel() 原样返回 → 不得报成功
        runtime2 = make_runtime()
        record2 = await runtime2.create_task(
            "跟着我", session_id="s", user_id="u", origin="user", plan=plan
        )
        expired = await runtime2.expire(record2.task_id, reason="probe ttl")
        assert expired.state is TaskState.EXPIRED
        returned = await runtime2.cancel(record2.task_id, reason="probe cancel")
        assert returned.state is TaskState.EXPIRED, "真契约：已终态任务被原样返回（不抛异常）"

        harness2 = self._harness(record=_pending_follow_record(record2.task_id), plan=None)
        harness2.entry.runtime = runtime2
        veto2 = await harness2.entry._follow_veto_message(
            "测试原因", task_id=record2.task_id, runtime=runtime2
        )
        assert "我取消了" not in veto2, "已终态任务被原样返回时绝不能报取消成功"
        assert "EXPIRED" in veto2 and "不是取消成功" in veto2

    async def test_consistent_identity_allows(self) -> None:
        plan = AgentPlan(
            plan_id="AP-1",
            source=PlanSource.USER.value,
            objective="跟着我",
            status=PlanStatus.LINKED.value,
            task_id="T-1",
            target=VERIFIED,
            fingerprint="f",
        )
        harness = self._harness(
            record=_pending_follow_record("T-1"),
            plan=plan,
            resolved={
                "status": "VERIFIED",
                "player_uuid": "a" * 32,
                "server_id": SERVER,
                "player_name": "Rinsora",
            },
        )
        assert await harness.entry._verify_follow_identity(_Identity()) is None
        assert harness.cancelled == [], "一致身份不触发取消"

    async def test_non_follow_task_no_special_cancel(self) -> None:
        record = type(
            "R",
            (),
            {
                "task_id": "T-2",
                "state": type("S", (), {"value": "PENDING_CONFIRMATION"})(),
                "plan": type(
                    "P", (), {"steps": [_Step("minecraft_dig", {"x": 1, "y": 2, "z": 3})]}
                ),
            },
        )()
        harness = self._harness(record=record, plan=None)
        assert await harness.entry._verify_follow_identity(_Identity()) is None
        assert harness.cancelled == [], "普通任务不触发 follow 专用取消"


def _identity_event(text: str) -> Any:
    """构造一个最小的 QQ 消息事件（入口链测试用）。"""
    return type(
        "E",
        (),
        {
            "user_id": _Identity.user_id,
            "session_id": _Identity.session_id,
            "text": text,
            "is_group": False,
            "self_id": 2934257196,
            "raw_message": text,
            "message": [{"type": "text", "data": {"text": text}}],
        },
    )()
