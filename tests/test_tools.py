"""Builtin tools + tool decision loop + end-to-end chat integration (spec §92-§98).

The AI layer is mocked, so "did the model call the right tool" is deterministic:
the mock returns either a JSON decision or a plain reply.
"""

from __future__ import annotations

import json

import pytest

from app.ai.engine import AIEngine
from app.ai.models import AIRequest, AIResponse
from app.config.settings import AIConfig, AppConfig, ToolsConfig
from app.tools.builtins import CalculatorTool, TimeTool, WeatherTool, WebSearchTool
from app.tools.models import ToolCall
from app.tools.policy import TurnBudget
from app.tools.registry import ToolRegistry
from app.tools.router import ToolOrchestrator, ToolRouter
from app.tools.runtime import ToolRuntime
from tests.ai_mocks import MockAIProvider
from tests.conftest import make_bot, private_event
from tests.test_tool_runtime import EchoTool, make_context, make_db


async def run_tool(tmp_path, tool, arguments: dict):
    """Execute a tool through the executor — raises inside a tool become results."""
    from app.tools.executor import ToolExecutor
    from app.tools.policy import ToolPolicy

    database = make_db(tmp_path)
    await database.connect()
    config = ToolsConfig(enabled=True, max_retries=0)
    registry = ToolRegistry()
    registry.register(tool)
    executor = ToolExecutor(config, registry, ToolPolicy(config), database)
    result = await executor.execute(
        ToolCall(name=tool.metadata.name, arguments=arguments), make_context(), TurnBudget()
    )
    await database.close()
    return result


def weather_payload(location: str = "Singapore", rain: int = 70) -> dict:
    return {
        "location": location,
        "current": {"temperature_c": 29, "humidity": 81, "wind_kph": 12, "condition": "多云"},
        "forecast": [
            {
                "date": "2026-09-30",
                "condition": "阵雨",
                "high_c": 31,
                "low_c": 26,
                "precipitation_probability": rain,
            }
        ],
    }


# --------------------------------------------------------------- builtin tools


class TestTimeTool:
    async def test_returns_current_time(self) -> None:
        result = await TimeTool().execute({}, make_context())
        assert result.success
        assert result.data["timezone"] == "Asia/Singapore"
        assert result.data["weekday"].startswith("星期")

    async def test_explicit_timezone(self) -> None:
        result = await TimeTool().execute({"timezone": "UTC"}, make_context())
        assert result.success and result.data["timezone"] == "UTC"

    async def test_unknown_timezone_falls_back(self) -> None:
        result = await TimeTool().execute({"timezone": "Mars/Olympus"}, make_context())
        assert result.success and result.data["timezone"] == "Asia/Singapore"

    async def test_cache_ttl_declared(self) -> None:
        assert TimeTool().metadata.cache_ttl_seconds > 0


class TestCalculatorTool:
    async def test_multiplication(self) -> None:
        result = await CalculatorTool().execute(
            {"operation": "evaluate", "expression": "12893 * 473"}, make_context()
        )
        assert result.success
        assert result.data["result"] == 12893 * 473
        assert "6098" in result.summary  # exact, not a hallucinated number

    async def test_percentage(self) -> None:
        result = await CalculatorTool().execute(
            {"operation": "percent", "value": 200, "percent": 15}, make_context()
        )
        assert result.success and result.data["result"] == 30

    async def test_unit_conversion(self) -> None:
        result = await CalculatorTool().execute(
            {"operation": "convert", "amount": 5, "from_unit": "km", "to_unit": "mile"},
            make_context(),
        )
        assert result.success
        assert round(result.data["result"], 3) == round(5 / 1.609344, 3)

    async def test_temperature_conversion(self) -> None:
        result = await CalculatorTool().execute(
            {"operation": "convert", "amount": 100, "from_unit": "c", "to_unit": "f"},
            make_context(),
        )
        assert result.success and result.data["result"] == 212

    async def test_dimension_mismatch_rejected(self, tmp_path) -> None:
        result = await run_tool(tmp_path, CalculatorTool(), {
            "operation": "convert", "amount": 5, "from_unit": "km", "to_unit": "kg",
        })
        assert not result.success and result.error_type == "invalid_arguments"

    async def test_code_injection_is_rejected(self, tmp_path) -> None:
        """A calculator must never become a code runner (§23/§106)."""
        for expression in ("__import__('os').system('echo hi')", "open('x')", "1 if True else 2"):
            result = await run_tool(
                tmp_path, CalculatorTool(),
                {"operation": "evaluate", "expression": expression},
            )
            assert not result.success, expression

    async def test_division_by_zero_is_an_error_not_a_crash(self, tmp_path) -> None:
        result = await run_tool(
            tmp_path, CalculatorTool(), {"operation": "evaluate", "expression": "1 / 0"}
        )
        assert not result.success and result.error_type == "invalid_arguments"


