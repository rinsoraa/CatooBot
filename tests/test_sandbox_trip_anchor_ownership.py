"""Phase C.1 tests: 采购动作的锚点归属 + 启动扫描直接成行（真实档案场景）。

真实档案（config/character_bible.md，只读）里没有「买可乐 / 买零食 / 补货」这类
字面词，但库存与偏好里有可乐、布丁、蛋糕。规则：采购家族（模板带 purchase /
restock / requires_absent，且引用槽位占位符 {drink}/{snack}/{dessert}）在**锚点
存在**时归属 —— 与关键词归属取并集；锚点为空或容器（fridge）不存在时绝不归属。

`character_bible_drinks.md` 是只含锚点、不含购买字面词的最小夹具（真实档案
不入库）。启动时已有两个槽位见底 → 启动 sweep 直接生成唯一 trip，无需事件。
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.definition import CharacterDefinition
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.goals import GoalKind, GoalStatus
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_memory_foundation import Clock, make_db

DRINKS_BIBLE = Path(__file__).resolve().parent / "fixtures" / "character_bible_drinks.md"
OTHER_BIBLE = Path(__file__).resolve().parent / "fixtures" / "character_other.md"

SHOPPING_FAMILY = {
    "buy_cola",
    "buy_snacks",
    "buy_sweets",
    "go_shopping_cola",
    "go_shopping_sweets",
}
#: literal words that would let keyword ownership (not anchors) own the family
BUYING_WORDS = ("买可乐", "补可乐", "买零食", "买饮料", "补货", "买甜食", "甜品店")


class TestAnchorOwnership:
    def test_anchor_presence_owns_the_shopping_family(self) -> None:
        bible = BibleCompiler(DRINKS_BIBLE).compile()
        definition = CharacterDefinition.from_bible(bible)
        owned = set(definition.action_ownership)

        assert {"buy_cola", "buy_snacks", "buy_sweets", "return_home"} <= owned
        assert owned >= SHOPPING_FAMILY  # go_shopping_* follow the same rule
        assert definition.anchors["drink"] == "可乐"
        assert definition.anchors["snack"] == "布丁"
        assert definition.anchors["dessert"] == "蛋糕"

        # the fixture text contains none of the literal buying words — the
        # ownership above can only come from anchor presence
        text = DRINKS_BIBLE.read_text(encoding="utf-8")
        for word in BUYING_WORDS:
            assert word not in text, f"fixture must not contain {word!r}"

    def test_keyword_ownership_stays_a_union(self) -> None:
        """The main fixture keeps owning the family it owned by words before."""
        definition = CharacterDefinition.from_bible(BibleCompiler(FIXTURE_BIBLE).compile())
        assert set(definition.action_ownership) >= SHOPPING_FAMILY

    def test_empty_anchors_or_missing_container_own_nothing(self) -> None:
        # 阿澈 has no 可乐/布丁/蛋糕 and no fridge container (only a kettle):
        # a non-empty generic drink anchor (咖啡) must not grant a cola action
        definition = CharacterDefinition.from_bible(BibleCompiler(OTHER_BIBLE).compile())
        assert definition.anchors.get("drink") == "咖啡"
        assert not (SHOPPING_FAMILY & set(definition.action_ownership))


class TestStartupSweepTrip:
    async def test_startup_sweep_forms_one_trip_and_one_experience(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        bible = BibleCompiler(DRINKS_BIBLE).compile()
        runtime = SandboxRuntime(
            SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
            SandboxStore(db),
            bible=bible,
            clock=clock,
        )
        # two slots are already at/below their floors *before* start — the
        # startup sweep must notice without any consume/acquire event
        pantry = runtime.inventories.get("fridge")
        pantry.items["可乐"] = 1  # min 2
        pantry.items["蛋糕"] = 1  # min 1
        await runtime.start()
        try:
            trip = runtime.goals.open_trip()
            assert trip is not None, "startup sweep must form the trip"
            stops = trip.metadata["stops"]
            assert {stop["space"] for stop in stops} == {
                "dessert_shop",
                "convenience_store",
            }
            assert [item["action_id"] for stop in stops for item in stop["items"]] == [
                "buy_sweets",
                "buy_cola",
            ]

            for _ in range(40):
                clock.advance(600)
                await runtime.tick(minutes=10)
                if runtime.goals.open_trip() is None:
                    break

            trips = [goal for goal in runtime.goals.all() if goal.kind is GoalKind.shopping_trip]
            assert len(trips) == 1, "repeated scanning must never open a second trip"
            assert trips[0].status is GoalStatus.completed
            created = [
                event
                for event in runtime.events.of_type(ET.GOAL_CREATED)
                if event.payload.get("kind") == GoalKind.shopping_trip.value
            ]
            assert len(created) == 1

            # home → dessert_shop → convenience_store → home (any later move
            # belongs to whatever she does after the trip closed)
            moved = [event.target_entity_id for event in runtime.events.of_type(ET.ENTITY_MOVED)]
            assert moved[:3] == ["dessert_shop", "convenience_store", "apartment"]

            # both purchase actions really ran, with their canonical effects
            rows = await db.fetchall(
                "SELECT definition_id, status FROM sandbox_actions"
                " WHERE definition_id IN ('buy_sweets', 'buy_cola')"
            )
            assert {row["definition_id"] for row in rows} == {"buy_sweets", "buy_cola"}
            assert all(row["status"] == "completed" for row in rows)
            acquired = [
                (event.payload["item"], event.payload["quantity"])
                for event in runtime.events.of_type(ET.ITEM_ACQUIRED)
            ]
            assert acquired == [("蛋糕", 2), ("可乐", 6)]

            # the user-visible layer shows exactly one trip experience
            experiences = await runtime.store.recent_experiences(
                character_id=runtime.character_id, limit=20
            )
            assert [row["kind"] for row in experiences] == ["errand_trip"]
            assert experiences[0]["summary"] == "完成了买甜食和可乐"
            assert experiences[0]["episode_key"] == f"trip:{trip.metadata['trip_id']}"
        finally:
            await runtime.shutdown()
            await db.close()
