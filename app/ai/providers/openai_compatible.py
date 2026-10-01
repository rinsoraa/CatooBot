"""OpenAI-compatible chat-completions provider.

Works with every service exposing ``POST {base_url}/chat/completions`` with
``Authorization: Bearer <key>`` — SiliconFlow, DeepSeek, GLM, Kimi, MiniMax,
OpenAI itself, local runners, etc. Adding a platform = adding YAML config.

The API key is injected via the ``Authorization`` header and is never logged;
exception messages only carry provider/model names and server-provided error
text (which contains no credentials).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.ai.errors import (
    AIConnectionError,
    AITimeoutError,
    AuthenticationError,
    EmptyResponseError,
    InvalidRequestError,
    ModelNotFoundError,
    RateLimitError,
    ServerError,
    UnknownAIError,
)
from app.ai.models import AIRequest, AIResponse
from app.ai.provider import AIProvider, register_provider_type

PROVIDER_TYPE = "openai_compatible"

# HTTP status -> error class for statuses we classify without inspecting the body
_STATUS_ERRORS: dict[int, Any] = {
    400: InvalidRequestError,
    401: AuthenticationError,
    403: AuthenticationError,
    404: None,  # body inspection decides: model_not_found vs bad endpoint
    422: InvalidRequestError,
    429: RateLimitError,
}


class OpenAICompatibleProvider(AIProvider):
    def __init__(
        self,
        name: str,
        base_url: str,
        api_key: str,
        timeout: float = 60.0,
        connect_timeout: float = 10.0,
        transport: httpx.AsyncBaseTransport | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self.name = name
        self._log = logger or logging.getLogger("CatooBot.AI.Provider")
        self._client = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=httpx.Timeout(timeout, connect=connect_timeout),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    async def chat(self, request: AIRequest) -> AIResponse:
        assert request.model, "AIRequest.model must be resolved before calling a provider"
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [m.to_openai() for m in request.messages],
            "stream": request.stream,
        }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if request.max_tokens is not None:
            payload["max_tokens"] = request.max_tokens
        if request.tools:
            from app.tools.schema import tool_json_schema

            payload["tools"] = [tool_json_schema(tool.metadata) for tool in request.tools]
            payload["tool_choice"] = "auto"

        self._log.info(
            "Sending request provider=%s model=%s (%d messages)",
            self.name, request.model, len(request.messages),
        )
        try:
            response = await self._client.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise AITimeoutError(self.name, model=request.model, detail=str(exc)) from exc
        except httpx.TransportError as exc:
            raise AIConnectionError(self.name, detail=exc.__class__.__name__) from exc

        if response.status_code != 200:
            raise self._http_error(response.status_code, request.model, response)

        return self._parse_success(request.model, response)

    # --------------------------------------------------------------- parsing

    def _parse_success(self, model: str, response: httpx.Response) -> AIResponse:
        try:
            data: dict[str, Any] = response.json()
        except ValueError as exc:
            raise UnknownAIError(self.name, detail="response is not valid JSON") from exc

        choices = data.get("choices") or []
        if not choices:
            raise UnknownAIError(self.name, detail="response contains no choices")

        message = choices[0].get("message") or {}
        content = str(message.get("content") or "")
        usage = data.get("usage") or {}
        tool_calls = self._parse_tool_calls(message.get("tool_calls"))
        finish_reason = choices[0].get("finish_reason")
        reasoning = str(message.get("reasoning_content") or "")
        self._log.info(
            "Response received provider=%s model=%s finish=%s tokens=%s/%s",
            self.name,
            data.get("model", model),
            finish_reason,
            usage.get("prompt_tokens", "-"),
            usage.get("completion_tokens", "-"),
        )
        if not content.strip() and not tool_calls:
            # Reasoning-only stop (observed: finish=stop, all completion
            # tokens in reasoning_content) — retry/failover instead of
            # feeding an empty reply to the character layer.
            reasoning_chars = len(str(reasoning)) if reasoning else 0
            self._log.warning(
                "Empty content from model=%s (finish=%s, reasoning_chars=%d) —"
                " treating as a transient failure",
                model,
                finish_reason,
                reasoning_chars,
            )
            raise EmptyResponseError(
                self.name,
                model=model,
                finish_reason=str(finish_reason or ""),
                reasoning_chars=reasoning_chars,
            )
        return AIResponse(
            content=content,
            reasoning=reasoning[:2000],
            model=str(data.get("model", model)),
            provider=self.name,
            usage=dict(usage) if isinstance(usage, dict) else {},
            finish_reason=choices[0].get("finish_reason"),
            raw_response=data,
            tool_calls=tool_calls,
        )

    @staticmethod
    def _parse_tool_calls(raw: Any) -> list[dict[str, Any]]:
        """OpenAI native tool_calls -> the runtime's normalized shape."""
        if not isinstance(raw, list):
            return []
        parsed: list[dict[str, Any]] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            function = item.get("function") or {}
            name = function.get("name") or item.get("name")
            if not name:
                continue
            parsed.append(
                {
                    "id": str(item.get("id") or ""),
                    "name": str(name),
                    "arguments": function.get("arguments") or item.get("arguments") or "{}",
                }
            )
        return parsed

    def _http_error(self, status: int, model: str, response: httpx.Response) -> Exception:
        """Map an HTTP error response to the standardized error types."""
        detail = self._error_detail(response)

        if status == 404:
            if "model" in detail.lower():
                return ModelNotFoundError(self.name, model=model, detail=detail)
            return ServerError(self.name, model=model, status=status, detail="endpoint not found")

        error_cls = _STATUS_ERRORS.get(status)
        if error_cls is AuthenticationError:
            return AuthenticationError(self.name, detail=detail)
        if error_cls is RateLimitError:
            return RateLimitError(self.name, model=model, detail=detail)
        if error_cls is InvalidRequestError:
            return InvalidRequestError(self.name, detail=detail)
        if 500 <= status <= 599:
            return ServerError(self.name, model=model, status=status, detail=detail)
        return UnknownAIError(self.name, detail=f"HTTP {status}: {detail}")

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extract a short, credential-free error message from the body."""
        try:
            data = response.json()
        except ValueError:
            return response.text[:200]
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            parts = [str(error.get(k)) for k in ("message", "code", "type") if error.get(k)]
            return " | ".join(parts)[:300] if parts else str(error)[:300]
        return str(data)[:300]

    # ------------------------------------------------------------- lifecycle

    async def close(self) -> None:
        await self._client.aclose()


register_provider_type(PROVIDER_TYPE, OpenAICompatibleProvider)
