"""Expression / 口癖 admin service (Task 22): the WebUI bridge for learned phrases.

Read-only listing plus the reversible operator actions — disable/enable and
delete (which also removes provenance and vectors). No writes touch the chat
path here; this is the management surface for the "可停用 / 可删除 / 可溯源"
constraint.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.config.settings import ExpressionConfig

if TYPE_CHECKING:
    from app.core.bot import Bot


class ExpressionAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Web.Expression")

    @property
    def store(self) -> Any:
        return self.bot.expression_store

    @property
    def config(self) -> ExpressionConfig:
        return self.bot.config.expression

    async def list_patterns(self, scope_key: str = "") -> list[dict[str, Any]]:
        return await self.store.list_patterns(scope_key)

    def stats(self) -> dict[str, Any]:
        """Metric counters for the page header (learned/injected/rejected)."""
        metrics = getattr(self.bot, "metrics", None)
        if metrics is None:
            return {}
        return {
            "learned": metrics.get("expressions_learned"),
            "injected": metrics.get("expressions_injected"),
            "evicted": metrics.get("expressions_evicted"),
        }

    async def samples(self, pattern_id: int) -> list[dict[str, Any]]:
        return await self.store.samples(pattern_id)

    async def set_status(self, pattern_id: int, status: str) -> None:
        await self.store.set_status(pattern_id, status)

    async def delete(self, pattern_id: int) -> None:
        await self.store.delete(pattern_id)
