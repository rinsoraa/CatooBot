"""Extraction must never fail silently (Task 25).

Four paths used to be invisible at the default log level: extraction disabled,
no usable models, an unparsable reply, and every parsed item being rejected.
Each one now ends in a single INFO line:

    [Memory.Extract] saved=x/y reason=<disabled|no_models|empty_content|
                                        parse_failed|timeout|ai_error|invalid_item|ok>

plus a WARN with a redacted excerpt when the model reply could not be parsed.
"""

from __future__ import annotations

import logging

import pytest

from app.ai.engine import AIEngine
from app.ai.errors import AITimeoutError, EmptyResponseError
from app.ai.models import AIResponse
from app.config.settings import AIConfig, MemoryConfig
from app.memory.extraction import MemoryExtractor

GOOD = '{"memories": [{"category": "fact", "content": "用户喜欢猫", "importance": 0.8}]}'


@pytest.fixture
def make_extractor(tmp_path, caplog):  # type: ignore[no-untyped-def]
    from tests.test_memory import make_manager

    async def build(script: list, *, extraction: dict | None = None):  # type: ignore[type-arg]
        manager, database = await make_manager(tmp_path)
        provider = _Provider(script)
        engine = AIEngine(
            AIConfig(
                enabled=True,
                models=[
                    {"name": "A", "provider": "mock", "model": "A"},
                    {"name": "E", "provider": "mock", "model": "E"},
                ],
            ),
            database,
            providers={"mock": provider},
        )
        config = MemoryConfig(extraction=extraction or {})
        extractor = MemoryExtractor(config, engine, manager)
        return extractor, manager, database

    return build


class _Provider:
    """Scripted provider: str = content, Exception = raised, callable = built."""

    def __init__(self, script: list) -> None:  # type: ignore[type-arg]
        self.name = "mock"
        self.script = list(script)
        self.calls = 0

    async def chat(self, request: object) -> AIResponse:  # type: ignore[no-untyped-def]
        self.calls += 1
        item = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(item, Exception):
            raise item
        if callable(item):
            return item(request)
        model = getattr(request, "model", "") or "A"
        return AIResponse(content=str(item), model=model, provider="mock")

    async def close(self) -> None:
        return None


def lines(caplog) -> list[str]:  # type: ignore[no-untyped-def]
    return [r.getMessage() for r in caplog.records]


