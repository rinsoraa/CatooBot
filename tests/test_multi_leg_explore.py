"""Phase 7F.3：有界**多段**探索的逐段到达验证 / 失败止步 / 授权覆盖测试。

与 7F.1 的单段测试（tests/test_task_runtime_explore.py）分开：这里测的是
"每一段都必须被独立验证，任何一段没到位就绝不再走后面的路点"。

三条硬性质（本文件的断言全部围绕它们，且都检查**真实动作序列**而不只看最终状态）：

1. 逐段独立证据：每一段的到达由重新读到的真实世界坐标判定，并落进持久化的
   record.leg_verifications（恢复/重放不会丢）；
2. 失败即止步：中途任何一段没到位 / 读不到世界 -> 后面的 move_to **一次都不发生**；
3. 授权覆盖：逐段后置条件冻结在计划里（进 plan_hash 与确认指纹），批准后改动会被检测。

多段计划用**真实 planner**（plan_exploration_task(legs=...)）生成，执行用**真实
TaskRuntime** + InMemoryTaskStore。注意内存 store 也走 to_payload/from_payload 往返
（docs: app/tasks/store.py:88 的 _copy），所以"证据没持久化"这类错误会在这里直接暴露。
"""

from __future__ import annotations

from typing import Any

from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome
from app.tasks.models import (
    ExpectedFinalState,
    TaskFailure,
    TaskPlan,
    TaskState,
    TaskStep,
)
from app.tasks.planner import plan_exploration_task
from app.tasks.runtime import TaskConfig, TaskInvocation, TaskRuntime
from app.tasks.store import InMemoryTaskStore
from tests.test_task_runtime import FakeConfirmation

REGISTRY = {
    "minecraft_world": "SAFE",
    "minecraft_look_at": "SAFE",
    "minecraft_move_to": "LOW",
    "minecraft_stop": "SAFE",
    "minecraft_inventory": "SAFE",
}

#: 规划起点与三个真实 POI：距起点 6 / 12 / 18 格，彼此相距 6 格。
START = (-8.0, 70.0, 5.0)
POIS = ((-8.0, 70.0, 11.0), (-8.0, 70.0, 17.0), (-8.0, 70.0, 23.0))


class _Reply:
    """SAFE 观察替身的返回形状（带 .result / .summary，与真实工具通道一致）。"""

    def __init__(self, result: dict[str, Any], summary: str = "") -> None:
        self.result = result
        self.summary = summary


def observe_with(
    *,
    start: tuple[float, float, float] = START,
    pois: tuple[tuple[float, float, float], ...] = POIS,
    offline: bool = False,
) -> Any:
    """规划期的 SAFE 观察替身：真实的 world 视图形状（self 坐标 + 兴趣点）。"""

    async def observe(tool: str, arguments: dict[str, Any]) -> Any:
        assert tool == "minecraft_world", tool
        if offline:
            return _Reply({"available": False, "online": False}, "offline")
        return _Reply(
            {
                "available": True,
                "online": True,
                "semantic": {
                    "self": {"position": {"x": start[0], "y": start[1], "z": start[2]}},
                    "points_of_interest": [
                        {"type": "minecraft:chest", "pos": {"x": px, "y": py, "z": pz}}
                        for px, py, pz in pois
                    ],
                    "terrain": [{"category": "grass", "direction": "north", "distance": 30.0}],
                },
            },
            "world",
        )

    return observe


async def build_route(
    *,
    legs: int = 3,
    pois: tuple[tuple[float, float, float], ...] = POIS,
    start: tuple[float, float, float] = START,
    max_distance: float = 24.0,
) -> TaskPlan:
    """用**真实 planner** 生成一条多段路线（不是手写计划）。"""

    planned = await plan_exploration_task(
        "去外面探索一下",
        observe=observe_with(start=start, pois=pois),
        legs=legs,
        max_distance=max_distance,
    )
    return planned.plan


