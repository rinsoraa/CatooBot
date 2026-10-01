"""Agent runtime tests: classifier, planner, validation, execution, evaluation.

Spec coverage: §122-§135 (classification, multi-step, parallel, failure,
replan, budget, loop, cancellation, persistence, persona, memory).
The AI is mocked, so plans and observations are fully deterministic.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from app.agent.errors import PlanningError, PlanValidationError
from app.agent.evaluator import Evaluator
from app.agent.executor import ExecutionEngine
from app.agent.goal import CLASSIFICATIONS, TaskClassifier
from app.agent.models import (
    AgentBudget,
    AgentResult,
    AgentStateMachine,
    Goal,
    Observation,
    Plan,
    StepRecord,
    StepSpec,
    TransitionError,
    observations_to_context,
)
from app.agent.planner import Planner
from app.agent.runtime import AgentRuntime
from app.ai.engine import AIEngine
from app.config.settings import AgentConfig, AIConfig, DatabaseConfig, ToolsConfig
from app.database.database import Database
from app.tools.builtins import TimeTool, WeatherTool
from app.tools.registry import ToolRegistry
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.test_tools import FakeWeatherProvider, run_tool  # noqa: F401 - fixture style helper

# ------------------------------------------------------------------ helpers


class LocationAwareWeather:
    """Succeeds for known places, fails for "Nowhere" — makes replanning testable."""

    name = "fake"

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def fetch(self, location: str, *, days: int = 3) -> dict:
        from app.tools.errors import ExternalServiceError
        from tests.test_tools import weather_payload

        self.calls.append(location)
        if location == "Nowhere":
            raise ExternalServiceError("unknown location")
        return {**weather_payload(location), "location": location}

    async def close(self) -> None:
        return None


def make_db(tmp_path, name: str = "agent.db") -> Database:
    return Database(DatabaseConfig(url=f"sqlite:///{tmp_path / name}"))


def plan_payload(steps: list[dict], *, criteria: list[str] | None = None) -> str:
    return json.dumps(
        {
            "goal": "测试目标",
            "summary": "测试计划",
            "criteria": criteria or ["得到结论"],
            "steps": steps,
        },
        ensure_ascii=False,
    )


async def make_runtime(tmp_path, replies: list[str], **agent_overrides):
    """AgentRuntime with mocked AI + a registry holding time/weather/calculator."""
    database = make_db(tmp_path)
    await database.connect()
    agent_config = AgentConfig(**agent_overrides)
    tools_config = ToolsConfig(enabled=True)
    tools = ToolRuntime(tools_config, database)
    await tools.start()
    tools.registry.unregister("weather")
    tools.registry.register(WeatherTool(providers=[LocationAwareWeather()]))

    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        database,
        providers={"mock": MockAIProvider(behaviors={"A": replies})},
    )
    runtime = AgentRuntime(agent_config, engine, tools, database)
    return runtime, engine, tools, database


def tool_step(step_id: str, tool: str, **arguments) -> dict:
    return {
        "id": step_id,
        "description": f"调用 {tool}",
        "tool": tool,
        "arguments": arguments,
        "depends_on": [],
    }


# -------------------------------------------------------------- classifier


class TestClassifier:
    def test_plain_chat_is_simple(self) -> None:
        classifier = TaskClassifier(AgentConfig())
        assert classifier.classify("今天心情不错").kind == "simple"

    def test_single_tool_question_is_tool_assisted(self) -> None:
        classifier = TaskClassifier(AgentConfig())
        result = classifier.classify("明天天气怎么样", tool_candidates=1)
        assert result.kind == "tool_assisted"

    def test_multi_step_markers_route_to_agent(self) -> None:
        classifier = TaskClassifier(AgentConfig())
        result = classifier.classify(
            "帮我查一下周六和周日天气，然后告诉我哪天适合出去", tool_candidates=1
        )
        assert result.kind == "multi_step"
        assert result.needs_agent

    def test_multi_step_without_tools_stays_simple(self) -> None:
        classifier = TaskClassifier(AgentConfig())
        assert classifier.classify("先聊聊天，然后再想想", tool_candidates=0).kind == "simple"

    def test_long_running_is_recognized_but_not_handled(self) -> None:
        config = AgentConfig()
        classifier = TaskClassifier(config)
        result = classifier.classify("每天帮我监控一下这个消息", tool_candidates=1)
        assert result.kind == "long_running"
        assert classifier.may_handle("long_running") is False

    def test_control_phrases(self) -> None:
        classifier = TaskClassifier(AgentConfig())
        assert classifier.classify("不用查了").control == "cancel"
        assert classifier.classify("算了").control == "cancel"
        assert classifier.classify("先停一下").control == "pause"
        assert classifier.classify("继续吧").control == "resume"
        # a long sentence containing a phrase is not a control command
        assert classifier.classify("我今天算了算账，还挺多的呢对吧").control == ""

    def test_modes_can_disable_classes(self) -> None:
        classifier = TaskClassifier(AgentConfig(mode={"multi_step": False}))
        assert classifier.may_handle("multi_step") is False


# -------------------------------------------------------------- state machine


class TestStateMachine:
    def test_legal_transitions(self) -> None:
        AgentStateMachine.ensure("created", "planning")
        AgentStateMachine.ensure("running", "completed")
        AgentStateMachine.ensure("running", "replanning")
        AgentStateMachine.ensure("paused", "running")

    def test_illegal_transitions_rejected(self) -> None:
        with pytest.raises(TransitionError):
            AgentStateMachine.ensure("completed", "running")
        with pytest.raises(TransitionError):
            AgentStateMachine.ensure("created", "completed")

    def test_terminal_states(self) -> None:
        assert AgentStateMachine.is_terminal("completed")
        assert not AgentStateMachine.is_terminal("running")

    def test_every_status_has_transition_rules(self) -> None:
        from app.agent.models import ALLOWED_TRANSITIONS, TASK_STATUSES

        assert set(TASK_STATUSES) == set(ALLOWED_TRANSITIONS)
        # every target status is itself a known status
        for targets in ALLOWED_TRANSITIONS.values():
            assert set(targets) <= set(TASK_STATUSES)


# ------------------------------------------------------------------ budget


class TestBudget:
    def test_remaining_and_expiry(self) -> None:
        budget = AgentBudget(max_steps=3, max_tool_calls=2, max_execution_seconds=0.01)
        budget.steps_used = 2
        budget.tool_calls_used = 1
        assert budget.steps_left == 1 and budget.tool_calls_left == 1
        assert budget.expired() is False
        budget.started_at -= 1
        assert budget.expired() is True

    def test_snapshot_shape(self) -> None:
        snapshot = AgentBudget().snapshot()
        assert {"max_steps", "steps_used", "max_tool_calls", "replans_used"} <= set(snapshot)


# ----------------------------------------------------------------- planner


class TestPlanner:
    async def test_plan_parsed_and_validated(self, tmp_path) -> None:
        payload = plan_payload(
            [
                tool_step("step_1", "weather", location="Singapore", days=2),
                {
                    "id": "step_2",
                    "description": "比较并给结论",
                    "tool": None,
                    "depends_on": ["step_1"],
                },
            ]
        )
        runtime, engine, tools, database = await make_runtime(tmp_path, [payload])
        try:
            goal = Goal(goal_id="g1", session_id="s1", description="查天气并给建议")
            plan = await runtime.planner.plan(goal)
            assert [step.id for step in plan.steps] == ["step_1", "step_2"]
            assert plan.steps[0].tool == "weather"
            assert plan.steps[1].tool is None
            assert runtime.planner.validate(plan) == []
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_fenced_json_is_accepted(self, tmp_path) -> None:
        payload = "```json\n" + plan_payload([tool_step("step_1", "time")]) + "\n```"
        runtime, engine, tools, database = await make_runtime(tmp_path, [payload])
        try:
            goal = Goal(goal_id="g", session_id="s", description="现在几点")
            plan = await runtime.planner.plan(goal)
            assert plan.steps[0].tool == "time"
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_unclear_goal_raises(self, tmp_path) -> None:
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [json.dumps({"unclear": True, "reason": "不知道说的是哪个"})]
        )
        try:
            goal = Goal(goal_id="g", session_id="s", description="帮我看看那个")
            with pytest.raises(PlanningError):
                await runtime.planner.plan(goal)
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_unknown_tool_repaired(self, tmp_path) -> None:
        """First plan is invalid, the repair produces a valid one (spec §66)."""
        bad = plan_payload([tool_step("step_1", "shell", cmd="ls")])
        good = plan_payload([tool_step("step_1", "time")])
        runtime, engine, tools, database = await make_runtime(tmp_path, [bad, good])
        try:
            goal = Goal(goal_id="g", session_id="s", description="现在几点")
            plan = await runtime.planner.plan(goal)
            assert plan.steps[0].tool == "time"
            assert plan.reason == "repaired"
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_repair_failure_raises_validation_error(self, tmp_path) -> None:
        bad = plan_payload([tool_step("step_1", "shell")])
        runtime, engine, tools, database = await make_runtime(tmp_path, [bad, bad])
        try:
            goal = Goal(goal_id="g", session_id="s", description="删个文件")
            with pytest.raises(PlanValidationError):
                await runtime.planner.plan(goal)
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_cycle_and_missing_dependency_detected(self, tmp_path) -> None:
        payload = plan_payload(
            [
                {"id": "s1", "description": "a", "depends_on": ["s2"]},
                {"id": "s2", "description": "b", "depends_on": ["s1"]},
                {"id": "s3", "description": "c", "depends_on": ["ghost"]},
            ]
        )
        runtime, engine, tools, database = await make_runtime(tmp_path, [payload, payload])
        try:
            goal = Goal(goal_id="g", session_id="s", description="循环依赖")
            with pytest.raises(PlanValidationError):
                await runtime.planner.plan(goal)
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_over_budget_plan_rejected(self, tmp_path) -> None:
        payload = plan_payload([tool_step(f"s{i}", "time") for i in range(1, 8)])
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [payload, payload], budget={"max_tool_calls": 2, "max_steps": 3}
        )
        try:
            goal = Goal(goal_id="g", session_id="s", description="很多步")
            with pytest.raises(PlanValidationError):
                await runtime.planner.plan(goal)
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    def test_validate_rejects_unknown_tool_directly(self) -> None:
        registry = ToolRegistry()
        registry.register(TimeTool())
        planner = Planner(AgentConfig(), None, registry)
        plan = Plan(
            plan_id="p",
            steps=[StepSpec(id="s1", description="x", tool="ghost")],
        )
        problems = planner.validate(plan)
        assert any("unknown tool" in problem for problem in problems)

    def test_validate_rejects_cycle(self) -> None:
        registry = ToolRegistry()
        planner = Planner(AgentConfig(), None, registry)
        plan = Plan(
            plan_id="p",
            steps=[
                StepSpec(id="a", description="a", depends_on=["b"]),
                StepSpec(id="b", description="b", depends_on=["a"]),
            ],
        )
        assert any("cycle" in problem for problem in planner.validate(plan))


# ---------------------------------------------------------------- execution


class TestExecution:
    async def test_dependent_steps_run_in_order(self, tmp_path) -> None:
        payload = plan_payload(
            [
                tool_step("step_1", "time"),
                {
                    "id": "step_2",
                    "description": "根据时间给建议",
                    "tool": None,
                    "depends_on": ["step_1"],
                },
            ]
        )
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [payload, "现在时间拿到了，可以给建议了"]
        )
        try:
            result = await runtime.run(
                "现在几点？然后给点建议", session_id="private:1", user_id="1"
            )
            assert result.status == "completed"
            assert result.facts
            detail = await runtime.task_detail(result.task_id)
            assert [step["status"] for step in detail["steps"]] == ["completed", "completed"]
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_independent_steps_run_in_parallel(self, tmp_path) -> None:
        payload = plan_payload(
            [
                tool_step("step_1", "weather", location="Singapore"),
                tool_step("step_2", "weather", location="Tokyo"),
                {
                    "id": "step_3",
                    "description": "比较",
                    "tool": None,
                    "depends_on": ["step_1", "step_2"],
                },
            ]
        )
        runtime, engine, tools, database = await make_runtime(tmp_path, [payload, "两地比较结果"])
        try:
            result = await runtime.run("比较两地天气", session_id="private:1", user_id="1")
            assert result.status == "completed"
            detail = await runtime.task_detail(result.task_id)
            plan_steps = json.loads(detail["plans"][0]["steps"])
            assert plan_steps[0]["depends_on"] == [] and plan_steps[1]["depends_on"] == []
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_step_budget_stops_execution(self, tmp_path) -> None:
        """5 tools needed, budget allows 2 → the task ends partially (spec §128)."""
        payload = plan_payload([tool_step(f"s{i}", "time") for i in range(1, 4)])
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [payload], budget={"max_steps": 2, "max_tool_calls": 3}
        )
        try:
            result = await runtime.run("查好几次", session_id="private:1", user_id="1")
            assert result.status in ("partial", "failed")
            detail = await runtime.task_detail(result.task_id)
            executed = [step for step in detail["steps"] if step["status"] != "pending"]
            assert len(executed) <= 2
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_tool_failure_does_not_crash_and_is_reported(self, tmp_path) -> None:
        payload = plan_payload([tool_step("s1", "weather", location="Nowhere")])
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [payload, payload, payload], budget={"max_replans": 0}
        )
        try:
            result = await runtime.run("查天气", session_id="private:1", user_id="1")
            assert result.status == "failed"
            assert result.unresolved, "the user must be told which part is missing"
            assert "weather" in result.unresolved[0]  # names the missing part
            assert "不要编造" in AgentResult(**result.model_dump()).to_prompt_block()
        finally:
            await engine.close()
            await tools.close()
            await database.close()


# ---------------------------------------------------------------- evaluation


class TestEvaluator:
    def test_all_steps_completed(self) -> None:
        plan = Plan(plan_id="p", steps=[StepSpec(id="a", description="a")])
        records = [StepRecord(step_id="a", status="completed")]
        observations = [Observation(step_id="a", summary="ok")]
        assert Evaluator(AgentConfig()).evaluate(plan, observations, records) == "complete"

    def test_some_failed_asks_for_replan(self) -> None:
        """A failure is recoverable; the runtime decides when to give up (§127)."""
        plan = Plan(
            plan_id="p",
            steps=[StepSpec(id="a", description="a"), StepSpec(id="b", description="b")],
        )
        records = [
            StepRecord(step_id="a", status="completed"),
            StepRecord(step_id="b", status="failed", error_type="blocked"),
        ]
        observations = [Observation(step_id="a", summary="ok")]
        assert Evaluator(AgentConfig()).evaluate(plan, observations, records) == "needs_replan"

    def test_needs_replan_when_a_step_failed(self) -> None:
        """A failure is retried via a new plan; the runtime caps replans (§127)."""
        plan = Plan(plan_id="p", steps=[StepSpec(id="a", description="a")])
        records = [StepRecord(step_id="a", status="failed", error_type="step_failed")]
        observations = [Observation(step_id="a", success=False, summary="no")]
        assert Evaluator(AgentConfig()).evaluate(plan, observations, records) == "needs_replan"

    def test_failed_without_any_step(self) -> None:
        plan = Plan(plan_id="p", steps=[])
        assert Evaluator(AgentConfig()).evaluate(plan, [], []) == "failed"

    def test_result_fields(self) -> None:
        plan = Plan(plan_id="p", summary="概要")
        result = Evaluator.build_result(
            task_id="t",
            goal_id="g",
            verdict="complete",
            plan=plan,
            observations=[Observation(step_id="a", summary="周六 80% 雨")],
            step_records=[StepRecord(step_id="a", status="completed")],
        )
        assert result.status == "completed"
        assert result.facts == ["周六 80% 雨"]
        assert 0 < result.confidence <= 0.95

    def test_partial_result_explains_gap(self) -> None:
        plan = Plan(plan_id="p", summary="概要")
        result = Evaluator.build_result(
            task_id="t",
            goal_id="g",
            verdict="partial",
            plan=plan,
            observations=[Observation(step_id="a", summary="周六查到了")],
            step_records=[
                StepRecord(step_id="a", status="completed"),
                StepRecord(step_id="b", status="failed", observation_summary="周日没拿到"),
            ],
        )
        assert result.status == "partial"
        assert "周日没拿到" in result.unresolved[0]
        assert "一部分" in result.summary


# ------------------------------------------------------------------ replan


class TestReplan:
    async def test_replan_produces_version_two(self, tmp_path) -> None:
        """Tool fails → agent replans → Plan v2 recorded (spec §127)."""
        v1 = plan_payload([tool_step("s1", "weather", location="Nowhere")])
        v2 = plan_payload([tool_step("s1", "weather", location="Singapore")])
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [v1, v2], budget={"max_replans": 2}
        )
        try:
            result = await runtime.run("查天气", session_id="private:1", user_id="1")
            detail = await runtime.task_detail(result.task_id)
            versions = [plan["version"] for plan in detail["plans"]]
            assert versions == [1, 2]
            assert detail["plans"][0]["status"] == "superseded"
            assert any(trace["event"] == "replan" for trace in detail["traces"])
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_replan_limit_enforced(self, tmp_path) -> None:
        """Always failing tool + max_replans=1 → stop, no infinite loop (§26)."""
        plan = plan_payload([tool_step("s1", "weather", location="Nowhere")])
        runtime, engine, tools, database = await make_runtime(
            tmp_path, [plan, plan, plan, plan], budget={"max_replans": 1}
        )
        try:
            result = await runtime.run("查天气", session_id="private:1", user_id="1")
            detail = await runtime.task_detail(result.task_id)
            assert len(detail["plans"]) == 2  # v1 + exactly one replan
            assert any(
                trace["event"] in ("replan_limit", "budget_exceeded") for trace in detail["traces"]
            )
        finally:
            await engine.close()
            await tools.close()
            await database.close()


# ------------------------------------------------ cancellation and persistence


class TestLifecycle:
    async def test_cancel_marks_task_cancelled(self, tmp_path) -> None:
        plan = plan_payload([tool_step("s1", "time")])
        runtime, engine, tools, database = await make_runtime(tmp_path, [plan])
        try:
            # simulate a running task without executing: create then cancel
            goal = Goal(goal_id="g", session_id="private:1", description="查时间")
            budget = AgentBudget()
            await runtime._store_task(  # noqa: SLF001 - lifecycle test
                "task-1", goal, status="running", classification="multi_step", budget=budget
            )
            runtime._active_by_session["private:1"] = "task-1"  # noqa: SLF001
            assert await runtime.cancel("task-1", reason="user_request") is True
            task = await runtime.get("task-1")
            assert task["status"] == "cancelled"
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_control_phrases_act_on_active_task(self, tmp_path) -> None:
        runtime, engine, tools, database = await make_runtime(tmp_path, [])
        try:
            goal = Goal(goal_id="g", session_id="private:7", description="查天气")
            await runtime._store_task(  # noqa: SLF001
                "task-9",
                goal,
                status="running",
                classification="multi_step",
                budget=AgentBudget(),
            )
            runtime._active_by_session["private:7"] = "task-9"  # noqa: SLF001

            intent = await runtime.handle_control("不用查了", "private:7")
            assert intent == "cancel"
            assert (await runtime.get("task-9"))["status"] == "cancelled"
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_pause_then_resume(self, tmp_path) -> None:
        runtime, engine, tools, database = await make_runtime(tmp_path, [])
        try:
            goal = Goal(goal_id="g", session_id="private:8", description="查天气")
            await runtime._store_task(  # noqa: SLF001
                "task-p",
                goal,
                status="running",
                classification="multi_step",
                budget=AgentBudget(),
            )
            assert await runtime.pause("task-p") is True
            assert (await runtime.get("task-p"))["status"] == "paused"
            assert await runtime.resume("task-p") is True
            assert (await runtime.get("task-p"))["status"] == "running"
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_running_tasks_become_paused_after_restart(self, tmp_path) -> None:
        """Spec §131: never auto-resume an interrupted task."""
        database = make_db(tmp_path, "restart.db")
        await database.connect()
        tools = ToolRuntime(ToolsConfig(enabled=True), database)
        await tools.start()
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": MockAIProvider(behaviors={"A": ["x"]})},
        )
        runtime = AgentRuntime(AgentConfig(), engine, tools, database)
        goal = Goal(goal_id="g", session_id="s", description="中断的任务")
        await runtime._store_task(  # noqa: SLF001
            "task-r", goal, status="running", classification="multi_step", budget=AgentBudget()
        )
        await database.close()

        # "restart": a fresh runtime over the same database
        database2 = make_db(tmp_path, "restart.db")
        await database2.connect()
        tools2 = ToolRuntime(ToolsConfig(enabled=True), database2)
        await tools2.start()
        engine2 = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database2,
            providers={"mock": MockAIProvider(behaviors={"A": ["x"]})},
        )
        runtime2 = AgentRuntime(AgentConfig(), engine2, tools2, database2)
        paused = await runtime2.mark_running_tasks_paused()
        assert paused == 1
        assert (await runtime2.get("task-r"))["status"] == "paused"
        await engine2.close()
        await tools2.close()
        await database2.close()

    async def test_housekeeping_expires_stale_tasks(self, tmp_path) -> None:
        runtime, engine, tools, database = await make_runtime(tmp_path, [])
        try:
            goal = Goal(goal_id="g", session_id="s", description="僵尸任务")
            await runtime._store_task(  # noqa: SLF001
                "task-z",
                goal,
                status="running",
                classification="multi_step",
                budget=AgentBudget(),
            )
            summary = await runtime.housekeeping()
            assert summary["stale"] == 1
            assert (await runtime.get("task-z"))["status"] == "expired"
        finally:
            await engine.close()
            await tools.close()
            await database.close()


# ------------------------------------------------------------------ context


class TestContextCompression:
    def test_observations_compressed_to_recent(self) -> None:
        observations = [Observation(step_id=f"s{i}", summary=f"结果{i}") for i in range(1, 11)]
        block = observations_to_context(observations, limit=3)
        assert "结果10" in block and "结果8" in block
        assert "结果7" not in block
        assert "省略" in block

    def test_failures_are_marked(self) -> None:
        block = observations_to_context(
            [Observation(step_id="s1", success=False, summary="没拿到")], limit=3
        )
        assert "✗" in block


# A query that the classifier routes to the agent (marked multi-step + tool available).
TWO_STEP_QUERY = "查一下天气 然后 给点建议"


class TestSimulation:
    async def test_simulate_does_not_execute(self, tmp_path) -> None:
        payload = plan_payload([tool_step("s1", "weather", location="Singapore")])
        runtime, engine, tools, database = await make_runtime(tmp_path, [payload])
        weather_tool = tools.registry.get("weather")
        providers = getattr(weather_tool, "_providers", [])  # noqa: SLF001
        provider = providers[0] if providers else None
        try:
            simulation = await runtime.simulate(TWO_STEP_QUERY)
            assert simulation["classification"] == "multi_step"
            assert simulation["estimated_tool_calls"] >= 1
            assert (await runtime.list_tasks()) == []  # nothing was created
            if provider is not None:
                assert provider.calls == []
        finally:
            await engine.close()
            await tools.close()
            await database.close()

    async def test_simulate_simple_message_short_circuits(self, tmp_path) -> None:
        runtime, engine, tools, database = await make_runtime(tmp_path, [])
        try:
            simulation = await runtime.simulate("你好呀")
            assert simulation["classification"] == "simple"
            assert "plan" not in simulation
        finally:
            await engine.close()
            await tools.close()
            await database.close()


class TestGoalAndClassificationModel:
    def test_classification_vocabulary(self) -> None:
        assert CLASSIFICATIONS == ("simple", "tool_assisted", "multi_step", "long_running")

    def test_goal_fields(self) -> None:
        goal = Goal(goal_id="g", session_id="s", description="d", type="multi_step")
        assert goal.status == "pending" and goal.constraints == {}


@pytest.mark.parametrize("marker", ["然后", "并且", "顺便", "比较"])
def test_multi_step_markers_are_configurable(marker: str) -> None:
    classifier = TaskClassifier(AgentConfig(multi_step_markers=[marker]))
    result = classifier.classify(f"查一下东西 {marker} 给结论", tool_candidates=1)
    assert result.kind == "multi_step"


def test_execution_engine_requires_observations_for_analysis() -> None:
    """An analysis step with nothing to analyse is a budget error, not a crash."""
    engine = ExecutionEngine(AgentConfig(), None, None)

    async def run() -> None:
        from app.agent.errors import BudgetExceededError

        step = StepSpec(id="s1", description="比较", tool=None)
        with pytest.raises(BudgetExceededError):
            await engine._run_analysis_step(step, "目标", [])  # noqa: SLF001

    asyncio.run(run())
