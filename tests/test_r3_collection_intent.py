"""R3 shared intent: target grounding and zero-observation refusal, not live evidence."""

from types import SimpleNamespace

import pytest

from app.tasks.agent_planner import BoundedAgentPlanner, PlanningOutcome
from app.tasks.intent import TaskIntentDetector
from app.tasks.planner import ObservationFailed, plan_resource_task, plan_wood_task
from app.tasks.resource_contract import RESOURCE_SPECS
from app.tasks.resource_intent import parse_collection_request
from app.tasks.skill_planner import SkillAwarePlanner
from app.tasks.turn import TaskTurnHandler, block_for
from app.tasks.validation import collect_references, collection_plan_problems
from tests.skill_fakes import FakeObserve, build_task_runtime, make_service, resource_record
from tests.test_resource_collection_contract import ResourceObservations


@pytest.mark.parametrize("target", RESOURCE_SPECS)
def test_every_supported_exact_id_survives_without_alias_substitution(target):
    text = f"采集一块 minecraft:{target} 并捡回来"
    request = parse_collection_request(text)
    assert request is not None and not request.problem
    assert request.candidates == (target,)
    assert block_for(text) == f"minecraft:{target}"
    assert TaskIntentDetector().detect(text).is_task


@pytest.mark.parametrize(
    "phrase,target",
    [
        ("云杉原木", "spruce_log"),
        ("白桦原木", "birch_log"),
        ("深色橡木", "dark_oak_log"),
        ("暗橡原木", "dark_oak_log"),
        ("泥土", "dirt"),
        ("沙子", "sand"),
        ("煤矿石", "coal_ore"),
        ("铁矿石", "iron_ore"),
        ("圆石", "cobblestone"),
    ],
)
def test_longest_chinese_aliases_are_the_same_at_the_task_entry(phrase, target):
    text = f"帮我挖一块{phrase}并捡回来"
    assert block_for(text) == f"minecraft:{target}"
    assert TaskIntentDetector().detect(text).is_task


def test_generic_log_is_a_candidate_set_and_never_an_oak_default():
    request = parse_collection_request("帮我采集一根木头并捡回来")
    assert request is not None and not request.problem
    assert set(request.candidates) == {
        name for name, spec in RESOURCE_SPECS.items() if spec.family == "log"
    }
    assert block_for("帮我采集一根木头并捡回来") == ""


INVALID = [
    "不要挖一块铁矿石并捡回来",
    "别采集一块沙子",
    "不用挖土，改成挖石头",
    "如果让你挖一块煤矿石并捡回来，你会先检查什么？",
    "我只是问问怎么挖一块沙子",
    "采集一块木头，白桦还是云杉我没决定。",
    "挖石头和沙子并捡回来",
    "挖99块石头并捡回来",
    "采集十一块土并捡回来",
    "砍二〇二根原木带回来",
    "砍三根云杉原木带回来",
    "采集0块圆石",
    "挖-1块土",
    "挖1.5块土",
    "采集一块樱花原木",
    "采集一块红树原木",
    "采集一块苍白橡木原木",
    "采集一块紫杉原木",
    "采集一块 minecraft:deepslate_iron_ore",
    "采集一块 unknown_log",
    "挖一块木板",
    "砍两棵树带回来",
    "挖一块石头并挖一块石头",
    "采集一根白桦原木再采集原木",
    "采集云杉原木然后采集云杉原木",
    "昨天我挖了原木",
    "采集白桦原木和云杉原木",
]


@pytest.mark.parametrize("text", INVALID)
@pytest.mark.parametrize("hint", ["", "resource", "wood", "follow", "explore"])
async def test_model_hint_cannot_promote_invalid_collection_to_a_plan(text, hint):
    calls = []

    async def observe(tool, arguments):
        calls.append(tool)
        raise AssertionError("invalid intent must not even observe")

    result = await BoundedAgentPlanner(allow_medium=True).plan(
        text, observe=observe, shape_hint=hint
    )
    assert result.outcome is not PlanningOutcome.READY_FOR_APPROVAL
    assert result.plan is None
    assert not calls


@pytest.mark.parametrize("target", RESOURCE_SPECS)
async def test_direct_template_cannot_replace_a_negated_request(target):
    calls = []

    async def observe(tool, arguments):
        calls.append(tool)

    with pytest.raises(ObservationFailed, match="resource.intent"):
        await plan_resource_task(
            f"不要挖一块 minecraft:{target}", observe=observe, block_name=target
        )
    assert not calls


async def test_direct_templates_cannot_change_species_or_quantity():
    observe = FakeObserve()
    with pytest.raises(ObservationFailed, match="mismatch"):
        await plan_resource_task("挖一根云杉原木", observe=observe, block_name="oak_log")
    with pytest.raises(ObservationFailed, match="mismatch"):
        await plan_wood_task(
            "采集两根云杉原木", observe=observe, candidates=("spruce_log",), count=1
        )
    assert not observe.calls