class LegWorld:
    """按段编排的执行替身：第 i 次 move_to 决定"罐头实际到了哪里"。

    arrival[i] = False 表示第 i 段**动作自述成功但实际没到**（正是要拦住的情形）；
    detached_legs 里的段返回持续型动作（终态要靠 action 事件），用于取消/恢复用例；
    extra_poi 会出现在**执行期**的每一次世界读里，用来验证"新兴趣点不会追加路点"。
    """

    def __init__(
        self,
        *,
        start: tuple[float, float, float] = START,
        arrival: dict[int, bool] | None = None,
        default_arrived: bool = True,
        move_mode: str = "ok",
        world_mode: str = "ok",
        detached_legs: tuple[int, ...] = (),
        extra_poi: tuple[tuple[float, float, float], ...] = (),
    ) -> None:
        self.position = {"x": start[0], "y": start[1], "z": start[2]}
        self.arrival = dict(arrival or {})
        self.default_arrived = default_arrived
        self.move_mode = move_mode
        self.world_mode = world_mode
        self.detached_legs = tuple(detached_legs)
        self.extra_poi = tuple(extra_poi)
        self.moves: list[dict[str, float]] = []
        self.calls: list[str] = []

    def move_plan(self) -> list[tuple[int, int, int]]:
        """真实发生的 move_to 目标序列（按调用顺序）——失败止步的原始证据。"""
        return [(int(t["x"]), int(t["y"]), int(t["z"])) for t in self.moves]

    async def __call__(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        task_id: str = "",
        step_id: str = "",
        plan_hash: str = "",
        risk: str = "",
        authorization: Any = None,
    ) -> TaskInvocation:
        self.calls.append(tool)
        if tool == "minecraft_world":
            if self.world_mode == "offline":
                return TaskInvocation(
                    ok=True, status="SUCCEEDED", result={"available": False, "online": False}
                )
            semantic: dict[str, Any] = {
                "points_of_interest": [
                    {"type": "minecraft:chest", "pos": {"x": px, "y": py, "z": pz}}
                    for px, py, pz in self.extra_poi
                ]
            }
            if self.world_mode != "no_position":
                semantic["self"] = {"position": dict(self.position)}
            return TaskInvocation(
                ok=True,
                status="SUCCEEDED",
                result={"available": True, "online": True, "semantic": semantic},
            )
        if tool == "minecraft_move_to":
            target = {
                "x": float(arguments["x"]),
                "y": float(arguments["y"]),
                "z": float(arguments["z"]),
            }
            self.moves.append(target)
            index = len(self.moves)
            if self.move_mode == "fail":
                return TaskInvocation(
                    ok=False, status="FAILED", error="找不到路", code="minecraft.path_not_found"
                )
            # 先按到达策略更新"真实位置"（持续型动作稍后也会真的走完），再决定返回形状：
            # 这样 action 完成事件之后的逐段验证读到的就是这次移动的真实落点。
            if self.arrival.get(index, self.default_arrived):
                self.position = dict(target)
            else:
                # 动作自述成功，但真实坐标离目标 6 格（远超 2 格半径）
                self.position = {"x": target["x"] + 6.0, "y": target["y"], "z": target["z"]}
            if self.move_mode == "detached" or index in self.detached_legs:
                return TaskInvocation(
                    ok=True, status="RUNNING", action_id=f"act_{index}", summary="正在过去"
                )
            return TaskInvocation(
                ok=True, status="SUCCEEDED", result={"ok": True}, action_id=f"act_{index}"
            )
        if tool in {"minecraft_look_at", "minecraft_stop", "minecraft_inventory"}:
            return TaskInvocation(ok=True, status="SUCCEEDED", result={"ok": True})
        raise AssertionError(f"意外工具 {tool}")


