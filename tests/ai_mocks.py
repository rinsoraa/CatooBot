"""Mock AI provider for tests — no network involved.

``behaviors`` maps model id -> script (list). Each call consumes the next
item; the last item repeats when the script is exhausted. Script items:

* ``str``                     -> success, returned as content
* ``Exception`` instance      -> raised as-is (use the AI error types)
* ``Callable(AIRequest)``     -> returns AIResponse or raises
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider

ScriptItem = str | Exception | Callable[[AIRequest], Any]


class MockAIProvider(AIProvider):
    def __init__(
        self, name: str = "mock", behaviors: dict[str, list[ScriptItem]] | None = None
    ) -> None:
        self.name = name
        self.behaviors = behaviors or {}
        self.calls: list[dict[str, Any]] = []  # {model, n_messages, last_user}
        self._cursors: dict[str, int] = {}

    def _next(self, model: str, request: AIRequest) -> Any:
        script = self.behaviors.get(model)
        if not script:
            return "ok:" + request.messages[-1].content
        index = min(self._cursors.get(model, 0), len(script) - 1)
        self._cursors[model] = index + 1
        return script[index]

    async def chat(self, request: AIRequest) -> AIResponse:
        assert request.model
        self.calls.append(
            {
                "model": request.model,
                "n_messages": len(request.messages),
                "messages": list(request.messages),
                "last_user": request.messages[-1].content,
                # budget assertions live in the call-site tests (2026-10-05 复盘)
                "max_tokens": request.max_tokens,
            }
        )
        outcome = self._next(request.model, request)
        if isinstance(outcome, Exception):
            raise outcome
        if callable(outcome):
            result = outcome(request)
            if isinstance(result, Exception):
                raise result
            return result
        return AIResponse(content=str(outcome), model=request.model, provider=self.name)

    async def close(self) -> None:
        return None

    def call_count(self, model: str | None = None) -> int:
        if model is None:
            return len(self.calls)
        return sum(1 for c in self.calls if c["model"] == model)
