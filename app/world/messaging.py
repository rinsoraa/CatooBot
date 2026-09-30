"""Pending initiative: what to do when the world has something to say but QQ is away.

Background life is **not** background messaging (spec §42/§43). When the
initiative gate approves a proactive message but the character cannot actually
deliver it (QQ offline, bot reconnecting), the text is parked here with a TTL —
and it is thrown away, never mass-resent, when it goes stale (spec §44).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from app.config.settings import WorldMessagingConfig
from app.world.clock import WorldClock

STATE_KEY = "world.pending_initiative"
MAX_PENDING = 10


class PendingInitiativeQueue:
    def __init__(
        self,
        *,
        database: Any,
        clock: WorldClock,
        config: WorldMessagingConfig | None = None,
        logger: logging.Logger | None = None,
    ) -> None:
        self._db = database
        self._clock = clock
        self._config = config or WorldMessagingConfig()
        self._log = logger or logging.getLogger("CatooBot.World")
        self.dropped = 0
        self.sent = 0

    # ---------------------------------------------------------------- write

    async def add(
        self,
        *,
        scope_key: str,
        user_id: str,
        text: str,
        reason: str = "",
        topic: str = "",
    ) -> bool:
        if not self._config.enabled or not text.strip():
            return False
        items = await self._load()
        # one pending message per scope: a newer thought replaces the older one
        items = [item for item in items if item.get("scope_key") != scope_key]
        items.append(
            {
                "scope_key": scope_key,
                "user_id": str(user_id),
                "text": text.strip()[:500],
                "reason": reason,
                "topic": topic,
                "created_at": int(self._clock.now().timestamp()),
            }
        )
        items = items[-MAX_PENDING:]
        await self._save(items)
        self._log.info("[World] Initiative parked for %s (offline or undeliverable)", scope_key)
        return True

    async def drop_expired(self) -> int:
        """TTL is the antidote to 'a stale message finally arrives hours later'."""
        items = await self._load()
        ttl = max(1, self._config.pending_ttl_minutes) * 60
        kept = [
            item
            for item in items
            if self._clock.elapsed_since(item.get("created_at")) < ttl
        ]
        dropped = len(items) - len(kept)
        if dropped:
            self.dropped += dropped
            await self._save(kept)
            self._log.info("[World] Dropped %s expired pending initiative(s)", dropped)
        return dropped

    async def clear(self, scope_key: str = "") -> int:
        items = await self._load()
        if not scope_key:
            await self._save([])
            return len(items)
        kept = [item for item in items if item.get("scope_key") != scope_key]
        removed = len(items) - len(kept)
        if removed:
            await self._save(kept)
        return removed

    # ----------------------------------------------------------------- read

    async def items(self) -> list[dict[str, Any]]:
        return await self._load()

    # ---------------------------------------------------------------- flush

    async def flush(
        self,
        send: Callable[[dict[str, Any]], Awaitable[bool]],
        *,
        online: bool,
        budget_left: int,
        skip: Callable[[dict[str, Any]], Awaitable[bool]] | None = None,
    ) -> int:
        """Deliver at most one parked message, if it is still appropriate.

        ``skip`` lets the caller veto an item (e.g. the user is already talking
        to the character right now — repeating a stale opener would be strange).
        """
        if not self._config.enabled or not online:
            return 0
        await self.drop_expired()
        if budget_left <= 0:
            remaining = await self._load()
            if remaining:
                self._log.info(
                    "[World] %s pending initiative(s) held: daily background budget spent",
                    len(remaining),
                )
            return 0
        items = await self._load()
        if not items:
            return 0
        item = items[0]
        if skip is not None and await skip(item):
            await self.clear(item.get("scope_key", ""))
            self.dropped += 1
            self._log.info("[World] Dropped pending initiative: user already active")
            return 0
        try:
            delivered = await send(item)
        except Exception:  # noqa: BLE001 - a failed send must not break the pass
            self._log.exception("[World] Pending initiative delivery failed")
            return 0
        if delivered:
            await self.clear(item.get("scope_key", ""))
            self.sent += 1
            self._log.info("[World] Delivered parked initiative to %s", item.get("scope_key"))
            from app.utils.narrator import narrate

            narrate().reach(
                f"把存在心里的话说给了 {item.get('scope_key')}",
                detail="QQ 重新上线后补上的",
            )
            return 1
        return 0

    async def view(self) -> dict[str, Any]:
        items = await self._load()
        return {
            "enabled": self._config.enabled,
            "pending": [
                {
                    "scope_key": item.get("scope_key"),
                    "text": item.get("text", "")[:80],
                    "reason": item.get("reason", ""),
                    "age_minutes": round(
                        self._clock.elapsed_since(item.get("created_at")) / 60.0, 1
                    ),
                }
                for item in items
            ],
            "ttl_minutes": self._config.pending_ttl_minutes,
            "max_background_messages_per_day": self._config.max_background_messages_per_day,
            "sent": self.sent,
            "dropped": self.dropped,
        }

    # --------------------------------------------------------------- storage

    async def _load(self) -> list[dict[str, Any]]:
        if self._db is None:
            return []
        try:
            raw = await self._db.get_state(STATE_KEY)
        except Exception:  # noqa: BLE001
            self._log.debug("[World] Pending queue read failed", exc_info=True)
            return []
        if not raw:
            return []
        try:
            data = json.loads(raw)
        except (TypeError, ValueError):
            return []
        return data if isinstance(data, list) else []

    async def _save(self, items: list[dict[str, Any]]) -> None:
        if self._db is None:
            return
        try:
            stamp = int(self._clock.now().timestamp())
            await self._db.set_state(
                STATE_KEY, json.dumps(items, ensure_ascii=False), stamp
            )
        except Exception:  # noqa: BLE001
            self._log.exception("[World] Pending queue write failed")