class FakeWeatherProvider:
    name = "fake"

    def __init__(self, payload: dict | None = None, fail: bool = False) -> None:
        self.payload = payload or weather_payload()
        self.fail = fail
        self.calls = 0

    async def fetch(self, location: str, *, days: int = 3) -> dict:
        self.calls += 1
        if self.fail:
            from app.tools.errors import ExternalServiceError

            raise ExternalServiceError("upstream 503")
        return {**self.payload, "location": location}

    async def close(self) -> None:
        return None


class TestWeatherTool:
    async def test_normalized_output(self) -> None:
        provider = FakeWeatherProvider()
        tool = WeatherTool(providers=[provider])
        result = await tool.execute({"location": "Singapore", "days": 2}, make_context())
        assert result.success
        assert "新加坡" not in result.summary  # provider returns the raw name
        assert "29" in result.summary and "降水概率 70%" in result.summary
        assert result.metadata["source_type"] == "external"

    async def test_provider_failover(self) -> None:
        broken = FakeWeatherProvider(fail=True)
        healthy = FakeWeatherProvider()
        tool = WeatherTool(providers=[broken, healthy])
        result = await tool.execute({"location": "Singapore"}, make_context())
        assert result.success  # fell over to the second provider (§108)
        assert broken.calls == 1 and healthy.calls == 1

    async def test_all_providers_down_reports_failure(self) -> None:
        tool = WeatherTool(providers=[FakeWeatherProvider(fail=True)])
        result = await tool.execute({"location": "Singapore"}, make_context())
        assert not result.success
        assert "不要编造" in result.to_prompt_block()

    async def test_missing_location_asks_instead_of_guessing(self) -> None:
        tool = WeatherTool(providers=[FakeWeatherProvider()])
        result = await tool.execute({}, make_context())
        assert not result.success and result.error_type == "invalid_arguments"


class FakeSearchProvider:
    name = "fake"

    def __init__(self, results: list[dict] | None = None, configured: bool = True) -> None:
        self.results = results if results is not None else [
            {
                "title": "标题一", "url": "https://a", "snippet": "摘要一",
                "source": "a", "published_at": "",
            },
            {
                "title": "标题二", "url": "https://b", "snippet": "摘要二",
                "source": "b", "published_at": "",
            },
        ]
        self._configured = configured

    @property
    def configured(self) -> bool:
        return self._configured

    async def search(self, query: str, *, limit: int = 5) -> list[dict]:
        return self.results[:limit]

    async def close(self) -> None:
        return None


class TestWebSearchTool:
    async def test_results_are_structured(self) -> None:
        tool = WebSearchTool(providers=[FakeSearchProvider()])
        result = await tool.execute({"query": "最近的新闻"}, make_context())
        assert result.success
        assert result.data["results"][0]["title"] == "标题一"
        assert "来源" in result.summary and result.metadata["source_type"] == "external"

    async def test_result_count_is_capped(self) -> None:
        many = [
            {
                "title": f"t{i}", "url": f"https://{i}", "snippet": "s",
                "source": "x", "published_at": "",
            }
            for i in range(20)
        ]
        tool = WebSearchTool(providers=[FakeSearchProvider(many)])
        result = await tool.execute({"query": "x", "max_results": 3}, make_context())
        assert len(result.data["results"]) == 3

    async def test_unconfigured_provider_does_not_invent(self) -> None:
        tool = WebSearchTool(providers=[FakeSearchProvider(results=[], configured=False)])
        result = await tool.execute({"query": "新闻"}, make_context())
        assert not result.success
        assert "不要编造" in result.to_prompt_block()

    async def test_empty_results_are_honest(self) -> None:
        tool = WebSearchTool(providers=[FakeSearchProvider(results=[])])
        result = await tool.execute({"query": "没有结果的话题"}, make_context())
        assert result.success
        assert result.metadata.get("insufficient") is True
        assert "没有找到" in result.summary


