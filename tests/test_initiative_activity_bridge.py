"""Phase 7A §三十四-§三十六/§五十四/§五十五：LifeIntent ↔ Activity 的桥。

矩阵：N（只能变成 Activity **候选**）/ O（绝不变成 Task）/ P（绝不变成 Minecraft 动作）。

结论先写在这里：7A 的桥是**单向、只读、无强制力**的 ——
Initiative 只能把"活动名 → 加成"交给 ActivityPlanner（6C），
它既没有 force_transition，也没有 TaskRuntime / ActionRuntime 的入口（§五十五）。
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

from app.initiative import InitiativeGate, LifeIntentService
from app.initiative.candidates import LONG_IDLE_SECONDS
from tests.initiative_fakes import Rig, goal

PACKAGE = Path(__file__).resolve().parent.parent / "app" / "initiative"

#: 这些名字在整包里**一个都不许出现**（AST 级，docstring 里提不算）
FORBIDDEN_NAMES = (
    "TaskRuntime",
    "ActionRuntime",
    "ConfirmationStore",
    "MinecraftService",
    "confirm_and_start",
    "create_task",
    "allow_medium",
    "force_transition",
    "Policy",
)


def _reference_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            found.add(node.id)
        elif isinstance(node, ast.Attribute):
            found.add(node.attr)
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            found.add(node.func.id)
    return found


class TestActivityBridge:
    async def test_n_intent_can_only_offer_a_candidate_activity(self) -> None:
        """N/§三十五/§五十四：意图只能给 ActivityPlanner 一个**候选加成**。"""
        rig = Rig()
        rig.feed(
            current_activity="idle",
            current_activity_elapsed=LONG_IDLE_SECONDS + 60,
            planner_candidates=("reading",),
        )
        out = await rig.check()
        assert out["action"] == "proposed"
        suggestions = rig.service.suggested_activities()  # type: ignore[union-attr]
        assert suggestions == {"reading": 0.84} or list(suggestions) == ["reading"]
        assert 0.0 < float(next(iter(suggestions.values()))) <= 1.0

    async def test_n_intent_never_touches_the_episode_itself(self) -> None:
        """N/§三十四：换不换活动仍然由 6B 说了算 —— 这一层不许有 force_transition。"""
        service = Rig().service
        assert service is not None
        for name in ("force_transition", "transition", "start", "apply_decision", "complete"):
            assert not hasattr(service, name), name

    async def test_n_minecraft_interest_offers_no_activity_name(self) -> None:
        """§三十六：MINECRAFT_INTEREST 只上交"想玩"这件事，不冒充一个活动名。"""
        rig = Rig()
        rig.feed(goals=(goal(kind="complete_project", label="小城"),))
        await rig.check()
        assert rig.service.suggested_activities() == {}  # type: ignore[union-attr]

    async def test_o_package_has_no_task_entry_points(self) -> None:
        """O/§四十一/§六十八：整包里不许出现任何任务创建 / 执行入口。"""
        for path in sorted(PACKAGE.glob("*.py")):
            names = _reference_names(path)
            for forbidden in ("TaskRuntime", "create_task", "confirm_and_start"):
                assert forbidden not in names, f"{path.name} 引用了 {forbidden}"

    async def test_p_package_has_no_minecraft_entry_points(self) -> None:
        """P/§三十六/§六十八：整包里不许出现 Minecraft 服务 / 动作运行时。"""
        for path in sorted(PACKAGE.glob("*.py")):
            names = _reference_names(path)
            for forbidden in ("MinecraftService", "ActionRuntime", "allow_medium"):
                assert forbidden not in names, f"{path.name} 引用了 {forbidden}"

    def test_service_constructor_has_no_execution_handles(self) -> None:
        """§四十三：构造函数签名里只有 store / config / 只读 provider —— 没有执行句柄。"""
        params = set(inspect.signature(LifeIntentService.__init__).parameters)
        forbidden = {
            "tasks",
            "task_runtime",
            "action_runtime",
            "minecraft",
            "policy",
            "confirmations",
            "confirmation_store",
            "tools",
            "sender",
            "qq",
            "engine",
            "ai",
        }
        assert not (params & forbidden), params & forbidden
        assert {"store", "config", "context_provider"} <= params

    def test_gate_constructor_is_stateless(self) -> None:
        """门禁是纯函数式的：**没有**自己的 ``__init__``（也就握不住任何 store / 执行器）。"""
        assert "__init__" not in InitiativeGate.__dict__
        assert set(inspect.signature(InitiativeGate).parameters) == set()

    async def test_no_forbidden_names_anywhere_in_the_package(self) -> None:
        """AST 扫描：上面那张禁止清单在整包里一条都不许出现。"""
        for path in sorted(PACKAGE.glob("*.py")):
            names = _reference_names(path)
            for forbidden in FORBIDDEN_NAMES:
                assert forbidden not in names, f"{path.name} 引用了 {forbidden}"
