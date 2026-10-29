"""Model concurrency, retry backoff and usage logging (Task 15).

Three transport-level safeguards, each with a deterministic test:

* one concurrency limit per ``base_url`` (shared by every model on it) so a
  burst of chat + vision + planner calls cannot flood one endpoint;
* transient failures retry with exponential backoff + equal jitter instead of
  hammering the same model immediately;
* every attempt is recorded (tokens, latency, outcome, purpose) and pruned by
  retention — recording failures never break a model call.
"""

from __future__ import annotations

import asyncio
import os
import random

import httpx
import pytest

from app.ai.engine import AIEngine
from app.ai.errors import AITimeoutError, RateLimitError
from app.ai.models import AIRequest, AIResponse, ChatMessage
from app.ai.router import ModelRouter, ModelSpec
from app.ai.usage import UsageRecorder
from app.config.settings import AIConfig, DatabaseConfig
from app.database.database import Database
from tests.ai_mocks import MockAIProvider

USAGE = {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
OK_BODY = {
    "choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
    "usage": USAGE,
}


def request(text: str = "hello", *, purpose: str = "") -> AIRequest:
    metadata = {"purpose": purpose} if purpose else {}
    return AIRequest(messages=[ChatMessage.user(text)], metadata=metadata)


def resolved(text: str = "hello") -> AIRequest:
    """A request the way the router hands it to a provider (model resolved)."""
    return AIRequest(messages=[ChatMessage.user(text)], model="mock-model")


def reply(content: str = "hi", *, usage: dict | None = None):  # type: ignore[type-arg,no-untyped-def]
    """A MockAIProvider script item that returns a real AIResponse."""

    def _make(_request: AIRequest) -> AIResponse:
        return AIResponse(content=content, model="A", provider="mock", usage=usage or {})

    return _make


class RecordingSleep:
    """Deterministic stand-in for asyncio.sleep (records every delay)."""

    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


class FakeMetrics:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def inc(self, key: str, amount: int = 1) -> None:
        self.calls.append(key)


def make_router(script: list, **kwargs: object):  # type: ignore[type-arg,no-untyped-def]
    """One model ('A') plus a healthy 'B', so failover stays available."""
    provider = MockAIProvider(behaviors={"A": script, "B": [reply("b")]})
    specs = [
        ModelSpec(name="A", provider="mock", model="A"),
        ModelSpec(name="B", provider="mock", model="B"),
    ]
    return ModelRouter(specs, {"mock": provider}, clock=lambda: 1000.0, **kwargs)


class TestRetryBackoff:
    async def test_transient_retry_waits_with_jitter(self) -> None:
        sleep = RecordingSleep()
        router = make_router(
            [AITimeoutError("mock", model="A"), reply("ok")],
            retry_backoff_seconds=0.5,
            retry_backoff_max_seconds=4.0,
            sleep=sleep,
            rng=random.Random(7),
        )
        response = await router._try_model(router.states["A"], request())
        assert response.content == "ok"
        assert len(sleep.delays) == 1
        assert 0.25 <= sleep.delays[0] <= 0.5  # half window + jitter

    async def test_backoff_grows_exponentially_then_caps(self) -> None:
        router = make_router([], retry_backoff_seconds=0.5, retry_backoff_max_seconds=4.0)
        router._rng = random.Random(0)
        delays = [router._backoff_delay(attempt) for attempt in (1, 2, 3, 4, 5, 6)]
        assert delays[0] < delays[1] < delays[2] < delays[3]
        assert all(delay <= 4.0 for delay in delays)
        assert router._backoff_delay(3) > 0.0  # never collapses to zero

    async def test_disabled_backoff_never_waits(self) -> None:
        sleep = RecordingSleep()
        router = make_router(
            [AITimeoutError("mock", model="A"), reply("ok")],
            retry_backoff_seconds=0.0,
            sleep=sleep,
        )
        await router._try_model(router.states["A"], request())
        assert sleep.delays == [0.0]

    async def test_second_transient_failure_fails_over(self) -> None:
        sleep = RecordingSleep()
        router = make_router(
            [AITimeoutError("mock", model="A")],  # the script repeats on every call
            retry_backoff_seconds=0.5,
            sleep=sleep,
        )
        error = await router._try_model(router.states["A"], request())
        assert isinstance(error, AITimeoutError)
        assert len(sleep.delays) == 1  # one retry, not an endless loop


class TestUsageRecording:
    @staticmethod
    async def _recorder(tmp_path, *, clock=None):  # type: ignore[no-untyped-def]
        db = Database(DatabaseConfig(url=f"sqlite:///{tmp_path / 'usage.db'}"))
        await db.connect()
        return UsageRecorder(db, clock=clock or (lambda: 1_700_000_000.0)), db

    async def test_success_writes_tokens_latency_and_purpose(self, tmp_path) -> None:
        recorder, db = await self._recorder(tmp_path)
        try:
            router = make_router([reply("ok", usage=USAGE)], usage=recorder)
            await router._try_model(router.states["A"], request(purpose="vision"))
            row = await db.fetchone("SELECT * FROM ai_usage")
            assert row is not None
            assert (row["provider"], row["model"], row["purpose"]) == ("mock", "A", "vision")
            assert (row["prompt_tokens"], row["completion_tokens"]) == (11, 7)
            assert row["ok"] == 1 and row["attempt"] == 1
            assert row["latency_ms"] >= 0.0
        finally:
            await db.close()

    async def test_failure_is_recorded_with_its_error_type(self, tmp_path) -> None:
        recorder, db = await self._recorder(tmp_path)
        try:
            router = make_router([RateLimitError("mock", model="A")], usage=recorder)
            await router._try_model(router.states["A"], request())
            row = await db.fetchone("SELECT * FROM ai_usage")
            assert row is not None and row["ok"] == 0
            assert row["error_type"] == "RateLimitError"
        finally:
            await db.close()

    async def test_retry_records_both_attempts(self, tmp_path) -> None:
        recorder, db = await self._recorder(tmp_path)
        try:
            router = make_router(
                [AITimeoutError("mock", model="A"), reply("ok")],
                usage=recorder,
                retry_backoff_seconds=0.0,
                sleep=RecordingSleep(),
            )
            await router._try_model(router.states["A"], request())
            rows = await db.fetchall("SELECT attempt, ok FROM ai_usage ORDER BY attempt")
            assert [(row["attempt"], row["ok"]) for row in rows] == [(1, 0), (2, 1)]
        finally:
            await db.close()

    async def test_recording_trouble_never_breaks_the_call(self, tmp_path) -> None:
        recorder, db = await self._recorder(tmp_path)
        await db.close()  # the usage sink is broken
        metrics = FakeMetrics()
        recorder._metrics = metrics
        router = make_router([reply("hi", usage=USAGE)], usage=recorder)
        response = await router._try_model(router.states["A"], request())
        assert response.content == "hi"  # the model call still succeeded
        assert metrics.calls == ["ai_usage_failed"]

    async def test_summary_and_prune(self, tmp_path) -> None:
        now = [1_700_000_000.0]
        recorder, db = await self._recorder(tmp_path, clock=lambda: now[0])
        try:
            await recorder.record(
                provider="mock", model="A", latency_ms=100.0, ok=True, usage=USAGE
            )
            now[0] += 60
            await recorder.record(
                provider="mock", model="A", latency_ms=300.0, ok=False, error_type="RateLimitError"
            )
            summary = await recorder.summary(days=7)
            assert summary[0]["model"] == "A"
            assert summary[0]["calls"] == 2 and summary[0]["failures"] == 1
            assert summary[0]["prompt_tokens"] == 11
            assert summary[0]["avg_latency_ms"] == pytest.approx(200.0, abs=0.5)

            await db.execute("UPDATE ai_usage SET ts = ts - 40 * 86400 WHERE id = 1")
            assert await recorder.prune(retention_days=30) == 1
            assert [entry["calls"] for entry in await recorder.summary(days=90)] == [1]
        finally:
            await db.close()


class TestProviderConcurrency:
    @staticmethod
    def make_provider(*, limit: int):  # type: ignore[no-untyped-def]
        from app.ai.providers import OpenAICompatibleProvider

        transport = httpx.MockTransport(lambda _request: httpx.Response(200, json=OK_BODY))
        return OpenAICompatibleProvider(
            name="mock",
            base_url="https://example.invalid/v1",
            api_key="k",
            transport=transport,
            semaphore=asyncio.Semaphore(limit),
        )

    async def test_a_closed_gate_blocks_the_call(self) -> None:
        """Semaphore(0) can never be acquired — proving the call passes the gate."""
        provider = self.make_provider(limit=0)
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(provider.chat(resolved()), timeout=0.05)

    async def test_an_open_gate_lets_the_call_through(self) -> None:
        provider = self.make_provider(limit=1)
        response = await provider.chat(resolved())
        assert response.content == "hi"

    async def test_providers_sharing_a_base_url_share_the_gate(self) -> None:
        config = AIConfig(
            enabled=True,
            concurrency={"max_parallel_per_provider": 3},
            providers={
                "p1": {"base_url": "https://a.example/v1", "api_key_env": "USAGE_TEST_KEY"},
                "p2": {"base_url": "https://a.example/v1", "api_key_env": "USAGE_TEST_KEY"},
                "p3": {"base_url": "https://b.example/v1", "api_key_env": "USAGE_TEST_KEY"},
            },
        )
        os.environ["USAGE_TEST_KEY"] = "key"
        try:
            providers = AIEngine._build_providers(config)
        finally:
            os.environ.pop("USAGE_TEST_KEY", None)
        assert set(providers) == {"p1", "p2", "p3"}
        gates = {name: provider._semaphore for name, provider in providers.items()}
        assert gates["p1"] is gates["p2"]
        assert gates["p3"] is not gates["p1"]
        assert gates["p1"]._value == 3


class TestBotWiring:
    async def test_bot_records_usage(self, tmp_path) -> None:
        from tests.conftest import make_ready_bot

        bot = await make_ready_bot(tmp_path)
        try:
            assert bot.ai_usage is not None
            assert bot.ai.router._usage is bot.ai_usage
            await bot.ai_usage.record(provider="mock", model="A", latency_ms=5.0, ok=True)
            assert (await bot.ai_usage.summary(days=1))[0]["calls"] == 1
        finally:
            await bot.shutdown()
