"""Model Router: model selection, automatic failover, cooldown, retries.

The router owns runtime model state (cooldowns, failure counters). Callers
just ``await router.chat(request)`` and get a response — model switching is
invisible to them.

Error policy (see spec v0.2 §13):

* switchable  -> RateLimit / ServerError / Timeout / Connection / ModelNotFound / Unknown
* fail-fast   -> Authentication / InvalidRequest  (retrying another model of the
                 same provider cannot help when the key or request is broken)
* per model   -> at most ONE retry, and only for transient network errors;
                 a 429 never retries the same model, it moves on immediately.

Concurrency: single asyncio.Lock guards cooldown check-and-set; state updates
happen only in this module, so concurrent requests cannot corrupt model state.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass
from typing import Any

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
    UnknownAIError,
)
from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider

SwitchableError = (
    RateLimitError,
    ServerError,
    AITimeoutError,
    AIConnectionError,
    ModelNotFoundError,
    UnknownAIError,
    EmptyResponseError,
)


@dataclass
class ModelSpec:
    """Static description of a model, straight from configuration."""

    name: str
    provider: str
    model: str
    enabled: bool = True


@dataclass
class ModelState:
    """Runtime state of one model — dashboard-friendly snapshot data."""

    spec: ModelSpec
    enabled: bool = True
    cooldown_until: float = 0.0
    last_error: str | None = None
    last_success: float | None = None
    failure_count: int = 0

    def in_cooldown(self, now: float) -> bool:
        return self.cooldown_until > now

    def snapshot(self, now: float | None = None) -> dict[str, Any]:
        return {
            "name": self.spec.name,
            "provider": self.spec.provider,
            "model": self.spec.model,
            "enabled": self.enabled,
            "in_cooldown": self.in_cooldown(time.monotonic() if now is None else now),
            "cooldown_until": self.cooldown_until,
            "last_error": self.last_error,
            "last_success": self.last_success,
            "failure_count": self.failure_count,
        }


@dataclass
class RouterAttempt:
    """One attempt summary, used for error reporting."""

    model_name: str
    error: str


class ModelRouter:
    def __init__(
        self,
        specs: list[ModelSpec],
        providers: dict[str, AIProvider],
        *,
        rate_limit_cooldown: float = 30.0,
        server_error_cooldown: float = 10.0,
        retry_backoff_seconds: float = 0.5,
        retry_backoff_max_seconds: float = 4.0,
        clock: Any = time.monotonic,
        logger: logging.Logger | None = None,
        event_listener: Any = None,
        usage: Any = None,
        sleep: Any = None,
        monotonic: Any = None,
        rng: Any = None,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.AI.Router")
        self._providers = providers
        self._rate_limit_cooldown = rate_limit_cooldown
        self._server_error_cooldown = server_error_cooldown
        self._backoff_base = max(0.0, retry_backoff_seconds)
        self._backoff_max = max(0.0, retry_backoff_max_seconds)
        self._clock = clock  # injectable for deterministic cooldown tests
        self._lock = asyncio.Lock()
        self._event_listener = event_listener  # optional (event, model_name) sink
        self._usage = usage  # optional UsageRecorder (per-call tokens/latency)
        self._sleep = sleep or asyncio.sleep  # injectable: tests assert the delays
        self._monotonic = monotonic or time.perf_counter  # latency measurement
        self._rng = rng  # injectable jitter source; None = module random
        self.states: dict[str, ModelState] = {}
        for spec in specs:
            if spec.provider not in providers:
                self._log.error(
                    "Model '%s' references unknown provider '%s' — disabled (known providers: %s)",
                    spec.name,
                    spec.provider,
                    sorted(providers) or "none — no provider had an API key",
                )
                continue
            self.states[spec.name] = ModelState(spec=spec, enabled=spec.enabled)
        if not self.states:
            # An empty router is normal when AI is not configured; a warning
            # is only warranted when models existed but none are usable.
            level = logging.WARNING if specs else logging.DEBUG
            self._log.log(level, "ModelRouter has no usable models")

    # ------------------------------------------------------------- accessors

    @property
    def model_count(self) -> int:
        return len(self.states)

    def snapshot(self) -> list[dict[str, Any]]:
        now = self._clock()
        return [state.snapshot(now) for state in self.states.values()]

    def _notify(self, event: str, model_name: str | None = None) -> None:
        if self._event_listener is not None:
            try:
                self._event_listener(event, model_name)
            except Exception:  # noqa: BLE001 - metrics must never break routing
                self._log.exception("Router event listener failed")

    # -------------------------------------------------- hot management (WebUI)

    def set_enabled(self, model_name: str, enabled: bool) -> bool:
        state = self.states.get(model_name)
        if state is None:
            return False
        state.enabled = enabled
        return True

    def update_spec(
        self, model_name: str, *, provider: str | None = None, model: str | None = None
    ) -> bool:
        """Point an existing model slot at a different provider/model id."""
        state = self.states.get(model_name)
        if state is None:
            return False
        if provider is not None and provider not in self._providers:
            return False
        state.spec = ModelSpec(
            name=state.spec.name,
            provider=provider or state.spec.provider,
            model=model or state.spec.model,
            enabled=state.enabled,
        )
        return True

    def reorder(self, ordered_names: list[str]) -> bool:
        """Apply a new priority order (missing names keep relative order)."""
        if set(ordered_names) != set(self.states):
            return False
        reordered: dict[str, ModelState] = {}
        for name in ordered_names:
            reordered[name] = self.states[name]
        for name, state in self.states.items():
            reordered.setdefault(name, state)
        self.states = reordered
        return True

    # ----------------------------------------------------------------- chat

    async def chat(self, request: AIRequest) -> AIResponse:
        """Route the request across candidate models with automatic failover."""
        pinned = self._resolve_pinned(request.model)
        attempts: list[RouterAttempt] = []

        for state in self._candidates(pinned):
            if attempts:
                self._log.info("Fallback -> %s", state.spec.name)
                self._notify("fallback", state.spec.name)
            outcome = await self._try_model(state, request)
            if isinstance(outcome, AIResponse):
                return outcome
            if isinstance(outcome, (AuthenticationError, InvalidRequestError)):
                # Broken key/request: no other model can succeed. Abort.
                self._notify("failed", state.spec.name)
                raise outcome
            attempts.append(RouterAttempt(state.spec.name, str(outcome)))

        if not attempts:
            raise AIError("No AI models are configured or available")
        self._notify("failed", None)
        raise AllModelsFailedError([f"{a.model_name}: {a.error}" for a in attempts])

    def _resolve_pinned(self, model_name: str | None) -> ModelState | None:
        """``request.model`` may pin a specific configured model by its name.

        A pin is explicit configuration, so a disabled model fails the request
        with a clear reason instead of being called (or silently swapped for
        another model, which would hide the mistake).
        """
        if model_name is None:
            return None
        state = self.states.get(model_name)
        if state is None:
            raise ModelNotFoundError(
                "<router>", model=model_name, detail="no such configured model"
            )
        if not state.enabled:
            raise ModelNotFoundError(
                "<router>",
                model=model_name,
                detail="model is disabled — enable it or clear the pinned model",
            )
        return state

    def _candidates(self, pinned: ModelState | None) -> list[ModelState]:
        now = self._clock()
        if pinned is not None:
            pool = [pinned]
        else:
            pool = [s for s in self.states.values() if s.enabled]
        return [s for s in pool if not s.in_cooldown(now)]

    # ------------------------------------------------------------ execution

    async def _try_model(self, state: ModelState, request: AIRequest) -> AIResponse | AIError:
        """Run one model (with its single allowed retry). Returns response or final error."""
        spec = state.spec
        provider = self._providers[spec.provider]
        retried_transient = False
        attempt = 0

        while True:
            attempt += 1
            self._log.info("Requesting model=%s (%s)", spec.name, spec.model)
            self._notify("request", spec.name)
            model_request = request.with_model(spec.model)
            started = self._monotonic()
            try:
                response = await provider.chat(model_request)
            except SwitchableError as exc:
                state.last_error = str(exc)
                state.failure_count += 1
                await self._record_usage(spec, request, attempt, started, ok=False, error=exc)

                # Reasoning models that stop because the completion budget ran
                # out (finish=length) with no visible text are a distinct, counted
                # failure mode — the operator can watch it and tune the budget.
                if isinstance(exc, EmptyResponseError) and exc.finish_reason == "length":
                    self._notify("empty_finish_length", spec.name)

                transient = (AITimeoutError, AIConnectionError, EmptyResponseError)
                if isinstance(exc, transient) and not retried_transient:
                    # One retry for transient trouble (network blips, a
                    # reasoning-only empty stop), spaced out so a struggling
                    # endpoint is not hammered — then move to the next model.
                    retried_transient = True
                    delay = self._backoff_delay(attempt)
                    self._log.warning(
                        "Transient error on model=%s: %s — retrying in %.2fs",
                        spec.name,
                        exc,
                        delay,
                    )
                    await self._sleep(delay)
                    continue

                if isinstance(exc, RateLimitError):
                    self._notify("rate_limited", spec.name)
                await self._apply_cooldown(state, exc)
                self._log.warning(
                    "Model %s failed (%s): %s", spec.name, exc.__class__.__name__, exc
                )
                return exc

            except AuthenticationError as exc:
                # Key/permission is broken: other models on this provider will fail too.
                state.last_error = str(exc)
                state.failure_count += 1
                await self._record_usage(spec, request, attempt, started, ok=False, error=exc)
                self._log.error("Aborting AI request: %s", exc)
                return exc

            except InvalidRequestError as exc:
                # The request itself is malformed: switching models will not fix it.
                state.last_error = str(exc)
                state.failure_count += 1
                await self._record_usage(spec, request, attempt, started, ok=False, error=exc)
                self._log.error("Aborting AI request: %s", exc)
                return exc

            except AIError as exc:  # pragma: no cover - defensive
                state.last_error = str(exc)
                state.failure_count += 1
                await self._record_usage(spec, request, attempt, started, ok=False, error=exc)
                self._log.warning("Model %s failed: %s", spec.name, exc)
                return exc

            await self._record_usage(spec, request, attempt, started, ok=True, response=response)
            state.last_success = self._clock()
            state.last_error = None
            self._log.info("Success model=%s", spec.name)
            self._notify("success", spec.name)
            return response

    def _backoff_delay(self, attempt: int) -> float:
        """Exponential backoff with equal jitter, capped (transport-level only).

        Half the window is fixed so the pause never collapses to zero; the
        jitter keeps several concurrent retries from waking up together.
        """
        if self._backoff_base <= 0:
            return 0.0
        ceiling = min(self._backoff_max, self._backoff_base * (2 ** (attempt - 1)))
        jitter = (self._rng or random).uniform(0.0, ceiling / 2)
        return ceiling / 2 + jitter

    async def _record_usage(
        self,
        spec: ModelSpec,
        request: AIRequest,
        attempt: int,
        started: float,
        *,
        ok: bool,
        response: AIResponse | None = None,
        error: AIError | None = None,
    ) -> None:
        if self._usage is None:
            return
        await self._usage.record(
            provider=spec.provider,
            model=spec.model,
            latency_ms=(self._monotonic() - started) * 1000,
            ok=ok,
            usage=response.usage if response is not None else {},
            error_type=error.__class__.__name__ if error is not None else "",
            attempt=attempt,
            purpose=str(request.metadata.get("purpose", "") or ""),
        )

    async def _apply_cooldown(self, state: ModelState, exc: AIError) -> None:
        if isinstance(exc, RateLimitError):
            seconds = self._rate_limit_cooldown
        elif isinstance(exc, (ServerError, ModelNotFoundError)):
            seconds = self._server_error_cooldown
        else:
            return  # timeout / connection / unknown: no cooldown, just move on
        async with self._lock:
            state.cooldown_until = self._clock() + seconds
        self._log.info("Cooling down model=%s for %.0fs", state.spec.name, seconds)
