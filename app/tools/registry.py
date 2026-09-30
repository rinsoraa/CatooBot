"""Tool registry + capability index (spec §9/§12/§13).

The registry knows *which* tools exist and what they can do; it never executes
anything. Capability retrieval scores tools against the current message so
only the few relevant schemas reach the model — never all of them.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Sequence
from typing import Any

from app.memory.retrieval import bigrams, keyword_overlap
from app.tools.errors import Tool as ToolBase
from app.tools.errors import ToolError, UnknownToolError
from app.tools.models import ToolMetadata


class ToolRegistry:
    def __init__(self, logger: logging.Logger | None = None) -> None:
        self._log = logger or logging.getLogger("CatooBot.Tools")
        self._tools: dict[str, ToolBase] = {}
        self._enabled: dict[str, bool] = {}

    # ---------------------------------------------------------------- write

    def register(self, tool: ToolBase) -> None:
        name = tool.metadata.name
        if name in self._tools:
            raise ToolError(f"Tool '{name}' is already registered")
        self._tools[name] = tool
        self._enabled[name] = tool.metadata.enabled
        self._log.info(
            "[Tool] registered %s v%s (%s, risk=%s)",
            name,
            tool.metadata.version,
            tool.metadata.category,
            tool.metadata.risk_level,
        )

    def unregister(self, name: str) -> bool:
        if name not in self._tools:
            return False
        del self._tools[name]
        self._enabled.pop(name, None)
        return True

    def enable(self, name: str, enabled: bool = True) -> bool:
        if name not in self._tools:
            return False
        self._enabled[name] = enabled
        return True

    def set_enabled_from_config(self, name: str, enabled: bool) -> None:
        if name in self._tools:
            self._enabled[name] = enabled

    # ----------------------------------------------------------------- read

    def get(self, name: str) -> ToolBase:
        tool = self._tools.get(name)
        if tool is None:
            raise UnknownToolError(f"Unknown tool: {name}")
        return tool

    def maybe_get(self, name: str) -> ToolBase | None:
        return self._tools.get(name)

    def is_enabled(self, name: str) -> bool:
        return bool(self._enabled.get(name, False))

    def all(self, *, include_disabled: bool = True) -> Sequence[ToolBase]:
        """All registered tools (the name ``list`` would shadow the builtin)."""
        tools: list[ToolBase] = list(self._tools.values())
        if not include_disabled:
            tools = [tool for tool in tools if self.is_enabled(tool.metadata.name)]
        return sorted(tools, key=lambda tool: tool.metadata.name)

    def names(self, *, enabled_only: bool = True) -> list[str]:
        return [
            tool.metadata.name
            for tool in self.all(include_disabled=not enabled_only)
            if not enabled_only or self.is_enabled(tool.metadata.name)
        ]

    def describe(self) -> list[dict[str, Any]]:
        """Registry view for the WebUI (spec §37)."""
        return [
            {
                **tool.metadata.model_dump(),
                "enabled": self.is_enabled(tool.metadata.name),
            }
            for tool in self.all()
        ]

    def __len__(self) -> int:
        return len(self._tools)

    # ------------------------------------------------ capability retrieval

    def candidates(
        self,
        query: str,
        *,
        limit: int = 5,
        allowed: Iterable[str] | None = None,
        min_score: float = 0.08,
    ) -> list[tuple[ToolBase, float]]:
        """Rank enabled tools by relevance to ``query`` (keyword + metadata)."""
        allowed_set = set(allowed) if allowed is not None else None
        query_tokens = bigrams(query)
        scored: list[tuple[ToolBase, float]] = []
        for tool in self.all(include_disabled=False):
            name = tool.metadata.name
            if allowed_set is not None and name not in allowed_set:
                continue
            score = self._score(tool.metadata, query, query_tokens)
            if score >= min_score:
                scored.append((tool, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:limit]

    @staticmethod
    def _score(metadata: ToolMetadata, query: str, query_tokens: set[str]) -> float:
        """Lexical + keyword + tag evidence; deliberately simple and debuggable."""
        score = 0.0
        lowered = query.lower()
        for keyword in metadata.keywords:
            if keyword and keyword.lower() in lowered:
                score += 0.55
        for tag in metadata.tags:
            if tag and tag.lower() in lowered:
                score += 0.25
        score += 0.5 * keyword_overlap(query_tokens, metadata.description)
        score += 0.35 * keyword_overlap(query_tokens, metadata.when_to_use)
        score += 0.15 * keyword_overlap(query_tokens, metadata.name.replace("_", " "))
        if metadata.category and metadata.category.lower() in lowered:
            score += 0.2
        return round(min(1.0, score), 4)