def make_runtime(invoke: Any, *, store: Any = None, **overrides: Any) -> TaskRuntime:
    """与 7F.1 测试同构的 runtime；逐段验证读固定为单次读（可确定性地编排）。"""

    return TaskRuntime(
        store=store if store is not None else InMemoryTaskStore(),
        invoke=invoke,
        confirmations=overrides.pop("confirmations", None) or FakeConfirmation(),
        config=overrides.pop("config", None)
        or TaskConfig(position_verify_attempts=1, position_verify_interval_seconds=0.0),
        publish=lambda event, payload: None,
        risk_of=lambda tool: REGISTRY.get(tool, ""),
        is_registered=lambda tool: tool in REGISTRY,
        **overrides,
    )


async def run_route(
    plan: TaskPlan,
    invoke: Any,
    *,
    store: Any = None,
    runtime: TaskRuntime | None = None,
) -> tuple[TaskRuntime, Any]:
    """创建 -> 第一道门批准 -> 执行到底，返回 (runtime, 终态记录)。"""

    engine = runtime or make_runtime(invoke, store=store)
    record = await engine.create_task(
        plan.objective, session_id="private:10001", user_id="10001", origin="user", plan=plan
    )
    started = await engine.confirm_and_start(
        record.task_id, user_id="10001", session_id="private:10001", origin="user"
    )
    return engine, started


def leg_targets(plan: TaskPlan) -> list[dict[str, Any]]:
    return plan.expected_final_state.legs()


def raw_route_plan(
    legs: list[tuple[float, float, float]],
    *,
    start: tuple[float, float, float] = (0.0, 64.0, 0.0),
    budget: float = 24.0,
    radius: float = 2.0,
) -> TaskPlan:
    """手写一份多段计划（legs = [(x, z, 声明的段距离)]），用于构造边界/非法用例。"""

    steps: list[TaskStep] = []
    route_legs: list[dict[str, Any]] = []
    for index, (x, z, declared) in enumerate(legs, start=1):
        move_id = f"step_{2 * index - 1}"
        steps.append(
            TaskStep(
                step_id=move_id,
                tool="minecraft_move_to",
                arguments={"x": int(x), "y": int(start[1]), "z": int(z)},
                risk="LOW",
            )
        )
        steps.append(
            TaskStep(
                step_id=f"step_{2 * index}",
                tool="minecraft_look_at",
                arguments={"x": float(x), "y": float(start[1]) + 1.5, "z": float(z)},
                risk="SAFE",
            )
        )
        route_legs.append(
            {
                "index": index,
                "step_id": move_id,
                "position_within": {
                    "x": float(x),
                    "y": float(start[1]),
                    "z": float(z),
                    "radius": radius,
                },
                "distance": float(declared),
            }
        )
    steps.append(
        TaskStep(
            step_id=f"step_{2 * len(legs) + 1}",
            tool="minecraft_world",
            arguments={},
            risk="SAFE",
        )
    )
    final = dict(route_legs[-1]["position_within"])
    return TaskPlan(
        objective="多段探索",
        steps=steps,
        expected_final_state=ExpectedFinalState(
            position_within=final,
            route={
                "start": {"x": start[0], "y": start[1], "z": start[2]},
                "budget": budget,
                "legs": route_legs,
            },
        ),
    )


