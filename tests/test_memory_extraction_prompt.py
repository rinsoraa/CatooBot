"""记忆提取提示词（v0.8 /prompts → v1 AI · 提示词）必须真正生效。

旧版页面把 `memory_extraction_prompt` 存进 ``settings.prompt_overrides``，
但提取器一直用内置 `EXTRACTION_PROMPT` —— 选项存了却没人读。现在：
* 保存即生效（`AdminService.save_prompts` 推给提取器）；
* 启动时从库里读回（`Bot._apply_prompt_overrides`）；
* 空值 = 回到内置提示词，不留"半个覆盖"。
"""

from __future__ import annotations

from app.config.settings import MemoryConfig
from app.memory.extraction import EXTRACTION_PROMPT, MemoryExtractor
from tests.conftest import make_bot


def make_extractor() -> MemoryExtractor:
    """Prompt-only extractor: these tests never call the model."""
    return MemoryExtractor(MemoryConfig(), engine=None, manager=None)  # type: ignore[arg-type]


class TestExtractionPromptOverride:
    def test_empty_override_falls_back_to_the_builtin_prompt(self) -> None:
        extractor = make_extractor()
        assert extractor.system_prompt() == EXTRACTION_PROMPT
        extractor.set_system_prompt("")
        assert extractor.system_prompt() == EXTRACTION_PROMPT
        extractor.set_system_prompt("   ")
        assert extractor.system_prompt() == EXTRACTION_PROMPT

    def test_custom_override_replaces_the_builtin_prompt(self) -> None:
        extractor = make_extractor()
        extractor.set_system_prompt("只记住用户明确说过的偏好。")
        assert extractor.system_prompt() == "只记住用户明确说过的偏好。"
        assert EXTRACTION_PROMPT not in extractor.system_prompt()  # 替换而非拼接

    async def test_save_prompts_applies_immediately(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        from app.web.services.admin import AdminService

        bot = make_bot(tmp_path)
        await bot.database.connect()
        try:
            assert bot.extractor is not None
            service = AdminService(bot)
            await service.save_prompts({"memory_extraction_prompt": "提取要点，别的都不要。"})
            assert bot.extractor.system_prompt() == "提取要点，别的都不要。"

            prompts = await service.get_prompts()
            assert prompts["memory_extraction_prompt"] == "提取要点，别的都不要。"

            await service.save_prompts({"memory_extraction_prompt": ""})
            assert bot.extractor.system_prompt() == EXTRACTION_PROMPT
        finally:
            await bot.shutdown()

    async def test_startup_reads_the_stored_override(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot = make_bot(tmp_path)
        await bot.database.connect()
        try:
            await bot.database.set_setting_json(
                "prompt_overrides", {"memory_extraction_prompt": "启动时读回来的提示词。"}, 0
            )
            assert bot.extractor is not None
            bot.extractor.set_system_prompt("")  # 模拟刚构造出来的状态
            await bot._apply_prompt_overrides()  # noqa: SLF001 - 启动路径本身
            assert bot.extractor.system_prompt() == "启动时读回来的提示词。"
        finally:
            await bot.shutdown()

    async def test_a_setting_without_the_key_is_treated_as_no_override(self, tmp_path) -> None:  # type: ignore[no-untyped-def]
        bot = make_bot(tmp_path)
        await bot.database.connect()
        try:
            await bot.database.set_setting_json("prompt_overrides", {"other": 1}, 0)
            assert bot.extractor is not None
            await bot._apply_prompt_overrides()  # noqa: SLF001
            assert bot.extractor.system_prompt() == EXTRACTION_PROMPT
        finally:
            await bot.shutdown()
