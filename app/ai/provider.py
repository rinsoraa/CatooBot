"""Provider abstraction: the only doorway through which models enter CatooBot.

The engine/router never talk HTTP themselves; they only know
``AIProvider.chat(request) -> AIResponse`` and the standardized error types
from :mod:`app.ai.errors`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable

from app.ai.models import AIRequest, AIResponse


class AIProvider(ABC):
    """Base class every AI backend must implement."""

    name: str = ""

    @abstractmethod
    async def chat(self, request: AIRequest) -> AIResponse:
        """Perform a chat completion. Must raise the standardized AI errors."""

    @abstractmethod
    async def close(self) -> None:
        """Release underlying resources (HTTP connections, sessions)."""


# Registry: provider config ``type`` -> factory. Adding a new backend kind
# means implementing AIProvider and registering it here — the engine and
# router never change.
ProviderFactory = Callable[..., AIProvider]

_PROVIDER_FACTORIES: dict[str, ProviderFactory] = {}


def register_provider_type(type_name: str, factory: ProviderFactory) -> None:
    _PROVIDER_FACTORIES[type_name] = factory


def create_provider(type_name: str, **kwargs: object) -> AIProvider:
    """Build one provider by type name.

    Factories receive every keyword the engine resolved for them — today
    ``name`` / ``base_url`` / ``api_key`` / ``timeout`` / ``semaphore`` (the
    per-``base_url`` concurrency gate); a factory that does not need one simply
    accepts ``**kwargs``.
    """
    try:
        factory = _PROVIDER_FACTORIES[type_name]
    except KeyError:
        raise ValueError(
            f"Unknown provider type '{type_name}' (available: {sorted(_PROVIDER_FACTORIES)})"
        ) from None
    return factory(**kwargs)