class TestRouteGeneration:
    async def test_three_legs_share_one_total_budget(self) -> None:
        plan = await build_route(legs=3)
        legs = leg_targets(plan)
        assert len(legs) == 3
        route = plan.expected_final_state.route
        assert route is not None
        assert route["budget"] == 24.0
        # 6 + 6 + 6 = 18 <= 24：共享**一份总预算**，不是每段各 24
        assert [leg["distance"] for leg in legs] == [6.0, 6.0, 6.0]
        assert sum(leg["distance"] for leg in legs) == 18.0
        # 路点就是三个真实 POI，最后一段 = 期望终态位置
        assert [leg["position_within"]["z"] for leg in legs] == [11.0, 17.0, 23.0]
        assert plan.expected_final_state.position_within["z"] == 23.0
        assert [step.step_id for step in plan.steps] == [
            "step_1",
            "step_2",
            "step_3",
            "step_4",
            "step_5",
            "step_6",
            "step_7",
        ]
        assert plan.steps[6].tool == "minecraft_world"

    async def test_route_shrinks_when_the_world_has_too_few_points(self) -> None:
        # 只有一个可用 POI -> 明确缩短成单段（绝不凑段数、绝不编造坐标）
        plan = await build_route(legs=3, pois=((-8.0, 70.0, 12.0),))
        assert leg_targets(plan) == []
        assert [step.step_id for step in plan.steps] == ["step_1", "step_2", "step_3"]

    async def test_shortens_to_two_legs_when_the_total_budget_runs_out(self) -> None:
        # max_distance=12：第 1 段 6 格 + 第 2 段 6 格就用满总预算 -> 第 3 段不许再走
        plan = await build_route(legs=3, max_distance=12.0)
        legs = leg_targets(plan)
        assert len(legs) == 2
        assert sum(leg["distance"] for leg in legs) == 12.0
        assert plan.expected_final_state.route["budget"] == 12.0

    async def test_single_leg_plan_is_unchanged_from_phase_7f1(self) -> None:
        # legs=1（默认）必须与 7F.1/7F.2 的单段计划**逐字节相同**：不带 route 字段、
        # plan_hash 不变 —— 这是"多段没有偷偷改掉既有被授权行为"的硬证据。
        plan = await build_route(legs=1)
        legacy = TaskPlan(
            objective="去外面探索一下",
            steps=[
                TaskStep(
                    step_id="step_1",
                    tool="minecraft_move_to",
                    arguments={"x": -8, "y": 70, "z": 11},
                    risk="LOW",
                ),
                TaskStep(
                    step_id="step_2",
                    tool="minecraft_look_at",
                    arguments={"x": -8.0, "y": 71.5, "z": 11.0},
                    risk="SAFE",
                ),
                TaskStep(step_id="step_3", tool="minecraft_world", arguments={}, risk="SAFE"),
            ],
            expected_final_state=ExpectedFinalState(
                position_within={"x": -8.0, "y": 70.0, "z": 11.0, "radius": 2.0}
            ),
        )
        assert "route" not in plan.expected_final_state.to_payload()
        assert plan.to_payload() == legacy.to_payload()
        assert plan.plan_hash == legacy.plan_hash


class TestPerLegVerification:
    async def test_every_leg_is_verified_by_a_fresh_real_coordinate_read(self) -> None:
        world = LegWorld()
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.SUCCEEDED
        assert record.failure == ""
        entries = record.leg_verifications
        assert [entry["index"] for entry in entries] == [1, 2, 3]
        assert all(entry["verified"] is True for entry in entries)
        for entry in entries:
            assert entry["task_id"] == record.task_id
            assert entry["step_id"].startswith("step_")
            # action_id：持续型动作（生产上 move_to 就是）会带真实 action id，
            # 由 detached 恢复用例单独断言；同步完成这一路没有 detached action id，
            # 字段仍然是字符串。
            assert isinstance(entry["action_id"], str)
            assert entry["position"]["distance"] == 0.0
            assert entry["position"]["z"] == entry["target"]["z"]
        assert world.move_plan() == [(-8, 70, 11), (-8, 70, 17), (-8, 70, 23)]

    async def test_leg_verification_happens_before_the_next_leg_moves(self) -> None:
        # 这是"崩溃恢复也安全"所依赖的顺序性质：第 1 段的到达验证必须发生在第 2 段的
        # move_to **之前**（否则恢复后会在没有上一段证据的情况下继续走）。
        world = LegWorld()
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.SUCCEEDED
        calls = world.calls
        first_verify = calls.index("minecraft_world")
        second_move = [index for index, tool in enumerate(calls) if tool == "minecraft_move_to"][1]
        assert first_verify < second_move

    async def test_world_reads_are_bounded_to_one_per_leg(self) -> None:
        # 每段一次验证读（attempts=1）+ 计划里的 world 步 + 终态复核 = 5 次：
        # 已完成的段不会被重复验证（幂等）。
        world = LegWorld()
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.SUCCEEDED
        assert world.calls.count("minecraft_world") == 5
        assert world.calls.count("minecraft_move_to") == 3
        assert world.calls.count("minecraft_look_at") == 3


