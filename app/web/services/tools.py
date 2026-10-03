"""Tool admin service: registry view, tests, logs, metrics, policy, credentials.

Everything the WebUI shows about tools comes through here — never through the
QQ pipeline (spec v0.6 §28/§100). Credentials are write-only and always masked.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from app.tools.models import ToolContext

if TYPE_CHECKING:
    from app.core.bot import Bot


# provider options offered in the WebUI (a provider is only used if it works)
KNOWN_PROVIDERS: dict[str, list[str]] = {
    "weather": ["open_meteo", "weatherapi"],
    "web_search": ["tavily", "brave"],
}


class ToolAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Tools")

    @property
    def runtime(self) -> Any:
        return self.bot.tools

    # ------------------------------------------------------------- dashboard

    async def dashboard(self) -> dict[str, Any]:
        snapshot = self.runtime.snapshot()
        metrics = await self.runtime.executor.metrics()
        recent = await self.runtime.executor.recent_executions(limit=10)
        snapshot["metrics"] = metrics
        snapshot["recent"] = recent
        snapshot["reasoning"] = await self.reasoning_modes()
        return snapshot

    async def tool_detail(self, name: str) -> dict[str, Any]:
        tool = self.runtime.registry.maybe_get(name)
        if tool is None:
            return {}
        metrics = await self.runtime.executor.metrics()
        settings = getattr(tool, "settings", {}) or {}
        return {
            "metadata": {
                **tool.metadata.model_dump(),
                "enabled": self.runtime.is_enabled(name),
            },
            "settings": settings,
            "credential_names": list(tool.metadata.requires_credentials),
            "credentials": self._credential_view(settings),
            "metrics": metrics.get("by_tool", {}).get(name, {}),
            "executions": await self.runtime.executor.recent_executions(limit=20, tool_name=name),
        }

    def _credential_view(self, settings: dict[str, Any]) -> dict[str, Any]:
        """Masked credential info for the detail page (never plaintext)."""
        name = str(settings.get("api_key_env") or "")
        if not name:
            return {}
        return {
            name: {
                "masked": self.runtime.credentials.masked(name),
                "source": self.runtime.credentials.source_of(name),
            }
        }

    async def executions(self, limit: int = 100, tool_name: str = "") -> list[dict[str, Any]]:
        rows = await self.runtime.executor.recent_executions(limit=limit, tool_name=tool_name)
        for row in rows:
            # never surface raw arguments or secrets (v0.6 §42/§101)
            row.pop("arguments_preview", None)
        return rows

    async def metrics(self) -> dict[str, Any]:
        return await self.runtime.executor.metrics()

    async def last_used(self) -> dict[str, float]:
        """Per-tool last execution time (W5 list column); ``{}`` without a DB."""
        database = getattr(self.bot, "database", None)
        if database is None:
            return {}
        try:
            rows = await database.fetchall(
                "SELECT tool_name, MAX(created_at) AS last_at FROM tool_executions"
                " GROUP BY tool_name"
            )
        except Exception:  # noqa: BLE001 - a missing column must not break the page
            self._log.debug("Failed to read tool last-used times", exc_info=True)
            return {}
        return {str(row["tool_name"]): float(row["last_at"]) for row in rows if row["last_at"]}

    async def reasoning_modes(self) -> list[str]:
        """Documented argument sources, shown in the trace view (v0.6 §56)."""
        from app.tools.models import ARGUMENT_SOURCES

        return list(ARGUMENT_SOURCES)

    # ------------------------------------------------------------- actions

    async def set_enabled(self, name: str, enabled: bool) -> bool:
        return await self.runtime.set_tool_enabled(name, enabled)

    async def save_settings(
        self,
        name: str,
        *,
        settings: dict[str, Any] | None = None,
        timeout: float | None = None,
        cache_ttl_seconds: float | None = None,
        provider: str = "",
        default_location: str = "",
        max_results: int | None = None,
        api_key_env: str = "",
        api_key_value: str = "",
    ) -> dict[str, Any]:
        """Apply + persist a tool's configuration; secrets go to the store.

        ``settings`` is the free-form bag the v1 API forwards; the named
        parameters remain the legacy form fields (they win on conflict).
        """
        payload: dict[str, Any] = dict(settings or {})
        if provider:
            payload["provider"] = provider
            # remember the other known providers as fallbacks (v0.6 §108)
            payload["fallback_providers"] = [
                item for item in KNOWN_PROVIDERS.get(name, []) if item != provider
            ]
        if default_location:
            payload["default_location"] = default_location
        if max_results is not None:
            payload["max_results"] = max(1, min(10, max_results))
        if api_key_env:
            payload["api_key_env"] = api_key_env
            if api_key_value:
                self.runtime.credentials.set_secret(api_key_env, api_key_value)

        await self.runtime.update_tool_settings(
            name, settings=payload or None, timeout=timeout, cache_ttl_seconds=cache_ttl_seconds
        )
        return {
            "ok": True,
            "settings": getattr(self.runtime.registry.maybe_get(name), "settings", {}),
        }

    async def test_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Admin dry run (spec v0.6 §41): executes but never sends anything to QQ."""
        tool = self.runtime.registry.maybe_get(name)
        if tool is None:
            return {"ok": False, "error": f"unknown tool: {name}"}

        from app.tools.policy import TurnBudget

        context = ToolContext(
            user_id="webui-admin",
            session_id="webui:test",
            character_name=self.bot.character.personas.persona.identity.name,
            timezone=self.bot.config.character.timezone,
        )
        budget = TurnBudget(
            max_calls=1, max_execution_time=self.bot.config.tools.max_execution_time
        )
        result = await self.runtime.executor.execute(
            # bypass per-user limits for an explicit admin test, keep schema+timeout
            self._call(name, arguments),
            context,
            budget,
        )
        return {
            "ok": result.success,
            "result": result.model_dump(),
            "latency_ms": round(result.execution_time * 1000, 2),
            "note": "测试结果不会发送到 QQ",
        }

    @staticmethod
    def _call(name: str, arguments: dict[str, Any]) -> Any:
        from app.tools.models import ToolCall

        return ToolCall(name=name, arguments=arguments, reason="webui_test")

    async def decision_debug(
        self, query: str, *, mode: str = "candidates", relationship_stage: str = "familiar"
    ) -> dict[str, Any]:
        """Explain tool selection (spec v0.6 §75/§103) — WebUI only."""
        candidates = self.runtime.router.candidates(query)
        blocked: list[dict[str, Any]] = []
        if mode == "candidates":
            for tool in self.runtime.registry.all():
                if tool.metadata.name in {tool.metadata.name for tool, _ in candidates}:
                    continue
                blocked.append(
                    {
                        "name": tool.metadata.name,
                        "enabled": self.runtime.is_enabled(tool.metadata.name),
                        "reason": (
                            "disabled"
                            if not self.runtime.is_enabled(tool.metadata.name)
                            else "no relevant intent"
                        ),
                    }
                )
        return {
            "query": query,
            "candidates": [
                {
                    "name": tool.metadata.name,
                    "score": score,
                    "enabled": self.runtime.is_enabled(tool.metadata.name),
                    "risk_level": tool.metadata.risk_level,
                }
                for tool, score in candidates
            ],
            "rejected": blocked,
            "selected": candidates[0][0].metadata.name if candidates else None,
            "decision_mode": self.runtime.config.decision_mode,
            "instruction_preview": (
                self.runtime.router.build_instruction(candidates)[:1500] if candidates else ""
            ),
        }

    # ------------------------------------------------------------ permissions

    async def permissions(self) -> list[dict[str, Any]]:
        if self.bot.database is None:
            return []
        rows = await self.bot.database.fetchall(
            "SELECT * FROM tool_permissions ORDER BY scope, ref, tool_name"
        )
        return [dict(row) for row in rows]

    async def set_permission(self, scope: str, ref: str, tool_name: str, allowed: bool) -> bool:
        return await self.runtime.set_permission(scope, ref, tool_name, allowed)

    async def clear_permission(self, scope: str, ref: str, tool_name: str) -> bool:
        return await self.runtime.clear_permission(scope, ref, tool_name)

    # ------------------------------------------------------------ credentials

    async def credentials(self) -> list[dict[str, Any]]:
        """Names + masked values only; the plaintext never leaves the store."""
        names = self.runtime.credentials.names()
        return [
            {
                "name": name,
                "masked": self.runtime.credentials.masked(name),
                "source": self.runtime.credentials.source_of(name),
            }
            for name in names
        ]

    async def set_credential(self, name: str, value: str) -> bool:
        self.runtime.credentials.set_secret(name, value)
        return True

    async def delete_credential(self, name: str) -> bool:
        return self.runtime.credentials.delete_secret(name)

    # ---------------------------------------------------------------- caches

    async def clear_cache(self, tool_name: str = "") -> int:
        return await self.runtime.executor.clear_cache(tool_name)

    async def reload(self) -> dict[str, Any]:
        """Hot reload: re-read tool configs and permissions from the database."""
        await self.runtime._load_overrides()  # noqa: SLF001
        rules = await self.runtime.reload_permissions()
        return {"ok": True, "permission_rules": rules}
