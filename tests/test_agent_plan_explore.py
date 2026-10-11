"""Phase 7F.1：有界自主探索 shape 的规划 / 两道门 / 风险边界的测试矩阵。

只覆盖"计划层"（BoundedAgentPlanner + AgentPlanService + 既有两道门）；
真实执行与后置条件验证在 ``tests/test_task_runtime_explore.py``。
"""

from __future__ import annotations

from typing import Any

from app.tasks.agent_plan import PlanStatus
from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome, detect_shape
from app.tasks.planner import ObservationFailed
from tests.agent_plan_fakes import PlanRig, proposal

# ---------------------------------------------------------------- 形状判定


class TestExploreShape:
    def test_explore_keywords_route_to_explore(self) -> None:
        assert detect_shape("有点想去外面转转") == "explore"
        assert detect_shape("想起：上次说要探索地图") == "explore"
        assert detect_shape("去远行吧") == "explore"
        assert detect_shape("let us explore the world") == "explore"
        assert detect_shape("出去转转") == "explore"

    def test_follow_and_resource_still_win(self) -> None:
        # 明确的"跟着我"仍然是 follow；明确的方块目标仍然是 resource
        assert detect_shape("跟着我，我们去探索") == "follow"
        assert detect_shape("去砍点橡木探索一下") == "resource"
        assert detect_shape("你好呀") == "unknown"


# ---------------------------------------------------------------- 规划器六值


class TestExplorePlanning:
    async def test_explore_plan_is_ready_and_bounded(self) -> None:
        rig = PlanRig()
        result = await rig.service.planner.plan(
            "有点想去外面转转 想起：上次说要探索地图", observe=rig.observe
        )
        assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
        assert result.plan is not None
        steps = result.plan.plan.steps
        tools = [step.tool for step in steps]
        # 第一版只用 SAFE + LOW：观察型移动 + 抬头看一眼 + 到达后一次 SAFE 观察，
        # 没有任何世界修改（Phase 7F.2 的 step_3 minecraft_world）。
        assert tools == ["minecraft_move_to", "minecraft_look_at", "minecraft_world"]
        assert all(step.risk in {"SAFE", "LOW"} for step in steps)
        assert not any(step.risk in {"MEDIUM", "HIGH", "DESTRUCTIVE"} for step in steps)
        expected = result.plan.plan.expected_final_state
        assert expected.inventory_delta == {}
        assert expected.position_within is not None
        assert expected.position_within["radius"] == 2.0

    async def test_explore_target_is_within_budget(self) -> None:
        rig = PlanRig()
        result = await rig.service.planner.plan("去外面探索一下", observe=rig.observe)
        move = result.plan.plan.steps[0]
        # 罐头在 (-8,70,5)，兴趣点在 (-8,70,21) → 距离 16 ≤ 24 的有界预算
        assert abs(move.arguments["z"] - 5) <= 24
        assert abs(move.arguments["x"] - (-8)) <= 24

    async def test_explore_without_observe_is_blocked_precondition(self) -> None:
        planner = BoundedAgentPlanner()
        result = await planner.plan("去外面探索一下", observe=None)
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION
        assert result.reason == "observe_unavailable"
        assert result.plan is None

    async def test_explore_blocked_when_low_disabled(self) -> None:
        rig = PlanRig()
        planner = BoundedAgentPlanner(allow_low=False)
        result = await planner.plan("去外面探索一下", observe=rig.observe)
        assert result.outcome is PlanningOutcome.BLOCKED_BY_POLICY
        assert result.plan is None

    async def test_explore_offline_world_is_precondition(self) -> None:
        async def offline(tool: str, arguments: Any) -> Any:
            return type(
                "R",
                (),
                {"result": {"available": False, "online": False}, "summary": ""},
            )()

        planner = BoundedAgentPlanner()
        result = await planner.plan("去外面探索一下", observe=offline)
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION

    async def test_explore_observation_failure_is_precondition(self) -> None:
        async def bad(tool: str, arguments: Any) -> Any:
            raise ObservationFailed("世界视图里没有罐头自己的坐标")

        planner = BoundedAgentPlanner()
        result = await planner.plan("去外面探索一下", observe=bad)
        assert result.outcome is PlanningOutcome.BLOCKED_BY_PRECONDITION
        assert result.plan is None


# ---------------------------------------------------------------- 两道门


class TestExploreGates:
    async def test_life_explore_plan_never_executes(self) -> None:
        rig = PlanRig()
        out = await rig.service.plan_from_proposal(
            proposal(objective="有点想去外面转转 想起：上次说要探索地图")
        )
        assert out["action"] == "planned"
        plan = out["plan"]
        assert plan.status == PlanStatus.READY_FOR_APPROVAL.value
        assert plan.task_id == ""
        assert rig.runtime.created == [], "LIFE 探索只规划，绝不建任务/执行"

    async def test_unapproved_explore_cannot_execute(self) -> None:
        """第一道门之前：只有 AgentPlan，没有 Task —— 没有可执行的入口。"""
        rig = PlanRig()
        out = await rig.service.plan_from_proposal(
            proposal(objective="有点想去外面转转 想起：上次说要探索地图")
        )
        assert out["action"] == "planned"
        assert await rig.service.plan_for_task("") is None
        assert rig.runtime.created == []

    async def test_approval_creates_pending_task_but_not_execution(self) -> None:
        rig = PlanRig()
        planned = await rig.service.plan_from_proposal(
            proposal(objective="有点想去外面转转 想起：上次说要探索地图")
        )
        plan = planned["plan"]
        out = await rig.service.approve(
            plan_id=plan.plan_id, user_id="10001", session_id="private:10001"
        )
        assert out["action"] == "approved"
        assert rig.runtime.created[-1]["steps"] == [
            "minecraft_move_to",
            "minecraft_look_at",
            "minecraft_world",
        ]
        # 第二道门：任务停在 PENDING_CONFIRMATION，没有真正执行
        assert str(out["record"].state.value) == "PENDING_CONFIRMATION"


# ---------------------------------------------------------------- 执行接线（真机门禁抓到的缺口）


class TestExploreServiceRoutes:
    """任务适配器的服务路由表必须覆盖探索计划里的**每一个**工具。

    真机门禁抓到：``minecraft_look_at`` 是已注册工具，但 ``_SERVICE_ROUTES`` 漏了它，
    于是 plan 校验通过、真实执行到第 2 步才 KeyError。这里把"计划里能用 = 真能调用"钉死。
    """

    def test_every_explore_step_tool_has_a_service_route(self) -> None:
        from app.integrations.minecraft.task_adapter import _SERVICE_ROUTES

        for tool in ("minecraft_move_to", "minecraft_look_at", "minecraft_world"):
            assert tool in _SERVICE_ROUTES, f"{tool} 没有服务路由，真实执行会失败"

    def test_look_at_route_calls_service_look_at(self) -> None:
        import asyncio

        from app.integrations.minecraft.task_adapter import _SERVICE_ROUTES

        calls: list[tuple[Any, Any, Any]] = []

        class FakeService:
            async def look_at(self, x: Any, y: Any, z: Any) -> dict[str, Any]:
                calls.append((x, y, z))
                return {"status": "SUCCEEDED"}

        result = asyncio.run(
            _SERVICE_ROUTES["minecraft_look_at"](FakeService(), {"x": 1.5, "y": 65.0, "z": -3.0})
        )
        assert result["status"] == "SUCCEEDED"
        assert calls == [(1.5, 65.0, -3.0)]
