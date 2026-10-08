"""Phase 6D §十九-§二十五：硬规则对模型提案的再验证（矩阵 J/K/L + U/V/W 的边界）。

**规则永远赢** —— 模型越权、越额度、撞锚点、撞冷却、候选不合格，一律整条拒绝并回退规则。
"""

from __future__ import annotations

from typing import Any

from app.activity.model_advisor import ModelFailureCode
from tests.activity_model_fakes import ScriptedProvider, proposal_json, rig_with_advisor

PROFILE = (600.0, 1800.0, 7200.0)


async def in_window(provider: Any, *, hour: int, minute: int = 0, activity: str = "gaming"):
    rig = rig_with_advisor(provider, hour=hour, minute=minute)
    episode = await rig.runtime.start(activity_name=activity, duration=PROFILE, now=rig.clock.now())
    rig.clock.advance_minutes(31)
    return rig, episode


async def tick(rig: Any) -> dict[str, Any]:
    current = await rig.runtime.advance()
    view = rig.runtime.decision_view(current)
    return dict(view.get("last_decision") or {})


class TestExtensionValidation:
    async def test_j_extension_across_a_hard_anchor_is_rejected(self) -> None:
        """§二十二：模型想 extend 60 分钟，但午饭锚点正在窗口里 → 规则拒绝（不硬推时间线）。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=60)])
        rig, _episode = await in_window(provider, hour=12, minute=20)  # 午饭窗口 11:15–12:45
        trace = await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["attempted"] is True
        assert receipt["accepted"] is False
        assert receipt["rejection_reason"] == ModelFailureCode.RULE_REJECTED
        assert trace["model_rejected"] is True
        assert trace["fallback_used"] is True

    async def test_j_control_without_anchor_is_accepted(self) -> None:
        """对照组：同一提案在没有锚点临期时会被采纳（证明拒绝确实来自锚点，而不是"什么都不给过"）。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=20)])
        rig, _episode = await in_window(provider, hour=14)
        trace = await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["accepted"] is True
        assert trace["decision"] == "EXTEND"
        assert trace["model_attempted"] is True
        assert trace["fallback_used"] is False

    async def test_g_extension_beyond_the_budget_is_rejected(self) -> None:
        """§二十一：超出剩余延长额度 → **拒绝**（绝不偷偷 clamp 成模型答案）。"""
        provider = ScriptedProvider(scripted=[proposal_json("extend", extension_minutes=600)])
        rig, episode = await in_window(provider, hour=14)
        before_end = float(episode.planned_end_at or 0.0)
        trace = await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["attempted"] is True
        assert receipt["accepted"] is False
        assert receipt["rejection_reason"] == ModelFailureCode.RULE_REJECTED
        assert trace["decision"] == "EXTEND"  # 走的是**规则**的延长
        current = await rig.runtime.current()
        assert current is not None
        # 规则只给了它自己的额度（typical = 30 分钟），绝不是模型的 600 分钟
        assert float(current.planned_end_at) == before_end + 1800.0


class TestTransitionValidation:
    async def test_k_bounce_blocked_candidate_is_rejected(self) -> None:
        """§二十三：模型想切换回**刚做过**的活动 → 撞车护栏拒绝。"""
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="reading")])
        rig = rig_with_advisor(provider, hour=14)
        # 让撞车冷却足够长，好把"刚做完 reading 就想切回去"这个场景真的卡住
        from app.activity.decision import ActivityBounceGuard

        rig.runtime.engine.bounce = ActivityBounceGuard(  # type: ignore[union-attr]
            cooldown_seconds=3600.0, clock=rig.clock
        )
        first = await rig.runtime.start(
            activity_name="reading", duration=PROFILE, now=rig.clock.now()
        )
        rig.clock.advance_minutes(20)
        await rig.runtime.switch_to(activity_name="gaming", duration=PROFILE)
        rig.clock.advance_minutes(31)
        trace = await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert first is not None
        assert receipt["attempted"] is True
        assert receipt["accepted"] is False
        assert receipt["rejection_reason"] == ModelFailureCode.RULE_REJECTED
        assert trace["next_activity_hint"] != "reading"

    async def test_l_ineligible_candidate_is_rejected(self) -> None:
        """§二十四/§五十一：候选表里 eligible=false 的活动，模型提了也不算。"""
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="eating")])
        rig, _episode = await in_window(provider, hour=14)  # 14:00 的三餐锚点不可挪 → eating 不合格
        trace = await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["accepted"] is False
        assert receipt["rejection_reason"] == ModelFailureCode.RULE_REJECTED
        assert trace["next_activity_hint"] != "eating"

    async def test_transition_to_the_current_activity_is_rejected(self) -> None:
        provider = ScriptedProvider(scripted=[proposal_json("transition", next_hint="gaming")])
        rig, _episode = await in_window(provider, hour=14)
        await tick(rig)
        receipt = rig.runtime.advisor_view()["last_receipt"]
        assert receipt["accepted"] is False


class TestBoundaries:
    def test_u_the_advisor_module_has_no_world_entry_points(self) -> None:
        """§四十四/§七十七/§九十二：顾问模块不许碰工具 / Minecraft / 任务 / 确认门 / Policy。"""
        import ast
        from pathlib import Path

        path = Path(__file__).resolve().parents[1] / "app" / "activity" / "model_advisor.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        forbidden_modules = (
            "app.tools",
            "app.tasks",
            "app.integrations.minecraft",
            "app.agent",
            "app.core",
            "app.sandbox",
            "app.web",
        )
        forbidden_calls = {
            "confirm_and_start",
            "execute_action",
            "move_to",
            "dig",
            "place_block",
            "send_message",
            "set_activity",
            "create_task",
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
                assert name not in forbidden_calls, name
        source = path.read_text(encoding="utf-8")
        for forbidden in ("allow_medium", "ConfirmationStore", "Policy", "TaskRuntime"):
            assert forbidden not in source, forbidden

    def test_v_the_advisor_cannot_reach_the_task_runtime(self) -> None:
        """§四十三/§四十六/§四十七：只有只读 context + provider，没有任务/执行句柄。"""
        import inspect

        from app.activity.model_advisor import ActivityModelAdvisor

        params = set(inspect.signature(ActivityModelAdvisor).parameters)
        assert params.isdisjoint(
            {
                "tasks",
                "task_runtime",
                "confirmation",
                "confirmation_store",
                "policy",
                "minecraft",
                "minecraft_service",
                "agent",
                "agent_bridge",
                "tools",
                "tool_orchestrator",
                "action_runtime",
            }
        )

    def test_w_allow_medium_is_untouched(self) -> None:
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "app" / "activity"
        for path in root.glob("*.py"):
            assert "allow_medium" not in path.read_text(encoding="utf-8"), path.name

    def test_u_runtime_has_no_minecraft_handle(self) -> None:
        from tests.activity_plan_fakes import PlanRig

        rig = PlanRig()
        assert not hasattr(rig.runtime, "minecraft")
        assert not hasattr(rig.runtime, "tasks")
