"""Phase 5 remediation tests: the sandbox-context prompt is character-neutral.

The Phase 5 bridge added prompt *labels* that assumed a female character
("她自己经历过的相关往事"). Fixed infrastructure text must stay neutral so a
male / non-binary / unspecified character reads correctly; the persona layer
owns voice and pronouns. Dynamic data from the seed (pet/project/space/action
names) is explicitly still allowed.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

from app.character.context import CharacterContextBuilder
from app.config.settings import DatabaseConfig, SandboxConfig
from app.database.database import Database
from app.sandbox.bible import BibleCompiler
from app.sandbox.models import ActionDefinition
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from tests.ai_mocks import MockAIProvider
from tests.conftest import BIBLE_PATH as FIXTURE_BIBLE
from tests.test_sandbox_cognitive_bridge import make_chat_bot

#: fixed-text pronouns / identity that must never be baked into a label.
#: "他人" (other people) and "其他" are ordinary words, not pronouns.
PRONOUN_RE = re.compile(r"她|罐头|小罐头|(?<!其)他(?!人)")

LIN_BIBLE = """# Lin 档案

## Static Facts

- 角色名：Lin
- 性别：男
- 年龄：30岁
- 居住：单身公寓
- 生活状态：木工手艺人

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

### Objects
- workbench 工作台（木工房）：木工

### Inventory
- workbench：木料 × 5

## Values

- 对木工：手上有活心里就踏实
"""

#: deliberately no 性别 line at all — the context layer must not need one
UNSPECIFIED_BIBLE = """# 小灰档案

## Static Facts

- 角色名：小灰
- 年龄：未知
- 居住：旅店

## Modes

### 宅模式
- id: home
- 触发：待在旅店，默认状态
- 风格：简短
- 行为：发呆

## World Seed

### Spaces
- inn（旅店，根空间）：
  - room 房间

### Objects
- desk 书桌（房间）：发呆

### Inventory
- desk：纸 × 3

## Values

