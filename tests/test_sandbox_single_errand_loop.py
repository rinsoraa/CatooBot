"""验收 2/3：单项采购的完整闭环 与"有货先消费"的行为审查。

验收 2（§C 闭环）：`MIN_TRIP_ITEMS = 2`，所以只有一个槽位见底时走 Phase B 的
`restock_resource → buy_*`。旧契约在货架补齐的瞬间就关闭 goal —— 那时她人还在店
里，"回家"只是后续某个居家动作的偶然结果。现在与 shopping_trip 同一条规则：
货架补齐 **且** 到家（或这个世界没有可走的回家动作）才结束；中间由 goal 驱动
可见、可恢复的 `return_home` 步，复用既有移动闸门，不新增第二套移动系统。

验收 3（行为审查）：有货时 hunger 不得驱动采购，应由吃/喝来满足；采购只加库存、
消费才减库存并恢复需求；双低库存仍是一个 shopping_trip、仍只产出一条
`errand_trip` 经历。
"""

from __future__ import annotations

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalStatus
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

TICKS_6H = 36


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=BibleCompiler(FIXTURE_BIBLE).compile(),
        clock=clock,
    )
    await runtime.start()
    return runtime


def goals_of(runtime, kind: GoalKind):  # type: ignore[no-untyped-def]
    return [goal for goal in runtime.goals.all() if goal.kind is kind]


def purchase_action_ids(runtime) -> set[str]:  # type: ignore[no-untyped-def]
    return {
        action_id
        for action_id, definition in runtime.actions.definitions.items()
        if definition.purchase
    }


def consumption_action_ids(runtime) -> set[str]:  # type: ignore[no-untyped-def]
    return {
        action_id
        for action_id, definition in runtime.actions.definitions.items()
        if definition.consumes
    }


def started_actions(runtime) -> list[str]:  # type: ignore[no-untyped-def]
    return [event.payload["action_id"] for event in runtime.events.of_type(ET.ACTION_STARTED)]


