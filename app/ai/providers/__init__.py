"""Built-in AI providers. Importing this package registers provider types."""

from app.ai.providers.openai_compatible import OpenAICompatibleProvider

__all__ = ["OpenAICompatibleProvider"]
