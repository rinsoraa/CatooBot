"""OpenAICompatibleProvider tests using httpx.MockTransport — no network."""

from __future__ import annotations

import json

import httpx
import pytest

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
from app.ai.models import AIRequest, ChatMessage
from app.ai.providers.openai_compatible import OpenAICompatibleProvider

OK_BODY = {
    "id": "chatcmpl-1",
    "model": "model-a",
    "choices": [
        {
            "index": 0,
            "message": {"role": "assistant", "content": "你好呀！"},
            "finish_reason": "stop",
        }
    ],
    "usage": {"prompt_tokens": 5, "completion_tokens": 7, "total_tokens": 12},
}


def make_provider(handler) -> tuple[OpenAICompatibleProvider, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def logging_handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return handler(request)

    provider = OpenAICompatibleProvider(
        name="testprov",
        base_url="https://api.test/v1",
        api_key="sk-secret-key",
        timeout=5.0,
        transport=httpx.MockTransport(logging_handler),
    )
    return provider, captured


def request(model: str = "model-a") -> AIRequest:
    return AIRequest(
        messages=[ChatMessage.system("sys"), ChatMessage.user("你好")],
        model=model,
        temperature=0.8,
    )


def error_body(message: str, code: str | int | None = None) -> dict:
    error: dict = {"message": message}
    if code is not None:
        error["code"] = code
    return {"error": error}


class TestSuccess:
    async def test_parses_response(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(200, json=OK_BODY))
        response = await provider.chat(request())
        assert response.content == "你好呀！"
        assert response.model == "model-a"
        assert response.provider == "testprov"
        assert response.usage["total_tokens"] == 12
        assert response.finish_reason == "stop"

    async def test_request_shape_and_auth_header(self) -> None:
        provider, captured = make_provider(lambda req: httpx.Response(200, json=OK_BODY))
        await provider.chat(request())
        sent = captured[0]
        assert sent.headers["Authorization"] == "Bearer sk-secret-key"
        assert sent.headers["Content-Type"] == "application/json"
        assert sent.url.path == "/v1/chat/completions"
        body = json.loads(sent.content)
        assert body["model"] == "model-a"
        assert body["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "你好"},
        ]
        assert body["temperature"] == 0.8
        assert body["stream"] is False


class TestErrorMapping:
    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (401, AuthenticationError),
            (403, AuthenticationError),
            (429, RateLimitError),
            (400, InvalidRequestError),
            (422, InvalidRequestError),
            (500, ServerError),
            (502, ServerError),
            (503, ServerError),
        ],
    )
    async def test_status_codes(self, status: int, expected: type[Exception]) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(status, json=error_body("boom")))
        with pytest.raises(expected):
            await provider.chat(request())

    async def test_404_model_not_found(self) -> None:
        provider, _ = make_provider(
            lambda req: httpx.Response(
                404, json=error_body("model not found", code="model_not_found")
            )
        )
        with pytest.raises(ModelNotFoundError):
            await provider.chat(request())

    async def test_404_bad_endpoint_is_server_error(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(404, json=error_body("no such url")))
        with pytest.raises(ServerError):
            await provider.chat(request())

    async def test_error_message_extracted(self) -> None:
        provider, _ = make_provider(
            lambda req: httpx.Response(429, json=error_body("quota exceeded"))
        )
        with pytest.raises(RateLimitError) as excinfo:
            await provider.chat(request())
        assert "quota exceeded" in str(excinfo.value)

    async def test_unknown_status(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(418, json=error_body("teapot")))
        with pytest.raises(UnknownAIError):
            await provider.chat(request())


class TestTransportFailures:
    async def test_timeout(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("timed out")

        provider, _ = make_provider(handler)
        with pytest.raises(AITimeoutError):
            await provider.chat(request())

    async def test_connection_error(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        provider, _ = make_provider(handler)
        with pytest.raises(AIConnectionError):
            await provider.chat(request())


class TestBadResponses:
    async def test_empty_choices(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(200, json={"choices": []}))
        with pytest.raises(UnknownAIError):
            await provider.chat(request())

    async def test_non_json_success(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(200, text="not json"))
        with pytest.raises(UnknownAIError):
            await provider.chat(request())

    async def test_exception_messages_never_contain_key(self) -> None:
        provider, _ = make_provider(
            lambda req: httpx.Response(401, json=error_body("unauthorized"))
        )
        with pytest.raises(Exception) as excinfo:  # noqa: PT011
            await provider.chat(request())
        assert "sk-secret-key" not in str(excinfo.value)


class TestEmptyContent:
    """Reasoning-only stops must become transient errors, not silent empties."""

    def _body(self, message: dict) -> dict:
        return {
            "id": "chatcmpl-2",
            "model": "model-a",
            "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 2280, "completion_tokens": 86},
        }

    async def test_reasoning_only_empty_content_raises(self) -> None:
        provider, _ = make_provider(
            lambda req: httpx.Response(
                200,
                json=self._body(
                    {
                        "role": "assistant",
                        "content": "",
                        "reasoning_content": "（想了很久但是没说话）" * 5,
                    }
                ),
            )
        )
        with pytest.raises(EmptyResponseError) as info:
            await provider.chat(request())
        assert "reasoning_chars" in str(info.value)

    async def test_empty_content_with_tool_calls_is_kept(self) -> None:
        provider, _ = make_provider(
            lambda req: httpx.Response(
                200,
                json=self._body(
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "time", "arguments": "{}"},
                            }
                        ],
                    }
                ),
            )
        )
        response = await provider.chat(request())
        assert response.tool_calls and response.tool_calls[0]["name"] == "time"


class TestReasoningCapture:
    """Reasoning models return their thinking separately — captured, not stored."""

    async def test_reasoning_content_is_captured(self) -> None:
        body = {
            "id": "chatcmpl-3",
            "model": "model-a",
            "choices": [
                {
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": "那就点吧",
                        "reasoning_content": "（他在催我点外卖……）",
                    },
                    "finish_reason": "stop",
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        }
        provider, _ = make_provider(lambda req: httpx.Response(200, json=body))
        response = await provider.chat(request())
        assert response.content == "那就点吧"
        assert "点外卖" in response.reasoning

    async def test_reasoning_defaults_empty(self) -> None:
        provider, _ = make_provider(lambda req: httpx.Response(200, json=OK_BODY))
        response = await provider.chat(request())
        assert response.reasoning == ""
