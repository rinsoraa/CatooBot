"""RuntimeReadService：运行期只读投影（WebUI v1.0 · W2 契约 §3/§7.1）。

不是存储、不缓存世界、不写任何状态：只把既有访问器（``Bot`` / ``SandboxRuntime`` /
``RuntimeScheduler`` / ``EventLoopWatchdog`` / ``Metrics`` / ``RealtimeHub`` /
``AdminService``）拼成契约的 ``GET /api/v1/overview``、``/world``、``/runtime`` 形状。
每个值都来自真实访问器；访问器不存在时返回 ``None``，绝不编造数据。

诚实说明（返回 None 或投影自其它字段，等 Core 增补公开访问器）：

* ``qq.last_event_at`` —— 读 ``OneBotGateway.last_event_at``（W2 新增属性）；
  gateway 未启用时为 ``None``；
* ``runtime.database.connected`` —— ``Database`` 没有公开属性，只读 ``_conn``；
  ``runtime.database.size_bytes`` 只在配置指向真实存在的 sqlite 文件时给出；
* ``runtime.process.started_at/uptime_seconds`` —— ``Bot.started_at`` 未启动时为 None；
* ``runtime.onebot`` —— 网关未启用时 ``state="disabled"``、计数为 None、lanes 为空
  （``OneBotGateway.stats()`` 是纯只读投影，W5 新增）；
* ``world.session.person_id`` —— ``SandboxRuntime`` 只有 ``social_session_active()``，
  person_id 只读运行时 ``_social_session`` 字典；
* ``runtime.hub.*`` —— 由 WebServer 注入的 ``RealtimeHub``；没有 hub 时为 ``None``；
* goals 的 ``title`` 投影自 ``Goal.reason``（模型没有 title 字段）；
  relationships 的 ``stage`` 投影自 ``RelationshipState.relation_type``；
* ``logs_tail`` 的 ``ts`` 恒为 ``None``（文件行只有 ``HH:MM:SS``，无法诚实换算 Unix 秒），
  其余字段用正则解析 ``[HH:MM:SS] [LEVEL] [logger] message``；叙述行消息开头的
  ``[channel]`` 前缀（``_PlainFilter`` 注入）会被解析成显式 ``channel`` 字段，
  解析不了的行退化为 ``{"message": line}``。
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any

from app.config.settings import PROJECT_ROOT, project_path
from app.web.services.ai_admin import derive_ai_status
from app.web.services.runtime_snapshot import (
    database_size_bytes,
    onebot_block,
    process_block,
)

if TYPE_CHECKING:
    from app.core.bot import Bot
    from app.web.realtime import RealtimeHub
    from app.web.services.admin import AdminService
    from app.web.services.memory import MemoryAdminService

#: 文件日志行：``[HH:MM:SS] [LEVEL] [logger] message``（app/utils/logger.py）
_LOG_LINE = re.compile(
    r"^\[(?P<time>\d{1,2}:\d{2}:\d{2})\]\s+\[(?P<level>[A-Z]+)\]\s+\[(?P<logger>[^\]]+)\]\s?(?P<message>.*)$"
)

#: 叙述行在消息开头的频道前缀：``[world] 🌍 世界 │ …``（_PlainFilter 注入）
_CHANNEL_PREFIX = re.compile(r"^\[(?P<channel>[a-z][a-z0-9_]*)\]\s+(?P<rest>.*)$")

#: AdminService 只读文件尾的最大行数（LOG_TAIL_LINES）
_LOG_TAIL_MAX = 400

LOG_FILE_NAME = "catoobot.log"


class RuntimeReadService:
    """把 Core 的实时状态投影成契约 JSON；纯读、无副作用。"""

    def __init__(
        self,
        bot: Bot,
        *,
        hub: RealtimeHub | None = None,
        admin: AdminService | None = None,
        memory_admin: MemoryAdminService | None = None,
    ) -> None:
        self._bot = bot
        self._hub = hub
        self._admin = admin
        self._memory_admin = memory_admin

    # ------------------------------------------------------------------ meta

    def meta(self) -> dict[str, Any]:
        """``GET /api/v1/meta`` 的数据（与 meta 路由同源，不依赖请求对象）。"""
        from app.main import VERSION
        from app.web import ui

        try:
            import importlib.metadata as metadata

            webui_version = metadata.version("catoobot")
        except Exception:  # noqa: BLE001 - 发行版名只是可选元数据
            webui_version = ui.VERSION
        started = float(getattr(self._bot, "started_at", 0.0) or 0.0)
        now = time.time()
        return {
            "api": "v1",
            "app_version": VERSION,
            "webui_version": webui_version,
            "core_behavior_phase": "P16",
            "started_at": started or None,
            "uptime_seconds": round(now - started, 3) if started else 0.0,
        }

    # -------------------------------------------------------------- overview

    async def overview(self) -> dict[str, Any]:
        """``GET /api/v1/overview``：契约 §3 的总览四卡（单请求聚合）。"""
        dashboard: dict[str, Any] = {}
        if self._admin is not None:
            dashboard = await self._admin.dashboard()
        metrics = self._bot.metrics.snapshot()
        models = list(dashboard.get("models") or self._models_snapshot())
        healthy = [m for m in models if m.get("enabled") and not m.get("in_cooldown")]
        gateway = getattr(self._bot, "onebot_gateway", None)
        return {
            "qq": {
                "online": bool(dashboard.get("online", getattr(self._bot, "is_connected", False))),
                "self_id": dashboard.get("self_id", getattr(self._bot, "self_id", None)),
                "messages_received": int(metrics.get("messages_received") or 0),
                "users": dashboard.get("users"),
                "groups": dashboard.get("groups"),
                "sessions": dashboard.get("sessions"),
                "last_event_at": (self._last_event_at(gateway) if gateway is not None else None),
            },
            "ai": {
                "enabled": bool(getattr(self._bot.ai, "enabled", False)),
                "current_model": healthy[0]["name"] if healthy else None,
                "models_ok": len(healthy),
                "models_total": len(models),
                "requests": int(metrics.get("ai_requests") or 0),
                "errors": int(metrics.get("ai_errors") or 0),
                "rate_limited": int(metrics.get("rate_limited") or 0),
                # §63: the health word comes from the backend, never from the UI
                "status": derive_ai_status(self._bot),
            },
            "world": self._world_core(),
            "runtime": self._runtime_block(),
            "counts": await self._counts(),
        }

    @staticmethod
    def _last_event_at(gateway: Any) -> float | None:
        stamp = float(getattr(gateway, "last_event_at", 0.0) or 0.0)
        return stamp or None

    def _models_snapshot(self) -> list[dict[str, Any]]:
        try:
            return list(self._bot.ai.router.snapshot())
        except Exception:  # noqa: BLE001 - 总览永不因模型表异常而失败
            return []

    # ----------------------------------------------------------------- world

    async def world(self) -> dict[str, Any]:
        """``GET /api/v1/world``：沙盒总览（契约 §7.1）。

        goals 的 ``title`` 投影自 ``Goal.reason``（模型没有 title 字段）；
        relationships 的 ``stage`` 投影自 ``RelationshipState.relation_type``
        —— v1.2 的 ``relationships.stage`` 属于另一套子系统，沙盒没有该列。
        """
        data = self._world_core()
        sandbox = self._sandbox()
        if sandbox is None:
            return {
                **data,
                "enabled": False,
                "needs": {**data["needs"], "bands": {}},
                "goals": [],
                "commitments": [],
                "relationships": [],
            }
        data["enabled"] = True
        data["needs"] = {**data["needs"], "bands": dict(sandbox.needs.bands())}
        data["goals"] = [
            {
                "id": goal.goal_id,
                "title": goal.reason or goal.kind.value,
                "kind": goal.kind.value,
                "status": goal.status.value,
                "priority": float(goal.priority),
                "progress": float(goal.progress),
                "reason": goal.reason,
                "created_at": float(goal.created_at) or None,
            }
            for goal in sandbox.goals.open_goals()
        ]
        data["commitments"] = [
            {
                "id": item.commitment_id,
                "kind": item.kind.value,
                "status": item.status.value,
                "due_at": float(item.due_at) or None,
                "person_id": item.person_id,
                "description": item.description,
                "strength": item.strength.value,
            }
            for item in sandbox.commitments.open()
        ]
        data["relationships"] = await self._relationships(sandbox)
        return data

    def _world_core(self) -> dict[str, Any]:
        """overview 与 world 共用的世界骨架（契约 §3 的 world 段）。"""
        sandbox = self._sandbox()
        if sandbox is None:
            return {
                "phase": None,
                "location": None,
                "action": None,
                "modes": [],
                "needs": {"critical": [], "pressing": []},
                "world_revision": None,
                "cognitive_revision": None,
                "session": {"active": False, "person_id": None},
                "interrupted": False,
            }
        context = sandbox.context()
        return {
            "phase": sandbox.phase.value,
            "location": context.get("location"),
            "action": self._action_block(sandbox),
            "modes": list(context.get("modes") or []),
            "needs": self._needs_block(sandbox),
            "world_revision": int(getattr(sandbox, "world_revision", 0) or 0),
            "cognitive_revision": int(getattr(sandbox, "cognitive_revision", 0) or 0),
            "session": self._session_block(sandbox),
            "interrupted": bool(context.get("interrupted") or ""),
        }

    @staticmethod
    def _action_block(sandbox: Any) -> dict[str, Any] | None:
        action = sandbox.current_action
        if action is None:
            return None
        definition = sandbox.actions.definition(action)
        return {
            "name": definition.name if definition is not None else None,
            "progress": round(float(action.progress), 4),
            "started_at": float(action.started_at),
            "planned_end_at": float(action.planned_end_at),
        }

    @staticmethod
    def _needs_block(sandbox: Any) -> dict[str, Any]:
        def row(need: Any) -> dict[str, Any]:
            return {
                "key": need.key,
                "level": round(float(need.level), 4),
                "band": need.band(),
            }

        return {
            "critical": [row(need) for need in sandbox.needs.critical()],
            "pressing": [row(need) for need in sandbox.needs.pressing()],
        }

    @staticmethod
    def _session_block(sandbox: Any) -> dict[str, Any]:
        active = bool(sandbox.social_session_active())
        # 只读：SandboxRuntime 没有 person_id 的公开 getter（见模块 docstring）
        session = getattr(sandbox, "_social_session", None)
        person = str(session.get("person_id", "")) if isinstance(session, dict) else ""
        return {"active": active, "person_id": person if active and person else None}

    @staticmethod
    async def _relationships(sandbox: Any) -> list[dict[str, Any]]:
        try:
            states = await sandbox.relationships_dyn.important(limit=3)
        except Exception:  # noqa: BLE001 - 世界投影的单块失败不拖垮整页
            return []
        return [
            {
                "id": state.person_id,
                "stage": state.relation_type,
                "relation_type": state.relation_type,
                "closeness": round(float(state.closeness), 4),
                "trust": round(float(state.trust), 4),
            }
            for state in states
        ]

    # --------------------------------------------------------------- runtime

    async def runtime(self) -> dict[str, Any]:
        """``GET /api/v1/runtime``：契约 §3 的 runtime 段 + 调度器 last_report。"""
        return self._runtime_block()

    def _runtime_block(self) -> dict[str, Any]:
        scheduler = getattr(self._bot, "runtime_scheduler", None)
        if scheduler is None:
            block: dict[str, Any] = {
                "running": False,
                "interval_seconds": None,
                "ticks": None,
                "catchups": None,
                "last_tick_at": None,
            }
        else:
            block = {
                "running": bool(scheduler.running),
                "interval_seconds": float(scheduler.interval),
                "ticks": int(scheduler.ticks),
                "catchups": int(scheduler.catchups),
                "last_tick_at": float(scheduler.last_tick_at) or None,
            }
        block["last_report"] = dict(getattr(scheduler, "last_report", {}) or {}) or None
        watchdog = getattr(self._bot, "watchdog", None)
        stats = watchdog.stats() if watchdog is not None else {}
        hub_stats = self._hub.stats() if self._hub is not None else {}
        return {
            "scheduler": block,
            "watchdog": {
                "last_lag_ms": stats.get("last_lag_ms"),
                "max_lag_ms": stats.get("max_lag_ms"),
                "lag_events": stats.get("lag_events"),
            },
            "database": self._database_block(),
            "hub": {
                "subscribers": hub_stats.get("subscribers"),
                "published": hub_stats.get("published"),
                "dropped": hub_stats.get("dropped"),
                "queue_size": hub_stats.get("queue_size"),
            },
            "process": self._process_block(),
            "onebot": self._onebot_block(),
        }

    def _process_block(self) -> dict[str, Any]:
        """进程事实：版本/Python/启动时间（拆出的纯投影，见 runtime_snapshot）。"""
        return process_block(self._bot)

    def _onebot_block(self) -> dict[str, Any]:
        """QQ 网关只读计数 + lane 深度（网关未启用时计数为 null、lanes 空）。"""
        return onebot_block(self._bot)

    def _database_block(self) -> dict[str, Any]:
        connected = self.database_connected()
        return {
            "connected": connected,
            "size_bytes": database_size_bytes(self._bot, connected=connected),
        }

    def database_connected(self) -> bool:
        """``Database`` 没有公开的 connected 属性，只读判断 ``_conn``。"""
        database = getattr(self._bot, "database", None)
        return bool(database is not None and getattr(database, "_conn", None) is not None)

    # ---------------------------------------------------------------- counts

    async def _counts(self) -> dict[str, Any]:
        sandbox = self._sandbox()
        if sandbox is not None:
            return {
                "memories": await sandbox.memory.count(),
                "experiences": await sandbox.store.count_experiences(
                    character_id=sandbox.character_id
                ),
                "goals_open": len(sandbox.goals.open_goals()),
                "commitments_open": len(sandbox.commitments.open()),
            }
        # 没有沙盒时只有全局 memories 行数可读；其余计数没有访问器
        memories = await self._admin._count("memories") if self._admin is not None else None
        return {
            "memories": memories,
            "experiences": None,
            "goals_open": None,
            "commitments_open": None,
        }

    # ------------------------------------------------------------- character

    async def character(self) -> dict[str, Any]:
        """persona + character_state（委托 AdminService），并给出 persona 来源。

        ``source``：数据库里存在 ``active_persona`` 时为 ``database``，
        否则是 config.yaml 的 ``character:`` 段（PersonaManager 的既有优先级）。
        """
        if self._admin is None:
            return {"persona": None, "state": None, "source": None}
        persona = await self._admin.get_persona()
        state = await self._admin.character_state()
        stored: Any = None
        database = getattr(self._bot, "database", None)
        if database is not None:
            try:
                stored = await database.get_setting_json("active_persona")
            except Exception:  # noqa: BLE001 - 来源只是展示信息
                stored = None
        return {
            "persona": persona.model_dump(),
            "state": state,
            "source": "database" if isinstance(stored, dict) else "config",
        }

    # ------------------------------------------------------------ world trace

    async def world_trace(self, limit: int = 120) -> dict[str, Any]:
        """结构化事件轨迹（``SandboxRuntime.replay``：事件 + 决策 trace 合并）。"""
        sandbox = self._sandbox()
        if sandbox is None:
            return {"enabled": False, "items": [], "count": 0}
        items = await sandbox.replay(limit=limit)
        return {"enabled": True, "items": items, "count": len(items)}

    # ------------------------------------------------------------------ logs

    async def logs_tail(
        self, level: str = "", keyword: str = "", lines: int = 200, channel: str = ""
    ) -> dict[str, Any]:
        """``GET /api/v1/logs/tail``：把 AdminService 的格式化行解析成字段。

        能解析的行给出 ``level``/``logger``/``message``（``ts`` 恒为 None，
        文件格式只有时刻没有日期）；叙述行消息以 ``[channel] `` 开头时，
        该前缀会被取出放进显式 ``channel`` 字段（消息本身保留其余文本）。
        ``channel`` 过滤在服务端完成：多取几行再筛，避免只筛到尾部的窗口。
        """
        fetch = lines
        if channel:
            fetch = min(_LOG_TAIL_MAX, max(lines * 4, 100))
        raw: list[str] = []
        if self._admin is not None:
            raw = await self._admin.logs(level=level, keyword=keyword, lines=fetch)
        known = self._channel_keys()
        items: list[dict[str, Any]] = []
        parsed = 0
        for line in raw:
            match = _LOG_LINE.match(line)
            if match is None:
                items.append(
                    {"ts": None, "level": "", "logger": "", "channel": None, "message": line}
                )
                continue
            parsed += 1
            message = match.group("message")
            channel_key: str | None = None
            prefix = _CHANNEL_PREFIX.match(message)
            if prefix is not None and prefix.group("channel") in known:
                channel_key = prefix.group("channel")
                message = prefix.group("rest")
            items.append(
                {
                    "ts": None,
                    "time": match.group("time"),
                    "level": match.group("level"),
                    "logger": match.group("logger"),
                    "channel": channel_key,
                    "message": message,
                }
            )
        if channel:
            items = [item for item in items if item.get("channel") == channel]
        items = items[:lines]
        return {
            "items": items,
            "file": self.log_file_display(),
            "truncated": bool(raw) and len(raw) >= fetch,
            "parsed": parsed,
            "total": len(raw),
        }

    @staticmethod
    def _channel_keys() -> set[str]:
        """已知的叙述频道 key（app/utils/narrator.py 是唯一事实源）。"""
        from app.utils.narrator import CHANNELS

        return set(CHANNELS)

    def log_file_display(self) -> str:
        """相对项目根的日志路径（契约示例：``logs/catoobot.log``）。"""
        log_dir = getattr(getattr(self._bot, "config", None), "logging", None)
        path = project_path(getattr(log_dir, "log_dir", "logs")) / LOG_FILE_NAME
        try:
            return str(path.relative_to(PROJECT_ROOT))
        except ValueError:  # 配置指向项目外时保留原样
            return str(path)

    def logs_channels(self) -> dict[str, Any]:
        """叙述频道字典（app/utils/narrator.py 的 CHANNELS，图标/标签图例）。"""
        from app.utils.narrator import CHANNELS

        return {
            "channels": [
                {"key": key, "icon": icon, "label": label, "accent": accent}
                for key, (icon, label, accent) in CHANNELS.items()
            ]
        }

    # ---------------------------------------------------------------- helpers

    def _sandbox(self) -> Any | None:
        return getattr(self._bot, "sandbox", None)
