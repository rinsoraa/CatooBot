"""Search provider abstraction (spec v0.6 §45) + a null provider.

Real search needs a vendor key, so the shipped implementations are:

* ``null``     — always "not configured"; the tool then reports unavailability
                 and the model must answer honestly instead of inventing news
                 (v0.6 §26/§95/§96);
* ``tavily``   — Tavily-style JSON API (``api_key_env``);
* ``brave``    — Brave Search API style (``X-Subscription-Token`` header).

The tool normalizes every provider's payload into
``{title, url, snippet, source, published_at}`` and caps the result count.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections.abc import Callable
from typing import Any

import httpx

from app.tools.errors import (
    ExternalServiceError,
    ToolAuthenticationError,
    ToolNotAvailableError,
    ToolRateLimitError,
    ToolTimeoutError,
)

MAX_RESULTS = 5


class SearchProvider(ABC):
    name = ""

    def __init__(self, settings: dict[str, Any], credentials: Any = None) -> None:
        self.settings = settings or {}
        self.credentials = credentials
        self._log = logging.getLogger(f"CatooBot.Tools.Search.{self.name}")

    @property
    def configured(self) -> bool:
        return True

    @abstractmethod
    async def search(self, query: str, *, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
        """Return normalized results (possibly empty)."""

    async def close(self) -> None:
        return None

    @staticmethod
    def _normalize(
        title: Any, url: Any, snippet: Any, source: Any = "", published_at: Any = None
    ) -> dict[str, Any]:
        return {
            "title": str(title or "").strip()[:200],
            "url": str(url or "").strip()[:300],
            "snippet": " ".join(str(snippet or "").split())[:400],
            "source": str(source or "").strip()[:80],
            "published_at": str(published_at or "")[:40],
        }

    @staticmethod
    def dedupe(results: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for item in results:
            key = (item.get("url") or item.get("title") or "").lower()
            if not key or key in seen:
                continue
            seen.add(key)
            unique.append(item)
            if len(unique) >= limit:
                break
        return unique


class NullSearchProvider(SearchProvider):
    """Placeholder used until an admin configures a real provider."""

    name = "null"

    @property
    def configured(self) -> bool:
        return False

    async def search(self, query: str, *, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
        raise ToolNotAvailableError("search provider is not configured")


class _HttpSearchProvider(SearchProvider):
    """Shared HTTP plumbing for JSON search APIs."""

    base_url = ""

    def _api_key(self) -> str:
        key_name = str(self.settings.get("api_key_env") or "")
        value = self.credentials.get_secret(key_name) if (self.credentials and key_name) else ""
        if not value:
            raise ToolAuthenticationError(
                f"search provider '{self.name}' has no credential ({key_name or 'unset'})"
            )
        return value

    async def _post_json(
        self, url: str, *, json_body: dict[str, Any], headers: dict[str, str]
    ) -> Any:
        timeout = float(self.settings.get("timeout", 10.0))
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                response = await client.post(url, json=json_body, headers=headers)
        except httpx.TimeoutException as exc:
            raise ToolTimeoutError(f"search timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise ExternalServiceError(f"search transport error: {exc.__class__.__name__}") from exc
        return self._handle(response)

    async def _get_json(self, url: str, *, params: dict[str, Any], headers: dict[str, str]) -> Any:
        timeout = float(self.settings.get("timeout", 10.0))
        try:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=timeout) as client:
                response = await client.get(url, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise ToolTimeoutError(f"search timeout: {exc}") from exc
        except httpx.TransportError as exc:
            raise ExternalServiceError(f"search transport error: {exc.__class__.__name__}") from exc
        return self._handle(response)

    @staticmethod
    def _handle(response: httpx.Response) -> Any:
        if response.status_code in (401, 403):
            raise ToolAuthenticationError("search provider rejected the API key")
        if response.status_code == 429:
            raise ToolRateLimitError("search provider rate limited the request")
        if response.status_code >= 500:
            raise ExternalServiceError(f"search provider error {response.status_code}")
        if response.status_code != 200:
            raise ExternalServiceError(f"search provider HTTP {response.status_code}")
        try:
            return response.json()
        except ValueError as exc:
            raise ExternalServiceError("search provider returned invalid JSON") from exc


class TavilySearchProvider(_HttpSearchProvider):
    name = "tavily"
    base_url = "https://api.tavily.com"

    async def search(self, query: str, *, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
        payload = await self._post_json(
            "/search",
            json_body={
                "api_key": self._api_key(),
                "query": query,
                "max_results": max(1, min(10, limit)),
                "search_depth": "basic",
            },
            headers={"Content-Type": "application/json"},
        )
        results = [
            self._normalize(item.get("title"), item.get("url"), item.get("content"), "tavily")
            for item in (payload.get("results") or [])
        ]
        return self.dedupe(results, limit)


class BraveSearchProvider(_HttpSearchProvider):
    name = "brave"
    base_url = "https://api.search.brave.com"

    async def search(self, query: str, *, limit: int = MAX_RESULTS) -> list[dict[str, Any]]:
        payload = await self._get_json(
            "/res/v1/web/search",
            params={"q": query, "count": max(1, min(10, limit))},
            headers={"X-Subscription-Token": self._api_key(), "Accept": "application/json"},
        )
        items = ((payload.get("web") or {}).get("results")) or []
        results = [
            self._normalize(
                item.get("title"),
                item.get("url"),
                item.get("description"),
                "brave",
                item.get("age") or item.get("page_age"),
            )
            for item in items
        ]
        return self.dedupe(results, limit)


PROVIDERS: dict[str, Callable[[dict[str, Any], Any], SearchProvider]] = {
    NullSearchProvider.name: NullSearchProvider,
    TavilySearchProvider.name: TavilySearchProvider,
    BraveSearchProvider.name: BraveSearchProvider,
}


def create_search_provider(
    name: str, settings: dict[str, Any], credentials: Any = None
) -> SearchProvider:
    factory = PROVIDERS.get(name or "null")
    if factory is None:
        raise ExternalServiceError(f"unknown search provider: {name}")
    return factory(settings, credentials)
