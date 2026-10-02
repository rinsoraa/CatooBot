"""Phase 4 remediation tests: continuity isolation + character-neutral language.

Two audits drove this round:

1. Continuity snapshots were persisted under one fixed ``sandbox_state`` key —
   two characters sharing a database overwrote each other. The key is now
   character-scoped with an owner-checked legacy fallback.
2. Experience/Memory text assumed a female character ("她…"). The foundation
   must produce neutral facts; persona/NLG layers own the phrasing later.
"""

from __future__ import annotations

from pathlib import Path

from app.config.settings import DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.experience import ExperienceKind, ExperienceRecord
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE

OTHER_BIBLE_PATH = Path(__file__).resolve().parent / "fixtures" / "character_other.md"

#: a third, synthetic character: male, no pet, woodworking + reading
LIN_BIBLE = """# Lin 档案

## Static Facts

- 角色名：Lin
- 性别：男
- 年龄：30岁
- 居住：单身公寓
- 生活状态：木工手艺人，作息规律

## Modes

### 工作模式
- id: home
- 触发：在工作间做木工，默认状态
- 风格：简短、务实
- 行为：做木工、阅读

## World Seed

### Spaces
- studio（工作间，根空间）：
  - benchroom 木工房
  - readingroom 阅读室

### Objects
- workbench 工作台（木工房）：木工

### Inventory
- workbench：木料 × 5

## Values

- 对木工：手上有活心里就踏实
"""


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'remediation.db'}"))
    await db.connect()
    return db


async def make_runtime(*, db: Database | None = None, bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock or Clock(),
    )
    await runtime.start()
    return runtime


async def make_lin_runtime(tmp_path, *, db=None, clock=None):  # type: ignore[no-untyped-def]
    path = tmp_path / "lin.md"
    path.write_text(LIN_BIBLE, encoding="utf-8")
    return await make_runtime(db=db, bible_path=path, clock=clock)


async def complete(runtime, clock, action_id: str) -> None:  # type: ignore[no-untyped-def]
    started = await runtime._start_action(action_id)  # noqa: SLF001
    assert started is not None, f"{action_id} did not start"
    runtime.current_action.planned_end_at = clock.now
    clock.advance(1)
    await runtime._complete_action()  # noqa: SLF001
    await runtime.flush_experiences()


# ------------------------------------------------------- continuity isolation


