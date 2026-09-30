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
import time
from dataclasses import dataclass
from typing import Any

from app.ai.errors import (
    AIConnectionError,
    AIError,
    AITimeoutError,
    AllModelsFailedError,
    AuthenticationError,
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
        clock: Any = time.monotonic,
        logger: logging.Logger | None = None,
        event_listener: Any = None,
    ) -> None:
        self._log = logger or logging.getLogger("CatooBot.AI.Router")
        self._providers = providers
        self._rate_limit_cooldown = rate_limit_cooldown
        self._server_error_cooldown = server_error_cooldown
        self._clock = clock  # injectable for deterministic cooldown tests
        self._lock = asyncio.Lock()
        self._event_listener = event_listener  # optional (event, model_name) sink
        self.states: dict[str, ModelState] = {}
        for spec in specs:
            if spec.provider not in providers:
                self._log.warning(
                    "Model '%s' references unknown provider '%s' — disabled",
                    spec.name,
                    spec.provider,
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
        """``request.model`` may pin a specific configured model by its name."""
        if model_name is None:
            return None
        state = self.states.get(model_name)
        if state is None:
            raise ModelNotFoundError(
                "<router>", model=model_name, detail="no such configured model"
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

        while True:
            self._log.info("Requesting model=%s (%s)", spec.name, spec.model)
            self._notify("request", spec.name)
            model_request = request.with_model(spec.model)
            try:
                response = await provider.chat(model_request)
            except SwitchableError as exc:
                state.last_error = str(exc)
                state.failure_count += 1

                if isinstance(exc, (AITimeoutError, AIConnectionError)) and not retried_transient:
                    # One short retry for transient network trouble, then move on.
                    retried_transient = True
                    self._log.warning(
                        "Transient error on model=%s: %s — retrying once", spec.name, exc
                    )
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
                self._log.error("Aborting AI request: %s", exc)
                return exc

            except InvalidRequestError as exc:
                # The request itself is malformed: switching models will not fix it.
                state.last_error = str(exc)
                state.failure_count += 1
                self._log.error("Aborting AI request: %s", exc)
                return exc

            except AIError as exc:  # pragma: no cover - defensive
                state.last_error = str(exc)
                state.failure_count += 1
                self._log.warning("Model %s failed: %s", spec.name, exc)
                return exc

            state.last_success = self._clock()
            state.last_error = None
            self._log.info("Success model=%s", spec.name)
            self._notify("success", spec.name)
            return response

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
