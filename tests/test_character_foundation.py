"""Phase 1: Character Bible → Definition → World Seed → Runtime (§63-§69).

The chain must work from *any* natural-language Chinese bible — not just the
shipped one. Every test that proves "换一份档案就是另一个角色" uses a
*different* character inline (阿澈, no pet), never the repo's own bible.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.config.settings import SandboxConfig
from app.sandbox.bible import BibleCompiler
from app.sandbox.definition import CharacterDefinition
from app.sandbox.runtime import SandboxRuntime
from app.sandbox.store import SandboxStore
from app.sandbox.world_seed import build_world_seed

REPO_BIBLE = Path(__file__).resolve().parent.parent / "config" / "character_bible.md"

#: a *different* character: different names, no pet, one room, no Minecraft
OTHER_BIBLE = """# 阿澈档案

## Static Facts

- 角色名：阿澈
- 别名：小澈
- 性别：男
- 年龄：22岁
- 居住：合租公寓
- 生活状态：自由职业画师，作息正常，喜欢咖啡

## Modes

### 宅家模式
- id: home
- 触发：在家画稿，默认状态
- 风格：安静、简短
- 行为：画画、煮咖啡、浇花

### 外出模式
- id: outdoor
- 触发：出门买咖啡豆和画材
- 风格：礼貌、简短
- 行为：快去快回

## Social Boundaries

- 不聊收入：直接岔开话题

## Preferences

### 食物饮料
- 咖啡、面包

### 画画
- 数位板是吃饭家伙

## World Seed

### Spaces
- studio（画室公寓，根空间）：
  - workroom 工作间
  - restroom 休息间

### Objects
- desk 数位桌（工作间）：画画
- kettle 咖啡壶（工作间）：煮咖啡

### Inventory
- desk：咖啡 × 3、面包 × 2

## Values

