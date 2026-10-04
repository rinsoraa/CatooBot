"""验收 1：`buy_sweets` 的 `requires_anchor` 必须与实际采购槽一致。

Phase B 遗留标注错误：模板买的是 ``{dessert}``（purchase/restock 都是），
`requires_anchor` 却写成 ``"snack"``。解析期门槛用的是 requires_anchor，于是
"有蛋糕、没有布丁"的档案会在 seed 解析阶段直接失去"买甜食"。

夹具 `character_bible_dessert_only.md` 只含蛋糕（dessert 锚点），刻意没有
布丁（snack 锚点），用来锁定这条回归。
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.definition import CharacterDefinition
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.test_sandbox_memory_foundation import Clock, make_db

DESSERT_ONLY_BIBLE = (
    Path(__file__).resolve().parent / "fixtures" / "character_bible_dessert_only.md"
)


async def make_sandbox(*, db, clock):  # type: ignore[no-untyped-def]
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=BibleCompiler(DESSERT_ONLY_BIBLE).compile(),
        clock=clock,
    )
    await runtime.start()
    return runtime


class TestBuySweetsAnchor:
    def test_requires_anchor_matches_the_purchase_slot(self) -> None:
        definition = CharacterDefinition.from_bible(BibleCompiler(DESSERT_ONLY_BIBLE).compile())

        assert definition.anchors.get("dessert") == "蛋糕"
        assert not definition.anchors.get("snack"), "夹具刻意的前提：没有 snack 锚点"
        # 关键回归：只有 dessert 锚点也要拥有 buy_sweets
        assert "buy_sweets" in definition.action_ownership
        # 旧的 go_shopping_sweets 仍按其自身标注（snack + 双槽）要求锚点，
        # 本夹具既有蛋糕又有西点店，但没有布丁 —— 它不该被归属
        assert "go_shopping_sweets" not in definition.action_ownership

    async def test_dessert_only_world_resolves_and_buys_the_dessert(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            sweets = runtime.actions.definitions["buy_sweets"]
            assert sweets.purchase == {"fridge": {"蛋糕": 2}}
            assert sweets.restock == {"inventory": "fridge", "slot": "蛋糕", "min": 1, "target": 3}
            assert not sweets.requires_absent  # 纯采购动作：不做"缺货才可买"的门

            pantry = runtime.inventories.get("fridge")
            pantry.items["蛋糕"] = 1  # 落到 min 之下
            started = await runtime._start_action("buy_sweets")  # noqa: SLF001
            assert started is not None
            runtime.current_action.planned_end_at = clock.now  # type: ignore[union-attr]
            clock.advance(1)
            await runtime._complete_action()  # noqa: SLF001

            acquired = [
                (event.payload["item"], event.payload["quantity"])
                for event in runtime.events.of_type(ET.ITEM_ACQUIRED)
            ]
            assert acquired == [("蛋糕", 2)]
            assert pantry.count("蛋糕") == 3
            assert not runtime.events.of_type(ET.ITEM_CONSUMED)
        finally:
            await runtime.shutdown()
            await db.close()
