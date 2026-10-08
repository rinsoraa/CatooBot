"""Phase 6A §十六/§十七/§二十九/§四十八/§四十九/§五十/§二十一：Minecraft 边界（矩阵 N/O + 安全）。

三条边界：

* **观察不是命令**（§十七）：Minecraft 的只读现状只能用来判断"这个 Episode 还合理吗"，
  绝不产生动作、绝不改 Episode 状态；
* **虚拟活动 ≠ 真实 Minecraft 行动**（§二十九/§四十八）：Minecraft 掉线时绝不凭空造
  ``minecraft_exploring`` 这类活动；
* **Activity 不是权限、也不碰记忆**（§五十/§二十一）：Policy 不认识它，
  它也不会把每个 Episode 都写进 Memory。
"""

from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any

import pytest

from app.activity import (
    ActivityPlanner,
    ActivityProjection,
    ActivityRuntime,
    ActivitySource,
    ActivityStatus,
    ActivityType,
    FakeClock,
    InMemoryActivityStore,
    TransitionReason,
)
from app.activity.adapters import (
    INTERACTION_MARKER,
    MinecraftObservationAdapter,
    SandboxActivityAdapter,
    TaskActivityAdapter,
    UserInteractionAdapter,
)
from app.activity.model import looks_like_minecraft_activity
from app.activity.planner import ROUTINE_BY_PERIOD

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
ACTIVITY_PACKAGE = REPO_ROOT / "app" / "activity"
#: Activity 这一层**不许**碰的世界动作（§四十九 的架构级测试）
FORBIDDEN_WORLD_ACTIONS = (
    "move_to",
    "dig",
    "place",
    "pickup",
    "follow_player",
    "equip",
    "inventory_move",
    "container_transfer",
    "craft",
    "look_at",
)


class FakeConnection:
    """与 MinecraftService 同形的只读替身（只暴露 snapshot / world_view）。"""

    def __init__(self, *, status: str = "ONLINE", players: tuple[str, ...] = ("空凛",)) -> None:
        self.enabled = True
        self._status = status
        self._players = players
        self.calls: list[str] = []

    def snapshot(self) -> dict[str, Any]:
        self.calls.append("snapshot")
        return {
            "connection": {
                "status": self._status,
                "host": "127.0.0.1",
                "port": 25565,
                "username": "Catodayo",
            },
            "action": {"status": "IDLE"},
        }

    def world_view(self) -> dict[str, Any]:
        self.calls.append("world_view")
        if self._status != "ONLINE":
            return {"semantic": {}}
        return {
            "semantic": {
                "self": {"position": {"x": 1.0, "y": 64.0, "z": 2.0}},
                "players": [{"name": name, "uuid": None} for name in self._players],
            }
        }


def build(service: Any = None) -> tuple[ActivityRuntime, MinecraftObservationAdapter, FakeClock]:
    clock = FakeClock(1_700_000_000.0)
    runtime = ActivityRuntime(
        store=InMemoryActivityStore(),
        clock=clock,
        character_id="罐头@deadbeef",
        planner=ActivityPlanner(),
        projection=ActivityProjection(None),
    )
    return runtime, MinecraftObservationAdapter(runtime, service), clock


class TestObservation:
    async def test_shape_is_read_only_facts(self) -> None:
        runtime, observer, _clock = build(FakeConnection())
        observation = await observer.observe(player_uuid="abc")
        assert observation["online"] is True
        assert observation["position"] == {"x": 1.0, "y": 64.0, "z": 2.0}
        assert observation["nearby_players"] == ["空凛"]
        assert observation["player_uuid"] == "abc"
        assert observation["current_action"] == {"status": "IDLE"}
        assert observation["observed_at"] > 0
        assert runtime.last_observation["online"] is True

    async def test_offline_reports_offline_and_no_position(self) -> None:
        runtime, observer, _clock = build(FakeConnection(status="DISCONNECTED"))
        observation = await observer.observe()
        assert observation["online"] is False
        assert observation["position"] is None
        assert observation["nearby_players"] == []

    async def test_missing_service_is_honest_not_optimistic(self) -> None:
        _runtime, observer, _clock = build(None)
        observation = await observer.observe()
        assert observation["online"] is False

    async def test_observation_never_changes_the_episode(self) -> None:
        """§十七：观察**不是命令** —— 看一眼世界不会创建/改变/终结任何活动。"""
        runtime, observer, clock = build(FakeConnection())
        await observer.observe()
        assert await runtime.current() is None  # 没有凭空开活动
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        await observer.observe()
        current = await runtime.current()
        assert current is not None
        assert current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE
        assert current.updated_at == episode.updated_at  # 连 updated_at 都没动

    async def test_plausibility_judgement_is_read_only(self) -> None:
        runtime, observer, clock = build(FakeConnection(status="DISCONNECTED"))
        await TaskActivityAdapter(runtime).on_task_event("task.started", {"task_id": "t1"})
        observation = await observer.observe()
        plausible, reasons = runtime.plausible(observation)
        assert plausible is False and "minecraft_offline" in reasons
        # 判断只是判断：Episode 还在 ACTIVE（该不该中断由任务事件/时钟说了算）
        current = await runtime.current()
        assert current is not None and current.status is ActivityStatus.ACTIVE