class TestSearchProviders:
    def test_null_provider_is_not_configured(self) -> None:
        from app.tools.providers.search import create_search_provider

        provider = create_search_provider("null", {})
        assert provider.configured is False

    async def test_tavily_without_key_is_an_auth_error(self) -> None:
        from app.tools.errors import ToolAuthenticationError
        from app.tools.providers.search import create_search_provider

        provider = create_search_provider("tavily", {"api_key_env": "MISSING_KEY"}, None)
        with pytest.raises(ToolAuthenticationError):
            await provider.search("x")


# ------------------------------------------------------------- decision loop


class TestToolRouter:
    def test_parse_json_decision(self) -> None:
        router = ToolRouter(ToolsConfig(enabled=True), ToolRegistry())
        call = router.parse_decision(
            json.dumps({"tool_call": {"name": "weather", "arguments": {"location": "Singapore"}}})
        )
        assert call is not None
        assert call.name == "weather" and call.arguments["location"] == "Singapore"

    def test_parse_fenced_json(self) -> None:
        router = ToolRouter(ToolsConfig(enabled=True), ToolRegistry())
        call = router.parse_decision(
            '```json\n{"tool_call": {"name": "time", "arguments": {}}}\n```'
        )
        assert call is not None and call.name == "time"

    def test_plain_reply_is_not_a_call(self) -> None:
        router = ToolRouter(ToolsConfig(enabled=True), ToolRegistry())
        assert router.parse_decision("今天天气不错呀，你那边呢？") is None

    def test_unknown_tool_name_still_parses_then_fails_later(self) -> None:
        router = ToolRouter(ToolsConfig(enabled=True), ToolRegistry())
        call = router.parse_decision('{"tool_call": {"name": "ghost", "arguments": {}}}')
        assert call is not None and call.name == "ghost"

    def test_instruction_only_contains_candidates(self) -> None:
        registry = ToolRegistry()
        registry.register(EchoTool())
        registry.register(TimeTool())
        router = ToolRouter(ToolsConfig(enabled=True, candidate_tools=1), registry)
        candidates = router.candidates("现在几点")
        instruction = router.build_instruction(candidates)
        assert "time" in instruction
        assert "echo" not in instruction  # never the whole registry (§12)


