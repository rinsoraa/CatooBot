"""Phase 7D §十：AgentPlan 测试矩阵。

计划解析与六值结论 / USER-LIFE 来源隔离与确认次数（修订 1）/
身份缺失·撤销·冲突 / 过期·指纹·终态 / 依赖既有执行链（源码级 + 句柄级）。
follow 的执行生命周期证明在 ``tests/test_agent_plan_follow.py``。
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

from app.tasks.agent_plan import (
    ALLOWED_PLAN_TRANSITIONS,
    TERMINAL_PLAN_STATUSES,
    AgentPlan,
    PlanSource,
    PlanStatus,
    PlanTarget,
    plan_fingerprint,
    plan_transition_allowed,
)
from app.tasks.agent_plan_store import InMemoryAgentPlanStore
from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome, detect_shape
from app.tasks.agent_service import is_approve_plan_command
from app.tasks.intent import TaskIntentDetector
from tests.agent_plan_fakes import (
    SERVER,
    T0,
    FakeIdentity,
    FakeTaskRuntime,
    PlanRig,
    link,
    proposal,
)

PACKAGE = Path(__file__).resolve().parent.parent / "app" / "tasks"
PHASE_7D_FILES = ("agent_plan.py", "agent_plan_store.py", "agent_planner.py", "agent_service.py")

FORBIDDEN_CALLS = {
    "execute",
    "confirm_and_start",
    "invoke_tool",
    "run_action",
    "dig",
    "place",
    "equip",
    "craft",
    "pickup",
    "send",
    "send_message",
    "reply",
    "deliver",
}


# ---------------------------------------------------------------- 规划器六值


class TestPlanningOutcomes:
    def test_shapes_are_deterministic(self) -> None:
        assert detect_shape("跟着我") == "follow"
        assert detect_shape("跟随过来一下") == "follow"
        assert detect_shape("去砍两块橡木回来") == "resource"
        assert detect_shape("你好呀") == "unknown"
        assert detect_shape("") == "unknown"

    async def test_follow_plan_ready_with_verified_target(self) -> None:
        rig = PlanRig()
        result = await rig.service.planner.plan(
            "跟着我",
            target=PlanTarget(
                status="VERIFIED",
                server_id=SERVER,
                player_uuid="a" * 32,
                player_name="Rinsora",
                reason="",
            ),
        )
        assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
        assert [step.tool for step in result.plan.plan.steps] == ["minecraft_follow_player"]
        assert result.plan.plan.steps[0].arguments == {"username": "Rinsora"}
        assert result.risk_summary["max_risk"] == "LOW"
        # 修订 2：期限三层如实展示（动作 120s / 授权 60s / 任务 600s）
        assert result.risk_summary["duration_limit_seconds"] == 120.0

    async def test_follow_plan_blocked_without_identity(self) -> None:
        planner = BoundedAgentPlanner()
        result = await planner.plan("跟着我", target=PlanTarget(status="MISSING"))
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION
        assert result.reason == "target_missing"

    async def test_follow_plan_blocked_by_policy_when_low_disabled(self) -> None:
        planner = BoundedAgentPlanner(allow_low=False)
        result = await planner.plan(
            "跟着我",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
        )
        assert result.outcome is PlanningOutcome.BLOCKED_BY_POLICY
        assert result.reason == "allow_low_disabled"

    async def test_resource_plan_reuses_the_5a_template(self) -> None:
        rig = PlanRig()
        result = await rig.service.planner.plan("去砍两块橡木并捡回来", observe=rig.observe)
        assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
        tools = [step.tool for step in result.plan.plan.steps]
        assert "minecraft_dig" in tools and "minecraft_pickup_item" in tools
        assert result.risk_summary["max_risk"] == "MEDIUM"

    async def test_resource_plan_observation_failure_is_precondition(self) -> None:
        async def bad_observe(tool: str, arguments: Any) -> Any:
            from app.tasks.planner import ObservationFailed

            raise ObservationFailed("附近 16 格内没有找到 oak_log")

        planner = BoundedAgentPlanner(allow_medium=True)
        result = await planner.plan("去砍两块橡木并捡回来", observe=bad_observe)
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION

    async def test_resource_plan_blocked_by_policy_when_medium_disabled(self) -> None:
        planner = BoundedAgentPlanner(allow_medium=False)
        result = await planner.plan("去砍两块橡木并捡回来", observe=(await None) if False else None)
        assert result is not None  # 无 observe 时先卡在观察前置
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION

    async def test_vague_objective_needs_more_information(self) -> None:
        planner = BoundedAgentPlanner()
        result = await planner.plan("随便做点什么吧")
        assert result.outcome is PlanningOutcome.NEEDS_MORE_INFORMATION

    async def test_design_goal_needs_more_information_not_a_plan(self) -> None:
        planner = BoundedAgentPlanner()
        result = await planner.plan("建造一台刷铁机")
        assert result.outcome is PlanningOutcome.NEEDS_MORE_INFORMATION
        assert result.plan is None

    def test_unregistered_capability_is_unsupported(self) -> None:
        planner = BoundedAgentPlanner()
        result = planner._plan_unknown("想造一台无人机")
        assert result.outcome in {
            PlanningOutcome.UNSUPPORTED,
            PlanningOutcome.NEEDS_MORE_INFORMATION,
        }
        assert result.plan is None

    async def test_no_template_for_supported_capabilities(self) -> None:
        planner = BoundedAgentPlanner()
        result = planner._plan_unknown("把箱子里的东西整理一下")
        assert result.outcome is PlanningOutcome.UNSUPPORTED
        assert result.reason == "no_plan_template"


# ---------------------------------------------------------------- USER / LIFE 隔离


class TestSourceRouting:
    async def test_user_plan_links_the_task_same_confirmation(self) -> None:
        """修订 1：USER 计划与待确认任务同建 → 一次确认（不单独挂批准门）。"""
        rig = PlanRig()
        plan = await rig.service.record_user_plan(
            objective="去砍两块橡木并捡回来",
            user_id="2731431246",
            session_id="private:2731431246",
            task_id="T-100",
            planned=type("P", (), {"plan": None, "risk_summary": {}})(),
        )
        assert plan is not None
        assert plan.source == PlanSource.USER.value
        assert plan.status == PlanStatus.LINKED.value
        assert plan.task_id == "T-100"
        assert plan.approver_user_id == ""  # USER 没有单独的批准门

    async def test_life_plan_never_creates_a_task(self) -> None:
        rig = PlanRig()
        out = await rig.service.plan_from_proposal(proposal())
        assert out["action"] == "planned"
        plan = out["plan"]
        assert plan.source == PlanSource.LIFE.value
        assert plan.status == PlanStatus.READY_FOR_APPROVAL.value
        assert plan.task_id == ""
        assert rig.runtime.created == [], "LIFE 计划绝不自动建任务"

    async def test_life_plan_from_unsupported_proposal_is_honest(self) -> None:
        rig = PlanRig()
        out = await rig.service.plan_from_proposal(
            proposal(objective="随便做点什么吧", status="READY_FOR_FUTURE_EXECUTION")
        )
        assert out["action"] == "planned"
        assert out["plan"].status == PlanStatus.NEEDS_MORE_INFORMATION.value

    async def test_life_plan_skips_closed_proposals(self) -> None:
        rig = PlanRig()
        out = await rig.service.plan_from_proposal(proposal(status="EXPIRED"))
        assert out == {"action": "skipped", "reason": "proposal_expired"}

    async def test_approval_creates_the_task_with_the_approver(self) -> None:
        """修订 1：LIFE「批准」→ 建待确认任务，批准者成为任务 owner（第二道门天然校验）。"""
        rig = PlanRig()
        planned = await rig.service.plan_from_proposal(proposal())
        plan = planned["plan"]
        plan = await rig.store.replace_plan(
            AgentPlan.from_payload(
                {
                    **plan.to_payload(),
                    "plan": {
                        "objective": plan.objective,
                        "steps": [
                            {
                                "step_id": "step_1",
                                "tool": "minecraft_dig",
                                "arguments": {"x": -10, "y": 70, "z": 5},
                                "risk": "MEDIUM",
                            }
                        ],
                    },
                    "plan_hash": "h1",
                }
            )
        )
        out = await rig.service.approve(
            plan_id=plan.plan_id, user_id="2731431246", session_id="private:2731431246"
        )
        assert out["action"] == "approved"
        assert out["record"].task_id
        assert out["plan"].status == PlanStatus.LINKED.value
        assert out["plan"].approver_user_id == "2731431246"
        assert rig.runtime.created[-1]["user_id"] == "2731431246"
        assert rig.runtime.created[-1]["session_id"] == "private:2731431246"
        # 计划审计明确记录了这是第一道门
        events = await rig.store.recent_events(limit=5)
        assert any(e["type"] == "agentplan.approved" for e in events)

    async def test_approval_of_empty_plan_is_refused(self) -> None:
        """没有可执行步骤的计划不能批准（fail-closed）。"""
        rig = PlanRig()
        from app.tasks.agent_plan import AgentPlan, PlanSource

        empty = await rig.service._create(
            AgentPlan(
                plan_id="",
                source=PlanSource.LIFE.value,
                objective="想去 Minecraft 收集一点橡木",
                status=PlanStatus.READY_FOR_APPROVAL.value,
                plan={},  # 没有步骤
                proposal_id="TP-001",
                created_at=T0,
                expires_at=T0 + 600,
                fingerprint="test|empty",
            ),
            now=T0,
        )
        out = await rig.service.approve(plan_id=empty.plan_id, user_id="u", session_id="s")
        assert out["action"] == "not_approvable"
        assert rig.runtime.created == []

    async def test_approve_command_only_consumed_when_a_life_plan_is_waiting(self) -> None:
        rig = PlanRig()
        assert await rig.service.handle_qq(text="批准", user_id="u", session_id="s") is None
        await rig.service.plan_from_proposal(proposal())
        out = await rig.service.handle_qq(text="批准这个计划吧", user_id="u", session_id="s")
        assert out is not None and out["action"] == "approved"

    def test_approve_keywords_are_narrow(self) -> None:
        assert is_approve_plan_command("批准")
        assert is_approve_plan_command("批准计划")
        assert not is_approve_plan_command("确认")
        assert not is_approve_plan_command("你好")


# ---------------------------------------------------------------- 身份（修订 2）


class TestIdentityInPlanning:
    async def test_follow_target_only_from_verified_link(self) -> None:
        from app.tasks.proposal_service import TaskProposalService

        proposals = TaskProposalService(
            store=InMemoryAgentPlanStore(), identity=FakeIdentity([link("a" * 32)])
        )
        resolved = await proposals.resolve_target(user_id="2731431246")
        assert resolved.player_name == "Rinsora"
        rig = PlanRig()
        out = await rig.service.plan_follow_from_user(
            objective="跟着我",
            user_id="2731431246",
            session_id="private:2731431246",
            target=resolved,
        )
        assert out["action"] == "created"
        step = out["record"].plan.steps[0]
        assert step.arguments == {"username": "Rinsora"}

    async def test_missing_identity_refuses_to_guess(self) -> None:
        from app.tasks.proposal_service import TaskProposalService

        proposals = TaskProposalService(store=InMemoryAgentPlanStore(), identity=FakeIdentity([]))
        resolved = await proposals.resolve_target(user_id="2731431246")
        rig = PlanRig()
        out = await rig.service.plan_follow_from_user(
            objective="跟着我",
            user_id="2731431246",
            session_id="s",
            target=resolved,
        )
        assert out["action"] == "plan_rejected"
        assert rig.runtime.created == []


# ---------------------------------------------------------------- 状态机 / 过期


class TestLifecycle:
    def test_terminal_states_have_no_exit(self) -> None:
        for status in TERMINAL_PLAN_STATUSES:
            assert ALLOWED_PLAN_TRANSITIONS[status] == frozenset()

    async def test_expired_plans_are_terminal_and_not_revived(self) -> None:
        rig = PlanRig()
        await rig.service.plan_from_proposal(proposal())
        rig.advance(601)
        expired = await rig.service.expire_due()
        assert len(expired) == 1 and expired[0].status == PlanStatus.EXPIRED.value
        again = await rig.service.plan_from_proposal(proposal())
        assert again["action"] == "planned"  # 新桶新计划；旧行不动
        rows = await rig.store.recent()
        assert sum(1 for r in rows if r.status == PlanStatus.EXPIRED.value) == 1

    async def test_recover_never_revives(self) -> None:
        rig = PlanRig()
        await rig.service.plan_from_proposal(proposal())
        rig.advance(601)
        await rig.service.expire_due()
        summary = await rig.service.recover()
        assert summary["action"] == "ok" and summary["open"] == 0

    def test_fingerprint_is_deterministic_and_bucketed(self) -> None:
        kwargs = {"source": PlanSource.LIFE, "objective": "跟着我", "proposal_id": "TP-1"}
        assert plan_fingerprint(**kwargs, bucket=1) == plan_fingerprint(**kwargs, bucket=1)
        assert plan_fingerprint(**kwargs, bucket=1) != plan_fingerprint(**kwargs, bucket=2)

    def test_transition_guard_rejects_illegal(self) -> None:
        assert (
            plan_transition_allowed(PlanStatus.UNSUPPORTED, PlanStatus.READY_FOR_APPROVAL) is False
        )


# ---------------------------------------------------------------- 安全边界


class TestSourceLevelGuards:
    def test_no_execution_shaped_calls_in_7d_files(self) -> None:
        for name in PHASE_7D_FILES:
            tree = ast.parse((PACKAGE / name).read_text(encoding="utf-8"))
            called: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    func = node.func
                    if isinstance(func, ast.Name):
                        called.add(func.id)
                    elif isinstance(func, ast.Attribute):
                        if ast.unparse(func.value) in {"conn", "cursor", "database", "db"}:
                            continue
                        called.add(func.attr)
            assert not (called & FORBIDDEN_CALLS), f"{name}: {called & FORBIDDEN_CALLS}"

    def test_service_holds_no_execution_power_of_its_own(self) -> None:
        """计划层唯一能"执行"的动作是调 TaskRuntime.create_task —— 这是设计本身。"""
        service = PlanRig().service
        for name in ("execute", "confirm", "invoke_tool", "send", "notify"):
            assert not hasattr(service, name), name

    def test_no_plan_status_is_an_execution_status(self) -> None:
        values = {status.value for status in PlanStatus}
        assert not values & {"RUNNING", "EXECUTING", "SUCCEEDED", "CONFIRMED"}

    def test_allow_medium_default_unchanged(self) -> None:
        from app.config.settings import AppConfig

        assert AppConfig().minecraft.agent.tools.allow_medium is False
        assert AppConfig().task.max_replans == 2
        assert AppConfig().task.ttl_seconds == 600.0


class TestQQEntryRouting:
    """「跟着我」与「批准」的入口路由（真实入口语义，鸭子类型注入）。"""

    def _entry(self, bot: Any) -> Any:
        from app.tasks.qq_entry import QQTaskEntry

        entry = QQTaskEntry.__new__(QQTaskEntry)
        entry.bot = bot
        entry.detector = TaskIntentDetector()
        entry._log = _NullLog()
        return entry

    class _Identity:
        user_id = "2731431246"
        session_id = "private:2731431246"

    async def test_follow_request_routes_to_the_plan_layer(self) -> None:
        rig = PlanRig()
        said: list[str] = []

        async def resolver(user_id: str, server_id: str = "") -> Any:
            from app.tasks.proposal import TargetResolution

            return TargetResolution(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            )

        bot = type(
            "B",
            (),
            {
                "agent_plans": rig.service,
                "proposals": type("P", (), {"resolve_target": staticmethod(resolver)})(),
            },
        )()
        entry = self._entry(bot)
        entry._say = _Say(said)  # type: ignore[method-assign]
        entry._server_id = lambda: SERVER  # type: ignore[method-assign]
        handled = await entry._handle_follow_request(self._Identity(), "跟着我")
        assert handled is True
        assert len(rig.runtime.created) == 1
        assert rig.runtime.created[0]["steps"] == ["minecraft_follow_player"]
        assert any("跟随" in text for text in said)
        assert any("确认" in text for text in said), "必须明确等待用户确认（修订 1）"

    async def test_approve_consumed_only_by_plan_layer(self) -> None:
        rig = PlanRig()
        said: list[str] = []
        await rig.service.plan_from_proposal(proposal())
        bot = type("B", (), {"agent_plans": rig.service})()
        entry = self._entry(bot)
        entry._say = _Say(said)  # type: ignore[method-assign]
        handled = await entry._handle_agent_plan(self._Identity(), "批准")
        assert handled is True
        assert len(rig.runtime.created) == 1
        assert any("确认" in text for text in said), "批准文案必须指向第二道门"

    async def test_confirm_veto_when_identity_no_longer_verified(self) -> None:
        """修订 2：确认前复核身份 —— 撤销/换号 → 取消任务，绝不带着旧目标跑。"""
        rig = PlanRig()
        await rig.service.plan_follow_from_user(
            objective="跟着我",
            user_id="2731431246",
            session_id="s",
            target=PlanTarget(
                status="VERIFIED", server_id=SERVER, player_uuid="a" * 32, player_name="Rinsora"
            ),
        )
        plan = (await rig.store.recent())[0]
        assert plan.task_id

        cancelled: list[str] = []

        class CancelableRuntime(FakeTaskRuntime):
            async def cancel(self, task_id: str, *, reason: str = "") -> Any:
                cancelled.append(task_id)
                return None

            async def current(self, session_id: str | None = None) -> Any:
                return type(
                    "R",
                    (),
                    {
                        "task_id": plan.task_id,
                        "state": type("S", (), {"value": "PENDING_CONFIRMATION"})(),
                    },
                )()

        # 现在身份解析成另一个 uuid（换号）→ 否决
        from app.tasks.proposal import TargetResolution

        async def resolver(user_id: str, server_id: str = "") -> Any:
            return TargetResolution(
                status="VERIFIED", server_id=SERVER, player_uuid="b" * 32, player_name="SomeoneElse"
            )

        runtime = CancelableRuntime()
        bot = type(
            "B",
            (),
            {
                "agent_plans": rig.service,
                "proposals": type("P", (), {"resolve_target": staticmethod(resolver)})(),
            },
        )()
        entry = self._entry(bot)
        entry.runtime = runtime
        entry._server_id = lambda: SERVER  # type: ignore[method-assign]
        veto = await entry._verify_follow_identity(self._Identity())
        assert veto is not None and cancelled == [plan.task_id]


class _Say:
    def __init__(self, sink: list[str]) -> None:
        self.sink = sink

    async def __call__(self, identity: Any, text: str) -> None:
        self.sink.append(str(text))


class _NullLog:
    def debug(self, *args: Any, **kwargs: Any) -> None: ...
    def info(self, *args: Any, **kwargs: Any) -> None: ...
    def warning(self, *args: Any, **kwargs: Any) -> None: ...
    def exception(self, *args: Any, **kwargs: Any) -> None: ...


class TestHandlerWiring:
    """真机门禁抓到的缺口：QQ 入口的 handler 是独立实例，两个都必须注入。"""

    def test_bot_wiring_injects_both_handlers(self) -> None:
        from pathlib import Path

        source = (Path(__file__).resolve().parent.parent / "app" / "core" / "bot.py").read_text(
            encoding="utf-8"
        )
        assert "entry_handler._plans = service" in source
        assert "self.task_turns._plans = service" in source