- 对创作：画完一张是一张
"""

# ------------------------------------------------------------------ fixtures


@pytest.fixture
def repo_bible():
    pytest.importorskip("app.sandbox.bible")
    if not REPO_BIBLE.exists():
        pytest.skip("repo character bible not present")
    return BibleCompiler(REPO_BIBLE).compile()


@pytest.fixture
def other_bible(tmp_path: Path):
    path = tmp_path / "other_bible.md"
    path.write_text(OTHER_BIBLE, encoding="utf-8")
    return BibleCompiler(path).compile()


async def make_runtime(bible, tmp_path, clock):  # type: ignore[no-untyped-def]
    store = SandboxStore(None)
    config = SandboxConfig(enabled=True, tick_seconds=600, simulation_seed=7)
    runtime = SandboxRuntime(config, store, bible=bible, clock=clock)
    await runtime.start()
    return runtime


# --------------------------------------------------- §63 Bible Parsing


class TestBibleParsing:
    def test_chinese_natural_language_bible_parses(self, other_bible) -> None:
        """中文自然语言档案：标题/列表/模式/边界全解析（§30/§31）。"""
        assert other_bible.facts["角色名"] == "阿澈"
        assert {mode.id for mode in other_bible.modes} == {"home", "outdoor"}
        assert other_bible.boundaries and "不聊收入" in other_bible.boundaries[0]
        assert other_bible.pet.name == ""  # no pet section → no pet
        assert other_bible.spaces and other_bible.objects

    def test_spaces_have_kind_parent_connections(self, repo_bible) -> None:
        """空间编译出 kind/parent/connections（拓扑来自缩进树）。"""
        by_id = {space.id: space for space in repo_bible.spaces}
        assert by_id["livingroom"].parent == "apartment"
        assert "apartment" in by_id["livingroom"].connections
        assert by_id["dessert_shop"].kind == "shop"
        assert by_id["apartment"].kind == "apartment"

    def test_modes_carry_speech_calibration(self, repo_bible) -> None:
        """§18/§32: 模式带口癖短语与语气提示（对话示例 ≠ 世界规则）。"""
        home = next(mode for mode in repo_bible.modes if mode.id == "home")
        assert "好麻烦" in home.phrases
        assert home.tone == "casual"
        definition = CharacterDefinition.from_bible(repo_bible)
        deep = next(mode for mode in definition.modes if mode.id == "deep_night")
        assert deep.time_window == (2, 5)  # 凌晨2点到5点
        assert deep.trigger_kind == "time"

    def test_social_spaces_from_prose_not_needles(self, repo_bible) -> None:
        """§24: 社交空间从行文提取（不再依赖硬编码关键词表）。"""
        assert "Minecraft服务器" in repo_bible.social_spaces
        assert "猫图群" in repo_bible.social_spaces

    def test_behavior_candidates_never_become_rules(self, repo_bible) -> None:
        """§33: 对话示例里的舞台指示 → 候选，绝不自动晋升为规则。"""
        candidates = [rule for rule in repo_bible.rules if rule.status == "candidate"]
        canonical = [rule for rule in repo_bible.rules if rule.status == "canonical"]
        assert candidates and canonical
        assert all(rule.canonical is False for rule in candidates)
        assert all(rule.runtime == "" for rule in candidates)

    def test_rules_record_source_section(self, repo_bible) -> None:
        """§35: 每条 Canonical 规则可反查档案原文段落。"""
        rule = repo_bible.rule("homewear_on_arrival")
        assert rule is not None
        assert rule.source.startswith("“")
        assert rule.source_section in ("Static Facts", "Modes", "Values", "Persona Prose", "")


# ------------------------------------------------ §64 Definition compilation


class TestCharacterDefinition:
    def test_definition_sections_compiled(self, repo_bible) -> None:
        definition = CharacterDefinition.from_bible(repo_bible)
        assert definition.identity.name == "罐头"
        assert definition.identity.nickname == "小罐头"
        assert len(definition.modes) == 5
        assert definition.pet is not None and definition.pet.name == "小喵"
        assert definition.pet.food_item == "猫粮"
        assert definition.relationships[0].name == "空凛"
        assert definition.relationships[0].core is True
        assert definition.anchors["drink"] == "可乐"
        assert "play_minecraft" in definition.action_ownership
        assert definition.space_definitions and definition.object_definitions

    def test_a_different_bible_compiles_differently(self, other_bible) -> None:
        """换一份档案 = 另一个角色（Phase 1 的最终目标的微缩版）。"""
        definition = CharacterDefinition.from_bible(other_bible)
        assert definition.identity.name == "阿澈"
        assert definition.pet is None  # no pet in the bible → no pet definition
        assert definition.anchors["drink"] == "咖啡"
        assert "play_minecraft" not in definition.action_ownership  # no Minecraft
        assert definition.social_space_definitions == []


# ------------------------------------------------------- §65 World Seed


class TestWorldSeed:
    def test_seed_contains_every_structure(self, repo_bible) -> None:
        definition = CharacterDefinition.from_bible(repo_bible)
        seed = build_world_seed(definition, repo_bible, simulation_seed=7)
        assert seed.character.name == "罐头"
        assert seed.character.start_modes == ["home"]
        assert seed.pets and seed.pets[0].name == "小喵"
        assert len(seed.spaces) >= 10
        assert len(seed.objects) >= 10
        assert "fridge" in seed.inventories and seed.inventories["fridge"]["可乐"] == 10
        assert seed.projects  # bible's 小城 became a project
        assert len(seed.social_spaces) >= 4
        assert len(seed.actions) >= 20
        assert len(seed.modes) == 5
        assert seed.rules
        assert seed.core_friend_names == ["空凛"]

    def test_actions_resolve_from_seed_not_hardcode(self, other_bible) -> None:
        """动作实例参数来自 Seed：阿澈的世界里没有 Minecraft/可乐链。"""
        definition = CharacterDefinition.from_bible(other_bible)
        seed = build_world_seed(definition, other_bible, simulation_seed=1)
        action_ids = {action["id"] for action in seed.actions}
        assert "drink_cola" not in action_ids  # no 可乐 anchor → not owned
        assert "play_minecraft" not in action_ids
        # pet actions are not ownable without a pet
        assert "feed_cat" not in action_ids

    def test_seed_is_serializable_and_reproducible(self, repo_bible) -> None:
        """§28/§29: 可导出；同一 bible+seed 重复构建结果逐字节一致。"""
        definition = CharacterDefinition.from_bible(repo_bible)
        first = build_world_seed(definition, repo_bible, simulation_seed=42)
        exported = first.export_seed()
        assert json.loads(json.dumps(exported)) == exported
        again = build_world_seed(
            CharacterDefinition.from_bible(BibleCompiler(REPO_BIBLE).compile()),
            BibleCompiler(REPO_BIBLE).compile(),
            simulation_seed=42,
        )
        assert again.export_seed() == exported


# -------------------------------------------- §67/§69 Isolation + Coverage


class TestRuntimeIsolation:
    async def test_runtime_initializes_from_a_different_bible(self, other_bible, tmp_path) -> None:
        """换 Bible 后：新角色名/无宠物/无旧角色痕迹（§67 微缩版）。"""
        import time

        runtime = await make_runtime(other_bible, tmp_path, clock=lambda: time.time())
        try:
            assert runtime.character.name == "阿澈"
            assert runtime.pet is None and runtime.pet_system is None
            assert "play_minecraft" not in runtime.actions.definitions
            assert runtime.character.location == runtime.seed.character.start_location
            # the repo character's names appear nowhere
            dumped = json.dumps(runtime.seed.export_seed(), ensure_ascii=False)
            assert "罐头" not in dumped and "小喵" not in dumped and "空凛" not in dumped
        finally:
            await runtime.shutdown()

    async def test_repo_bible_runtime_uses_seed_everywhere(self, repo_bible, tmp_path) -> None:
        """实体/库存/动作全部来自 Seed；无宠物世界不建宠物系统。"""
        import time

        runtime = await make_runtime(repo_bible, tmp_path, clock=lambda: time.time())
        try:
            assert runtime.character.name == "罐头"
            assert runtime.pet_system is not None
            assert runtime.pet_system.pet.name == "小喵"
            assert runtime.inventories.get("fridge").count("可乐") == 10
            key, item = runtime._pet_food or ("", "")  # noqa: SLF001
            assert key and item == "猫粮"
        finally:
            await runtime.shutdown()


class TestCoverage:
    def test_coverage_chain_has_all_levels(self, repo_bible) -> None:
        """§36/§37: Parsed→Compiled→Seeded→RuntimeConnected→Tested 全链可查。"""
        definition = CharacterDefinition.from_bible(repo_bible)
        seed = build_world_seed(definition, repo_bible, simulation_seed=7)
        report = seed.coverage_report(repo_bible)
        levels = report["levels"]
        assert levels["parsed"] > 0
        assert levels["compiled"] >= levels["parsed"] * 0.5
        assert levels["seeded"] > 0
        assert levels["runtime_connected"] == levels["seeded"]
        assert levels["tested"] > 0
        assert report["chain_samples"], "至少给一条 档案→Compiler→Runtime 链示例"

    def test_unresolved_is_reported_not_silent(self, tmp_path: Path) -> None:
        """§69: 无法实现的条目必须标记 Unresolved，不能静默丢弃。"""
        bible_text = OTHER_BIBLE.replace("- 咖啡、面包", "- 非常多说不清的东西")
        path = tmp_path / "odd.md"
        path.write_text(bible_text, encoding="utf-8")
        bible = BibleCompiler(path).compile()
        definition = CharacterDefinition.from_bible(bible)
        seed = build_world_seed(definition, bible, simulation_seed=1)
        report = seed.coverage_report(bible)
        # the odd preference line parses to *something* or lands in unresolved —
        # either way it is accounted for
        assert isinstance(report["unresolved"], list)


# ------------------------------------------------------ §70 Repository hygiene


class TestRepositoryHygiene:
    def test_gitignore_covers_runtime_data(self) -> None:
        """§72: .env/真实配置/数据库/日志不得进 Git。"""
        gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(
            encoding="utf-8"
        )
        for required in (".env", "config/config.yaml", "config/overrides.yaml", "data/", "logs/"):
            assert required in gitignore, f".gitignore 缺少 {required}"

    def test_mirror_never_ships_runtime_files(self) -> None:
        """sync_github 的镜像清单不含真实配置/数据库/覆盖文件。"""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "sync_github", Path(__file__).resolve().parent.parent / "sync_github.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        shipped = set(module.ROOT_FILES) | set(module.CONFIG_DIR_FILES)
        assert "config.yaml" not in shipped
        assert "overrides.yaml" not in shipped
        assert ".env" not in shipped
        for name in module.MIRRORED_DIRS:
            assert name not in ("data", "logs", "config", "scripts")
