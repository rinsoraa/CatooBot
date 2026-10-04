"""Phase B tests: 购买 / 消费 的语义分离与补货阈值。

契约（§B）:
* 购买 action = inventory += (ITEM_ACQUIRED + StateMutation)，**不**缓解需求；
* 消费 action = inventory -= (ITEM_CONSUMED) + 恰好一次 need_relief；
* 补货阈值：``stock(slot) <= min`` 时采购动作才可用，短fall 决定动机分；
* 低于 min（不只是为 0）就生成同一个 restock_resource goal，去重不重复；
* 完成采购（have >= target）满足 goal，下一 tick 不再新建；
* 陈旧提案被 DecisionValidator 以 ``restock_not_needed`` 拒绝；
* 这些路径上的 inventory 写入只来自 acquire_item/take_item。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.definition import CharacterDefinition
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalSource, GoalStatus
from app.sandbox.intent import DecisionTrigger, IntentProposal
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from app.sandbox.world_seed import build_world_seed
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock,
    )
    await runtime.start()
    return runtime


async def complete_now(runtime, clock, action_id: str):  # type: ignore[no-untyped-def]
    """Start the action and complete it immediately (no drift in between)."""
    started = await runtime._start_action(action_id)  # noqa: SLF001
    assert started is not None, f"{action_id} did not start"
    runtime.current_action.planned_end_at = clock.now
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001 - the completion path itself
    return started


def goals_of(runtime, kind: GoalKind, item: str = ""):  # type: ignore[no-untyped-def]
    return [
        goal
        for goal in runtime.goals.all()
        if goal.kind is kind and (not item or goal.target_item == item)
    ]


def fridge(runtime):  # type: ignore[no-untyped-def]
    return runtime.inventories.get("fridge")


def candidate_map(runtime, space_id: str):  # type: ignore[no-untyped-def]
    return {
        definition.id: (score, reasons)
        for definition, score, reasons in runtime.engine.candidates(space_id=space_id)
    }


# ---------------------------------------------------- purchase vs consumption


class TestPurchaseSemantics:
    async def test_purchase_raises_stock_and_does_not_relieve_hunger(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["布丁"] = 0
            pantry.items["蛋糕"] = 0
            runtime.needs.get("hunger").level = 0.6  # type: ignore[union-attr]
            hunger_before = runtime.needs.level("hunger")

            started = await complete_now(runtime, clock, "buy_sweets")

            # 购买只加库存，不降需求（Phase C：甜食店只买 dessert 槽）
            assert pantry.count("布丁") == 0
            assert pantry.count("蛋糕") == 2
            assert runtime.needs.level("hunger") == hunger_before

            acquired = [e for e in runtime.events.of_type(ET.ITEM_ACQUIRED)]
            assert [(e.payload["item"], e.payload["quantity"]) for e in acquired] == [
                ("蛋糕", 2),
            ]
            assert all(e.target_entity_id == "inventory:fridge" for e in acquired)
            assert not runtime.events.of_type(ET.ITEM_CONSUMED)

            # 每一笔都由 StateMutation 记录（before/after 可审计）
            mutations = [
                m
                for m in runtime.mutations.recent(limit=100)
                if m["target"] == "inventory:fridge" and m["reason"] == "buy_sweets"
            ]
            assert {(m["field"], m["before"], m["after"]) for m in mutations} == {
                ("蛋糕", 0, 2),
            }
            assert runtime.actions.definitions["buy_sweets"].need_relief == {}
            assert started.definition_id == "buy_sweets"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_consume_lowers_stock_and_relieves_exactly_once(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 5
            runtime.needs.get("thirst").level = 0.8  # type: ignore[union-attr]
            thirst_before = runtime.needs.level("thirst")

            started = await complete_now(runtime, clock, "drink_cola")

            assert pantry.count("可乐") == 4
            consumed = runtime.events.of_type(ET.ITEM_CONSUMED)
            assert len(consumed) == 1
            assert consumed[0].payload == {"item": "可乐", "quantity": 1, "left": 4}
            assert runtime.needs.level("thirst") < thirst_before

            chain = runtime.events.chain(f"act_{started.id}")
            relieved = [
                e
                for e in chain
                if e.event_type is ET.NEED_CHANGED and e.target_entity_id == "thirst"
            ]
            assert len(relieved) == 1  # 恰好一次
            mutations = [
                m
                for m in runtime.mutations.recent(limit=100)
                if m["target"] == "inventory:fridge" and m["reason"] == "drink_cola"
            ]
            assert [(m["before"], m["after"]) for m in mutations] == [(5, 4)]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_buy_cola_is_a_purchase_not_a_drink(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 0
            runtime.needs.get("thirst").level = 0.8  # type: ignore[union-attr]
            thirst_before = runtime.needs.level("thirst")

            await complete_now(runtime, clock, "buy_cola")

            assert pantry.count("可乐") == 6
            assert runtime.needs.level("thirst") == thirst_before
            assert runtime.actions.definitions["buy_cola"].need_relief == {}
            assert runtime.events.last(ET.ITEM_ACQUIRED) is not None
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------------- availability gate


class TestRestockGate:
    async def test_stocked_slot_filters_purchase_but_keeps_consumption(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            assert pantry.count("布丁") == 3 and pantry.count("蛋糕") == 2
            runtime.needs.get("hunger").level = 0.9  # type: ignore[union-attr]

            # 冰箱有货：即使很饿，采购也不在候选里
            for action_id in ("buy_sweets", "buy_snacks", "buy_cola"):
                ok, reason = runtime.engine.restock_gate(runtime.actions.definitions[action_id])  # noqa: SLF001
                assert ok is False and reason == "restock_not_needed"
                assert (
                    runtime.engine._requirements_met(  # noqa: SLF001
                        runtime.actions.definitions[action_id]
                    )
                    is False
                )
            assert "buy_sweets" not in candidate_map(runtime, "dessert_shop")
            assert "buy_snacks" not in candidate_map(runtime, "convenience_store")

            # 家里有吃的：消费动作仍然可用（“家里有吃的就别出门”）
            kitchen = candidate_map(runtime, "kitchen")
            assert "eat_cake" in kitchen
            assert "eat_pudding" in kitchen
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_stock_purchase_is_a_candidate_with_restock_reason(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 0
            pantry.items["布丁"] = 1  # == min(1)

            at_store = candidate_map(runtime, "convenience_store")
            assert "buy_cola" in at_store and "buy_snacks" in at_store
            for action_id in ("buy_cola", "buy_snacks"):
                score, reasons = at_store[action_id]
                assert "restock" in reasons, action_id
                assert score > 0.0

            # 阈值：stock <= min 可用，stock > min 不可用
            pantry.items["可乐"] = 3  # > min(2)
            assert runtime.engine.restock_gate(  # noqa: SLF001
                runtime.actions.definitions["buy_cola"]
            ) == (False, "restock_not_needed")
            assert "buy_cola" not in candidate_map(runtime, "convenience_store")
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_empty_slot_outranks_the_legacy_nibble(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["布丁"] = 0
            pantry.items["蛋糕"] = 0  # 两个槽都空 → 旧采购线也可用

            at_shop = candidate_map(runtime, "dessert_shop")
            assert "buy_sweets" in at_shop and "go_shopping_sweets" in at_shop
            buy_score, buy_reasons = at_shop["buy_sweets"]
            legacy_score, _legacy_reasons = at_shop["go_shopping_sweets"]
            assert "restock" in buy_reasons and "preference" in buy_reasons
            assert buy_score > legacy_score
            ids = [
                definition.id
                for definition, _score, _reasons in runtime.engine.candidates(
                    space_id="dessert_shop"
                )
            ]
            assert ids.index("buy_sweets") < ids.index("go_shopping_sweets")
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------------- goal layer


class TestRestockGoalTrigger:
    async def test_consumption_at_the_floor_creates_a_goal_before_zero(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 3  # min(2)：喝一瓶就到阈值，不是 0

            await complete_now(runtime, clock, "drink_cola")

            assert pantry.count("可乐") == 2
            assert runtime.events.last(ET.INVENTORY_DEPLETED) is None  # 不是「清空」
            restock = goals_of(runtime, GoalKind.restock_resource, "可乐")
            assert len(restock) == 1
            goal = restock[0]
            assert goal.source is GoalSource.inventory_low
            assert goal.metadata["inventory_key"] == "fridge"
            assert goal.metadata["desired_quantity"] == 6  # 模板 target
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_low_stock_goal_is_deduped_across_repeated_events(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 3
            await complete_now(runtime, clock, "drink_cola")  # 3 → 2
            goals = goals_of(runtime, GoalKind.restock_resource, "可乐")
            assert len(goals) == 1
            goal_id = goals[0].goal_id
            detected = runtime.goals.detected

            await complete_now(runtime, clock, "drink_cola")  # 2 → 1
            await complete_now(runtime, clock, "drink_cola")  # 1 → 0（depleted 也发声）
            runtime.goal_detector.observe(runtime.events.last(ET.ITEM_CONSUMED))  # replay

            goals = goals_of(runtime, GoalKind.restock_resource, "可乐")
            assert len(goals) == 1 and goals[0].goal_id == goal_id
            assert runtime.goals.detected == detected  # §14: 不为同一短缺再开一个
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_startup_sweep_catches_preexisting_low_stock(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            assert not goals_of(runtime, GoalKind.restock_resource, "可乐")
            pantry.items["可乐"] = 1  # 低于 min(2)，接近空但不是 0
            pantry.items["布丁"] = 3  # 高于 min(1) → 不该有 goal

            runtime.goal_detector.sweep()

            restock = goals_of(runtime, GoalKind.restock_resource, "可乐")
            assert len(restock) == 1
            assert restock[0].metadata["desired_quantity"] == 6
            assert not goals_of(runtime, GoalKind.restock_resource, "布丁")
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_purchase_completion_satisfies_goal_without_a_new_one(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 1
            await complete_now(runtime, clock, "drink_cola")  # 1 → 0
            goal = goals_of(runtime, GoalKind.restock_resource, "可乐")[0]

            clock.advance(10)
            await runtime.tick(minutes=1)
            assert runtime.current_action is not None
            assert runtime.current_action.definition_id in {"buy_cola", "go_shopping_cola"}
            runtime.current_action.planned_end_at = clock.now
            clock.advance(1)
            await runtime.tick(minutes=1)

            assert pantry.count("可乐") >= goal.metadata["desired_quantity"]
            assert goal.status is GoalStatus.completed
            completed = runtime.events.last(ET.GOAL_COMPLETED)
            assert completed is not None and completed.payload["goal_kind"] == "restock_resource"

            # 下一 tick 库存高于 min → 不新建 goal
            clock.advance(10)
            await runtime.tick(minutes=1)
            restock = goals_of(runtime, GoalKind.restock_resource, "可乐")
            assert len(restock) == 1 and restock[0].status is GoalStatus.completed
            assert not [g for g in restock if not g.status.terminal]
        finally:
            await runtime.shutdown()
            await db.close()


# --------------------------------------------------------- validator mirror


class TestValidationAndAuthority:
    async def test_validator_rejects_a_stale_restock_premise(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["可乐"] = 3
            await complete_now(runtime, clock, "drink_cola")  # 3 → 2, goal 打开
            goal = goals_of(runtime, GoalKind.restock_resource, "可乐")[0]

            candidates = runtime.goals.step_candidates(goal)
            request = runtime.decisions._request(  # noqa: SLF001
                trigger=DecisionTrigger.goal_step, candidates=candidates
            )
            proposal = IntentProposal(
                request_id=request.request_id,
                candidate_id="action:buy_cola",
                confidence=1.0,
            )
            ok, why = runtime.decisions.validator.validate(request, proposal)
            assert ok is True and why == ""

            # 决策与执行之间世界动了：库存回到 min 之上（提案前提失效）
            pantry.items["可乐"] = 3
            ok, why = runtime.decisions.validator.validate(request, proposal)
            assert ok is False and why == "restock_not_needed"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_inventory_writes_only_go_through_canonical_helpers(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = fridge(runtime)
            pantry.items["布丁"] = 0
            pantry.items["蛋糕"] = 0

            started = await complete_now(runtime, clock, "buy_sweets")
            chain = runtime.events.chain(f"act_{started.id}")
            acquired = [e for e in chain if e.event_type is ET.ITEM_ACQUIRED]
            mutations = [
                m
                for m in runtime.mutations.recent(limit=100)
                if m["target"] == "inventory:fridge" and m["reason"] == "buy_sweets"
            ]

            # acquire_item 的签名：一笔 mutation ⇄ 一个 ITEM_ACQUIRED（total=after）
            # Phase C：buy_sweets 现在只采购 dessert 槽（蛋糕 ×2）
            assert len(mutations) == len(acquired) == 1
            for mutation in mutations:
                matches = [e for e in acquired if e.payload["item"] == mutation["field"]]
                assert len(matches) == 1
                assert matches[0].payload["total"] == mutation["after"]
                assert mutation["source"] == "character_action"
            assert not [e for e in chain if e.event_type is ET.ITEM_CONSUMED]
            # 没有事件支撑的库存写入不存在（否则这里会有多余 mutation）
            assert {(m["field"], m["after"]) for m in mutations} == {("蛋糕", 2)}
        finally:
            await runtime.shutdown()
            await db.close()


# ---------------------------------------------------- runtime/seed plumbing


class TestPlumbing:
    async def test_restock_actions_cover_purchase_and_legacy_paths(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            cola = {
                action_id: quantity
                for action_id, _definition, quantity in runtime.restock_actions("fridge", "可乐")
            }
            assert cola == {"buy_cola": 6, "go_shopping_cola": 6}
            cake = {
                action_id: quantity
                for action_id, _definition, quantity in runtime.restock_actions("fridge", "蛋糕")
            }
            assert cake == {"buy_sweets": 2, "go_shopping_sweets": 1}
        finally:
            await runtime.shutdown()
            await db.close()

    def test_templates_resolve_purchase_and_restock_placeholders(self) -> None:
        bible = BibleCompiler(FIXTURE_BIBLE).compile()
        definition = CharacterDefinition.from_bible(bible)
        seed = build_world_seed(definition, bible, simulation_seed=7)
        actions = {payload["id"]: payload for payload in seed.actions}

        sweets = actions["buy_sweets"]
        assert sweets["purchase"] == {"fridge": {"蛋糕": 2}}
        assert sweets["restock"] == {"inventory": "fridge", "slot": "蛋糕", "min": 1, "target": 3}
        assert not sweets.get("need_relief")
        cola = actions["buy_cola"]
        assert cola["purchase"] == {"fridge": {"可乐": 6}}
        assert cola["restock"]["slot"] == "可乐"

        # 没有 restock 的模板行为完全不变（additive）
        minecraft = actions["play_minecraft"]
        assert not minecraft.get("restock")
        assert not minecraft.get("purchase")
