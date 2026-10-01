"""AI data model tests: ChatMessage / AIRequest / AIResponse."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.ai.models import AIRequest, AIResponse, ChatMessage


class TestChatMessage:
    def test_basic_roles(self) -> None:
        for role in ("system", "user", "assistant"):
            message = ChatMessage(role=role, content="hi")
            assert message.role == role

    def test_invalid_role_rejected(self) -> None:
        with pytest.raises(ValidationError):
            ChatMessage(role="tool", content="hi")

    def test_helpers(self) -> None:
        assert ChatMessage.system("s").role == "system"
        assert ChatMessage.user("u").role == "user"
        assert ChatMessage.assistant("a").role == "assistant"

    def test_to_openai(self) -> None:
        assert ChatMessage.user("你好").to_openai() == {"role": "user", "content": "你好"}


class TestAIRequest:
    def test_minimal(self) -> None:
        request = AIRequest(messages=[ChatMessage.user("hi")])
        assert request.model is None
        assert request.temperature is None
        assert request.max_tokens is None
        assert request.metadata == {}

    def test_streaming_option_is_rejected_not_ignored(self) -> None:
        """``stream`` used to exist while the provider parsed JSON — a request
        option that could never be honoured. It is gone, and passing it fails
        loudly instead of being silently dropped."""
        assert "stream" not in AIRequest.model_fields
        with pytest.raises(ValidationError):
            AIRequest(messages=[ChatMessage.user("hi")], stream=True)

    def test_full(self) -> None:
        request = AIRequest(
            messages=[ChatMessage.user("hi")],
            model="some-model",
            temperature=0.5,
            max_tokens=100,
            metadata={"user_id": 1},
        )
        assert request.model == "some-model"
        assert request.metadata == {"user_id": 1}

    def test_with_model_returns_copy(self) -> None:
        request = AIRequest(messages=[ChatMessage.user("hi")], temperature=0.5)
        pinned = request.with_model("m-1")
        assert pinned.model == "m-1"
        assert request.model is None
        assert pinned.temperature == 0.5

    def test_messages_required(self) -> None:
        with pytest.raises(ValidationError):
            AIRequest(messages=[])


class TestAIResponse:
    def test_minimal(self) -> None:
        response = AIResponse(content="hello")
        assert response.content == "hello"
        assert response.model == ""
        assert response.provider == ""
        assert response.usage == {}
        assert response.finish_reason is None

    def test_full(self) -> None:
        response = AIResponse(
            content="hello",
            model="m",
            provider="p",
            usage={"total_tokens": 10},
            finish_reason="stop",
            raw_response={"choices": []},
        )
        assert response.usage["total_tokens"] == 10
        assert response.finish_reason == "stop"