async def test_generic_wood_uses_observed_spruce_in_user_entry_and_unhinted_life_planner():
    text = "帮我采集一根木头并捡回来"
    observe = FakeObserve(block="spruce_log")
    planner = BoundedAgentPlanner(allow_medium=True)
    result = await planner.plan(text, observe=observe)
    assert result.ready
    dig = next(step for step in result.plan.plan.steps if step.tool == "minecraft_dig")
    assert dig.arguments["expected_block"] == "spruce_log"
    runtime = build_task_runtime()
    outcome = await TaskTurnHandler(runtime, observe=observe).handle(
        session_id="s", user_id="u", text=text
    )
    assert outcome.action == "created"
    record = await runtime.get(outcome.task_id)
    assert record.state.value == "PENDING_CONFIRMATION"
    dig = next(step for step in record.plan.steps if step.tool == "minecraft_dig")
    assert dig.arguments["expected_block"] == "spruce_log"


async def test_nonwood_entry_refuses_overbudget_without_observation_or_task():
    observe = ResourceObservations()
    runtime = build_task_runtime()
    result = await TaskTurnHandler(runtime, observe=observe).handle(
        session_id="s", user_id="u", text="帮我挖99块石头并捡回来"
    )
    assert result.action == "plan_failed"
    assert result.detail["reason_code"] == "resource.intent.quantity_out_of_budget"
    assert "数量超出范围" in result.reply
    assert "quantity_out_of_budget" not in result.reply
    assert not observe.calls
    assert await runtime.current("s") is None


async def test_skill_cannot_override_invalid_original_text():
    class HostileSkills:
        def __init__(self):
            self.calls = []

        async def suggest(self, objective):
            self.calls.append(objective)
            return SimpleNamespace(plan=resource_record().plan)

    skills = HostileSkills()
    planner = SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=skills)
    for text in INVALID:
        result = await planner.plan(text, observe=FakeObserve())
        assert not result.ready
    assert not skills.calls


async def test_legacy_skill_is_rematerialized_with_current_facts_and_exact_drop_parent():
    observe = FakeObserve()
    service = make_service(observe=observe)
    await service.on_task_finished(resource_record(task_id="legacy-1"))
    promoted = await service.on_task_finished(resource_record(task_id="legacy-2"))
    skill = await service.store.get(promoted["skill_id"])
    payload = skill.to_payload()
    for step in payload["steps"]:
        if step["tool"] == "minecraft_dig":
            step["arguments"].pop("require_collection", None)
        if step["tool"] == "minecraft_pickup_item":
            step["arguments"] = {
                "entity_id": {"from_step": "step_4", "path": "items.0.entity_id"},
                "expected_item": "oak_log",
            }
    await service.store.replace_skill(type(skill).from_payload(payload))
    planned = await service.suggest("去挖一块橡木并捡回来")
    assert planned is not None
    dig = next(step for step in planned.plan.steps if step.tool == "minecraft_dig")
    pickup = next(step for step in planned.plan.steps if step.tool == "minecraft_pickup_item")
    assert dig.arguments["require_collection"] is True
    assert pickup.references["entity_uuid"].from_step == dig.step_id
    assert pickup.references["expected_break_action"].from_step == dig.step_id
    assert "minecraft_world" in [tool for tool, _args in observe.calls]
    assert any(
        row.get("result", {}).get("admission") == "fresh_collection_contract"
        for row in planned.plan.observations
    )


async def test_stale_skill_observation_cannot_materialize_a_candidate():
    class Stale(FakeObserve):
        async def __call__(self, tool, arguments):
            result = await super().__call__(tool, arguments)
            if tool == "minecraft_find_blocks":
                result.result["observation"]["captured_at"] = 0
            return result

    service = make_service(observe=Stale())
    await service.on_task_finished(resource_record(task_id="legacy-1"))
    await service.on_task_finished(resource_record(task_id="legacy-2"))
    assert await service.suggest("去挖一块橡木并捡回来") is None


@pytest.mark.parametrize("prefix", ["罐头，", "CatooBot，", "想去 Minecraft "])
def test_current_character_vocative_and_life_context_do_not_change_target(prefix):
    request = parse_collection_request(prefix + "采集一块白桦原木并捡回来")
    assert request is not None and not request.problem
    assert request.candidates == ("birch_log",)


@pytest.mark.parametrize(
    "mutation,problem",
    [
        ("legacy_drop", "exact_pickup_parent_required"),
        ("missing_uuid", "exact_pickup_parent_required"),
        ("wrong_parent", "exact_pickup_parent_required"),
        ("no_collection", "collection_proof_required"),
        ("no_frozen_hand", "hand_not_frozen"),
        ("different_item", "pickup_mismatch"),
        ("inventory_only", "pickup_count_mismatch"),
    ],
)
async def test_shared_validator_rejects_incomplete_collection_graphs(mutation, problem):
    planned = await plan_resource_task(
        "挖一块石头并捡回来", observe=ResourceObservations(), block_name="stone"
    )
    plan = planned.plan
    assert collection_plan_problems(plan) == []
    dig = next(step for step in plan.steps if step.tool == "minecraft_dig")
    pickup = next(step for step in plan.steps if step.tool == "minecraft_pickup_item")
    if mutation == "legacy_drop":
        pickup.arguments["entity_id"] = {
            "from_step": plan.steps[-1].step_id,
            "path": "items.0.entity_id",
        }
    elif mutation == "missing_uuid":
        pickup.arguments.pop("entity_uuid")
    elif mutation == "wrong_parent":
        pickup.arguments["expected_break_action"] = "older-action"
    elif mutation == "no_collection":
        dig.arguments.pop("require_collection")
    elif mutation == "no_frozen_hand":
        dig.arguments.pop("expected_tool")
    elif mutation == "different_item":
        pickup.arguments["expected_item"] = "diamond"
    elif mutation == "inventory_only":
        plan.steps.remove(pickup)
    pickup.references = collect_references(pickup.arguments)
    assert any(problem in row for row in collection_plan_problems(plan))