class TestVirtualVersusReal:
    def test_planner_table_has_no_minecraft_activities(self) -> None:
        """§二十九：日程表里绝不允许出现 Minecraft 活动名。"""
        for period, names in ROUTINE_BY_PERIOD.items():
            for name in names:
                assert not looks_like_minecraft_activity(name), f"{period}/{name}"

    def test_planner_refuses_a_minecraft_routine_table(self) -> None:
        with pytest.raises(ValueError):
            ActivityPlanner(routine={"morning": ("minecraft_exploring",)})

    async def test_virtual_activity_cannot_impersonate_minecraft(self) -> None:
        """§四十八：虚拟活动不许冒用真实 Minecraft 行动的名字。"""
        runtime, _observer, clock = build()
        for name in ("minecraft_digging", "minecraft_exploring", "minecraft_gathering"):
            assert await runtime.start(activity_name=name, now=clock.now()) is None
        assert await runtime.current() is None
        sandbox = SandboxActivityAdapter(runtime)
        assert await sandbox.observe_virtual_life("minecraft_exploring") is None

    async def test_offline_never_creates_minecraft_activity(self) -> None:
        """N：Minecraft 掉线 → 不会因为"看了世界一眼"就出现 Minecraft 活动。"""
        runtime, observer, clock = build(FakeConnection(status="DISCONNECTED"))
        for _ in range(5):
            await observer.observe()
        assert await runtime.current() is None
        for _ in range(3):
            created = await runtime.advance()
            assert created is not None
            assert not looks_like_minecraft_activity(created.activity_name)
            assert created.source is ActivitySource.ROUTINE

    async def test_task_activity_does_require_a_real_task(self) -> None:
        """任务型活动只由真实 task 事件产生（并且只引用 task_id，§十八/§十九）。"""
        runtime, _observer, _clock = build()
        adapter = TaskActivityAdapter(runtime)
        assert await adapter.on_task_event("task.started", {"task_id": ""}) is None
        assert await runtime.current() is None
        created = await adapter.on_task_event("task.started", {"task_id": "task_real"})
        assert created is not None
        assert created.activity_type is ActivityType.TASK_EXECUTION
        assert created.related_task_id == "task_real"


def imported_modules(path: pathlib.Path) -> set[str]:
    """这个模块真正 import 了哪些 app 包（AST 级别；docstring 里的名字不算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module)
        elif isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
    return found


def called_attributes(path: pathlib.Path) -> set[str]:
    """调用了哪些属性方法（``x.move_to(...)`` → ``move_to``）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    return {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }


def referenced_names(path: pathlib.Path) -> set[str]:
    """代码里出现的名字（AST 级别的 Name/Attribute，docstring 不算）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


class TestMemoryIsolation:
    def test_activity_never_imports_the_memory_engine(self) -> None:
        """§二十一：6A 不把 Activity 历史复制进 Memory —— 这一层连 memory 都不 import。"""
        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            modules = imported_modules(path)
            assert not any(name.startswith("app.memory") for name in modules), path.name
            names = referenced_names(path)
            assert "MemoryManager" not in names, path.name
            assert "minecraft_memory" not in names, path.name

    def test_runtime_takes_no_memory_manager(self) -> None:
        """构造参数里也没有"记忆"入口（不是"没写"，而是"拿不到"）。"""
        params = set(inspect.signature(ActivityRuntime.__init__).parameters)
        assert params.isdisjoint({"memory", "memory_manager", "memories", "retriever"})

    async def test_a_lifecycle_writes_no_memories(self) -> None:
        """跑一整段生命周期：没有任何东西被写进记忆（没有记忆对象也照样跑完）。"""
        runtime, _observer, clock = build()
        first = await runtime.advance()
        assert first is not None
        clock.advance_hours(2)
        second = await runtime.advance()
        assert second is not None
        await runtime.complete(now=clock.now())
        recent = await runtime.recent(10)
        # 只有 Episode 表里有东西。Phase 6C 起计划可能挑到**可延长**的活动（例如 reading），
        # 于是 2 小时后是 EXTEND 而不是换新 Episode —— 所以断言"生命周期真的推进过"，
        # 而不是钉死条数（这个用例关心的是**记忆隔离**，条数只是脚手架）。
        assert len(recent) >= 1
        assert recent[0].extension_count >= 1 or recent[0].status.terminal or len(recent) >= 2


class TestSecurityGuards:
    def test_decision_layer_has_no_permission_or_world_entry_points(self) -> None:
        """§五二（Phase 6B）：决策引擎这一层不许 import/调用任务确认、Policy 绕过、世界动作。"""
        forbidden_modules = (
            "app.integrations",
            "app.tools",
            "app.ai",
            "app.character",
            "app.tasks",
        )
        forbidden_names = (
            "MinecraftService",
            "ActionRuntime",
            "TaskRuntime",
            "ConfirmationStore",
            "confirm_and_start",
            "consume",
            "allow_medium",
            "Policy",
        )
        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            modules = imported_modules(path)
            # Phase 6D §四十四/§七十九：`model_advisor.py` 是**唯一**被批准的模型缝 ——
            # 它只允许引用 provider 抽象（app.ai），工具/任务/世界动作仍然一律禁止。
            seam = path.name == "model_advisor.py"
            for module in modules:
                if seam and module.startswith("app.ai"):
                    continue  # Phase 6D §四十四/§七十九：模型缝允许引用 provider 抽象
                assert not any(module.startswith(bad) for bad in forbidden_modules), (
                    f"{path.name} import 了 {module}"
                )
            names = referenced_names(path)
            for needle in forbidden_names:
                assert needle not in names, f"{path.name} 引用了 {needle}"

    def test_decision_engine_has_no_write_authority(self) -> None:
        """§二五：决策引擎自己**不能**改 Episode 状态 —— 它只产出决策与 trace。"""
        from app.activity.decision import ActivityDecisionEngine

        for forbidden in ("start", "complete", "interrupt", "cancel", "expire", "extend"):
            assert not hasattr(ActivityDecisionEngine, forbidden)
        # 它也不认识 store（生命周期改动只由 ActivityRuntime 做）
        assert "store" not in set(
            __import__("inspect").signature(ActivityDecisionEngine.__init__).parameters
        )

    def test_activity_layer_has_no_world_action_symbols(self) -> None:
        """§四十九：架构级 guard（AST 级）—— 这一层不许调用任何世界动作、也不许 import 它们。

        用 AST 而不是文本匹配：docstring 里可以**解释**这些名字，但代码里绝不能有。
        """
        for path in sorted(ACTIVITY_PACKAGE.glob("*.py")):
            modules = imported_modules(path)
            for module in modules:
                assert not module.startswith("app.integrations"), f"{path.name} import {module}"
                assert not module.startswith("app.tools"), f"{path.name} import {module}"
                if path.name == "model_advisor.py" and module.startswith("app.ai"):
                    continue  # Phase 6D §四十四：唯一被批准的模型缝
                assert not module.startswith("app.ai"), f"{path.name} import {module}"
                assert not module.startswith("app.character"), f"{path.name} import {module}"
            calls = called_attributes(path)
            for action in FORBIDDEN_WORLD_ACTIONS:
                assert action not in calls, f"{path.name} 调用了世界动作 {action}()"
            names = referenced_names(path)
            assert "MinecraftService" not in names, f"{path.name} 引用了 MinecraftService"

    def test_observer_only_calls_read_methods(self) -> None:
        """观察器只允许调用 ``snapshot()`` / ``world_view()`` 这两个只读方法。"""
        path = ACTIVITY_PACKAGE / "adapters.py"
        calls = called_attributes(path)
        assert {"snapshot", "world_view"} <= calls
        assert not (calls & set(FORBIDDEN_WORLD_ACTIONS))

    @pytest.mark.parametrize(
        "module",
        [
            "app/integrations/minecraft/agent.py",
            "app/tools/executor.py",
            "app/integrations/minecraft/confirmation.py",
        ],
    )
    def test_policy_layer_does_not_know_activity(self, module: str) -> None:
        """§五十：授权/策略链路里根本没有"活动"这个概念（不是靠自觉，是靠没有入口）。"""
        path = REPO_ROOT / module
        if not path.exists():
            pytest.skip(f"{module} 不存在")
        source = path.read_text(encoding="utf-8")
        forbidden = ("ActivityRuntime", "ActivityEpisode", "activity_episodes", "activity_status")
        for needle in forbidden:
            assert needle not in source, f"{module} 不应认识 {needle}"

    async def test_interaction_only_records(self) -> None:
        """§三十八/§三十九：用户交互只记观察，不改 Activity。"""
        runtime, _observer, clock = build()
        episode = await runtime.start(activity_name="reading", now=clock.now())
        assert episode is not None
        observation = await UserInteractionAdapter(runtime).note_interaction(
            session_id="private:1", source="qq", at=clock.now()
        )
        assert observation[INTERACTION_MARKER] == clock.now()
        current = await runtime.current()
        assert current is not None
        assert current.episode_id == episode.episode_id
        assert current.status is ActivityStatus.ACTIVE
        assert current.transition_reason == TransitionReason.SCHEDULED.value
