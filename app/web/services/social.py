"""Social Cognition admin service (v0.9).

WebUI-only: dashboard metrics, observation log, per-group live context, a
manual "analyze now" (never sends to QQ), and decision replay (also never
sends). Everything here reads the live :class:`SocialCognitionEngine`; it never
mutates QQ state.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.bot import Bot

SETTINGS_KEY = "social_overrides"


class SocialAdminService:
    def __init__(self, bot: Bot) -> None:
        self.bot = bot
        self._log = logging.getLogger("CatooBot.Social")

    @property
    def social(self):
        return getattr(self.bot, "social", None)

    @property
    def available(self) -> bool:
        social = self.social
        return social is not None and getattr(social, "enabled", False)

    # ------------------------------------------------------------- dashboard

    async def dashboard(self) -> dict[str, Any]:
        if not self.available:
            return {"enabled": False}
        stats = await self.observations_stats()
        groups = self.social.groups()
        return {
            "enabled": True,
            "config": self.bot.config.social.model_dump(),
            "groups": groups,
            "group_count": len(groups),
            "active_threads": [t.as_dict() for t in self.social.threads.all_active()],
            "stats": stats,
        }

    async def observations_stats(self) -> dict[str, Any]:
        if self.bot.database is None:
            return {}
        row = await self.bot.database.fetchone(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN decision='reply' THEN 1 ELSE 0 END) AS replies,
                      SUM(CASE WHEN decision='ignore' THEN 1 ELSE 0 END) AS ignores,
                      SUM(CASE WHEN decision='defer' THEN 1 ELSE 0 END) AS defers,
                      SUM(CASE WHEN decision='observe' THEN 1 ELSE 0 END) AS observes
                 FROM social_observations"""
        )
        if row is None:
            return {}
        total = int(row["total"] or 0)
        replies = int(row["replies"] or 0)
        return {
            "total": total,
            "replies": replies,
            "ignores": int(row["ignores"] or 0),
            "defers": int(row["defers"] or 0),
            "observes": int(row["observes"] or 0),
            "reply_rate": round(replies / total, 3) if total else 0.0,
        }

    async def observations(self, *, group_id: str = "", limit: int = 50) -> list[dict[str, Any]]:
        if self.bot.database is None:
            return []
        sql = "SELECT * FROM social_observations"
        params: tuple[Any, ...] = ()
        if group_id:
            sql += " WHERE group_id = ?"
            params = (group_id,)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
        rows = await self.bot.database.fetchall(sql, (*params, int(limit)))
        import json

        result = []
        for row in rows:
            detail: dict[str, Any] = {}
            raw = row.get("detail")
            if raw:
                try:
                    detail = json.loads(raw)
                except (TypeError, ValueError):
                    detail = {}
            result.append(
                {
                    "observation_id": row["observation_id"],
                    "group_id": row["group_id"],
                    "message_range": row["message_range"],
                    "topic": row["topic"],
                    "decision": row["decision"],
                    "reason_code": row["reason_code"],
                    "confidence": round(float(row["confidence"] or 0.0), 3),
                    "created_at": row["created_at"],
                    "scores": detail.get("scores", {}),
                    "response_goal": detail.get("response_goal", ""),
                }
            )
        return result

    def group_context(self, group_id: str) -> dict[str, Any] | None:
        if not self.available:
            return None
        return self.social.group_snapshot(str(group_id))

    # ------------------------------------------------------------ simulation

    async def analyze_now(self, group_id: str) -> dict[str, Any]:
        """Run one observer pass over the current buffer — never sends to QQ."""
        social = self.social
        if not self.available:
            return {"error": "social disabled"}
        batch = social.monitor.unobserved(str(group_id))
        if not batch:
            batch = social.monitor.external_recent(
                str(group_id), limit=social.config.observer.batch_size
            )
        if not batch:
            return {"error": "该群还没有可分析的群聊消息"}
        decision = await social._observe(str(group_id), batch)  # noqa: SLF001
        return {
            "group_id": str(group_id),
            "batch": [m.content[:60] for m in batch],
            "decision": decision.as_dict(),
        }

    async def replay(self, group_id: str, *, limit: int = 10) -> dict[str, Any]:
        """Re-evaluate the most recent batch with the current model (no send)."""
        social = self.social
        if not self.available:
            return {"error": "social disabled"}
        batch = social.monitor.external_recent(str(group_id), limit=limit)
        if not batch:
            return {"error": "没有历史消息可回放"}
        topic = await social._active_topic(str(group_id))  # noqa: SLF001
        block = await social._character_block(str(group_id), batch)  # noqa: SLF001
        recent = social.monitor.recent(
            str(group_id), limit=social.config.observer.min_context_messages
        )
        decision = await social.observer.observe(
            group_id=str(group_id),
            batch=batch,
            recent_context=recent,
            character_block=block,
            topic=topic,
        )
        return {
            "group_id": str(group_id),
            "decision": decision.as_dict(),
            "note": "回放不写入数据库、不发送 QQ 消息",
        }

    # ------------------------------------------------------------- settings

    async def load_overrides(self) -> dict[str, Any]:
        data = await self.bot.database.get_setting_json(SETTINGS_KEY) if self.bot.database else None
        return data if isinstance(data, dict) else {}

    async def save_settings(self, form: dict[str, Any]) -> dict[str, Any]:
        def as_bool(value: Any, default: bool = False) -> bool:
            return str(value) == "1" if value is not None else default

        def as_int(name: str, default: int) -> int:
            try:
                return int(float(form.get(name, default)))
            except (TypeError, ValueError):
                return default

        def as_float(name: str, default: float) -> float:
            try:
                return float(form.get(name, default))
            except (TypeError, ValueError):
                return default

        payload: dict[str, Any] = {
            "enabled": as_bool(form.get("social_enabled"), True),
            "decision_model": str(form.get("decision_model", "")).strip(),
            "continuation": {
                "enabled": as_bool(form.get("continuation_enabled"), True),
                "window_minutes": as_int("window_minutes", 10),
                "max_messages": as_int("continuation_max_messages", 8),
            },
            "observer": {
                "batch_size": as_int("batch_size", 5),
                "min_context_messages": as_int("min_context", 20),
                "max_staleness_messages": as_int("max_staleness", 5),
            },
            "participation": {
                "daily_limit": as_int("daily_limit", 30),
                "cooldown_seconds": as_int("cooldown_seconds", 90),
            },
            "thresholds": {
                "follow_up": as_float("follow_up", 0.75),
                "topic_relevance": as_float("topic_relevance", 0.70),
                "social_fit": as_float("social_fit", 0.65),
                "contribution_value": as_float("contribution_value", 0.65),
            },
            "attention": {"enabled": as_bool(form.get("attention_enabled"), True)},
            "fatigue": {"enabled": as_bool(form.get("fatigue_enabled"), True)},
            "topic": {"enabled": as_bool(form.get("topic_enabled"), True)},
        }
        overrides = await self.load_overrides()
        overrides.update(payload)
        await self.bot.database.set_setting_json(SETTINGS_KEY, overrides, int(time.time()))
        self._apply(payload)
        return payload

    def _apply(self, payload: dict[str, Any]) -> None:
        from app.config.settings import SocialConfig

        merged = self.bot.config.social.model_dump()
        for key, value in payload.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key].update(value)
            else:
                merged[key] = value
        config = SocialConfig.model_validate(merged)
        self.bot.config.social = config
        social = self.social
        if social is not None:
            social.config = config
            social.policy._config = config  # noqa: SLF001
            social.continuation._threshold = config.thresholds.follow_up  # noqa: SLF001
            social.continuation._window = max(1, config.continuation.window_minutes) * 60  # noqa: SLF001
            social.observer._config = config  # noqa: SLF001
            social.monitor._max = max(1, config.group_context.max_messages)  # noqa: SLF001
        self._log.info("[Social] settings applied (hot reload)")