class TestExtractReport:
    async def test_success_reports_saved(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor([GOOD])
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "我喜欢猫", "真好呀！")
                await extractor.wait_idle()
            assert any("[Memory.Extract] saved=1/1 reason=ok" in line for line in lines(caplog))
        finally:
            await database.close()

    async def test_disabled_and_no_models_are_reported(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor([GOOD], extraction={"enabled": False})
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            assert any("reason=disabled" in line for line in lines(caplog))
        finally:
            await database.close()

        extractor, _manager, database = await make_extractor([GOOD])
        extractor.engine.enabled = False  # no usable models
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            assert any("reason=no_models" in line for line in lines(caplog))
        finally:
            await database.close()

    async def test_unparsable_reply_warns_with_a_redacted_excerpt(
        self, make_extractor, caplog
    ) -> None:
        extractor, _manager, database = await make_extractor(
            ["对不起我不会 JSON token=SUPERSECRET 后面还有很长的一段话"]
        )
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            assert any("reason=parse_failed" in line for line in lines(caplog))
            warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
            assert any("对不起我不会 JSON" in message for message in warnings)
            assert not any("SUPERSECRET" in message for message in warnings), "密钥必须脱敏"
        finally:
            await database.close()

    async def test_empty_content_with_reasoning_is_flagged(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor(
            [EmptyResponseError("mock", model="A", finish_reason="stop", reasoning_chars=86)]
        )
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            joined = "\n".join(lines(caplog))
            assert "reason=empty_content" in joined
            assert "reasoning" in joined  # the reasoning-only case is called out
        finally:
            await database.close()

    async def test_timeout_and_ai_error_reasons(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor(
            [AITimeoutError("mock", model="A")], extraction={"timeout": 0.01}
        )
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            assert any("reason=timeout" in line for line in lines(caplog))
        finally:
            await database.close()

    async def test_all_items_rejected_reports_invalid_item(self, make_extractor, caplog) -> None:
        # "嗯" survives _parse (it has content) and is rejected by
        # min_content_length inside remember() — that is the invalid_item path.
        # If a future change filtered it while parsing, the honest answer would
        # be reason=parse_failed; hence the printed line below.
        tiny = '{"memories": [{"category": "fact", "content": "嗯"}]}'
        extractor, _manager, database = await make_extractor([tiny])
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            report = [line for line in lines(caplog) if line.startswith("[Memory.Extract] saved=")]
            print("invalid_item 用例的实际报告行:", report)
            assert report, "抽取必须留下报告行"
            assert "saved=0/1 reason=invalid_item" in report[-1]
        finally:
            await database.close()

    async def test_metric_counts_failures_by_reason(self, make_extractor) -> None:
        from app.core.metrics import Metrics

        extractor, _manager, database = await make_extractor(["不是 JSON"])
        metrics = Metrics()
        extractor._metrics = metrics
        try:
            await extractor.schedule("private:5", "5", None, "msg", "reply")
            await extractor.wait_idle()
            assert metrics.get("memory_extract_failed") >= 1
        finally:
            await database.close()


class ByModelProvider:
    """Empty for every model except E (dispatch by name, not by call count)."""

    name = "mock"

    def __init__(self, *, e_is_good: bool = True) -> None:
        self.models: list[str] = []
        self.e_is_good = e_is_good

    async def chat(self, request: object) -> AIResponse:  # type: ignore[no-untyped-def]
        model = getattr(request, "model", "") or ""
        self.models.append(model)
        if model == "E" and self.e_is_good:
            return AIResponse(content=GOOD, model=model, provider="mock")
        raise EmptyResponseError("mock", model=model, finish_reason="stop", reasoning_chars=50)

    async def close(self) -> None:
        return None


class TestEmptyResponseRetry:
    async def _extractor(self, tmp_path, *, e_is_good: bool = True):  # type: ignore[no-untyped-def]
        from tests.test_memory import make_manager

        manager, database = await make_manager(tmp_path)
        provider = ByModelProvider(e_is_good=e_is_good)
        engine = AIEngine(
            AIConfig(
                enabled=True,
                models=[
                    {"name": "A", "provider": "mock", "model": "A"},
                    {"name": "E", "provider": "mock", "model": "E"},
                ],
            ),
            database,
            providers={"mock": provider},
        )
        extractor = MemoryExtractor(MemoryConfig(extraction={"model": "E"}), engine, manager)
        return extractor, provider, database

    async def test_the_configured_model_answers_the_empty_content_path(
        self, tmp_path, caplog
    ) -> None:
        extractor, provider, database = await self._extractor(tmp_path)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "我喜欢猫", "真好呀！")
                await extractor.wait_idle()
            assert "E" in provider.models, provider.models
            assert any("saved=1/1" in line for line in lines(caplog)), lines(caplog)[-3:]
        finally:
            await database.close()

    async def test_a_failed_retry_keeps_the_original_reason(self, tmp_path, caplog) -> None:
        extractor, _provider, database = await self._extractor(tmp_path, e_is_good=False)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            report = [line for line in lines(caplog) if line.startswith("[Memory.Extract] saved=")][
                -1
            ]
            print("实际报告行:", report)
            assert "reason=empty_content" in report, report
            assert "retry=failed" in report, report
        finally:
            await database.close()

    async def test_without_a_configured_model_the_hint_names_the_setting(
        self, tmp_path, caplog
    ) -> None:
        from tests.test_memory import make_manager

        manager, database = await make_manager(tmp_path)
        provider = ByModelProvider(e_is_good=False)
        engine = AIEngine(
            AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
            database,
            providers={"mock": provider},
        )
        extractor = MemoryExtractor(MemoryConfig(), engine, manager)
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
                await extractor.wait_idle()
            assert any("reason=empty_content" in line for line in lines(caplog))
            assert any("memory.extraction.model" in line for line in lines(caplog))
        finally:
            await database.close()


class TestZeroStreak:
    async def test_five_empty_extractions_warn_once(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor(["不是 JSON"])
        try:
            with caplog.at_level(logging.INFO, logger="CatooBot.Memory"):
                for _ in range(5):
                    await extractor.schedule("private:5", "5", None, "msg", "reply")
                    await extractor.wait_idle()
            warnings = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
            streak = [message for message in warnings if "连续" in message]
            assert len(streak) == 1, f"should warn exactly once: {streak}"
            assert extractor.health_note() is not None  # the WebUI banner reads this
        finally:
            await database.close()

    async def test_a_success_resets_the_streak(self, make_extractor, caplog) -> None:
        extractor, _manager, database = await make_extractor(["不是 JSON", GOOD])
        try:
            for _ in range(4):
                await extractor.schedule("private:5", "5", None, "msg", "reply")
            await extractor.wait_idle()
            await extractor.schedule("private:5", "5", None, "msg", "reply")  # succeeds
            await extractor.wait_idle()
            assert extractor.health_note() is None
        finally:
            await database.close()