class TestContinuityIsolation:
    async def test_snapshots_do_not_overwrite_each_other(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        first = await make_runtime(db=db, clock=clock)
        second = await make_runtime(db=db, bible_path=OTHER_BIBLE_PATH, clock=clock)
        try:
            # two clearly different worlds
            await first._start_action("watch_animation", duration_minutes=60)  # noqa: SLF001
            first.set_knowledge("first_world_fact", source="observation", reason="test")
            second.set_knowledge("second_world_fact", source="observation", reason="test")
            await first.flush_experiences()
            await second.flush_experiences()

            assert first.character_id != second.character_id
            a_snapshot = await first.build_continuity()
            b_snapshot = await second.build_continuity()

            a_loaded = await first.continuity_snapshot.load()
            b_loaded = await second.continuity_snapshot.load()
            assert a_loaded is not None and b_loaded is not None
            assert a_loaded["character_id"] == first.character_id
            assert b_loaded["character_id"] == second.character_id
            # load(A) == A, load(B) == B — no cross-contamination
            assert a_loaded["current_location"] == a_snapshot.current_location
            assert b_loaded["current_location"] == b_snapshot.current_location
            a_keys = {k["key"] for k in a_loaded["active_knowledge"]}
            b_keys = {k["key"] for k in b_loaded["active_knowledge"]}
            assert "first_world_fact" in a_keys and "first_world_fact" not in b_keys
            assert "second_world_fact" in b_keys and "second_world_fact" not in a_keys
            assert first.character_id not in str(b_loaded)
            assert second.character_id not in str(a_loaded)

            # the scoped keys are the ones written
            assert await first.store.state_get(f"continuity_snapshot:{first.character_id}")
            assert await first.store.state_get(f"continuity_snapshot:{second.character_id}")
        finally:
            await first.shutdown()
            await second.shutdown()
            await db.close()

    async def test_legacy_key_is_owner_checked(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """§4: an old fixed-key snapshot is read only by its actual owner."""
        import json

        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, clock=clock)
        try:
            other = await make_runtime(db=db, bible_path=OTHER_BIBLE_PATH, clock=clock)
            try:
                # legacy key without a reliable owner → never attributed
                await runtime.store.state_set(
                    "continuity_snapshot", json.dumps({"current_location": "somewhere"})
                )
                assert await runtime.continuity_snapshot.load() is None

                # legacy key owned by this character → accepted as compat data
                await runtime.store.state_set(
                    "continuity_snapshot",
                    json.dumps({"character_id": runtime.character_id, "current_location": "old"}),
                )
                loaded = await runtime.continuity_snapshot.load()
                assert loaded is not None and loaded["current_location"] == "old"
                # ...but the *other* character must not read it
                assert await other.continuity_snapshot.load() is None

                # once a scoped snapshot exists it wins over the legacy key
                await runtime.build_continuity()
                scoped = await runtime.continuity_snapshot.load()
                assert scoped is not None and scoped["current_location"] != "old"
            finally:
                await other.shutdown()
        finally:
            await runtime.shutdown()
            await db.close()


# ------------------------------------------------------------ neutral language


FORBIDDEN_IN_MEMORY = ("她", "罐头", "小罐头", "小喵", "空凛", "可乐")


class TestMemoryLanguageIsCharacterNeutral:
    async def test_second_character_memory_text_is_neutral(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_runtime(db=db, bible_path=OTHER_BIBLE_PATH, clock=clock)
        try:
            await complete(runtime, clock, "idle")
            # an experience exists for the completed action…
            experiences = runtime.experiences.emitted()
            assert experiences
            # …and its candidate content is neutral even when below threshold
            candidates = [
                c
                for record in experiences
                for c in runtime.candidates.from_experience(record, now=clock.now)
            ]
            assert candidates
            # a promoted memory (knowledge discovery) is neutral too
            runtime.set_knowledge("learned_thing", source="observation", reason="test")
            await runtime.flush_experiences()
            memories = await runtime.memory.active_memories()
            assert memories

            texts = (
                [r.summary for r in experiences]
                + [c.content for c in candidates]
                + [m["content"] for m in memories]
            )
            for text in texts:
                for needle in FORBIDDEN_IN_MEMORY:
                    assert needle not in text, f"角色专属文案泄漏: {text!r} 含 {needle!r}"
                # 阿澈's world owns no Minecraft action, so the word must not appear
                assert "Minecraft" not in text
        finally:
            await runtime.shutdown()
            await db.close()

    async def test_third_synthetic_character_reads_naturally(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """A synthetic bible (Lin, woodworking) proves the foundation is agnostic."""
        db = await make_db(tmp_path)
        clock = Clock()
        runtime = await make_lin_runtime(tmp_path, db=db, clock=clock)
        try:
            assert runtime.character_id.startswith("Lin@")
            # give Lin an action the templates cannot know about
            from app.sandbox.models import ActionDefinition

            template = runtime.actions.definitions["idle"]
            runtime.actions.definitions["woodwork"] = ActionDefinition(
                **{
                    **template.model_dump(),
                    "id": "woodwork",
                    "name": "木工",
                    "activity": "crafting",
                    "spaces": ["*"],
                    "required_objects": [],
                    "consumes": {},
                    "detail_pool": ["做了一把椅子"],
                    "typical_minutes": 60,
                }
            )
            await complete(runtime, clock, "woodwork")

            experience = runtime.experiences.emitted()[-1]
            assert experience.kind is ExperienceKind.action_completed
            assert experience.summary.startswith("完成了木工")
            candidate = runtime.candidates.from_experience(experience, now=clock.now)[0]
            assert candidate.content == "完成了木工"
            memories = await runtime.memory.active_memories()
            assert any(m["content"] == "完成了木工" for m in memories)
            for text in (experience.summary, candidate.content, *[m["content"] for m in memories]):
                assert "她" not in text and "罐头" not in text
        finally:
            await runtime.shutdown()
            await db.close()

    def test_foundation_sources_are_character_neutral(self) -> None:
        """§15 source scan: neutral templates only, facts come from the seed."""
        root = Path(__file__).resolve().parent.parent / "app" / "sandbox"
        forbidden = ("她", "罐头", "小罐头", "小喵", "空凛", "可乐", "play_minecraft")
        for name in ("experience.py", "memory_foundation.py", "continuity_snapshot.py"):
            source = (root / name).read_text(encoding="utf-8")
            hits = [needle for needle in forbidden if needle in source]
            assert hits == [], f"{name} 仍含角色专属/性别化文案: {hits}"

    def test_experience_models_carry_no_gendered_defaults(self) -> None:
        """Neutral means neutral in data too: no gendered summary literals."""
        record = ExperienceRecord(
            id="exp_neutral",
            character_id="whoever",
            kind=ExperienceKind.action_completed,
            summary="完成了某件事",
        )
        assert "她" not in record.summary
        assert record.character_id == "whoever"
