"""ToolRuntime: assembles registry + policy + executor and owns their lifecycle.

Startup: load config → register builtins → apply DB overrides → load permissions.
A single broken tool/provider is isolated: it is disabled with a log line and
everything else keeps working (spec v0.6 §72).

WebUI edits (enable/disable, timeout, provider settings, permissions) hot-apply
through :meth:`apply_settings` / :meth:`set_tool_enabled` — no restart (v0.6 §73).
"""

from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from app.config.settings import ToolsConfig
from app.tools.credentials import CredentialManager
from app.tools.executor import ToolExecutor
from app.tools.policy import ToolPolicy
from app.tools.registry import ToolRegistry
from app.tools.result import ToolResultProcessor
from app.tools.router import ToolOrchestrator, ToolRouter

if TYPE_CHECKING:  # pragma: no cover
    from app.database.database import Database

CONFIG_KEY_PREFIX = "tool:"


class ToolRuntime:
    def __init__(
        self,
        config: ToolsConfig,
        database: Database | None = None,
        logger: logging.Logger | None = None,
        credentials: CredentialManager | None = None,
    ) -> None:
        self.config = config
        self._db = database
        self._log = logger or logging.getLogger("CatooBot.Tools")
        self.credentials = credentials or CredentialManager(logger=self._log)

        self.registry = ToolRegistry(self._log)
        self.policy = ToolPolicy(config, self._log)
        self.processor = ToolResultProcessor(self._log)
        self.executor = ToolExecutor(
            config, self.registry, self.policy, database, self.processor, self._log
        )
        self.router = ToolRouter(config, self.registry, self._log)
        self.orchestrator = ToolOrchestrator(
            config, self.registry, self.router, self.executor, self.policy, self._log
        )

    # ------------------------------------------------------------- status

    @property
    def enabled(self) -> bool:
        """True when the runtime is switched on *and* has usable tools.

        The character runtime checks this before trying any tool call, so a
        disabled runtime costs nothing per message.
        """
        return bool(self.config.enabled and len(self.registry))

    # ------------------------------------------------------------ bootstrap

    async def start(self) -> None:
        """Register builtins, apply overrides and permissions. Never raises."""
        try:
            self._register_builtins()
        except Exception:  # noqa: BLE001 - a broken builtin must not stop startup
            self._log.exception("[Tool] registering builtin tools failed")
        try:
            await self._load_overrides()
        except Exception:  # noqa: BLE001
            self._log.exception("[Tool] loading tool overrides failed")
        try:
            await self.reload_permissions()
        except Exception:  # noqa: BLE001
            self._log.exception("[Tool] loading tool permissions failed")

    def _register_builtins(self) -> None:
        from app.tools.builtins import (
            CalculatorTool,
            MinecraftChatTool,
            MinecraftContainerInspectTool,
            MinecraftContainerTransferTool,
            MinecraftCraftTool,
            MinecraftDigCapabilityTool,
            MinecraftDigTool,
            MinecraftDroppedItemsTool,
            MinecraftEquipTool,
            MinecraftFindBlocksTool,
            MinecraftFollowPlayerTool,
            MinecraftInventoryMoveTool,
            MinecraftInventoryTool,
            MinecraftLookAtTool,
            MinecraftMoveToTool,
            MinecraftPickupItemTool,
            MinecraftPlaceTool,
            MinecraftRecipeLookupTool,
            MinecraftStopTool,
            MinecraftWorldTool,
            QueryImageMemoryTool,
            TimeTool,
            WeatherTool,
            WebSearchTool,
        )

        settings = {name: cfg.settings for name, cfg in self.config.configs.items()}
        builders = (
            ("time", lambda: TimeTool()),
            ("calculator", lambda: CalculatorTool()),
            # Phase 3E：Minecraft 感知与行动的正式 LLM Tool 面（§二）
            ("minecraft_world", lambda: MinecraftWorldTool()),
            ("minecraft_chat", lambda: MinecraftChatTool()),
            ("minecraft_look_at", lambda: MinecraftLookAtTool()),
            ("minecraft_move_to", lambda: MinecraftMoveToTool()),
            ("minecraft_follow_player", lambda: MinecraftFollowPlayerTool()),
            ("minecraft_stop", lambda: MinecraftStopTool()),
            # Phase 4B：第一个世界修改动作（MEDIUM；必须用户确认，只挖单块）
            ("minecraft_dig", lambda: MinecraftDigTool()),
            # Phase 4C：只读背包切片（SAFE）+ 放置单方块（MEDIUM，对称于 dig）
            ("minecraft_inventory", lambda: MinecraftInventoryTool()),
            ("minecraft_place", lambda: MinecraftPlaceTool()),
            # Phase 4D：背包写操作（MEDIUM；单物品单槽位，必须用户确认）
            ("minecraft_equip", lambda: MinecraftEquipTool()),
            ("minecraft_inventory_move", lambda: MinecraftInventoryMoveTool()),
            # Phase 4E：单方块容器（读 Chest/Barrel 的 SAFE inspection + MEDIUM 单项存取）
            ("minecraft_container_inspect", lambda: MinecraftContainerInspectTool()),
            ("minecraft_container_transfer", lambda: MinecraftContainerTransferTool()),
            # Phase 4F：玩家自身 2×2 背包合成（查配方 SAFE / 执行一次 MEDIUM）
            ("minecraft_recipe_lookup", lambda: MinecraftRecipeLookupTool()),
            # Phase 4H：掉落物感知（SAFE 只读）/ 捡起一个明确实体（MEDIUM）
            ("minecraft_dropped_items", lambda: MinecraftDroppedItemsTool()),
            # Phase 4J：挖掘能力只读查询（SAFE；只回答「现在能不能挖、多久」）
            ("minecraft_dig_capability", lambda: MinecraftDigCapabilityTool()),
            # Phase 4K：找方块（SAFE 只读；只定位，不移动/不装备/不挖/不拾取）
            ("minecraft_find_blocks", lambda: MinecraftFindBlocksTool()),
            ("minecraft_pickup_item", lambda: MinecraftPickupItemTool()),
            ("minecraft_craft", lambda: MinecraftCraftTool()),
            ("query_image_memory", lambda: QueryImageMemoryTool()),
            (
                "weather",
                lambda: WeatherTool(settings.get("weather", {}), self.credentials),
            ),
            (
                "web_search",
                lambda: WebSearchTool(settings.get("web_search", {}), self.credentials),
            ),
        )
        for name, build in builders:
            try:
                self.registry.register(build())
            except Exception:  # noqa: BLE001 - isolate per-tool failures
                self._log.exception("[Tool] builtin '%s' could not be registered", name)

    async def _load_overrides(self) -> None:
        if self._db is None:
            return
        rows = await self._db.fetchall("SELECT * FROM tool_configs")
        for row in rows:
            name = row["name"]
            if self.registry.maybe_get(name) is None:
                continue
            self.registry.set_enabled_from_config(name, bool(row["enabled"]))
            try:
                settings = json.loads(row["config"] or "{}")
            except (TypeError, ValueError):
                continue
            self._apply_settings(name, settings)
            if row["timeout"]:
                self.registry.get(name).metadata.timeout = float(row["timeout"])
        if rows:
            self._log.info("[Tool] applied %d stored override(s)", len(rows))

    def _apply_settings(self, name: str, settings: dict[str, Any]) -> None:
        """Push WebUI settings into a live tool instance."""
        tool = self.registry.maybe_get(name)
        if tool is None:
            return
        merged = {**getattr(tool, "settings", {}), **settings}
        if hasattr(tool, "settings"):
            tool.settings = merged
        # provider choices change → rebuild lazily on next call
        for attribute in ("_runtime_providers",):
            if hasattr(tool, attribute):
                setattr(tool, attribute, [])
        if "timeout" in settings:
            try:
                tool.metadata.timeout = float(settings["timeout"])
            except (TypeError, ValueError):
                pass
        if "cache_ttl_seconds" in settings:
            try:
                tool.metadata.cache_ttl_seconds = float(settings["cache_ttl_seconds"])
            except (TypeError, ValueError):
                pass

    async def reload_permissions(self) -> int:
        if self._db is None:
            return 0
        rows = await self._db.fetchall(
            "SELECT scope, ref, tool_name, allowed FROM tool_permissions"
        )
        self.policy.load_permissions([dict(row) for row in rows])
        return len(rows)

    # ------------------------------------------------------------- settings

    def is_enabled(self, name: str) -> bool:
        return self.registry.is_enabled(name)

    async def set_tool_enabled(self, name: str, enabled: bool) -> bool:
        """Enable/disable a tool and persist the choice (hot reload, v0.6 §39)."""
        if not self.registry.enable(name, enabled):
            return False
        await self._persist_config(name, enabled=enabled)
        self._log.info("[Tool] %s %s", name, "enabled" if enabled else "disabled")
        return True

    async def update_tool_settings(
        self,
        name: str,
        *,
        settings: dict[str, Any] | None = None,
        timeout: float | None = None,
        cache_ttl_seconds: float | None = None,
    ) -> bool:
        tool = self.registry.maybe_get(name)
        if tool is None:
            return False
        payload: dict[str, Any] = {}
        if settings:
            payload.update(settings)
        if timeout is not None:
            payload["timeout"] = timeout
        if cache_ttl_seconds is not None:
            payload["cache_ttl_seconds"] = cache_ttl_seconds
        self._apply_settings(name, payload)
        await self._persist_config(name, settings=payload)
        return True

    async def _persist_config(
        self,
        name: str,
        *,
        enabled: bool | None = None,
        settings: dict[str, Any] | None = None,
    ) -> None:
        if self._db is None:
            return
        existing = await self._db.fetchone(
            "SELECT enabled, timeout, config FROM tool_configs WHERE name = ?", (name,)
        )
        current_enabled = bool(existing["enabled"]) if existing else True
        current_timeout = existing["timeout"] if existing else None
        try:
            current_settings = json.loads(existing["config"] or "{}") if existing else {}
        except (TypeError, ValueError):
            current_settings = {}
        if settings:
            current_settings.update(settings)
            current_timeout = current_settings.get("timeout", current_timeout)
        await self._db.execute(
            """INSERT INTO tool_configs (name, enabled, timeout, config, updated_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   enabled=excluded.enabled, timeout=excluded.timeout,
                   config=excluded.config, updated_at=excluded.updated_at""",
            (
                name,
                1 if (enabled if enabled is not None else current_enabled) else 0,
                current_timeout,
                json.dumps(current_settings, ensure_ascii=False),
                int(time.time()),
            ),
        )

    async def set_permission(self, scope: str, ref: str, tool_name: str, allowed: bool) -> bool:
        if self._db is None:
            return False
        await self._db.execute(
            """INSERT INTO tool_permissions (scope, ref, tool_name, allowed, created_at)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(scope, ref, tool_name) DO UPDATE SET allowed=excluded.allowed""",
            (scope, str(ref), tool_name, 1 if allowed else 0, int(time.time())),
        )
        await self.reload_permissions()
        return True

    async def clear_permission(self, scope: str, ref: str, tool_name: str) -> bool:
        if self._db is None:
            return False
        await self._db.execute(
            "DELETE FROM tool_permissions WHERE scope = ? AND ref = ? AND tool_name = ?",
            (scope, str(ref), tool_name),
        )
        await self.reload_permissions()
        return True

    # ------------------------------------------------------------- lifecycle

    async def close(self) -> None:
        for tool in self.registry.all():
            try:
                await tool.close()
            except Exception:  # noqa: BLE001
                self._log.debug("Closing tool %s failed", tool.metadata.name, exc_info=True)

    # --------------------------------------------------------------- status

    def snapshot(self) -> dict[str, Any]:
        tools = self.registry.describe()
        enabled = [tool for tool in tools if tool["enabled"]]
        return {
            "enabled": self.config.enabled,
            "decision_mode": self.config.decision_mode,
            "total": len(tools),
            "enabled_count": len(enabled),
            "disabled_count": len(tools) - len(enabled),
            "max_calls_per_turn": self.config.max_calls_per_turn,
            "policy": self.policy.snapshot(),
            "tools": tools,
        }
