"""AI data models: the vocabulary shared by engine, router and providers.

These are protocol-agnostic: neither OpenAI specifics nor QQ specifics leak
into them. ``AIRequest.to_provider_payload()``-style conversion happens inside
each provider.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

VALID_ROLES = {"system", "user", "assistant"}


class ChatMessage(BaseModel):
    """A single turn in a chat conversation.

    ``images`` holds image URLs or data-URLs for multimodal turns; when set,
    ``to_openai`` emits the OpenAI list-content form (spec v1.1 §6). It stays
    empty for ordinary text, so nothing downstream changes.
    """

    role: str
    content: str
    images: list[str] = Field(default_factory=list)
    # Wall-clock epoch of this turn. Never serialized to the provider; the
    # character context builder uses it to tag stale history turns with a
    # human time marker ("[今天凌晨4点]") so old topics are not mistaken
    # for the present.
    created_at: float | None = None

    def model_post_init(self, __context: Any) -> None:
        if self.role not in VALID_ROLES:
            allowed = sorted(VALID_ROLES)
            raise ValueError(f"Invalid ChatMessage role: {self.role!r} (expected one of {allowed})")

    @classmethod
    def system(cls, content: str) -> ChatMessage:
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> ChatMessage:
        return cls(role="user", content=content)

    @classmethod
    def assistant(cls, content: str) -> ChatMessage:
        return cls(role="assistant", content=content)

    @classmethod
    def user_with_images(cls, content: str, images: list[str]) -> ChatMessage:
        """A multimodal user turn (text + one or more image URLs)."""
        return cls(role="user", content=content, images=images)

    def to_openai(self) -> dict[str, Any]:
        if self.images:
            parts: list[dict[str, Any]] = [
                {"type": "text", "text": self.content or ""}
            ]
            for image in self.images:
                parts.append({"type": "image_url", "image_url": {"url": image}})
            return {"role": self.role, "content": parts}
        return {"role": self.role, "content": self.content}


class AIRequest(BaseModel):
    """A chat completion request.

    ``model`` may be empty — the ModelRouter then decides which model to use.
    """

    messages: list[ChatMessage]
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    stream: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    # Optional tool schemas (native function calling). Providers that do not
    # support the parameter simply never receive it (see the tool runtime).
    tools: list[Any] | None = None

    @field_validator("messages")
    @classmethod
    def _require_messages(cls, value: list[ChatMessage]) -> list[ChatMessage]:
        if not value:
            raise ValueError("AIRequest requires at least one message")
        return value

    def with_model(self, model: str) -> AIRequest:
        """A copy of this request pinned to a concrete model identifier."""
        return self.model_copy(update={"model": model})


class AIResponse(BaseModel):
    """A normalized chat completion result."""

    content: str
    model: str = ""
    provider: str = ""
    usage: dict[str, Any] = Field(default_factory=dict)
    finish_reason: str | None = None
    raw_response: dict[str, Any] = Field(default_factory=dict, repr=False)
    # Normalized native tool calls: [{"id", "name", "arguments"}]
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