class ScriptedProvider(MockAIProvider):
    """Returns queued replies so a decision loop can be exercised deterministically."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(behaviors={"A": replies})

    async def chat__(self, request: AIRequest) -> AIResponse:  # pragma: no cover
        raise NotImplementedError


async def make_orchestrator(tmp_path, tools, replies, max_calls: int = 3):
    from app.tools.executor import ToolExecutor
    from app.tools.policy import ToolPolicy

    database = make_db(tmp_path)
    await database.connect()
    config = ToolsConfig(enabled=True, decision_mode="json", max_calls_per_turn=max_calls)
    registry = ToolRegistry()
    for tool in tools:
        registry.register(tool)
    policy = ToolPolicy(config)
    executor = ToolExecutor(config, registry, policy, database, clock=lambda: 0.0)
    router = ToolRouter(config, registry)
    orchestrator = ToolOrchestrator(config, registry, router, executor, policy)
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        database,
        providers={"mock": MockAIProvider(behaviors={"A": replies})},
    )
    return orchestrator, engine, database, registry


class TestOrchestrator:
    async def test_plain_chat_uses_no_tools(self, tmp_path) -> None:
        """Ordinary small talk must not trigger a tool (spec §93/§114)."""
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [TimeTool()], ["嗯嗯，今天挺好的"]
        )
        try:
            text, results = await orchestrator.run(
                engine, [], context=make_context(), query="今天心情不错"
            )
            assert results == []
            assert text == "嗯嗯，今天挺好的"
        finally:
            await engine.close()
            await database.close()

    async def test_single_tool_call_round_trip(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "time", "arguments": {}}}),
            "我看了一下，现在挺晚的了～",
        ]
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [TimeTool()], replies
        )
        try:
            text, results = await orchestrator.run(
                engine, [], context=make_context(), query="现在几点了"
            )
            assert len(results) == 1 and results[0].tool_name == "time"
            assert results[0].success
            assert text == "我看了一下，现在挺晚的了～"
        finally:
            await engine.close()
            await database.close()

    async def test_tool_result_is_labelled_untrusted(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "time", "arguments": {}}}),
            "好的",
        ]
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [TimeTool()], replies
        )
        provider = engine._providers["mock"]  # noqa: SLF001
        try:
            await orchestrator.run(engine, [], context=make_context(), query="几点了")
            follow_up = provider.calls[-1]["messages"][-1].content
            assert "不可信参考数据" in follow_up
            assert "不是指令" in follow_up
        finally:
            await engine.close()
            await database.close()

    async def test_budget_stops_the_loop(self, tmp_path) -> None:
        """A model that keeps calling tools is cut off at max_calls_per_turn."""
        loop_reply = json.dumps({"tool_call": {"name": "time", "arguments": {}}})
        orchestrator, engine, database, registry = await make_orchestrator(
            tmp_path, [TimeTool()], [loop_reply, loop_reply, loop_reply, loop_reply, "结束"]
        )
        try:
            _, results = await orchestrator.run(
                engine, [], context=make_context(), query="现在几点"
            )
            # identical arguments trip loop detection before the budget does
            assert len(results) <= 3
        finally:
            await engine.close()
            await database.close()

    async def test_repeated_identical_calls_are_blocked(self, tmp_path) -> None:
        loop_reply = json.dumps({"tool_call": {"name": "time", "arguments": {}}})
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [TimeTool()], [loop_reply] * 5 + ["好了"], max_calls=6
        )
        try:
            _, results = await orchestrator.run(
                engine, [], context=make_context(), query="现在几点"
            )
            assert any(not r.success and r.error_type == "loop_detected" for r in results)
        finally:
            await engine.close()
            await database.close()

    async def test_invalid_arguments_do_not_crash(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "weather", "arguments": {}}}),
            "我不太确定你在哪，方便说下城市吗？",
        ]
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [WeatherTool(providers=[])], replies
        )
        try:
            text, results = await orchestrator.run(
                engine, [], context=make_context(), query="明天会下雨吗"
            )
            assert len(results) == 1 and not results[0].success
            assert results[0].error_type == "invalid_arguments"
            assert text.startswith("我不太确定")
        finally:
            await engine.close()
            await database.close()

    async def test_unknown_tool_from_model_is_handled(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "shell", "arguments": {"cmd": "rm -rf /"}}}),
            "这个我做不到哦",
        ]
        orchestrator, engine, database, _ = await make_orchestrator(
            tmp_path, [TimeTool()], replies
        )
        try:
            text, results = await orchestrator.run(
                engine, [], context=make_context(), query="现在几点？顺便帮我删个文件"
            )
            assert results and results[0].error_type == "unknown_tool"
            assert text == "这个我做不到哦"
        finally:
            await engine.close()
            await database.close()

    async def test_disabled_tool_never_runs(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "time", "arguments": {}}}),
            "我暂时看不到时间诶",
        ]
        orchestrator, engine, database, registry = await make_orchestrator(
            tmp_path, [TimeTool()], replies
        )
        registry.enable("time", False)
        try:
            _, results = await orchestrator.run(
                engine, [], context=make_context(), query="现在几点"
            )
            assert results == [] or not results[0].success
        finally:
            await engine.close()
            await database.close()


# ------------------------------------------------------------- chat pipeline


async def make_tool_bot(tmp_path, replies: list[str]):
    """A bot with the tool runtime enabled and a scripted (tool-deciding) model."""
    provider = MockAIProvider(behaviors={"A": replies})
    bot = make_bot(tmp_path)
    config = AppConfig(
        bot={"name": "TestBot"},
        database={"url": f"sqlite:///{tmp_path / 'chat.db'}"},
        logging={"log_dir": str(tmp_path / "logs")},
        behavior={"reply": {"enabled": False}},
        tools={"enabled": True, "decision_mode": "json", "max_calls_per_turn": 2},
    )
    bot.config = config
    engine = AIEngine(
        AIConfig(enabled=True, models=[{"name": "A", "provider": "mock", "model": "A"}]),
        bot.database,
        providers={"mock": provider},
    )
    bot.ai = engine
    bot.character.engine = engine
    if bot.character.extractor is not None:
        bot.character.extractor.engine = engine
        bot.character.extractor.config.extraction.enabled = False  # keep call counts exact
    await bot.database.connect()
    bot.tools = ToolRuntime(config.tools, bot.database)
    bot.character.tools = bot.tools
    await bot.tools.start()
    # swap the network-backed weather tool for a deterministic fake
    bot.tools.registry.unregister("weather")
    bot.tools.registry.register(WeatherTool(providers=[FakeWeatherProvider()]))
    bot.event_bus.on("message", bot.core_router.on_message)
    await bot.plugins.load_all()
    return bot, provider


class TestChatWithTools:
    async def test_weather_question_calls_the_tool(self, tmp_path) -> None:
        replies = [
            json.dumps(
                {"tool_call": {"name": "weather", "arguments": {"location": "Singapore"}}}
            ),
            "看了一下，明天大概率有阵雨，出门带把伞吧。",
        ]
        bot, provider = await make_tool_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event("明天新加坡会不会下雨？", user_id=7))
            texts = bot.adapter.sent_texts()  # type: ignore[attr-defined]
            assert texts == ["看了一下，明天大概率有阵雨，出门带把伞吧。"]
            # the tool ran for real (fake provider) and the model saw its result
            assert provider.calls[-1]["messages"][-1].content.find("不可信参考数据") >= 0
        finally:
            await bot.shutdown()

    async def test_tool_usage_is_invisible_to_the_user(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "calculator", "arguments": {
                "operation": "evaluate", "expression": "23891 * 731"}}}),
            "算好了，是 17464321。",
        ]
        bot, _ = await make_tool_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event("23891 × 731 是多少？", user_id=7))
            text = bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            assert "17464321" in text  # the calculator's answer
            lowered = text.lower()
            assert "tool" not in lowered and "provider" not in lowered
            assert "{" not in text and "}" not in text  # no raw JSON leaking
        finally:
            await bot.shutdown()

    async def test_plain_chat_does_not_call_tools(self, tmp_path) -> None:
        bot, provider = await make_tool_bot(tmp_path, ["哈哈，今天确实挺舒服的"])
        try:
            await bot.event_bus.emit(private_event("今天心情不错", user_id=7))
            assert bot.adapter.sent_texts() == ["哈哈，今天确实挺舒服的"]  # type: ignore[attr-defined]
            assert len(provider.calls) == 1  # exactly one model call, no tool round
        finally:
            await bot.shutdown()

    async def test_disabled_tool_means_honest_answer(self, tmp_path) -> None:
        replies = ["我这会儿查不到天气诶，回头再看看？"]
        bot, _ = await make_tool_bot(tmp_path, replies)
        try:
            await bot.tools.set_tool_enabled("weather", False)
            await bot.event_bus.emit(private_event("明天会下雨吗", user_id=7))
            text = bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            assert "查不到" in text
            assert "℃" not in text and "度" not in text  # never fabricates a forecast
        finally:
            await bot.shutdown()

    async def test_tool_failure_does_not_break_chat(self, tmp_path) -> None:
        replies = [
            json.dumps({"tool_call": {"name": "weather", "arguments": {"location": "X"}}}),
            "我刚刚查了一下，不过这次没拿到结果……",
        ]
        bot, _ = await make_tool_bot(tmp_path, replies)
        bot.tools.registry.unregister("weather")
        bot.tools.registry.register(WeatherTool(providers=[FakeWeatherProvider(fail=True)]))
        try:
            await bot.event_bus.emit(private_event("明天会下雨吗", user_id=7))
            assert "没拿到结果" in bot.adapter.sent_texts()[-1]  # type: ignore[attr-defined]
            await bot.event_bus.emit(private_event("还在吗", user_id=7))
            assert len(bot.adapter.sent_texts()) == 2  # type: ignore[attr-defined]
        finally:
            await bot.shutdown()

    async def test_tool_results_are_not_written_to_memory(self, tmp_path) -> None:
        """Weather output must never become a long-term fact (spec §29/§61)."""
        replies = [
            json.dumps({"tool_call": {"name": "weather", "arguments": {"location": "Singapore"}}}),
            "明天有阵雨的样子。",
        ]
        bot, _ = await make_tool_bot(tmp_path, replies)
        try:
            await bot.event_bus.emit(private_event("明天新加坡天气怎么样", user_id=7))
            if bot.memory is not None:
                memories = await bot.memory.list_memories()
                assert all("29" not in m.content for m in memories)
        finally:
            await bot.shutdown()