class TestSingleItemErrandLoop:
    async def test_single_low_slot_is_a_restock_goal_not_a_trip(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.inventories.get("fridge").items["蛋糕"] = 1  # 只有 dessert 见底
            runtime.goal_detector.sweep()

            assert goals_of(runtime, GoalKind.shopping_trip) == []
            restock = goals_of(runtime, GoalKind.restock_resource)
            assert len(restock) == 1 and restock[0].target_item == "蛋糕"
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_purchase_then_walk_home_closes_the_errand(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            pantry.items["蛋糕"] = 1
            runtime.goal_detector.sweep()
            goal = goals_of(runtime, GoalKind.restock_resource)[0]
            home = runtime.seed.character.home_space
            assert runtime.spaces.is_home(runtime.character.location)
            # 纯采购动作（requires_absent 为空）才是合法步骤：go_shopping_sweets
            # 在货架有货时不满足"缺货才可买"的前提，因此这次选择是确定的
            assert not runtime.actions.definitions["buy_sweets"].requires_absent

            for _ in range(TICKS_6H):
                clock.advance(600)
                await runtime.tick(minutes=10)
                if goal.status.terminal:
                    break

            # 出门 → 购买 → 回家：动作序列就是这条闭环
            started = started_actions(runtime)
            assert started[:2] == ["buy_sweets", "return_home"], started
            assert started.count("buy_sweets") == 1
            assert started.count("return_home") == 1
            moved = [event.target_entity_id for event in runtime.events.of_type(ET.ENTITY_MOVED)]
            assert moved[:2] == ["dessert_shop", home], moved
            assert runtime.spaces.is_home(runtime.character.location)
            assert pantry.count("蛋糕") == 3  # 1 + 2，真实写入

            assert goal.status is GoalStatus.completed and goal.progress == 1.0
            completions = [
                event
                for event in runtime.events.of_type(ET.GOAL_COMPLETED)
                if event.payload.get("goal_id") == goal.goal_id
            ]
            assert len(completions) == 1
            assert completions[0].payload["reason"] == "restocked"
            assert goals_of(runtime, GoalKind.shopping_trip) == []  # 单项绝不是行程
            # 到家时货架已高于 min → 不会立刻再开一张补货单
            assert not [
                g for g in goals_of(runtime, GoalKind.restock_resource) if not g.status.terminal
            ]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_restart_mid_walk_still_ends_at_home(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """买完还在店里时重启：同一张补货单续走回家，不再买第二次。"""
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_sandbox(db=db, clock=clock)
        home = first.seed.character.home_space
        try:
            first.inventories.get("fridge").items["蛋糕"] = 1
            first.goal_detector.sweep()
            assert await first.goals.advance() is True
            assert first.current_action is not None
            assert first.current_action.definition_id == "buy_sweets"
            first.current_action.planned_end_at = clock.now
            clock.advance(1)
            await first._complete_action()  # noqa: SLF001

            goal = goals_of(first, GoalKind.restock_resource)[0]
            assert not goal.status.terminal, "补齐货架不等于差事结束"
            assert not first.spaces.is_home(first.character.location)

            await first.goals.flush()
            await first._snapshot()  # noqa: SLF001 - 恢复点
            await first.shutdown()
        finally:
            if first.phase.value != "stopped":
                await first.shutdown()

        revived = await make_sandbox(db=db, clock=clock)
        try:
            assert revived.character.location == "dessert_shop"
            goal = goals_of(revived, GoalKind.restock_resource)[0]
            assert not goal.status.terminal

            assert await revived.goals.advance() is True
            assert revived.current_action is not None
            assert revived.current_action.definition_id == "return_home"
            revived.current_action.planned_end_at = clock.now
            clock.advance(1)
            await revived._complete_action()  # noqa: SLF001

            assert revived.character.location == home
            assert revived.spaces.is_home(revived.character.location)
            assert goal.status is GoalStatus.completed
            # 采购只发生过一次：重启不会让她再买一轮
            rows = await revived.store.recent_finished_actions(limit=50)
            assert [row[0] for row in rows].count("buy_sweets") == 1
        finally:
            await revived.shutdown()
            await db.close()


class TestStockedShelvesPreferConsumption:
    async def test_hunger_with_full_shelves_eats_instead_of_buying(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """把"货架始终充足"作为唯一变量：hunger 再高也只能驱动吃/喝。

        现实里吃到见底当然会合法地触发补货（那是 §B 的阈值在起作用，不是这里
        要证的命题），所以本测试把货架放得足够深（远高于各自 min），让 hunger
        与 thirst 再高也只能驱动吃/喝；见底→采购的路径由 Phase B 与 §17 的既有
        测试覆盖。
        """
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            stock = {"可乐": 12, "布丁": 6, "蛋糕": 5}  # 全部远高于 min
            pantry.items = dict(stock)
            runtime.needs.get("hunger").level = 0.95  # type: ignore[union-attr]
            runtime.needs.get("thirst").level = 0.9  # type: ignore[union-attr]

            started: list[str] = []
            consumed: list[str] = []
            dropped = False
            for _ in range(24):  # 4h
                clock.advance(600)
                await runtime.tick(minutes=10)
                started.extend(
                    event.payload["action_id"]
                    for event in runtime.events.of_type(ET.ACTION_STARTED)
                )
                consumed.extend(
                    event.payload["action_id"]
                    for event in runtime.events.of_type(ET.ACTION_COMPLETED)
                    if event.payload["action_id"] in consumption_action_ids(runtime)
                )
                if any(pantry.count(key) < value for key, value in stock.items()):
                    dropped = True
                if consumed:
                    break

            assert started, "她必须继续生活，而不是冻住"
            assert not set(started) & purchase_action_ids(runtime), started
            assert consumed, "饥饿应当被吃/喝满足"
            # 消费确实减库存并缓解需求（采购不在这条链上）
            assert dropped and runtime.events.of_type(ET.ITEM_CONSUMED)
            assert runtime.needs.level("hunger") < 0.95
            # 货架从未落到 min 之下：连补货目标都不该有事实依据
            assert goals_of(runtime, GoalKind.restock_resource) == []
            assert goals_of(runtime, GoalKind.shopping_trip) == []
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_two_low_slots_stay_one_trip_and_one_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            pantry = runtime.inventories.get("fridge")
            pantry.items["可乐"] = 1
            pantry.items["蛋糕"] = 1
            runtime.goal_detector.sweep()

            assert len(goals_of(runtime, GoalKind.shopping_trip)) == 1
            assert not [
                g for g in goals_of(runtime, GoalKind.restock_resource) if not g.status.terminal
            ]

            for _ in range(TICKS_6H):
                clock.advance(600)
                await runtime.tick(minutes=10)
                await runtime.flush_experiences()
                open_trips = [
                    g for g in goals_of(runtime, GoalKind.shopping_trip) if not g.status.terminal
                ]
                assert len(open_trips) <= 1

            trips = goals_of(runtime, GoalKind.shopping_trip)
            assert len(trips) == 1 and trips[0].status is GoalStatus.completed
            errands = [
                record
                for record in runtime.experiences.emitted()
                if record.kind.value == "errand_trip"
            ]
            assert len(errands) == 1, [record.summary for record in errands]
            assert errands[0].summary == "完成了买甜食和可乐"
            assert errands[0].episode_key.startswith("trip:")
            # 采购动作各跑一次，被行程约束
            purchase_started = [
                a for a in started_actions(runtime) if a in purchase_action_ids(runtime)
            ]
            assert sorted(purchase_started) == ["buy_cola", "buy_sweets"]
        finally:
            await runtime.shutdown()
            await db.close()
