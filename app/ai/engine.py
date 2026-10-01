"""AIEngine: the single entry point for AI features.

    ChatPlugin / commands ──► AIEngine ──► ModelRouter ──► AIProvider(s)

Business code only ever sees ``await ai.chat(...)`` / ``await ai.chat_in_session(...)``
— provider choice, model failover and cooldowns are invisible from here.
System prompts are composed at this level (personality / memory hooks plug in
here in later versions).
"""

from __future__ import annotations

import asyncio
import os
from typing import TYPE_CHECKING, Any

from app.ai.context import ConversationManager
from app.ai.errors import AIError
from app.ai.models import AIRequest, AIResponse, ChatMessage
from app.ai.provider import AIProvider, create_provider
from app.ai.providers import OpenAICompatibleProvider  # noqa: F401 - registers provider types
from app.ai.router import ModelRouter, ModelSpec
from app.config.settings import AIConfig
from app.utils.logger import get_logger

if TYPE_CHECKING:
    from app.database.database import Database


class AIEngine:
    def __init__(
        self,
        config: AIConfig,
        database: Database | None = None,
        providers: dict[str, AIProvider] | None = None,
        router_event_listener: Any = None,
        usage: Any = None,
    ) -> None:
        """``providers`` overrides config-driven construction (used by tests
        to inject mocks without touching the network)."""
        self.config = config
        self.log = get_logger("AI")
        self._router_event_listener = router_event_listener
        self._usage = usage  # optional UsageRecorder (per-call tokens/latency)
        self._providers: dict[str, AIProvider] = {}
        self._owned_providers = providers is None  # close only what we built

        if providers is not None:
            self._providers = dict(providers)
        elif config.enabled:
            self._providers = self._build_providers(config)

        self.router = ModelRouter(
            specs=[
                ModelSpec(
                    name=m.name,
                    provider=m.provider,
                    model=m.model,
                    enabled=m.enabled,
                )
                for m in config.models
            ],
            providers=self._providers,
            rate_limit_cooldown=config.cooldown.rate_limit_seconds,
            server_error_cooldown=config.cooldown.server_error_seconds,
            retry_backoff_seconds=config.cooldown.retry_backoff_seconds,
            retry_backoff_max_seconds=config.cooldown.retry_backoff_max_seconds,
            event_listener=router_event_listener,
            usage=usage,
        )
        self.conversations = ConversationManager(
            config.context, database, logger=get_logger("AI.Context")
        )

        usable = self.router.model_count
        if config.enabled and usable == 0:
            self.log.warning("AI is enabled but no usable model/provider was configured")
        self.enabled = bool(config.enabled and usable > 0)
        self.log.info(
            "AI engine initialized: enabled=%s models=%d providers=%d",
            self.enabled,
            usable,
            len(self._providers),
        )

    # ------------------------------------------------------------- building

    @staticmethod
    def _build_providers(config: AIConfig) -> dict[str, AIProvider]:
        """Instantiate providers from config; keys come from the environment.

        Providers that share a ``base_url`` share one concurrency limit, so a
        burst of chat + vision + planner calls cannot flood the same endpoint.
        """
        log = get_logger("AI")
        providers: dict[str, AIProvider] = {}
        gates: dict[str, asyncio.Semaphore] = {}
        limit = config.concurrency.max_parallel_per_provider
        for name, pcfg in config.providers.items():
            api_key = os.environ.get(pcfg.api_key_env, "") if pcfg.api_key_env else ""
            if not api_key:
                log.warning(
                    "Provider '%s' has no API key: environment variable '%s' is empty — disabled",
                    name,
                    pcfg.api_key_env or "<none configured>",
                )
                continue
            gate = gates.setdefault(pcfg.base_url, asyncio.Semaphore(limit))
            try:
                providers[name] = create_provider(
                    pcfg.type,
                    name=name,
                    base_url=pcfg.base_url,
                    api_key=api_key,
                    timeout=config.timeout,
                    semaphore=gate,
                )
            except ValueError as exc:
                log.error("Provider '%s' skipped: %s", name, exc)
        return providers

    async def reconfigure(self, config: AIConfig) -> dict[str, Any]:
        """Apply a new AI config in place (WebUI model/provider editing).

        Rebuilds providers and the model router while keeping this engine
        object — everything else in the process holds *this* reference, so no
        re-wiring is needed. Returns a small report for the operator.
        """
        before_models = sorted(self.router.states)
        previous = dict(self._providers)
        self.config = config

        if self._owned_providers:
            new_providers = self._build_providers(config)
        else:
            # Tests inject providers; keep them and add anything newly configured.
            new_providers = dict(previous)
            new_providers.update(self._build_providers(config))

        self.router = ModelRouter(
            specs=[
                ModelSpec(
                    name=m.name,
                    provider=m.provider,
                    model=m.model,
                    enabled=m.enabled,
                )
                for m in config.models
            ],
            providers=new_providers,
            rate_limit_cooldown=config.cooldown.rate_limit_seconds,
            server_error_cooldown=config.cooldown.server_error_seconds,
            retry_backoff_seconds=config.cooldown.retry_backoff_seconds,
            retry_backoff_max_seconds=config.cooldown.retry_backoff_max_seconds,
            event_listener=self._router_event_listener,
            usage=self._usage,
        )
        dropped = [name for name in previous if name not in new_providers]
        self._providers = new_providers
        self.enabled = bool(config.enabled and self.router.model_count > 0)

        if self._owned_providers:
            for name in dropped:
                closer = getattr(previous[name], "close", None)
                if closer is None:
                    continue
                try:
                    await closer()
                except Exception:  # noqa: BLE001 - a stuck client must not block the save
                    self.log.warning("Provider '%s' failed to close cleanly", name)

        models_now = sorted(self.router.states)
        report: dict[str, Any] = {
            "enabled": self.enabled,
            "models": models_now,
            "models_added": sorted(set(models_now) - set(before_models)),
            "models_removed": sorted(set(before_models) - set(models_now)),
            "providers": sorted(new_providers),
            "providers_dropped": dropped,
        }
        self.log.info(
            "[AI] reconfigured: enabled=%s models=%s providers=%s",
            report["enabled"],
            ", ".join(models_now) or "-",
            ", ".join(sorted(new_providers)) or "-",
        )
        return report

    # ------------------------------------------------------------ main API

    def build_messages(
        self,
        history: list[ChatMessage],
        user_text: str,
        *,
        system_extra: str | None = None,
    ) -> list[ChatMessage]:
        """Compose the full prompt: system prompt (+extras) + history + user."""
        system_text = self.config.system_prompt.strip()
        extra = system_extra.strip() if system_extra else ""
        if extra:
            system_text = f"{system_text}\n\n{extra}" if system_text else extra
        messages: list[ChatMessage] = []
        if system_text:
            messages.append(ChatMessage.system(system_text))
        messages.extend(history)
        messages.append(ChatMessage.user(user_text))
        return messages

    async def chat(self, request: AIRequest) -> AIResponse:
        """Raw routed chat (no session handling). Raises AIError on failure."""
        return await self.router.chat(request)

    async def chat_in_session(
        self,
        session_id: str,
        user_text: str,
        *,
        system_extra: str | None = None,
    ) -> AIResponse:
        """Session-aware chat: context in, both turns recorded on success.

        The user turn is only persisted after a successful response, so a
        failed request never leaves an unanswered turn in the history.
        """
        history = await self.conversations.get_context(session_id)
        messages = self.build_messages(history, user_text, system_extra=system_extra)
        request = AIRequest(messages=messages, temperature=self.config.default_temperature)

        self.log.info(
            "Chat session=%s prompt_messages=%d history=%d", session_id, len(messages), len(history)
        )
        response = await self.router.chat(request)

        if response.content.strip():
            await self.conversations.append_user_message(session_id, user_text)
            await self.conversations.append_assistant_message(session_id, response.content)
        else:
            self.log.warning("Empty response for session=%s — context not updated", session_id)
        return response

    async def clear_session(self, session_id: str) -> None:
        await self.conversations.clear_context(session_id)

    # ------------------------------------------------------------ lifecycle

    async def close(self) -> None:
        for provider in self._providers.values():
            try:
                await provider.close()
            except Exception:  # noqa: BLE001
                self.log.exception("Provider '%s' failed to close", getattr(provider, "name", "?"))
        if self._owned_providers:
            self._providers.clear()


__all__ = ["AIEngine", "AIError"]
