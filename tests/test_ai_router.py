"""ModelRouter tests: selection, failover, cooldown, error policy, concurrency.

All tests use MockAIProvider with a controllable clock — no real API access.
"""

from __future__ import annotations

import asyncio
import logging

import pytest

from app.ai.errors import (
    AIConnectionError,
    AIError,
    AITimeoutError,
    AllModelsFailedError,
    AuthenticationError,
    EmptyResponseError,
    InvalidRequestError,
    ModelNotFoundError,
    RateLimitError,
    ServerError,
)
from app.ai.models import AIRequest, ChatMessage
from app.ai.router import ModelRouter, ModelSpec
from tests.ai_mocks import MockAIProvider


class FakeClock:
    """Deterministic monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_router(
    behaviors: dict[str, list],
    model_names: list[str] | None = None,
    clock: FakeClock | None = None,
    rate_limit_cooldown: float = 30.0,
    server_error_cooldown: float = 10.0,
) -> tuple[ModelRouter, MockAIProvider, FakeClock]:
    clock = clock or FakeClock()
    model_names = model_names or list(behaviors)
    provider = MockAIProvider(behaviors=behaviors)
    specs = [ModelSpec(name=n, provider="mock", model=n) for n in model_names]
    router = ModelRouter(
        specs,
        {"mock": provider},
        rate_limit_cooldown=rate_limit_cooldown,
        server_error_cooldown=server_error_cooldown,
        clock=clock,
        logger=logging.getLogger("test.router"),
    )
    return router, provider, clock


def request(text: str = "hello", model: str | None = None) -> AIRequest:
    return AIRequest(messages=[ChatMessage.user(text)], model=model)


class TestNormalFlow:
    async def test_first_model_success(self) -> None:
        router, provider, _ = make_router({"A": ["ok!"]})
        response = await router.chat(request("hi"))
        assert response.content == "ok!"
        assert response.model == "A"
        assert provider.call_count() == 1

    async def test_success_updates_state(self) -> None:
        router, _, clock = make_router({"A": ["ok"]})
        await router.chat(request())
        state = router.states["A"]
        assert state.last_error is None
        assert state.last_success == clock.now
        assert state.failure_count == 0


class TestFailover:
    async def test_429_falls_back(self) -> None:
        router, provider, _ = make_router(
            {"A": [RateLimitError("mock", "A")], "B": ["from B"]},
            model_names=["A", "B"],
        )
        response = await router.chat(request())
        assert response.content == "from B"
        assert response.model == "B"
        assert provider.call_count("A") == 1
        assert provider.call_count("B") == 1

    async def test_429_then_500_then_success(self) -> None:
        router, _, _ = make_router(
            {
                "A": [RateLimitError("mock", "A")],
                "B": [ServerError("mock", "B", 500)],
                "C": ["from C"],
            },
            model_names=["A", "B", "C"],
        )
        response = await router.chat(request())
        assert response.content == "from C"

    async def test_timeout_retries_once_then_falls_back(self) -> None:
        router, provider, _ = make_router(
            {"A": [AITimeoutError("mock", "A"), AITimeoutError("mock", "A")], "B": ["from B"]},
            model_names=["A", "B"],
        )
        response = await router.chat(request())
        assert response.content == "from B"
        assert provider.call_count("A") == 2  # one retry allowed

    async def test_connection_error_retries_once(self) -> None:
        router, provider, _ = make_router(
            {"A": [AIConnectionError("mock"), "recovered"]},
            model_names=["A"],
        )
        response = await router.chat(request())
        assert response.content == "recovered"
        assert provider.call_count("A") == 2

    async def test_all_fail_raises_unified_error(self) -> None:
        router, provider, _ = make_router(
            {
                "A": [RateLimitError("mock", "A")],
                "B": [ServerError("mock", "B", 500)],
                "C": [AITimeoutError("mock", "C")],
            },
            model_names=["A", "B", "C"],
        )
        with pytest.raises(AllModelsFailedError) as excinfo:
            await router.chat(request())
        assert "A" in str(excinfo.value)
        assert "C" in str(excinfo.value)
        # A: 1 attempt, B: 1, C: 2 (one transient retry allowed)
        assert provider.call_count() == 4


class TestFailFast:
    async def test_auth_error_aborts_all(self) -> None:
        router, provider, _ = make_router(
            {"A": [AuthenticationError("mock")], "B": ["never"]},
            model_names=["A", "B"],
        )
        with pytest.raises(AuthenticationError):
            await router.chat(request())
        assert provider.call_count("B") == 0  # same provider: no point retrying

    async def test_invalid_request_aborts_all(self) -> None:
        router, provider, _ = make_router(
            {"A": [InvalidRequestError("mock", "bad prompt")], "B": ["never"]},
            model_names=["A", "B"],
        )
        with pytest.raises(InvalidRequestError):
            await router.chat(request())
        assert provider.call_count("B") == 0


class TestCooldown:
    async def test_rate_limited_model_skipped(self) -> None:
        clock = FakeClock()
        router, provider, clock = make_router(
            {"A": [RateLimitError("mock", "A")], "B": ["B-1", "B-2"]},
            model_names=["A", "B"],
            clock=clock,
            rate_limit_cooldown=30.0,
        )
        await router.chat(request())  # A fails -> B answers, A in cooldown
        assert provider.call_count("A") == 1

        response = await router.chat(request())  # within cooldown: A skipped
        assert response.content == "B-2"
        assert provider.call_count("A") == 1

        clock.advance(31.0)  # cooldown over: A is selected again
        await router.chat(request())
        assert provider.call_count("A") == 2

    async def test_server_error_cooldown_shorter(self) -> None:
        clock = FakeClock()
        router, provider, clock = make_router(
            {"A": [ServerError("mock", "A", 500)], "B": ["B-1", "B-2"]},
            model_names=["A", "B"],
            clock=clock,
            server_error_cooldown=10.0,
        )
        await router.chat(request())
        assert provider.call_count("A") == 1

        clock.advance(11.0)
        await router.chat(request())
        assert provider.call_count("A") == 2

    async def test_cooldown_state_exposed(self) -> None:
        router, _, clock = make_router(
            {"A": [RateLimitError("mock", "A")], "B": ["ok"]},
            model_names=["A", "B"],
        )
        await router.chat(request())
        snapshots = {s["name"]: s for s in router.snapshot()}
        assert snapshots["A"]["in_cooldown"] is True
        assert snapshots["A"]["failure_count"] == 1
        assert snapshots["A"]["last_error"] is not None
        assert snapshots["A"]["enabled"] is True
        assert snapshots["B"]["in_cooldown"] is False
        clock.advance(31.0)
        assert snapshots["A"]["in_cooldown"] is True  # snapshot is static


class TestSelection:
    async def test_disabled_model_never_selected(self) -> None:
        provider = MockAIProvider(behaviors={"A": ["should not run"]})
        specs = [
            ModelSpec(name="A", provider="mock", model="A", enabled=False),
            ModelSpec(name="B", provider="mock", model="B"),
        ]
        router = ModelRouter(specs, {"mock": provider}, clock=FakeClock())
        response = await router.chat(request())
        assert response.model == "B"

    async def test_unknown_provider_disabled(self) -> None:
        specs = [ModelSpec(name="X", provider="ghost", model="X")]
        router = ModelRouter(specs, {"mock": MockAIProvider()}, clock=FakeClock())
        assert router.model_count == 0
        with pytest.raises(AIError):
            await router.chat(request())

    async def test_pinned_model(self) -> None:
        router, provider, _ = make_router({"A": ["a"], "B": ["b"]}, model_names=["A", "B"])
        response = await router.chat(request(model="B"))
        assert response.content == "b"
        assert provider.call_count("A") == 0

    async def test_pinned_unknown_model(self) -> None:
        router, _, _ = make_router({"A": ["a"]})
        with pytest.raises(ModelNotFoundError):
            await router.chat(request(model="ghost"))

    async def test_pinned_disabled_model_is_not_used(self) -> None:
        """A pin is explicit config, but a disabled model must never run.

        Before: ``_candidates`` short-circuited to ``[pinned]`` without the
        enabled filter, so pinning a disabled model still called it.
        """
        provider = MockAIProvider(behaviors={"A": ["should not run"], "B": ["b"]})
        specs = [
            ModelSpec(name="A", provider="mock", model="A", enabled=False),
            ModelSpec(name="B", provider="mock", model="B"),
        ]
        router = ModelRouter(specs, {"mock": provider}, clock=FakeClock())
        with pytest.raises(ModelNotFoundError):
            await router.chat(request(model="A"))
        assert provider.call_count("A") == 0  # the disabled model was never called


class TestConcurrency:
    async def test_concurrent_requests_share_failover_safely(self) -> None:
        """Many simultaneous requests against a 429-ing model: the first one
        trips the cooldown, the rest skip A immediately, nothing corrupts."""
        behaviors = {
            "A": [RateLimitError("mock", "A") for _ in range(50)],
            "B": ["ok"] * 50,
        }
        router, provider, _ = make_router(behaviors, model_names=["A", "B"])
        responses = await asyncio.gather(*(router.chat(request(f"q{i}")) for i in range(20)))
        assert all(r.content == "ok" for r in responses)
        assert provider.call_count("A") == 1  # cooldown stopped the hammering
        assert provider.call_count("B") == 20
        state = router.states["A"]
        assert state.failure_count == 1
        assert state.in_cooldown(router._clock())

    async def test_cooldown_prevents_repeat_hammering(self) -> None:
        """After A cools down, subsequent concurrent requests skip A entirely."""
        behaviors = {
            "A": [RateLimitError("mock", "A")],
            "B": ["ok"] * 30,
        }
        router, provider, _ = make_router(behaviors, model_names=["A", "B"])
        first = await router.chat(request())  # A 429 -> cooldown
        assert first.content == "ok"
        await asyncio.gather(*(router.chat(request(f"q{i}")) for i in range(10)))
        assert provider.call_count("A") == 1  # never hammered while cooling down


class TestEmptyResponsePolicy:
    """A reasoning-only empty stop: retry the same model once, then fail over."""

    async def test_retries_same_model_once_then_succeeds(self) -> None:
        router, provider, _ = make_router({"A": [EmptyResponseError("mock", "A"), "这下有了"]})
        response = await router.chat(request())
        assert response.content == "这下有了"
        assert provider.call_count("A") == 2

    async def test_fails_over_when_still_empty(self) -> None:
        router, provider, _ = make_router(
            {
                "A": [EmptyResponseError("mock", "A")],
                "B": ["备用模型的回答"],
            },
            ["A", "B"],
        )
        response = await router.chat(request())
        assert response.content == "备用模型的回答"
        assert provider.call_count("A") == 2  # one retry, then move on

    async def test_all_models_empty_raises(self) -> None:
        router, _provider, _ = make_router(
            {
                "A": [EmptyResponseError("mock", "A")],
                "B": [EmptyResponseError("mock", "B")],
            },
            ["A", "B"],
        )
        with pytest.raises(AllModelsFailedError):
            await router.chat(request())
