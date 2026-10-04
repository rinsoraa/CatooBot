"""Phase D：基础设施动作按"世界能不能支持"归属，不靠字面词。

契约:
* 采购家族之外，``tags`` 标记为能力家族（``CAPABILITY_FAMILIES``：self-care /
  work）的模板在**世界的房间与工具齐备**时即被拥有 —— 有卫生间的家就能洗澡、
  有客厅和电脑的家就能接活，档案正文不必恰好写出「洗澡」「委托」；
* 缺房间或缺工具的档案不拥有这些动作（顺带确认 ``walk`` 仍是按语料归属的
  普通动作，不在本次范围内）；
* 而且这不是显示层面的补丁：``hygiene`` / ``work_need`` 到临界时，critical 强制
  逻辑会真的选中对应动作，需求随之下降。

夹具 ``character_bible_capable.md`` 有卫生间/客厅/电脑但刻意不含任何字面词；
``character_bible_dessert_only.md`` 既没有这些房间也没有电脑。
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import SandboxConfig
from app.sandbox.action_templates import ACTION_TEMPLATES
from app.sandbox.bible import BibleCompiler
from app.sandbox.definition import CAPABILITY_FAMILIES, CharacterDefinition
from app.sandbox.events import SandboxEventType as ET
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from app.sandbox.world_seed import build_world_seed
from tests.test_sandbox_memory_foundation import Clock, make_db

FIXTURES = Path(__file__).resolve().parent / "fixtures"
CAPABLE_BIBLE = FIXTURES / "character_bible_capable.md"
DESSERT_ONLY_BIBLE = FIXTURES / "character_bible_dessert_only.md"
MAIN_BIBLE = FIXTURES / "character_bible.md"

#: literal words that would let keyword ownership (not capability) own the pair
LITERAL_WORDS = ("洗澡", "洗漱", "委托", "约稿", "零工", "画稿", "插画稿")
CAPABILITY_ACTIONS = ("shower", "work_commission")


async def make_sandbox(*, db, clock, path=CAPABLE_BIBLE):  # type: ignore[no-untyped-def]
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=BibleCompiler(path).compile(),
        clock=clock,
    )
    await runtime.start()
    return runtime


async def run_until_relieved(runtime, clock, need: str, *, limit: int = 24) -> list[str]:
    """Tick until the need drops below 0.5; returns the actions she started."""
    started: list[str] = []
    for _ in range(limit):
        clock.advance(600)
        await runtime.tick(minutes=10)
        action = runtime.current_action.definition_id if runtime.current_action else ""
        if action and (not started or started[-1] != action):
            started.append(action)
        if runtime.needs.level(need) < 0.5:
            break
    return started


def requested_reasons(runtime) -> dict[str, list[str]]:  # type: ignore[no-untyped-def]
    out: dict[str, list[str]] = {}
    for event in runtime.events.of_type(ET.ACTION_REQUESTED):
        out.setdefault(str(event.target_entity_id), []).extend(
            event.payload.get("reason_codes", [])
        )
    return out


class TestCapabilityOwnership:
    def test_the_world_supplies_the_actions_not_the_words(self) -> None:
        bible = BibleCompiler(CAPABLE_BIBLE).compile()
        definition = CharacterDefinition.from_bible(bible)

        text = CAPABLE_BIBLE.read_text(encoding="utf-8")
        for word in LITERAL_WORDS:
            assert word not in text, f"夹具必须不含字面词 {word!r}"

        for action_id in CAPABILITY_ACTIONS:
            assert action_id in definition.action_ownership
            tags = ACTION_TEMPLATES[action_id]["tags"]
            assert any(tag in CAPABILITY_FAMILIES for tag in tags), tags

    def test_resolved_actions_relieve_the_orphan_needs(self) -> None:
        bible = BibleCompiler(CAPABLE_BIBLE).compile()
        definition = CharacterDefinition.from_bible(bible)

        assert definition.anchors.get("dessert") == "蛋糕"

        seed = build_world_seed(definition, bible)
        by_id = {action["id"]: action for action in seed.actions}
        assert by_id["shower"]["need_relief"] == {"hygiene": 0.95}
        assert by_id["shower"]["spaces"] == ["bathroom"]
        assert by_id["work_commission"]["need_relief"]["work_need"] == 0.9
        assert by_id["work_commission"]["spaces"] == ["livingroom"]
        assert "computer" in {obj.id for obj in definition.object_definitions}

    def test_missing_room_or_tools_owns_nothing(self) -> None:
        bible = BibleCompiler(DESSERT_ONLY_BIBLE).compile()
        definition = CharacterDefinition.from_bible(bible)

        assert not (set(CAPABILITY_ACTIONS) & set(definition.action_ownership))
        assert "bathroom" not in {space.id for space in definition.space_definitions}
        assert "computer" not in {obj.id for obj in definition.object_definitions}

    def test_the_main_fixture_gains_them_too(self) -> None:
        """主夹具也受益：它有卫生间和电脑，但正文只字未提洗澡/委托。"""
        definition = CharacterDefinition.from_bible(BibleCompiler(MAIN_BIBLE).compile())
        assert set(CAPABILITY_ACTIONS) <= set(definition.action_ownership)

    def test_walk_stays_a_text_owned_action(self) -> None:
        """范围说明：walk 不属于能力家族，仍按语料归属（两个世界都没有它）。"""
        for path in (CAPABLE_BIBLE, DESSERT_ONLY_BIBLE):
            definition = CharacterDefinition.from_bible(BibleCompiler(path).compile())
            assert "walk" not in definition.action_ownership


class TestCapabilityBehaviour:
    async def test_critical_hygiene_really_showers(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.needs.get("hygiene").level = 1.0  # type: ignore[union-attr]

            started = await run_until_relieved(runtime, clock, "hygiene")

            assert "shower" in started, started
            assert runtime.needs.level("hygiene") < 0.2
            reasons = requested_reasons(runtime)
            assert "critical:hygiene" in reasons.get("shower", []), reasons
            rows = await runtime.store.recent_finished_actions(limit=20)
            assert "shower" in [row[0] for row in rows]
            # 真的去了卫生间，而不是原地净化
            moved = [event.target_entity_id for event in runtime.events.of_type(ET.ENTITY_MOVED)]
            assert "bathroom" in moved
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_critical_work_need_really_takes_work(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock)
        try:
            runtime.needs.get("work_need").level = 1.0  # type: ignore[union-attr]

            started = await run_until_relieved(runtime, clock, "work_need")

            assert "work_commission" in started, started
            assert runtime.needs.level("work_need") < 0.2
            reasons = requested_reasons(runtime)
            assert "critical:work_need" in reasons.get("work_commission", []), reasons
            rows = await runtime.store.recent_finished_actions(limit=20)
            assert "work_commission" in [row[0] for row in rows]
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_no_bathroom_no_shower_means_hygiene_stays_put(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """反例：世界里没有卫生间 → 没有任何动作能缓解，需求原地不动。"""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_sandbox(db=db, clock=clock, path=DESSERT_ONLY_BIBLE)
        try:
            runtime.needs.get("hygiene").level = 1.0  # type: ignore[union-attr]

            started = await run_until_relieved(runtime, clock, "hygiene", limit=24)

            assert "shower" not in started
            assert runtime.needs.level("hygiene") == 1.0
        finally:
            await runtime.shutdown()
            await db.close()