async def test_persisted_legacy_plan_cannot_consume_a_new_confirmation():
    observe = FakeObserve()
    runtime = build_task_runtime()
    outcome = await TaskTurnHandler(runtime, observe=observe).handle(
        session_id="s", user_id="u", text="挖一块橡木并捡回来"
    )
    record = await runtime.get(outcome.task_id)
    assert record.state.value == "PENDING_CONFIRMATION"
    dig = next(step for step in record.plan.steps if step.tool == "minecraft_dig")
    dig.arguments.pop("require_collection")
    await runtime._store.save(record)
    failed = await runtime.confirm_and_start(
        record.task_id, session_id="s", user_id="u", origin="user"
    )
    assert failed.state.value == "FAILED"
    assert failed.authorization is None
    assert "collection_proof_required" in failed.message


@pytest.mark.parametrize(
    "text", ["把我面前这个石头挖掉", "把面前这个石头挖掉", "挖掉那个石头", "把前面这个石头挖掉"]
)
def test_explicit_single_block_removal_keeps_the_atomic_p3_path(text):
    assert parse_collection_request(text) is None


@pytest.mark.parametrize(
    "text", ["不要把这个石头挖掉", "我只是问问怎么挖掉那个石头", "挖掉那个位置的99块石头"]
)
async def test_atomic_wording_cannot_hide_negation_query_or_quantity(text):
    result = await BoundedAgentPlanner(allow_medium=True).plan(text, observe=FakeObserve())
    assert not result.ready


async def test_resource_template_rejects_a_find_response_for_a_different_block():
    with pytest.raises(ObservationFailed, match="target_unverified"):
        await plan_resource_task(
            "挖一块石头并捡回来", observe=ResourceObservations("sand"), block_name="stone"
        )


async def test_model_planner_cannot_rewrite_the_original_negative_objective():
    import json

    from app.tasks.planner import ModelTaskPlanner

    class Engine:
        async def chat(self, messages):
            return SimpleNamespace(
                content=json.dumps({"objective": "挖一块石头并捡回来", "steps": []})
            )

    original = "不要挖一块石头并捡回来"
    plan = await ModelTaskPlanner(Engine(), tools=[], risk_of=lambda tool: "SAFE").plan(original)
    assert plan.objective == original


@pytest.mark.parametrize("missing_collection", [False, True])
async def test_skill_candidate_cannot_replace_species_or_omit_collection_steps(missing_collection):
    from app.tasks.models import TaskPlan, TaskStep

    class Wrong:
        async def suggest(self, objective):
            plan = resource_record().plan
            if missing_collection:
                plan = TaskPlan(
                    objective=objective,
                    steps=[
                        TaskStep(
                            step_id="walk",
                            tool="minecraft_move_to",
                            arguments={"x": 1, "y": 64, "z": 1},
                            risk="LOW",
                        )
                    ],
                )
            return SimpleNamespace(plan=plan)

    text = "采集一根白桦原木并捡回来"
    result = await SkillAwarePlanner(BoundedAgentPlanner(allow_medium=True), skills=Wrong()).plan(
        text, observe=FakeObserve(block="birch_log")
    )
    assert result.ready and result.reason == "wood_plan_ready"
    assert (
        next(step for step in result.plan.plan.steps if step.tool == "minecraft_dig").arguments[
            "expected_block"
        ]
        == "birch_log"
    )


async def test_runtime_checks_original_objective_even_if_candidate_rewrites_it():
    planned = await plan_resource_task(
        "挖一块石头并捡回来", observe=ResourceObservations(), block_name="stone"
    )
    runtime = build_task_runtime()
    record = await runtime.create_task(
        "不要挖一块石头并捡回来", session_id="s", user_id="u", origin="user", plan=planned.plan
    )
    assert record.state.value == "FAILED"
    assert record.authorization is None
    assert "intent_unresolved" in record.message
    assert all(not step.dispatch_state for step in record.steps)


async def test_shorter_observed_log_group_is_explicit_in_confirmation_summary():
    runtime = build_task_runtime()
    result = await TaskTurnHandler(runtime, observe=FakeObserve()).handle(
        session_id="s", user_id="u", text="采集两根橡木原木并捡回来"
    )
    assert result.action == "created"
    assert "本次只能验证 1 个采集目标" in result.reply
    assert "原请求为 2 个" in result.reply
