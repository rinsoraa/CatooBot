"""Phase 6C §二十-§二十五/§三十八/§五十三/§五十四：持久目标（PersistentGoal）。

关键：6C **不建第二套目标系统**（§二）—— ``PersistentGoal`` 是既有沙盒目标层的**只读投影**。
所以这里断言的不只是字段，还有三条边界：

* Goal ≠ Task（§二十一）：目标层没有任何"执行"入口；
* Goal 只影响排名（§二十二）：相关度只是一个 0~1 的数；
* Goal 不得覆盖生活/记忆不得成为权限（§二十三/§五十四）：能量低时目标也压不过休息。
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from app.activity import (
    GOAL_KIND_AFFINITY,
    GoalSnapshot,
    GoalStatus,
    NullGoalSource,
    PersistentGoal,
    SandboxGoalSource,
    build_goal,
)
from app.activity.goals import GOAL_KIND_TITLE, normalize_status
from tests.activity_plan_fakes import BrokenGoalSource, GoalSourceStub, State, goal

GOALS_MODULE = Path(__file__).resolve().parents[1] / "app" / "activity" / "goals.py"


@dataclass
class _SandboxGoal:
    """既有沙盒目标的最小替身（只有属性，没有方法 —— 强调"只读投影"）。"""

    goal_id: str
    kind: str = "restock_resource"
    status: str = "pending"
    priority: float = 0.5
    progress: float = 0.0
    reason: str = ""
    source: str = "unfinished_task"
    target_item: str = ""
    target_project: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0


# ---------------------------------------------------------------- 模型


class TestPersistentGoalModel:
    def test_required_fields_from_the_taskbook(self) -> None:
        """§二十：id / title / description / priority / progress / status / 时间戳。"""
        item = goal(goal_id="g-1", priority=0.7, progress=0.4)
        assert isinstance(item, PersistentGoal)
        assert item.goal_id == "g-1"
        assert item.title
        assert item.description
        assert item.priority == 0.7
        assert item.progress == 0.4
        assert item.status is GoalStatus.ACTIVE
        assert item.created_at >= 0
        assert item.updated_at >= 0

    def test_progress_uses_one_scale_only(self) -> None:
        """§二十四：统一 0.0~1.0（与既有沙盒目标同口径，而不是 0~100 两套并存）。"""
        assert build_goal(goal_id="g", kind="pet_care", progress=1.5).progress == 1.0
        assert build_goal(goal_id="g", kind="pet_care", progress=-3).progress == 0.0

    def test_status_normalization_matches_the_existing_layer(self) -> None:
        """§二十 + §二：既有层的状态名映射到本层（唯一口径）。"""
        assert normalize_status("pending") is GoalStatus.ACTIVE
        assert normalize_status("active") is GoalStatus.ACTIVE
        assert normalize_status("blocked") is GoalStatus.PAUSED
        assert normalize_status("completed") is GoalStatus.COMPLETED
        assert normalize_status("cancelled") is GoalStatus.CANCELLED
        assert normalize_status("expired") is GoalStatus.EXPIRED
        # 不认识的名字**保守**当成 ACTIVE（宁可不加分，也不丢目标）
        assert normalize_status("weird") is GoalStatus.ACTIVE
        assert GoalStatus.ACTIVE.open and GoalStatus.PAUSED.open
        assert not GoalStatus.COMPLETED.open and not GoalStatus.CANCELLED.open

    def test_title_is_deterministic_and_includes_the_target(self) -> None:
        titled = build_goal(goal_id="g", kind="complete_project", target_project="小屋")
        assert titled.title == "推进自己的项目（小屋）"
        explicit = build_goal(goal_id="g", kind="complete_project", title="我自己写的")
        assert explicit.title == "我自己写的"
        assert set(GOAL_KIND_TITLE) >= {"restock_resource", "pet_care", "complete_project"}

    def test_goal_is_read_only(self) -> None:
        """§二十五：Goal 上**没有**任何改状态的方法（完成条件只能来自结构化来源）。"""
        for forbidden in ("complete", "cancel", "set_progress", "update", "save", "delete"):
            assert not hasattr(PersistentGoal, forbidden)


# ---------------------------------------------------------------- 相关度


class TestGoalRelevance:
    def test_affinity_shapes_the_relevance(self) -> None:
        """§三十八：目标只对**相关活动**加成（building / working 之于项目目标）。"""
        item = goal(kind="complete_project", priority=1.0, progress=0.0)
        assert item.affinity == GOAL_KIND_AFFINITY["complete_project"] == ("building", "working")
        assert item.relevance_for("building") > 0
        assert item.relevance_for("sleeping") == 0.0
        assert item.relevance_for("gaming") == 0.0

    def test_priority_and_progress_scale_it(self) -> None:
        """越重要、越没做完 → 越值得推一把（确定性线性，不做玄学）。"""
        low = goal(kind="complete_project", priority=0.2, progress=0.5)
        high = goal(kind="complete_project", priority=1.0, progress=0.0)
        assert high.relevance_for("building") > low.relevance_for("building")
        done = goal(kind="complete_project", priority=1.0, progress=1.0)
        assert done.relevance_for("building") == 0.0

    def test_snapshot_picks_the_strongest_sponsor(self) -> None:
        snapshot = GoalSnapshot(
            goals=(
                goal(goal_id="weak", kind="complete_project", priority=0.3),
                goal(goal_id="strong", kind="complete_project", priority=0.9),
            ),
            source="stub",
        )
        value, sponsor = snapshot.relevance_for("building")
        assert sponsor == "strong" and value > 0

    def test_closed_goals_never_add_relevance(self) -> None:
        """完成的/取消的目标不再推任何活动（但历史仍在快照里，可审计）。"""
        snapshot = GoalSnapshot(
            goals=(
                goal(goal_id="done", kind="complete_project", status="completed", priority=1.0),
                goal(goal_id="off", kind="complete_project", status="cancelled", priority=1.0),
            ),
            source="stub",
        )
        assert snapshot.open_goals == ()
        assert snapshot.relevance_for("building") == (0.0, "")
        assert len(snapshot.goals) == 2

    def test_goal_changes_never_override_energy_hard_rules(self) -> None:
        """§二十三 + §二十八：goal priority=100 也压不过"能量太低只能休息"。"""
        from app.activity import ActivityPlanner, PlannerContext, RejectionReason

        exhausted = State(energy=0.1)
        source = GoalSourceStub(goals=(goal(kind="complete_project", priority=1.0),))
        clock_state = exhausted
        planner = ActivityPlanner(goal_source=source, state_provider=lambda: clock_state)
        from tests.activity_plan_fakes import clock_at

        clock = clock_at(14)
        plan = planner.plan_next(
            clock_state,
            None,
            now=clock.now(),
            context=PlannerContext(
                clock=clock,
                now=clock.now(),
                character_state=clock_state,
                goals=source.snapshot(),
                anchors=planner.anchors,
            ),
        )
        building = next(c for c in plan.candidates if c.activity == "building")
        assert building.eligible is False
        assert building.reason == RejectionReason.ENERGY_TOO_LOW.value
        # 而且计划里绝不会有 building
        assert all(item.activity != "building" for item in plan.items)


# ---------------------------------------------------------------- 快照与来源


class TestGoalSnapshot:
    def test_signature_changes_only_when_content_changes(self) -> None:
        """§八 触发点 4 靠它判断"目标变了没"（便宜、稳定）。"""
        same_a = GoalSnapshot(goals=(goal(goal_id="g", progress=0.4),), source="stub")
        same_b = GoalSnapshot(goals=(goal(goal_id="g", progress=0.4),), source="stub")
        moved = GoalSnapshot(goals=(goal(goal_id="g", progress=0.6),), source="stub")
        assert same_a.signature() == same_b.signature()
        assert same_a.signature() != moved.signature()

    def test_signature_ignores_ordering(self) -> None:
        first = GoalSnapshot(goals=(goal(goal_id="a"), goal(goal_id="b")), source="stub")
        second = GoalSnapshot(goals=(goal(goal_id="b"), goal(goal_id="a")), source="stub")
        assert first.signature() == second.signature()

    def test_null_source_is_empty_and_harmless(self) -> None:
        """没有目标层（沙盒关了）时，Planner 照样能规划。"""
        snapshot = NullGoalSource().snapshot()
        assert snapshot.goals == ()
        assert snapshot.open_goals == ()
        assert snapshot.relevance_for("building") == (0.0, "")
        assert snapshot.to_payload()["open_count"] == 0

    def test_sandbox_source_reads_the_existing_layer(self) -> None:
        """§二：复用既有沙盒目标层（鸭子类型，不 import app.sandbox）。"""

        class FakeManager:
            def __init__(self) -> None:
                self.received: list[object] = []

            def all(self, statuses: object = None) -> list[object]:
                self.received.append(statuses)
                return [
                    _SandboxGoal(
                        goal_id="sg-1",
                        kind="complete_project",
                        status="active",
                        priority=0.9,
                        progress=0.3,
                        reason="她想把小屋搭完",
                        target_project="小屋",
                    )
                ]

        manager = FakeManager()
        snapshot = SandboxGoalSource(manager=manager).snapshot()
        assert snapshot.source == "sandbox"
        assert len(snapshot.goals) == 1
        projected = snapshot.goals[0]
        assert projected.goal_id == "sg-1"
        assert projected.status is GoalStatus.ACTIVE
        assert projected.progress == 0.3
        assert projected.description == "她想把小屋搭完"
        assert projected.affinity == ("building", "working")
        assert projected.title == "推进自己的项目（小屋）"

    def test_sandbox_source_without_a_manager_is_empty(self) -> None:
        assert SandboxGoalSource(manager=None).snapshot().goals == ()

    def test_sandbox_source_degrades_on_error(self) -> None:
        """§四十八：读目标失败只降级（空快照 + 原因），绝不抛给规划。"""

        class Exploding:
            def all(self, statuses: object = None) -> list[object]:
                raise RuntimeError("boom")

        source = SandboxGoalSource(manager=Exploding())
        snapshot = source.snapshot()
        assert snapshot.goals == ()
        assert snapshot.degraded_reason == "RuntimeError"
        assert source.degraded_reason == "RuntimeError"  # 降级原因挂在来源上，供 WebUI 显示

    def test_sandbox_source_falls_back_when_all_rejects_statuses(self) -> None:
        """老签名不接受 ``statuses`` 时退化成"全都要"（仍然只读）。"""

        class OldManager:
            def all(self) -> list[object]:
                return [_SandboxGoal(goal_id="old", kind="pet_care", status="pending")]

        snapshot = SandboxGoalSource(manager=OldManager()).snapshot()
        assert [item.goal_id for item in snapshot.goals] == ["old"]

    def test_broken_source_is_treated_as_empty_by_the_planner(self) -> None:
        from app.activity import ActivityPlanner, PlannerContext
        from tests.activity_plan_fakes import clock_at

        clock = clock_at(14)
        planner = ActivityPlanner(goal_source=BrokenGoalSource())
        plan = planner.plan_next(
            State(),
            None,
            now=clock.now(),
            context=PlannerContext(
                clock=clock, now=clock.now(), character_state=State(), anchors=planner.anchors
            ),
        )
        assert plan.items  # 规划照常
        assert all(item.goal_id == "" for item in plan.items)

    def test_payload_is_readable_and_auditable(self) -> None:
        snapshot = GoalSnapshot(goals=(goal(goal_id="g"),), source="stub")
        payload = snapshot.to_payload()
        assert payload["source"] == "stub"
        assert payload["open_count"] == 1
        assert payload["signature"]
        assert payload["goals"][0]["affinity"] == ["building", "working"]


# ---------------------------------------------------------------- 权限边界


class TestGoalIsNeverPermission:
    def test_goals_module_has_no_execution_entry_points(self) -> None:
        """§二十一/§六十六：目标层不许 import/调用任何执行入口（AST guard）。"""
        tree = ast.parse(GOALS_MODULE.read_text(encoding="utf-8"))
        forbidden_modules = (
            "app.tasks",
            "app.tools",
            "app.integrations.minecraft",
            "app.sandbox",
            "app.agent",
            "app.ai",
        )
        forbidden_names = {
            "confirm_and_start",
            "execute",
            "send_message",
            "start_task",
            "allow_medium",
            "set_activity",
        }
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                module = str(node.module or "")
                assert not module.startswith(forbidden_modules), module
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith(forbidden_modules), alias.name
            elif isinstance(node, ast.Call):
                name = ""
                if isinstance(node.func, ast.Name):
                    name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    name = node.func.attr
                assert name not in forbidden_names, name

    def test_goal_source_is_read_only_by_contract(self) -> None:
        for forbidden in ("create", "cancel", "complete", "update", "delete", "advance"):
            assert not hasattr(SandboxGoalSource, forbidden)
            assert not hasattr(NullGoalSource, forbidden)

    def test_goals_never_touch_allow_medium_or_policy(self) -> None:
        """§五十四：记忆/目标都只是上下文 —— 绝不改变 ``allow_medium``。"""
        source = GOALS_MODULE.read_text(encoding="utf-8")
        assert "allow_medium" not in source
        assert "Policy" not in source