class TestFailureStopsTheRoute:
    async def test_unarrived_first_leg_blocks_every_later_leg(self) -> None:
        world = LegWorld(arrival={1: False})
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value
        # 原始证据：只发起过第 1 段移动，第 2/3 段一次都没有发生
        assert world.move_plan() == [(-8, 70, 11)]
        entries = record.leg_verifications
        assert len(entries) == 1
        assert entries[0]["index"] == 1
        assert entries[0]["verified"] is False
        assert entries[0]["distance"] == 6.0
        assert entries[0]["failure"] == TaskFailure.VERIFICATION.value

    async def test_unarrived_middle_leg_blocks_the_rest(self) -> None:
        world = LegWorld(arrival={2: False})
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value
        assert world.move_plan() == [(-8, 70, 11), (-8, 70, 17)]
        assert [entry["verified"] for entry in record.leg_verifications] == [True, False]

    async def test_unreachable_first_leg_stops_as_action_failure(self) -> None:
        # 路径找不到 = 既有语义的安全停止（PAUSED + ACTION_FAILED），不是验证失败，
        # 也绝不冒充到达。
        world = LegWorld(move_mode="fail")
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.PAUSED
        assert record.failure == TaskFailure.ACTION_FAILED.value
        assert world.move_plan() == [(-8, 70, 11)]
        assert record.leg_verifications == []

    async def test_offline_world_blocks_before_the_next_leg(self) -> None:
        world = LegWorld(world_mode="offline")
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value
        assert world.move_plan() == [(-8, 70, 11)]
        assert record.leg_verifications[0]["verified"] is False
        assert record.leg_verifications[0]["position"] is None

    async def test_missing_self_position_blocks_before_the_next_leg(self) -> None:
        world = LegWorld(world_mode="no_position")
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VERIFICATION.value
        assert world.move_plan() == [(-8, 70, 11)]


class TestCancelAndRecovery:
    async def test_cancel_mid_route_leaves_no_residual_action_or_extra_leg(self) -> None:
        world = LegWorld(move_mode="detached")
        plan = await build_route(legs=3)
        runtime = make_runtime(world)
        record = await runtime.create_task(
            "去探索", session_id="s", user_id="u", origin="user", plan=plan
        )
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert started.state is TaskState.WAITING_ACTION
        assert world.move_plan() == [(-8, 70, 11)]
        cancelled = await runtime.cancel(started.task_id, reason="stop")
        assert cancelled.state is TaskState.CANCELLED
        assert "minecraft_stop" in world.calls
        before = len(world.calls)
        await runtime.drive(started.task_id)
        assert len(world.calls) == before
        assert world.move_plan() == [(-8, 70, 11)]

    async def test_detached_leg_is_verified_on_resume_and_keeps_action_id(self) -> None:
        # 与"进程恢复 -> resume -> drive"同一条代码路径：动作完成事件之后，逐段守卫
        # 必须在下一段移动**之前**补上第 1 段的到达验证。
        world = LegWorld(detached_legs=(1,))
        plan = await build_route(legs=3)
        runtime = make_runtime(world)
        record = await runtime.create_task(
            "去探索", session_id="s", user_id="u", origin="user", plan=plan
        )
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert started.state is TaskState.WAITING_ACTION
        resumed = await runtime.on_action_event(
            action_id="act_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert resumed is not None
        assert resumed.state is TaskState.SUCCEEDED
        assert [entry["index"] for entry in resumed.leg_verifications] == [1, 2, 3]
        assert resumed.leg_verifications[0]["action_id"] == "act_1"
        assert world.move_plan() == [(-8, 70, 11), (-8, 70, 17), (-8, 70, 23)]


class TestPersistenceAndReplay:
    async def test_leg_evidence_survives_a_store_round_trip(self) -> None:
        store = InMemoryTaskStore()
        world = LegWorld()
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world, store=store)
        assert record.state is TaskState.SUCCEEDED
        reloaded = await store.load(record.task_id)
        assert reloaded is not None
        assert reloaded.leg_verifications == record.leg_verifications
        assert [entry["index"] for entry in reloaded.leg_verifications] == [1, 2, 3]

    async def test_redrive_replays_nothing_and_keeps_the_evidence(self) -> None:
        store = InMemoryTaskStore()
        world = LegWorld()
        plan = await build_route(legs=3)
        runtime, record = await run_route(plan, world, store=store)
        assert record.state is TaskState.SUCCEEDED
        calls = list(world.calls)
        evidence = [dict(item) for item in record.leg_verifications]
        again = await runtime.drive(record.task_id)
        assert again.state is TaskState.SUCCEEDED
        assert world.calls == calls, "终态重放不得再发起任何动作"
        assert [dict(item) for item in again.leg_verifications] == evidence