- 对生活：慢慢来
"""


class Clock:
    def __init__(self, now: float = 1_700_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


async def make_db(tmp_path) -> Database:  # type: ignore[no-untyped-def]
    db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'neutral.db'}"))
    await db.connect()
    return db


async def make_sandbox(db, *, bible_text: str | None = None, bible_path=None, clock=None):  # type: ignore[no-untyped-def]
    if bible_text is not None:
        path = Path(db._config.sqlite_path).parent / "synthetic.md"  # noqa: SLF001
        path.write_text(bible_text, encoding="utf-8")
        bible_path = path
    bible = BibleCompiler(bible_path or FIXTURE_BIBLE).compile()
    runtime = SandboxRuntime(
        SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7),
        SandboxStore(db),
        bible=bible,
        clock=clock or Clock(),
    )
    await runtime.start()
    return runtime


async def give_life(sandbox, clock, *, action_id: str = "idle", name: str = "") -> None:  # type: ignore[no-untyped-def]
    """Produce a bit of life so every sandbox section has content."""
    if name:
        template = sandbox.actions.definitions["idle"]
        sandbox.actions.definitions[action_id] = ActionDefinition(
            **{
                **template.model_dump(),
                "id": action_id,
                "name": name,
                "activity": "crafting",
                "spaces": ["*"],
                "required_objects": [],
                "consumes": {},
                "detail_pool": [f"做的是{name}"],
                "typical_minutes": 60,
            }
        )
    started = await sandbox._start_action(action_id)  # noqa: SLF001
    assert started is not None, action_id
    sandbox.current_action.planned_end_at = clock.now
    clock.advance(1)
    await sandbox._complete_action()  # noqa: SLF001
    await sandbox.flush_experiences()
    sandbox.set_knowledge("some_life_fact", source="observation", reason="test")


async def rendered_sandbox_sections(sandbox) -> str:  # type: ignore[no-untyped-def]
    # a realistic query mentions the topic; evidence-gated retrieval needs it
    payload = (await sandbox.cognitive_context(query="玩单机游戏 木工 发呆")).as_prompt_payload()
    parts, _layers = CharacterContextBuilder._sandbox_blocks(payload)  # noqa: SLF001
    return "\n".join(parts)


# ---------------------------------------------------------------- Test 8/9


class TestSandboxContextIsCharacterNeutral:
    async def test_female_character_sections_are_neutral(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        sandbox = await make_sandbox(db, clock=clock)  # 罐头 (female)
        try:
            await give_life(sandbox, clock, action_id="play_singleplayer")
            rendered = await rendered_sandbox_sections(sandbox)
            assert rendered, "sections rendered empty"
            assert not PRONOUN_RE.search(rendered), rendered
            assert "【相关生活记忆】" in rendered
        finally:
            await sandbox.shutdown()
            await db.close()

    async def test_male_character_end_to_end_prompt(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        sandbox = await make_sandbox(db, bible_text=LIN_BIBLE, clock=clock)
        try:
            await give_life(sandbox, clock, action_id="woodwork", name="木工")
            rendered = await rendered_sandbox_sections(sandbox)
            assert "完成了木工" in rendered  # dynamic action name flows through
            assert not PRONOUN_RE.search(rendered)

            provider = MockAIProvider(behaviors={"A": ["椅子做好啦"]})
            bot = await make_chat_bot(tmp_path, provider, sandbox=sandbox)
            try:
                trace: dict = {}
                await bot.character.respond(
                    "private:777", 777, "木工做得怎么样了？", context_trace=trace
                )
                system = provider.calls[0]["messages"][0].content
                labels = ("【近期延续状态】", "【相关生活记忆】", "【最近发生的经历】")
                assert all(label in system for label in labels)
                # the labels and their notes carry no fixed pronouns
                for label in labels:
                    section = system.split(label, 1)[1]
                    note = section.split("\n", 1)[0]
                    assert not PRONOUN_RE.search(note), note
                layers = {row["layer"] for row in trace["layers"]}
                assert {"sandbox_memory", "continuity_snapshot", "recent_experience"} <= layers
            finally:
                await bot.shutdown()
        finally:
            await sandbox.shutdown()
            await db.close()

    async def test_gender_unspecified_character_works(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        db = await make_db(tmp_path)
        clock = Clock()
        sandbox = await make_sandbox(db, bible_text=UNSPECIFIED_BIBLE, clock=clock)
        try:
            await give_life(sandbox, clock, action_id="idle")
            payload = (await sandbox.cognitive_context(query="在做什么")).as_prompt_payload()
            parts, layers = CharacterContextBuilder._sandbox_blocks(payload)  # noqa: SLF001
            rendered = "\n".join(parts)
            assert rendered
            assert not PRONOUN_RE.search(rendered)
            assert layers  # trace produced without any gender field
        finally:
            await sandbox.shutdown()
            await db.close()

    async def test_dynamic_seed_data_still_reaches_the_prompt(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        """Neutral labels must not strip legitimate world data (§8)."""
        db = await make_db(tmp_path)
        clock = Clock()
        sandbox = await make_sandbox(db, clock=clock)
        try:
            await give_life(sandbox, clock, action_id="play_singleplayer")
            rendered = await rendered_sandbox_sections(sandbox)
            dynamic_names = [
                *[payload.get("name", "") for payload in sandbox.projects.values()],
                sandbox.spaces.name(sandbox.character.location),
            ]
            assert any(name and name in rendered for name in dynamic_names), rendered
        finally:
            await sandbox.shutdown()
            await db.close()


# ---------------------------------------------------------------- Test 10


class TestPromptSourceIsNeutral:
    """§10 source guard: *template* text is neutral; comments/docstrings are
    documentation, not prompt text — the AST check targets real literals."""

    def _prompt_literals(self, path: Path) -> list[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        literals: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if id(node) in docstrings:
                    continue
                literals.append(node.value)
        return literals

    def test_context_and_cognitive_prompt_literals_are_neutral(self) -> None:
        root = Path(__file__).resolve().parent.parent / "app"
        for path in (root / "character" / "context.py", root / "sandbox" / "cognitive.py"):
            offenders = [text for text in self._prompt_literals(path) if PRONOUN_RE.search(text)]
            assert offenders == [], f"{path.name} 固定文案含性别/身份词: {offenders}"

    def test_neutral_labels_are_the_ones_shipped(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        source = (
            Path(__file__).resolve().parent.parent / "app" / "character" / "context.py"
        ).read_text(encoding="utf-8")
        for label in ("【近期延续状态】", "【相关生活记忆】", "【最近发生的经历】"):
            assert label in source
        for retired in ("【她自己经历过的相关往事】", "她自己生活的近况"):
            assert retired not in source