class TestAuthorizationCoverage:
    async def test_plan_hash_covers_every_leg_target_and_distance(self) -> None:
        plan = await build_route(legs=3)
        baseline = plan.plan_hash
        legs = plan.expected_final_state.route["legs"]
        legs[1]["position_within"]["x"] += 1.0
        assert plan.plan_hash != baseline, "改了路点坐标必须换 plan_hash"
        legs[1]["position_within"]["x"] -= 1.0
        assert plan.plan_hash == baseline
        legs[2]["distance"] += 0.5
        assert plan.plan_hash != baseline, "改了段距离也必须换 plan_hash"

    async def test_post_approval_leg_tamper_is_detected_and_not_executed(self) -> None:
        store = InMemoryTaskStore()
        world = LegWorld(detached_legs=(1,))
        plan = await build_route(legs=3)
        runtime = make_runtime(world, store=store)
        record = await runtime.create_task(
            "去探索", session_id="s", user_id="u", origin="user", plan=plan
        )
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert started.state is TaskState.WAITING_ACTION
        stored = await store.load(record.task_id)
        assert stored is not None
        # 批准之后偷偷改第 2 段的路点（改的是**持久化**里的那一份）
        stored.plan.expected_final_state.route["legs"][1]["position_within"]["z"] += 4.0
        await store.save(stored)
        result = await runtime.on_action_event(
            action_id="act_1",
            event="minecraft.action.completed",
            status="SUCCEEDED",
            result={"ok": True},
        )
        assert result is not None
        assert result.state is TaskState.FAILED
        assert result.failure == TaskFailure.AUTHORIZATION.value
        assert world.move_plan() == [(-8, 70, 11)], "被改过的路点绝不被执行"

    async def test_new_interest_points_do_not_add_unapproved_waypoints(self) -> None:
        # 执行期世界视图里出现一个新兴趣点：只影响记忆回流，绝不追加未经批准的路点。
        world = LegWorld(extra_poi=((120.0, 70.0, 5.0),))
        plan = await build_route(legs=3)
        _, record = await run_route(plan, world)
        assert record.state is TaskState.SUCCEEDED
        assert world.move_plan() == [(-8, 70, 11), (-8, 70, 17), (-8, 70, 23)]
        assert len(record.plan.steps) == 7
        assert [entry["index"] for entry in record.leg_verifications] == [1, 2, 3]


class TestPlanValidationRejectsBadRoutes:
    async def test_more_than_three_legs_is_rejected(self) -> None:
        plan = raw_route_plan(
            [(0.0, 5.0, 5.0), (0.0, 10.0, 5.0), (0.0, 15.0, 5.0), (0.0, 20.0, 5.0)]
        )
        record = await make_runtime(LegWorld()).create_task(
            "x", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VALIDATION.value
        assert "超过上限" in record.message

    async def test_route_over_the_total_budget_is_rejected(self) -> None:
        plan = raw_route_plan([(0.0, 10.0, 10.0), (0.0, 20.0, 10.0)], budget=12.0)
        record = await make_runtime(LegWorld()).create_task(
            "x", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VALIDATION.value
        assert "超过预算" in record.message

    async def test_duplicate_waypoints_are_rejected(self) -> None:
        plan = raw_route_plan([(0.0, 10.0, 10.0), (0.0, 10.0, 0.0)])
        record = await make_runtime(LegWorld()).create_task(
            "x", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VALIDATION.value
        assert "重复" in record.message

    async def test_leg_pointing_at_a_non_move_step_is_rejected(self) -> None:
        plan = raw_route_plan([(0.0, 8.0, 8.0), (0.0, 16.0, 8.0)])
        plan.expected_final_state.route["legs"][1]["step_id"] = "step_2"
        record = await make_runtime(LegWorld()).create_task(
            "x", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.FAILED
        assert record.failure == TaskFailure.VALIDATION.value
        assert "不是移动步" in record.message

    async def test_non_finite_waypoint_coordinates_are_rejected(self) -> None:
        # NaN / Infinity / bool / 转换溢出 / 不可转换字符串 —— 全部 fail-closed。
        for bad in (float("nan"), float("inf"), float("-inf"), True, 10**400, "not-a-number"):
            expected = ExpectedFinalState.from_payload(
                {
                    "position_within": {"x": 0.0, "y": 64.0, "z": 16.0, "radius": 2.0},
                    "route": {
                        "start": {"x": 0.0, "y": 64.0, "z": 0.0},
                        "budget": 24.0,
                        "legs": [
                            {
                                "index": 1,
                                "step_id": "step_1",
                                "position_within": {
                                    "x": 0.0,
                                    "y": 64.0,
                                    "z": 8.0,
                                    "radius": 2.0,
                                },
                                "distance": 8.0,
                            },
                            {
                                "index": 2,
                                "step_id": "step_3",
                                "position_within": {
                                    "x": bad,
                                    "y": 64.0,
                                    "z": 16.0,
                                    "radius": 2.0,
                                },
                                "distance": 8.0,
                            },
                        ],
                    },
                }
            )
            assert expected.route_invalid is True, f"{bad!r} 必须被判为非法路线"
            assert expected.legs() == []
            plan = raw_route_plan([(0.0, 8.0, 8.0), (0.0, 16.0, 8.0)])
            plan.expected_final_state = expected
            record = await make_runtime(LegWorld()).create_task(
                "x", session_id="s", user_id="u", origin="user", plan=plan
            )
            assert record.state is TaskState.FAILED, f"{bad!r} 必须让计划被拒绝"
            assert record.failure == TaskFailure.VALIDATION.value


class TestProductPath:
    async def test_multi_leg_planner_path_requires_both_gates(self) -> None:
        planner = BoundedAgentPlanner(allow_safe=True, allow_low=True, explore_legs=3)
        result = await planner.plan("去外面探索一下", observe=observe_with())
        assert result.outcome is PlanningOutcome.READY_FOR_APPROVAL
        assert result.plan is not None
        plan = result.plan.plan
        assert [leg["index"] for leg in leg_targets(plan)] == [1, 2, 3]
        world = LegWorld()
        runtime = make_runtime(world)
        record = await runtime.create_task(
            "去探索", session_id="s", user_id="u", origin="user", plan=plan
        )
        assert record.state is TaskState.PENDING_CONFIRMATION
        assert world.calls == [], "批准之前一步都不许走"
        started = await runtime.confirm_and_start(
            record.task_id, user_id="u", session_id="s", origin="user"
        )
        assert started.state is TaskState.SUCCEEDED
        assert world.move_plan() == [(-8, 70, 11), (-8, 70, 17), (-8, 70, 23)]

    async def test_default_planner_stays_single_leg(self) -> None:
        planner = BoundedAgentPlanner(allow_safe=True, allow_low=True)
        assert planner.explore_legs == 1
        result = await planner.plan("去外面探索一下", observe=observe_with())
        assert result.plan is not None
        assert leg_targets(result.plan.plan) == []
        assert [step.step_id for step in result.plan.plan.steps] == [
            "step_1",
            "step_2",
            "step_3",
        ]
